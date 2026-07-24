"""Replay sources for historical F1 session data.

The default source reads a JSONL file of pre-distilled ``TelemetryEvent`` rows
(one JSON object per line), which keeps the demo self-contained and deterministic
- no network, no FastF1 cache warm-up. A ``FastF1ReplaySource`` stub shows where a
real historical loader would plug in without changing the server or orchestrator.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path

from ..events import TelemetryEvent


class ReplaySource(Iterable[TelemetryEvent]):
    """Anything that can yield a time-ordered stream of telemetry events."""

    def __iter__(self) -> Iterator[TelemetryEvent]:  # pragma: no cover - interface
        raise NotImplementedError


class JsonlReplaySource(ReplaySource):
    """Read distilled events from a JSONL file, sorted by ``session_time``.

    This is the format the "previous build" would export its multi-signal events
    into - a clean seam between the (already solved) distillation problem and the
    (new) transport/orchestration problem.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def __iter__(self) -> Iterator[TelemetryEvent]:
        events: list[TelemetryEvent] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line_no, raw in enumerate(handle, start=1):
                raw = raw.strip()
                if not raw or raw.startswith("#"):
                    continue
                try:
                    events.append(TelemetryEvent.model_validate(json.loads(raw)))
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError(f"{self._path}:{line_no}: bad event row: {exc}") from exc
        events.sort(key=lambda e: e.session_time)
        return iter(events)


class FastF1ReplaySource(ReplaySource):
    """Placeholder for a real FastF1 / OpenF1 historical loader.

    A production implementation would load a session (e.g. ``fastf1.get_session``),
    walk laps/position/weather channels, run the existing distillation logic, and
    yield ``TelemetryEvent`` instances. Kept as a stub so the wiring is obvious
    without pulling a heavy dependency into the POV.
    """

    def __init__(self, year: int, grand_prix: str, session: str) -> None:
        self.year = year
        self.grand_prix = grand_prix
        self.session = session

    def __iter__(self) -> Iterator[TelemetryEvent]:  # pragma: no cover - stub
        raise NotImplementedError(
            "FastF1ReplaySource is a stub. Wire up fastf1.get_session(...) here and "
            "reuse the existing telemetry distillation to emit TelemetryEvent rows."
        )


async def paced_replay(
    source: ReplaySource,
    *,
    speed_multiplier: float = 1.0,
    max_gap_seconds: float = 8.0,
    sleep=None,
) -> AsyncIterator[TelemetryEvent]:
    """Yield events at (a scaled version of) their real inter-event timing.

    The wait between two events is ``(t2 - t1) / speed_multiplier``, clamped to
    ``max_gap_seconds`` so long quiet stretches don't stall a demo. ``sleep`` is
    injectable so tests can run instantly with a fake clock.
    """
    import asyncio

    sleep = sleep or asyncio.sleep
    prev_time: float | None = None
    for event in source:
        if prev_time is not None:
            gap = (event.session_time - prev_time) / max(speed_multiplier, 1e-6)
            gap = max(0.0, min(gap, max_gap_seconds))
            if gap:
                await sleep(gap)
        prev_time = event.session_time
        yield event
