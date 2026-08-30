"""Clean hourly report generator — one per-isolated-$10k-account overview.

Produces the exact current P&L from the live paper DBs so the cron agent has
truthful numbers to send (rather than guessing from stale rows). Prints the
report text to stdout for the scheduler to deliver.
"""
import sqlite3, os, glob, json, sys
from hedge_fund.paths import state_root
from hedge_fund.data.binance import CcxtSource

STATE = str(state_root())
START_CASH = 10_000.0


def _load_acct(db):
    try:
        c = sqlite3.connect(db)
        r = c.execute("SELECT value FROM account_state ORDER BY rowid DESC LIMIT 1").fetchone()
        c.close()
        return json.loads(r[0]) if r else None
    except Exception:
        return None


def main():
    src = CcxtSource()
    px = {}
    for sym in ("BTC/USDT", "ETH/USDT"):
        try:
            px[sym] = src.fetch_price(sym)
        except Exception:
            px[sym] = None

    lines = []
    lines.append("📊 **PaperBot LIVE** — isolated $10k accts")
    lines.append(f"BTC **${px.get('BTC/USDT') or 0:,.0f}** · ETH **${px.get('ETH/USDT') or 0:,.0f}**\n")

    dbs = sorted(glob.glob(os.path.join(STATE, "trades_*.sqlite")))
    if not dbs:
        lines.append("_No active tournament accounts yet._")
        print("\n".join(lines)); return

    # Load graduated list for display
    grad_list = []
    grad_path = os.path.join(STATE, "graduated.json")
    if os.path.exists(grad_path):
        try:
            grad_list = json.load(open(grad_path))
        except Exception:
            pass

    grand_pnl = 0.0
    active_accounts_summary = []

    for db in dbs:
        acct = _load_acct(db)
        strat = os.path.basename(db)[7:-7]
        c = sqlite3.connect(db); c.row_factory = sqlite3.Row
        opens = c.execute("SELECT symbol,entry_price,size,condition,entry_ts FROM trades WHERE exit_ts IS NULL").fetchall()
        closed = c.execute("SELECT symbol,entry_price,exit_price,pnl,exit_reason,hit FROM trades WHERE exit_ts IS NOT NULL").fetchall()
        c.close()

        unreal = 0.0
        held_desc = []
        for o in opens:
            cur = px.get(o["symbol"])
            if cur is None:
                continue
            p = (cur - o["entry_price"]) * o["size"]
            unreal += p
            pct = (cur / o["entry_price"] - 1) * 100 if o["entry_price"] else 0
            held_desc.append(f"{o['symbol']} {pct:+.1f}%")

        realized = sum((r["pnl"] or 0) for r in closed)
        tot_strat_pnl = realized + unreal
        grand_pnl += tot_strat_pnl

        active_accounts_summary.append({
            "name": strat,
            "closed": len(closed),
            "open_count": len(opens),
            "held_desc": ", ".join(held_desc) if held_desc else "flat",
            "realized": realized,
            "unrealized": unreal,
            "total_pnl": tot_strat_pnl,
        })

    # Sort leaderboard by total PnL desc
    active_accounts_summary.sort(key=lambda x: x["total_pnl"], reverse=True)

    lines.append(f"🏆 **Active Tournament Leaderboard** ({len(dbs)} Strategies in Arena · Target: 25 Trades):")
    lines.append(f"{'Strategy':<26} {'Trades':<8} {'Open Positions':<18} {'Net P&L'}")
    lines.append("-" * 68)

    for item in active_accounts_summary:
        pnl_str = f"+${item['total_pnl']:,.2f}" if item['total_pnl'] >= 0 else f"-${abs(item['total_pnl']):,.2f}"
        lines.append(f"{item['name']:<26} {item['closed']}/25     {item['held_desc']:<18} {pnl_str}")

    lines.append("-" * 68)
    lines.append(f"**Total Arena Net P&L:** {'+$' if grand_pnl >= 0 else '-$'}{abs(grand_pnl):,.2f}")
    
    if grad_list:
        lines.append("\n🎓 **Graduated Strategies (25/25 Trades Completed):**")
        for g in grad_list:
            lines.append(f"- **{g['name']}**: P&L {g['total_pnl']:+,.2f} | WinRate: {g['win_rate_pct']}% | Status: `{g['status']}`")
    else:
        lines.append("\n🎓 **Graduated Strategies:** None yet (requires 25 closed entries to graduate).")

    print("\n".join(lines))


if __name__ == "__main__":
    main()