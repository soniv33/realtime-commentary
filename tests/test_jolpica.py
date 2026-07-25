"""Tests for the Jolpica (championship + deep history) parsers — no network.

Payload shapes are copied from real API responses so the parsers are pinned to the
actual contract, including the pre-code-era drivers who have no three-letter code.
"""

from __future__ import annotations

from f1_commentator.context import jolpica
from f1_commentator.context.store import ContextStore
from f1_commentator.events import EventType, TelemetryEvent

STANDINGS = {
    "StandingsTable": {
        "season": "2024",
        "round": "14",
        "StandingsLists": [
            {
                "season": "2024",
                "round": "14",
                "DriverStandings": [
                    {"position": "1", "points": "277", "wins": "7",
                     "Driver": {"code": "VER", "familyName": "Verstappen"},
                     "Constructors": [{"name": "Red Bull"}]},
                    {"position": "2", "points": "199", "wins": "1",
                     "Driver": {"code": "NOR", "familyName": "Norris"},
                     "Constructors": [{"name": "McLaren"}]},
                    {"position": "3", "points": "177", "wins": "1",
                     "Driver": {"code": "LEC", "familyName": "Leclerc"},
                     "Constructors": [{"name": "Ferrari"}]},
                ],
            }
        ],
    }
}

CIRCUIT_RESULTS = {
    "RaceTable": {
        "circuitId": "zandvoort",
        "Races": [
            # 1950s-era entry: no driver code, and won from pole.
            {"season": "1952", "Results": [
                {"grid": "1", "Driver": {"familyName": "Ascari"}, "Constructor": {"name": "Ferrari"}}]},
            {"season": "2022", "Results": [
                {"grid": "1", "Driver": {"code": "VER"}, "Constructor": {"name": "Red Bull"}}]},
            {"season": "2023", "Results": [
                {"grid": "1", "Driver": {"code": "VER"}, "Constructor": {"name": "Red Bull"}}]},
            {"season": "2024", "Results": [
                {"grid": "2", "Driver": {"code": "NOR"}, "Constructor": {"name": "McLaren"}}]},
            {"season": "2025", "Results": [
                {"grid": "2", "Driver": {"code": "PIA"}, "Constructor": {"name": "McLaren"}}]},
        ],
    }
}


def test_parse_standings_uses_official_points_and_gaps():
    rows = jolpica.parse_standings(STANDINGS)
    assert [r.driver for r in rows] == ["VER", "NOR", "LEC"]
    assert rows[0].points == 277 and rows[0].wins == 7
    assert rows[0].gap_to_leader == 0.0
    assert rows[1].gap_to_leader == 78.0    # 277 - 199
    assert rows[1].team == "McLaren"


def test_parse_standings_handles_empty_payload():
    assert jolpica.parse_standings({"StandingsTable": {"StandingsLists": []}}) == []


def test_parse_circuit_record_summarises_deep_history():
    rec = jolpica.parse_circuit_record(CIRCUIT_RESULTS, "Zandvoort")
    assert rec is not None
    assert rec.races_held == 5
    assert rec.first_year == 1952 and rec.latest_year == 2025
    assert rec.most_wins_driver == "VER" and rec.most_wins_count == 2
    assert rec.pole_to_win_rate == 0.6      # 3 of 5 won from pole
    # Newest first, with the constructor attached.
    assert rec.recent_winners[0].startswith("2025 PIA")
    assert "McLaren" in rec.recent_winners[0]


def test_parse_circuit_record_labels_pre_code_era_drivers_by_surname():
    rec = jolpica.parse_circuit_record(
        {"RaceTable": {"Races": [
            {"season": "1952", "Results": [
                {"grid": "1", "Driver": {"familyName": "Ascari"}, "Constructor": {"name": "Ferrari"}}]},
        ]}},
        "Zandvoort",
    )
    assert rec is not None
    assert rec.most_wins_driver == "Ascari"


def test_parse_circuit_record_returns_none_when_no_races():
    assert jolpica.parse_circuit_record({"RaceTable": {"Races": []}}, "Nowhere") is None


def test_circuit_record_summaries_read_naturally():
    rec = jolpica.parse_circuit_record(CIRCUIT_RESULTS, "Zandvoort")
    assert "hosted 5 races since 1952" in rec.summary()
    assert "most wins here" in rec.summary()
    assert rec.recent_summary().startswith("Recent winners at Zandvoort:")


# --------------------------------------------------------------------------- #
# Integration with the store's colour beats
# --------------------------------------------------------------------------- #
def _store_with_record() -> ContextStore:
    from f1_commentator.context.models import RaceContext

    ctx = RaceContext(
        year=2026, circuit="Zandvoort", session_key=1, round_number=15, rounds_in_season=24,
        standings=jolpica.parse_standings(STANDINGS),
        constructor_standings=[("McLaren", 500.0), ("Red Bull", 450.0), ("Ferrari", 400.0)],
        circuit_record=jolpica.parse_circuit_record(CIRCUIT_RESULTS, "Zandvoort"),
        standings_source="official",
    )
    return ContextStore(ctx)


def test_deep_history_appears_in_colour_beats():
    beats = dict(_store_with_record().beats())
    assert "record" in beats and "since 1952" in beats["record"]
    assert "recent_winners" in beats
    assert "constructors" in beats and "McLaren" in beats["constructors"]


def test_race_start_context_prefers_recent_winners():
    store = _store_with_record()
    facts = store.for_event(TelemetryEvent(type=EventType.RACE_START, session_time=0.0))
    assert any("Recent winners" in f for f in facts)


def test_race_finish_credits_winningest_driver_at_venue():
    store = _store_with_record()
    ev = TelemetryEvent(type=EventType.RACE_FINISH, session_time=100.0, drivers=["VER"])
    facts = store.for_event(ev)
    assert any("most wins at Zandvoort" in f for f in facts)
