"""Causal daily indicators for the strategy library.

Every value at index ``t`` uses only bars ``0..t`` (the close of day ``t``
included). A value that is not defined yet is NaN. A signal built on a NaN
is false, and that is not a tradable flat: scored days must be finite
(``signal_ready``). The scorer refuses a window that would treat the hole
as off. Calendar helpers read the bar's own UTC date, which is known at
that close, so they are not lookahead either.
"""
from __future__ import annotations

import math

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
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Middle (EMA of typical price), lower band, and upper band.

    Bands sit ``width`` Wilder-ATR units off the middle line. A missing ATR
    or a missing EMA leaves that band NaN.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    typical = (high + low + close) / 3.0
    mid = ema(typical, ema_n)
    band = float(width) * atr_wilder(high, low, close, atr_n)
    return mid, mid - band, mid + band


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


def _finite_tail(x: np.ndarray) -> int | None:
    """Index of the first finite value, if the rest of the series stays finite."""
    x = np.asarray(x, dtype=np.float64)
    idx = np.flatnonzero(np.isfinite(x))
    if idx.size == 0:
        return None
    first = int(idx[0])
    if not bool(np.all(np.isfinite(x[first:]))):
        return None
    return first


def wma(x: np.ndarray, n: int) -> np.ndarray:
    """Linear weighted moving average. Weight ``n`` is on the newest value."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    n = int(n)
    if n <= 0 or x.shape[0] < n:
        return out
    weights = np.arange(1, n + 1, dtype=np.float64)
    out[n - 1 :] = sliding_window_view(x, n) @ weights / float(weights.sum())
    return out


def wma_skip_leading(x: np.ndarray, n: int) -> np.ndarray:
    """WMA that starts once ``n`` finite values exist after leading NaNs."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    first = _finite_tail(x)
    if first is None:
        return out
    out[first:] = wma(x[first:], n)
    return out


def rolling_sum_from(x: np.ndarray, n: int, start: int) -> np.ndarray:
    """Sum of ``x[t - n + 1 : t + 1]`` for ``t >= start``. Earlier bars are NaN."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    n = int(n)
    start = int(start)
    if n <= 0 or start < n - 1 or x.shape[0] <= start:
        return out
    csum = np.concatenate(([0.0], np.cumsum(x)))
    idx = np.arange(start, x.shape[0])
    out[idx] = csum[idx + 1] - csum[idx + 1 - n]
    return out


def rolling_median_skip_leading(x: np.ndarray, n: int) -> np.ndarray:
    """Median of the last ``n`` finite values. Leading NaNs are skipped."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    first = _finite_tail(x)
    n = int(n)
    if first is None or n <= 0 or x.shape[0] - first < n:
        return out
    out[first + n - 1 :] = np.median(sliding_window_view(x[first:], n), axis=1)
    return out


