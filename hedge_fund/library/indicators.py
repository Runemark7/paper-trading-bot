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
