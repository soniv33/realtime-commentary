# realtime-commentary

Ultra-low-latency **voice AI** Proof of Value: a real-time Formula 1 telemetry
voice commentator. It replays historical F1 telemetry as a live feed, distills
threshold events (yellow flags, overtakes, weather, position dynamics), generates
a punchy one-sentence broadcast call with a **fast LLM**, and streams the audio to
your speakers — chaining the LLM token stream *directly* into ElevenLabs to
minimise Time-To-First-Byte.

The defining technical property: **nothing waits for the LLM to finish.** Claude's
tokens are intercepted live, grouped by punctuation, and pushed into ElevenLabs'
streaming-input WebSocket while the model is still writing — so the first phrase is
already speaking as the sentence completes.

## Live demo (GitHub Pages)

There's a self-contained browser demo in [`docs/`](docs/index.html) that runs the
**same dual-streaming pipeline client-side** — replayed telemetry → token stream →
punctuation chunker → ElevenLabs Flash streaming-input WebSocket → Web Audio, with
a live broadcast-offset slider and an event→first-audio TTFB readout.

- **No keys:** shows the full streaming + chunk-dispatch behaviour, muted.
- **Real audio:** paste your own ElevenLabs key (kept in the browser tab only,
  never committed) and it speaks for real.
- **Real Claude (optional):** tick the box and add an Anthropic key to generate the
  commentary with Claude Haiku live instead of the scripted lines.

Once Pages is enabled it's served at `https://<owner>.github.io/realtime-commentary/`.

> Why keys are pasted at runtime: GitHub Pages is static — there's no server to
> hold a secret. The Python backend (or a serverless proxy) is where keys live in
> production; the static demo asks the presenter to supply their own.

## Architecture

```
┌─────────────────────┐   JSON events over    ┌──────────────────────────────────────────┐
│  Simulator          │   local WebSocket     │  Orchestrator (dual-streaming pipeline)    │
│  (replay "live feed")│ ───────────────────▶ │                                            │
│                      │                       │  events ─▶ classifier (threshold gate)     │
│  data/*.jsonl        │                       │            │                               │
│  paced by session_   │                       │            ▼                               │
│  time                │                       │   LLMCommentator.stream_commentary(event)  │
└─────────────────────┘                        │            │ (Claude token deltas, streamed)│
                                               │            ▼                               │
                                               │   chunk_by_punctuation(...)  (phrases)     │
                                               │            ▼                               │
                                               │   ElevenLabs streaming-input (PCM audio)   │
                                               │            ▼                               │
                                               │   broadcast offset ─▶ speakers             │
                                               └──────────────────────────────────────────┘
```

Four decoupled layers, each behind a `Protocol` so any provider is a one-line swap
and the pipeline is testable with in-memory fakes (no network / LLM / audio):

| Layer | Package | Role |
|-------|---------|------|
| Data ingestion | `f1_commentator.simulator` | asyncio WebSocket replay server; reads distilled events from JSONL (FastF1/OpenF1 loader stub included) and broadcasts them paced by `session_time`. |
| Orchestration | `f1_commentator.orchestrator` | threshold classifier, rigid prompts, punctuation chunker, and the dual-streaming pipeline. |
| LLM | `f1_commentator.llm` | `LLMCommentator` protocol + streaming Anthropic (Claude) adapter. |
| Audio | `f1_commentator.audio` | `AudioStreamer` protocol + ElevenLabs streaming-input adapter + speaker/null/buffer sinks. |

## Layout

