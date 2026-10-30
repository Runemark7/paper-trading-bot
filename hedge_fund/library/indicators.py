"""Causal daily indicators for the strategy library.

Every value at index ``t`` uses only bars ``0..t`` (the close of day ``t``
included). A value that is not defined yet is NaN. A signal built on a NaN
is false, and that is not a tradable flat: scored days must be finite
(``signal_ready``). The scorer refuses a window that would treat the hole
as off. Calendar helpers read the bar's own UTC date, which is known at
that close, so they are not lookahead either.
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


def rolling_max(x: np.ndarray, n: int) -> np.ndarray:
    """Highest value of the last ``n`` observations, ``t`` included."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    n = int(n)
    if n <= 0 or x.shape[0] < n:
        return out
    out[n - 1 :] = sliding_window_view(x, n).max(axis=1)
    return out


def rolling_min(x: np.ndarray, n: int) -> np.ndarray:
    """Lowest value of the last ``n`` observations, ``t`` included."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    n = int(n)
    if n <= 0 or x.shape[0] < n:
        return out
    out[n - 1 :] = sliding_window_view(x, n).min(axis=1)
    return out


def rolling_std(x: np.ndarray, n: int) -> np.ndarray:
    """Population standard deviation of the last ``n`` observations, ``t`` included.

    Bollinger's bands divide by ``n``, not ``n - 1``. A window with no
    variation has standard deviation 0.
    """
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    n = int(n)
    if n <= 1 or x.shape[0] < n:
        return out
    c1 = np.concatenate(([0.0], np.cumsum(x)))
    c2 = np.concatenate(([0.0], np.cumsum(x * x)))
    total = c1[n:] - c1[:-n]
    total_sq = c2[n:] - c2[:-n]
    mean = total / n
    var = np.maximum(total_sq / n - mean * mean, 0.0)
    out[n - 1 :] = np.sqrt(var)
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Wilder true range. Index 0 is high-low; later bars include the prior close."""
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    n = int(close.shape[0])
    out = _nan(n)
    if n == 0:
        return out
    out[0] = high[0] - low[0]
    if n == 1:
        return out
    hl = high[1:] - low[1:]
    hc = np.abs(high[1:] - close[:-1])
    lc = np.abs(low[1:] - close[:-1])
    out[1:] = np.maximum(hl, np.maximum(hc, lc))
    return out


def wilder_smooth(raw: np.ndarray, n: int) -> np.ndarray:
    """Wilder moving average. Index 0 of ``raw`` is not part of the seed.

    The first value sits at index ``n`` and is the mean of ``raw[1:n+1]``
    (the first ``n`` observations that have a previous bar). Later values
    are ``(prev * (n - 1) + raw[i]) / n``.
    """
    raw = np.asarray(raw, dtype=np.float64)
    out = _nan(raw.shape[0])
    n = int(n)
    if n <= 0 or raw.shape[0] <= n:
        return out
    window = raw[1 : n + 1]
    if window.shape[0] != n or not bool(np.all(np.isfinite(window))):
        return out
    value = float(np.mean(window))
    out[n] = value
    for i in range(n + 1, raw.shape[0]):
        value = (value * (n - 1) + float(raw[i])) / n
        out[i] = value
    return out


def atr_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Wilder ATR. Defined from index ``n``."""
    return wilder_smooth(true_range(high, low, close), n)


def adx_first_index(n: int) -> int:
    """First index where ADX is defined (mean of the first ``n`` DX values)."""
    return 2 * int(n) - 1


def adx_wilder(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Wilder +DI, -DI, and ADX. A zero-range day is 0, not a missing value.

    +DM / -DM follow Wilder: the larger of the up-move and the down-move
    when that move is positive, otherwise both are 0 (including a tie).
    Directional indexes and DX are finite once the smoothed true range
    exists, including a day with no range. ADX starts at
    :func:`adx_first_index`.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    m = int(close.shape[0])
    plus_di = _nan(m)
    minus_di = _nan(m)
    adx = _nan(m)
    n = int(n)
    if n <= 0 or m <= n:
        return plus_di, minus_di, adx
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    plus_raw = np.zeros(m, dtype=np.float64)
    minus_raw = np.zeros(m, dtype=np.float64)
    plus_raw[1:] = np.where((up > down) & (up > 0.0), up, 0.0)
    minus_raw[1:] = np.where((down > up) & (down > 0.0), down, 0.0)
    tr_s = wilder_smooth(true_range(high, low, close), n)
    plus_s = wilder_smooth(plus_raw, n)
    minus_s = wilder_smooth(minus_raw, n)
    both = np.isfinite(tr_s) & np.isfinite(plus_s) & np.isfinite(minus_s)
    with np.errstate(invalid="ignore", divide="ignore"):
        plus_di = np.where(both & (tr_s > 0.0), 100.0 * plus_s / tr_s, np.where(both, 0.0, np.nan))
        minus_di = np.where(both & (tr_s > 0.0), 100.0 * minus_s / tr_s, np.where(both, 0.0, np.nan))
        denom = plus_di + minus_di
        dx = np.where(
            np.isfinite(denom) & (denom > 0.0),
            100.0 * np.abs(plus_di - minus_di) / denom,
            np.where(np.isfinite(denom), 0.0, np.nan),
        )
    first = adx_first_index(n)
    if m <= first:
        return plus_di, minus_di, adx
    chunk = dx[n : 2 * n]
    if chunk.shape[0] != n or not bool(np.all(np.isfinite(chunk))):
        return plus_di, minus_di, adx
    value = float(np.mean(chunk))
    adx[first] = value
    for i in range(first + 1, m):
        value = (value * (n - 1) + float(dx[i])) / n
        adx[i] = value
    return plus_di, minus_di, adx


def bollinger(close: np.ndarray, n: int, width: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Middle, lower, and upper Bollinger bands (population standard deviation)."""
    mid = sma(close, n)
    sd = rolling_std(close, n)
    w = float(width)
    return mid, mid - w * sd, mid + w * sd


