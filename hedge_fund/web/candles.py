"""Read-only public OHLCV for the paper chart.

Public Binance/ccxt klines only — no trading keys, no broker. The chart
refetches this payload; it does not invent ticks between bars.
"""

from __future__ import annotations

import time
from typing import Any

from hedge_fund.data.binance import DEFAULT_TIMEFRAME, SUPPORTED_SYMBOLS

ALLOWED_SYMBOLS = tuple(SUPPORTED_SYMBOLS)
ALLOWED_TIMEFRAMES = (DEFAULT_TIMEFRAME,)  # live tape is 5m
CACHE_TTL = 20.0  # seconds; chart refetch cadence, not a tick stream
DEFAULT_LIMIT = 500
MAX_LIMIT = 1000

_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}


class CandleRequestError(ValueError):
    """Bad query (symbol/timeframe/limit) — HTTP 400."""


def normalize_symbol(raw: str | None) -> str | None:
    """Accept BTC/USDT, btc-usdt, BTC-USDT. Reject everything else."""
    if not raw:
        return None
    s = raw.strip().upper().replace("-", "/")
    if s in ALLOWED_SYMBOLS:
        return s
    return None


def _parse_limit(raw: str | int | None) -> int:
    if raw is None or raw == "":
        return DEFAULT_LIMIT
    try:
        n = int(raw)
    except (TypeError, ValueError) as exc:
        raise CandleRequestError("limit must be an integer") from exc
    if n < 1:
        raise CandleRequestError("limit must be >= 1")
    return min(n, MAX_LIMIT)


def candles_payload(
    symbol: str | None,
    timeframe: str | None = DEFAULT_TIMEFRAME,
    limit: str | int | None = None,
    *,
    source=None,
) -> dict:
    """Fetch (and briefly cache) public OHLCV for the paper chart."""
    sym = normalize_symbol(symbol)
    if not sym:
        raise CandleRequestError("symbol must be BTC/USDT or ETH/USDT")
    tf = (timeframe or DEFAULT_TIMEFRAME).strip()
    if tf not in ALLOWED_TIMEFRAMES:
        raise CandleRequestError(f"timeframe must be {DEFAULT_TIMEFRAME}")
    n = _parse_limit(limit)
    return _fetch_cached(sym, tf, n, source=source)


def _fetch_cached(symbol: str, timeframe: str, limit: int, source=None) -> dict:
    now = time.time()
    key = (symbol, timeframe, limit)
    hit = _CACHE.get(key)
    if hit and (now - hit["_ts"] <= CACHE_TTL):
        return {k: v for k, v in hit.items() if k != "_ts"}

    src = source
    if src is None:
        from hedge_fund.data.binance import CcxtSource

        src = CcxtSource()
    bars = src.fetch_klines(symbol, timeframe=timeframe, limit=limit)
    candles = [
        {
            "t": int(c.ts),
            "o": float(c.open),
            "h": float(c.high),
            "l": float(c.low),
            "c": float(c.close),
            "v": float(c.volume),
        }
        for c in bars
    ]
    payload = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": candles,
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "source": "binance_public",
        "paper_only": True,
    }
    _CACHE[key] = {**payload, "_ts": now}
    return payload
