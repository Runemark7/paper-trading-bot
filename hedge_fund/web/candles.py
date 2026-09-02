"""Read-only public OHLCV for the paper chart.

Public Binance/ccxt klines only — no trading keys, no broker. The chart
refetches this payload; it does not invent ticks between bars.

Production 502s on limit=500 were too fast for a Binance timeout (worker
death / nginx cutting the response). The chart only needs ~3–4h of 5m
history, so we cap well below 500 and never let a ccxt exception become a
bare nginx 502.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from hedge_fund.data.binance import DEFAULT_TIMEFRAME, SUPPORTED_SYMBOLS

ALLOWED_SYMBOLS = tuple(SUPPORTED_SYMBOLS)
ALLOWED_TIMEFRAMES = (DEFAULT_TIMEFRAME,)  # live tape is 5m
CACHE_TTL = 20.0  # seconds; chart refetch cadence, not a tick stream
DEFAULT_LIMIT = 200  # ~16.7h of 5m bars; frontend default lives in this band
MAX_LIMIT = 250  # hard cap — 500 OOMs/crashes the small web worker
FETCH_TIMEOUT = 15.0  # seconds per public venue; nginx must wait longer

_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}
_SOURCES: dict[str, Any] = {}
_SOURCE_LOCK = threading.Lock()


class CandleRequestError(ValueError):
    """Bad query (symbol/timeframe/limit) — HTTP 400."""


class CandleFetchError(RuntimeError):
    """Upstream OHLCV failed — HTTP 503 or 504 JSON, never a bare 502."""

    def __init__(self, message: str, *, status: int = 503) -> None:
        super().__init__(message)
        self.status = 504 if status == 504 else 503


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


def _status_for(exc: BaseException | None) -> int:
    if exc is None:
        return 503
    if isinstance(exc, TimeoutError):
        return 504
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timed out" in msg or "timeout" in msg:
        return 504
    return 503


def _public_source(exchange_id: str):
    """Reuse one public ccxt client per venue. Constructing ccxt.binance()
    per request is what blows the 512Mi web worker — not the candle rows."""
    with _SOURCE_LOCK:
        cached = _SOURCES.get(exchange_id)
        if cached is not None:
            return cached
        from hedge_fund.data.binance import CcxtSource

        src = CcxtSource(exchange_id=exchange_id)
        ex = getattr(src, "exchange", None)
        if ex is not None:
            try:
                ex.timeout = int(FETCH_TIMEOUT * 1000)
            except Exception:  # noqa: BLE001 — mock sources in tests
                pass
            for attr in ("apiKey", "secret", "password", "uid"):
                if hasattr(ex, attr):
                    try:
                        setattr(ex, attr, "")
                    except Exception:  # noqa: BLE001
                        pass
        _SOURCES[exchange_id] = src
        return src


def _public_klines(symbol: str, timeframe: str, limit: int, source=None):
    """Public Binance OHLCV. Fall back to binanceus when .com is geo-blocked (HTTP 451)."""
    if source is not None:
        return source.fetch_klines(symbol, timeframe=timeframe, limit=limit)

    last_exc: Exception | None = None
    for exchange_id in ("binance", "binanceus"):
        try:
            src = _public_source(exchange_id)
            with _SOURCE_LOCK:
                return src.fetch_klines(symbol, timeframe=timeframe, limit=limit)
        except Exception as exc:  # noqa: BLE001 — try the next public venue
            last_exc = exc
            continue
    status = _status_for(last_exc)
    raise CandleFetchError(
        str(last_exc or "no public klines"),
        status=status,
    ) from last_exc


def _fetch_cached(symbol: str, timeframe: str, limit: int, source=None) -> dict:
    now = time.time()
    key = (symbol, timeframe, limit)
    hit = _CACHE.get(key)
    if hit and (now - hit["_ts"] <= CACHE_TTL):
        return {k: v for k, v in hit.items() if k != "_ts"}

    try:
        bars = _public_klines(symbol, timeframe, limit, source=source)
    except CandleFetchError:
        raise
    except Exception as exc:  # noqa: BLE001 — map ccxt/network to JSON 503/504
        raise CandleFetchError(str(exc), status=_status_for(exc)) from exc
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
