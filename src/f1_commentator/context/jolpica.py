"""Championship and deep historical context from the Jolpica-F1 API.

Jolpica (https://api.jolpi.ca) is the maintained successor to Ergast and covers the
whole championship back to 1950. It gives us two things OpenF1 cannot:

* **Official standings** — real championship points, including fastest-lap bonuses
  and post-race stewards' decisions. This replaces the approximation derived from
  the position channel (2024 R15: 277/199/177 official vs 273/196/172 derived).
* **Deep circuit history** — every running of a venue, not just the 2023+ seasons
  OpenF1 holds. Zandvoort, for instance, goes back to 1952 across 35 races.

OpenF1 stays the source for telemetry-derived colour (safety cars, on-track passes,
rainfall), which Jolpica doesn't carry. Each API is used for what it is actually
good at, and every Jolpica call degrades gracefully — if it's unreachable, the
caller falls back to the OpenF1-derived pack.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

from .models import CircuitRecord, SeasonStanding

logger = logging.getLogger(__name__)

BASE_URL = "https://api.jolpi.ca/ergast/f1"
MIN_INTERVAL = 0.3
MAX_RETRIES = 4
_last_request = 0.0


class JolpicaUnavailable(RuntimeError):
    """Raised when Jolpica cannot be reached — the caller should degrade, not crash."""


def get(path: str, *, timeout: float = 30.0, **params) -> dict:
    """GET ``/{path}.json``, returning the decoded ``MRData`` payload.

    Self-throttled with exponential backoff, mirroring the OpenF1 client.
    """
    global _last_request
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BASE_URL}/{path}.json" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={"User-Agent": "f1-commentator/0.1"})

    delay = 1.0
    for attempt in range(MAX_RETRIES):
        gap = time.monotonic() - _last_request
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
                return json.loads(resp.read().decode("utf-8"))["MRData"]
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError) as exc:
            if attempt == MAX_RETRIES - 1:
                raise JolpicaUnavailable(f"{path}: {exc}") from exc
            time.sleep(delay)
            delay *= 2
    raise JolpicaUnavailable(path)  # pragma: no cover


def _driver_label(driver: dict) -> str:
    """Three-letter code where it exists, else surname (pre-code-era drivers)."""
    return driver.get("code") or driver.get("familyName") or "?"


# --------------------------------------------------------------------------- #
# Pure parsers (unit-testable against captured payloads)
# --------------------------------------------------------------------------- #
def parse_standings(mrdata: dict) -> list[SeasonStanding]:
    """Official driver standings from a ``driverStandings`` payload."""
    lists = mrdata.get("StandingsTable", {}).get("StandingsLists", [])
    if not lists:
        return []
    rows = lists[0].get("DriverStandings", [])
    out: list[SeasonStanding] = []
    leader = float(rows[0]["points"]) if rows else 0.0
    for row in rows:
        pts = float(row["points"])
        constructor = (row.get("Constructors") or [{}])[0].get("name")
        out.append(
            SeasonStanding(
                position=int(row["position"]),
                driver=_driver_label(row["Driver"]),
                points=pts,
                gap_to_leader=round(leader - pts, 1),
                wins=int(row.get("wins", 0)),
                team=constructor,
            )
        )
    return out


def parse_circuit_record(mrdata: dict, circuit: str) -> CircuitRecord | None:
    """All-time record at a circuit from a ``circuits/<id>/results/1`` payload."""
    races = mrdata.get("RaceTable", {}).get("Races", [])
    if not races:
        return None

    wins: Counter[str] = Counter()
    poles_converted = 0
    recent: list[str] = []
    for race in races:
        result = (race.get("Results") or [{}])[0]
        drv = _driver_label(result.get("Driver", {}))
        wins[drv] += 1
        if str(result.get("grid")) == "1":
            poles_converted += 1
    for race in races[-5:][::-1]:
        result = (race.get("Results") or [{}])[0]
        team = result.get("Constructor", {}).get("name", "")
        recent.append(f"{race['season']} {_driver_label(result.get('Driver', {}))}" + (f" ({team})" if team else ""))

    top_driver, top_count = wins.most_common(1)[0]
    seasons = [int(r["season"]) for r in races]
    return CircuitRecord(
        circuit=circuit,
        first_year=min(seasons),
        latest_year=max(seasons),
        races_held=len(races),
        most_wins_driver=top_driver,
        most_wins_count=top_count,
        recent_winners=recent,
        pole_to_win_rate=round(poles_converted / len(races), 2) if races else None,
    )


# --------------------------------------------------------------------------- #
# Online helpers
# --------------------------------------------------------------------------- #
def standings_going_into(year: int, round_number: int) -> list[SeasonStanding]:
    """Official standings *before* ``round_number`` (i.e. after the previous round).

    Round 1 has no prior classification, so this returns an empty table.
    """
    prior = round_number - 1
    if prior < 1:
        return []
    return parse_standings(get(f"{year}/{prior}/driverStandings", limit=30))


def constructor_standings_going_into(year: int, round_number: int) -> list[tuple[str, float]]:
    """Official constructors' table before ``round_number`` as (team, points)."""
    prior = round_number - 1
    if prior < 1:
        return []
    mrdata = get(f"{year}/{prior}/constructorStandings", limit=15)
    lists = mrdata.get("StandingsTable", {}).get("StandingsLists", [])
    if not lists:
        return []
    return [
        (row["Constructor"]["name"], float(row["points"]))
        for row in lists[0].get("ConstructorStandings", [])
    ]


def circuit_record(circuit_id: str, circuit_name: str) -> CircuitRecord | None:
    """Deep all-time record for a circuit (every running, back to 1950)."""
    return parse_circuit_record(get(f"circuits/{circuit_id}/results/1", limit=100), circuit_name)


def find_circuit_id(location: str) -> str | None:
    """Best-effort map an OpenF1 ``location`` to a Jolpica ``circuitId``.

    OpenF1 uses city/venue names ("Zandvoort", "Monza", "Sakhir"); Jolpica uses
    slugs ("zandvoort", "monza", "bahrain"). We try the obvious slug, then fall
    back to a substring search over the circuit list.
    """
    slug = location.lower().replace(" ", "_")
    try:
        mrdata = get(f"circuits/{slug}", limit=1)
        if mrdata.get("CircuitTable", {}).get("Circuits"):
            return slug
    except JolpicaUnavailable:
        pass

    try:
        mrdata = get("circuits", limit=100)
    except JolpicaUnavailable:
        return None
    needle = location.lower()
    for circuit in mrdata.get("CircuitTable", {}).get("Circuits", []):
        haystack = " ".join(
            [
                circuit.get("circuitId", ""),
                circuit.get("circuitName", ""),
                circuit.get("Location", {}).get("locality", ""),
                circuit.get("Location", {}).get("country", ""),
            ]
        ).lower()
        if needle in haystack:
            return circuit["circuitId"]
    return None
