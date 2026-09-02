"""Named OHLC structure atoms: Donchian channel and fractal swing S&R.

Stolen (small, named, backtestable):

- Donchian N-bar high/low — turtles / channel breakout. ``don_hi_N`` is a
  *close* break of the prior N-bar high. ``don_lo_N`` tags a dip *at* the
  prior N-bar low (support), not a chart-pattern zoo.
- Williams-style fractal swings — pivot at ``j`` is the unique extreme of
  ``[j-k, j+k]``, and is invisible until ``k`` bars exist to the right.

Refused (lookahead magnets / universe explosion): head-and-shoulders, flags,
triangles, FVGs, order blocks, 40 candlestick names, screenshot vision, and
the price-action-lib kitchen sink. Round-number psychological levels
(``near_round_100``) are skipped: a $100 step is a different game on BTC vs
ETH, and we refuse a silent symbol-dependent rewrite.

No lookahead
------------
Donchian uses bars **before** the decision bar: level = max/min of
``highs[i-N:i]`` / ``lows[i-N:i]`` (Python slice, current bar excluded).
The current bar's high cannot be the Donchian level that is being broken.
A wick through the prior high with close still below is not a break.

Swing pivots require the right-hand ``k`` bars to already exist
(``j + k <= i``). A pivot on the decision bar is not a swing yet.

Near-level tolerance
--------------------
``near_swing_*`` and ``don_lo_*`` (support tag) are true when

    abs(close - level) <= max(NEAR_PCT * close, NEAR_ATR_MULT * ATR(14))

with ``NEAR_PCT = 0.002`` (0.20%) and ``NEAR_ATR_MULT = 0.25``. If ATR is
unavailable the percent band is used alone. ``don_hi_*`` is a strict close
break (``close > prior high``), not a "near" band.

Honesty: these atoms need a high/low series. Passing ``highs=None`` raises
rather than silently using close as high/low (no same-series lie).
"""
from __future__ import annotations

import math

NEAR_PCT = 0.002  # 0.20% of close
NEAR_ATR_MULT = 0.25
NEAR_ATR_PERIOD = 14


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
    trs: list[float] = []
    for j in range(i - period + 1, i + 1):
        h, l, pc = highs[j], lows[j], closes[j - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def near_band(close: float, atr_val: float) -> float:
    """Tolerance in price units: max(0.20% of close, 0.25 × ATR)."""
    band = NEAR_PCT * close
    if not math.isnan(atr_val) and atr_val > 0:
        band = max(band, NEAR_ATR_MULT * atr_val)
    return band


def is_near_level(close: float, level: float, atr_val: float) -> bool:
    return abs(close - level) <= near_band(close, atr_val)


def prior_donchian_high(highs: list[float], n: int, i: int) -> float:
    """Max high of the N bars *before* i. Current bar is not in the window."""
    if n < 1 or i < n:
        return float("nan")
    window = highs[i - n : i]
    return max(window) if window else float("nan")


def prior_donchian_low(lows: list[float], n: int, i: int) -> float:
    """Min low of the N bars *before* i. Current bar is not in the window."""
    if n < 1 or i < n:
        return float("nan")
    window = lows[i - n : i]
    return min(window) if window else float("nan")


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
    if k < 1 or i < 2 * k:
        return None
    for j in range(i - k, k - 1, -1):
        lo, hi = j - k, j + k
        extreme = values[j]
        unique = True
        for t in range(lo, hi + 1):
            if t == j:
                continue
            if want_high:
                if values[t] >= extreme:
                    unique = False
                    break
            else:
                if values[t] <= extreme:
                    unique = False
                    break
        if unique:
            return float(extreme)
    return None


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
