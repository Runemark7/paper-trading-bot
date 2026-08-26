import sqlite3, glob, os, sys
sys.path.insert(0, '/opt/data/paper-trading-bot')
from hedge_fund.data.binance import CcxtSource

state = '/opt/data/paper-trading-bot/state'
src = CcxtSource()
px = {s: src.fetch_price(s) for s in ('BTC/USDT', 'ETH/USDT')}
print('Live Prices:', px)
print('='*50)

for db in sorted(glob.glob(os.path.join(state, 'trades_*.sqlite'))):
    strat = os.path.basename(db)[7:-7]
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    trades = con.execute('SELECT * FROM trades WHERE exit_ts IS NULL').fetchall()
    con.close()
    print(f'Strategy: {strat}')
    for t in trades:
        cur = px[t['symbol']]
        stop = t['entry_price'] * 0.975
        tp = t['entry_price'] * 1.05
        pnl_pct = (cur / t['entry_price'] - 1) * 100
        dist_to_stop_pct = (cur / stop - 1) * 100
        dist_to_tp_pct = (tp / cur - 1) * 100
        print(f"  [{t['symbol']}] Entry: ${t['entry_price']:,.1f} | Stop: ${stop:,.1f} (-2.5%) | TP: ${tp:,.1f} (+5.0%) | Now: ${cur:,.1f} ({pnl_pct:+.2f}%)")
        print(f"    -> Needs {dist_to_stop_pct:.2f}% drop to hit Stop Loss | Needs {dist_to_tp_pct:.2f}% gain to hit Take-Profit")
