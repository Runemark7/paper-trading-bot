"""Incremental candle update: last stored open through the last closed bar.

    python -m hedge_fund.data.candle_update

The prod CronJob runs this every 5 minutes. An empty store is not a
backfill; that command is ``python -m hedge_fund.data.candle_backfill``.
Only closed candles are written. The connection string is
``PAPERBOT_DATABASE_URL`` and is never printed.
"""
from __future__ import annotations

import argparse

from hedge_fund.data.candle_store import configured
from hedge_fund.data.candle_sync import DEFAULT_PAUSE, UPDATE_MAX_PAGES, format_report, update_incremental


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append closed Binance 5m candles to Postgres")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=UPDATE_MAX_PAGES,
        help="Pages per symbol this run (default 10). The next run resumes.",
    )
    args = parser.parse_args(argv)
    if not configured():
        print("PAPERBOT_DATABASE_URL is not set", flush=True)
        return 2
    try:
        report = update_incremental(
            pause_s=max(0.0, float(args.pause)),
            max_pages=max(1, int(args.max_pages)),
        )
    except Exception as exc:
        print(f"[candles] update failed: {exc}", flush=True)
        return 1
    print(format_report(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
