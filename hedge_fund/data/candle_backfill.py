"""One-time Binance 5m backfill into Postgres.

    python -m hedge_fund.data.candle_backfill
    python -m hedge_fund.data.candle_backfill --since 2021-01-26
    python -m hedge_fund.data.candle_backfill --library

Reads ``PAPERBOT_DATABASE_URL``. Does not print it. Re-run to resume:
each page is committed, and the next run continues from the stored
frontier and fills holes. See docs/CANDLE_STORE.md.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from hedge_fund.data.candle_quality import dt_to_ms
from hedge_fund.data.candle_store import configured
from hedge_fund.data.candle_sync import DEFAULT_PAUSE, backfill, format_report
from hedge_fund.trading.constants import LIBRARY_TAPE_START_MS, QUAL_CANONICAL_START_MS, TRADE_SYMBOLS


def _since_ms(text: str) -> int:
    raw = text.strip()
    if not raw:
        return QUAL_CANONICAL_START_MS
    try:
        moment = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"--since must be YYYY-MM-DD, got {raw!r}") from exc
    return dt_to_ms(moment)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Binance BTC/USDT and ETH/USDT 5m candles into Postgres"
    )
    parser.add_argument(
        "--since",
        default="",
        help="UTC date YYYY-MM-DD (default: canonical tape start 2021-01-26)",
    )
    parser.add_argument(
        "--library",
        action="store_true",
        help="BTC/USDT only, from the Binance listing 2017-08-17. Does not move the 5m canonical start.",
    )
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE)
    parser.add_argument("--max-pages", type=int, default=10_000)
    args = parser.parse_args(argv)
    if not configured():
        print("PAPERBOT_DATABASE_URL is not set", flush=True)
        return 2
    start_ms = _since_ms(args.since)
    symbols = None
    if args.library:
        if not args.since.strip():
            start_ms = LIBRARY_TAPE_START_MS
        symbols = (TRADE_SYMBOLS[0],)
    try:
        report = backfill(
            symbols=symbols,
            start_ms=start_ms,
            pause_s=max(0.0, float(args.pause)),
            max_pages=max(1, int(args.max_pages)),
        )
    except Exception as exc:
        print(f"[candles] backfill failed: {exc}", flush=True)
        return 1
    print(format_report(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
