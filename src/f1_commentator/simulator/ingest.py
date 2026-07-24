"""Bake a real F1 session into a JSONL replay file.

    python -m f1_commentator.simulator.ingest --year 2023 --country Netherlands --out data/dutch_gp_2023.jsonl
    python -m f1_commentator.simulator.ingest --session-key 9149 --out data/dutch_gp_2023.jsonl

The simulator then replays that file in real time (SIM_SPEED_MULTIPLIER=1.0),
so the demo uses genuine telemetry but stays deterministic and offline.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from . import openf1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Distil a real OpenF1 session into a JSONL replay file.")
    parser.add_argument("--session-key", type=int, help="OpenF1 session_key (skips the lookup).")
    parser.add_argument("--year", type=int, help="Season year, e.g. 2023.")
    parser.add_argument("--country", help="Country name, e.g. Netherlands.")
    parser.add_argument("--location", help="Circuit location, e.g. Zandvoort.")
    parser.add_argument("--session", default="Race", help="Session name (default: Race).")
    parser.add_argument("--out", required=True, help="Output JSONL path.")
    args = parser.parse_args(argv)

    print("Resolving session…", file=sys.stderr)
    session = openf1.find_session(
        session_key=args.session_key,
        year=args.year,
        country_name=args.country,
        location=args.location,
        session_name=args.session,
    )
    print(
        f"  → {session['year']} {session['location']} {session['session_name']} "
        f"(session_key={session['session_key']}, start={session['date_start']})",
        file=sys.stderr,
    )

    print("Fetching + distilling telemetry…", file=sys.stderr)
    events = openf1.distil_session(session)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        fh.write(
            f"# {session['year']} {session['location']} {session['session_name']} "
            f"— distilled from OpenF1 (session_key={session['session_key']})\n"
        )
        for ev in events:
            fh.write(ev.model_dump_json() + "\n")

    counts = Counter(e.type.value for e in events)
    span = events[-1].session_time if events else 0.0
    print(f"\nWrote {len(events)} events across {span/60:.1f} min → {out}", file=sys.stderr)
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
