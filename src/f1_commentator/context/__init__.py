"""Contextual ("colour") knowledge that runs in parallel to the live event feed.

Play-by-play answers *what just happened*. This package answers *why it matters*:
season standings and title gaps, what happened at this circuit in previous years,
each driver's recent form, and head-to-head records between two drivers.

Everything here is **derived from telemetry data**, never invented — that keeps the
colour commentary grounded and defensible (no hallucinated history).
"""

from .models import CircuitHistory, DriverForm, HeadToHead, RaceContext, SeasonStanding
from .store import ContextStore

__all__ = [
    "CircuitHistory",
    "ContextStore",
    "DriverForm",
    "HeadToHead",
    "RaceContext",
    "SeasonStanding",
]
