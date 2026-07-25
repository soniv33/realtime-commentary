"""In-memory fakes for the injectable seams.

These let the whole pipeline run in a unit test with no network, no LLM, and no
audio device - the payoff of keeping every boundary a Protocol.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from f1_commentator.events import TelemetryEvent


class FakeCommentator:
    """Emits a fixed line, one small delta at a time, to mimic token streaming."""

    def __init__(self, line: str = "Leclerc sends it down the inside, brilliant move!") -> None:
        self._line = line
        self.calls: list[TelemetryEvent] = []
        self.contexts: list[list[str]] = []
        self.colour_calls: list[str] = []

    async def stream_commentary(
        self, event: TelemetryEvent, context: list[str] | None = None
    ) -> AsyncIterator[str]:
        self.calls.append(event)
        self.contexts.append(list(context or []))
        # Yield in ~3-char deltas so the chunker sees realistic fragmentation.
        for i in range(0, len(self._line), 3):
            yield self._line[i : i + 3]

    async def stream_colour(
        self, fact: str, *, race: str = "", since_last: float | None = None
    ) -> AsyncIterator[str]:
        self.colour_calls.append(fact)
        line = f"Some context for you: {fact}."
        for i in range(0, len(line), 3):
            yield line[i : i + 3]


class FakeStreamer:
    """Turns each text chunk into deterministic 'audio' bytes."""

    def __init__(self) -> None:
        self.chunks: list[str] = []

    async def synthesize(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[bytes]:
        async for chunk in text_chunks:
            self.chunks.append(chunk)
            yield chunk.encode("utf-8")


class RecordingSink:
    """Captures everything written, so tests can assert on the audio output."""

    def __init__(self) -> None:
        self.written = bytearray()
        self.closed = False

    async def write(self, pcm: bytes) -> None:
        self.written.extend(pcm)

    async def close(self) -> None:
        self.closed = True


async def async_iter(items) -> AsyncIterator:
    """Wrap a sync iterable as an async iterator for feeding the pipeline."""
    for item in items:
        yield item
