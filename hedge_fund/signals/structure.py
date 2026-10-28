"""Named OHLC structure atoms: Donchian, fractal swing S&R, double bottom/top.

Stolen (small, named, backtestable):

- Donchian N-bar high/low — turtles / channel breakout. ``don_hi_N`` is a
  *close* break of the prior N-bar high. ``don_lo_N`` tags a dip *at* the
  prior N-bar low (support), not a chart-pattern zoo.
- Williams-style fractal swings — pivot at ``j`` is the unique extreme of
  ``[j-k, j+k]``, and is invisible until ``k`` bars exist to the right.
- Double bottom / double top — two confirmed fractal swings (same window as
  ``near_swing_*_k``). The second swing **holds** (within the hold band of
  the first); close has recovered off the second low (``dbl_bot``) or broken
  down from the second high (``dbl_top``).

Trend / breakout / momentum are **not** re-invented here: they already exist
as ``sma_stack`` / ``sma_abv``, ``don_hi_*``, and ``mom_*``.

Refused (lookahead magnets / universe explosion): head-and-shoulders, flags,
triangles, FVGs, order blocks, 40 candlestick names, screenshot vision, and
the price-action-lib kitchen sink. Round-number psychological levels
(``near_round_100``) are skipped: a $100 step is a different game on BTC vs
ETH, and we refuse a silent symbol-dependent rewrite. ``dbl_top`` is parsed
but not listed as a standalone long (long-only book; no NOT combinator).

No lookahead
------------
Donchian uses bars **before** the decision bar: level = max/min of
``highs[i-N:i]`` / ``lows[i-N:i]`` (Python slice, current bar excluded).
The current bar's high cannot be the Donchian level that is being broken.
A wick through the prior high with close still below is not a break.

Swing pivots require the right-hand ``k`` bars to already exist
(``j + k <= i``). A pivot on the decision bar is not a swing yet. Double
bottom/top cannot fire on the unconfirmed second pivot for the same reason.

Near-level tolerance
--------------------
``near_swing_*`` and ``don_lo_*`` (support tag) are true when

    abs(close - level) <= max(NEAR_PCT * close, NEAR_ATR_MULT * ATR(14))

with ``NEAR_PCT = 0.002`` (0.20%) and ``NEAR_ATR_MULT = 0.25``. If ATR is
unavailable the percent band is used alone. ``don_hi_*`` is a strict close
break (``close > prior high``), not a "near" band.

Hold tolerance (double bottom / top)
------------------------------------
The second swing holds when it is within

    max(HOLD_PCT * |first|, HOLD_ATR_MULT * ATR(14))

of the first, with ``HOLD_PCT = 0.01`` (1.0%) and ``HOLD_ATR_MULT = 1.0``.
A much lower second low is not a double bottom; a much higher second high
is not a double top. If ATR is unavailable the percent band is used alone.

Honesty: these atoms need a high/low series. Passing ``highs=None`` raises
rather than silently using close as high/low (no same-series lie).
"""
from __future__ import annotations

import math
from collections import deque

NEAR_PCT = 0.002  # 0.20% of close
NEAR_ATR_MULT = 0.25
NEAR_ATR_PERIOD = 14

HOLD_PCT = 0.01  # 1.0% of the first swing — second holds if within this
HOLD_ATR_MULT = 1.0  # …or 1 × ATR(14), whichever is larger


def _idx(closes: list[float], i: int | None) -> int:
    return len(closes) - 1 if i is None else i


def require_ohlc(
    closes: list[float],
    highs: list[float] | None,
    lows: list[float] | None,
) -> tuple[list[float], list[float]]:
    """Refuse close-as-high/low. Structure is OHLC, not a close-only proxy."""
    if highs is None or lows is None:
        raise ValueError(
            "structure atoms need high/low series; refusing to use close as a "
            "high/low proxy (no silent same-series lie)"
        )
    if len(highs) != len(closes) or len(lows) != len(closes):
        raise ValueError("highs/lows length must match closes")
    return highs, lows


