"""Centralised, environment-driven configuration.

Every tunable that a pre-sales engineer might want to change during a demo lives
here and can be overridden via environment variables or a local ``.env`` file.
The ``broadcast_offset_seconds`` knob is intentionally surfaced at the top level:
it is the "sync the audio to a TV feed" control called out in the requirements.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SimulatorSettings(BaseSettings):
    """Replay simulator (the fake 'live feed')."""

    model_config = SettingsConfigDict(env_prefix="SIM_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8765
    # A real race distilled from OpenF1 (2023 Dutch GP). See:
    #   python -m f1_commentator.simulator.ingest --session-key 9149 --out data/dutch_gp_2023.jsonl
    # data/sample_session.jsonl is a short synthetic feed kept for tests/offline.
    replay_file: str = "data/dutch_gp_2023.jsonl"
    # 1.0 = real time (events paced by their real session_time). >1 fast-forwards
    # for a short demo (e.g. 10 replays a 2.5h race in ~15 min of active feed).
    speed_multiplier: float = 1.0
    # Cap on the wait between events so long green-flag / red-flag stretches don't
    # stall the demo. Raise it (e.g. 100000) for strict, uncompressed real time.
    max_gap_seconds: float = 20.0
    loop: bool = Field(False, description="Restart the replay when it ends.")

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}"


class LLMSettings(BaseSettings):
    """Fast LLM commentator."""

    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", extra="ignore")

    # Haiku is Anthropic's speed/cost tier - the right pick for a punchy,
    # one-sentence broadcast line with the lowest time-to-first-token.
    model: str = "claude-haiku-4-5"
    max_tokens: int = 90
    # Colour lines run slightly longer than a play-by-play call.
    colour_max_tokens: int = 120
    # Kept tight: broadcast lines are one sentence. See prompts.py.


class AudioSettings(BaseSettings):
    """ElevenLabs streaming TTS + local playback."""

    model_config = SettingsConfigDict(env_prefix="TTS_", env_file=".env", extra="ignore")

    voice_id: str = "JBFqnCBsd6RMkjVDRZzb"  # ElevenLabs stock voice ("George").
    model: str = "eleven_flash_v2_5"  # Flash = lowest-latency ElevenLabs model.
    # PCM 16-bit mono at 16 kHz keeps the audio path simple and low-latency.
    sample_rate: int = 16_000
    # ElevenLabs input-streaming latency optimisation (0-4). 3 is a good demo default.
    optimize_streaming_latency: int = 3
    # Play through the speakers. Disable for headless CI / benchmarking runs.
    enable_playback: bool = True


class OrchestratorSettings(BaseSettings):
    """The orchestration layer that wires the pipeline together."""

    model_config = SettingsConfigDict(env_prefix="ORCH_", env_file=".env", extra="ignore")

    # THE broadcast offset: artificially delay the *audio* so commentary lands in
    # sync with a standard (delayed) TV feed. 0.0 = live.
    broadcast_offset_seconds: float = 0.0
    # Per-event-type cooldown so a flurry of overtakes doesn't spam the commentator.
    cooldown_seconds: float = 4.0
    # Drop any commentary request below this priority.
    min_priority: int = 40

    # --- Colour commentary (the parallel context track) ---------------------
    # Baked context pack (see: python -m f1_commentator.context.ingest). Empty
    # disables the colour track and the system runs pure play-by-play.
    context_pack: str = ""
    enable_colour: bool = True
    # Speak a colour beat once the feed has been quiet this long (wall seconds).
    colour_after_silence_seconds: float = 12.0
    # Never let colour lines run closer together than this.
    colour_min_interval_seconds: float = 25.0


class Settings(BaseSettings):
    """Top-level aggregate. Import ``get_settings()`` everywhere else."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    simulator: SimulatorSettings = Field(default_factory=SimulatorSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)


def get_settings() -> Settings:
    """Build settings from the environment. Cheap enough to call at startup."""
    return Settings()
