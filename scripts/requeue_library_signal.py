#!/usr/bin/env python3
"""Show corrected lib.* names a library claim will re-lease.

There is no ``--apply``. The queue is the discovery log: ``lib.td_cd``,
``lib.pierce``, and ``lib.soldiers`` are leased again until the newest
row carries ``signal_revision``. Stored rows are not deleted. A
corrected pass is seated on ingest like any other canonical ``lib.*``
gate-v7b pass.

    python3 scripts/requeue_library_signal.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hedge_fund.trading.requeue_library_signal import signal_correction_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    del argv
    json.dump(signal_correction_report(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
