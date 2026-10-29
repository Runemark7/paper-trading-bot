"""BTC tape for the library, from the Binance listing.

The hashed 5m tape stays cut at ``QUAL_CANONICAL_START_MS`` (2021-01-26).
Library discovery and the live ``lib.*`` cycle both read this longer
series instead, so a 200-day or 12-month rule is finite before it is
scored. ETH is not loaded: the library is BTC only.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from hedge_fund.library.daily import DailyBars, daily_from_tape, empty_daily
from hedge_fund.trading.constants import LIBRARY_TAPE_START_MS, TRADE_SYMBOLS

SYMBOL = TRADE_SYMBOLS[0]


def load_library_tape(state: Path | None = None) -> np.ndarray:
    """BTC/USDT 5m bars from ``LIBRARY_TAPE_START_MS``. Empty if none are stored."""
    from hedge_fund.trading.live_tape import _db_selected, load_array

    if _db_selected():
        from hedge_fund.data.candle_store import load_tape

        return load_tape(SYMBOL, since_ms=LIBRARY_TAPE_START_MS)
    arr = load_array(SYMBOL, state)
    if len(arr) == 0:
        return arr
    lo = int(np.searchsorted(arr["ts"], np.int64(LIBRARY_TAPE_START_MS), side="left"))
    return arr[lo:]


def load_library_daily(state: Path | None = None) -> DailyBars:
    """Completed UTC days of :func:`load_library_tape`. Empty when the tape is empty."""
    arr = load_library_tape(state)
    if len(arr) == 0:
        return empty_daily()
    return daily_from_tape(arr)
