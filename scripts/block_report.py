"""Print or publish the exploratory discovery block report.

Read-only with respect to discovery: pre-holdout rows only. The report
does not change the gate, qualification, the trial count, or what gets
minted. ``--publish`` writes ``discovery_blocks.json`` in STATE_ROOT for
the web process to serve (at most once per 10 minutes unless ``--force``).
"""
from __future__ import annotations

import argparse
import json
import os

from hedge_fund.trading.block_report import build_block_report, publish_block_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--nice", type=int, default=0)
    args = parser.parse_args()
    if args.nice:
        try:
            os.nice(args.nice)
        except OSError:
            pass
    if args.publish:
        print(f"block_report: {publish_block_report(force=args.force)}", flush=True)
        return
    print(json.dumps(build_block_report(), indent=2))


if __name__ == "__main__":
    main()
