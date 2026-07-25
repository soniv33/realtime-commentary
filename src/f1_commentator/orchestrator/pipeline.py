"""The orchestration layer: dual-streaming LLM -> TTS -> speakers, two voices.

Data flow for a single narration:

    event / context beat
      -> LLMCommentator.stream_*(...)               # token deltas, streamed
      -> chunk_by_punctuation(...)                  # speakable phrases, streamed
      -> AudioStreamer.synthesize(...)              # PCM audio, streamed
      -> _play_with_offset(...)                     # broadcast-offset -> AudioSink

Crucially, nothing here waits for the LLM to finish. Each stage is an async
generator, so the first audio phrase is already playing while the model is still
writing the tail of the sentence.

**Two parallel tracks**, mirroring a real broadcast booth:

* *Play-by-play* — reactive. A threshold event is called immediately and
  **preempts** any colour line in progress (the director always cuts to the
  action).
* *Colour* — a background task that waits for a lull in the feed and then speaks
  one data-derived context beat (championship picture, what happened at this
  circuit last year, driver form, head-to-head). It never invents facts: the
  ContextStore hands it only what was derived from telemetry.

Both tracks share one audio sink, so exactly one voice is ever on air; a
``_NarrationGate`` serialises them and lets play-by-play cancel colour mid-line.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator

import websockets

from ..audio.base import AudioSink, AudioStreamer
from ..config import OrchestratorSettings
from ..context.store import ContextStore
from ..events import TelemetryEvent
from ..llm.base import LLMCommentator
from .chunker import chunk_by_punctuation
from .classifier import EventClassifier

logger = logging.getLogger(__name__)


class _NarrationGate:
    """Serialises the two voices and lets play-by-play preempt colour.

    Only one narration may hold the microphone. Play-by-play acquires it by
    cancelling whatever colour line is in flight; colour only acquires it when the
    mic is free, and yields immediately if asked to stop.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._colour_task: asyncio.Task | None = None

    def register_colour(self, task: asyncio.Task) -> None:
        self._colour_task = task

    async def preempt_colour(self) -> None:
        """Cancel an in-flight colour line so the action can be called at once."""
        task = self._colour_task
        self._colour_task = None
        if task and not task.done():
            logger.debug("preempting colour line for play-by-play")
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def hold(self):
        """Async context manager granting exclusive use of the audio sink."""
        return self._lock


class CommentaryPipeline:
    """Wires ingestion, classification, context, LLM, and audio into one pipeline."""

    def __init__(
        self,
        *,
        classifier: EventClassifier,
        commentator: LLMCommentator,
        streamer: AudioStreamer,
        sink: AudioSink,
        settings: OrchestratorSettings,
        context: ContextStore | None = None,
    ) -> None:
        self._classifier = classifier
        self._commentator = commentator
        self._streamer = streamer
        self._sink = sink
        self._settings = settings
        self._context = context or ContextStore.empty()
        self._gate = _NarrationGate()
        # Wall-clock of the last thing we said — drives lull detection.
        self._last_spoken = 0.0
        self._last_colour = 0.0

    # -- the dual-streaming primitive --------------------------------------- #
    async def _speak(self, deltas: AsyncIterator[str]) -> None:
        """Pipe an LLM token stream through the chunker and TTS to the speakers."""
        chunks = chunk_by_punctuation(deltas)
        audio = self._streamer.synthesize(chunks)
        await self._play_with_offset(audio, self._settings.broadcast_offset_seconds)
        self._last_spoken = time.monotonic()

    async def narrate(self, event: TelemetryEvent) -> None:
        """Call one event — preempting any colour line in progress."""
        await self._gate.preempt_colour()
        facts = self._context.for_event(event)
        if facts:
            logger.debug("context for %s: %s", event.type.value, facts)
        async with self._gate.hold():
            await self._speak(self._commentator.stream_commentary(event, facts))

    async def narrate_colour(self, fact: str) -> None:
        """Speak one colour beat. Cancellable: play-by-play may cut it off."""
        since = time.monotonic() - self._last_spoken if self._last_spoken else None
        async with self._gate.hold():
            await self._speak(
                self._commentator.stream_colour(
                    fact, race=self._context.headline(), since_last=since
                )
            )
        self._last_colour = time.monotonic()

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
            if not producer.done():
                producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer

    # -- the live loop ------------------------------------------------------ #
    async def run(self, events: AsyncIterator[TelemetryEvent]) -> None:
        """Consume a live event stream, narrating events and filling lulls.

        The event source is injected (an async iterator), so this loop is fully
        testable with fakes - no WebSocket, LLM, or audio device required.
        """
        pending: asyncio.Queue[TelemetryEvent | None] = asyncio.Queue(maxsize=1)
        worker = asyncio.create_task(self._narration_worker(pending))
        colour = None
        if self._settings.enable_colour and self._context.available:
            colour = asyncio.create_task(self._colour_worker())
            logger.info("colour track enabled — %s", self._context.headline() or "context loaded")

        self._last_spoken = time.monotonic()
        try:
            async for event in events:
                if not self._classifier.should_commentate(event):
                    continue
                logger.info("threshold event: %s", event.headline())
                _drop_and_put(pending, event)
        finally:
            if colour:
                colour.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await colour
            await pending.put(None)  # shutdown sentinel
            await worker

    async def _narration_worker(self, pending: asyncio.Queue) -> None:
        """Serialise play-by-play: one utterance at a time, newest event wins."""
        while True:
            event = await pending.get()
            if event is None:  # shutdown sentinel
                return
            try:
                await self.narrate(event)
            except Exception:  # noqa: BLE001 - a bad line must not kill the feed
                logger.exception("narration failed for %s", event.type)

    async def _colour_worker(self) -> None:
        """Fill quiet stretches with data-derived context, one beat at a time."""
        s = self._settings
        # Poll well inside the silence threshold, otherwise a fast-forwarded replay
        # (where the whole race passes in seconds) never gets sampled during a lull.
        poll = max(0.05, min(0.25, s.colour_after_silence_seconds / 4))
        while True:
            await asyncio.sleep(poll)
            now = time.monotonic()
            if self._gate.busy:
                continue
            if (now - self._last_spoken) < s.colour_after_silence_seconds:
                continue
            if self._last_colour and (now - self._last_colour) < s.colour_min_interval_seconds:
                continue
            fact = self._context.next_beat()
            if fact is None:  # beats exhausted — recycle so long races stay filled
                self._context.reset_beats()
                fact = self._context.next_beat()
                if fact is None:
                    return
            logger.info("colour beat: %s", fact)
            task = asyncio.create_task(self.narrate_colour(fact))
            self._gate.register_colour(task)
            # asyncio.wait() reports the child's outcome without re-raising it, so a
            # preempted colour line doesn't look like *this worker* being cancelled.
            # (A bare `except CancelledError` here would swallow our own shutdown
            # cancellation and spin this loop forever.)
            await asyncio.wait({task})
            if task.cancelled():
                logger.debug("colour line cut short by play-by-play")
            elif task.exception() is not None:
                logger.error("colour narration failed", exc_info=task.exception())

    # -- websocket event source -------------------------------------------- #
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
