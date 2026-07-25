"""Derive a :class:`RaceContext` from OpenF1 historical data.

Everything is computed from race results and telemetry — championship points from
finishing orders, circuit history from the same venue in prior seasons, form and
head-to-head from this season's earlier rounds. Nothing is recalled from model
memory, so the colour commentary can't invent history it can't back up.

The heavy lifting is pure: :func:`build_context` takes already-fetched results and
returns the pack, so it is unit-testable offline. :func:`build_from_openf1` is the
thin online wrapper.
"""

from __future__ import annotations

from collections import defaultdict

from ..simulator import openf1
from .models import CircuitHistory, DriverForm, HeadToHead, RaceContext, SeasonStanding

# Championship points. Grands Prix score the top 10; Sprints the top 8. Both are
# counted, otherwise the standings drift well away from the official table (2024
# after 14 rounds: 277 for VER with sprints, 249 without).
#
# The fastest-lap bonus point is intentionally omitted — it was abolished for 2025
# and, where it did apply, only moves a total by one point.
POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
SPRINT_POINTS = [8, 7, 6, 5, 4, 3, 2, 1]


class RaceResult:
    """Finishing order for one completed race, plus a few telemetry-derived notes."""

    def __init__(
        self,
        *,
        year: int,
        circuit: str,
        session_key: int,
        date_start: str,
        order: list[str],
        safety_cars: int = 0,
        red_flags: int = 0,
        on_track_passes: int = 0,
        rain: bool = False,
        is_sprint: bool = False,
    ) -> None:
        self.year = year
        self.circuit = circuit
        self.session_key = session_key
        self.date_start = date_start
        self.order = order  # driver acronyms, P1 first
        self.safety_cars = safety_cars
        self.red_flags = red_flags
        self.on_track_passes = on_track_passes
        self.rain = rain
        self.is_sprint = is_sprint


def final_order(position: list[dict], drivers: dict[int, str]) -> list[str]:
    """Reduce the position channel to a finishing order of driver acronyms."""
    latest: dict[int, int] = {}
    for r in sorted(position, key=lambda x: x["date"]):
        latest[r["driver_number"]] = r["position"]
    ordered = sorted(latest.items(), key=lambda kv: kv[1])
    return [drivers[num] for num, _ in ordered if num in drivers]


# --------------------------------------------------------------------------- #
# Pure derivations
# --------------------------------------------------------------------------- #
def standings_from_results(results: list[RaceResult]) -> list[SeasonStanding]:
    """Championship table from completed sessions (points, wins, podiums).

    Sprint results contribute points on the sprint scale but do **not** count as
    wins or podiums, matching how the championship is reported.
    """
    pts: dict[str, float] = defaultdict(float)
    wins: dict[str, int] = defaultdict(int)
    podiums: dict[str, int] = defaultdict(int)

    for res in results:
        table = SPRINT_POINTS if res.is_sprint else POINTS
        for idx, drv in enumerate(res.order):
            if idx < len(table):
                pts[drv] += table[idx]
            if res.is_sprint:
                continue
            if idx == 0:
                wins[drv] += 1
            if idx < 3:
                podiums[drv] += 1

    ranked = sorted(pts.items(), key=lambda kv: (-kv[1], kv[0]))
    leader = ranked[0][1] if ranked else 0.0
    return [
        SeasonStanding(
            position=i + 1, driver=drv, points=p,
            gap_to_leader=round(leader - p, 1), wins=wins[drv], podiums=podiums[drv],
        )
        for i, (drv, p) in enumerate(ranked)
    ]


def driver_form_from_results(results: list[RaceResult], *, limit: int = 5) -> dict[str, DriverForm]:
    """Recent Grand Prix finishing positions per driver, most recent first.

    Sprints are excluded so "recent form" means the same thing a broadcaster means.
    """
    finishes: dict[str, list[int]] = defaultdict(list)
    for res in sorted((r for r in results if not r.is_sprint), key=lambda r: r.date_start, reverse=True):
        for idx, drv in enumerate(res.order):
            if len(finishes[drv]) < limit:
                finishes[drv].append(idx + 1)
    return {
        drv: DriverForm(driver=drv, recent_finishes=f, best_finish=min(f) if f else None)
        for drv, f in finishes.items()
    }


def head_to_head_from_results(results: list[RaceResult], drivers: list[str]) -> dict[str, HeadToHead]:
    """Pairwise Grand Prix finish records for the given drivers (sprints excluded)."""
    out: dict[str, HeadToHead] = {}
    races = [r for r in results if not r.is_sprint]
    for i, a in enumerate(drivers):
        for b in drivers[i + 1 :]:
            a_ahead = b_ahead = 0
            for res in races:
                if a in res.order and b in res.order:
                    if res.order.index(a) < res.order.index(b):
                        a_ahead += 1
                    else:
                        b_ahead += 1
            if a_ahead or b_ahead:
                out[f"{a}|{b}"] = HeadToHead(driver_a=a, driver_b=b, a_ahead=a_ahead, b_ahead=b_ahead)
    return out


