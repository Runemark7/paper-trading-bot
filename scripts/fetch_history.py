"""Fetch deep crypto close history and persist it (not /tmp).

Default **5m** (QUAL_TIMEFRAME) — the admit tape. Paginating BACKWARD
(Binance gives the most recent N first; to go deeper you must set `since`
to just before the OLDEST bar of the current page). Persists to
<PAPER_STATE>/crypto_history_<tf>.json and mirrors to /tmp for legacy tools.

Discovery qualification reads crypto_history_5m.json (HIST_TIMEFRAME=5m).
4h fetches may remain for research; they must not admit champions.

Usage:
  python scripts/fetch_history.py
  HIST_TIMEFRAME=5m HIST_BARS=210000 python scripts/fetch_history.py
  HIST_TIMEFRAME=4h HIST_BARS=20000 python scripts/fetch_history.py
"""
import json, os, time
from pathlib import Path
from hedge_fund.paths import state_root
from hedge_fund.data.binance import CcxtSource
from hedge_fund.trading.constants import QUAL_TIMEFRAME, QUAL_N_WINDOWS, QUAL_WINDOW_BARS
from datetime import datetime, timezone

DEFAULT_TF = os.environ.get("HIST_TIMEFRAME", QUAL_TIMEFRAME)
# Cover QUAL_N_WINDOWS × ~90d of 5m (8 × 25920 → 207360 bars, ~720d) plus slack.
# Tracks the walk-forward constants so a longer span fetches more tape.
_DEFAULT_BARS = QUAL_WINDOW_BARS * QUAL_N_WINDOWS + 3000 if DEFAULT_TF == QUAL_TIMEFRAME else 70000
DEFAULT_BARS = int(os.environ.get("HIST_BARS", str(_DEFAULT_BARS)))
# ~1000 bars/page. 300 pages capped a deep 5m fetch around ~280d; 2500
# is enough for multi-year tape (e.g. ~600k bars / ~5y) with overlap.
HIST_FETCH_PAGE_CAP = 2500
STATE_DIR = state_root()
OUT = STATE_DIR / f"crypto_history_{DEFAULT_TF}.json"
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]

src = CcxtSource()


def history(sym, timeframe, max_bars):
    bars = []
    seen = set()
    since = None
    pages = 0
    page_ms = _tf_ms(timeframe) * 1000  # 1000 bars worth of the timeframe
    while len(bars) < max_bars and pages < HIST_FETCH_PAGE_CAP:
        batch = src.exchange.fetch_ohlcv(sym, timeframe=timeframe, since=since, limit=1000)
        if not batch:
            break
        new = 0
        oldest = None
        for b in batch:
            if b[0] not in seen:
                seen.add(b[0]); bars.append(b); new += 1
            if oldest is None or b[0] < oldest:
                oldest = b[0]
        if new == 0:
            # fully overlapped: step back a full page width to reach older bars
            since = (oldest if oldest is not None else (batch[0][0])) - page_ms
        else:
            # step back just before the oldest NEW bar so we don't re-fetch
            since = oldest - 1
        pages += 1
        time.sleep(0.4)
    bars.sort(key=lambda x: x[0])
    return bars


def _tf_ms(timeframe: str) -> int:
    """Bar width in milliseconds for a ccxt timeframe (e.g. '1h' -> 3600000)."""
    from hedge_fund.data.binance import CcxtSource
    import ccxt
    # ccxt parses '1h' fine; fall back to mapping
    m = {"1h": 3600, "4h": 14400, "1d": 86400, "15m": 900, "5m": 300}
    unit = timeframe.lower()
    if unit in m:
        return m[unit] * 1000
    # crude parse: <n><m|h|d>
    import re
    mm = re.match(r"(\d+)([mhd])", unit)
    if mm:
        n = int(mm.group(1)); u = mm.group(2)
        base = {"m": 60, "h": 3600, "d": 86400}[u]
        return n * base * 1000
    return 3600 * 1000  # default 1h


def main():
    out = {}
    for sym in SYMBOLS:
        h = history(sym, DEFAULT_TF, DEFAULT_BARS)
        out[sym] = [[b[0], b[1], b[2], b[3], b[4]] for b in h]
        print(f"{sym}: {len(h)} bars  "
              f"{datetime.fromtimestamp(h[0][0]/1000, tz=timezone.utc).strftime('%Y-%m-%d')} -> "
              f"{datetime.fromtimestamp(h[-1][0]/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out))
    print(f"saved {OUT}")
    # mirror for legacy tools (Linux). Skip on Windows if /tmp is not writable.
    tmp = Path("/tmp/crypto_history.json")
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(out))
        print(f"mirrored -> {tmp}")
    except OSError as exc:
        print(f"skip /tmp mirror ({exc})")


if __name__ == "__main__":
    main()