def keltner(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, ema_n: int, atr_n: int, width: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Middle (EMA of typical price) and upper band (middle + ``width`` * ATR)."""
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    typical = (high + low + close) / 3.0
    mid = ema(typical, ema_n)
    upper = mid + float(width) * atr_wilder(high, low, close, atr_n)
    return mid, upper


def ichimoku_first_index(base: int, span: int) -> int:
    """First index of the cloud plotted at ``t`` from span B computed at ``t - base``."""
    return int(span) - 1 + int(base)


def ichimoku_cloud_top(high: np.ndarray, low: np.ndarray, conv: int, base: int, span: int) -> np.ndarray:
    """Top of the cloud plotted on day ``t``.

    Senkou span A and span B are computed through day ``t - base`` and
    displaced forward by ``base`` (the standard 26-period plot). The value
    at ``t`` does not use bars after ``t - base``.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    tenkan = (rolling_max(high, conv) + rolling_min(low, conv)) / 2.0
    kijun = (rolling_max(high, base) + rolling_min(low, base)) / 2.0
    span_a = (tenkan + kijun) / 2.0
    span_b = (rolling_max(high, span) + rolling_min(low, span)) / 2.0
    disp = int(base)
    cloud_a = _nan(high.shape[0])
    cloud_b = _nan(high.shape[0])
    if disp > 0 and high.shape[0] > disp:
        cloud_a[disp:] = span_a[:-disp]
        cloud_b[disp:] = span_b[:-disp]
    elif disp <= 0:
        cloud_a = span_a
        cloud_b = span_b
    # Both spans have to exist. np.fmax would treat a missing span as the
    # other one and turn the filter on before span B is warm.
    out = _nan(high.shape[0])
    ok = np.isfinite(cloud_a) & np.isfinite(cloud_b)
    out[ok] = np.maximum(cloud_a[ok], cloud_b[ok])
    return out


def supertrend_long(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int, mult: float,
) -> np.ndarray:
    """True while Supertrend direction is up.

    Basic bands are ``(high+low)/2 ± mult * ATR(n)``. The final bands
    ratchet: the upper band falls only, unless the previous close already
    finished above it, and the lower band rises only, unless the previous
    close finished below it. Direction flips to up on a close above the
    final upper band and to down on a close below the final lower band.
    The first ATR bar is up only when that close is already above the
    upper band. Every input at ``t`` is known at that close.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    m = int(close.shape[0])
    out = np.zeros(m, dtype=bool)
    atr = atr_wilder(high, low, close, n)
    hl2 = (high + low) / 2.0
    basic_u = hl2 + float(mult) * atr
    basic_l = hl2 - float(mult) * atr
    final_u = _nan(m)
    final_l = _nan(m)
    up = False
    started = False
    for t in range(m):
        if not np.isfinite(atr[t]):
            continue
        if not started:
            final_u[t] = basic_u[t]
            final_l[t] = basic_l[t]
            up = bool(close[t] > basic_u[t])
            started = True
        else:
            prev_u = final_u[t - 1]
            prev_l = final_l[t - 1]
            if basic_u[t] < prev_u or close[t - 1] > prev_u:
                final_u[t] = basic_u[t]
            else:
                final_u[t] = prev_u
            if basic_l[t] > prev_l or close[t - 1] < prev_l:
                final_l[t] = basic_l[t]
            else:
                final_l[t] = prev_l
            if up:
                up = not bool(close[t] < final_l[t])
            else:
                up = bool(close[t] > final_u[t])
        out[t] = up
    return out


def williams_r(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Williams %R on the last ``n`` bars, ``t`` included. Range is -100 to 0.

    A zero-range window is NaN (the position state machine does not treat
    that as an exit). -100 is a close at the window low.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    hh = rolling_max(high, n)
    ll = rolling_min(low, n)
    out = _nan(close.shape[0])
    span = hh - ll
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(span) & (span > 0.0) & np.isfinite(close)
        out[ok] = (hh[ok] - close[ok]) / span[ok] * -100.0
    return out


def ibs(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Internal bar strength ``(close - low) / (high - low)``. Zero range is NaN."""
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    span = high - low
    out = _nan(close.shape[0])
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(span) & (span > 0.0)
        out[ok] = (close[ok] - low[ok]) / span[ok]
    return out


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
