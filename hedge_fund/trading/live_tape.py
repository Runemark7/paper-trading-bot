"""Persistent 5m tape for the live paper cycle.

Qualification walks every bar since ``QUAL_CANONICAL_START_MS`` (``qual_store_bars()``) of BTC/USDT and
ETH/USDT 5m history. The live cycle used to ask Binance for 300 bars
(~25h), so HTF atoms such as ``h1_ema_abv_60`` (60 completed hours) were
always false.

Bars live on the paper PVC at ``<PAPER_STATE>/live_tape/<symbol>.npy``
(one structured array per symbol — much smaller than rewriting
``crypto_history_5m.json`` every cycle). Each cycle appends only new
pages. A missing or short store is backfilled in ``CHUNK_SIZE`` pages
with the same timeout and binance → binanceus fallback as the chart
(``hedge_fund.web.candles.fetch_chunk``). If ``crypto_history_5m.json``
is already on the volume, it is imported once instead of re-downloaded.

When ``PAPERBOT_DATABASE_URL`` is set and the candle store covers the
canonical tape, the signal slice is read from Postgres instead. The
5-minute updater (and a short append at the start of this cycle) owns
new closed bars. The file tape remains the fallback while the URL is
unset or the store is not caught up.

Signals are computed on the trailing last qualification window
(``live_signal_eval_bars()`` = ``QUAL_WINDOW_BARS + QUAL_WARMUP_BARS``),
not on the whole multi-year file. That is the series the last
walk-forward window feeds the indicators, so the latest bar matches.
A shorter tail (warmup + lookback only) reseeds EMA and does not match.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from hedge_fund.data.binance import DEFAULT_TIMEFRAME, Candle
from hedge_fund.paths import state_root
from hedge_fund.signals.dynamic import eval_predicate, parse_strategy
from hedge_fund.signals.htf import BARS_PER_H4
from hedge_fund.trading.constants import (
    QUAL_SYMBOLS,
    QUAL_TIMEFRAME,
    QUAL_WARMUP_BARS,
    library_store_bars,
    live_signal_eval_bars,
    qual_keep_bars,
    qual_store_bars,
)
from hedge_fund.web.candles import CHUNK_SIZE, TF_MS, CandleFetchError, fetch_chunk

# Parser-allowed binding HTF: h4_ema_abv_70 needs 70 completed 4h closes
# plus up to 47 bars of an incomplete 4h bucket (see constants warmup note).
BINDING_H4_PERIOD = 70
# Shared wall clock for a cold backfill of both symbols. Partial progress
# is saved and the next cycle continues. Override with env.
DEFAULT_BACKFILL_BUDGET_SECONDS = 1800
_SAVE_EVERY_PAGES = 100
_JSON_IMPORT_MAX_BYTES = 250 * 1024 * 1024

TAPE_DTYPE = np.dtype(
    [
        ("ts", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("volume", "<f8"),
    ]
)

SYMBOLS = tuple(QUAL_SYMBOLS)


def longest_atom_lookback_bars() -> int:
    """5m bars the longest parser-allowed HTF atom needs before it can be true."""
    return BINDING_H4_PERIOD * BARS_PER_H4 + (BARS_PER_H4 - 1)


def minimum_store_bars() -> int:
    """Floor if a full qual tape is unavailable: lookback plus qual warm-up."""
    return longest_atom_lookback_bars() + QUAL_WARMUP_BARS


def tape_dir(state_dir: Path | None = None) -> Path:
    return Path(state_dir) if state_dir is not None else state_root()


def _symbol_slug(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":", "_")


def tape_path(symbol: str, state_dir: Path | None = None) -> Path:
    return tape_dir(state_dir) / "live_tape" / f"{_symbol_slug(symbol)}.npy"


def _empty() -> np.ndarray:
    return np.empty(0, dtype=TAPE_DTYPE)


def load_array(symbol: str, state_dir: Path | None = None) -> np.ndarray:
    path = tape_path(symbol, state_dir)
    if not path.exists():
        return _empty()
    arr = np.load(path)
    if arr.dtype != TAPE_DTYPE:
        arr = arr.astype(TAPE_DTYPE, copy=False)
    return arr


def load_tail(symbol: str, n: int, state_dir: Path | None = None) -> np.ndarray:
    """Copy the last ``n`` rows. Missing file → empty. Does not load a prefix."""
    path = tape_path(symbol, state_dir)
    if n <= 0 or not path.exists():
        return _empty()
    arr = np.load(path, mmap_mode="r")
    try:
        if int(arr.shape[0]) <= n:
            return np.array(arr, dtype=TAPE_DTYPE)
        return np.array(arr[-n:], dtype=TAPE_DTYPE)
    finally:
        del arr


def save_array(symbol: str, arr: np.ndarray, state_dir: Path | None = None) -> None:
    """Atomic replace so a crashed write cannot tear the tape."""
    path = tape_path(symbol, state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.stem}.tmp.npy"
    np.save(tmp, np.ascontiguousarray(arr, dtype=TAPE_DTYPE))
    tmp.replace(path)


def _dedup_last(arr: np.ndarray) -> np.ndarray:
    """Sort by ts. Equal timestamps keep the later row (forming-bar update)."""
    if arr.size == 0:
        return arr
    order = np.argsort(arr["ts"], kind="mergesort")
    arr = arr[order]
    if len(arr) == 1:
        return arr
    ts = arr["ts"]
    keep = np.empty(len(arr), dtype=bool)
    keep[:-1] = ts[:-1] != ts[1:]
    keep[-1] = True
    return arr[keep]


def merge_arrays(old: np.ndarray, new: np.ndarray) -> np.ndarray:
    """Append ``new`` onto ``old``. Overlapping ts keep the new row. Sorted."""
    if old.size == 0:
        return _dedup_last(new)
    if new.size == 0:
        return old
    return _dedup_last(np.concatenate([old, new]))


def rows_to_array(rows: list) -> np.ndarray:
    """Qual JSON rows ``[ts, o, h, l, c]`` or ``[..., volume]`` → tape array."""
    if not rows:
        return _empty()
    arr = np.empty(len(rows), dtype=TAPE_DTYPE)
    for i, row in enumerate(rows):
        ts = int(row[0])
        o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
        vol = float(row[5]) if len(row) > 5 and row[5] is not None else 0.0
        arr[i] = (ts, o, h, l, c, vol)
    return _dedup_last(arr)


def candles_to_array(candles: list) -> np.ndarray:
    if not candles:
        return _empty()
    arr = np.empty(len(candles), dtype=TAPE_DTYPE)
    for i, c in enumerate(candles):
        try:
            ts = c.ts
        except AttributeError as exc:
            raise TypeError("kline row is not a Candle") from exc
        arr[i] = (
            int(ts),
            float(c.open),
            float(c.high),
            float(c.low),
            float(c.close),
            float(getattr(c, "volume", 0.0) or 0.0),
        )
    return arr


def array_to_candles(arr: np.ndarray) -> list[Candle]:
    out: list[Candle] = []
    for row in arr:
        out.append(
            Candle(
                ts=int(row["ts"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
        )
    return out


def _iso_ms(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def _backfill_budget_seconds() -> float:
    raw = os.environ.get("LIVE_TAPE_BACKFILL_BUDGET_SECONDS", "")
    if not str(raw).strip():
        return float(DEFAULT_BACKFILL_BUDGET_SECONDS)
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return float(DEFAULT_BACKFILL_BUDGET_SECONDS)


def _is_real_ccxt(source) -> bool:
    ex = getattr(source, "exchange", None)
    return isinstance(getattr(ex, "id", None), str)


def _page(symbol: str, *, limit: int, since: int | None, source) -> list:
    """One CHUNK_SIZE page. Real ccxt failures fall through to the public venues."""
    try:
        if source is not None:
            return list(fetch_chunk(symbol, limit=limit, since=since, source=source))
    except TypeError:
        raise
    except Exception:
        if source is not None and not _is_real_ccxt(source):
            raise
    return list(fetch_chunk(symbol, limit=limit, since=since, source=None))


def _trim(arr: np.ndarray, target: int) -> np.ndarray:
    if target > 0 and len(arr) > target:
        return np.ascontiguousarray(arr[-target:])
    return arr


def _extend_forward(
    symbol: str,
    arr: np.ndarray,
    *,
    source,
    deadline: float,
    max_pages: int,
    target: int,
    state_dir: Path | None,
) -> tuple[np.ndarray, int]:
    if len(arr) == 0 or max_pages <= 0:
        return arr, 0
    cursor = int(arr[-1]["ts"])
    pages = 0
    while pages < max_pages and time.monotonic() < deadline:
        try:
            page = _page(symbol, limit=CHUNK_SIZE, since=cursor, source=source)
        except (CandleFetchError, TypeError, Exception) as exc:  # noqa: BLE001
            print(f"[live tape] {symbol} forward page failed: {exc}", flush=True)
            break
        if not page:
            break
        try:
            fetched = candles_to_array(page)
        except TypeError as exc:
            print(f"[live tape] {symbol} forward page failed: {exc}", flush=True)
            break
        arr = merge_arrays(arr, fetched)
        pages += 1
        if pages % _SAVE_EVERY_PAGES == 0:
            save_array(symbol, _trim(arr, target), state_dir)
        last = int(arr[-1]["ts"])
        # Short page means the venue has no further closed bars after `cursor`.
        # A full page that did not move past `cursor` still advances so the
        # next request cannot spin on the same window.
        if len(page) < CHUNK_SIZE or last + TF_MS <= cursor:
            break
        cursor = last + TF_MS
    return arr, pages


def _extend_backward(
    symbol: str,
    arr: np.ndarray,
    *,
    source,
    target: int,
    deadline: float,
    max_pages: int,
    state_dir: Path | None,
) -> tuple[np.ndarray, int]:
    pages = 0
    while len(arr) < target and pages < max_pages and time.monotonic() < deadline:
        if len(arr) == 0:
            since = None
        else:
            since = max(0, int(arr[0]["ts"]) - CHUNK_SIZE * TF_MS)
        try:
            page = _page(symbol, limit=CHUNK_SIZE, since=since, source=source)
        except TypeError as exc:
            print(f"[live tape] {symbol} backfill page failed: {exc}", flush=True)
            break
        except (CandleFetchError, Exception) as exc:  # noqa: BLE001
            print(f"[live tape] {symbol} backfill page failed: {exc}", flush=True)
            break
        if not page:
            break
        try:
            fetched = candles_to_array(page)
        except TypeError as exc:
            print(f"[live tape] {symbol} backfill page failed: {exc}", flush=True)
            break
        before_n = len(arr)
        before_first = int(arr[0]["ts"]) if before_n else None
        arr = merge_arrays(arr, fetched)
        pages += 1
        if before_n and len(arr) == before_n and int(arr[0]["ts"]) >= int(before_first):
            break
        if pages % _SAVE_EVERY_PAGES == 0:
            save_array(symbol, _trim(arr, target), state_dir)
            print(
                f"[live tape] {symbol}: {len(arr)} bars saved (page {pages})",
                flush=True,
            )
    return arr, pages


def _import_qual_json(state_dir: Path, target: int) -> None:
    """One-shot import of an on-disk qualification JSON. Does not re-fetch."""
    path = state_dir / f"crypto_history_{QUAL_TIMEFRAME}.json"
    if not path.exists():
        return
    missing = [s for s in SYMBOLS if not tape_path(s, state_dir).exists()]
    if not missing:
        return
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size > _JSON_IMPORT_MAX_BYTES:
        print(f"[live tape] skip {path.name} ({size} bytes) — backfill from Binance", flush=True)
        return
    print(f"[live tape] importing {path.name} for {', '.join(missing)}", flush=True)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        print(f"[live tape] json import failed: {exc}", flush=True)
        return
    if not isinstance(data, dict):
        return
    for sym in missing:
        rows = data.get(sym)
        if not rows:
            continue
        arr = rows_to_array(rows)
        arr = _trim(arr, target)
        if len(arr):
            save_array(sym, arr, state_dir)
            print(f"[live tape] imported {sym}: {len(arr)} bars", flush=True)


def ensure_symbol(
    symbol: str,
    *,
    source=None,
    state_dir: Path | None = None,
    target_bars: int | None = None,
    deadline: float | None = None,
    max_pages: int | None = None,
) -> np.ndarray:
    """Bring one symbol up to ``target_bars`` (default: qualification span).

    Newest bars first, then older history. Overlaps dedup. The forming bar
    (same ts) is replaced by the later fetch. Stops at ``deadline`` and
    leaves a resumable file.
    """
    if symbol not in SYMBOLS:
        raise ValueError(f"live tape is BTC/USDT and ETH/USDT only, got {symbol}")
    if target_bars is None:
        target = library_store_bars() if symbol == "BTC/USDT" else qual_store_bars()
    else:
        target = int(target_bars)
    if deadline is None:
        deadline = time.monotonic() + _backfill_budget_seconds()
    page_cap = (target // CHUNK_SIZE) + 20 if max_pages is None else int(max_pages)
    root = tape_dir(state_dir)
    arr = load_array(symbol, root)
    t0 = time.perf_counter()
    fwd_pages = 0
    back_pages = 0
    try:
        arr, fwd_pages = _extend_forward(
            symbol, arr, source=source, deadline=deadline, max_pages=page_cap,
            target=target, state_dir=root,
        )
        if len(arr) < target and time.monotonic() < deadline:
            arr, back_pages = _extend_backward(
                symbol, arr, source=source, target=target, deadline=deadline,
                max_pages=page_cap, state_dir=root,
            )
    finally:
        arr = _trim(arr, target)
        if len(arr):
            save_array(symbol, arr, root)
    elapsed = time.perf_counter() - t0
    if len(arr):
        print(
            f"[live tape] {symbol}: {len(arr)} bars "
            f"({_iso_ms(int(arr[0]['ts']))} -> {_iso_ms(int(arr[-1]['ts']))}) "
            f"pages={fwd_pages + back_pages} in {elapsed:.1f}s",
            flush=True,
        )
    else:
        print(f"[live tape] {symbol}: no bars ({elapsed:.1f}s)", flush=True)
    return arr


def _db_selected() -> bool:
    if not os.environ.get("PAPERBOT_DATABASE_URL", "").strip():
        return False
    try:
        from hedge_fund.data.candle_store import candle_store_selected

        return bool(candle_store_selected())
    except Exception as exc:
        print(f"[live tape] candle store unavailable, using files: {exc}", flush=True)
        return False


def _fetch_adapter(source):
    """ccxt page function, or None to use the store's own public client."""
    exchange = getattr(source, "exchange", None)
    fetch_ohlcv = getattr(exchange, "fetch_ohlcv", None) if exchange is not None else None
    if fetch_ohlcv is None:
        return None

    def _fetch(symbol, since_ms, limit):
        return fetch_ohlcv(
            symbol,
            timeframe=QUAL_TIMEFRAME,
            since=int(since_ms),
            limit=int(limit),
        )

    return _fetch


