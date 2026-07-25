"""Tests for the contextual ("colour") knowledge layer — pure, no network."""

from __future__ import annotations

from f1_commentator.context.build import (
    RaceResult,
    build_context,
    driver_form_from_results,
    final_order,
    head_to_head_from_results,
    standings_from_results,
)
from f1_commentator.context.store import ContextStore
from f1_commentator.events import EventType, TelemetryEvent


def _res(year, circuit, order, *, date, key=1, **kw) -> RaceResult:
    return RaceResult(year=year, circuit=circuit, session_key=key, date_start=date, order=order, **kw)


SEASON = [
    _res(2024, "Sakhir", ["VER", "PER", "SAI"], date="2024-03-02", key=1),
    _res(2024, "Jeddah", ["VER", "PER", "LEC"], date="2024-03-09", key=2),
    _res(2024, "Melbourne", ["SAI", "LEC", "NOR"], date="2024-03-24", key=3),
]


def test_final_order_uses_latest_position_per_driver():
    position = [
        {"date": "2024-01-01T13:00:00+00:00", "driver_number": 1, "position": 5},
        {"date": "2024-01-01T13:30:00+00:00", "driver_number": 1, "position": 1},
        {"date": "2024-01-01T13:30:00+00:00", "driver_number": 11, "position": 2},
    ]
    assert final_order(position, {1: "VER", 11: "PER"}) == ["VER", "PER"]


def test_standings_award_points_wins_and_podiums():
    table = standings_from_results(SEASON)
    top = {s.driver: s for s in table}
    assert table[0].driver == "VER"
    assert top["VER"].points == 50          # two wins: 25 + 25
    assert top["VER"].wins == 2
    assert top["SAI"].points == 15 + 25     # P3 then a win
    assert top["VER"].gap_to_leader == 0.0
    assert top["PER"].gap_to_leader > 0
    assert top["PER"].podiums == 2


def test_sprints_score_points_but_not_wins_or_form():
    sprint = _res(2024, "Shanghai", ["NOR", "VER", "PER"], date="2024-04-20", key=99, is_sprint=True)
    table = {s.driver: s for s in standings_from_results([*SEASON, sprint])}
    # Sprint P1 = 8 points, but it is not counted as a win or a podium.
    assert table["NOR"].points == 15 + 8   # P3 at Melbourne + sprint win
    assert table["NOR"].wins == 0
    assert table["NOR"].podiums == 1
    # Form ignores sprints entirely, so NOR's most recent finish is still Melbourne P3.
    form = driver_form_from_results([*SEASON, sprint])
    assert form["NOR"].recent_finishes == [3]


def test_driver_form_is_most_recent_first():
    form = driver_form_from_results(SEASON)
    # Melbourne (24 Mar) is most recent: SAI won it, was P3 in Sakhir.
    assert form["SAI"].recent_finishes[0] == 1
    assert form["SAI"].best_finish == 1
    assert form["NOR"].recent_finishes == [3]


def test_head_to_head_counts_race_finishes():
    h2h = head_to_head_from_results(SEASON, ["VER", "PER"])
    rec = h2h["VER|PER"]
    assert (rec.a_ahead, rec.b_ahead) == (2, 0)  # VER ahead in both races they both finished


def test_build_context_assembles_pack_and_history():
    prior_zandvoort = [
        _res(2023, "Zandvoort", ["VER", "ALO", "GAS"], date="2023-08-27", key=9149,
             safety_cars=2, red_flags=1, on_track_passes=43, rain=True),
    ]
    ctx = build_context(
        year=2024, circuit="Zandvoort", session_key=9582,
        season_results=SEASON, circuit_results=prior_zandvoort,
        entrants=["VER", "PER", "SAI", "LEC", "NOR"],
        round_number=4, rounds_in_season=24,
    )
    assert ctx.standings[0].driver == "VER"
    assert ctx.circuit_history[0].year == 2023
    assert ctx.circuit_history[0].winner == "VER"
    assert ctx.h2h("PER", "VER") is not None  # order-insensitive lookup
    assert "2023 at Zandvoort" in ctx.circuit_history[0].summary()
    assert "wet race" in ctx.circuit_history[0].summary()


# --------------------------------------------------------------------------- #
# ContextStore
# --------------------------------------------------------------------------- #
def _store() -> ContextStore:
    ctx = build_context(
        year=2024, circuit="Zandvoort", session_key=9582,
        season_results=SEASON,
        circuit_results=[_res(2023, "Zandvoort", ["VER", "ALO", "GAS"], date="2023-08-27",
                              safety_cars=2, on_track_passes=43, rain=True)],
        entrants=["VER", "PER", "SAI", "LEC", "NOR"],
        round_number=4, rounds_in_season=24,
    )
    return ContextStore(ctx)


def test_empty_store_yields_no_context():
    store = ContextStore.empty()
    assert store.available is False
    assert store.for_event(TelemetryEvent(type=EventType.OVERTAKE, session_time=1.0)) == []
    assert store.next_beat() is None
    assert store.headline() == ""


def test_overtake_context_includes_head_to_head():
    store = _store()
    ev = TelemetryEvent(type=EventType.OVERTAKE, session_time=10.0, drivers=["PER", "VER"])
    facts = store.for_event(ev)
    assert any("vs" in f and "race finishes" in f for f in facts)


def test_weather_context_references_a_prior_wet_race():
    store = _store()
    ev = TelemetryEvent(type=EventType.WEATHER_CHANGE, session_time=10.0)
    facts = store.for_event(ev)
    assert any("2023" in f and "wet" in f for f in facts)


def test_safety_car_context_uses_circuit_history():
    store = _store()
    ev = TelemetryEvent(type=EventType.SAFETY_CAR, session_time=10.0)
    facts = store.for_event(ev)
    assert any("Zandvoort" in f for f in facts)


def test_race_start_context_has_title_picture():
    store = _store()
    facts = store.for_event(TelemetryEvent(type=EventType.RACE_START, session_time=0.0))
    assert any("Championship" in f for f in facts)


def test_for_event_caps_number_of_facts():
    store = _store()
    ev = TelemetryEvent(type=EventType.OVERTAKE, session_time=10.0, drivers=["PER", "VER"])
    assert len(store.for_event(ev, max_facts=2)) <= 2


def test_beats_rotate_without_repeating_then_reset():
    store = _store()
    seen = []
    while (beat := store.next_beat()) is not None:
        seen.append(beat)
    assert len(seen) == len(set(seen)) and seen, "beats must be unique and non-empty"
    assert store.next_beat() is None      # exhausted
    store.reset_beats()
    assert store.next_beat() is not None  # recycled for long races


def test_headline_describes_the_race():
    assert "2024 Zandvoort" in _store().headline()
    assert "round 4 of 24" in _store().headline()


def test_store_roundtrips_through_json(tmp_path):
    store = _store()
    p = tmp_path / "ctx.json"
    p.write_text(store.context.model_dump_json(), encoding="utf-8")
    loaded = ContextStore.load(p)
    assert loaded.available
    assert loaded.headline() == store.headline()
    assert loaded.context.standings[0].driver == "VER"
