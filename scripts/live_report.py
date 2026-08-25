"""Build live cycle report data: signals per strategy + positions + closes + champion."""
import json, sqlite3, sys
sys.path.insert(0, '/opt/data/paper-trading-bot')
from hedge_fund.data.binance import CcxtSource
from hedge_fund.signals.momentum import compute_signal

TF = "4h"
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
STRATS = ["sma_stack", "sma_stack_5_20_50", "sma_100_abv",
          "rsi_30_57", "rsi_trend_50", "robust_open"]

data = CcxtSource()

# --- prices ---
px = {}
for s in SYMBOLS:
    try:
        px[s] = data.fetch_price(s)
    except Exception as e:
        px[s] = None
print("PRICES", json.dumps(px))

# --- signals per strategy per symbol ---
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
                      "rsi": round(sig.features.rsi,1)}
        except Exception as e:
            row[s] = {"error": str(e)}
    print("SIG", strat, json.dumps(row))

# --- trades from sqlite ---
con = sqlite3.connect('/opt/data/paper-trading-bot/state/trades.sqlite')
con.row_factory = sqlite3.Row
print("=== OPEN TRADES (exit_ts null) ===")
for r in con.execute("SELECT id,symbol,timeframe,condition,entry_ts,entry_price,exit_ts,exit_price,size,exit_reason,pnl,pnl_pct FROM trades WHERE exit_ts IS NULL ORDER BY id"):
    d = dict(r); d['entry_ts']=d['entry_ts']; print(json.dumps(d, default=str))
print("=== CLOSED TRADES ===")
for r in con.execute("SELECT id,symbol,timeframe,condition,entry_ts,entry_price,exit_ts,exit_price,size,exit_reason,pnl,pnl_pct FROM trades WHERE exit_ts IS NOT NULL ORDER BY id"):
    d = dict(r); print(json.dumps(d, default=str))
print("=== ALL TRADE COUNT ===")
print(con.execute("SELECT COUNT(*) c, SUM(CASE WHEN exit_ts IS NULL THEN 1 ELSE 0 END) open, SUM(CASE WHEN exit_ts IS NOT NULL THEN 1 ELSE 0 END) closed FROM trades").fetchone()["c"])
con.close()