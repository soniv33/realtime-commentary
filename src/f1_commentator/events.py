"""Telemetry event model shared across every layer.

The simulator broadcasts these as JSON over the WebSocket; the orchestrator
parses them back into ``TelemetryEvent`` instances. Keeping a single, versioned
schema here is what lets the ingestion, classification, and prompting layers stay
independently testable.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """The multi-signal event vocabulary distilled from raw telemetry.

    These are the "already mapped out" signals from the prior build: incidents,
    position dynamics, and weather, normalised into a flat enum the classifier
    can threshold on.
    """

    # Race control / incidents
    YELLOW_FLAG = "yellow_flag"
    DOUBLE_YELLOW = "double_yellow"
    RED_FLAG = "red_flag"
    GREEN_FLAG = "green_flag"
    SAFETY_CAR = "safety_car"
    VIRTUAL_SAFETY_CAR = "virtual_safety_car"

    # Position dynamics
    OVERTAKE = "overtake"
    PIT_STOP = "pit_stop"
    FASTEST_LAP = "fastest_lap"
    RETIREMENT = "retirement"

    # Session lifecycle
    RACE_START = "race_start"
    RACE_FINISH = "race_finish"

    # Ambient
    WEATHER_CHANGE = "weather_change"
    TICK = "tick"  # heartbeat / low-signal telemetry sample


class TelemetryEvent(BaseModel):
    """A single distilled telemetry event on the wire.

    ``session_time`` is seconds since the green light and drives the replay
    cadence. ``priority`` (0-100) lets the classifier apply a global threshold on
    top of per-type rules.
    """

    type: EventType
    session_time: float = Field(..., description="Seconds since session start.")
    priority: int = Field(50, ge=0, le=100, description="Editorial importance 0-100.")

    lap: int | None = Field(None, description="Current lap number, if applicable.")
    drivers: list[str] = Field(
        default_factory=list,
        description="Driver codes involved, e.g. ['VER', 'HAM'].",
    )
    location: str | None = Field(None, description="Track sector / corner, if known.")
    detail: str | None = Field(None, description="Human-readable one-liner of raw signal.")

    # Free-form bag for anything the prompt might use (gap deltas, tyre compound,
    # air temp, etc.) without needing a schema migration for every new signal.
    payload: dict = Field(default_factory=dict)

    def headline(self) -> str:
        """A compact, deterministic summary used to seed the LLM prompt."""
        who = ", ".join(self.drivers) if self.drivers else "field"
        where = f" at {self.location}" if self.location else ""
        lap = f" (lap {self.lap})" if self.lap is not None else ""
        base = self.detail or self.type.value.replace("_", " ")
        return f"{base} | {who}{where}{lap}".strip()
