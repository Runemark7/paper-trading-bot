#!/usr/bin/env python3
"""Re-score stored discovery rows under gate v5. Dry-run unless --apply.

Prints a JSON report: how many names would be re-labelled, and the top
names by deflated Sharpe then daily Sharpe. Does not admit or retire
champions. Does not read or print secrets.

    python3 scripts/rescore_gate_v5.py
    python3 scripts/rescore_gate_v5.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hedge_fund.trading.rescore_v5 import rescore_v5  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run gate v5 buy-and-hold rescore.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the re-label. Omit this flag to print the dry-run report only.",
    )
    args = parser.parse_args(argv)
    report = rescore_v5(apply=bool(args.apply))
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
