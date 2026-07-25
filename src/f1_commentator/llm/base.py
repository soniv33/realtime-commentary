"""The streaming LLM commentator contract.

The single most important property of these methods is that they are *async
generators of text deltas* — they must yield tokens as the model produces them,
never buffer the whole sentence. That is what makes the dual-streaming
(LLM -> ElevenLabs) pipeline possible.

Two voices, matching a real broadcast booth: reactive play-by-play, and the colour
analyst who fills the quiet stretches with context.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from ..events import TelemetryEvent


@runtime_checkable
class LLMCommentator(Protocol):
    """Turns telemetry events and context beats into streamed broadcast lines."""

    async def stream_commentary(
        self, event: TelemetryEvent, context: list[str] | None = None
    ) -> AsyncIterator[str]:
        """Yield text deltas for a punchy, one-sentence play-by-play call.

        ``context`` carries a few pre-selected, data-derived facts the model may
        weave in as at most one short clause.

        Implementations MUST yield incrementally (token-by-token or small chunks).
        The pipeline intercepts these deltas, groups them by punctuation, and
        forwards each chunk straight to the audio streamer.
        """
        ...

    async def stream_colour(self, fact: str, *, race: str = "", since_last: float | None = None) -> AsyncIterator[str]:
        """Yield text deltas for one sentence of colour context during a lull.

        The model is constrained to the supplied ``fact`` so the analyst voice
        cannot invent history.
        """
        ...
