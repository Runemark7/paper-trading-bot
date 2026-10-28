"""Causal daily indicators for the strategy library.

Every value at index ``t`` uses only bars ``0..t`` (the close of day ``t``
included). A value that is not defined yet is NaN; a signal built on it is
off. Calendar helpers read the bar's own UTC date, which is known at that
close, so they are not lookahead either.
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from hedge_fund.library.daily import DAY_MS


def _nan(n: int) -> np.ndarray:
    return np.full(int(n), np.nan, dtype=np.float64)


def sma(x: np.ndarray, n: int) -> np.ndarray:
    """Simple moving average of the last ``n`` values, ``t`` included."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    n = int(n)
    if n <= 0 or x.shape[0] < n:
        return out
    csum = np.concatenate(([0.0], np.cumsum(x)))
    out[n - 1 :] = (csum[n:] - csum[:-n]) / n
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    """EMA with ``alpha = 2 / (n + 1)``, seeded by the SMA of its first ``n`` values.

    Leading NaNs (for example a MACD line before its slow EMA exists) are
    skipped; the seed is the first ``n`` finite values after them.
    """
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    n = int(n)
    finite = np.flatnonzero(np.isfinite(x))
    if n <= 0 or finite.size == 0:
        return out
    first = int(finite[0])
    if x.shape[0] - first < n or not np.all(np.isfinite(x[first:])):
        return out
    alpha = 2.0 / (n + 1.0)
    seed = first + n - 1
    value = float(np.mean(x[first : seed + 1]))
    out[seed] = value
    for i in range(seed + 1, x.shape[0]):
        value = alpha * float(x[i]) + (1.0 - alpha) * value
        out[i] = value
    return out


def rsi_wilder(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder's RSI (the standard RSI). Defined from index ``n``."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    n = int(n)
    if n <= 0 or x.shape[0] <= n:
        return out
    diff = np.diff(x)
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)
    avg_gain = float(np.mean(gains[:n]))
    avg_loss = float(np.mean(losses[:n]))

    def value(g: float, lo: float) -> float:
        if lo == 0.0:
            return 50.0 if g == 0.0 else 100.0
        return 100.0 - 100.0 / (1.0 + g / lo)

    out[n] = value(avg_gain, avg_loss)
    for i in range(n + 1, x.shape[0]):
        avg_gain = (avg_gain * (n - 1) + float(gains[i - 1])) / n
        avg_loss = (avg_loss * (n - 1) + float(losses[i - 1])) / n
        out[i] = value(avg_gain, avg_loss)
    return out


def prior_max(x: np.ndarray, n: int) -> np.ndarray:
    """Highest value over the ``n`` days before ``t`` (``t`` excluded)."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    n = int(n)
    if n <= 0 or x.shape[0] <= n:
        return out
    out[n:] = sliding_window_view(x, n).max(axis=1)[: x.shape[0] - n]
    return out


def prior_min(x: np.ndarray, n: int) -> np.ndarray:
    """Lowest value over the ``n`` days before ``t`` (``t`` excluded)."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    n = int(n)
    if n <= 0 or x.shape[0] <= n:
        return out
    out[n:] = sliding_window_view(x, n).min(axis=1)[: x.shape[0] - n]
    return out


def trailing_return(x: np.ndarray, k: int) -> np.ndarray:
    """``x[t] / x[t - k] - 1``."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    k = int(k)
    if k <= 0 or x.shape[0] <= k:
        return out
    out[k:] = x[k:] / x[:-k] - 1.0
    return out


def month_number(ts: np.ndarray) -> np.ndarray:
    """Calendar month of each UTC day as an integer (year * 12 + month)."""
    t = np.asarray(ts, dtype=np.int64)
    return t.astype("datetime64[ms]").astype("datetime64[M]").astype(np.int64)


def is_month_end(ts: np.ndarray) -> np.ndarray:
    """True on the last calendar day of a month."""
    t = np.asarray(ts, dtype=np.int64)
    return month_number(t) != month_number(t + DAY_MS)


def is_week_end(ts: np.ndarray) -> np.ndarray:
    """True on Sundays (UTC). 1970-01-01 was a Thursday."""
    t = np.asarray(ts, dtype=np.int64)
    return ((t // DAY_MS) + 3) % 7 == 6


def week_number(ts: np.ndarray) -> np.ndarray:
    """Monday-based week index of each UTC day."""
    t = np.asarray(ts, dtype=np.int64)
    return ((t // DAY_MS) + 3) // 7


def hold_between(eval_mask: np.ndarray, value: np.ndarray) -> np.ndarray:
    """Desired position that changes only on ``eval_mask`` days.

    On an evaluation day the position is ``value`` (False when NaN); on other
    days it carries the last evaluation forward. Before the first evaluation
    day it is False.
    """
    mask = np.asarray(eval_mask, dtype=bool)
    vals = np.asarray(value, dtype=np.float64)
    out = np.zeros(mask.shape[0], dtype=bool)
    state = False
    for i in range(mask.shape[0]):
        if mask[i]:
            v = vals[i]
            state = bool(np.isfinite(v) and v > 0.0)
        out[i] = state
    return out