def rolling_max_skip_leading(x: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    first = _finite_tail(x)
    if first is None:
        return out
    out[first:] = rolling_max(x[first:], n)
    return out


def rolling_min_skip_leading(x: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    first = _finite_tail(x)
    if first is None:
        return out
    out[first:] = rolling_min(x[first:], n)
    return out


def shift_previous(x: np.ndarray, k: int) -> np.ndarray:
    """``x[t - k]``, NaN for the first ``k`` bars. ``k`` is not a look into the future."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    k = int(k)
    if k <= 0 or x.shape[0] <= k:
        return out
    out[k:] = x[:-k]
    return out


def weekday_utc(ts: np.ndarray) -> np.ndarray:
    """UTC weekday, Monday = 0. 1970-01-01 was a Thursday."""
    t = np.asarray(ts, dtype=np.int64)
    return ((t // DAY_MS) + 3) % 7


def month_of_year(ts: np.ndarray) -> np.ndarray:
    """Calendar month 1..12 of each UTC timestamp."""
    t = np.asarray(ts, dtype=np.int64)
    months = t.astype("datetime64[ms]").astype("datetime64[M]").astype(np.int64)
    return (months % 12) + 1


def day_of_month(ts: np.ndarray) -> np.ndarray:
    """Calendar day 1..31 of each UTC timestamp."""
    t = np.asarray(ts, dtype=np.int64)
    days = t.astype("datetime64[ms]").astype("datetime64[D]")
    months = days.astype("datetime64[M]")
    return (days - months).astype(np.int64) + 1


def days_in_month(ts: np.ndarray) -> np.ndarray:
    """Length of the UTC month that contains each timestamp."""
    t = np.asarray(ts, dtype=np.int64)
    months = t.astype("datetime64[ms]").astype("datetime64[M]")
    nxt = months + np.timedelta64(1, "M")
    last = nxt.astype("datetime64[D]") - np.timedelta64(1, "D")
    start = months.astype("datetime64[D]")
    return (last - start).astype(np.int64) + 1


def parabolic_sar(
    high: np.ndarray, low: np.ndarray, af_start: float, af_step: float, af_max: float,
) -> np.ndarray:
    """True while Wilder's parabolic SAR is in an uptrend.

    The first bar is not a signal (there is no prior extreme yet). From the
    second bar the stop ratchets toward the extreme price by ``af``, which
    starts at ``af_start``, steps by ``af_step`` on a new extreme, and caps
    at ``af_max``. In an uptrend the stop cannot sit above either of the two
    previous lows; in a downtrend it cannot sit below either of the two
    previous highs. A bar that breaks the stop flips, and that bar takes the
    new direction. Every input at ``t`` is known at that close.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    m = int(high.shape[0])
    out = np.zeros(m, dtype=bool)
    if m < 2:
        return out
    bull = True
    af = float(af_start)
    ep = float(high[0])
    sar = float(low[0])
    cap = float(af_max)
    step = float(af_step)
    for t in range(1, m):
        nxt = sar + af * (ep - sar)
        if bull:
            nxt = min(nxt, float(low[t - 1]))
            if t >= 2:
                nxt = min(nxt, float(low[t - 2]))
            if float(low[t]) < nxt:
                bull = False
                nxt = ep
                ep = float(low[t])
                af = float(af_start)
            elif float(high[t]) > ep:
                ep = float(high[t])
                af = min(af + step, cap)
        else:
            nxt = max(nxt, float(high[t - 1]))
            if t >= 2:
                nxt = max(nxt, float(high[t - 2]))
            if float(high[t]) > nxt:
                bull = True
                nxt = ep
                ep = float(high[t])
                af = float(af_start)
            elif float(low[t]) < ep:
                ep = float(low[t])
                af = min(af + step, cap)
        sar = nxt
        out[t] = bull
    return out


def kaufman_ama(close: np.ndarray, er_n: int, fast: int, slow: int) -> np.ndarray:
    """Kaufman adaptive moving average.

    The efficiency ratio is the n-day net change divided by the sum of the
    absolute one-day changes. The smoothing constant is the squared mix of
    the fast and slow EMA constants. The seed, at index ``er_n``, is the
    close. A zero-volatility window has efficiency 0 and uses the slow constant.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    er_n = int(er_n)
    fast = int(fast)
    slow = int(slow)
    if er_n <= 0 or fast <= 0 or slow <= 0 or close.shape[0] <= er_n:
        return out
    change = np.abs(np.diff(close))
    csum = np.concatenate(([0.0], np.cumsum(change)))
    volatility = csum[er_n:] - csum[:-er_n]
    direction = np.abs(close[er_n:] - close[:-er_n])
    efficiency = np.zeros(direction.shape[0], dtype=np.float64)
    np.divide(direction, volatility, out=efficiency, where=volatility > 0.0)
    fast_sc = 2.0 / (fast + 1.0)
    slow_sc = 2.0 / (slow + 1.0)
    smooth = (efficiency * (fast_sc - slow_sc) + slow_sc) ** 2
    value = float(close[er_n])
    out[er_n] = value
    for i in range(1, smooth.shape[0]):
        value = value + float(smooth[i]) * (float(close[er_n + i]) - value)
        out[er_n + i] = value
    return out


def aroon(high: np.ndarray, low: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Aroon Up and Aroon Down. Ties take the most recent extreme.

    The window is the last ``n`` bars plus today (``n + 1`` closes of the
    high/low). 100 means the extreme printed today. Defined from index ``n``.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    m = int(high.shape[0])
    up = _nan(m)
    down = _nan(m)
    n = int(n)
    if n <= 0 or m <= n:
        return up, down
    hw = sliding_window_view(high, n + 1)
    lw = sliding_window_view(low, n + 1)
    since_high = np.argmax(hw[:, ::-1], axis=1)
    since_low = np.argmin(lw[:, ::-1], axis=1)
    up[n:] = 100.0 * (n - since_high) / n
    down[n:] = 100.0 * (n - since_low) / n
    return up, down


def cci(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Lambert CCI. Typical price versus its mean, scaled by 0.015 * MAD.

    A window with no deviation is 0 when typical price equals its mean.
    Defined from index ``n - 1``.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 1 or close.shape[0] < n:
        return out
    typical = (high + low + close) / 3.0
    mid = sma(typical, n)
    window = sliding_window_view(typical, n)
    mad = np.mean(np.abs(window - mid[n - 1 :, None]), axis=1)
    num = typical[n - 1 :] - mid[n - 1 :]
    with np.errstate(invalid="ignore", divide="ignore"):
        scaled = num / (0.015 * mad)
    flat = np.isfinite(mid[n - 1 :]) & (mad == 0.0)
    ok = np.isfinite(scaled)
    out[n - 1 :] = np.where(ok, scaled, np.where(flat, 0.0, np.nan))
    return out


def stochastic_k(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Lane fast %K, 0 to 100, on the last ``n`` bars including today.

    A zero-range window is 50 (the close is the only price in the window),
    so a later %D still has a defined input. Defined from index ``n - 1``.
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
        flat = np.isfinite(span) & (span == 0.0)
        out[ok] = 100.0 * (close[ok] - ll[ok]) / span[ok]
        out[flat] = 50.0
    return out


def sma_skip_leading(x: np.ndarray, n: int) -> np.ndarray:
    """SMA that starts once ``n`` finite values exist after leading NaNs."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    first = _finite_tail(x)
    if first is None:
        return out
    out[first:] = sma(x[first:], n)
    return out


def close_streak(close: np.ndarray) -> np.ndarray:
    """Connors up/down streak. A higher close adds 1, a lower close subtracts 1, a tie is 0."""
    close = np.asarray(close, dtype=np.float64)
    out = np.zeros(close.shape[0], dtype=np.float64)
    for i in range(1, close.shape[0]):
        if close[i] > close[i - 1]:
            out[i] = out[i - 1] + 1.0 if out[i - 1] > 0.0 else 1.0
        elif close[i] < close[i - 1]:
            out[i] = out[i - 1] - 1.0 if out[i - 1] < 0.0 else -1.0
    return out


def percent_rank_change(close: np.ndarray, n: int) -> np.ndarray:
    """Percent of the last ``n`` one-day percent changes, today included, strictly below today's.

    This is the StockCharts / Connors form (count below, divided by ``n``).
    Today's change is one of the ``n``, so the value is at most ``100 * (n - 1) / n``.
    Defined from index ``n`` (the first ``n`` returns end there).
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 1 or close.shape[0] <= n:
        return out
    change = np.zeros(close.shape[0], dtype=np.float64)
    prev = close[:-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        step = np.where(prev != 0.0, close[1:] / prev - 1.0, 0.0)
    change[1:] = np.where(np.isfinite(step), step, 0.0)
    window = sliding_window_view(change[1:], n)
    last = window[:, -1]
    below = np.sum(window < last[:, None], axis=1)
    # ``last`` is inside the window, so it contributes one false comparison.
    out[n:] = 100.0 * below / n
    return out


def connors_rsi(close: np.ndarray, price_n: int = 3, streak_n: int = 2, rank_n: int = 100) -> np.ndarray:
    """Average of RSI(price), RSI(streak), and the percent rank of the one-day change."""
    close = np.asarray(close, dtype=np.float64)
    price = rsi_wilder(close, int(price_n))
    streak = rsi_wilder(close_streak(close), int(streak_n))
    rank = percent_rank_change(close, int(rank_n))
    out = _nan(close.shape[0])
    ok = np.isfinite(price) & np.isfinite(streak) & np.isfinite(rank)
    out[ok] = (price[ok] + streak[ok] + rank[ok]) / 3.0
    return out


def realized_vol(close: np.ndarray, n: int) -> np.ndarray:
    """Sample standard deviation of the last ``n`` one-day log returns.

    Not annualized: callers compare it with its own trailing median.
    Defined from index ``n``.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 1 or close.shape[0] <= n:
        return out
    prev = close[:-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(prev > 0.0, close[1:] / prev, np.nan)
        log_ret = np.log(ratio)
    if not bool(np.all(np.isfinite(log_ret))):
        return out
    window = sliding_window_view(log_ret, n)
    sd = window.std(axis=1, ddof=1)
    # A window whose log returns differ only in float noise is flat. Real
    # daily bitcoin moves are far above this, so it does not change a tape.
    span = window.max(axis=1) - window.min(axis=1)
    out[n:] = np.where(span <= 1e-12, 0.0, sd)
    return out


def hull_ma(close: np.ndarray, n: int) -> np.ndarray:
    """Hull moving average: WMA of ``2 * WMA(n/2) - WMA(n)`` over ``round(sqrt(n))`` bars."""
    close = np.asarray(close, dtype=np.float64)
    n = int(n)
    half = max(n // 2, 1)
    root = max(int(round(n ** 0.5)), 1)
    raw = 2.0 * wma(close, half) - wma(close, n)
    return wma_skip_leading(raw, root)


def vortex(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Vortex VI+ and VI-. Sums start at the first bar that has a previous bar.

    VI+ is the sum of ``|high - prior low|`` over ``n`` bars divided by the
    sum of true range. VI- uses ``|low - prior high|``. Defined from index ``n``.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    m = int(close.shape[0])
    plus = _nan(m)
    minus = _nan(m)
    n = int(n)
    if n <= 0 or m <= n:
        return plus, minus
    vm_plus = np.zeros(m, dtype=np.float64)
    vm_minus = np.zeros(m, dtype=np.float64)
    vm_plus[1:] = np.abs(high[1:] - low[:-1])
    vm_minus[1:] = np.abs(low[1:] - high[:-1])
    tr = true_range(high, low, close)
    tr = np.where(np.isfinite(tr), tr, 0.0)
    sp = rolling_sum_from(vm_plus, n, n)
    sm = rolling_sum_from(vm_minus, n, n)
    st = rolling_sum_from(tr, n, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(st) & (st > 0.0)
        plus[ok] = sp[ok] / st[ok]
        minus[ok] = sm[ok] / st[ok]
    return plus, minus


def ultimate_oscillator(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, short: int, mid: int, long: int,
) -> np.ndarray:
    """Williams Ultimate Oscillator. Buying pressure over true range, weighted 4/2/1.

    Bar 0 has no prior close and is excluded from every sum. Defined from
    index ``long`` when ``long`` is the longest period.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    m = int(close.shape[0])
    out = _nan(m)
    if m < 2:
        return out
    bp = np.zeros(m, dtype=np.float64)
    tr = np.zeros(m, dtype=np.float64)
    bp[1:] = close[1:] - np.minimum(low[1:], close[:-1])
    tr[1:] = np.maximum(high[1:], close[:-1]) - np.minimum(low[1:], close[:-1])

    def average(period: int) -> np.ndarray:
        bought = rolling_sum_from(bp, period, period)
        span = rolling_sum_from(tr, period, period)
        avg = _nan(m)
        with np.errstate(invalid="ignore", divide="ignore"):
            ok = np.isfinite(span) & (span > 0.0)
            avg[ok] = bought[ok] / span[ok]
        return avg

    a_short = average(int(short))
    a_mid = average(int(mid))
    a_long = average(int(long))
    ok = np.isfinite(a_short) & np.isfinite(a_mid) & np.isfinite(a_long)
    out[ok] = 100.0 * (4.0 * a_short[ok] + 2.0 * a_mid[ok] + a_long[ok]) / 7.0
    return out


def trix(close: np.ndarray, n: int) -> np.ndarray:
    """One-day rate of change of a triple EMA. Positive means the triple EMA is rising."""
    close = np.asarray(close, dtype=np.float64)
    third = ema(ema(ema(close, n), n), n)
    prev = shift_previous(third, 1)
    out = _nan(close.shape[0])
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(third) & np.isfinite(prev) & (prev != 0.0)
        out[ok] = (third[ok] - prev[ok]) / prev[ok]
    return out


def stoch_rsi(close: np.ndarray, n: int) -> np.ndarray:
    """Stochastic of Wilder RSI(n) over the last ``n`` RSI values.

    A zero RSI range uses the RSI level itself: a pinned 100 stays 100 and a
    pinned 0 stays 0. Defined once ``n`` RSI values exist. RSI itself starts
    at index ``n``, so this starts at index ``2n - 1``.
    """
    close = np.asarray(close, dtype=np.float64)
    value = rsi_wilder(close, n)
    hh = rolling_max_skip_leading(value, n)
    ll = rolling_min_skip_leading(value, n)
    out = _nan(close.shape[0])
    span = hh - ll
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(span) & (span > 0.0) & np.isfinite(value)
        flat = np.isfinite(span) & (span == 0.0) & np.isfinite(value)
        out[ok] = 100.0 * (value[ok] - ll[ok]) / span[ok]
        out[flat] = value[flat]
    return out


def sum_skip_leading(x: np.ndarray, n: int) -> np.ndarray:
    """Sum of the last ``n`` values once ``n`` finite values exist after leading NaNs."""
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    first = _finite_tail(x)
    n = int(n)
    if first is None or n <= 0 or x.shape[0] - first < n:
        return out
    tail = x[first:]
    csum = np.concatenate(([0.0], np.cumsum(tail)))
    idx = np.arange(n - 1, tail.shape[0])
    out[first + idx] = csum[idx + 1] - csum[idx + 1 - n]
    return out


def dema(close: np.ndarray, n: int) -> np.ndarray:
    """Mulloy double EMA: ``2 * EMA(n) - EMA(EMA(n))``.

    The inner EMA is finite at ``n - 1``. The outer EMA needs ``n`` finite
    values of that line, so the result starts at ``2 * (n - 1)``.
    """
    close = np.asarray(close, dtype=np.float64)
    first = ema(close, n)
    second = ema(first, n)
    out = _nan(close.shape[0])
    ok = np.isfinite(first) & np.isfinite(second)
    out[ok] = 2.0 * first[ok] - second[ok]
    return out


def chande_momentum(close: np.ndarray, n: int) -> np.ndarray:
    """Chande momentum oscillator. Raw up-sum minus down-sum, over their total.

    Unlike Wilder RSI, the sums are not smoothed. A window with no movement
    is 0. Defined from index ``n`` (``n`` one-day changes).
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 0 or close.shape[0] <= n:
        return out
    diff = np.diff(close)
    up = np.where(diff > 0.0, diff, 0.0)
    down = np.where(diff < 0.0, -diff, 0.0)
    c_up = np.concatenate(([0.0], np.cumsum(up)))
    c_down = np.concatenate(([0.0], np.cumsum(down)))
    idx = np.arange(n, close.shape[0])
    su = c_up[idx] - c_up[idx - n]
    sd = c_down[idx] - c_down[idx - n]
    denom = su + sd
    with np.errstate(invalid="ignore", divide="ignore"):
        out[idx] = np.where(denom > 0.0, 100.0 * (su - sd) / denom, 0.0)
    return out


def qstick(open_: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """SMA of close minus open. Positive means the bodies closed up on balance."""
    body = np.asarray(close, dtype=np.float64) - np.asarray(open_, dtype=np.float64)
    return sma(body, n)


def know_sure_thing(close: np.ndarray) -> np.ndarray:
    """Pring daily KST. Four smoothed rates of change, weights 1, 2, 3, 4.

    ROC(10), ROC(15) and ROC(20) are smoothed by an SMA of 10. ROC(30) is
    smoothed by an SMA of 15. The line is finite once that slowest average
    is, at index ``30 + 15 - 1``.
    """
    close = np.asarray(close, dtype=np.float64)
    roc10 = sma_skip_leading(trailing_return(close, 10), 10)
    roc15 = sma_skip_leading(trailing_return(close, 15), 10)
    roc20 = sma_skip_leading(trailing_return(close, 20), 10)
    roc30 = sma_skip_leading(trailing_return(close, 30), 15)
    out = _nan(close.shape[0])
    ok = np.isfinite(roc10) & np.isfinite(roc15) & np.isfinite(roc20) & np.isfinite(roc30)
    out[ok] = roc10[ok] + 2.0 * roc15[ok] + 3.0 * roc20[ok] + 4.0 * roc30[ok]
    return out


def fisher_transform(high: np.ndarray, low: np.ndarray, n: int) -> np.ndarray:
    """Ehlers Fisher transform of the smoothed n-day location of the midpoint.

    The midpoint's position in its own n-day range is smoothed with weights
    0.33 and 0.67, clamped to ±0.999, then passed through the Fisher map
    and smoothed the same way. The seed of both recursions is 0, which is
    "no history", not a later bar. The first value sits at index ``n - 1``.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    price = (high + low) / 2.0
    out = _nan(price.shape[0])
    n = int(n)
    if n <= 1 or price.shape[0] < n:
        return out
    highest = rolling_max(price, n)
    lowest = rolling_min(price, n)
    value = 0.0
    fish = 0.0
    for t in range(n - 1, price.shape[0]):
        span = float(highest[t] - lowest[t])
        if not np.isfinite(span) or span <= 0.0:
            raw = 0.0
        else:
            raw = 2.0 * ((float(price[t]) - float(lowest[t])) / span - 0.5)
        raw = float(np.clip(raw, -0.999, 0.999))
        value = 0.33 * raw + 0.67 * value
        value = float(np.clip(value, -0.999, 0.999))
        fish = 0.5 * float(np.log((1.0 + value) / (1.0 - value))) + 0.5 * fish
        out[t] = fish
    return out


def relative_vigor(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Ehlers relative vigor index and its 4-bar signal line.

    Close-open and high-low are each passed through the symmetric 1-2-2-1
    weights, summed over ``n`` bars, and divided. The signal line is the
    same weighting of the index. A zero range is 0. The index starts once
    ``n`` weighted bars exist (index ``n + 2``). The signal needs three
    earlier index values, so it starts at ``n + 5``.
    """
    open_ = np.asarray(open_, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    m = int(close.shape[0])
    rvi = _nan(m)
    signal = _nan(m)
    n = int(n)
    if n <= 0 or m < 4:
        return rvi, signal
    body = close - open_
    span = high - low
    num_bar = _nan(m)
    den_bar = _nan(m)
    num_bar[3:] = (body[3:] + 2.0 * body[2:-1] + 2.0 * body[1:-2] + body[:-3]) / 6.0
    den_bar[3:] = (span[3:] + 2.0 * span[2:-1] + 2.0 * span[1:-2] + span[:-3]) / 6.0
    num = sum_skip_leading(num_bar, n)
    den = sum_skip_leading(den_bar, n)
    ok = np.isfinite(num) & np.isfinite(den) & (den != 0.0)
    flat = np.isfinite(num) & np.isfinite(den) & (den == 0.0)
    rvi[ok] = num[ok] / den[ok]
    rvi[flat] = 0.0
    if m > 3:
        mixed = (rvi[3:] + 2.0 * rvi[2:-1] + 2.0 * rvi[1:-2] + rvi[:-3]) / 6.0
        signal[3:] = mixed
    return rvi, signal


def schaff_trend(close: np.ndarray, cycle: int, fast: int = 23, slow: int = 50) -> np.ndarray:
    """Schaff trend cycle: MACD line, then a stochastic, twice, each smoothed by 1/2.

    The 23/50 MACD lengths and the 0.5 smoother are the published defaults
    and are not arguments a caller grids here. A zero-range stochastic keeps
    the previous fraction; the first bar with no range and no history is 50.
    The cycle length is the only lookback. The result starts once both
    stochastic windows exist.
    """
    close = np.asarray(close, dtype=np.float64)
    fast_ma = ema(close, fast)
    slow_ma = ema(close, slow)
    macd = _nan(close.shape[0])
    ok = np.isfinite(fast_ma) & np.isfinite(slow_ma)
    macd[ok] = fast_ma[ok] - slow_ma[ok]

    def _smooth_stoch(series: np.ndarray, length: int) -> np.ndarray:
        highest = rolling_max_skip_leading(series, length)
        lowest = rolling_min_skip_leading(series, length)
        out = _nan(series.shape[0])
        frac_prev = None
        smooth = None
        for t in range(series.shape[0]):
            if not (np.isfinite(highest[t]) and np.isfinite(lowest[t]) and np.isfinite(series[t])):
                continue
            span = float(highest[t] - lowest[t])
            if span > 0.0:
                frac = 100.0 * (float(series[t]) - float(lowest[t])) / span
            elif frac_prev is not None:
                frac = frac_prev
            else:
                frac = 50.0
            frac_prev = frac
            smooth = frac if smooth is None else smooth + 0.5 * (frac - smooth)
            out[t] = smooth
        return out

    return _smooth_stoch(_smooth_stoch(macd, int(cycle)), int(cycle))


def detrended_price(close: np.ndarray, n: int) -> np.ndarray:
    """Close from ``n // 2 + 1`` bars ago, minus the SMA of ``n`` closes through today.

    The displaced close is older than the average. Nothing after ``t`` enters
    the value at ``t``. Defined from index ``n - 1``.
    """
    close = np.asarray(close, dtype=np.float64)
    n = int(n)
    lagged = shift_previous(close, n // 2 + 1)
    average = sma(close, n)
    out = _nan(close.shape[0])
    ok = np.isfinite(lagged) & np.isfinite(average)
    out[ok] = lagged[ok] - average[ok]
    return out


def ulcer_index(close: np.ndarray, n: int) -> np.ndarray:
    """Martin ulcer index: root-mean-square percentage drawdown from the window high.

    The high is the highest close in the last ``n`` closes, today included.
    A window whose peak is not positive is left undefined. Defined from
    index ``n - 1``.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 1 or close.shape[0] < n:
        return out
    windows = sliding_window_view(close, n)
    peak = windows.max(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        drawdown = np.where(
            peak[:, None] > 0.0,
            100.0 * (peak[:, None] - windows) / peak[:, None],
            np.nan,
        )
        value = np.sqrt(np.mean(drawdown * drawdown, axis=1))
    ok = np.isfinite(value)
    filled = out[n - 1 :]
    filled[ok] = value[ok]
    out[n - 1 :] = filled
    return out


def mass_index(high: np.ndarray, low: np.ndarray, ema_n: int, span: int) -> np.ndarray:
    """Dorsey mass index: sum of the ratio of two EMAs of the high-low range.

    Both EMAs use the standard ``2 / (n + 1)`` seed. A zero double-smoothed
    range (no range at all) has ratio 1, so the sum stays defined. The sum
    starts once ``span`` ratios exist.
    """
    span_px = np.asarray(high, dtype=np.float64) - np.asarray(low, dtype=np.float64)
    first = ema(span_px, ema_n)
    second = ema(first, ema_n)
    ratio = _nan(span_px.shape[0])
    ok = np.isfinite(first) & np.isfinite(second) & (second > 0.0)
    flat = np.isfinite(first) & np.isfinite(second) & (second == 0.0)
    ratio[ok] = first[ok] / second[ok]
    ratio[flat] = 1.0
    return sum_skip_leading(ratio, span)


def relative_volatility(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Dorsey relative volatility index, 0 to 100.

    An up close contributes the population standard deviation of the highs;
    a down close contributes the standard deviation of the lows. Those two
    series are Wilder-smoothed over ``n`` and combined like RSI. Unchanged
    closes contribute nothing. A day with no volatility on either side is 50.
    The standard deviation exists at ``n - 1``; the smoother needs ``n`` of
    those values, so the index starts at ``2 * n - 2``.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    m = int(close.shape[0])
    out = _nan(m)
    n = int(n)
    if n <= 1 or m <= 2 * n - 2:
        return out
    sd_high = rolling_std(high, n)
    sd_low = rolling_std(low, n)
    up = np.zeros(m, dtype=np.float64)
    down = np.zeros(m, dtype=np.float64)
    start = n - 1
    higher = close[start:] > close[start - 1 : -1]
    lower = close[start:] < close[start - 1 : -1]
    up[start:] = np.where(higher, sd_high[start:], 0.0)
    down[start:] = np.where(lower, sd_low[start:], 0.0)
    up_avg = float(np.mean(up[start : start + n]))
    down_avg = float(np.mean(down[start : start + n]))
    first = start + n - 1

    def _wilder(seed: float, raw: np.ndarray) -> np.ndarray:
        series = _nan(m)
        value = seed
        series[first] = value
        for i in range(first + 1, m):
            value = (value * (n - 1) + float(raw[i])) / n
            series[i] = value
        return series

    up_s = _wilder(up_avg, up)
    down_s = _wilder(down_avg, down)
    denom = up_s + down_s
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(denom) & (denom > 0.0)
        flat = np.isfinite(denom) & (denom == 0.0)
        out[ok] = 100.0 * up_s[ok] / denom[ok]
        out[flat] = 50.0
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


def smma(x: np.ndarray, n: int) -> np.ndarray:
    """Smoothed moving average. Seeded by the SMA, then ``(prev * (n - 1) + x) / n``.

    Finite at index ``n - 1``. This is the average Williams uses for the
    alligator, not an EMA.
    """
    x = np.asarray(x, dtype=np.float64)
    out = _nan(x.shape[0])
    n = int(n)
    if n <= 0 or x.shape[0] < n or not bool(np.all(np.isfinite(x))):
        return out
    value = float(np.mean(x[:n]))
    out[n - 1] = value
    for i in range(n, x.shape[0]):
        value = (value * (n - 1) + float(x[i])) / n
        out[i] = value
    return out


def macd_histogram(close: np.ndarray, fast: int, slow: int, signal: int) -> np.ndarray:
    """MACD line minus its signal EMA. NaN until the signal average exists."""
    close = np.asarray(close, dtype=np.float64)
    line = ema(close, int(fast)) - ema(close, int(slow))
    sig = ema(line, int(signal))
    out = _nan(close.shape[0])
    ok = np.isfinite(line) & np.isfinite(sig)
    out[ok] = line[ok] - sig[ok]
    return out


def true_strength_index(close: np.ndarray, long: int, short: int) -> np.ndarray:
    """Blau TSI: 100 times double-smoothed momentum over double-smoothed absolute momentum.

    The first smoothing is ``long`` (25 in the article) and the second is
    ``short`` (13). Momentum is the one-day change. A flat window is 0.
    Finite at index ``long + short - 1``.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    long, short = int(long), int(short)
    if long <= 0 or short <= 0 or close.shape[0] <= long + short:
        return out
    mom = _nan(close.shape[0])
    mom[1:] = np.diff(close)
    num = ema(ema(mom, long), short)
    den = ema(ema(np.abs(mom), long), short)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(num) & np.isfinite(den) & (den > 0.0)
        flat = np.isfinite(num) & np.isfinite(den) & (den == 0.0)
        out[ok] = 100.0 * num[ok] / den[ok]
        out[flat] = 0.0
    return out


def awesome_oscillator(high: np.ndarray, low: np.ndarray, fast: int, slow: int) -> np.ndarray:
    """Williams: SMA of the median price, fast minus slow. Median is (high + low) / 2."""
    median = (np.asarray(high, dtype=np.float64) + np.asarray(low, dtype=np.float64)) / 2.0
    fast_ma = sma(median, int(fast))
    slow_ma = sma(median, int(slow))
    out = _nan(median.shape[0])
    ok = np.isfinite(fast_ma) & np.isfinite(slow_ma)
    out[ok] = fast_ma[ok] - slow_ma[ok]
    return out


def balance_of_power(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int,
) -> np.ndarray:
    """SMA of (close - open) / (high - low). A zero-range bar contributes 0."""
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    span = high - low
    raw = np.zeros(span.shape[0], dtype=np.float64)
    ok = span > 0.0
    raw[ok] = (
        np.asarray(close, dtype=np.float64)[ok] - np.asarray(open_, dtype=np.float64)[ok]
    ) / span[ok]
    return sma(raw, int(n))


def psychological_line(close: np.ndarray, n: int) -> np.ndarray:
    """Percent of the last ``n`` sessions that closed strictly above the prior close.

    The first ``n`` one-day comparisons end at index ``n``. A tie is not an up day.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 0 or close.shape[0] <= n:
        return out
    up = np.zeros(close.shape[0], dtype=np.float64)
    up[1:] = close[1:] > close[:-1]
    csum = np.concatenate(([0.0], np.cumsum(up)))
    idx = np.arange(n, close.shape[0])
    out[idx] = 100.0 * (csum[idx + 1] - csum[idx + 1 - n]) / n
    return out


def choppiness_index(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Dreiss choppiness index, 0 to 100. Low means the path was directional.

    ``100 * log10(sum of n true ranges / n-bar high-low range) / log10(n)``.
    A zero range is NaN, not a low reading. Finite at index ``n - 1``.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 1 or close.shape[0] < n:
        return out
    tr = true_range(high, low, close)
    csum = np.concatenate(([0.0], np.cumsum(tr)))
    sum_tr = csum[n:] - csum[:-n]
    span = rolling_max(high, n)[n - 1 :] - rolling_min(low, n)[n - 1 :]
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = sum_tr / span
        ci = 100.0 * np.log10(ratio) / np.log10(float(n))
    ok = np.isfinite(ci) & (span > 0.0) & (sum_tr > 0.0)
    out[n - 1 :][ok] = ci[ok]
    return out


def random_walk_index(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Poulos RWI of the highs and of the lows. Each is the max ratio over lengths 2..n.

    The length-``i`` ratio divides the ``i``-bar range by ``ATR(i) * sqrt(i)``.
    Both series are finite at index ``n`` once every shorter ATR exists.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    m = int(close.shape[0])
    up = _nan(m)
    down = _nan(m)
    n = int(n)
    if n < 2 or m <= n:
        return up, down
    tr = true_range(high, low, close)
    atrs = [wilder_smooth(tr, i) for i in range(n + 1)]
    root = np.sqrt(np.arange(n + 1, dtype=np.float64))
    for t in range(n, m):
        best_up = -np.inf
        best_down = -np.inf
        got = False
        for i in range(2, n + 1):
            atr = float(atrs[i][t])
            if not np.isfinite(atr) or atr <= 0.0:
                continue
            scale = atr * float(root[i])
            up_i = (float(high[t]) - float(low[t - i])) / scale
            down_i = (float(high[t - i]) - float(low[t])) / scale
            if up_i > best_up:
                best_up = up_i
            if down_i > best_down:
                best_down = down_i
            got = True
        if got:
            up[t] = best_up
            down[t] = best_down
    return up, down


def swing_index(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, limit_frac: float = 0.5,
) -> np.ndarray:
    """Wilder swing index. Index 0 is NaN. Later bars use only the previous bar.

    ``limit_frac`` times the previous close stands in for the futures limit
    move, which BTC does not have. The factor is positive, so it scales the
    index and does not change its sign. A bar with no range and no gap is 0.
    """
    open_ = np.asarray(open_, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    if close.shape[0] < 2:
        return out
    prev_c = close[:-1]
    prev_o = open_[:-1]
    cur_o = open_[1:]
    cur_h = high[1:]
    cur_l = low[1:]
    cur_c = close[1:]
    a = np.abs(cur_h - prev_c)
    b = np.abs(cur_l - prev_c)
    c = np.abs(cur_h - cur_l)
    d = np.abs(prev_c - prev_o)
    k = np.maximum(a, b)
    a_largest = (a >= b) & (a >= c)
    b_largest = (b > a) & (b >= c)
    r = np.where(
        a_largest,
        a + 0.5 * b + 0.25 * d,
        np.where(b_largest, b + 0.5 * a + 0.25 * d, c + 0.25 * d),
    )
    num = (cur_c - prev_c) + 0.5 * (cur_c - cur_o) + 0.25 * (prev_c - prev_o)
    limit = float(limit_frac) * prev_c
    si = np.zeros(cur_c.shape[0], dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = (r > 0.0) & (limit > 0.0) & np.isfinite(num) & np.isfinite(k)
        si[ok] = 50.0 * num[ok] / r[ok] * k[ok] / limit[ok]
    out[1:] = si
    return out


def range_expansion_index(high: np.ndarray, low: np.ndarray, n: int) -> np.ndarray:
    """DeMark range expansion index.

    Each bar adds (high - high two bars ago) + (low - low two bars ago), and
    the same absolute sum, but only when the bar overlaps the range of the
    bars five and six days back. Otherwise that bar adds zero. The index is
    100 times the n-bar sum of the signed terms over the sum of the absolute
    terms. A zero denominator is 0. Finite at index ``n + 5``.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    out = _nan(high.shape[0])
    n = int(n)
    if n <= 0 or high.shape[0] <= n + 5:
        return out
    h2 = shift_previous(high, 2)
    l2 = shift_previous(low, 2)
    h5 = shift_previous(high, 5)
    l5 = shift_previous(low, 5)
    h6 = shift_previous(high, 6)
    l6 = shift_previous(low, 6)
    defined = (
        np.isfinite(h2) & np.isfinite(l2) & np.isfinite(h5)
        & np.isfinite(l5) & np.isfinite(h6) & np.isfinite(l6)
    )
    cond = ((high >= l5) | (high >= l6)) & ((low <= h5) | (low <= h6))
    dh = high - h2
    dl = low - l2
    num = np.where(defined & cond, dh + dl, np.where(defined, 0.0, np.nan))
    den = np.where(defined & cond, np.abs(dh) + np.abs(dl), np.where(defined, 0.0, np.nan))
    s_num = sum_skip_leading(num, n)
    s_den = sum_skip_leading(den, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(s_num) & np.isfinite(s_den) & (s_den > 0.0)
        flat = np.isfinite(s_num) & np.isfinite(s_den) & (s_den == 0.0)
        out[ok] = 100.0 * s_num[ok] / s_den[ok]
        out[flat] = 0.0
    return out


def linreg_slope(close: np.ndarray, n: int) -> np.ndarray:
    """Least-squares slope of the last ``n`` closes, older bar at x = 0.

    Finite at index ``n - 1``. A positive slope means the fitted line rises
    toward the current bar.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 1 or close.shape[0] < n:
        return out
    x = np.arange(n, dtype=np.float64)
    sum_x = float(x.sum())
    sum_x2 = float(x @ x)
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0.0:
        return out
    window = sliding_window_view(close, n)
    sum_y = window.sum(axis=1)
    sum_xy = window @ x
    out[n - 1 :] = (n * sum_xy - sum_x * sum_y) / denom
    return out


def laguerre_rsi(close: np.ndarray, gamma: float) -> np.ndarray:
    """Ehlers Laguerre RSI, on 0..1. Seeded by the first close. Finite from index 1.

    ``gamma`` is the damping constant (he uses 0.5). Four Laguerre poles, then
    the share of the up-moves among the three adjacent differences.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    if close.shape[0] == 0 or not np.isfinite(close[0]):
        return out
    g = float(gamma)
    l0 = l1 = l2 = l3 = float(close[0])
    for i in range(1, close.shape[0]):
        price = float(close[i])
        prev0, prev1, prev2 = l0, l1, l2
        l0 = (1.0 - g) * price + g * l0
        l1 = -g * l0 + prev0 + g * l1
        l2 = -g * l1 + prev1 + g * l2
        l3 = -g * l2 + prev2 + g * l3
        cu = 0.0
        cd = 0.0
        if l0 >= l1:
            cu += l0 - l1
        else:
            cd += l1 - l0
        if l1 >= l2:
            cu += l1 - l2
        else:
            cd += l2 - l1
        if l2 >= l3:
            cu += l2 - l3
        else:
            cd += l3 - l2
        total = cu + cd
        out[i] = cu / total if total > 0.0 else 0.0
    return out


def vidya(close: np.ndarray, n: int) -> np.ndarray:
    """Chande VIDYA. The smoothing weight is the absolute CMO of the same length.

    Seeded with the close on the first CMO bar (index ``n``).
    ``alpha = 2 / (n + 1)``, times ``|CMO| / 100``.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 0 or close.shape[0] <= n:
        return out
    cmo = chande_momentum(close, n)
    if not np.isfinite(cmo[n]):
        return out
    alpha = 2.0 / (n + 1.0)
    value = float(close[n])
    out[n] = value
    for i in range(n + 1, close.shape[0]):
        k = abs(float(cmo[i])) / 100.0
        value = alpha * k * float(close[i]) + (1.0 - alpha * k) * value
        out[i] = value
    return out


def tillson_t3(close: np.ndarray, n: int, volume_factor: float) -> np.ndarray:
    """Tillson T3. Six EMAs, then the cubic coefficients of the volume factor.

    Finite when the sixth EMA is finite, at index ``6 * (n - 1)``. The volume
    factor is Tillson's constant, not traded volume.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 0:
        return out
    e1 = ema(close, n)
    e2 = ema(e1, n)
    e3 = ema(e2, n)
    e4 = ema(e3, n)
    e5 = ema(e4, n)
    e6 = ema(e5, n)
    a = float(volume_factor)
    a2 = a * a
    a3 = a2 * a
    c1 = -a3
    c2 = 3.0 * a2 + 3.0 * a3
    c3 = -6.0 * a2 - 3.0 * a - 3.0 * a3
    c4 = 1.0 + 3.0 * a + a3 + 3.0 * a2
    ok = np.isfinite(e3) & np.isfinite(e4) & np.isfinite(e5) & np.isfinite(e6)
    out[ok] = c1 * e6[ok] + c2 * e5[ok] + c3 * e4[ok] + c4 * e3[ok]
    return out


def relative_momentum_index(close: np.ndarray, momentum: int, n: int) -> np.ndarray:
    """Altman RMI. Wilder RSI built on an ``momentum``-day price change, smoothed ``n``.

    Finite at index ``momentum + n - 1``. A window with no losses is 100, and
    a window with no movement is 50, matching :func:`rsi_wilder`.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    momentum, n = int(momentum), int(n)
    if momentum <= 0 or n <= 0 or close.shape[0] <= momentum + n - 1:
        return out
    change = np.zeros(close.shape[0], dtype=np.float64)
    change[momentum:] = close[momentum:] - close[:-momentum]
    gains = np.where(change > 0.0, change, 0.0)
    losses = np.where(change < 0.0, -change, 0.0)
    first = momentum + n - 1
    avg_gain = float(np.mean(gains[momentum : momentum + n]))
    avg_loss = float(np.mean(losses[momentum : momentum + n]))

    def value(g: float, lo: float) -> float:
        if lo == 0.0:
            return 50.0 if g == 0.0 else 100.0
        return 100.0 - 100.0 / (1.0 + g / lo)

    out[first] = value(avg_gain, avg_loss)
    for i in range(first + 1, close.shape[0]):
        avg_gain = (avg_gain * (n - 1) + float(gains[i])) / n
        avg_loss = (avg_loss * (n - 1) + float(losses[i])) / n
        out[i] = value(avg_gain, avg_loss)
    return out


def mcginley_dynamic(close: np.ndarray, n: int) -> np.ndarray:
    """McGinley dynamic average. Seeded by the SMA at index ``n - 1``.

    ``MD + (close - MD) / (n * (close / MD) ** 4)``. The adjustment uses the
    close relative to the running average, so a fast market catches up sooner
    than an EMA of the same length.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 0 or close.shape[0] < n or not bool(np.all(np.isfinite(close))):
        return out
    value = float(np.mean(close[:n]))
    out[n - 1] = value
    for i in range(n, close.shape[0]):
        price = float(close[i])
        if value <= 0.0 or price <= 0.0:
            out[i] = value
            continue
        adj = n * (price / value) ** 4
        if adj == 0.0:
            out[i] = value
            continue
        value = value + (price - value) / adj
        out[i] = value
    return out


def ichimoku_spans(
    high: np.ndarray, low: np.ndarray, conv: int, base: int, span: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Senkou span A and span B plotted on day ``t``.

    Same displacement as :func:`ichimoku_cloud_top`: both spans are the values
    computed through day ``t - base``. A missing span stays NaN.
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
    return cloud_a, cloud_b


def third_friday_dom(ts: np.ndarray) -> np.ndarray:
    """Day-of-month of the third Friday in each timestamp's UTC month.

    The third Friday always falls on day 15 through 21. Friday is weekday 4
    (Monday is 0), matching :func:`weekday_utc`.
    """
    dom = day_of_month(ts).astype(np.int64)
    dow = weekday_utc(ts).astype(np.int64)
    dow1 = np.mod(dow - (dom - 1), 7).astype(np.int64)
    first_fri = 1 + np.mod(4 - dow1, 7).astype(np.int64)
    return first_fri + 14


def demarker(high: np.ndarray, low: np.ndarray, n: int) -> np.ndarray:
    """DeMark DeMarker. SMA of DeMax over SMA of DeMax plus DeMin.

    DeMax is the part of today's high above yesterday's high. DeMin is the
    part of yesterday's low above today's low. Finite once ``n`` of those
    one-day comparisons exist, at index ``n``. A window with no extension
    either way is 0.5.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    demax = _nan(high.shape[0])
    demin = _nan(high.shape[0])
    if high.shape[0] > 1:
        up = high[1:] - high[:-1]
        down = low[:-1] - low[1:]
        demax[1:] = np.where(up > 0.0, up, 0.0)
        demin[1:] = np.where(down > 0.0, down, 0.0)
    num = sma_skip_leading(demax, n)
    den = sma_skip_leading(demin, n)
    out = _nan(high.shape[0])
    total = num + den
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(total) & (total > 0.0)
        flat = np.isfinite(total) & (total == 0.0)
        out[ok] = num[ok] / total[ok]
        out[flat] = 0.5
    return out


def zero_lag_ema(close: np.ndarray, n: int) -> np.ndarray:
    """Ehlers zero-lag EMA. The lag is ``(n - 1) // 2`` bars.

    The EMA input is ``close + (close - close[lag])``. Finite at
    ``lag + n - 1``, where the EMA of that adjusted series seeds.
    """
    close = np.asarray(close, dtype=np.float64)
    n = int(n)
    if n <= 0:
        return _nan(close.shape[0])
    lag = (n - 1) // 2
    adjusted = _nan(close.shape[0])
    if lag <= 0:
        adjusted = close.copy()
    elif close.shape[0] > lag:
        adjusted[lag:] = 2.0 * close[lag:] - close[:-lag]
    return ema(adjusted, n)


def frama(close: np.ndarray, n: int) -> np.ndarray:
    """Ehlers fractal adaptive moving average of the close.

    The window is split in half. The dimension is
    ``(log(N1 + N2) - log(N3)) / log(2)`` with each range divided by its
    length. ``alpha = exp(-4.6 * (D - 1))``, clipped to 0.01..1. A half
    with no range keeps the previous alpha. Seeded with the close at
    index ``n - 1``.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    half = n // 2
    if half < 1 or close.shape[0] < n:
        return out

    def dimension(t: int) -> float:
        recent = close[t - half + 1 : t + 1]
        older = close[t - n + 1 : t - half + 1]
        full = close[t - n + 1 : t + 1]
        n1 = (float(np.max(recent)) - float(np.min(recent))) / half
        n2 = (float(np.max(older)) - float(np.min(older))) / max(older.shape[0], 1)
        n3 = (float(np.max(full)) - float(np.min(full))) / n
        if n1 > 0.0 and n2 > 0.0 and n3 > 0.0:
            return float((np.log(n1 + n2) - np.log(n3)) / np.log(2.0))
        return float("nan")

    def alpha_of(dim: float, previous: float) -> float:
        if not np.isfinite(dim):
            return previous
        return float(np.clip(np.exp(-4.6 * (dim - 1.0)), 0.01, 1.0))

    alpha = alpha_of(dimension(n - 1), 1.0)
    value = float(close[n - 1])
    out[n - 1] = value
    for t in range(n, close.shape[0]):
        alpha = alpha_of(dimension(t), alpha)
        value = alpha * float(close[t]) + (1.0 - alpha) * value
        out[t] = value
    return out


def super_smoother(close: np.ndarray, n: int) -> np.ndarray:
    """Ehlers two-pole super smoother. Finite from index 2.

    The first two bars seed the recursion with the price and are not
    themselves filter output. Coefficients use ``sqrt(2) * pi / n``.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n < 1 or close.shape[0] < 3 or not bool(np.all(np.isfinite(close))):
        return out
    a1 = float(np.exp(-np.sqrt(2.0) * np.pi / n))
    b1 = float(2.0 * a1 * np.cos(np.sqrt(2.0) * np.pi / n))
    c2 = b1
    c3 = -a1 * a1
    c1 = 1.0 - c2 - c3
    prev2 = float(close[0])
    prev1 = float(close[1])
    for t in range(2, close.shape[0]):
        value = c1 * (float(close[t]) + float(close[t - 1])) / 2.0 + c2 * prev1 + c3 * prev2
        out[t] = value
        prev2 = prev1
        prev1 = value
    return out


def cyber_cycle(price: np.ndarray, alpha: float) -> np.ndarray:
    """Ehlers cyber cycle. The trigger is the previous value, not returned here.

    For the first six bars (indexes 2..5) the cycle is the four-bar
    second difference he uses before the filter is full. From index 6 it
    is the smoothed high-pass. ``alpha`` is the book constant (0.07).
    """
    price = np.asarray(price, dtype=np.float64)
    out = _nan(price.shape[0])
    if price.shape[0] < 3 or not bool(np.all(np.isfinite(price))):
        return out
    a = float(alpha)
    lead = (1.0 - 0.5 * a) ** 2
    follow = 2.0 * (1.0 - a)
    decay = (1.0 - a) ** 2
    smooth = _nan(price.shape[0])
    for t in range(price.shape[0]):
        if t >= 3:
            smooth[t] = (
                float(price[t]) + 2.0 * float(price[t - 1]) + 2.0 * float(price[t - 2]) + float(price[t - 3])
            ) / 6.0
        if t < 2:
            continue
        if t < 6:
            out[t] = (float(price[t]) - 2.0 * float(price[t - 1]) + float(price[t - 2])) / 4.0
        else:
            out[t] = (
                lead * (float(smooth[t]) - 2.0 * float(smooth[t - 1]) + float(smooth[t - 2]))
                + follow * float(out[t - 1])
                - decay * float(out[t - 2])
            )
    return out


def laguerre_filter(close: np.ndarray, gamma: float) -> np.ndarray:
    """Ehlers four-pole Laguerre moving average. Finite from index 1.

    Same poles as :func:`laguerre_rsi`. The output is
    ``(L0 + 2 L1 + 2 L2 + L3) / 6``, not the up-move share of those poles.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    if close.shape[0] == 0 or not np.isfinite(close[0]):
        return out
    g = float(gamma)
    l0 = l1 = l2 = l3 = float(close[0])
    for i in range(1, close.shape[0]):
        price = float(close[i])
        prev0, prev1, prev2 = l0, l1, l2
        l0 = (1.0 - g) * price + g * l0
        l1 = -g * l0 + prev0 + g * l1
        l2 = -g * l1 + prev1 + g * l2
        l3 = -g * l2 + prev2 + g * l3
        out[i] = (l0 + 2.0 * l1 + 2.0 * l2 + l3) / 6.0
    return out


def alma(close: np.ndarray, n: int, offset: float, sigma: float) -> np.ndarray:
    """Arnaud Legoux moving average. Finite at index ``n - 1``.

    ``m = offset * (n - 1)`` and ``s = n / sigma``. Weights are Gaussian
    in the window, index 0 the oldest bar. Offset 0.85 puts the peak near
    the newest bar. Sigma stays the published 6 at the call site.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 0 or sigma == 0.0 or close.shape[0] < n:
        return out
    m = float(offset) * (n - 1)
    s = n / float(sigma)
    i = np.arange(n, dtype=np.float64)
    weights = np.exp(-((i - m) ** 2) / (2.0 * s * s))
    scale = float(weights.sum())
    if scale == 0.0:
        return out
    out[n - 1 :] = sliding_window_view(close, n) @ weights / scale
    return out


def linreg_value(close: np.ndarray, n: int) -> np.ndarray:
    """Fitted value of the least-squares line on the current bar.

    The line is the same one as :func:`linreg_slope`. At the newest bar,
    ``mean + slope * (n - 1) / 2``. Finite at index ``n - 1``. The close
    can sit above this value while the slope is negative.
    """
    close = np.asarray(close, dtype=np.float64)
    n = int(n)
    out = _nan(close.shape[0])
    if n <= 1:
        return out
    slope = linreg_slope(close, n)
    mean = sma(close, n)
    shift = (n - 1) / 2.0
    ok = np.isfinite(slope) & np.isfinite(mean)
    out[ok] = mean[ok] + slope[ok] * shift
    return out


def variance_ratio(close: np.ndarray, q: int, window: int) -> np.ndarray:
    """Lo-MacKinlay variance ratio of overlapping log returns. Finite at ``window``.

    ``window`` is the number of one-day log returns. The numerator is the
    population variance of the overlapping ``q``-day sums that lie inside
    that window. The denominator is ``q`` times the population variance of
    those one-day returns. A zero one-day variance is NaN, not a ratio of 1.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    q = int(q)
    window = int(window)
    if q < 2 or window <= q or close.shape[0] <= window:
        return out
    ret = np.full(close.shape[0], np.nan, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        valid = (close[1:] > 0.0) & (close[:-1] > 0.0)
        logged = np.log(close[1:] / close[:-1])
    ret[1:] = np.where(valid, logged, np.nan)
    returns = ret[1:]
    if returns.shape[0] < window or not bool(np.all(np.isfinite(returns))):
        return out
    samples = sliding_window_view(returns, window)
    cumulative = np.cumsum(samples, axis=1)
    lead = cumulative[:, q - 1 :]
    tail = np.concatenate(
        [np.zeros((samples.shape[0], 1), dtype=np.float64), cumulative[:, : samples.shape[1] - q]],
        axis=1,
    )
    summed = lead - tail
    var1 = np.var(samples, axis=1)
    varq = np.var(summed, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ok = (var1 > 0.0) & np.isfinite(varq)
        ratios = np.full(samples.shape[0], np.nan, dtype=np.float64)
        ratios[ok] = varq[ok] / (q * var1[ok])
    out[window : window + ratios.shape[0]] = ratios
    return out


def vertical_horizontal_filter(close: np.ndarray, n: int) -> np.ndarray:
    """Adam White VHF. Range of the last ``n`` closes over the sum of ``n`` absolute changes.

    Finite at index ``n``. A window with no close-to-close movement is NaN.
    A high reading is a directional path. A low reading is a choppy one.
    """
    close = np.asarray(close, dtype=np.float64)
    n = int(n)
    out = _nan(close.shape[0])
    if n <= 0:
        return out
    span = rolling_max(close, n) - rolling_min(close, n)
    change = _nan(close.shape[0])
    if close.shape[0] > 1:
        change[1:] = np.abs(np.diff(close))
    denom = sum_skip_leading(change, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        ok = np.isfinite(span) & np.isfinite(denom) & (denom > 0.0)
        out[ok] = span[ok] / denom[ok]
    return out


def garman_klass(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Mean of the last ``n`` Garman-Klass bar variances. Finite at index ``n - 1``.

    One bar is ``0.5 * ln(H/L)^2 - (2 ln 2 - 1) * ln(C/O)^2``. The average
    can be negative. A non-positive price leaves that bar undefined.
    """
    open_ = np.asarray(open_, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    raw = _nan(close.shape[0])
    with np.errstate(divide="ignore", invalid="ignore"):
        ok = (high > 0.0) & (low > 0.0) & (open_ > 0.0) & (close > 0.0) & (high >= low)
        hl = np.log(high[ok] / low[ok])
        co = np.log(close[ok] / open_[ok])
        raw[ok] = 0.5 * hl * hl - (2.0 * np.log(2.0) - 1.0) * co * co
    return sma_skip_leading(raw, n)


def efficiency_ratio(close: np.ndarray, n: int) -> np.ndarray:
    """Kaufman efficiency ratio. Finite at index ``n``.

    The net change over ``n`` closes, divided by the sum of the absolute
    one-day changes in that window. A window with no movement is 0.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 0 or close.shape[0] <= n:
        return out
    change = np.abs(np.diff(close))
    csum = np.concatenate(([0.0], np.cumsum(change)))
    volatility = csum[n:] - csum[:-n]
    direction = np.abs(close[n:] - close[:-n])
    ratio = np.zeros(direction.shape[0], dtype=np.float64)
    np.divide(direction, volatility, out=ratio, where=volatility > 0.0)
    out[n:] = ratio
    return out


def stochastic_momentum_index(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int, smooth: int,
) -> np.ndarray:
    """Blau SMI. Double-smoothed distance from the range midpoint, over half the range.

    Both exponential smooths use ``smooth``. A zero range is 0, the midpoint.
    The scale is about -100 to 100. Finite once the second smooth exists.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    smooth = int(smooth)
    if n <= 0 or smooth <= 0:
        return out
    hh = rolling_max(high, n)
    ll = rolling_min(low, n)
    width = hh - ll
    num = ema(ema(close - (hh + ll) / 2.0, smooth), smooth)
    den = ema(ema(width, smooth), smooth)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(num) & np.isfinite(den) & (den > 0.0)
        flat = np.isfinite(num) & np.isfinite(den) & (den == 0.0)
        out[ok] = 100.0 * num[ok] / (0.5 * den[ok])
        out[flat] = 0.0
    return out


def linreg_r2(close: np.ndarray, n: int) -> np.ndarray:
    """R-squared of the least-squares line on the last ``n`` closes. Finite at ``n - 1``.

    A flat window, with no variance, is 0 rather than undefined.
    """
    _slope, _fitted, r2, _se = _linreg_parts(close, n)
    return r2


def linreg_stderr(close: np.ndarray, n: int) -> np.ndarray:
    """Residual standard error of that line, divisor ``n - 2``. Finite at ``n - 1``.

    A perfect line has standard error 0. ``n`` below 3 is undefined.
    """
    _slope, _fitted, _r2, se = _linreg_parts(close, n)
    return se


def _linreg_parts(
    close: np.ndarray, n: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    close = np.asarray(close, dtype=np.float64)
    m = close.shape[0]
    slope = _nan(m)
    fitted = _nan(m)
    r2 = _nan(m)
    se = _nan(m)
    n = int(n)
    if n <= 2 or m < n:
        return slope, fitted, r2, se
    x = np.arange(n, dtype=np.float64)
    sum_x = float(x.sum())
    sum_x2 = float(x @ x)
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0.0:
        return slope, fitted, r2, se
    window = sliding_window_view(close, n)
    sum_y = window.sum(axis=1)
    sum_y2 = np.square(window).sum(axis=1)
    sum_xy = window @ x
    mean_x = sum_x / n
    mean_y = sum_y / n
    slope_v = (n * sum_xy - sum_x * sum_y) / denom
    intercept = mean_y - slope_v * mean_x
    fitted_v = intercept + slope_v * (n - 1)
    ss_tot = sum_y2 - n * np.square(mean_y)
    ss_xx = sum_x2 - n * mean_x * mean_x
    ss_res = np.maximum(ss_tot - np.square(slope_v) * ss_xx, 0.0)
    r2_v = np.zeros(slope_v.shape[0], dtype=np.float64)
    np.divide(ss_tot - ss_res, ss_tot, out=r2_v, where=ss_tot > 1e-18)
    se_v = np.sqrt(ss_res / (n - 2))
    slope[n - 1 :] = slope_v
    fitted[n - 1 :] = fitted_v
    r2[n - 1 :] = r2_v
    se[n - 1 :] = se_v
    return slope, fitted, r2, se


def instantaneous_trendline(high: np.ndarray, low: np.ndarray, alpha: float) -> np.ndarray:
    """Ehlers instantaneous trendline of the median price. Finite from index 6.

    The first six bars only seed the recursion, the same way the book
    replaces the filter until the seventh bar. ``alpha`` is the book
    coefficient (he uses 0.07), not a period.
    """
    price = (np.asarray(high, dtype=np.float64) + np.asarray(low, dtype=np.float64)) / 2.0
    out = _nan(price.shape[0])
    a = float(alpha)
    if price.shape[0] < 7 or not (0.0 < a < 1.0):
        return out
    smooth = _nan(price.shape[0])
    for t in range(3, price.shape[0]):
        smooth[t] = (price[t] + 2.0 * price[t - 1] + 2.0 * price[t - 2] + price[t - 3]) / 6.0
    it0 = float(price[4])
    it1 = float(price[5])
    c0 = a - 0.25 * a * a
    c1 = 0.5 * a * a
    c2 = -(a - 0.75 * a * a)
    a1 = 2.0 * (1.0 - a)
    a2 = -((1.0 - a) ** 2)
    for t in range(6, price.shape[0]):
        value = (
            c0 * float(smooth[t]) + c1 * float(smooth[t - 1]) + c2 * float(smooth[t - 2])
            + a1 * it1 + a2 * it0
        )
        out[t] = value
        it0, it1 = it1, value
    return out


# First bar at which the Hilbert homodyne uses only earlier real prices.
# Smooth needs 3, the detrender's sixth lag needs smooth, and that series
# is lagged again by the quadrature and the 90-degree advance. Index 40
# is past that chain, and the period clamp has had bars to settle.
HILBERT_READY = 40


def mesa_adaptive(
    high: np.ndarray, low: np.ndarray, fast_limit: float, slow_limit: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Ehlers MAMA and the dominant-cycle phase in degrees.

    Price is the median. Fast and slow limits bound the adaptive alpha.
    Both outputs are finite from :data:`HILBERT_READY`. Earlier bars are
    the zero-seeded startup the EasyLanguage leaves unpublished.
    Phase is the arctangent of the quadrature over the in-phase component,
    in degrees, which the sine-wave rule reads. It is not MAMA.
    """
    price = (np.asarray(high, dtype=np.float64) + np.asarray(low, dtype=np.float64)) / 2.0
    m = price.shape[0]
    mama = _nan(m)
    phase = _nan(m)
    fast = float(fast_limit)
    slow = float(slow_limit)
    if m <= HILBERT_READY or fast <= 0.0 or slow <= 0.0 or slow > fast:
        return mama, phase
    smooth = np.zeros(m, dtype=np.float64)
    detrender = np.zeros(m, dtype=np.float64)
    i1 = np.zeros(m, dtype=np.float64)
    q1 = np.zeros(m, dtype=np.float64)
    i2 = np.zeros(m, dtype=np.float64)
    q2 = np.zeros(m, dtype=np.float64)
    re = np.zeros(m, dtype=np.float64)
    im = np.zeros(m, dtype=np.float64)
    period = np.zeros(m, dtype=np.float64)
    smooth_period = np.zeros(m, dtype=np.float64)
    phase_v = np.zeros(m, dtype=np.float64)
    mama_v = 0.0
    fama_v = 0.0
    for t in range(m):
        if t >= 3:
            smooth[t] = (
                4.0 * price[t] + 3.0 * price[t - 1] + 2.0 * price[t - 2] + price[t - 3]
            ) / 10.0
        adj = 0.075 * (period[t - 1] if t >= 1 else 0.0) + 0.54
        if t >= 6:
            detrender[t] = (
                0.0962 * smooth[t] + 0.5769 * smooth[t - 2]
                - 0.5769 * smooth[t - 4] - 0.0962 * smooth[t - 6]
            ) * adj
        if t >= 6:
            q1[t] = (
                0.0962 * detrender[t] + 0.5769 * detrender[t - 2]
                - 0.5769 * detrender[t - 4] - 0.0962 * detrender[t - 6]
            ) * adj
            i1[t] = detrender[t - 3]
        if t >= 6:
            ji = (
                0.0962 * i1[t] + 0.5769 * i1[t - 2]
                - 0.5769 * i1[t - 4] - 0.0962 * i1[t - 6]
            ) * adj
            jq = (
                0.0962 * q1[t] + 0.5769 * q1[t - 2]
                - 0.5769 * q1[t - 4] - 0.0962 * q1[t - 6]
            ) * adj
            i2_raw = i1[t] - jq
            q2_raw = q1[t] + ji
            i2[t] = 0.2 * i2_raw + 0.8 * i2[t - 1]
            q2[t] = 0.2 * q2_raw + 0.8 * q2[t - 1]
        if t >= 1:
            re_raw = i2[t] * i2[t - 1] + q2[t] * q2[t - 1]
            im_raw = i2[t] * q2[t - 1] - q2[t] * i2[t - 1]
            re[t] = 0.2 * re_raw + 0.8 * re[t - 1]
            im[t] = 0.2 * im_raw + 0.8 * im[t - 1]
        per = period[t - 1] if t >= 1 else 0.0
        if im[t] != 0.0 and re[t] != 0.0:
            per = 360.0 / math.degrees(math.atan(im[t] / re[t]))
        prev = period[t - 1] if t >= 1 else 0.0
        if prev != 0.0:
            if per > 1.5 * prev:
                per = 1.5 * prev
            if per < 0.67 * prev:
                per = 0.67 * prev
        if per < 6.0:
            per = 6.0
        if per > 50.0:
            per = 50.0
        if t >= 1:
            per = 0.2 * per + 0.8 * period[t - 1]
            smooth_period[t] = 0.33 * per + 0.67 * smooth_period[t - 1]
        period[t] = per
        if i1[t] != 0.0:
            phase_v[t] = math.degrees(math.atan(q1[t] / i1[t]))
        elif t >= 1:
            phase_v[t] = phase_v[t - 1]
        delta = (phase_v[t - 1] - phase_v[t]) if t >= 1 else 1.0
        if delta < 1.0:
            delta = 1.0
        alpha = fast / delta
        if alpha < slow:
            alpha = slow
        if alpha > fast:
            alpha = fast
        if t == 0:
            mama_v = float(price[0])
            fama_v = float(price[0])
        else:
            mama_v = alpha * float(price[t]) + (1.0 - alpha) * mama_v
            fama_v = 0.5 * alpha * mama_v + (1.0 - 0.5 * alpha) * fama_v
        if t >= HILBERT_READY:
            mama[t] = mama_v
            phase[t] = phase_v[t]
    return mama, phase


def center_of_gravity(close: np.ndarray, n: int) -> np.ndarray:
    """Ehlers center of gravity of the last ``n`` closes. Finite at ``n - 1``.

    The newest close has weight 1 and the oldest has weight ``n``. The
    ratio is negated, so the value rises when the later closes get heavier.
    A window that sums to zero is left undefined. A flat window sits at
    ``-(n + 1) / 2``.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 1 or close.shape[0] < n:
        return out
    weights = np.arange(n, 0, -1, dtype=np.float64)
    window = sliding_window_view(close, n)
    numer = window @ weights
    denom = window.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(denom) & (denom != 0.0)
        out[n - 1 :][ok] = -numer[ok] / denom[ok]
    return out


def roofing_filter(close: np.ndarray, highpass: int, lowpass: int = 10) -> np.ndarray:
    """Ehlers roofing filter: a high-pass, then a super smoother. Finite from index 2.

    The high-pass length is the cutoff. The smoother length stays at the
    call site. Both recursions are seeded at 0, which is "no history", not
    a later bar. A straight line has a zero second difference, so the
    filter stays at 0.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    hp_n = int(highpass)
    lp = int(lowpass)
    if hp_n < 2 or lp < 1 or close.shape[0] < 3 or not bool(np.all(np.isfinite(close))):
        return out
    angle = 0.707 * 2.0 * np.pi / hp_n
    cosine = float(np.cos(angle))
    if cosine == 0.0:
        return out
    alpha = (cosine + float(np.sin(angle)) - 1.0) / cosine
    decay = 1.0 - alpha
    lead = (1.0 - alpha / 2.0) ** 2
    hp_prev2 = 0.0
    hp_prev1 = 0.0
    a1 = float(np.exp(-np.sqrt(2.0) * np.pi / lp))
    b1 = float(2.0 * a1 * np.cos(np.sqrt(2.0) * np.pi / lp))
    c2 = b1
    c3 = -a1 * a1
    c1 = 1.0 - c2 - c3
    filt_prev2 = 0.0
    filt_prev1 = 0.0
    hp_prev = 0.0
    for t in range(2, close.shape[0]):
        hp = (
            lead * (float(close[t]) - 2.0 * float(close[t - 1]) + float(close[t - 2]))
            + 2.0 * decay * hp_prev1
            - decay * decay * hp_prev2
        )
        filt = c1 * (hp + hp_prev) / 2.0 + c2 * filt_prev1 + c3 * filt_prev2
        out[t] = filt
        hp_prev2 = hp_prev1
        hp_prev1 = hp
        hp_prev = hp
        filt_prev2 = filt_prev1
        filt_prev1 = filt
    return out


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """Average ranks of one window. The smallest value has rank 0."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.shape[0], dtype=np.float64)
    ordered = values[order]
    start = 0
    n = int(values.shape[0])
    while start < n:
        stop = start + 1
        while stop < n and ordered[stop] == ordered[start]:
            stop += 1
        ranks[order[start:stop]] = 0.5 * (start + (stop - 1))
        start = stop
    return ranks


def spearman_rho(close: np.ndarray, n: int) -> np.ndarray:
    """Spearman correlation of the last ``n`` closes with time. Finite at ``n - 1``.

    Time runs oldest to newest. Ties share the average rank. A constant
    window has no price rank to compare and is 0, which is not a long.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n < 3 or close.shape[0] < n:
        return out
    time = np.arange(n, dtype=np.float64)
    time = time - time.mean()
    time_ss = float(np.dot(time, time))
    windows = sliding_window_view(close, n)
    for i in range(windows.shape[0]):
        ranks = _average_ranks(windows[i])
        ranks = ranks - ranks.mean()
        price_ss = float(np.dot(ranks, ranks))
        if price_ss <= 0.0 or time_ss <= 0.0:
            out[n - 1 + i] = 0.0
        else:
            out[n - 1 + i] = float(np.dot(ranks, time) / np.sqrt(price_ss * time_ss))
    return out


def theil_sen_slope(close: np.ndarray, n: int) -> np.ndarray:
    """Median of the pairwise slopes of the last ``n`` closes. Finite at ``n - 1``.

    Each pair of closes contributes ``(later - earlier) / bars between``.
    A flat window has median 0, which is not a long.
    """
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n < 2 or close.shape[0] < n:
        return out
    window = sliding_window_view(close, n)
    slopes = [
        (window[:, j] - window[:, i]) / float(j - i)
        for i in range(n)
        for j in range(i + 1, n)
    ]
    out[n - 1 :] = np.median(np.stack(slopes, axis=1), axis=1)
    return out


def intraday_momentum(open_: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Chande intraday momentum index. Finite at ``n - 1``.

    Gains are close minus open when the close is higher. Losses are the
    other way. The index is 100 times the gains over gains plus losses.
    A window with no body is 50, the same reading as a flat RSI.
    """
    open_ = np.asarray(open_, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    out = _nan(close.shape[0])
    n = int(n)
    if n <= 0 or close.shape[0] < n:
        return out
    gain = np.maximum(close - open_, 0.0)
    loss = np.maximum(open_ - close, 0.0)
    sum_gain = np.concatenate(([0.0], np.cumsum(gain)))
    sum_loss = np.concatenate(([0.0], np.cumsum(loss)))
    idx = np.arange(n - 1, close.shape[0])
    up = sum_gain[idx + 1] - sum_gain[idx + 1 - n]
    down = sum_loss[idx + 1] - sum_loss[idx + 1 - n]
    total = up + down
    value = np.full(idx.shape[0], 50.0, dtype=np.float64)
    np.divide(100.0 * up, total, out=value, where=total > 0.0)
    out[n - 1 :] = value
    return out