```
src/f1_commentator/
├── config.py                 # env-driven settings (incl. the broadcast offset)
├── events.py                 # TelemetryEvent schema shared on the wire
├── simulator/
│   ├── replay.py             # JSONL + FastF1(stub) sources, paced replay
│   ├── openf1.py             # OpenF1 → TelemetryEvent distillation (real race data)
│   ├── ingest.py             # CLI: bake a real session to JSONL
│   ├── server.py             # WebSocket broadcast server
│   └── __main__.py           # python -m f1_commentator.simulator
├── context/                  # the parallel "colour analyst" knowledge layer
│   ├── models.py             # typed context pack (standings, history, form, h2h)
│   ├── jolpica.py            # official standings + deep venue history (1950+)
│   ├── build.py              # assemble the pack (pure + online halves, w/ fallback)
│   ├── store.py              # per-event relevance + rotating colour beats
│   └── ingest.py             # CLI: bake a context pack to JSON
├── llm/
│   ├── base.py               # LLMCommentator protocol (play-by-play + colour)
│   └── anthropic_client.py   # Claude streaming adapter
├── audio/
│   ├── base.py               # AudioStreamer / AudioSink protocols
│   ├── elevenlabs_client.py  # ElevenLabs streaming-input WebSocket adapter
│   └── playback.py           # SpeakerSink / NullSink / BufferSink
└── orchestrator/
    ├── classifier.py         # threshold + priority + per-type cooldown
    ├── prompts.py            # rigid one-sentence broadcast prompt
    ├── chunker.py            # punctuation-aware streaming chunker
    ├── pipeline.py           # the dual-streaming pipeline + broadcast offset
    └── __main__.py           # python -m f1_commentator.orchestrator
data/sample_session.jsonl     # a short, self-contained replay
tests/                        # pytest suite (fakes, chunker, classifier, pipeline)
```

## Real race data

