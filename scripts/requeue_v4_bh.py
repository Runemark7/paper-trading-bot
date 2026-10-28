#!/usr/bin/env python3
"""Queue discovery-window buy-and-hold misses for one v5 retest.

The set is every stored fail that is only a discovery-window
buy-and-hold comparison under ``gate_v4_*``,
``sltp_cap100_bhdsr_tiled87_20261008``, or
``sltp_cap100_bhdsr_20261008``. Dry-run unless ``--apply``. Does not
read the sealed holdout, does not admit or retire a champion, and
does not add a DSR trial.

    python3 scripts/requeue_v4_bh.py
    python3 scripts/requeue_v4_bh.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hedge_fund.trading.requeue_v4_bh import requeue_v4_bh  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run the discovery-window buy-and-hold retest queue.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the frozen queue. Omit this flag to print the dry-run report only.",
    )
    args = parser.parse_args(argv)
    report = requeue_v4_bh(apply=bool(args.apply))
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
