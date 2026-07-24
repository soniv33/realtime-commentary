"""Rigid prompt templates for broadcast commentary.

The prompt is deliberately constrained: a fixed persona, hard length cap, and a
single distilled event as input. Rigid prompting keeps latency and cost low and
output predictable - exactly what a live broadcast overlay needs.
"""

from __future__ import annotations

from ..events import TelemetryEvent

SYSTEM_PROMPT = (
    "You are the lead voice commentator for a live Formula 1 broadcast. "
    "You receive one distilled telemetry event and call it for the audience. "
    "Rules, always: respond with exactly ONE punchy sentence; under 20 words; "
    "present tense; broadcast energy; no preamble, no emoji, no markdown, no "
    "quotes; use the standard driver code (e.g. VER, HAM) if drivers are named. "
    "Never invent facts beyond the event you are given."
)


def build_user_prompt(event: TelemetryEvent) -> str:
    """Render a single event into the rigid user turn."""
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
        lines.append(f"CONTEXT: {extras}")
    lines.append("Call it now in one sentence.")
    return "\n".join(lines)
