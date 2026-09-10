"""LazyBear WaveTrend (TCI-style) atoms — closed-bar 5m, HLC3 source.

Credit: public LazyBear WaveTrend oscillator (TradingView). The long rule is
the VuManChu Cipher B **green-dot family**: WT1 crosses above WT2 while WT2
is oversold. Inspired by that public green-dot, **not** CF Strategies Market
Cipher, not affiliated with Market Cipher, and not a scrape of the
invite-only Pine.

Frozen LazyBear-classic defaults (not VuManChu 9/12/3):

    CHANNEL_LEN = 10   # n1 — ESA / deviation EMA
    AVERAGE_LEN = 21   # n2 — TCI = EMA of CI
    SIGNAL_LEN  = 4    # WT2 = SMA of WT1
    OS_LEVEL    = -60  # LazyBear osLevel1
    OB_LEVEL    = +60  # LazyBear obLevel1
    source      = HLC3 = (high + low + close) / 3

    esa = EMA(HLC3, 10)
    d   = EMA(|HLC3 - esa|, 10)
    ci  = (HLC3 - esa) / (0.015 * d)
    WT1 = EMA(ci, 21)
    WT2 = SMA(WT1, 4)

Closed-bar only
---------------
Evaluated on the same 5m OHLC series as live. Bar ``i`` is a **closed**
bar: HLC3[i] uses that bar's high/low/close. Cross uses WT1/WT2 at ``i-1``
and ``i`` (both closed). No Heikin-Ashi rewrite, no ``request.security``
higher-TF with lookahead, no MFI / CMF / VWAP money-flow (volume is not
honest for the universe). Sommi flags/diamonds, gold-dot kitchen sink,
and divergence zoo are out of v1.

Long-only book
--------------
``wt_cross_up_os`` is the green-dot long. ``wt_below_os`` is a filter
(WT2 still oversold) for ANDs. ``wt_cross_down_ob`` is parsed for tests
but is not a standalone long (same policy as ``dbl_top``).

Honesty: needs high/low. Passing ``highs=None`` raises rather than using
close as HLC3 (no silent same-series lie).
"""
from __future__ import annotations

import math

# LazyBear-classic (public WT script). Frozen — do not silently retune.
CHANNEL_LEN = 10
AVERAGE_LEN = 21
SIGNAL_LEN = 4
OS_LEVEL = -60.0
OB_LEVEL = 60.0
CI_SCALE = 0.015  # LazyBear constant


def _idx(closes: list[float], i: int | None) -> int:
    return len(closes) - 1 if i is None else i


def require_hlc(
    closes: list[float],
    highs: list[float] | None,
    lows: list[float] | None,
) -> tuple[list[float], list[float]]:
    """Refuse close-as-HLC3. WaveTrend source is (H+L+C)/3, not close-only."""
    if highs is None or lows is None:
        raise ValueError(
            "WaveTrend atoms need high/low series for HLC3; refusing to use "
            "close as a high/low proxy (no silent same-series lie)"
        )
    if len(highs) != len(closes) or len(lows) != len(closes):
        raise ValueError("highs/lows length must match closes")
    return highs, lows


def _ema_series(values: list[float], period: int) -> list[float]:
    """EMA with SMA seed; leading NaNs skipped (Pine chained-EMA warmup)."""
    n = len(values)
    out = [float("nan")] * n
    if period <= 0:
        return out
    start = 0
    while start < n and math.isnan(values[start]):
        start += 1
    if start + period > n:
        return out
    seed = values[start : start + period]
    if any(math.isnan(x) for x in seed):
        return out
    k = 2.0 / (period + 1)
    val = sum(seed) / period
    out[start + period - 1] = val
    for idx in range(start + period, n):
        if math.isnan(values[idx]):
            break
        val = values[idx] * k + val * (1.0 - k)
        out[idx] = val
    return out


def _sma_series(values: list[float], period: int) -> list[float]:
    """SMA; windows that contain NaN stay NaN (warmup of the source series)."""
    n = len(values)
    out = [float("nan")] * n
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        if any(math.isnan(x) for x in window):
            continue
        out[i] = sum(window) / period
    return out


def hlc3_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[float]:
    return [(highs[k] + lows[k] + closes[k]) / 3.0 for k in range(len(closes))]


