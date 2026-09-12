"""5m history fetch — live and qualification tape.

Writes crypto_history_5m.json. Qualification and the live book use 5m
(`scripts/tournament_engine.py`, TradingLoop, CcxtSource.DEFAULT_TIMEFRAME).
4h history must not admit champions.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.paths import state_root
from hedge_fund.data.binance import CcxtSource

STATE_DIR = state_root()
TIMEFRAME = "5m"
MAX_BARS = int(os.environ.get("HIST_BARS", "900000"))  # Up to ~8.5 years of 5m bars
OUT = STATE_DIR / f"crypto_history_{TIMEFRAME}.json"
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]

src = CcxtSource()


def history(sym: str, timeframe: str = TIMEFRAME, max_bars: int = MAX_BARS) -> list[list]:
    bars: list[list] = []
    seen: set[int] = set()
    since = None
    pages = 0
    page_ms = 5 * 60 * 1000 * 1000  # 1000 bars of 5m = 5,000,000 ms (~3.47 days)

    print(f"[{sym}] Starting 5m fetch (target max: {max_bars:,} bars)...", flush=True)
    t0 = time.time()

    while len(bars) < max_bars and pages < 2500:
        try:
            batch = src.exchange.fetch_ohlcv(sym, timeframe=timeframe, since=since, limit=1000)
        except Exception as e:
            print(f"[{sym}] Fetch error on page {pages}: {e}, retrying in 2s...", flush=True)
            time.sleep(2.0)
            continue

        if not batch:
            print(f"[{sym}] Reached data boundary at page {pages}.", flush=True)
            break

        new = 0
        oldest = None
        for b in batch:
            if b[0] not in seen:
                seen.add(b[0])
                bars.append(b)
                new += 1
            if oldest is None or b[0] < oldest:
                oldest = b[0]

        if new == 0:
            base_ts = oldest if oldest is not None else batch[0][0]
            since = base_ts - page_ms
        else:
            since = (oldest - 1) if oldest is not None else None

        pages += 1
        if pages % 50 == 0:
            elapsed = time.time() - t0
            oldest_dt = datetime.fromtimestamp(oldest / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if oldest else "?"
            print(f"[{sym}] Page {pages}: {len(bars):,} bars collected (back to {oldest_dt}) in {elapsed:.1f}s", flush=True)

        time.sleep(0.15)  # Stay within Binance rate limits

    bars.sort(key=lambda x: x[0])
    return bars


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for sym in SYMBOLS:
        h = history(sym)
        out[sym] = [[b[0], b[1], b[2], b[3], b[4]] for b in h]
        start_str = datetime.fromtimestamp(h[0][0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if h else "?"
        end_str = datetime.fromtimestamp(h[-1][0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if h else "?"
        print(f"✅ {sym}: {len(h):,} bars ({start_str} -> {end_str})", flush=True)

    print(f"Writing dataset to {OUT}...", flush=True)
    OUT.write_text(json.dumps(out))
    size_mb = os.path.getsize(OUT) / (1024 * 1024)
    print(f"Saved {OUT} ({size_mb:.2f} MB)", flush=True)


if __name__ == "__main__":
    main()
