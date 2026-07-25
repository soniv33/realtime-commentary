"""Query layer over a :class:`RaceContext`.

Two jobs:

* :meth:`ContextStore.for_event` — pick the *few* facts relevant to the event being
  called, so a play-by-play line can carry depth ("...their fourth battle this
  season") without bloating the prompt or the latency budget.
* :meth:`ContextStore.next_beat` — hand the colour commentator its next talking
  point during a lull, rotating through beats so it never repeats itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..events import EventType, TelemetryEvent
from .models import RaceContext


class ContextStore:
    """Selects relevant context for narration. Cheap, synchronous, side-effect free."""

    def __init__(self, context: RaceContext | None = None) -> None:
        self._ctx = context
        self._used_beats: set[str] = set()

    # -- construction ------------------------------------------------------- #
    @classmethod
    def load(cls, path: str | Path) -> ContextStore:
        """Load a baked context pack (JSON)."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(RaceContext.model_validate(data))

    @classmethod
    def empty(cls) -> ContextStore:
        """A store with no context — the pipeline degrades to pure play-by-play."""
        return cls(None)

    @property
    def context(self) -> RaceContext | None:
        return self._ctx

    @property
    def available(self) -> bool:
        return self._ctx is not None

    def headline(self) -> str:
        """One-line description of the race being narrated, for the system prompt."""
        if not self._ctx:
            return ""
        c = self._ctx
        rnd = f"round {c.round_number}" + (f" of {c.rounds_in_season}" if c.rounds_in_season else "")
        return f"{c.year} {c.circuit} Grand Prix" + (f", {rnd}" if c.round_number else "")

    # -- per-event relevance ------------------------------------------------ #
    def for_event(self, event: TelemetryEvent, *, max_facts: int = 3) -> list[str]:
        """Facts worth weaving into the call for this specific event."""
        if not self._ctx:
            return []
        c, facts = self._ctx, []

        if event.type is EventType.OVERTAKE and len(event.drivers) >= 2:
            a, b = event.drivers[0], event.drivers[1]
            h2h = c.h2h(a, b)
            if h2h:
                facts.append(h2h.summary())
            for drv in (a, b):
                standing = next((s for s in c.standings if s.driver == drv), None)
                if standing:
                    facts.append(standing.summary())

        elif event.type is EventType.RACE_START:
            if c.standings:
                facts.append(f"Championship going in — {c.title_picture()}")
            if c.circuit_history:
                facts.append(c.circuit_history[0].summary())

        elif event.type is EventType.RACE_FINISH:
            winner = event.drivers[0] if event.drivers else None
            standing = next((s for s in c.standings if s.driver == winner), None) if winner else None
            if standing:
                facts.append(standing.summary())
            if c.circuit_history and c.circuit_history[0].winner:
                facts.append(f"{c.circuit_history[0].year} winner here: {c.circuit_history[0].winner}")

        elif event.type in (EventType.SAFETY_CAR, EventType.RED_FLAG, EventType.VIRTUAL_SAFETY_CAR):
            prior = c.circuit_history[0] if c.circuit_history else None
            if prior and (prior.safety_cars or prior.red_flags):
                facts.append(prior.summary())

        elif event.type is EventType.WEATHER_CHANGE:
            wet = next((h for h in c.circuit_history if h.rain), None)
            if wet:
                facts.append(f"{wet.year} here was also wet — {wet.winner} won")

        elif event.drivers:
            drv = event.drivers[0]
            form = c.driver_form.get(drv)
            if form:
                facts.append(form.summary())
            standing = next((s for s in c.standings if s.driver == drv), None)
            if standing:
                facts.append(standing.summary())

        return facts[:max_facts]

    # -- colour beats for lulls -------------------------------------------- #
    def beats(self) -> list[tuple[str, str]]:
        """All available colour talking points as ``(id, fact)`` pairs."""
        if not self._ctx:
            return []
        c, out = self._ctx, []

        if c.standings:
            out.append(("title", f"Championship picture: {c.title_picture(top=3)}"))
            if len(c.standings) >= 2:
                lead, second = c.standings[0], c.standings[1]
                out.append((
                    "title_gap",
                    f"{lead.driver} leads {second.driver} by {second.gap_to_leader:g} points"
                    f" with {lead.wins} win{'s' if lead.wins != 1 else ''} so far",
                ))
        for h in c.circuit_history:
            out.append((f"hist{h.year}", h.summary()))
        if c.round_number and c.rounds_in_season:
            out.append(("round", f"This is round {c.round_number} of {c.rounds_in_season} in {c.year}"))
        # A couple of form notes for drivers near the front of the championship.
        for s in c.standings[:4]:
            form = c.driver_form.get(s.driver)
            if form and form.recent_finishes:
                out.append((f"form{s.driver}", form.summary()))
        return out

    def next_beat(self) -> str | None:
        """Return an unused colour beat, or ``None`` when they're exhausted."""
        for key, fact in self.beats():
            if key not in self._used_beats:
                self._used_beats.add(key)
                return fact
        return None

    def reset_beats(self) -> None:
        self._used_beats.clear()
