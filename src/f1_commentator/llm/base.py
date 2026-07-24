"""The streaming LLM commentator contract.

The single most important property of this interface is that ``stream_commentary``
is an *async generator of text deltas* - it must yield tokens as the model
produces them, never buffer the whole sentence. That is what makes the
dual-streaming (LLM -> ElevenLabs) pipeline possible.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from ..events import TelemetryEvent


@runtime_checkable
class LLMCommentator(Protocol):
    """Turns a threshold telemetry event into a streamed broadcast line."""

    async def stream_commentary(self, event: TelemetryEvent) -> AsyncIterator[str]:
        """Yield text deltas for a punchy, one-sentence broadcast update.

        Implementations MUST yield incrementally (token-by-token or small
        chunks). The pipeline intercepts these deltas, groups them by
        punctuation, and forwards each chunk straight to the audio streamer.
        """
        ...
