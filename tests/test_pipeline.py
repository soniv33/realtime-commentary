"""End-to-end pipeline tests using in-memory fakes (no network/LLM/audio)."""

from __future__ import annotations

import time

import pytest

from f1_commentator.config import OrchestratorSettings
from f1_commentator.events import EventType, TelemetryEvent
from f1_commentator.orchestrator.classifier import EventClassifier
from f1_commentator.orchestrator.pipeline import CommentaryPipeline
from tests.fakes import FakeCommentator, FakeStreamer, RecordingSink, async_iter


def _pipeline(**orch_overrides):
    settings = OrchestratorSettings(**orch_overrides)
    commentator = FakeCommentator()
    streamer = FakeStreamer()
    sink = RecordingSink()
    pipeline = CommentaryPipeline(
        classifier=EventClassifier(settings),
        commentator=commentator,
        streamer=streamer,
        sink=sink,
        settings=settings,
    )
    return pipeline, commentator, streamer, sink


@pytest.mark.asyncio
async def test_narrate_streams_end_to_end():
    pipeline, commentator, streamer, sink = _pipeline()
    event = TelemetryEvent(type=EventType.OVERTAKE, session_time=0.0, priority=80)

    await pipeline.narrate(event)

    # The LLM was invoked, chunks reached the streamer, audio reached the sink.
    assert commentator.calls == [event]
    assert streamer.chunks, "streamer received no text chunks"
    # Reassembled audio equals the reassembled chunks (FakeStreamer is identity).
    assert sink.written.decode() == "".join(streamer.chunks)


@pytest.mark.asyncio
async def test_run_only_narrates_threshold_events():
    pipeline, commentator, _streamer, _sink = _pipeline()
    events = [
        TelemetryEvent(type=EventType.TICK, session_time=1.0, priority=10),
        TelemetryEvent(type=EventType.OVERTAKE, session_time=2.0, priority=80),
        TelemetryEvent(type=EventType.TICK, session_time=3.0, priority=10),
    ]

    await pipeline.run(async_iter(events))

    assert [e.type for e in commentator.calls] == [EventType.OVERTAKE]


@pytest.mark.asyncio
async def test_broadcast_offset_delays_playback():
    # A 0.15s offset should push first audio out by at least the offset.
    pipeline, _c, _s, _sink = _pipeline(broadcast_offset_seconds=0.15)
    event = TelemetryEvent(type=EventType.RED_FLAG, session_time=0.0, priority=90)

    start = time.perf_counter()
    await pipeline.narrate(event)
    elapsed = time.perf_counter() - start

    assert elapsed >= 0.15


@pytest.mark.asyncio
async def test_narration_failure_does_not_kill_the_feed():
    class Boom:
        async def stream_commentary(self, event):
            raise RuntimeError("model unavailable")
            yield  # pragma: no cover - makes this an async generator

    settings = OrchestratorSettings()
    pipeline = CommentaryPipeline(
        classifier=EventClassifier(settings),
        commentator=Boom(),
        streamer=FakeStreamer(),
        sink=RecordingSink(),
        settings=settings,
    )
    # Should complete without raising even though narration blows up.
    await pipeline.run(async_iter([TelemetryEvent(type=EventType.OVERTAKE, session_time=0.0, priority=80)]))
