#!/usr/bin/env python3
"""Generate the GOAT v1 deck library from the locked reference decklists.

Resolves card names against the pinned Project Ignis card database, validates
every deck against the April 2005 Forbidden & Limited list, and writes
`resources/decks/goat/`.

By default nothing is written unless every deck is valid; use --force to write
anyway (useful when iterating on a discrepancy).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ygobench.cards import CardIndex  # noqa: E402
from ygobench.engine.upstream import UpstreamLayout  # noqa: E402
from ygobench.formats import GOAT_2005  # noqa: E402
from ygobench.goat.build import DECK_ROOT, build_all, write_library  # noqa: E402
from ygobench.legality import parse_lflist  # noqa: E402


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance is best-effort
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DECK_ROOT)
    parser.add_argument("--force", action="store_true", help="write even if validation fails")
    parser.add_argument("--check", action="store_true", help="validate only; write nothing")
    args = parser.parse_args(argv)

    layout = UpstreamLayout()
    if not layout.card_database_dir.is_dir():
        print(f"Card database missing: {layout.card_database_dir}", file=sys.stderr)
        print("Run: ygo-bench setup", file=sys.stderr)
        return 2

    index = CardIndex(layout.card_database_dir)
    lflist_path = GOAT_2005.lflist_path
    assert lflist_path is not None
    limit_list = parse_lflist(lflist_path)
    decks = build_all(index=index, limit_list=limit_list, profile=GOAT_2005)

    failures = 0
    for built in decks:
        counts = f"main={len(built.main)} side={len(built.side)} fusion={len(built.fusion)}"
        print(f"{'PASS' if built.ok else 'FAIL'}  {built.deck.id:<26} {counts}")
        for note in built.aliases_applied:
            print(f"        ~ {note}")
        for error in built.errors:
            print(f"        - {error}")
        failures += not built.ok

    print(f"\n{len(decks) - failures}/{len(decks)} decks valid")

    if args.check:
        return 1 if failures else 0
    if failures and not args.force:
        print("Nothing written. Re-run with --force to write anyway.", file=sys.stderr)
        return 1

    provenance = {
        "card_database": sorted(p.name for p in layout.card_database_dir.glob("*.cdb")),
        "lflist": lflist_path.name,
        "yugi_bench_commit": _git("-C", str(layout.root), "rev-parse", "HEAD"),
        "cardscripts_commit": _git(
            "-C", str(layout.root / "vendor" / "distribution" / "script"), "rev-parse", "HEAD"
        ),
    }
    path = write_library(decks, profile=GOAT_2005, output_dir=args.output, provenance=provenance)
    print(f"Wrote {path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
