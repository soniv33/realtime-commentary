"""Rigid prompt templates for broadcast commentary.

Two voices, mirroring a real broadcast booth:

* **Play-by-play** — reactive, one punchy sentence, fired by a threshold event. May
  carry one clause of supplied context, but the event is the story.
* **Colour** — the analyst filling a lull with season/circuit context. Never
  invents: it may only use the facts handed to it.

Both prompts are deliberately constrained (fixed persona, hard length cap, and a
"use only the supplied facts" rule) to keep latency low, output predictable, and
history grounded in derived data.
"""

from __future__ import annotations

from ..events import TelemetryEvent

SYSTEM_PROMPT = (
    "You are the lead voice commentator for a live Formula 1 broadcast. "
    "You receive one distilled telemetry event and call it for the audience. "
    "Rules, always: respond with exactly ONE punchy sentence; under 25 words; "
    "present tense; broadcast energy; no preamble, no emoji, no markdown, no "
    "quotes; use the standard driver code (e.g. VER, HAM) if drivers are named. "
    "If CONTEXT lines are supplied you may weave in at most one of them as a short "
    "clause — only if it fits naturally. Never state any fact that is not in the "
    "event or the CONTEXT; never invent history, statistics, or results."
)

COLOUR_SYSTEM_PROMPT = (
    "You are the colour analyst on a live Formula 1 broadcast, speaking during a "
    "quiet stretch of the race while nothing is happening on track. Your job is "
    "background and stakes, not play-by-play. "
    "Rules, always: respond with exactly ONE sentence; under 30 words; "
    "conversational broadcast tone; no preamble, no emoji, no markdown, no quotes; "
    "use standard driver codes (e.g. VER, HAM). "
    "You may ONLY use the facts given to you in FACT. Do not add statistics, "
    "history, results, or predictions of your own — if the fact is thin, say less. "
    "Never invent anything."
)


def build_user_prompt(event: TelemetryEvent, context: list[str] | None = None) -> str:
    """Render one event (plus optional relevant context) into the rigid user turn."""
    lines = [
        f"EVENT: {event.type.value}",
        f"HEADLINE: {event.headline()}",
    ]
    if event.lap is not None:
        lines.append(f"LAP: {event.lap}")
    if event.drivers:
        lines.append(f"DRIVERS: {', '.join(event.drivers)}")
    if event.payload:
        # Compact, deterministic key ordering so identical events cache well.
        extras = ", ".join(f"{k}={event.payload[k]}" for k in sorted(event.payload))
        lines.append(f"CONTEXT_DATA: {extras}")
    for fact in context or []:
        lines.append(f"CONTEXT: {fact}")
    lines.append("Call it now in one sentence.")
    return "\n".join(lines)


def build_colour_prompt(fact: str, *, race: str = "", since_last: float | None = None) -> str:
    """Render a colour beat into a user turn for the analyst voice."""
    lines = []
    if race:
        lines.append(f"RACE: {race}")
    if since_last is not None:
        lines.append(f"SITUATION: no major incident for {since_last:.0f} seconds — fill the gap.")
    lines.append(f"FACT: {fact}")
    lines.append("Give one sentence of context using only that fact.")
    return "\n".join(lines)