_SERIES_CACHE_MAX = 128
_atr_cache: dict[tuple, list[float]] = {}
_swing_cache: dict[tuple, tuple] = {}
_donchian_cache: dict[tuple, list[float]] = {}


def clear_structure_cache() -> None:
    """Drop per-tape structure series. The next name on the same lists rebuilds nothing."""
    _atr_cache.clear()
    _swing_cache.clear()
    _donchian_cache.clear()


def _remember(cache: dict, key: tuple, value):
    if len(cache) >= _SERIES_CACHE_MAX:
        cache.clear()
    cache[key] = value
    return value


def _atr_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int,
) -> list[float]:
    """ATR at every bar. Same window mean as the per-bar loop in ``atr_at``."""
    n = len(closes)
    out = [float("nan")] * n
    if period <= 0 or n <= period:
        return out
    trs = [0.0] * n
    for j in range(1, n):
        h, l, pc = highs[j], lows[j], closes[j - 1]
        trs[j] = max(h - l, abs(h - pc), abs(l - pc))
    acc = sum(trs[1 : period + 1])
    out[period] = acc / period
    for i in range(period + 1, n):
        acc += trs[i] - trs[i - period]
        out[i] = acc / period
    return out


def _cached_atr_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int,
) -> list[float]:
    key = (id(highs), id(lows), id(closes), period, len(closes))
    hit = _atr_cache.get(key)
    if hit is not None and len(hit) == len(closes):
        return hit
    return _remember(_atr_cache, key, _atr_series(highs, lows, closes, period))


def atr_at(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = NEAR_ATR_PERIOD,
    i: int | None = None,
) -> float:
    """Average true range at bar i (same Wilder-window mean as rm_v1 backtests)."""
    i = _idx(closes, i)
    if i < period or period <= 0:
        return float("nan")
    return _cached_atr_series(highs, lows, closes, period)[i]


def near_band(close: float, atr_val: float) -> float:
    """Tolerance in price units: max(0.20% of close, 0.25 × ATR)."""
    band = NEAR_PCT * close
    if not math.isnan(atr_val) and atr_val > 0:
        band = max(band, NEAR_ATR_MULT * atr_val)
    return band


def is_near_level(close: float, level: float, atr_val: float) -> bool:
    return abs(close - level) <= near_band(close, atr_val)


def hold_band(first_level: float, atr_val: float) -> float:
    """Second-swing hold tolerance: max(1.0% of first, 1 × ATR)."""
    band = HOLD_PCT * abs(first_level)
    if not math.isnan(atr_val) and atr_val > 0:
        band = max(band, HOLD_ATR_MULT * atr_val)
    return band


def swings_hold(first: float, second: float, atr_val: float) -> bool:
    """True when the second swing is within the hold band of the first."""
    return abs(second - first) <= hold_band(first, atr_val)


def _range_before(values: list[float], n: int, *, high: bool) -> list[float]:
    """``out[i]`` is max/min of ``values[i-n:i]``. Not enough history → nan.

    One forward pass. Ties keep the extreme value, matching ``max`` / ``min``.
    """
    length = len(values)
    out = [float("nan")] * length
    if n < 1:
        return out
    dq: deque[int] = deque()
    for i in range(length):
        while dq and dq[0] < i - n:
            dq.popleft()
        if i >= n and dq:
            out[i] = values[dq[0]]
        if high:
            while dq and values[dq[-1]] <= values[i]:
                dq.pop()
        else:
            while dq and values[dq[-1]] >= values[i]:
                dq.pop()
        dq.append(i)
    return out


def _donchian_series(values: list[float], n: int, *, high: bool) -> list[float]:
    key = (id(values), n, high, len(values))
    hit = _donchian_cache.get(key)
    if hit is not None and len(hit) == len(values):
        return hit
    return _remember(_donchian_cache, key, _range_before(values, n, high=high))


