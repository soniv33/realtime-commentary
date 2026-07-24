"""Audio layer contracts.

Two seams, both swappable and both trivially fakeable in tests:

* ``AudioStreamer`` turns a stream of text chunks into a stream of PCM audio
  bytes (the ElevenLabs adapter). It must yield audio incrementally.
* ``AudioSink`` consumes PCM bytes and renders them (speakers, a file, /dev/null).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable


@runtime_checkable
class AudioStreamer(Protocol):
    """Streams text chunks into synthesised PCM audio, chunk by chunk."""

    async def synthesize(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[bytes]:
        """Yield PCM audio bytes as they are synthesised from the text chunks."""
        ...


@runtime_checkable
class AudioSink(Protocol):
    """Consumes PCM audio bytes and plays/persists them."""

    async def write(self, pcm: bytes) -> None:
        """Render one buffer of PCM audio."""
        ...

    async def close(self) -> None:
        """Flush and release any underlying device/handle."""
        ...
