"""Tests for the parallel colour track: lull-filling and play-by-play preemption."""

from __future__ import annotations

import asyncio

import pytest

from f1_commentator.config import OrchestratorSettings
from f1_commentator.context.build import RaceResult, build_context
from f1_commentator.context.store import ContextStore
from f1_commentator.events import EventType, TelemetryEvent
from f1_commentator.orchestrator.classifier import EventClassifier
from f1_commentator.orchestrator.pipeline import CommentaryPipeline
from tests.fakes import FakeCommentator, FakeStreamer, RecordingSink


def _context() -> ContextStore:
    season = [
        RaceResult(year=2024, circuit="Sakhir", session_key=1, date_start="2024-03-02",
                   order=["VER", "PER", "SAI"]),
        RaceResult(year=2024, circuit="Jeddah", session_key=2, date_start="2024-03-09",
                   order=["VER", "PER", "LEC"]),
    ]
    history = [
        RaceResult(year=2023, circuit="Zandvoort", session_key=9149, date_start="2023-08-27",
                   order=["VER", "ALO", "GAS"], safety_cars=2, on_track_passes=43, rain=True),
    ]
    ctx = build_context(
        year=2024, circuit="Zandvoort", session_key=9582,
        season_results=season, circuit_results=history,
        entrants=["VER", "PER", "SAI", "LEC"], round_number=4, rounds_in_season=24,
    )
    return ContextStore(ctx)


def _pipeline(*, context: ContextStore | None = None, **overrides):
    settings = OrchestratorSettings(**overrides)
    commentator = FakeCommentator()
    pipeline = CommentaryPipeline(
        classifier=EventClassifier(settings),
        commentator=commentator,
        streamer=FakeStreamer(),
        sink=RecordingSink(),
        settings=settings,
        context=context,
    )
    return pipeline, commentator


@pytest.mark.asyncio
async def test_play_by_play_receives_relevant_context():
    pipeline, commentator = _pipeline(context=_context())
    ev = TelemetryEvent(type=EventType.OVERTAKE, session_time=10.0, priority=80, drivers=["PER", "VER"])

    await pipeline.narrate(ev)

    assert commentator.calls == [ev]
    # The head-to-head fact should have been handed to the model.
    assert any("race finishes" in f for f in commentator.contexts[0])


@pytest.mark.asyncio
async def test_no_context_store_means_plain_play_by_play():
    pipeline, commentator = _pipeline()  # context=None → empty store
    ev = TelemetryEvent(type=EventType.OVERTAKE, session_time=10.0, priority=80, drivers=["PER", "VER"])

    await pipeline.narrate(ev)

    assert commentator.contexts == [[]]


@pytest.mark.asyncio
async def test_colour_fills_a_lull_when_feed_is_quiet():
    # Silence threshold of 0 so the colour worker speaks almost immediately.
    pipeline, commentator = _pipeline(
        context=_context(), colour_after_silence_seconds=0.0, colour_min_interval_seconds=0.0
    )

    async def quiet_feed():
        # A feed that yields nothing for a while — the lull the analyst fills.
        await asyncio.sleep(2.5)
        return
        yield  # pragma: no cover - makes this an async generator

    await pipeline.run(quiet_feed())

    assert commentator.colour_calls, "colour track should have spoken during the lull"
    assert commentator.calls == [], "no threshold events, so no play-by-play"


@pytest.mark.asyncio
async def test_colour_disabled_by_setting():
    pipeline, commentator = _pipeline(
        context=_context(), enable_colour=False, colour_after_silence_seconds=0.0
    )

    async def quiet_feed():
        await asyncio.sleep(1.5)
        return
        yield  # pragma: no cover

    await pipeline.run(quiet_feed())
    assert commentator.colour_calls == []


@pytest.mark.asyncio
async def test_colour_does_not_run_without_context():
    pipeline, commentator = _pipeline(colour_after_silence_seconds=0.0)  # no context pack

    async def quiet_feed():
        await asyncio.sleep(1.5)
        return
        yield  # pragma: no cover

    await pipeline.run(quiet_feed())
    assert commentator.colour_calls == []


@pytest.mark.asyncio
async def test_play_by_play_preempts_an_in_flight_colour_line():
    """A long colour line must be cancelled the moment the action needs calling."""
    settings = OrchestratorSettings(colour_after_silence_seconds=0.0, colour_min_interval_seconds=0.0)

    class SlowColour(FakeCommentator):
        async def stream_colour(self, fact, *, race="", since_last=None):
            self.colour_calls.append(fact)
            yield "This is a very long analyst aside"
            await asyncio.sleep(5)      # still talking…
            yield " that should never finish."

    commentator = SlowColour()
    pipeline = CommentaryPipeline(
        classifier=EventClassifier(settings), commentator=commentator,
        streamer=FakeStreamer(), sink=RecordingSink(), settings=settings, context=_context(),
    )

    async def feed():
        # The colour worker polls once a second, so give it a clear head start —
        # we want the event to land while a colour line is genuinely mid-flight.
        await asyncio.sleep(2.2)
        yield TelemetryEvent(type=EventType.SAFETY_CAR, session_time=50.0, priority=92)

    await asyncio.wait_for(pipeline.run(feed()), timeout=10)

    assert commentator.colour_calls, "colour should have started"
    assert [e.type for e in commentator.calls] == [EventType.SAFETY_CAR], "action must still be called"
