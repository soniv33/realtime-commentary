"""Bake a race's contextual "colour analyst notes" into a JSON context pack.

    python -m f1_commentator.context.ingest --session-key 9149 --out data/dutch_gp_2023.context.json
    python -m f1_commentator.context.ingest --year 2024 --country Netherlands --out data/zandvoort_2024.context.json

Point the orchestrator at the result via ``ORCH_CONTEXT_PACK``. Baking keeps the
demo deterministic and offline — and keeps the many OpenF1 calls this needs out of
the live path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..simulator import openf1
from .build import build_from_openf1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a contextual race pack from OpenF1 history.")
    parser.add_argument("--session-key", type=int, help="OpenF1 session_key (skips the lookup).")
    parser.add_argument("--year", type=int, help="Season year, e.g. 2024.")
    parser.add_argument("--country", help="Country name, e.g. Netherlands.")
    parser.add_argument("--location", help="Circuit location, e.g. Zandvoort.")
    parser.add_argument("--session", default="Race", help="Session name (default: Race).")
    parser.add_argument(
        "--history-years", type=int, default=2,
        help="How many prior seasons of this circuit to summarise (default: 2).",
    )
    parser.add_argument("--out", required=True, help="Output JSON path.")
    args = parser.parse_args(argv)

    def say(msg: str) -> None:
        print(f"  {msg}", file=sys.stderr)

    print("Resolving session…", file=sys.stderr)
    session = openf1.find_session(
        session_key=args.session_key, year=args.year, country_name=args.country,
        location=args.location, session_name=args.session,
    )
    print(
        f"  → {session['year']} {session['location']} {session['session_name']} "
        f"(session_key={session['session_key']})",
        file=sys.stderr,
    )

    print("Building context (standings, circuit history, form, head-to-head)…", file=sys.stderr)
    ctx = build_from_openf1(session, history_years=args.history_years, progress=say)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(ctx.model_dump_json(indent=2), encoding="utf-8")

    print(f"\nWrote context pack → {out}", file=sys.stderr)
    print(f"  round            : {ctx.round_number or '?'} of {ctx.rounds_in_season or '?'}", file=sys.stderr)
    print(f"  standings        : {len(ctx.standings)} drivers", file=sys.stderr)
    if ctx.standings:
        print(f"    {ctx.title_picture()}", file=sys.stderr)
    print(f"  circuit history  : {len(ctx.circuit_history)} prior year(s)", file=sys.stderr)
    for h in ctx.circuit_history:
        print(f"    {h.summary()}", file=sys.stderr)
    print(f"  driver form      : {len(ctx.driver_form)} drivers", file=sys.stderr)
    print(f"  head-to-head     : {len(ctx.head_to_head)} pairs", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
