"""Read-only public OHLCV for the paper chart.

Public Binance/ccxt klines only — no trading keys, no broker. The chart
refetches this payload; it does not invent ticks between bars.

Production 502s on ``limit=500`` were too fast for a Binance timeout
(worker death / nginx cutting the response). Root cause: constructing
``ccxt.binance()`` per request OOMs the 512Mi web worker — not the candle
rows. We reuse one ``CcxtSource`` per venue, page public klines in chunks
of ``CHUNK_SIZE`` (never a one-shot ``limit=500+``), and map ccxt/timeout
to JSON 503/504 (never a bare nginx 502).

``_SOURCE_LOCK`` only guards the source cache (construct once). Paging
serializes per venue so ccxt stays single-threaded on that client, but
``/api/champions`` and a cache-hit ``/api/live`` do not wait on that lock.
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
# Whole /api/candles page-in. Last in-flight page may add FETCH_TIMEOUT.
# frontend nginx proxy_read_timeout must stay above this + FETCH_TIMEOUT.
FETCH_BUDGET = 45.0
SIGNAL_LIMIT = 200  # lot-health lookback; never the 3–7 day chart window

_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}
_SOURCES: dict[str, Any] = {}
_SOURCE_LOCK = threading.Lock()
_VENUE_LOCKS: dict[str, threading.Lock] = {}


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
    if "timeout" in name or "timed out" in msg or "timeout" in msg or "busy" in msg:
        return 504
    return 503


def _venue_lock(exchange_id: str) -> threading.Lock:
    with _SOURCE_LOCK:
        lock = _VENUE_LOCKS.get(exchange_id)
        if lock is None:
            lock = threading.Lock()
            _VENUE_LOCKS[exchange_id] = lock
        return lock


def _public_source(exchange_id: str):
    """Reuse one public ccxt client per venue. Constructing ccxt.binance()
    per request is what blows the 512Mi web worker — not the candle rows.

    Only the source dict is locked here. Callers must serialize fetch_klines
    on the per-venue lock — ccxt clients are not thread-safe.
    """
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


def _acquire_venue(exchange_id: str, *, blocking: bool) -> bool:
    lock = _venue_lock(exchange_id)
    if blocking:
        return lock.acquire()
    return lock.acquire(blocking=False)


def _release_venue(exchange_id: str) -> None:
    try:
        _venue_lock(exchange_id).release()
    except RuntimeError:
        pass


def _call_klines(src, symbol: str, timeframe: str, limit: int, since: int | None = None):
    """One public page. Never ask for more than CHUNK_SIZE. No new ccxt client."""
    n = min(max(1, int(limit)), CHUNK_SIZE)
    if since is None:
        return list(src.fetch_klines(symbol, timeframe=timeframe, limit=n))
    return list(src.fetch_klines(symbol, timeframe=timeframe, limit=n, since=since))


def _fetch_last_n(src, symbol: str, timeframe: str, n: int, *, deadline: float | None = None):
    """Newest ``n`` 5m bars, paged in CHUNK_SIZE pieces on this source only.

    Does not take ``_SOURCE_LOCK``. The caller holds the per-venue lock if
    this is a shared ccxt client. ``deadline`` is monotonic time: stop paging
    and return what we have rather than overrun nginx.
    """
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
        if deadline is not None and time.monotonic() >= deadline:
            break
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


def _public_klines(
    symbol: str,
    timeframe: str,
    limit: int,
    source=None,
    *,
    blocking: bool = True,
    deadline: float | None = None,
):
    """Public Binance OHLCV. Fall back to binanceus when .com is geo-blocked (HTTP 451).

    Pages on the reused source. Does not construct a new exchange client per
    chunk or per request. Does not hold ``_SOURCE_LOCK`` across pages.

    ``blocking=False``: skip a venue whose fetch lock is held (chart paging).
    Used by ``/api/live`` so lot-health never waits on a 3–7 day page-in.
    """
    if source is not None:
        return _fetch_last_n(source, symbol, timeframe, limit, deadline=deadline)

    last_exc: Exception | None = None
    busy = False
    for exchange_id in ("binance", "binanceus"):
        try:
            src = _public_source(exchange_id)
            if not _acquire_venue(exchange_id, blocking=blocking):
                busy = True
                last_exc = TimeoutError(f"{exchange_id} venue busy")
                continue
            try:
                return _fetch_last_n(
                    src, symbol, timeframe, limit, deadline=deadline
                )
            finally:
                _release_venue(exchange_id)
        except Exception as exc:  # noqa: BLE001 — try the next public venue
            last_exc = exc
            continue
    if busy and not blocking:
        raise CandleFetchError(
            str(last_exc or "venue busy"),
            status=504,
        ) from last_exc
    status = _status_for(last_exc)
    raise CandleFetchError(
        str(last_exc or "no public klines"),
        status=status,
    ) from last_exc


def peek_cached_candles(symbol: str, timeframe: str, limit: int) -> list[dict] | None:
    """Last ``limit`` bars from any fresh cache entry for this symbol/tf.

    Chart history (864–2016) and lot-health (200) share this peek so a live
    request does not start a second kline fetch while the tape is warm.
    """
    now = time.time()
    best: list[dict] | None = None
    for (sym, tf, _n), hit in _CACHE.items():
        if sym != symbol or tf != timeframe:
            continue
        if now - hit.get("_ts", 0) > CACHE_TTL:
            continue
        rows = hit.get("candles") or []
        if len(rows) < 2:
            continue
        if best is None or len(rows) > len(best):
            best = rows
    if best is None:
        return None
    return best[-min(limit, len(best)) :]


def try_recent_candles(symbol: str, timeframe: str, limit: int) -> list[dict] | None:
    """≤200 bars for lot health. Cache hit or one non-blocking page. Never pages 3–7 days.

    Returns None if the venue lock is busy or the fetch fails — caller sets
    ``signal: unknown`` rather than waiting on chart paging.
    """
    n = min(max(1, int(limit)), SIGNAL_LIMIT, CHUNK_SIZE)
    cached = peek_cached_candles(symbol, timeframe, n)
    if cached is not None:
        return cached
    try:
        bars = _public_klines(symbol, timeframe, n, blocking=False)
    except (CandleFetchError, Exception):  # noqa: BLE001
        return None
    return [
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


def try_live_prices(symbols: list[str]) -> dict[str, float]:
    """Spot last from the reused public client. Skip if the venue is paging.

    Never constructs a new ``ccxt.binance()``. Empty dict = caller keeps
    stale cache or marks lots at entry.
    """
    last_exc: Exception | None = None
    for exchange_id in ("binance", "binanceus"):
        try:
            src = _public_source(exchange_id)
            if not _acquire_venue(exchange_id, blocking=False):
                last_exc = TimeoutError(f"{exchange_id} venue busy")
                continue
            try:
                got: dict[str, float] = {}
                for sym in symbols:
                    fetch_price = getattr(src, "fetch_price", None)
                    if fetch_price is None:
                        raise RuntimeError("source has no fetch_price")
                    got[sym] = fetch_price(sym)
                if got:
                    return got
            finally:
                _release_venue(exchange_id)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    _ = last_exc
    return {}


def _bars_to_payload(symbol: str, timeframe: str, bars) -> dict:
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
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": candles,
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "source": "binance_public",
        "paper_only": True,
    }


def _fetch_cached(symbol: str, timeframe: str, limit: int, source=None) -> dict:
    now = time.time()
    key = (symbol, timeframe, limit)
    hit = _CACHE.get(key)
    if hit and (now - hit["_ts"] <= CACHE_TTL):
        return {k: v for k, v in hit.items() if k != "_ts"}

    deadline = time.monotonic() + FETCH_BUDGET
    try:
        bars = _public_klines(
            symbol, timeframe, limit, source=source, blocking=True, deadline=deadline
        )
    except CandleFetchError:
        raise
    except Exception as exc:  # noqa: BLE001 — map ccxt/network to JSON 503/504
        raise CandleFetchError(str(exc), status=_status_for(exc)) from exc
    payload = _bars_to_payload(symbol, timeframe, bars)
    _CACHE[key] = {**payload, "_ts": now}
    return payload