def circuit_history_from_results(results: list[RaceResult]) -> list[CircuitHistory]:
    """Turn prior-season races at this circuit into history entries (newest first)."""
    return [
        CircuitHistory(
            year=r.year, circuit=r.circuit, winner=r.order[0] if r.order else None,
            podium=r.order[:3], safety_cars=r.safety_cars, red_flags=r.red_flags,
            on_track_passes=r.on_track_passes, rain=r.rain,
        )
        for r in sorted(results, key=lambda r: r.year, reverse=True)
    ]


def build_context(
    *,
    year: int,
    circuit: str,
    session_key: int,
    season_results: list[RaceResult],
    circuit_results: list[RaceResult],
    entrants: list[str],
    round_number: int | None = None,
    rounds_in_season: int | None = None,
) -> RaceContext:
    """Assemble the pack from already-fetched results (pure, offline-testable).

    ``season_results`` must contain only races *before* the one being narrated —
    the standings are "going into" this race, as a broadcaster would present them.
    """
    return RaceContext(
        year=year,
        circuit=circuit,
        session_key=session_key,
        round_number=round_number,
        rounds_in_season=rounds_in_season,
        standings=standings_from_results(season_results),
        circuit_history=circuit_history_from_results(circuit_results),
        driver_form=driver_form_from_results(season_results),
        head_to_head=head_to_head_from_results(season_results, entrants),
    )


# --------------------------------------------------------------------------- #
# Online assembly
# --------------------------------------------------------------------------- #
def _telemetry_notes(session_key: int) -> dict:
    """Derive safety cars / red flags / passes / rain for a past race."""
    session = openf1.find_session(session_key=session_key)
    t0 = openf1._parse(session["date_start"])
    drivers = openf1.driver_map(openf1.get("drivers", session_key=session_key))
    rc = openf1.get("race_control", session_key=session_key)
    position = openf1.get("position", session_key=session_key)
    pit = openf1.get("pit", session_key=session_key)
    weather = openf1.get("weather", session_key=session_key)

    events = openf1.distil_race_control(rc, t0, drivers)
    passes = openf1.distil_overtakes(position, t0, drivers, pit)
    return {
        "order": final_order(position, drivers),
        "safety_cars": sum(1 for e in events if e.type.value in ("safety_car", "virtual_safety_car")),
        "red_flags": sum(1 for e in events if e.type.value == "red_flag"),
        "on_track_passes": len(passes),
        "rain": any(w.get("rainfall") for w in weather),
    }


def _result_for(session: dict, *, with_notes: bool) -> RaceResult:
    key = session["session_key"]
    if with_notes:
        notes = _telemetry_notes(key)
    else:
        drivers = openf1.driver_map(openf1.get("drivers", session_key=key))
        notes = {"order": final_order(openf1.get("position", session_key=key), drivers)}
    return RaceResult(
        year=session["year"], circuit=session["location"], session_key=key,
        date_start=session["date_start"], order=notes["order"],
        safety_cars=notes.get("safety_cars", 0), red_flags=notes.get("red_flags", 0),
        on_track_passes=notes.get("on_track_passes", 0), rain=notes.get("rain", False),
        is_sprint=session.get("session_name") == "Sprint",
    )


def build_from_openf1(session: dict, *, history_years: int = 2, progress=None) -> RaceContext:
    """Fetch everything needed and build the context pack for ``session`` (online).

    ``progress`` is an optional ``callable(str)`` for CLI feedback.
    """
    say = progress or (lambda _msg: None)
    year, circuit, key = session["year"], session["location"], session["session_key"]

    # 1) Earlier rounds this season → standings, form, head-to-head. Sprints are
    #    included so championship points match the official table.
    season = list(openf1.get("sessions", year=year, session_name="Race"))
    season.sort(key=lambda s: s["date_start"])
    round_number = next((i + 1 for i, s in enumerate(season) if s["session_key"] == key), None)
    sprints = list(openf1.get("sessions", year=year, session_name="Sprint"))
    scoring = sorted(season + sprints, key=lambda s: s["date_start"])
    prior = [s for s in scoring if s["date_start"] < session["date_start"]]
    say(f"season: {len(prior)} earlier scoring session(s) in {year} "
        f"({sum(1 for s in prior if s.get('session_name') == 'Sprint')} sprint)")
    season_results = []
    for s in prior:
        try:
            season_results.append(_result_for(s, with_notes=False))
        except Exception as exc:  # noqa: BLE001 - a missing round shouldn't sink the pack
            say(f"  ! skipped {s['location']}: {exc}")

    # 2) Same circuit in prior seasons → circuit history (with telemetry notes).
    circuit_results = []
    for y in range(year - 1, year - 1 - history_years, -1):
        try:
            found = openf1.get("sessions", year=y, session_name="Race", location=circuit)
        except Exception:  # noqa: BLE001
            found = []
        for s in found:
            try:
                say(f"circuit history: {y} {circuit}")
                circuit_results.append(_result_for(s, with_notes=True))
            except Exception as exc:  # noqa: BLE001
                say(f"  ! skipped {y} {circuit}: {exc}")

    entrants = list(openf1.driver_map(openf1.get("drivers", session_key=key)).values())
    return build_context(
        year=year, circuit=circuit, session_key=key,
        season_results=season_results, circuit_results=circuit_results,
        entrants=entrants, round_number=round_number, rounds_in_season=len(season) or None,
    )
