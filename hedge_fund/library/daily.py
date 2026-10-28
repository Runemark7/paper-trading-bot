"""Completed UTC daily bars resampled from the stored 5m tape.

A day is the UTC calendar day ``[00:00, 24:00)``. Its open is the first 5m
open in that day, its close the last 5m close, and high / low are the 5m
extremes. A day counts only once the tape holds a bar at or after the next
00:00 UTC, so the day still forming never produces a signal. The worker
backtest, ``scripts/run_library.py`` and the live cycle all build daily bars
with :func:`daily_bars`, from the same 5m tape.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DAY_MS = 86_400_000


@dataclass(frozen=True)
class DailyBars:
    """Completed UTC days. ``ts`` is 00:00 UTC of each day, in ms."""

    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    def __len__(self) -> int:
        return int(self.ts.shape[0])

    @property
    def last_ts(self) -> int | None:
        return int(self.ts[-1]) if len(self) else None

    def index_of(self, ts_ms: int) -> int:
        """First day whose ``ts`` is at or after ``ts_ms``."""
        return int(np.searchsorted(self.ts, np.int64(ts_ms), side="left"))

    def before(self, ts_ms: int) -> "DailyBars":
        """Days that start strictly before ``ts_ms``."""
        return self._take(self.index_of(ts_ms))

    def _take(self, end: int) -> "DailyBars":
        end = max(0, min(len(self), int(end)))
        return DailyBars(
            ts=self.ts[:end],
            open=self.open[:end],
            high=self.high[:end],
            low=self.low[:end],
            close=self.close[:end],
        )


def empty_daily() -> DailyBars:
    z = np.empty(0, dtype=np.float64)
    return DailyBars(ts=np.empty(0, dtype=np.int64), open=z, high=z, low=z, close=z)


def daily_bars(ts, open_, high, low, close) -> DailyBars:
    """Resample ascending 5m bars into completed UTC days.

    Duplicate timestamps keep the last row. The day of the newest bar is
    always dropped: nothing after it proves that day has closed.
    """
    t = np.asarray(ts, dtype=np.int64)
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    lo = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = int(t.shape[0])
    if n == 0:
        return empty_daily()
    if n > 1 and not bool(np.all(t[1:] > t[:-1])):
        order = np.argsort(t, kind="mergesort")
        t, o, h, lo, c = t[order], o[order], h[order], lo[order], c[order]
        keep = np.ones(n, dtype=bool)
        keep[:-1] = t[:-1] != t[1:]
        t, o, h, lo, c = t[keep], o[keep], h[keep], lo[keep], c[keep]
        n = int(t.shape[0])
    day = t - (t % DAY_MS)
    starts = np.flatnonzero(np.concatenate(([True], day[1:] != day[:-1])))
    ends = np.concatenate((starts[1:], [n]))
    # The newest group has no later bar, so it is the forming day.
    starts, ends = starts[:-1], ends[:-1]
    if starts.size == 0:
        return empty_daily()
    stop = int(ends[-1])
    return DailyBars(
        ts=day[starts].copy(),
        open=o[starts].copy(),
        high=np.maximum.reduceat(h[:stop], starts),
        low=np.minimum.reduceat(lo[:stop], starts),
        close=c[ends - 1].copy(),
    )


def daily_from_rows(rows) -> DailyBars:
    """Rows ``[ts, open, high, low, close, ...]`` (the qualification loader shape)."""
    if rows is None or len(rows) == 0:
        return empty_daily()
    arr = np.asarray([r[:5] for r in rows], dtype=np.float64)
    return daily_bars(arr[:, 0].astype(np.int64), arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4])


def daily_from_tape(arr) -> DailyBars:
    """A structured tape array (``ts``, ``open``, ``high``, ``low``, ``close``)."""
    if arr is None or len(arr) == 0:
        return empty_daily()
    return daily_bars(arr["ts"], arr["open"], arr["high"], arr["low"], arr["close"])
