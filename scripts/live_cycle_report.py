"""Live cycle report data: prices + signals (run set) + live PnL for open trades."""
import json, sqlite3, sys
from hedge_fund.paths import state_root
from hedge_fund.data.binance import CcxtSource
from hedge_fund.signals.momentum import compute_signal

TF = "4h"
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
# current run set: live default + evolve champion + champion pool
STRATS = ["sma_stack_7_25_50", "sma_100_abv", "rsi_30_57", "robust_open",
          "sma_stack_5_20_50"]

data = CcxtSource()

# --- prices ---
px = {}
for s in SYMBOLS:
    try:
        px[s] = data.fetch_price(s)
    except Exception as e:
        px[s] = None
print("PRICES", json.dumps(px))

# --- signals ---
klines = {}
for s in SYMBOLS:
    try:
        klines[s] = data.fetch_klines(s, TF, limit=300)
    except Exception as e:
        klines[s] = None

for strat in STRATS:
    row = {}
    for s in SYMBOLS:
        ks = klines.get(s)
        try:
            sig = compute_signal(ks, s, TF, strategy=strat)
            row[s] = {"condition": sig.condition, "direction": sig.direction,
                      "score": round(sig.raw_score, 3),
                      "rsi": round(sig.features.rsi, 1),
                      "px": px.get(s)}
        except Exception as e:
            row[s] = {"error": str(e)}
    print("SIG", strat, json.dumps(row))

# --- live PnL for open trades ---
con = sqlite3.connect(str(state_root() / 'trades.sqlite'))
con.row_factory = sqlite3.Row
print("=== OPEN TRADES (live PnL) ===")
for r in con.execute("SELECT * FROM trades WHERE exit_ts IS NULL ORDER BY id"):
    d = dict(r)
    cur = px.get(d['symbol'])
    pnl = None; pnl_pct = None
    if cur and d['entry_price']:
        # gross pnl incl. entry fee (fee already deducted at entry; approx exit fee 0.1%)
        exit_fee = d['entry_price'] * d['size'] * 0.001
        pnl = (cur - d['entry_price']) * d['size'] - d['entry_fee'] - exit_fee
        pnl_pct = (cur - d['entry_price']) / d['entry_price'] * 100
    d['current_price'] = cur
    d['live_pnl'] = round(pnl, 2) if pnl is not None else None
    d['live_pnl_pct'] = round(pnl_pct, 2) if pnl_pct is not None else None
    print(json.dumps(d, default=str))
print("=== CLOSED Trades ===")
for r in con.execute("SELECT * FROM trades WHERE exit_ts IS NOT NULL ORDER BY id"):
    print(json.dumps(dict(r), default=str))
print("=== EVOLVE CHAMPION ===")
champ = json.load(open(state_root() / 'evolve.json')).get('champion')
print(json.dumps(champ))
print("=== ACCOUNT ===")
for r in con.execute("SELECT * FROM account_state"):
    print(dict(r))
con.close()