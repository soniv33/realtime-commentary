"""Unit tests for the OpenF1 distillation (pure functions, no network)."""

from __future__ import annotations

from datetime import datetime, timezone

from f1_commentator.events import EventType
from f1_commentator.simulator import openf1

T0 = datetime(2023, 1, 1, 13, 0, 0, tzinfo=timezone.utc)
DRIVERS = {1: "VER", 4: "NOR", 44: "HAM", 63: "RUS"}


def _iso(seconds: float) -> str:
    from datetime import timedelta

    return (T0 + timedelta(seconds=seconds)).isoformat()


def test_driver_map_uses_acronym():
    raw = [{"driver_number": 1, "name_acronym": "VER"}, {"driver_number": 99}]
    m = openf1.driver_map(raw)
    assert m[1] == "VER"
    assert m[99] == "#99"  # falls back when acronym missing


def test_race_control_maps_flags_and_drops_pre_race():
    rc = [
        {"date": _iso(-30), "category": "Flag", "flag": "GREEN", "message": "PIT EXIT OPEN"},  # pre-race → dropped
        {"date": _iso(100), "category": "Flag", "flag": "YELLOW", "sector": 5, "message": "YELLOW IN SECTOR 5"},
        {"date": _iso(300), "category": "SafetyCar", "message": "SAFETY CAR DEPLOYED"},
        {"date": _iso(600), "category": "Flag", "flag": "CHEQUERED", "message": "CHEQUERED FLAG"},
    ]
    events = openf1.distil_race_control(rc, T0, DRIVERS)
    types = [e.type for e in events]
    assert EventType.YELLOW_FLAG in types
    assert EventType.SAFETY_CAR in types
    assert EventType.RACE_FINISH in types
    assert all(e.session_time >= 0 for e in events)


def test_race_control_debounces_repeated_flag():
    rc = [
        {"date": _iso(100), "category": "Flag", "flag": "YELLOW", "sector": 1, "message": "YELLOW S1"},
        {"date": _iso(105), "category": "Flag", "flag": "YELLOW", "sector": 2, "message": "YELLOW S2"},  # within 15s
        {"date": _iso(140), "category": "Flag", "flag": "YELLOW", "sector": 3, "message": "YELLOW S3"},  # >15s later
    ]
    events = openf1.distil_race_control(rc, T0, DRIVERS)
    assert len(events) == 2  # the 105s repeat is collapsed


def test_overtake_detects_single_place_swap():
    # VER (1) and NOR (4): NOR is P4, VER P3; then VER drops to P4 and NOR takes P3.
    position = [
        {"date": _iso(10), "driver_number": 1, "position": 3},
        {"date": _iso(10), "driver_number": 4, "position": 4},
        {"date": _iso(50), "driver_number": 4, "position": 3},  # NOR gains P3
        {"date": _iso(50), "driver_number": 1, "position": 4},  # VER cedes P3
    ]
    events = openf1.distil_overtakes(position, T0, DRIVERS, pit=[])
    assert len(events) == 1
    ev = events[0]
    assert ev.type is EventType.OVERTAKE
    assert ev.drivers == ["NOR", "VER"]
    assert ev.payload["position"] == 3


def test_overtake_suppressed_near_pit():
    position = [
        {"date": _iso(10), "driver_number": 1, "position": 3},
        {"date": _iso(10), "driver_number": 4, "position": 4},
        {"date": _iso(50), "driver_number": 4, "position": 3},
        {"date": _iso(50), "driver_number": 1, "position": 4},
    ]
    pit = [{"date": _iso(48), "driver_number": 1}]  # VER just pitted → position churn, not a real pass
    events = openf1.distil_overtakes(position, T0, DRIVERS, pit=pit)
    assert events == []


def test_weather_toggle_debounced():
    weather = [
        {"date": _iso(10), "rainfall": 0},
        {"date": _iso(100), "rainfall": 1},   # onset → emit
        {"date": _iso(130), "rainfall": 0},   # 30s later → debounced (min_gap 120)
        {"date": _iso(400), "rainfall": 1},   # >120s later → emit
    ]
    events = openf1.distil_weather(weather, T0, min_gap=120.0)
    assert len(events) == 2


def test_fastest_lap_only_on_improvement():
    laps = [
        {"lap_number": 2, "lap_duration": 95.0, "date_start": _iso(60), "is_pit_out_lap": False},
        {"lap_number": 3, "lap_duration": 96.0, "date_start": _iso(160), "is_pit_out_lap": False},  # slower → no event
        {"lap_number": 4, "lap_duration": 94.0, "date_start": _iso(260), "is_pit_out_lap": False},  # faster → event
        {"lap_number": 5, "lap_duration": 80.0, "date_start": _iso(360), "is_pit_out_lap": True},   # pit-out ignored
    ]
    events = openf1.distil_fastest_laps(laps, T0, DRIVERS)
    assert len(events) == 2
    assert all(e.type is EventType.FASTEST_LAP for e in events)
