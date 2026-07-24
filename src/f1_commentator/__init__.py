"""Real-time F1 telemetry voice commentator.

A Proof-of-Value system that distills live (or replayed) OpenF1/FastF1 telemetry
into multi-signal events, generates punchy broadcast commentary with a fast LLM,
and streams the audio to the local speakers with the lowest possible
Time-To-First-Byte via ElevenLabs.

The package is split into four decoupled layers:

* ``simulator``    - asyncio WebSocket replay server (the "live feed").
* ``orchestrator`` - the LLM orchestration + dual-streaming pipeline.
* ``llm``          - swappable streaming LLM commentator adapters.
* ``audio``        - swappable streaming text-to-speech + playback adapters.
"""

__version__ = "0.1.0"
