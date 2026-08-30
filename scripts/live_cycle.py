"""One Hermes-equivalent paper live cycle for the k8s/compose sidecar.

SEQUENCE (matches the former Hermes ``paperbot-live-cycle`` cron):
1. Continuous discovery / tournament qualification
2. Isolated €10k paper cycle across champion accounts
3. Sync live results / graduation
4. Print hourly status report (pod logs; optional Telegram later)

Exit non-zero if the trading step fails; discovery/report failures are logged
but do not block the rest of the cycle.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(label: str, argv: list[str], *, critical: bool = False) -> int:
    print(f"live_cycle: {label}", flush=True)
    print("+", " ".join(argv), flush=True)
    proc = subprocess.run(argv, cwd=str(ROOT))
    if proc.returncode != 0:
        print(f"live_cycle: {label} failed rc={proc.returncode}", flush=True)
        if critical:
            return proc.returncode
    return 0


def main() -> int:
    rc = _run(
        "tournament",
        [sys.executable, str(ROOT / "scripts" / "tournament_engine.py")],
    )
    trade_rc = _run(
        "run_isolated",
        [sys.executable, "-m", "hedge_fund.trading.run_isolated", "--cycles", "1"],
        critical=True,
    )
    if trade_rc:
        return trade_rc
    _run(
        "collect_live_results",
        [
            sys.executable,
            "-c",
            "from hedge_fund.trading.champions import collect_live_results; "
            "print(collect_live_results())",
        ],
    )
    _run(
        "hourly_report",
        [sys.executable, str(ROOT / "scripts" / "hourly_report.py")],
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
