"""Tests for the threshold classifier."""

from __future__ import annotations

from f1_commentator.config import OrchestratorSettings
from f1_commentator.events import EventType, TelemetryEvent
from f1_commentator.orchestrator.classifier import EventClassifier


def _event(type_: EventType, *, t: float = 0.0, priority: int = 80) -> TelemetryEvent:
    return TelemetryEvent(type=type_, session_time=t, priority=priority)


def _classifier(**overrides) -> EventClassifier:
    settings = OrchestratorSettings(**overrides)
    return EventClassifier(settings)


def test_low_signal_tick_is_ignored():
    clf = _classifier()
    assert clf.should_commentate(_event(EventType.TICK, priority=95)) is False


def test_threshold_event_passes():
    clf = _classifier()
    assert clf.should_commentate(_event(EventType.OVERTAKE)) is True


def test_below_priority_floor_is_dropped():
    clf = _classifier(min_priority=70)
    assert clf.should_commentate(_event(EventType.FASTEST_LAP, priority=60)) is False


def test_cooldown_debounces_same_type():
    clf = _classifier(cooldown_seconds=5.0)
    assert clf.should_commentate(_event(EventType.OVERTAKE, t=0.0)) is True
    # Second overtake 2s later is inside the cooldown window -> suppressed.
    assert clf.should_commentate(_event(EventType.OVERTAKE, t=2.0)) is False
    # 6s later the cooldown has elapsed -> allowed again.
    assert clf.should_commentate(_event(EventType.OVERTAKE, t=6.0)) is True


def test_cooldown_is_per_type():
    clf = _classifier(cooldown_seconds=5.0)
    assert clf.should_commentate(_event(EventType.OVERTAKE, t=0.0)) is True
    # A different type at the same instant is unaffected by the overtake cooldown.
    assert clf.should_commentate(_event(EventType.YELLOW_FLAG, t=0.0)) is True
