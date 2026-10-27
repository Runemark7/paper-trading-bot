"""Fetch Binance 5m candles into the store.

Backfill walks from the canonical tape start (or ``--since``) to the last
closed bar, resumes from rows already stored, and fills holes it can still
see. The incremental updater only walks forward from ``max(open_time)``
and never writes the forming candle.

Paging is 1000 bars (Binance's public kline cap) with a pause between
pages and a retry on rate-limit errors. Each page is committed, so a
killed run continues from the frontier.
"""
from __future__ import annotations

import threading
import time

from hedge_fund.data.candle_quality import TF_MS, last_closed_open_ms
from hedge_fund.data.candle_store import (
    configured,
    gap_edges,
    quality_summary,
    symbol_bounds,
    upsert_ohlcv,
)
from hedge_fund.trading.constants import QUAL_CANONICAL_START_MS, QUAL_SYMBOLS, QUAL_TIMEFRAME

PAGE_LIMIT = 1000
BACKFILL_MAX_PAGES = 10_000
UPDATE_MAX_PAGES = 10
DEFAULT_PAUSE = 0.25

_source = None
_source_lock = threading.Lock()


def binance_fetch(symbol: str, since_ms: int, limit: int) -> list:
    """One public Binance page. Empty is a real end, not an error."""
    global _source
    with _source_lock:
        if _source is None:
            from hedge_fund.data.binance import CcxtSource

            _source = CcxtSource("binance")
            exchange = getattr(_source, "exchange", None)
            if exchange is not None:
                try:
                    exchange.timeout = 20_000
                except Exception:
                    pass
        src = _source
    raw = src.exchange.fetch_ohlcv(
        symbol,
        timeframe=QUAL_TIMEFRAME,
        since=int(since_ms),
        limit=int(limit),
    )
    return list(raw or [])


def fetch_with_retry(
    fetch,
    symbol: str,
    since_ms: int,
    limit: int,
    *,
    attempts: int = 6,
    retry_pause: float | None = None,
) -> list:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return list(fetch(symbol, int(since_ms), int(limit)) or [])
        except Exception as exc:
            last = exc
            if i == attempts - 1:
                break
            if retry_pause is not None:
                delay = float(retry_pause)
            else:
                delay = min(30.0, 0.5 * (2 ** i))
            msg = str(exc).lower()
            if "429" in msg or "418" in msg or "rate" in msg or "too many" in msg:
                delay = max(delay, 5.0)
            print(
                f"[candles] {symbol} fetch failed ({exc}); retry in {delay:.1f}s",
                flush=True,
            )
            if delay:
                time.sleep(delay)
    raise RuntimeError(f"{symbol} fetch failed after retries: {last}") from last


def _closed(rows: list, last_closed_ms: int, earliest_ms: int) -> list:
    out = []
    for row in rows:
        ts = int(row[0])
        if earliest_ms <= ts <= last_closed_ms:
            out.append(row)
    return out


def page_forward(
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    fetch,
    pause_s: float,
    page_limit: int,
    max_pages: int,
    retry_pause: float | None = None,
) -> dict:
    """Upsert closed pages from ``start_ms`` through ``end_ms`` inclusive."""
    cursor = int(start_ms)
    end_ms = int(end_ms)
    pages = 0
    written = 0
    while cursor <= end_ms and pages < max_pages:
        raw = fetch_with_retry(
            fetch, symbol, cursor, page_limit, retry_pause=retry_pause
        )
        pages += 1
        if not raw:
            break
        raw.sort(key=lambda row: int(row[0]))
        closed = _closed(raw, end_ms, cursor)
        if closed:
            written += upsert_ohlcv(symbol, closed)
        last = int(raw[-1][0])
        nxt = last + TF_MS
        if pages % 10 == 0:
            print(
                f"[candles] {symbol}: page {pages} through {last} ({written} upserted)",
                flush=True,
            )
        if nxt <= cursor:
            break
        reached_end = last >= end_ms or len(raw) < page_limit
        cursor = nxt
        if reached_end:
            break
        if pause_s:
            time.sleep(pause_s)
    return {"pages": pages, "upserted": written, "next_open_ms": cursor}


def _require_url() -> None:
    if not configured():
        raise RuntimeError("PAPERBOT_DATABASE_URL is not set")


def update_incremental(
    *,
    fetch=None,
    symbols: tuple[str, ...] | None = None,
    now_ms: int | None = None,
    max_pages: int = UPDATE_MAX_PAGES,
    pause_s: float = DEFAULT_PAUSE,
    page_limit: int = PAGE_LIMIT,
    retry_pause: float | None = None,
) -> dict:
    """From ``max(open_time)`` to the last closed candle. No-op when empty.

    An empty symbol is left for the backfill command. A 5-minute cron must
    not start a multi-year download by itself.
    """
    _require_url()
    fetch = fetch or binance_fetch
    symbols = tuple(symbols or QUAL_SYMBOLS)
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    end_ms = last_closed_open_ms(now)
    report: dict = {
        "paper_only": True,
        "last_closed_open_ms": end_ms,
        "symbols": {},
    }
    for sym in symbols:
        bounds = symbol_bounds(sym)
        if bounds is None:
            report["symbols"][sym] = {"skipped": "empty", "upserted": 0, "pages": 0}
            print(
                f"[candles] {sym}: store empty; run python -m hedge_fund.data.candle_backfill",
                flush=True,
            )
            continue
        _min_ms, max_ms, _n = bounds
        start = max_ms + TF_MS
        if start > end_ms:
            report["symbols"][sym] = {
                "upserted": 0,
                "pages": 0,
                "up_to_date": True,
                "last_open_ms": max_ms,
            }
            continue
        stats = page_forward(
            sym,
            start,
            end_ms,
            fetch=fetch,
            pause_s=pause_s,
            page_limit=page_limit,
            max_pages=max_pages,
            retry_pause=retry_pause,
        )
        after = symbol_bounds(sym)
        stats["last_open_ms"] = after[1] if after else max_ms
        report["symbols"][sym] = stats
        print(
            f"[candles] {sym}: upserted {stats['upserted']} in {stats['pages']} page(s)",
            flush=True,
        )
    report["quality"] = quality_summary(force=True)
    return report


