"""Distil a real F1 session from the OpenF1 historical API into TelemetryEvents.

OpenF1 (https://openf1.org) exposes free historical timing for 2023+ sessions as
plain JSON — no pandas, no cache warm-up. We pull the relevant channels (race
control, pit, laps, position, weather) and reduce them to the same multi-signal
``TelemetryEvent`` vocabulary the rest of the system already speaks.

The reduction logic is split into small **pure** functions that take raw dict
lists and return events, so the distillation is unit-testable without a network.
``distill_session`` is the thin online wrapper that fetches, then distils.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timedelta

from ..events import EventType, TelemetryEvent
from .replay import ReplaySource

BASE_URL = "https://api.openf1.org/v1"

# race_control flag value -> (event type, priority)
_FLAG_MAP: dict[str, tuple[EventType, int]] = {
    "YELLOW": (EventType.YELLOW_FLAG, 78),
    "DOUBLE YELLOW": (EventType.DOUBLE_YELLOW, 90),
    "RED": (EventType.RED_FLAG, 96),
    "GREEN": (EventType.GREEN_FLAG, 84),
}


# --------------------------------------------------------------------------- #
# HTTP client (stdlib only — no extra runtime dependency)
# --------------------------------------------------------------------------- #
def get(path: str, *, timeout: float = 60.0, **params) -> list[dict]:
    """GET ``/{path}`` with query params, returning the decoded JSON list."""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BASE_URL}/{path}?{query}" if query else f"{BASE_URL}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "f1-commentator/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
        return json.loads(resp.read().decode("utf-8"))


def find_session(
    *,
    year: int | None = None,
    country_name: str | None = None,
    location: str | None = None,
    session_name: str = "Race",
    session_key: int | None = None,
) -> dict:
    """Resolve a single session record. Raises if zero or many match."""
    if session_key is not None:
        sessions = get("sessions", session_key=session_key)
    else:
        sessions = get(
            "sessions",
            year=year,
            country_name=country_name,
            location=location,
            session_name=session_name,
        )
    if not sessions:
        raise LookupError("No OpenF1 session matched the given filters.")
    if len(sessions) > 1:
        labels = ", ".join(f"{s['year']} {s['location']} ({s['session_key']})" for s in sessions)
        raise LookupError(f"Multiple sessions matched — narrow the filters: {labels}")
    return sessions[0]


def _parse(dt: str) -> datetime:
    return datetime.fromisoformat(dt)


def _st(dt: str, t0: datetime) -> float:
    """Session time in seconds relative to lights-out (session ``date_start``)."""
    return (_parse(dt) - t0).total_seconds()


# --------------------------------------------------------------------------- #
# Pure per-signal distillers
# --------------------------------------------------------------------------- #
def driver_map(drivers: list[dict]) -> dict[int, str]:
    """driver_number -> three-letter acronym (e.g. 1 -> 'VER')."""
    return {d["driver_number"]: d.get("name_acronym") or f"#{d['driver_number']}" for d in drivers}


def distil_race_control(rc: list[dict], t0: datetime, drivers: dict[int, str]) -> list[TelemetryEvent]:
    """Flags, safety cars and the chequered flag from race-control messages."""
    events: list[TelemetryEvent] = []
    last_emit: dict[EventType, float] = {}
    for m in sorted(rc, key=lambda x: x["date"]):
        st = _st(m["date"], t0)
        if st < 0:  # pre-race formation / pit-lane messages
            continue
        cat, flag = m.get("category"), m.get("flag")
        msg = (m.get("message") or "").strip()
        etype: EventType | None = None
        prio = 50

        if cat == "Flag" and flag in _FLAG_MAP:
            etype, prio = _FLAG_MAP[flag]
        elif cat == "Flag" and flag == "CHEQUERED":
            events.append(
                TelemetryEvent(
                    type=EventType.RACE_FINISH, session_time=st, priority=97,
                    lap=m.get("lap_number"), detail="The chequered flag is out",
                )
            )
            continue
        elif cat == "SafetyCar" and "DEPLOYED" in msg:
            if "VIRTUAL" in msg:
                etype, prio = EventType.VIRTUAL_SAFETY_CAR, 85
            else:
                etype, prio = EventType.SAFETY_CAR, 92

        if etype is None:
            continue
        # Collapse sector-by-sector repeats of the same flag within 15s.
        if etype in last_emit and (st - last_emit[etype]) < 15:
            continue
        last_emit[etype] = st

        num = m.get("driver_number")
        drv = [drivers[num]] if num in drivers else []
        loc = f"sector {m['sector']}" if m.get("sector") else None
        events.append(
            TelemetryEvent(
                type=etype, session_time=st, priority=prio, lap=m.get("lap_number"),
                drivers=drv, location=loc, detail=msg.capitalize() if msg else etype.value.replace("_", " "),
            )
        )
    return events


def distil_pit(pit: list[dict], t0: datetime, drivers: dict[int, str]) -> list[TelemetryEvent]:
    events: list[TelemetryEvent] = []
    for p in sorted(pit, key=lambda x: x["date"]):
        st = _st(p["date"], t0)
        if st < 0:
            continue
        num = p.get("driver_number")
        acr = drivers.get(num)
        dur = p.get("pit_duration")
        detail = f"{acr or f'Car #{num}'} pits" + (f" — {dur:.1f}s in the lane" if dur else "")
        events.append(
            TelemetryEvent(
                type=EventType.PIT_STOP, session_time=st, priority=62, lap=p.get("lap_number"),
                drivers=[acr] if acr else [], detail=detail,
                payload={"pit_duration": dur} if dur else {},
            )
        )
    return events


def distil_fastest_laps(laps: list[dict], t0: datetime, drivers: dict[int, str]) -> list[TelemetryEvent]:
    """Emit an event each time the overall fastest lap of the race improves."""
    completed = []
    for lap in laps:
        dur, start = lap.get("lap_duration"), lap.get("date_start")
        if not dur or not start or lap.get("is_pit_out_lap"):
            continue
        end = _parse(start) + timedelta(seconds=dur)
        completed.append((end, dur, lap))

    events: list[TelemetryEvent] = []
    best: float | None = None
    for end, dur, lap in sorted(completed, key=lambda x: x[0]):
        st = (end - t0).total_seconds()
        if st < 0:
            continue
        if best is None or dur < best:
            best = dur
            acr = drivers.get(lap.get("driver_number"))
            mins, secs = divmod(dur, 60)
            events.append(
                TelemetryEvent(
                    type=EventType.FASTEST_LAP, session_time=st, priority=60, lap=lap.get("lap_number"),
                    drivers=[acr] if acr else [], detail=f"{acr or 'Car'} sets the fastest lap of the race",
                    payload={"time": f"{int(mins)}:{secs:06.3f}"},
                )
            )
    return events


def distil_overtakes(
    position: list[dict], t0: datetime, drivers: dict[int, str], pit: list[dict],
    *, top_n: int = 12, pair_cooldown: float = 45.0,
) -> list[TelemetryEvent]:
    """Detect single-place, on-track position swaps inside the top ``top_n``.

    OpenF1's ``position`` channel emits a record only when a driver's position
    changes. We track the current order and, when a driver gains exactly one
    place, credit the pass against whoever held that slot — suppressing swaps
    near a pit event (pit-out laps churn the order) and debouncing flip-flops.
    This is a pragmatic heuristic, not the official FIA overtake count.
    """
    pit_times: dict[int, list[float]] = {}
    for p in pit:
        pit_times.setdefault(p.get("driver_number"), []).append(_st(p["date"], t0))

    def near_pit(num: int, st: float) -> bool:
        return any(abs(st - pt) < 8 for pt in pit_times.get(num, []))

    events: list[TelemetryEvent] = []
    pos: dict[int, int] = {}
    holder: dict[int, int] = {}  # position -> driver_number currently there
    last_pair: dict[tuple[int, int], float] = {}

    for r in sorted(position, key=lambda x: x["date"]):
        num, new = r["driver_number"], r["position"]
        st = _st(r["date"], t0)
        old = pos.get(num)
        passed = holder.get(new)

        # Maintain the position<->driver maps.
        if old is not None and holder.get(old) == num:
            del holder[old]
        pos[num] = new
        holder[new] = num

        if old is None or st < 0:
            continue
        if new < old and (old - new) == 1 and new <= top_n and passed and passed != num:
            if near_pit(num, st) or near_pit(passed, st):
                continue
            key = (num, passed)
            if key in last_pair and (st - last_pair[key]) < pair_cooldown:
                continue
            last_pair[key] = st
            a, b = drivers.get(num), drivers.get(passed)
            if not a or not b:
                continue
            prio = 90 if new == 1 else 82 if new <= 3 else 74 if new <= 6 else 66
            events.append(
                TelemetryEvent(
                    type=EventType.OVERTAKE, session_time=st, priority=prio,
                    drivers=[a, b], location=f"P{new}",
                    detail=f"{a} passes {b} for P{new}", payload={"position": new},
                )
            )
    return events


def distil_weather(weather: list[dict], t0: datetime, *, min_gap: float = 120.0) -> list[TelemetryEvent]:
    """Rain onset / cessation from the ``rainfall`` channel.

    ``min_gap`` debounces a flickering rain sensor: a toggle is only reported if
    the previous weather event was at least that many seconds earlier, so brief
    on/off drizzle bounces don't spam the feed.
    """
    events: list[TelemetryEvent] = []
    prev: int | None = None
    last_emit: float | None = None
    for w in sorted(weather, key=lambda x: x["date"]):
        st = _st(w["date"], t0)
        rain = 1 if w.get("rainfall") else 0
        if st < 0:
            prev = rain
            continue
        if prev is not None and rain != prev and (last_emit is None or (st - last_emit) >= min_gap):
            if rain:
                detail = "Rain is starting to fall"
            else:
                detail = "The rain has stopped — track is drying"
            events.append(
                TelemetryEvent(
                    type=EventType.WEATHER_CHANGE, session_time=st, priority=74,
                    detail=detail,
                    payload={"track_temp": w.get("track_temperature"), "air_temp": w.get("air_temperature")},
                )
            )
            last_emit = st
        prev = rain
    return events


def _winner(position: list[dict], drivers: dict[int, str]) -> str | None:
    """Driver acronym holding P1 at the final position update."""
    leader = None
    for r in sorted(position, key=lambda x: x["date"]):
        if r["position"] == 1:
            leader = drivers.get(r["driver_number"])
    return leader


def _race_start_finish(
    position: list[dict], t0: datetime, drivers: dict[int, str],
) -> list[TelemetryEvent]:
    """Bookend events: a lights-out start and, if positions allow, the winner."""
    events: list[TelemetryEvent] = []
    ordered = sorted(position, key=lambda x: x["date"])
    if ordered:
        first_date = ordered[0]["date"]
        grid = {r["position"]: drivers.get(r["driver_number"]) for r in ordered if r["date"] == first_date}
        top3 = [grid[p] for p in (1, 2, 3) if grid.get(p)]
        events.append(
            TelemetryEvent(
                type=EventType.RACE_START, session_time=0.0, priority=95, lap=1,
                drivers=top3, detail="Lights out and the race is underway",
            )
        )
    return events


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def distil_events(
    *,
    session_start: datetime,
    drivers: dict[int, str],
    race_control: list[dict],
    pit: list[dict],
    laps: list[dict],
    position: list[dict],
    weather: list[dict],
) -> list[TelemetryEvent]:
    """Pure reduction of raw OpenF1 channels into a time-ordered event stream."""
    events: list[TelemetryEvent] = []
    events += _race_start_finish(position, session_start, drivers)
    events += distil_race_control(race_control, session_start, drivers)
    events += distil_pit(pit, session_start, drivers)
    events += distil_fastest_laps(laps, session_start, drivers)
    events += distil_overtakes(position, session_start, drivers, pit)
    events += distil_weather(weather, session_start)

    # Name the winner on the chequered-flag event for a satisfying closing call.
    winner = _winner(position, drivers)
    if winner:
        for ev in events:
            if ev.type is EventType.RACE_FINISH and not ev.drivers:
                ev.drivers = [winner]
                ev.detail = f"{winner} takes the chequered flag to win"

    events.sort(key=lambda e: e.session_time)
    return events


def distil_session(session: dict) -> list[TelemetryEvent]:
    """Fetch every channel for ``session`` and distil them (online)."""
    key = session["session_key"]
    t0 = _parse(session["date_start"])
    drivers = driver_map(get("drivers", session_key=key))
    return distil_events(
        session_start=t0,
        drivers=drivers,
        race_control=get("race_control", session_key=key),
        pit=get("pit", session_key=key),
        laps=get("laps", session_key=key),
        position=get("position", session_key=key),
        weather=get("weather", session_key=key),
    )


class OpenF1ReplaySource(ReplaySource):
    """A live-distilling replay source (fetches + distils on iteration).

    Prefer baking to JSONL with ``python -m f1_commentator.simulator.ingest`` for
    a deterministic, offline demo; use this when you want the freshest data or to
    avoid a build step.
    """

    def __init__(self, **session_filters) -> None:
        self._filters = session_filters
        self._session: dict | None = None

    def __iter__(self) -> Iterator[TelemetryEvent]:
        self._session = find_session(**self._filters)
        return iter(distil_session(self._session))
