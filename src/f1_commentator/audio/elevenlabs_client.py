"""ElevenLabs streaming-input text-to-speech adapter.

This is the heart of the low-latency audio path. It uses ElevenLabs' WebSocket
*input streaming* endpoint (the same transport the ElevenLabs Python SDK's
realtime helper wraps): we push text chunks in as the LLM produces them and pull
PCM audio out as it is synthesised - both directions concurrent. That overlap is
what minimises Time-To-First-Byte.

Auth uses the ``ELEVENLABS_API_KEY`` environment variable; no key is hardcoded.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import AsyncIterator

import websockets

from ..config import AudioSettings

_ENDPOINT = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"


class ElevenLabsStreamer:
    """Streams text chunks into PCM audio over the ElevenLabs WebSocket.

    Implements the :class:`~f1_commentator.audio.base.AudioStreamer` protocol.
    """

    def __init__(self, settings: AudioSettings, api_key: str | None = None) -> None:
        self._settings = settings
        self._api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")

    def _url(self) -> str:
        s = self._settings
        return (
            _ENDPOINT.format(voice_id=s.voice_id)
            + f"?model_id={s.model}"
            + f"&output_format=pcm_{s.sample_rate}"
            + f"&optimize_streaming_latency={s.optimize_streaming_latency}"
        )

    async def synthesize(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[bytes]:
        """Open the socket, stream text in, and yield PCM audio out.

        A background task forwards text chunks (and the end-of-stream marker) while
        this coroutine yields decoded audio as it arrives - so the first audio
        buffer can play while later text is still being sent.
        """
        if not self._api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set")

        headers = {"xi-api-key": self._api_key}
        async with websockets.connect(self._url(), additional_headers=headers) as ws:
            # BOS: initialise the stream (single leading space per ElevenLabs proto).
            await ws.send(json.dumps({"text": " "}))

            async def _pump_text() -> None:
                async for chunk in text_chunks:
                    if chunk:
                        await ws.send(json.dumps({"text": chunk, "try_trigger_generation": True}))
                # EOS: empty string flushes and closes the generation.
                await ws.send(json.dumps({"text": ""}))

            pump = asyncio.create_task(_pump_text())
            try:
                async for message in ws:
                    data = json.loads(message)
                    audio_b64 = data.get("audio")
                    if audio_b64:
                        yield base64.b64decode(audio_b64)
                    if data.get("isFinal"):
                        break
            finally:
                pump.cancel()
                # Surface pump errors (auth, network) rather than swallow them.
                try:
                    await pump
                except (asyncio.CancelledError, websockets.ConnectionClosed):
                    pass
