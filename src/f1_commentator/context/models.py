"""Typed context objects, serialisable to a "context pack" JSON.

These are the facts the colour commentator is allowed to talk about. Keeping them
as an explicit, typed schema (rather than free text) is what stops the LLM from
drifting into invented history: the prompt can only reference what's in here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SeasonStanding(BaseModel):
    """A driver's championship position going *into* the race being narrated."""

    position: int
    driver: str = Field(..., description="Three-letter acronym, e.g. 'VER'.")
    points: float
    gap_to_leader: float = Field(0.0, description="Points behind the championship leader.")
    wins: int = 0
    podiums: int = 0

    def summary(self) -> str:
        gap = "leads the championship" if self.gap_to_leader == 0 else f"{self.gap_to_leader:g} points behind"
        return f"P{self.position} {self.driver} — {self.points:g} pts, {gap}"


class CircuitHistory(BaseModel):
    """What happened at this circuit in a previous season (derived, not recalled)."""

    year: int
    circuit: str
    winner: str | None = None
    podium: list[str] = Field(default_factory=list)
    safety_cars: int = 0
    red_flags: int = 0
    on_track_passes: int = 0
    rain: bool = False

    def summary(self) -> str:
        bits: list[str] = []
        if self.winner:
            bits.append(f"{self.winner} won")
        if self.podium and len(self.podium) >= 3:
            bits.append(f"podium {'/'.join(self.podium[:3])}")
        if self.rain:
            bits.append("wet race")
        if self.safety_cars:
            bits.append(f"{self.safety_cars} safety car{'s' if self.safety_cars > 1 else ''}")
        if self.red_flags:
            bits.append(f"{self.red_flags} red flag{'s' if self.red_flags > 1 else ''}")
        if self.on_track_passes:
            bits.append(f"{self.on_track_passes} on-track passes")
        return f"{self.year} at {self.circuit}: " + ", ".join(bits) if bits else f"{self.year} at {self.circuit}"


class DriverForm(BaseModel):
    """A driver's recent results this season, most recent first."""

    driver: str
    recent_finishes: list[int] = Field(default_factory=list, description="Finishing positions, most recent first.")
    best_finish: int | None = None

    def summary(self) -> str:
        if not self.recent_finishes:
            return f"{self.driver}: no prior races this season"
        runs = ", ".join(f"P{p}" for p in self.recent_finishes[:4])
        return f"{self.driver} recent form: {runs}"


class HeadToHead(BaseModel):
    """Race-finish record between two drivers across the season so far."""

    driver_a: str
    driver_b: str
    a_ahead: int = 0
    b_ahead: int = 0

    def summary(self) -> str:
        return (
            f"{self.driver_a} vs {self.driver_b} this season: "
            f"{self.a_ahead}-{self.b_ahead} on race finishes"
        )


PROVENANCE = (
    "Derived from OpenF1 timing data. Standings count Grand Prix and Sprint points "
    "from finishing order; they exclude fastest-lap bonus points and post-race "
    "stewards' decisions (penalties, disqualifications), so totals can differ from "
    "the official table by a few points."
)


class RaceContext(BaseModel):
    """The complete context pack for one race — the colour analyst's notes."""

    year: int
    circuit: str
    session_key: int
    round_number: int | None = None
    rounds_in_season: int | None = None
    # Stated up front so anyone reading the pack knows exactly how it was computed
    # and where it can drift from official figures.
    provenance: str = PROVENANCE

    standings: list[SeasonStanding] = Field(default_factory=list)
    circuit_history: list[CircuitHistory] = Field(default_factory=list)
    driver_form: dict[str, DriverForm] = Field(default_factory=dict)
    head_to_head: dict[str, HeadToHead] = Field(default_factory=dict)

    def h2h(self, a: str, b: str) -> HeadToHead | None:
        """Look up a pair in either order."""
        return self.head_to_head.get(f"{a}|{b}") or self.head_to_head.get(f"{b}|{a}")

    def title_picture(self, top: int = 3) -> str:
        return "; ".join(s.summary() for s in self.standings[:top])
