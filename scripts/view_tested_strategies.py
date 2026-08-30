#!/usr/bin/env python3
"""CLI tool to inspect all continuous candidate evaluations and discovery logs."""
import json
import os
import sys

from hedge_fund.paths import state_root
STATE_DIR = str(state_root())
LOG_FILE = os.path.join(STATE_DIR, "discovery_log.json")

def main():
    if not os.path.exists(LOG_FILE):
        print("No discovery evaluation logs found yet. Run tournament_engine.py first.")
        return

    try:
        data = json.load(open(LOG_FILE))
    except Exception as e:
        print(f"Error reading log file: {e}")
        return

    if not data:
        print("No candidate evaluations recorded in log.")
        return

    limit = 30
    print("=" * 85)
    print(f"🔍 CONTINUOUS STRATEGY DISCOVERY & BACKTEST EVALUATIONS (Showing latest {min(len(data), limit)} / {len(data)})")
    print("=" * 85)
    print(f"{'#':<3} {'Strategy Name':<28} {'Status':<11} {'WinRate':<9} {'Sharpe':<8} {'Train PnL':<11} {'Test PnL'}")
    print("-" * 85)

    for idx, item in enumerate(data[:limit], 1):
        status = "✅ QUALIFIED" if item.get("qualified") else "❌ REJECTED"
        win = f"{item.get('win_rate_pct', 0.0):.1f}%"
        sharpe = f"{item.get('sharpe', 0.0):.2f}"
        tr_pnl = f"${item.get('train_pnl', 0.0):+,.1f}"
        te_pnl = f"${item.get('test_pnl', 0.0):+,.1f}"
        print(f"{idx:<3} {item.get('strategy', ''):<28} {status:<11} {win:<9} {sharpe:<8} {tr_pnl:<11} {te_pnl}")

    print("=" * 85)

if __name__ == "__main__":
    main()
