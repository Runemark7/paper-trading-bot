"""Dynamic strategy definition, parsing, and execution engine.

Provides a unified AST / rule parser so any strategy name, JSON spec, or composite rule
can be evaluated dynamically across backtests and live execution without hardcoded elif chains.
"""
from __future__ import annotations

import math
import re
from typing import Callable, Any

# ---------------------------------------------------------------------------
# Indicator calculations on a closes array up to index i
# ---------------------------------------------------------------------------

def sma(closes: list[float], period: int, i: int | None = None) -> float:
    i = len(closes) - 1 if i is None else i
    if i < period - 1 or period <= 0:
        return float("nan")
    return sum(closes[i - period + 1 : i + 1]) / period


def ema(closes: list[float], period: int, i: int | None = None) -> float:
    i = len(closes) - 1 if i is None else i
    if i < period - 1 or period <= 0:
        return float("nan")
    k = 2.0 / (period + 1)
    val = sum(closes[:period]) / period
    for idx in range(period, i + 1):
        val = closes[idx] * k + val * (1.0 - k)
    return val


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

def parse_strategy(expr: str | Callable | dict) -> Callable[[list[float], int | None], bool]:
    """Dynamically parses any strategy expression into a predicate function: (closes, i) -> bool."""
    if callable(expr):
        return expr

    if isinstance(expr, dict):
        kind = expr.get("kind")
        if kind == "and":
            p1 = parse_strategy(expr["a"])
            p2 = parse_strategy(expr["b"])
            return lambda c, i=None: p1(c, i) and p2(c, i)
        if kind == "or":
            p1 = parse_strategy(expr["a"])
            p2 = parse_strategy(expr["b"])
            return lambda c, i=None: p1(c, i) or p2(c, i)
        if kind == "sma_stack":
            periods = expr.get("periods", (7, 25, 50))
            return lambda c, i=None: _eval_sma_stack(c, periods, i)
        if kind == "ema_stack":
            periods = expr.get("periods", (7, 25, 50))
            return lambda c, i=None: _eval_ema_stack(c, periods, i)
        if kind == "rsi_range":
            return lambda c, i=None: _eval_rsi_range(c, expr.get("period", 14), expr.get("min", 50), expr.get("max", 100), i)
        if kind == "sma_above":
            return lambda c, i=None: _eval_sma_above(c, expr.get("period", 50), i)
        if kind == "ema_above":
            return lambda c, i=None: _eval_ema_above(c, expr.get("period", 50), i)
        if kind == "mom_gt":
            return lambda c, i=None: _eval_mom_gt(c, expr.get("lookback", 12), expr.get("thr", 0.02), i)
        if kind == "mom_lt":
            return lambda c, i=None: _eval_mom_lt(c, expr.get("lookback", 6), expr.get("thr", -0.02), i)
        if kind == "vol_low":
            return lambda c, i=None: _eval_vol_low(c, expr.get("short", 30), expr.get("long", 90), i)

    if not isinstance(expr, str):
        return lambda c, i=None: False

    expr_clean = expr.strip()

    # Combinator: AND (&) or OR (|)
    if "&" in expr_clean and not (expr_clean.startswith("(") and expr_clean.endswith(")") and "&" not in expr_clean[1:-1]):
        sub_exprs = [e.strip("() ") for e in expr_clean.split("&")]
        preds = [parse_strategy(sub) for sub in sub_exprs]
        return lambda c, i=None: all(p(c, i) for p in preds)

    if "|" in expr_clean and not (expr_clean.startswith("(") and expr_clean.endswith(")") and "|" not in expr_clean[1:-1]):
        sub_exprs = [e.strip("() ") for e in expr_clean.split("|")]
        preds = [parse_strategy(sub) for sub in sub_exprs]
        return lambda c, i=None: any(p(c, i) for p in preds)

    # 1. SMA Stack: sma_stack_7_25_50 or sma_stack (default 7, 25, 50)
    m_sma_stack = re.match(r"^sma_stack(?:_([\d_]+))?$", expr_clean)
    if m_sma_stack:
        periods_str = m_sma_stack.group(1)
        periods = tuple(int(p) for p in periods_str.split("_")) if periods_str else (7, 25, 50)
        return lambda c, i=None: _eval_sma_stack(c, periods, i)

    # 2. EMA Stack: ema_stack_7_25_50
    m_ema_stack = re.match(r"^ema_stack(?:_([\d_]+))?$", expr_clean)
    if m_ema_stack:
        periods_str = m_ema_stack.group(1)
        periods = tuple(int(p) for p in periods_str.split("_")) if periods_str else (7, 25, 50)
        return lambda c, i=None: _eval_ema_stack(c, periods, i)

    # 3. SMA Above: sma_abv_150 or sma100_above or sma_above_200
    m_sma_abv = re.match(r"^sma(?:_abv|_above)?_?(\d+)(?:_abv|_above)?$", expr_clean)
    if m_sma_abv:
        period = int(m_sma_abv.group(1))
        return lambda c, i=None: _eval_sma_above(c, period, i)

    # 4. EMA Above: ema_abv_150 or ema50_above
    m_ema_abv = re.match(r"^ema(?:_abv|_above)?_?(\d+)(?:_abv|_above)?$", expr_clean)
    if m_ema_abv:
        period = int(m_ema_abv.group(1))
        return lambda c, i=None: _eval_ema_above(c, period, i)

    # 5. RSI Range: rsi_30_>57_<90 or rsi_30_57 or rsi_14_>50
    m_rsi_full = re.match(r"^rsi_(\d+)_>_?(\d+)(?:_<_?(\d+))?$", expr_clean)
    if m_rsi_full:
        p, th = int(m_rsi_full.group(1)), int(m_rsi_full.group(2))
        ov = int(m_rsi_full.group(3)) if m_rsi_full.group(3) else 100
        return lambda c, i=None: _eval_rsi_range(c, p, th, ov, i)

    m_rsi_short = re.match(r"^rsi_(\d+)_(\d+)$", expr_clean)
    if m_rsi_short:
        p, th = int(m_rsi_short.group(1)), int(m_rsi_short.group(2))
        return lambda c, i=None: _eval_rsi_range(c, p, th, 100, i)

    if expr_clean == "rsi_momentum":
        return lambda c, i=None: _eval_ema_above(c, 20, i) and _eval_rsi_range(c, 14, 50, 100, i)

    if expr_clean == "robust_open":
        return lambda c, i=None: _eval_sma_stack(c, (7, 25, 50), i) and _eval_rsi_range(c, 20, 55, 100, i)

    # 6. Momentum GT: mom_12b_gt2pc or mom_12_0.02
    m_mom = re.match(r"^mom_(\d+)b?_gt_?(\d+)(?:pc)?$", expr_clean)
    if m_mom:
        lb = int(m_mom.group(1))
        val = int(m_mom.group(2))
        thr = val / 100.0 if val >= 1 else val
        return lambda c, i=None: _eval_mom_gt(c, lb, thr, i)

    # 7. Dip (Momentum LT): dip_6b_lt3pc or dip_6_0.03
    m_dip = re.match(r"^dip_(\d+)b?_lt_?(\d+)(?:pc)?$", expr_clean)
    if m_dip:
        lb = int(m_dip.group(1))
        val = int(m_dip.group(2))
        thr = -(val / 100.0) if val >= 1 else -abs(val)
        return lambda c, i=None: _eval_mom_lt(c, lb, thr, i)

    # 8. Volatility Low Smoothing: vol_lowsm_30_90
    m_vol = re.match(r"^vol_lowsm_(\d+)_(\d+)$", expr_clean)
    if m_vol:
        s_lb, l_lb = int(m_vol.group(1)), int(m_vol.group(2))
        return lambda c, i=None: _eval_vol_low(c, s_lb, l_lb, i)

    # 8b. MFI & Flow Indicators: mfi_14_<25 or mfi_14_>50
    m_mfi = re.match(r"^mfi_(\d+)_(>|<)_?(\d+)$", expr_clean)
    if m_mfi:
        p, op, th = int(m_mfi.group(1)), m_mfi.group(2), int(m_mfi.group(3))
        from hedge_fund.signals.indicators import mfi
        # Proxy volume if only close series provided
        return lambda c, i=None: (
            (mfi(c, c, c, [1.0]*len(c), p, i) > th) if op == ">"
            else (mfi(c, c, c, [1.0]*len(c), p, i) < th)
        )

    # 8c. Bollinger Band Breakouts: bb_lower_20_2 or bb_upper_20_2
    m_bb = re.match(r"^bb_(lower|upper)_(\d+)_?(\d*)$", expr_clean)
    if m_bb:
        side, p, sd = m_bb.group(1), int(m_bb.group(2)), float(m_bb.group(3) or 2.0)
        from hedge_fund.signals.indicators import bollinger_bands
        if side == "lower":
            return lambda c, i=None: (c[len(c)-1 if i is None else i] <= bollinger_bands(c, p, sd, i)[0])
        else:
            return lambda c, i=None: (c[len(c)-1 if i is None else i] >= bollinger_bands(c, p, sd, i)[2])

    # 8d. Multi-Timeframe Wrappers: daily(expr), h1(expr), m5(expr)
    m_mtf = re.match(r"^(daily|h1|m5|1d|4h|15m)\((.*)\)$", expr_clean)
    if m_mtf:
        tf, inner_expr = m_mtf.group(1), m_mtf.group(2)
        inner_pred = parse_strategy(inner_expr)
        # In a single series evaluation, step downsample or evaluate inner rule
        return lambda c, i=None: inner_pred(c, i)

    # 9. Composite patterns: sma200_rsi50, sma100_mom12_2
    m_sma_rsi = re.match(r"^sma(\d+)_rsi(\d+)$", expr_clean)
    if m_sma_rsi:
        sp, rp = int(m_sma_rsi.group(1)), int(m_sma_rsi.group(2))
        return lambda c, i=None: _eval_sma_above(c, sp, i) and _eval_rsi_range(c, 14, rp, 100, i)

    m_sma_mom = re.match(r"^sma(\d+)_mom(\d+)_(\d+)$", expr_clean)
    if m_sma_mom:
        sp, lb, thr_int = int(m_sma_mom.group(1)), int(m_sma_mom.group(2)), int(m_sma_mom.group(3))
        thr = thr_int / 100.0
        return lambda c, i=None: _eval_sma_above(c, sp, i) and _eval_mom_gt(c, lb, thr, i)

    # Fallback
    return lambda c, i=None: False


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
