"""Punctuation-aware text chunker for the dual-streaming path.

This sits between the LLM token stream and the audio streamer. It groups incoming
text deltas into the smallest speakable units - flushing at sentence/clause
punctuation - so ElevenLabs can start synthesising the first phrase while the
model is still generating the rest. That overlap is what drives TTFB down.

Flushing at the *earliest* qualifying boundary (not the latest) is deliberate:
we want the first complete phrase on its way to the vendor as soon as possible.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

# Boundaries at which a buffered phrase becomes speakable.
_HARD_BOUNDARIES = frozenset(".!?")
_SOFT_BOUNDARIES = frozenset(",;:")


def _flush_index(buffer: str, min_chunk_chars: int) -> int:
    """Return the index to flush up to (inclusive), or -1 if not ready.

    Hard boundaries (``. ! ?``) always qualify. Soft boundaries (``, ; :``)
    qualify only once the segment is long enough to be worth its own audio clip.
    The earliest qualifying boundary wins.
    """
    hard = min((i for i, c in enumerate(buffer) if c in _HARD_BOUNDARIES), default=-1)
    soft = min(
        (i for i, c in enumerate(buffer) if c in _SOFT_BOUNDARIES and (i + 1) >= min_chunk_chars),
        default=-1,
    )
    candidates = [i for i in (hard, soft) if i != -1]
    return min(candidates) if candidates else -1


async def chunk_by_punctuation(
    deltas: AsyncIterator[str],
    *,
    min_chunk_chars: int = 12,
) -> AsyncIterator[str]:
    """Regroup a stream of text deltas into speakable, punctuation-bounded chunks.

    Handles boundaries anywhere in a delta (real tokens look like ``"flag. "`` or
    ``". Car"``), emits complete phrases as soon as they close, and flushes the
    remainder when the source ends. Emitted chunks carry a trailing space so the
    downstream TTS concatenates phrases naturally.
    """
    buffer = ""
    async for delta in deltas:
        if not delta:
            continue
        buffer += delta
        while True:
            idx = _flush_index(buffer, min_chunk_chars)
            if idx == -1:
                break
            segment = buffer[: idx + 1].strip()
            if segment:
                yield segment + " "
            buffer = buffer[idx + 1 :]

    tail = buffer.strip()
    if tail:
        yield tail + " "
