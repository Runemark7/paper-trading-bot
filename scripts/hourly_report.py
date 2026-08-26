"""Clean hourly report generator — one per-isolated-$10k-account overview.

Produces the exact current P&L from the live paper DBs so the cron agent has
truthful numbers to send (rather than guessing from stale rows). Prints the
report text to stdout for the scheduler to deliver.
"""
import sqlite3, os, glob, json, sys
sys.path.insert(0, "/opt/data/paper-trading-bot")
from hedge_fund.data.binance import CcxtSource

STATE = os.environ.get("PAPER_STATE", "/opt/data/paper-trading-bot/state")
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
        lines.append("_No isolated accounts yet._")
        print("\n".join(lines)); return

    # Load graduated list for display
    grad_list = []
    grad_path = os.path.join(STATE, "graduated.json")
    if os.path.exists(grad_path):
        try:
            grad_list = json.load(open(grad_path))
        except Exception:
            pass

    grand = 0.0
    active_with_pos = 0
    for db in dbs:
        acct = _load_acct(db)
        strat = os.path.basename(db)[7:-7]
        c = sqlite3.connect(db); c.row_factory = sqlite3.Row
        opens = c.execute("SELECT symbol,entry_price,size,condition,entry_ts FROM trades WHERE exit_ts IS NULL").fetchall()
        closed = c.execute("SELECT symbol,entry_price,exit_price,pnl,exit_reason,hit FROM trades WHERE exit_ts IS NOT NULL").fetchall()
        c.close()

        # live mark-to-market
        unreal = 0.0
        held = []
        for o in opens:
            cur = px.get(o["symbol"])
            if cur is None:
                continue
            p = (cur - o["entry_price"]) * o["size"]
            unreal += p
            held.append((o["symbol"], o["entry_price"], cur, o["size"], p))
        realized = sum((r["pnl"] or 0) for r in closed)
        equity = START_CASH + realized + unreal
        grand += (equity - START_CASH)

        # account cash from persisted account_state (authoritative starting point)
        acct_cash = None
        if acct and acct.get("broker"):
            acct_cash = acct["broker"].get("cash")

        lines.append(f"### {strat} — ${START_CASH:,.0f} acct ({len(closed)}/10 trades evaluated)")
        if not held and realized == 0:
            lines.append("_Flat — no open positions, waiting for setup._")
        for sym, ep, cur, qty, p in held:
            pct = (cur / ep - 1) * 100 if ep else 0
            lines.append(f"- **{sym}** long {qty:.4f} @ {ep:,.0f} → {cur:,.0f} = **{p:+,.2f} ({pct:+.2f}%)**")
        if realized:
            lines.append(f"- Realized P&L: **{realized:+,.2f}** ({len(closed)} trades)")
        lines.append(f"- **Unrealized: {unreal:+,.2f}** · equity ≈ **{equity:,.0f}**")
        lines.append("")

    lines.append(f"**Active Champions Tested:** {len(dbs)}/10 accounts (Total P&L: {grand:+,.2f})")
    
    if grad_list:
        lines.append("\n🏆 **Graduated Production Strategy Candidates (10/10 trades completed):**")
        for g in grad_list:
            lines.append(f"- **{g['name']}**: P&L {g['total_pnl']:+,.2f} | WinRate: {g['win_rate_pct']}% | Status: `{g['status']}`")
    else:
        lines.append("\n🏆 **Graduated Strategies:** None yet (requires 10 closed entries to graduate).")

    print("\n".join(lines))


if __name__ == "__main__":
    main()