def backfill(
    *,
    fetch=None,
    symbols: tuple[str, ...] | None = None,
    start_ms: int | None = None,
    now_ms: int | None = None,
    max_pages: int = BACKFILL_MAX_PAGES,
    pause_s: float = DEFAULT_PAUSE,
    page_limit: int = PAGE_LIMIT,
    retry_pause: float | None = None,
) -> dict:
    """Fill ``[start, last closed]``, resume from stored rows, then fill holes.

    ``start`` defaults to ``QUAL_CANONICAL_START_MS`` (2021-01-26), the
    earliest open the shared tape hashes. ETH/USDT is stored too: gate v4
    reads it as a cross-asset check.
    """
    _require_url()
    fetch = fetch or binance_fetch
    symbols = tuple(symbols or QUAL_SYMBOLS)
    start_ms = QUAL_CANONICAL_START_MS if start_ms is None else int(start_ms)
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    end_ms = last_closed_open_ms(now)
    report: dict = {
        "paper_only": True,
        "start_ms": start_ms,
        "last_closed_open_ms": end_ms,
        "symbols": {},
    }
    for sym in symbols:
        remaining = int(max_pages)

        def run(lo: int, hi: int, *, _sym=sym) -> dict:
            nonlocal remaining
            if remaining <= 0 or lo > hi:
                return {"pages": 0, "upserted": 0, "next_open_ms": lo}
            stats = page_forward(
                _sym,
                lo,
                hi,
                fetch=fetch,
                pause_s=pause_s,
                page_limit=page_limit,
                max_pages=remaining,
                retry_pause=retry_pause,
            )
            remaining -= int(stats["pages"])
            return stats

        parts: list[dict] = []
        bounds = symbol_bounds(sym)
        if bounds is None:
            parts.append(run(start_ms, end_ms))
        else:
            min_ms, max_ms, _n = bounds
            if min_ms > start_ms:
                parts.append(run(start_ms, min_ms - TF_MS))
            bounds = symbol_bounds(sym)
            if bounds is not None and bounds[1] < end_ms:
                parts.append(run(bounds[1] + TF_MS, end_ms))
        filled = 0
        for prev, nxt in gap_edges(sym):
            if remaining <= 0:
                break
            stats = run(prev + TF_MS, nxt - TF_MS)
            filled += int(stats["upserted"])
            parts.append(stats)
        upserted = sum(int(part["upserted"]) for part in parts)
        pages = sum(int(part["pages"]) for part in parts)
        after = symbol_bounds(sym)
        report["symbols"][sym] = {
            "upserted": upserted,
            "pages": pages,
            "gap_fill_upserted": filled,
            "pages_remaining_budget": remaining,
            "bars": after[2] if after else 0,
            "first_open_ms": after[0] if after else None,
            "last_open_ms": after[1] if after else None,
        }
        print(
            f"[candles] {sym}: backfill upserted {upserted} in {pages} page(s)",
            flush=True,
        )
    report["quality"] = quality_summary(force=True)
    return report


def format_report(report: dict) -> str:
    quality = report.get("quality") or {}
    lines = ["[candles] paper_only"]
    symbols = quality.get("symbols") or {}
    for sym in QUAL_SYMBOLS:
        row = symbols.get(sym) or {}
        run = (report.get("symbols") or {}).get(sym) or {}
        lines.append(
            f"[candles] {sym}: bars={row.get('bars', 0)} "
            f"first={row.get('first_bar_time')} last={row.get('last_candle_time')} "
            f"upserted={run.get('upserted', 0)} "
            f"unexpected_gaps={row.get('gap_count', 0)} "
            f"known_gaps={row.get('known_gap_count', 0)} "
            f"duplicates={row.get('duplicates', 0)} "
            f"monotonic={row.get('monotonic')}"
        )
        for gap in row.get("gaps") or []:
            kind = "known" if gap.get("known") else "UNEXPECTED"
            lines.append(
                f"[candles] {sym} {kind} gap after {gap.get('after')} "
                f"before {gap.get('before')} missing_bars={gap.get('missing_bars')} "
                f"{gap.get('reason') or ''}".rstrip()
            )
    unexpected = quality.get("gap_count")
    if unexpected:
        lines.append(f"[candles] UNEXPECTED GAPS {unexpected}")
    else:
        lines.append("[candles] unexpected_gaps=0")
    return "\n".join(lines)
