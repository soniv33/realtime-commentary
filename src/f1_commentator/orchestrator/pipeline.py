"""The orchestration layer: dual-streaming LLM -> TTS -> speakers.

Data flow for a single threshold event:

    event
      -> LLMCommentator.stream_commentary(event)   # token deltas, streamed
      -> chunk_by_punctuation(...)                 # speakable phrases, streamed
      -> AudioStreamer.synthesize(...)             # PCM audio, streamed
      -> _play_with_offset(...)                    # broadcast-offset -> AudioSink

Crucially, nothing here waits for the LLM to finish. Each stage is an async
generator, so the first audio phrase is already playing while Claude is still
writing the tail of the sentence.

Concurrency model: the receive loop never blocks on narration. Threshold events
are handed to a single-slot narrator worker via a bounded queue (one commentator
voice = one utterance at a time); if narration falls behind, the oldest queued
event is dropped so commentary stays live rather than lagging the race.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import websockets

from ..audio.base import AudioSink, AudioStreamer
from ..config import OrchestratorSettings
from ..events import TelemetryEvent
from ..llm.base import LLMCommentator
from .chunker import chunk_by_punctuation
from .classifier import EventClassifier

logger = logging.getLogger(__name__)


class CommentaryPipeline:
    """Wires ingestion, classification, LLM, and audio into one live pipeline."""

    def __init__(
        self,
        *,
        classifier: EventClassifier,
        commentator: LLMCommentator,
        streamer: AudioStreamer,
        sink: AudioSink,
        settings: OrchestratorSettings,
    ) -> None:
        self._classifier = classifier
        self._commentator = commentator
        self._streamer = streamer
        self._sink = sink
        self._settings = settings

    # -- the dual-streaming primitive --------------------------------------

    async def narrate(self, event: TelemetryEvent) -> None:
        """Stream one event all the way to the speakers, offset-delayed.

        This is the "do not wait for the LLM to finish" path: the LLM token
        stream is piped, live, through the punctuation chunker into ElevenLabs,
        and the resulting PCM is played with the configured broadcast offset.
        """
        deltas = self._commentator.stream_commentary(event)
        chunks = chunk_by_punctuation(deltas)
        audio = self._streamer.synthesize(chunks)
        await self._play_with_offset(audio, self._settings.broadcast_offset_seconds)

    async def _play_with_offset(self, audio: AsyncIterator[bytes], offset: float) -> None:
        """Play a PCM stream, delaying *speaker output* by ``offset`` seconds.

        Synthesis starts immediately (a producer task fills a queue), so the
        vendor-side TTFB is unaffected; only playback to the sink is delayed,
        which is exactly what "sync the audio to a TV feed" requires.
        """
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def _produce() -> None:
            try:
                async for pcm in audio:
                    await queue.put(pcm)
            finally:
                await queue.put(None)  # sentinel

        producer = asyncio.create_task(_produce())
        try:
            if offset > 0:
                await asyncio.sleep(offset)
            while True:
                pcm = await queue.get()
                if pcm is None:
                    break
                await self._sink.write(pcm)
        finally:
            await producer

    # -- the live loop ------------------------------------------------------

    async def run(self, events: AsyncIterator[TelemetryEvent]) -> None:
        """Consume a live event stream and narrate the threshold events.

        The event source is injected (an async iterator), so this loop is fully
        testable with fakes - no WebSocket, LLM, or audio device required.
        """
        pending: asyncio.Queue[TelemetryEvent] = asyncio.Queue(maxsize=1)
        worker = asyncio.create_task(self._narration_worker(pending))
        try:
            async for event in events:
                if not self._classifier.should_commentate(event):
                    continue
                logger.info("threshold event: %s", event.headline())
                _drop_and_put(pending, event)
        finally:
            await pending.put(None)  # type: ignore[arg-type]  # shutdown sentinel
            await worker

    async def _narration_worker(self, pending: asyncio.Queue) -> None:
        """Serialise narration: one utterance at a time, newest event wins."""
        while True:
            event = await pending.get()
            if event is None:  # shutdown sentinel
                return
            try:
                await self.narrate(event)
            except Exception:  # noqa: BLE001 - a bad line must not kill the feed
                logger.exception("narration failed for %s", event.type)

    # -- websocket event source --------------------------------------------

    async def connect_and_run(self, ws_url: str) -> None:
        """Connect to the simulator's WebSocket and run the pipeline."""
        logger.info("connecting to simulator at %s", ws_url)
        async with websockets.connect(ws_url) as ws:
            await self.run(_events_from_socket(ws))
        await self._sink.close()


def _drop_and_put(queue: asyncio.Queue, item: TelemetryEvent) -> None:
    """Enqueue ``item``, discarding the oldest queued event if the queue is full.

    Keeps commentary live: better to skip a stale overtake call than to fall
    seconds behind the race.
    """
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        try:
            dropped = queue.get_nowait()
            logger.debug("dropping stale event %s", getattr(dropped, "type", "?"))
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:  # pragma: no cover - racing producers
            pass


async def _events_from_socket(ws) -> AsyncIterator[TelemetryEvent]:
    """Decode ``TelemetryEvent`` objects off a WebSocket connection."""
    try:
        async for raw in ws:
            try:
                yield TelemetryEvent.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                logger.warning("skipping malformed event frame")
    except websockets.ConnectionClosed:
        logger.info("simulator connection closed")
