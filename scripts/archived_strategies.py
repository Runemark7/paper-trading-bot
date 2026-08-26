#!/usr/bin/env python3
"""CLI utility to inspect graduated / archived strategies and their 10-trade histories."""
import json
import os
import sys

STATE_DIR = os.environ.get("PAPER_STATE", "/opt/data/paper-trading-bot/state")
GRAD_FILE = os.path.join(STATE_DIR, "graduated.json")

def main():
    if not os.path.exists(GRAD_FILE):
        print("No graduated strategies found yet (state/graduated.json does not exist).")
        return

    try:
        data = json.load(open(GRAD_FILE))
    except Exception as e:
        print(f"Error reading graduated file: {e}")
        return

    if not data:
        print("🏆 Graduated Strategies: None yet (requires 10 closed entries to graduate).")
        return

    print("=" * 80)
    print(f"🏆 GRADUATED STRATEGY ARCHIVE ({len(data)} strategies evaluated)")
    print("=" * 80)

    for idx, strat in enumerate(data, 1):
        status_emoji = "✅" if strat.get("status") == "READY_FOR_LIVE" else "❌"
        pnl = strat.get("total_pnl", 0.0)
        pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
        
        print(f"\n{idx}. {status_emoji} Strategy: {strat['name']}")
        print(f"   Status:        {strat.get('status')} | Graduated: {strat.get('graduated_at', '')[:16].replace('T', ' ')}")
        print(f"   Total P&L:     {pnl_str}")
        print(f"   Win Rate:      {strat.get('win_rate_pct', 0.0)}% ({strat.get('wins', 0)}/{strat.get('closed_trades', 0)} wins)")
        
        history = strat.get("trade_history", [])
        if history:
            print("   Trade Breakdown (10 Trades):")
            print(f"     {'#':<3} {'Symbol':<10} {'Entry $':<10} {'Exit $':<10} {'P&L ($)':<12} {'Return':<9} {'Reason'}")
            print("     " + "-" * 70)
            for t_idx, t in enumerate(history, 1):
                t_pnl = t.get("pnl", 0.0)
                t_pnl_str = f"+${t_pnl:,.2f}" if t_pnl >= 0 else f"-${abs(t_pnl):,.2f}"
                pct = (t.get("pnl_pct", 0.0) * 100)
                pct_str = f"{pct:+.2f}%"
                print(f"     {t_idx:<3} {t.get('symbol', ''):<10} ${t.get('entry_price', 0):<9,.1f} ${t.get('exit_price', 0):<9,.1f} {t_pnl_str:<12} {pct_str:<9} {t.get('exit_reason', '')}")
    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()
