"""Anthropic (Claude) streaming commentator.

Defaults to Claude Haiku 4.5 - Anthropic's fast tier - because a broadcast line
is a short, latency-critical generation where time-to-first-token dominates. The
model is configurable, so a demo can trade up to Sonnet/Opus for richer phrasing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import anthropic

from ..config import LLMSettings
from ..events import TelemetryEvent
from ..orchestrator.prompts import (
    COLOUR_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_colour_prompt,
    build_user_prompt,
)


class AnthropicCommentator:
    """Streams one-sentence commentary from a Claude model.

    Implements the :class:`~f1_commentator.llm.base.LLMCommentator` protocol.
    """

    def __init__(self, settings: LLMSettings, client: anthropic.AsyncAnthropic | None = None) -> None:
        self._settings = settings
        # The SDK resolves credentials from the environment (ANTHROPIC_API_KEY or
        # an `ant auth login` profile); no key is hardcoded.
        self._client = client or anthropic.AsyncAnthropic()

    async def _stream(self, system: str, user: str, max_tokens: int) -> AsyncIterator[str]:
        """Shared streaming call — yields text deltas the instant they arrive."""
        async with self._client.messages.stream(
            model=self._settings.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            async for delta in stream.text_stream:
                if delta:
                    yield delta

    async def stream_commentary(
        self, event: TelemetryEvent, context: list[str] | None = None
    ) -> AsyncIterator[str]:
        """Yield Claude's play-by-play text deltas as they arrive.

        Uses ``messages.stream`` and forwards ``text_stream`` so the first token
        leaves this method the instant the model produces it - no waiting for the
        full sentence.
        """
        async for delta in self._stream(
            SYSTEM_PROMPT, build_user_prompt(event, context), self._settings.max_tokens
        ):
            yield delta

    async def stream_colour(
        self, fact: str, *, race: str = "", since_last: float | None = None
    ) -> AsyncIterator[str]:
        """Yield colour-analyst text deltas for one context beat."""
        async for delta in self._stream(
            COLOUR_SYSTEM_PROMPT,
            build_colour_prompt(fact, race=race, since_last=since_last),
            self._settings.colour_max_tokens,
        ):
            yield delta