def prior_donchian_high(highs: list[float], n: int, i: int) -> float:
    """Max high of the N bars *before* i. Current bar is not in the window."""
    if n < 1 or i < n:
        return float("nan")
    return _donchian_series(highs, n, high=True)[i]


def prior_donchian_low(lows: list[float], n: int, i: int) -> float:
    """Min low of the N bars *before* i. Current bar is not in the window."""
    if n < 1 or i < n:
        return float("nan")
    return _donchian_series(lows, n, high=False)[i]


def don_hi(
    closes: list[float],
    highs: list[float] | None,
    lows: list[float] | None,
    n: int,
    i: int | None = None,
) -> bool:
    """Long when close breaks the prior N-bar high (current bar excluded)."""
    highs, lows = require_ohlc(closes, highs, lows)
    i = _idx(closes, i)
    level = prior_donchian_high(highs, n, i)
    if math.isnan(level):
        return False
    return closes[i] > level


def don_lo(
    closes: list[float],
    highs: list[float] | None,
    lows: list[float] | None,
    n: int,
    i: int | None = None,
) -> bool:
    """Support tag: close is within the near-band of the prior N-bar low."""
    highs, lows = require_ohlc(closes, highs, lows)
    i = _idx(closes, i)
    level = prior_donchian_low(lows, n, i)
    if math.isnan(level):
        return False
    return is_near_level(closes[i], level, atr_at(highs, lows, closes, NEAR_ATR_PERIOD, i))


def _range_after(values: list[float], k: int, *, high: bool) -> list[float]:
    """``out[j]`` is max/min of the ``k`` bars after ``j`` (``j+1`` .. ``j+k``)."""
    n = len(values)
    rev = _range_before(values[::-1], k, high=high)
    out = [float("nan")] * n
    for j in range(n):
        # k bars after j are the k bars before rev index (n-1-j).
        src = n - 1 - j
        if 0 <= src < n:
            out[j] = rev[src]
    return out


def _pivot_list(values: list[float], k: int, want_high: bool) -> list[tuple[int, float]]:
    """Confirmed fractal pivots, oldest first. Built once per series and ``k``.

    ``j`` is a pivot when it is the strict unique extreme of
    ``values[j-k:j+k+1]``. The right-hand ``k`` bars must exist, so the
    last possible pivot is ``len-1-k``. Callers still hide a pivot until
    bar ``i`` satisfies ``j+k <= i``.
    """
    key = (id(values), k, want_high, len(values))
    hit = _swing_cache.get(key)
    if hit is not None and hit[0] is values:
        return hit[1]
    n = len(values)
    if k < 1 or n < 2 * k + 1:
        pivots: list[tuple[int, float]] = []
        _remember(_swing_cache, key, (values, pivots))
        return pivots
    left = _range_before(values, k, high=want_high)
    right = _range_after(values, k, high=want_high)
    pivots = []
    for j in range(k, n - k):
        v = values[j]
        lv = left[j]
        rv = right[j]
        if lv != lv or rv != rv:  # nan: window missing
            continue
        if want_high:
            if v > lv and v > rv:
                pivots.append((j, float(v)))
        elif v < lv and v < rv:
            pivots.append((j, float(v)))
    _remember(_swing_cache, key, (values, pivots))
    return pivots


def confirmed_swings(
    values: list[float],
    k: int,
    i: int,
    want_high: bool,
    limit: int | None = None,
) -> list[tuple[int, float]]:
    """Confirmed Williams-style fractal pivots on ``values``, newest first.

    Pivot at ``j`` is the unique max (high) or min (low) of
    ``values[j-k:j+k+1]``. Confirmed only when ``j + k <= i`` — the right
    side of the window already exists. A pivot on the decision bar is not
    a swing yet (no lookahead).

    The pivot list for ``(values, k)`` is built once in O(n) and reused
    for every bar and every later name on that same tape.
    """
    if k < 1 or i < 2 * k:
        return []
    pivots = _pivot_list(values, k, want_high)
    # Rightmost pivot with j+k <= i.
    lo, hi = 0, len(pivots)
    while lo < hi:
        mid = (lo + hi) // 2
        if pivots[mid][0] + k <= i:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return []
    start = 0 if limit is None else max(0, lo - limit)
    return list(reversed(pivots[start:lo]))


