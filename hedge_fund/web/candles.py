"""Read-only public OHLCV for the paper chart.

Public Binance/ccxt klines only — no trading keys, no broker. The chart
refetches this payload; it does not invent ticks between bars.

Production 502s on ``limit=500`` were too fast for a Binance timeout
(worker death / nginx cutting the response). Root cause: constructing
``ccxt.binance()`` per request OOMs the 512Mi web worker — not the candle
rows. We reuse one ``CcxtSource`` per venue, page public klines in chunks
of ``CHUNK_SIZE`` (never a one-shot ``limit=500+``), and map ccxt/timeout
to JSON 503/504 (never a bare nginx 502).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from hedge_fund.data.binance import DEFAULT_TIMEFRAME, SUPPORTED_SYMBOLS

ALLOWED_SYMBOLS = tuple(SUPPORTED_SYMBOLS)
ALLOWED_TIMEFRAMES = (DEFAULT_TIMEFRAME,)  # live tape is 5m
CACHE_TTL = 20.0  # seconds; chart refetch cadence, not a tick stream
TF_MS = 5 * 60 * 1000  # 5m bar width in unix ms
BARS_PER_DAY = 24 * 12  # 288
DEFAULT_DAYS = 3
MAX_DAYS = 7
DEFAULT_LIMIT = DEFAULT_DAYS * BARS_PER_DAY  # 864 ≈ 3 calendar days of 5m
MAX_LIMIT = MAX_DAYS * BARS_PER_DAY  # 2016 ≈ 7 calendar days of 5m
CHUNK_SIZE = 200  # known-safe public kline page; do not one-shot 500+
ENTRY_PAD_BARS = 12  # 1h of 5m so an entry is not glued to the left edge
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


def _parse_since(raw: str | int | None) -> int | None:
    """Optional unix-ms start. None if omitted. 400 on garbage. Do not invent."""
    if raw is None or raw == "":
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError) as exc:
        raise CandleRequestError("since must be an integer (unix ms)") from exc
    if n < 0:
        raise CandleRequestError("since must be >= 0")
    return n


def _limit_for_since(n: int, since_ms: int | None) -> int:
    """Grow ``n`` so the window reaches ``since_ms``, still capped at 7 days."""
    if since_ms is None:
        return n
    now_ms = int(time.time() * 1000)
    if since_ms >= now_ms:
        return n
    span = (now_ms - since_ms) // TF_MS + 1 + ENTRY_PAD_BARS
    return min(MAX_LIMIT, max(n, span))


def candles_payload(
    symbol: str | None,
    timeframe: str | None = DEFAULT_TIMEFRAME,
    limit: str | int | None = None,
    *,
    source=None,
    since: str | int | None = None,
) -> dict:
    """Fetch (and briefly cache) public OHLCV for the paper chart."""
    sym = normalize_symbol(symbol)
    if not sym:
        raise CandleRequestError("symbol must be BTC/USDT or ETH/USDT")
    tf = (timeframe or DEFAULT_TIMEFRAME).strip()
    if tf not in ALLOWED_TIMEFRAMES:
        raise CandleRequestError(f"timeframe must be {DEFAULT_TIMEFRAME}")
    n = _limit_for_since(_parse_limit(limit), _parse_since(since))
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


def _call_klines(src, symbol: str, timeframe: str, limit: int, since: int | None = None):
    """One public page. Never ask for more than CHUNK_SIZE. No new ccxt client."""
    n = min(max(1, int(limit)), CHUNK_SIZE)
    if since is None:
        return list(src.fetch_klines(symbol, timeframe=timeframe, limit=n))
    return list(src.fetch_klines(symbol, timeframe=timeframe, limit=n, since=since))


def _fetch_last_n(src, symbol: str, timeframe: str, n: int):
    """Newest ``n`` 5m bars, paged in CHUNK_SIZE pieces on this source only."""
    want = min(max(1, n), MAX_LIMIT)
    if want <= CHUNK_SIZE:
        bars = _call_klines(src, symbol, timeframe, want)
        bars.sort(key=lambda c: c.ts)
        return bars[-want:]

    newest = _call_klines(src, symbol, timeframe, CHUNK_SIZE)
    newest.sort(key=lambda c: c.ts)
    out = list(newest)
    seen = {c.ts for c in out}
    while len(out) < want:
        oldest_ts = out[0].ts
        need = min(CHUNK_SIZE, want - len(out))
        page_since = oldest_ts - need * TF_MS
        try:
            chunk = _call_klines(src, symbol, timeframe, need, since=page_since)
        except RuntimeError:
            # Venue has no older bars (CcxtSource raises on an empty page).
            break
        older = [c for c in chunk if c.ts < oldest_ts and c.ts not in seen]
        if not older:
            break
        older.sort(key=lambda c: c.ts)
        for c in older:
            seen.add(c.ts)
        out = older + out
        if len(chunk) < need:
            break
    out.sort(key=lambda c: c.ts)
    return out[-want:]


def _public_klines(symbol: str, timeframe: str, limit: int, source=None):
    """Public Binance OHLCV. Fall back to binanceus when .com is geo-blocked (HTTP 451).

    Pages on the reused source. Does not construct a new exchange client per
    chunk or per request.
    """
    if source is not None:
        return _fetch_last_n(source, symbol, timeframe, limit)

    last_exc: Exception | None = None
    for exchange_id in ("binance", "binanceus"):
        try:
            src = _public_source(exchange_id)
            with _SOURCE_LOCK:
                return _fetch_last_n(src, symbol, timeframe, limit)
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