def ensure_live_tape(
    *,
    source=None,
    state_dir: Path | None = None,
    target_bars: int | None = None,
    deadline: float | None = None,
    max_pages: int | None = None,
) -> dict:
    """Import JSON if present, then incremental-append / backfill both symbols."""
    if _db_selected():
        from hedge_fund.data.candle_store import quality_summary

        quality = quality_summary()
        out: dict[str, dict] = {}
        for sym in SYMBOLS:
            row = (quality.get("symbols") or {}).get(sym) or {}
            out[sym] = {
                "bars": int(row.get("bars") or 0),
                "last_bar_time": row.get("last_bar_time"),
            }
        target = qual_store_bars() if target_bars is None else int(target_bars)
        return {
            "target_bars": target,
            "symbols": out,
            "paper_only": True,
            "store": "candles",
        }
    root = tape_dir(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    # Status reports the 5m qualification budget. BTC's file is deeper so the
    # 2017 library prefix is not trimmed off; ETH stays on the qual budget.
    target = qual_store_bars() if target_bars is None else int(target_bars)
    if deadline is None:
        deadline = time.monotonic() + _backfill_budget_seconds()
    try:
        _import_qual_json(root, library_store_bars() if target_bars is None else target)
    except Exception as exc:  # noqa: BLE001 — network backfill still runs
        print(f"[live tape] json import failed: {exc}", flush=True)
    out: dict[str, dict] = {}
    for sym in SYMBOLS:
        if target_bars is None:
            sym_target = library_store_bars() if sym == "BTC/USDT" else qual_store_bars()
        else:
            sym_target = target
        arr = ensure_symbol(
            sym, source=source, state_dir=root, target_bars=sym_target,
            deadline=deadline, max_pages=max_pages,
        )
        out[sym] = {
            "bars": int(len(arr)),
            "last_bar_time": _iso_ms(int(arr[-1]["ts"])) if len(arr) else None,
        }
    return {"target_bars": target, "symbols": out, "paper_only": True}


def signal_candles(
    symbol: str,
    *,
    state_dir: Path | None = None,
    n: int | None = None,
) -> list[Candle]:
    """Trailing slice used for live signals. Already the qual last window when full."""
    want = live_signal_eval_bars() if n is None else int(n)
    if _db_selected():
        from hedge_fund.data.candle_store import clear_ready, tail_candles

        try:
            bars = tail_candles(symbol, want)
        except Exception as exc:
            print(
                f"[live tape] candle read {symbol} failed, using file tape: {exc}",
                flush=True,
            )
        else:
            if bars:
                return bars
            print(f"[live tape] candle store has no {symbol} bars; using file tape", flush=True)
            clear_ready()
    return array_to_candles(load_tail(symbol, want, state_dir))


def strategy_signal_flags(candles: list[Candle], strategy: str) -> list[bool]:
    """Per-bar long/flat predicate on one series. Same ``eval_predicate`` as backtest."""
    if not candles:
        return []
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    pred = parse_strategy(strategy)
    return [
        bool(eval_predicate(pred, closes, i, highs=highs, lows=lows))
        for i in range(len(closes))
    ]


def load_cycle_klines(
    *,
    source=None,
    state_dir: Path | None = None,
    refresh: bool = True,
    target_bars: int | None = None,
    deadline: float | None = None,
    max_pages: int | None = None,
) -> dict[str, list[Candle] | None]:
    """Signal-slice klines for both symbols. ``refresh`` appends/backfills first."""
    root = tape_dir(state_dir)
    if _db_selected():
        if refresh:
            try:
                from hedge_fund.data.candle_sync import update_incremental

                update_incremental(fetch=_fetch_adapter(source), max_pages=3, pause_s=0.0)
            except Exception as exc:
                print(f"[live tape] candle update failed: {exc}", flush=True)
        try:
            out: dict[str, list[Candle] | None] = {}
            empty = True
            for sym in SYMBOLS:
                bars = signal_candles(sym, state_dir=root)
                if bars:
                    empty = False
                out[sym] = bars or None
            if not empty:
                return out
            print("[live tape] candle store empty; using file tape", flush=True)
        except Exception as exc:
            print(f"[live tape] candle read failed, using file tape: {exc}", flush=True)
    if refresh:
        try:
            ensure_live_tape(
                source=source, state_dir=root, target_bars=target_bars,
                deadline=deadline, max_pages=max_pages,
            )
        except Exception as exc:  # noqa: BLE001 — serve whatever is already on disk
            print(f"[live tape] refresh failed: {exc}", flush=True)
    out: dict[str, list[Candle] | None] = {}
    for sym in SYMBOLS:
        try:
            bars = signal_candles(sym, state_dir=root)
        except Exception as exc:  # noqa: BLE001
            print(f"[live tape] read {sym} failed: {exc}", flush=True)
            bars = []
        out[sym] = bars or None
    return out


class CachedMarket:
    """Duck-typed market over one pre-fetched snapshot.

    The kline cache is already the signal slice. ``fetch_klines`` returns
    that slice whole — a legacy ``limit=300`` must not starve HTF atoms.
    """

    def __init__(self, prices: dict, klines: dict) -> None:
        self._prices = prices
        self._klines = klines

    def fetch_price(self, symbol: str) -> float:
        px = self._prices.get(symbol)
        if px is None:
            raise RuntimeError(f"no cached price for {symbol}")
        return px

    def fetch_klines(
        self,
        symbol: str,
        timeframe: str = DEFAULT_TIMEFRAME,
        limit: int | None = None,
        since: int | None = None,
    ):
        del timeframe, limit, since  # snapshot is the eval slice; do not re-cut
        bars = self._klines.get(symbol)
        if not bars:
            raise RuntimeError(f"no cached klines for {symbol}")
        return list(bars)


def refresh_cycle_market(price_source, *, state_dir: Path, **tape_kw) -> CachedMarket:
    """Prices from ``price_source`` plus the persistent signal slice."""
    klines = load_cycle_klines(source=price_source, state_dir=state_dir, refresh=True, **tape_kw)
    prices: dict = {}
    clean: dict = {}
    for sym in SYMBOLS:
        bars = klines.get(sym)
        if bars:
            clean[sym] = bars
        try:
            prices[sym] = price_source.fetch_price(sym)
        except Exception as exc:  # noqa: BLE001
            print(f"[data error for {sym}]: {exc}", flush=True)
            if bars:
                prices[sym] = bars[-1].close
    return CachedMarket(prices, clean)


def _empty_symbol() -> dict:
    return {
        "bars": 0,
        "first_bar_time": None,
        "last_bar_time": None,
        "last_candle_time": None,
        "short": True,
        "gap_count": 0,
        "known_gap_count": 0,
        "duplicates": 0,
        "monotonic": True,
        "gaps": [],
    }


def _symbol_health(symbol: str, state_dir: Path) -> dict:
    path = tape_path(symbol, state_dir)
    if not path.exists():
        return _empty_symbol()
    arr = np.load(path, mmap_mode="r")
    try:
        n = int(arr.shape[0])
        if n <= 0:
            return _empty_symbol()
        first = int(arr[0]["ts"])
        last = int(arr[-1]["ts"])
        ts = np.array(arr["ts"], dtype=np.int64, copy=True)
    finally:
        del arr
    from hedge_fund.data.candle_quality import summarize_timestamp_array

    summary = summarize_timestamp_array(ts)
    last_iso = _iso_ms(last)
    return {
        "bars": n,
        "first_bar_time": _iso_ms(first),
        "last_bar_time": last_iso,
        "last_candle_time": last_iso,
        "short": n < live_signal_eval_bars(),
        "gap_count": summary["gap_count"],
        "known_gap_count": summary["known_gap_count"],
        "duplicates": summary["duplicates"],
        "monotonic": summary["monotonic"],
        "gaps": summary["gaps"],
    }


def _history_from_symbols(symbols: dict, *, store: str) -> dict:
    lasts = []
    for sym in SYMBOLS:
        row = symbols.get(sym) or _empty_symbol()
        symbols[sym] = row
        if row.get("last_candle_time"):
            lasts.append(row["last_candle_time"])
    return {
        "paper_only": True,
        "timeframe": QUAL_TIMEFRAME,
        "symbols": symbols,
        "target_bars": qual_store_bars(),
        "signal_eval_bars": live_signal_eval_bars(),
        "minimum_bars": minimum_store_bars(),
        "store": store,
        "gap_count": sum(int((symbols.get(sym) or {}).get("gap_count") or 0) for sym in SYMBOLS),
        "known_gap_count": sum(
            int((symbols.get(sym) or {}).get("known_gap_count") or 0) for sym in SYMBOLS
        ),
        "duplicates": sum(int((symbols.get(sym) or {}).get("duplicates") or 0) for sym in SYMBOLS),
        "monotonic": all(bool((symbols.get(sym) or {}).get("monotonic", True)) for sym in SYMBOLS),
        "last_candle_time": min(lasts) if lasts else None,
    }


def _db_history_payload() -> dict:
    from hedge_fund.data.candle_store import quality_summary

    quality = quality_summary()
    symbols = {}
    for sym in SYMBOLS:
        row = (quality.get("symbols") or {}).get(sym) or {}
        n = int(row.get("bars") or 0)
        symbols[sym] = {
            "bars": n,
            "first_bar_time": row.get("first_bar_time"),
            "last_bar_time": row.get("last_bar_time"),
            "last_candle_time": row.get("last_candle_time"),
            "short": n < live_signal_eval_bars(),
            "gap_count": int(row.get("gap_count") or 0),
            "known_gap_count": int(row.get("known_gap_count") or 0),
            "duplicates": int(row.get("duplicates") or 0),
            "monotonic": bool(row.get("monotonic", True)),
            "gaps": list(row.get("gaps") or []),
        }
    payload = _history_from_symbols(symbols, store="candles")
    payload["gap_count"] = int(quality.get("gap_count") or 0)
    payload["known_gap_count"] = int(quality.get("known_gap_count") or 0)
    payload["duplicates"] = int(quality.get("duplicates") or 0)
    payload["monotonic"] = bool(quality.get("monotonic"))
    payload["last_candle_time"] = quality.get("last_candle_time")
    return payload


def _candle_store_block(serving: str) -> dict:
    block = {
        "configured": False,
        "ready": False,
        "serving": serving,
        "gap_count": None,
        "known_gap_count": None,
        "last_candle_time": None,
        "monotonic": None,
        "duplicates": None,
        "error": None,
    }
    if not os.environ.get("PAPERBOT_DATABASE_URL", "").strip():
        return block
    block["configured"] = True
    try:
        from hedge_fund.data.candle_store import quality_summary, store_is_ready

        quality = quality_summary()
        block["ready"] = bool(store_is_ready())
        block["gap_count"] = quality["gap_count"]
        block["known_gap_count"] = quality["known_gap_count"]
        block["last_candle_time"] = quality["last_candle_time"]
        block["monotonic"] = quality["monotonic"]
        block["duplicates"] = quality["duplicates"]
    except Exception as exc:  # noqa: BLE001 — status must still render
        block["error"] = str(exc)
    return block


def live_history_health(state_dir: Path | None = None) -> dict:
    """Read-only tape summary for /api/status and /api/summary. No Binance."""
    root = tape_dir(state_dir)
    try:
        if _db_selected():
            payload = _db_history_payload()
        else:
            symbols = {sym: _symbol_health(sym, root) for sym in SYMBOLS}
            payload = _history_from_symbols(symbols, store="live_tape")
    except Exception as exc:  # noqa: BLE001 — status must still render
        payload = _history_from_symbols(
            {sym: _empty_symbol() for sym in SYMBOLS},
            store="live_tape",
        )
        payload["error"] = str(exc)
    payload["candle_store"] = _candle_store_block(payload.get("store") or "live_tape")
    return payload
