"""Fetch full BTC/ETH 4h close history by paginating ccxt, save to JSON for backtests."""
import sys, json
sys.path.insert(0, "/opt/data/paper-trading-bot")
from hedge_fund.data.binance import CcxtSource
from datetime import datetime, timezone

src = CcxtSource()

def history(sym, timeframe="4h", max_bars=10000):
    bars = []
    since = None
    while len(bars) < max_bars:
        batch = src.exchange.fetch_ohlcv(sym, timeframe=timeframe, since=since, limit=1000)
        if not batch:
            break
        bars.extend(batch)
        last_ts = batch[-1][0]
        if since is not None and last_ts <= since:
            break
        since = last_ts + 100
    seen = {}
    for b in bars:
        seen[b[0]] = b
    return sorted(seen.values(), key=lambda x: x[0])

out = {}
for sym in ("BTC/USDT", "ETH/USDT"):
    h = history(sym, "4h", 4000)
    out[sym] = [[b[0], b[1], b[2], b[3], b[4]] for b in h]  # ts,open,high,low,close
    print(f"{sym}: {len(h)} bars  "
          f"{datetime.fromtimestamp(h[0][0]/1000, tz=timezone.utc).strftime('%Y-%m-%d')} -> "
          f"{datetime.fromtimestamp(h[-1][0]/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")

with open("/tmp/crypto_history.json", "w") as f:
    json.dump(out, f)
print("saved /tmp/crypto_history.json")