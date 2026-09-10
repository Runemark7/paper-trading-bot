"""Dynamic strategy definition, parsing, and execution engine.

Provides a unified AST / rule parser so any strategy name, JSON spec, or composite rule
can be evaluated dynamically across backtests and live execution without hardcoded elif chains.

Close-only atoms (SMA/RSI/mom/dip/…) still evaluate as ``pred(closes, i)``.
Structure atoms (Donchian / swing S&R / double bottom-top) and WaveTrend
atoms also accept ``highs=`` and ``lows=`` from the same bar series — live
klines already have OHLC. Missing highs/lows raises rather than using close
as a high/low proxy (WaveTrend source is HLC3).
"""
from __future__ import annotations

import math
import re
from typing import Callable, Any

# Predicates accept (closes, i=None, highs=None, lows=None). Close-only atoms
# ignore highs/lows; structure / WaveTrend atoms require them.
Predicate = Callable[..., bool]

# ---------------------------------------------------------------------------
# Indicator calculations on a closes array up to index i
# ---------------------------------------------------------------------------

# Per-bar EMA used to recompute 0..i from the SMA seed every call (O(i)).
# Qualification walks thousands of bars; cache the causal series (O(n) once).
_EMA_CACHE_MAX = 48
_ema_series_cache: dict[tuple[int, int, int], list[float]] = {}


def clear_ema_cache() -> None:
    _ema_series_cache.clear()


def _ema_series(closes: list[float], period: int) -> list[float]:
    """SMA-seeded EMA for every bar. Same recurrence as ``ema`` at each i."""
    n = len(closes)
    out = [float("nan")] * n
    if period <= 0 or n < period:
        return out
    k = 2.0 / (period + 1)
    val = sum(closes[:period]) / period
    out[period - 1] = val
    for idx in range(period, n):
        val = closes[idx] * k + val * (1.0 - k)
        out[idx] = val
    return out


def sma(closes: list[float], period: int, i: int | None = None) -> float:
    i = len(closes) - 1 if i is None else i
    if i < period - 1 or period <= 0:
        return float("nan")
    return sum(closes[i - period + 1 : i + 1]) / period


def ema(closes: list[float], period: int, i: int | None = None) -> float:
    i = len(closes) - 1 if i is None else i
    if i < period - 1 or period <= 0:
        return float("nan")
    n = len(closes)
    key = (id(closes), period, n)
    series = _ema_series_cache.get(key)
    if series is None or len(series) != n:
        if len(_ema_series_cache) >= _EMA_CACHE_MAX:
            _ema_series_cache.clear()
        series = _ema_series(closes, period)
        _ema_series_cache[key] = series
    return series[i]


