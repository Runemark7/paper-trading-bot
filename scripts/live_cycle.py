"""One Hermes-equivalent paper live cycle for the k8s/compose sidecar.

SEQUENCE:
1. Optional tournament (off by default — Windows discovery farm)
2. Isolated €10k paper cycle across champion accounts
3. Sync live results / graduation
4. Print status report (pod logs; optional Telegram later)

Discovery walk-forwards do **not** run on the 1 CPU / 1.5GiB cycle sidecar
unless ``DISCOVERY_ON_CYCLE=1`` or ``PAPER_DISCOVERY_MODE=on``. Default is
off. Cluster keeps ``run_isolated`` → collect → report. See
``docs/WINDOWS_DISCOVERY.md``.

Exit non-zero if the trading step fails; discovery/report failures are logged
but do not block the rest of the cycle.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from hedge_fund.trading.discovery_mode import discovery_on_cycle
from hedge_fund.trading.stamps import write_pipeline_stamp

ROOT = Path(__file__).resolve().parents[1]


def _run(label: str, argv: list[str], *, critical: bool = False) -> int:
    print(f"live_cycle: {label}", flush=True)
    print("+", " ".join(argv), flush=True)
    try:
        write_pipeline_stamp(label, "started")
    except Exception as exc:
        print(f"live_cycle: stamp start failed: {exc}", flush=True)
    proc = subprocess.run(argv, cwd=str(ROOT))
    try:
        write_pipeline_stamp(label, "finished", exit_code=proc.returncode)
    except Exception as exc:
        print(f"live_cycle: stamp finish failed: {exc}", flush=True)
    if proc.returncode != 0:
        print(f"live_cycle: {label} failed rc={proc.returncode}", flush=True)
        if critical:
            return proc.returncode
    return 0


def main() -> int:
    rc = 0
    if discovery_on_cycle():
        rc = _run(
            "tournament",
            [sys.executable, str(ROOT / "scripts" / "tournament_engine.py")],
        )
    else:
        print(
            "live_cycle: tournament skipped "
            "(DISCOVERY_ON_CYCLE=0 / PAPER_DISCOVERY_MODE=off). "
            "Walk-forwards run on the Windows discovery worker.",
            flush=True,
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
    try:
        write_pipeline_stamp("idle", "finished")
    except Exception as exc:
        print(f"live_cycle: idle stamp failed: {exc}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