def wavetrend_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    channel_len: int = CHANNEL_LEN,
    average_len: int = AVERAGE_LEN,
    signal_len: int = SIGNAL_LEN,
) -> tuple[list[float], list[float]]:
    """Return (WT1, WT2) series on HLC3. Prefix-stable: bar i ignores i+1…"""
    ap = hlc3_series(highs, lows, closes)
    esa = _ema_series(ap, channel_len)
    dev = [abs(ap[k] - esa[k]) if not math.isnan(esa[k]) else float("nan") for k in range(len(ap))]
    d = _ema_series(dev, channel_len)
    ci: list[float] = []
    for k in range(len(ap)):
        if math.isnan(esa[k]) or math.isnan(d[k]):
            ci.append(float("nan"))
        elif d[k] == 0.0:
            ci.append(0.0)
        else:
            ci.append((ap[k] - esa[k]) / (CI_SCALE * d[k]))
    wt1 = _ema_series(ci, average_len)
    wt2 = _sma_series(wt1, signal_len)
    return wt1, wt2


_WT_CACHE_MAX = 32
_wt_series_cache: dict[tuple[int, int, int, int], tuple[list[float], list[float]]] = {}


def clear_wavetrend_cache() -> None:
    _wt_series_cache.clear()


def _cached_wavetrend_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> tuple[list[float], list[float]]:
    """Causal WT1/WT2 for the whole series. Bar i ignores i+1… (prefix-stable)."""
    key = (id(highs), id(lows), id(closes), len(closes))
    hit = _wt_series_cache.get(key)
    if hit is None:
        if len(_wt_series_cache) >= _WT_CACHE_MAX:
            _wt_series_cache.clear()
        hit = wavetrend_series(highs, lows, closes)
        _wt_series_cache[key] = hit
    return hit


def wavetrend_at(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    i: int | None = None,
) -> tuple[float, float]:
    """(WT1, WT2) at closed bar i. Causal: bar i ignores i+1…"""
    i = _idx(closes, i)
    if i < 0:
        return float("nan"), float("nan")
    wt1, wt2 = _cached_wavetrend_series(highs, lows, closes)
    return wt1[i], wt2[i]


def _cross_up(wt1: list[float], wt2: list[float], i: int) -> bool:
    if i < 1:
        return False
    a0, b0 = wt1[i - 1], wt2[i - 1]
    a1, b1 = wt1[i], wt2[i]
    if any(math.isnan(x) for x in (a0, b0, a1, b1)):
        return False
    return a0 <= b0 and a1 > b1


def _cross_down(wt1: list[float], wt2: list[float], i: int) -> bool:
    if i < 1:
        return False
    a0, b0 = wt1[i - 1], wt2[i - 1]
    a1, b1 = wt1[i], wt2[i]
    if any(math.isnan(x) for x in (a0, b0, a1, b1)):
        return False
    return a0 >= b0 and a1 < b1


def wt_cross_up_os(
    closes: list[float],
    highs: list[float] | None,
    lows: list[float] | None,
    i: int | None = None,
) -> bool:
    """Green-dot long: WT1 crosses above WT2 on bar close while WT2 is oversold."""
    highs, lows = require_hlc(closes, highs, lows)
    i = _idx(closes, i)
    wt1, wt2 = _cached_wavetrend_series(highs, lows, closes)
    if not _cross_up(wt1, wt2, i):
        return False
    return not math.isnan(wt2[i]) and wt2[i] <= OS_LEVEL


def wt_below_os(
    closes: list[float],
    highs: list[float] | None,
    lows: list[float] | None,
    i: int | None = None,
) -> bool:
    """Filter: WT2 is still oversold (no cross required)."""
    highs, lows = require_hlc(closes, highs, lows)
    i = _idx(closes, i)
    _wt1, wt2 = _cached_wavetrend_series(highs, lows, closes)
    return not math.isnan(wt2[i]) and wt2[i] <= OS_LEVEL


def wt_cross_down_ob(
    closes: list[float],
    highs: list[float] | None,
    lows: list[float] | None,
    i: int | None = None,
) -> bool:
    """Short-side red-dot family. Parsed for tests; not a universe long."""
    highs, lows = require_hlc(closes, highs, lows)
    i = _idx(closes, i)
    wt1, wt2 = _cached_wavetrend_series(highs, lows, closes)
    if not _cross_down(wt1, wt2, i):
        return False
    return not math.isnan(wt2[i]) and wt2[i] >= OB_LEVEL