def rsi(closes: list[float], period: int = 14, i: int | None = None) -> float:
    i = len(closes) - 1 if i is None else i
    if i < period:
        return float("nan")
    gains, losses = [], []
    for idx in range(i - period + 1, i + 1):
        diff = closes[idx] - closes[idx - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rolling_ret(closes: list[float], i: int | None = None, lookback: int = 12) -> float:
    i = len(closes) - 1 if i is None else i
    if i < lookback or lookback <= 0:
        return float("nan")
    return closes[i] / closes[i - lookback] - 1.0


def vol_ratio(closes: list[float], i: int | None = None, short_lb: int = 30, long_lb: int = 90) -> float:
    i = len(closes) - 1 if i is None else i
    if i < long_lb:
        return float("nan")
    short_v = [abs(closes[j] - closes[j - 1]) / closes[j - 1] for j in range(max(1, i - short_lb + 1), i + 1)]
    long_v = [abs(closes[j] - closes[j - 1]) / closes[j - 1] for j in range(max(1, i - long_lb + 1), i + 1)]
    if not short_v or not long_v:
        return float("nan")
    return (sum(short_v) / len(short_v)) / (sum(long_v) / len(long_v))


# ---------------------------------------------------------------------------
# Dynamic DSL Expression Evaluator
# ---------------------------------------------------------------------------

def _close_pred(fn: Callable) -> Predicate:
    """Wrap a close-only (c, i) fn so OHLC kwargs are ignored, not TypeError."""

    def pred(c, i=None, highs=None, lows=None, **_kw):
        return fn(c, i)

    return pred


def _all_preds(preds: list[Predicate]) -> Predicate:
    def pred(c, i=None, highs=None, lows=None, **kw):
        return all(p(c, i, highs=highs, lows=lows, **kw) for p in preds)

    return pred


def _any_preds(preds: list[Predicate]) -> Predicate:
    def pred(c, i=None, highs=None, lows=None, **kw):
        return any(p(c, i, highs=highs, lows=lows, **kw) for p in preds)

    return pred


def _adapt_callable(fn: Callable) -> Predicate:
    """Accept either close-only callables or ones that already take highs/lows."""

    def pred(c, i=None, highs=None, lows=None, **kw):
        try:
            return fn(c, i, highs=highs, lows=lows)
        except TypeError:
            return fn(c, i)

    return pred


def eval_predicate(pred, closes, i=None, *, highs=None, lows=None) -> bool:
    """Run a parse_strategy predicate, threading highs/lows when accepted."""
    try:
        return bool(pred(closes, i, highs=highs, lows=lows))
    except TypeError:
        return bool(pred(closes, i))


def parse_strategy(expr: str | Callable | dict) -> Predicate:
    """Parse a strategy expression into ``(closes, i, highs=, lows=) -> bool``.

    Close-only names (``dip_24b_lt1pc``, ``sma_abv_50``, …) still work when
    called as ``pred(closes)`` or ``pred(closes, i)``. Structure and
    WaveTrend names need ``highs``/``lows`` from the same bars.
    """
    if callable(expr):
        return _adapt_callable(expr)

    if isinstance(expr, dict):
        kind = expr.get("kind")
        if kind == "and":
            return _all_preds([parse_strategy(expr["a"]), parse_strategy(expr["b"])])
        if kind == "or":
            return _any_preds([parse_strategy(expr["a"]), parse_strategy(expr["b"])])
        if kind == "sma_stack":
            periods = expr.get("periods", (7, 25, 50))
            return _close_pred(lambda c, i=None: _eval_sma_stack(c, periods, i))
        if kind == "ema_stack":
            periods = expr.get("periods", (7, 25, 50))
            return _close_pred(lambda c, i=None: _eval_ema_stack(c, periods, i))
        if kind == "rsi_range":
            return _close_pred(lambda c, i=None: _eval_rsi_range(c, expr.get("period", 14), expr.get("min", 50), expr.get("max", 100), i))
        if kind == "sma_above":
            return _close_pred(lambda c, i=None: _eval_sma_above(c, expr.get("period", 50), i))
        if kind == "ema_above":
            return _close_pred(lambda c, i=None: _eval_ema_above(c, expr.get("period", 50), i))
        if kind == "mom_gt":
            return _close_pred(lambda c, i=None: _eval_mom_gt(c, expr.get("lookback", 12), expr.get("thr", 0.02), i))
        if kind == "mom_lt":
            return _close_pred(lambda c, i=None: _eval_mom_lt(c, expr.get("lookback", 6), expr.get("thr", -0.02), i))
        if kind == "vol_low":
            return _close_pred(lambda c, i=None: _eval_vol_low(c, expr.get("short", 30), expr.get("long", 90), i))

    if not isinstance(expr, str):
        return _close_pred(lambda c, i=None: False)

    expr_clean = expr.strip()

    # Combinator: AND (&) or OR (|)
    if "&" in expr_clean and not (expr_clean.startswith("(") and expr_clean.endswith(")") and "&" not in expr_clean[1:-1]):
        sub_exprs = [e.strip("() ") for e in expr_clean.split("&")]
        return _all_preds([parse_strategy(sub) for sub in sub_exprs])

    if "|" in expr_clean and not (expr_clean.startswith("(") and expr_clean.endswith(")") and "|" not in expr_clean[1:-1]):
        sub_exprs = [e.strip("() ") for e in expr_clean.split("|")]
        return _any_preds([parse_strategy(sub) for sub in sub_exprs])

    # 1. SMA Stack: sma_stack_7_25_50 or sma_stack (default 7, 25, 50)
    m_sma_stack = re.match(r"^sma_stack(?:_([\d_]+))?$", expr_clean)
    if m_sma_stack:
        periods_str = m_sma_stack.group(1)
        periods = tuple(int(p) for p in periods_str.split("_")) if periods_str else (7, 25, 50)
        return _close_pred(lambda c, i=None: _eval_sma_stack(c, periods, i))

    # 2. EMA Stack: ema_stack_7_25_50
    m_ema_stack = re.match(r"^ema_stack(?:_([\d_]+))?$", expr_clean)
    if m_ema_stack:
        periods_str = m_ema_stack.group(1)
        periods = tuple(int(p) for p in periods_str.split("_")) if periods_str else (7, 25, 50)
        return _close_pred(lambda c, i=None: _eval_ema_stack(c, periods, i))

    # 3. SMA Above: sma_abv_150 or sma100_above or sma_above_200
    m_sma_abv = re.match(r"^sma(?:_abv|_above)?_?(\d+)(?:_abv|_above)?$", expr_clean)
    if m_sma_abv:
        period = int(m_sma_abv.group(1))
        return _close_pred(lambda c, i=None: _eval_sma_above(c, period, i))

    # 4. EMA Above: ema_abv_150 or ema50_above
    m_ema_abv = re.match(r"^ema(?:_abv|_above)?_?(\d+)(?:_abv|_above)?$", expr_clean)
    if m_ema_abv:
        period = int(m_ema_abv.group(1))
        return _close_pred(lambda c, i=None: _eval_ema_above(c, period, i))

    # 5. RSI Range: rsi_30_>57_<90 or rsi_30_57 or rsi_14_>50
    m_rsi_full = re.match(r"^rsi_(\d+)_>_?(\d+)(?:_<_?(\d+))?$", expr_clean)
    if m_rsi_full:
        p, th = int(m_rsi_full.group(1)), int(m_rsi_full.group(2))
        ov = int(m_rsi_full.group(3)) if m_rsi_full.group(3) else 100
        return _close_pred(lambda c, i=None: _eval_rsi_range(c, p, th, ov, i))

    m_rsi_short = re.match(r"^rsi_(\d+)_(\d+)$", expr_clean)
    if m_rsi_short:
        p, th = int(m_rsi_short.group(1)), int(m_rsi_short.group(2))
        return _close_pred(lambda c, i=None: _eval_rsi_range(c, p, th, 100, i))

    if expr_clean == "rsi_momentum":
        return _close_pred(lambda c, i=None: _eval_ema_above(c, 20, i) and _eval_rsi_range(c, 14, 50, 100, i))

    if expr_clean == "robust_open":
        return _close_pred(lambda c, i=None: _eval_sma_stack(c, (7, 25, 50), i) and _eval_rsi_range(c, 20, 55, 100, i))

    # 6. Momentum GT: mom_12b_gt2pc or mom_12_0.02
    m_mom = re.match(r"^mom_(\d+)b?_gt_?(\d+)(?:pc)?$", expr_clean)
    if m_mom:
        lb = int(m_mom.group(1))
        val = int(m_mom.group(2))
        thr = val / 100.0 if val >= 1 else val
        return _close_pred(lambda c, i=None: _eval_mom_gt(c, lb, thr, i))

    # 7. Dip (Momentum LT): dip_6b_lt3pc or dip_6_0.03
    m_dip = re.match(r"^dip_(\d+)b?_lt_?(\d+)(?:pc)?$", expr_clean)
    if m_dip:
        lb = int(m_dip.group(1))
        val = int(m_dip.group(2))
        thr = -(val / 100.0) if val >= 1 else -abs(val)
        return _close_pred(lambda c, i=None: _eval_mom_lt(c, lb, thr, i))

    # 8. Volatility Low Smoothing: vol_lowsm_30_90
    m_vol = re.match(r"^vol_lowsm_(\d+)_(\d+)$", expr_clean)
    if m_vol:
        s_lb, l_lb = int(m_vol.group(1)), int(m_vol.group(2))
        return _close_pred(lambda c, i=None: _eval_vol_low(c, s_lb, l_lb, i))

    # 8b. MFI requires OHLCV volume. Refuses a unit-volume proxy.
    m_mfi = re.match(r"^mfi_(\d+)_(>|<)_?(\d+)$", expr_clean)
    if m_mfi:
        raise ValueError(
            "MFI requires OHLCV volume; refusing a [1.0]*len(closes) proxy"
        )

    # 8c. Bollinger Band Breakouts: bb_lower_20_2 or bb_upper_20_2
    m_bb = re.match(r"^bb_(lower|upper)_(\d+)_?(\d*)$", expr_clean)
    if m_bb:
        side, p, sd = m_bb.group(1), int(m_bb.group(2)), float(m_bb.group(3) or 2.0)
        from hedge_fund.signals.indicators import bollinger_bands
        if side == "lower":
            return _close_pred(lambda c, i=None: (c[len(c)-1 if i is None else i] <= bollinger_bands(c, p, sd, i)[0]))
        else:
            return _close_pred(lambda c, i=None: (c[len(c)-1 if i is None else i] >= bollinger_bands(c, p, sd, i)[2]))

    # 8d. Multi-timeframe wrappers are not implemented: the live cycle fetches
    # a single 5m series. Refusing a silent same-series wrap.
    m_mtf = re.match(r"^(daily|h1|m5|1d|4h|15m)\((.*)\)$", expr_clean)
    if m_mtf:
        tf = m_mtf.group(1)
        raise ValueError(
            f"multi-timeframe prefix {tf}(...) is not supported: live cycle and "
            "parse_strategy evaluate a single series; refusing silent same-series wrap"
        )

    # 8e. OHLC structure atoms (Donchian / fractal swing / double). Need highs+lows.
    m_don = re.match(r"^don_(hi|lo)_(\d+)$", expr_clean)
    if m_don:
        from hedge_fund.signals.structure import don_hi, don_lo

        n = int(m_don.group(2))
        if m_don.group(1) == "hi":
            return lambda c, i=None, highs=None, lows=None, **_k: don_hi(c, highs, lows, n, i)
        return lambda c, i=None, highs=None, lows=None, **_k: don_lo(c, highs, lows, n, i)

    m_swing = re.match(r"^near_swing_(hi|lo)_(\d+)$", expr_clean)
    if m_swing:
        from hedge_fund.signals.structure import near_swing_hi, near_swing_lo

        k = int(m_swing.group(2))
        if m_swing.group(1) == "hi":
            return lambda c, i=None, highs=None, lows=None, **_k: near_swing_hi(c, highs, lows, k, i)
        return lambda c, i=None, highs=None, lows=None, **_k: near_swing_lo(c, highs, lows, k, i)

    m_dbl = re.match(r"^dbl_(bot|top)_(\d+)$", expr_clean)
    if m_dbl:
        from hedge_fund.signals.structure import dbl_bot, dbl_top

        k = int(m_dbl.group(2))
        if m_dbl.group(1) == "bot":
            return lambda c, i=None, highs=None, lows=None, **_k: dbl_bot(c, highs, lows, k, i)
        return lambda c, i=None, highs=None, lows=None, **_k: dbl_top(c, highs, lows, k, i)

    # 8f. LazyBear WaveTrend (HLC3). Frozen 10/21/4, OS=-60. Long-only book:
    # wt_cross_up_os is the green-dot; wt_cross_down_ob parses but is not a
    # universe long. Not Market Cipher.
    if expr_clean == "wt_cross_up_os":
        from hedge_fund.signals.wavetrend import wt_cross_up_os

        return lambda c, i=None, highs=None, lows=None, **_k: wt_cross_up_os(c, highs, lows, i)
    if expr_clean == "wt_below_os":
        from hedge_fund.signals.wavetrend import wt_below_os

        return lambda c, i=None, highs=None, lows=None, **_k: wt_below_os(c, highs, lows, i)
    if expr_clean == "wt_cross_down_ob":
        from hedge_fund.signals.wavetrend import wt_cross_down_ob

        return lambda c, i=None, highs=None, lows=None, **_k: wt_cross_down_ob(c, highs, lows, i)

    # 9. Composite patterns: sma200_rsi50, sma100_mom12_2
    m_sma_rsi = re.match(r"^sma(\d+)_rsi(\d+)$", expr_clean)
    if m_sma_rsi:
        sp, rp = int(m_sma_rsi.group(1)), int(m_sma_rsi.group(2))
        return _close_pred(lambda c, i=None: _eval_sma_above(c, sp, i) and _eval_rsi_range(c, 14, rp, 100, i))

    m_sma_mom = re.match(r"^sma(\d+)_mom(\d+)_(\d+)$", expr_clean)
    if m_sma_mom:
        sp, lb, thr_int = int(m_sma_mom.group(1)), int(m_sma_mom.group(2)), int(m_sma_mom.group(3))
        thr = thr_int / 100.0
        return _close_pred(lambda c, i=None: _eval_sma_above(c, sp, i) and _eval_mom_gt(c, lb, thr, i))

    # Fallback
    return _close_pred(lambda c, i=None: False)


# ---------------------------------------------------------------------------
# Helper evaluation primitives
# ---------------------------------------------------------------------------

def _eval_sma_stack(closes: list[float], periods: tuple[int, ...], i: int | None) -> bool:
    i = len(closes) - 1 if i is None else i
    vals = [sma(closes, p, i) for p in periods]
    if any(math.isnan(v) for v in vals):
        return False
    return closes[i] > vals[0] and all(vals[j] > vals[j + 1] for j in range(len(vals) - 1))


def _eval_ema_stack(closes: list[float], periods: tuple[int, ...], i: int | None) -> bool:
    i = len(closes) - 1 if i is None else i
    vals = [ema(closes, p, i) for p in periods]
    if any(math.isnan(v) for v in vals):
        return False
    return closes[i] > vals[0] and all(vals[j] > vals[j + 1] for j in range(len(vals) - 1))


def _eval_sma_above(closes: list[float], period: int, i: int | None) -> bool:
    i = len(closes) - 1 if i is None else i
    s = sma(closes, period, i)
    return not math.isnan(s) and closes[i] > s


def _eval_ema_above(closes: list[float], period: int, i: int | None) -> bool:
    i = len(closes) - 1 if i is None else i
    e = ema(closes, period, i)
    return not math.isnan(e) and closes[i] > e


def _eval_rsi_range(closes: list[float], period: int, min_val: float, max_val: float, i: int | None) -> bool:
    i = len(closes) - 1 if i is None else i
    r = rsi(closes, period, i)
    if math.isnan(r):
        return False
    return r > min_val and r <= max_val


def _eval_mom_gt(closes: list[float], lookback: int, threshold: float, i: int | None) -> bool:
    i = len(closes) - 1 if i is None else i
    r = rolling_ret(closes, i, lookback)
    return not math.isnan(r) and r > threshold


def _eval_mom_lt(closes: list[float], lookback: int, threshold: float, i: int | None) -> bool:
    i = len(closes) - 1 if i is None else i
    r = rolling_ret(closes, i, lookback)
    return not math.isnan(r) and r < threshold


def _eval_vol_low(closes: list[float], short_lb: int, long_lb: int, i: int | None) -> bool:
    i = len(closes) - 1 if i is None else i
    ratio = vol_ratio(closes, i, short_lb, long_lb)
    return not math.isnan(ratio) and ratio < 1.0