The simulator replays a **real F1 session distilled from the [OpenF1](https://openf1.org)
historical API** — by default the eventful 2023 Dutch GP (rain, a safety car, a
VSC, a red flag, 100+ overtakes, VER wins). The distillation reduces OpenF1's raw
channels (race control, pit, laps, position, weather) into the same
`TelemetryEvent` stream everything else consumes, and bakes it to a JSONL file so
the demo is deterministic and offline.

Re-bake it, or pick a different race:

```bash
# by session key (fastest)
python -m f1_commentator.simulator.ingest --session-key 9149 --out data/dutch_gp_2023.jsonl
# or by lookup
python -m f1_commentator.simulator.ingest --year 2023 --country Brazil --session Race --out data/brazil_2023.jsonl
```

Point the simulator at any baked file via `SIM_REPLAY_FILE`. Events play in **real
time** (`SIM_SPEED_MULTIPLIER=1.0`, paced by their real `session_time`); bump the
multiplier for a shorter demo, or raise `SIM_MAX_GAP_SECONDS` for strict,
uncompressed timing. The distillation logic lives in
`simulator/openf1.py` as pure, unit-tested functions; `OpenF1ReplaySource` also
supports live-distilling without a build step. `data/sample_session.jsonl` remains
as a tiny synthetic feed for tests and offline runs.

## Two voices: play-by-play + colour context

A transcript of events isn't broadcast. Real F1 has a lap-by-lap caller *and* a
colour analyst, and this system runs both **in parallel**:

| Track | Trigger | Voice |
|-------|---------|-------|
| **Play-by-play** | a threshold event | reactive, one punchy sentence, **preempts** colour mid-line |
| **Colour** | a lull in the feed | season/circuit context, one sentence, drawn from a rotating pool of beats |

Both share one audio sink, so exactly one voice is ever on air — a narration gate
serialises them and lets the action cut the analyst off, like a real director.

Play-by-play calls are also **enriched**: an overtake between two drivers carries
their season head-to-head and championship positions, so the line has depth
("…their fourth battle this season") instead of just naming the pass.

### Everything is sourced, nothing is invented

The colour analyst can only reference facts in the **context pack** — a typed,
baked JSON artefact. Two APIs feed it, each used for what it's genuinely good at:

| Source | Provides |
|---|---|
| **[Jolpica-F1](https://api.jolpi.ca)** (maintained Ergast successor, complete from 1950) | **Official** championship standings going into the race (points, wins, gaps), constructors' table, and **deep venue history** — every running of the circuit, winningest driver there, pole-to-win rate, recent winners |
| **[OpenF1](https://openf1.org)** (2023+) | telemetry colour Jolpica doesn't carry: safety cars, red flags, on-track passes, rainfall — plus driver form and season head-to-head |

Real output for the 2024 Dutch GP:

```
Championship picture: P1 VER — 277 pts; P2 NOR — 199, 78 behind; P3 LEC — 177, 100 behind
Constructors' championship: Red Bull 408, McLaren 366, Ferrari 345
Zandvoort has hosted 35 races since 1952; Clark has the most wins here with 4;
    49% of them won from pole
Recent winners at Zandvoort: 2025 PIA (McLaren), 2024 NOR (McLaren), 2023 VER, 2022 VER
2023 at Zandvoort: VER won, podium VER/ALO/GAS, wet race, 2 safety cars,
    1 red flag, 107 on-track passes
```

The prompts forbid stating anything outside the event and the supplied facts, so
there is no hallucinated history. Build a pack with:

```bash
python -m f1_commentator.context.ingest --session-key 9582 --out data/zandvoort_2024.context.json
```

Then set `ORCH_CONTEXT_PACK` to it. With no pack the system degrades cleanly to
pure play-by-play.

> **Provenance.** Each pack records its own `provenance` and a `standings_source`
> of `official` or `derived`. If Jolpica is unreachable the build **falls back** to
> standings computed from OpenF1 finishing order — correct to within a few points,
> but missing fastest-lap bonuses and post-race stewards' decisions, and it says so.
> The overtake count is a position-channel heuristic, not the official FIA figure.

## Quick start

```bash
pip install -e ".[dev]"          # or: pip install -r requirements.txt
cp .env.example .env             # add ANTHROPIC_API_KEY and ELEVENLABS_API_KEY

# Terminal 1 — the replay simulator (fake live feed)
make simulator                   # python -m f1_commentator.simulator

# Terminal 2 — the LLM orchestrator + audio streamer
make orchestrator                # python -m f1_commentator.orchestrator
```

No sound card (CI, benchmarking)? Set `TTS_ENABLE_PLAYBACK=false` to route audio
to a null sink while still exercising the full LLM→TTS streaming path.

## The demo knobs (all via `.env` / env vars)

| Knob | Env var | Purpose |
|------|---------|---------|
| **Broadcast offset** | `ORCH_BROADCAST_OFFSET_SECONDS` | Artificially delay the **audio** to sync commentary with a standard (delayed) TV feed. Synthesis still starts immediately, so only speaker output is delayed. |
| Replay speed | `SIM_SPEED_MULTIPLIER` | `>1` fast-forwards the replay for short demos. |
| LLM model | `LLM_MODEL` | `claude-haiku-4-5` (default, fast) → `claude-sonnet-5` for richer phrasing. |
| TTS latency | `TTS_OPTIMIZE_STREAMING_LATENCY` | ElevenLabs latency optimisation 0–4. |
| Threshold | `ORCH_MIN_PRIORITY`, `ORCH_COOLDOWN_SECONDS` | Which events get called, and per-type debounce. |

## Tests

```bash
make test        # pytest -q  → 16 passing
```

The suite runs the entire pipeline against fakes — chunker boundary behaviour,
the threshold/cooldown classifier, and an end-to-end `narrate()` including the
broadcast-offset timing — with no external services.

## Notes for reviewers

- **Fast LLM choice.** The commentator defaults to **Claude Haiku 4.5** — Anthropic's
  speed/cost tier — because a broadcast line is a short, latency-critical generation
  where time-to-first-token dominates. Swap models with one env var.
- **ElevenLabs transport.** The adapter drives ElevenLabs' *input-streaming*
  WebSocket directly (the same transport the ElevenLabs SDK's realtime helper
  wraps). Driving it directly gives the tightest, most observable TTFB control for a
  pre-sales benchmark, and keeps text-in / audio-out fully concurrent.
- **Historical data.** The self-contained JSONL replay stands in for the upstream
  distillation step; `FastF1ReplaySource` marks exactly where a real
  FastF1/OpenF1 historical loader plugs in — no change to the server or pipeline.
- **Credentials** are read from `ANTHROPIC_API_KEY` and `ELEVENLABS_API_KEY`; none
  are hardcoded.