def last_confirmed_swing(
    values: list[float],
    k: int,
    i: int,
    want_high: bool,
) -> float | None:
    """Most recent Williams-style fractal pivot that is fully confirmed by bar i.

    Pivot at ``j`` is the unique max (high) or min (low) of ``values[j-k:j+k+1]``.
    Confirmed only when ``j + k <= i`` — the right side of the window exists.
    Search newest-first so a later confirmed pivot supersedes an older one.
    """
    found = confirmed_swings(values, k, i, want_high, limit=1)
    return found[0][1] if found else None


def near_swing_hi(
    closes: list[float],
    highs: list[float] | None,
    lows: list[float] | None,
    k: int,
    i: int | None = None,
) -> bool:
    """Close is within the near-band of the last confirmed swing high."""
    highs, lows = require_ohlc(closes, highs, lows)
    i = _idx(closes, i)
    level = last_confirmed_swing(highs, k, i, want_high=True)
    if level is None:
        return False
    return is_near_level(closes[i], level, atr_at(highs, lows, closes, NEAR_ATR_PERIOD, i))


def near_swing_lo(
    closes: list[float],
    highs: list[float] | None,
    lows: list[float] | None,
    k: int,
    i: int | None = None,
) -> bool:
    """Close is within the near-band of the last confirmed swing low."""
    highs, lows = require_ohlc(closes, highs, lows)
    i = _idx(closes, i)
    level = last_confirmed_swing(lows, k, i, want_high=False)
    if level is None:
        return False
    return is_near_level(closes[i], level, atr_at(highs, lows, closes, NEAR_ATR_PERIOD, i))


def dbl_bot(
    closes: list[float],
    highs: list[float] | None,
    lows: list[float] | None,
    k: int,
    i: int | None = None,
) -> bool:
    """Two confirmed swing lows, second holds, close recovered off the second.

    Same fractal as ``near_swing_lo_k``. Needs ``k`` bars to the right of
    the second swing before it exists. Second low holds if it is within
    ``max(1.0% of the first low, 1 × ATR(14))`` of the first — not a much
    lower low. Recovered means ``close >`` the second swing low.
    """
    highs, lows = require_ohlc(closes, highs, lows)
    i = _idx(closes, i)
    swings = confirmed_swings(lows, k, i, want_high=False, limit=2)
    if len(swings) < 2:
        return False
    (_j2, lo2), (_j1, lo1) = swings
    atr_val = atr_at(highs, lows, closes, NEAR_ATR_PERIOD, i)
    if not swings_hold(lo1, lo2, atr_val):
        return False
    return closes[i] > lo2


def dbl_top(
    closes: list[float],
    highs: list[float] | None,
    lows: list[float] | None,
    k: int,
    i: int | None = None,
) -> bool:
    """Two confirmed swing highs, second holds, close broken down.

    Long-only book: useful as a filter / short-signal we do not trade.
    ``parse_strategy`` accepts the name; the universe does not emit
    standalone ``dbl_top_*`` longs (no NOT combinator).
    """
    highs, lows = require_ohlc(closes, highs, lows)
    i = _idx(closes, i)
    swings = confirmed_swings(highs, k, i, want_high=True, limit=2)
    if len(swings) < 2:
        return False
    (_j2, hi2), (_j1, hi1) = swings
    atr_val = atr_at(highs, lows, closes, NEAR_ATR_PERIOD, i)
    if not swings_hold(hi1, hi2, atr_val):
        return False
    return closes[i] < hi2
