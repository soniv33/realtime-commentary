"""Local audio sinks.

``SpeakerSink`` streams PCM straight to the default output device via sounddevice
(PortAudio). ``NullSink`` and ``BufferSink`` let the whole pipeline run headless -
in CI, or when benchmarking TTFB without a sound card.
"""

from __future__ import annotations

import asyncio


class NullSink:
    """Discards audio. Useful for headless runs and latency benchmarking."""

    async def write(self, pcm: bytes) -> None:  # noqa: D102
        return None

    async def close(self) -> None:  # noqa: D102
        return None


class BufferSink:
    """Accumulates all PCM in memory. Handy for tests and offline rendering."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    async def write(self, pcm: bytes) -> None:  # noqa: D102
        self.buffer.extend(pcm)

    async def close(self) -> None:  # noqa: D102
        return None


class SpeakerSink:
    """Plays 16-bit mono PCM to the default output device via sounddevice.

    ``sounddevice`` is imported lazily so the package import graph does not need
    PortAudio present (tests and the simulator never touch this class).
    """

    def __init__(self, sample_rate: int = 16_000) -> None:
        import sounddevice  # noqa: PLC0415 - lazy, optional dependency

        self._sd = sounddevice
        self._stream = sounddevice.RawOutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
        )
        self._stream.start()

    async def write(self, pcm: bytes) -> None:
        # RawOutputStream.write blocks on the audio thread; hand it to the default
        # executor so we never stall the event loop feeding new audio in.
        await asyncio.to_thread(self._stream.write, pcm)

    async def close(self) -> None:
        await asyncio.to_thread(self._stream.stop)
        await asyncio.to_thread(self._stream.close)
