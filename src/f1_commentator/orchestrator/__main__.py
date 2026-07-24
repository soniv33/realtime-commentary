"""``python -m f1_commentator.orchestrator`` - the live commentator entrypoint.

Assembles the concrete adapters (Anthropic + ElevenLabs + speakers) from config
and drives the pipeline against the simulator's WebSocket feed. Every dependency
is constructed here and injected, so swapping a provider is a one-line change and
never touches the pipeline logic.
"""

from __future__ import annotations

import asyncio
import logging

from ..audio.elevenlabs_client import ElevenLabsStreamer
from ..audio.playback import NullSink, SpeakerSink
from ..config import get_settings
from ..llm.anthropic_client import AnthropicCommentator
from .classifier import EventClassifier
from .pipeline import CommentaryPipeline

logger = logging.getLogger(__name__)


def build_pipeline(settings) -> CommentaryPipeline:
    """Construct the fully-wired pipeline from settings (composition root)."""
    sink = SpeakerSink(settings.audio.sample_rate) if settings.audio.enable_playback else NullSink()
    return CommentaryPipeline(
        classifier=EventClassifier(settings.orchestrator),
        commentator=AnthropicCommentator(settings.llm),
        streamer=ElevenLabsStreamer(settings.audio),
        sink=sink,
        settings=settings.orchestrator,
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    logger.info(
        "orchestrator starting | model=%s voice=%s offset=%.1fs",
        settings.llm.model,
        settings.audio.voice_id,
        settings.orchestrator.broadcast_offset_seconds,
    )
    pipeline = build_pipeline(settings)
    try:
        await pipeline.connect_and_run(settings.simulator.ws_url)
    except (KeyboardInterrupt, asyncio.CancelledError):  # pragma: no cover
        logger.info("orchestrator shutting down")


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
