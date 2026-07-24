"""asyncio WebSocket server that broadcasts a replayed race feed.

Every connected client (typically a single orchestrator) receives the same
JSON-encoded ``TelemetryEvent`` stream, paced to mimic a live session. This is a
deliberately thin transport: all the interesting logic lives in ``replay.py`` and
downstream in the orchestrator.
"""

from __future__ import annotations

import asyncio
import logging

import websockets
from websockets.asyncio.server import ServerConnection, serve

from ..config import SimulatorSettings
from .replay import JsonlReplaySource, ReplaySource, paced_replay

logger = logging.getLogger(__name__)


class ReplayServer:
    """Broadcasts a paced replay to all connected WebSocket clients."""

    def __init__(self, settings: SimulatorSettings, source: ReplaySource | None = None) -> None:
        self._settings = settings
        self._source = source or JsonlReplaySource(settings.replay_file)
        self._clients: set[ServerConnection] = set()

    async def _register(self, connection: ServerConnection) -> None:
        """Handler invoked per client connection - just parks the socket open."""
        self._clients.add(connection)
        peer = getattr(connection, "remote_address", None)
        logger.info("client connected: %s (%d total)", peer, len(self._clients))
        try:
            await connection.wait_closed()
        finally:
            self._clients.discard(connection)
            logger.info("client disconnected: %s", peer)

    async def _broadcast(self, payload: str) -> None:
        """Fan out one JSON payload to every live client, dropping dead ones."""
        if not self._clients:
            return
        results = await asyncio.gather(
            *(client.send(payload) for client in list(self._clients)),
            return_exceptions=True,
        )
        for client, result in zip(list(self._clients), results):
            if isinstance(result, Exception):
                self._clients.discard(client)

    async def _run_replay_forever(self) -> None:
        """Continuously (optionally looping) pace and broadcast the replay."""
        while True:
            logger.info("starting replay from %s", self._settings.replay_file)
            async for event in paced_replay(
                self._source,
                speed_multiplier=self._settings.speed_multiplier,
                max_gap_seconds=self._settings.max_gap_seconds,
            ):
                await self._broadcast(event.model_dump_json())
            if not self._settings.loop:
                logger.info("replay complete")
                return
            logger.info("looping replay")

    async def serve_forever(self) -> None:
        """Start the WebSocket server and drive the replay until it finishes."""
        async with serve(self._register, self._settings.host, self._settings.port):
            logger.info("simulator listening on %s", self._settings.ws_url)
            await self._run_replay_forever()
            # Keep the socket open briefly so a client can drain the last event.
            await asyncio.sleep(1.0)


async def main() -> None:
    from ..config import get_settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    server = ReplayServer(settings.simulator)
    try:
        await server.serve_forever()
    except (KeyboardInterrupt, asyncio.CancelledError):  # pragma: no cover
        logger.info("simulator shutting down")


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
