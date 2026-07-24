"""Decides which telemetry events are worth commentary.

The classifier is the "threshold" gate from the requirements: a yellow flag or an
overtake gets called; a low-signal heartbeat tick does not. It is deliberately
pure and clock-injectable so the debounce logic is unit-testable without waiting.
"""

from __future__ import annotations

from ..config import OrchestratorSettings
from ..events import EventType, TelemetryEvent

# Event types that are always broadcast-worthy when above the priority floor.
COMMENTARY_WORTHY: frozenset[EventType] = frozenset(
    {
        EventType.YELLOW_FLAG,
        EventType.DOUBLE_YELLOW,
        EventType.RED_FLAG,
        EventType.GREEN_FLAG,
        EventType.SAFETY_CAR,
        EventType.VIRTUAL_SAFETY_CAR,
        EventType.OVERTAKE,
        EventType.PIT_STOP,
        EventType.FASTEST_LAP,
        EventType.RETIREMENT,
        EventType.RACE_START,
        EventType.RACE_FINISH,
        EventType.WEATHER_CHANGE,
    }
)


class EventClassifier:
    """Applies type, priority, and per-type cooldown thresholds."""

    def __init__(self, settings: OrchestratorSettings, clock=None) -> None:
        self._settings = settings
        # Monotonic clock, injectable for tests. Uses session_time as the clock
        # domain so cooldowns track race time, not wall time.
        self._clock = clock
        self._last_called: dict[EventType, float] = {}

    def should_commentate(self, event: TelemetryEvent) -> bool:
        """Return True if this event crosses the commentary threshold."""
        if event.type not in COMMENTARY_WORTHY:
            return False
        if event.priority < self._settings.min_priority:
            return False

        now = event.session_time if self._clock is None else self._clock()
        last = self._last_called.get(event.type)
        if last is not None and (now - last) < self._settings.cooldown_seconds:
            return False

        self._last_called[event.type] = now
        return True
