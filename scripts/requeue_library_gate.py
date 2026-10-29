#!/usr/bin/env python3
"""Queue canonical lib.* names whose newest log row is an older gate tag.

Dry-run unless ``--apply``. Does not admit, retire, or edit a champion
row or a paper account. A pass on the later ingest is proposed.

On the prod web container (after this image is the one running, so
``GATE_RULES`` is ``gate_v7b_library_profit_20261029``):

    python3 /app/scripts/requeue_library_gate.py
    python3 /app/scripts/requeue_library_gate.py --apply

Locally, with ``PAPER_STATE`` pointed at the state directory:

    python3 scripts/requeue_library_gate.py
    python3 scripts/requeue_library_gate.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hedge_fund.trading.requeue_library_gate import requeue_library_gate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run the library gate-tag requeue.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the frozen queue. Omit this flag to print the dry-run report only.",
    )
    args = parser.parse_args(argv)
    report = requeue_library_gate(apply=bool(args.apply))
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
