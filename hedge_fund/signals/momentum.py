"""Signal engine — turns OHLCV klines into named signal conditions.

This is the deterministic, reproducible layer. It computes a handful of
cheap technical features on closing prices and buckets the latest bar into a
named condition (e.g. "mom_pos_rsi_mid"). The *condition* is what the
calibration layer keys on: each condition accumulates its own measured
success rate, so the strategy learns which conditions actually predict
the next move.

Deliberately simple and transparent — this is the substrate for the
probability experiment, not a black-box. The conditions are named by
hand so a human can always see what "mom_neg_rsi_low" means.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from hedge_fund.data.binance import Candle


# ---------------------------------------------------------------------------
# Indicator helpers (from a close-price series)
# ---------------------------------------------------------------------------


def ema(values: list[float], period: int) -> float:
    """Exponential moving average of the last value."""
    if not values:
        return 0.0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: list[float], period: int = 14) -> float:
    """Wilder RSI over the given window, or 50 if not enough data."""
    if len(values) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(len(values) - period, len(values)):
        chg = values[i] - values[i - 1]
        if chg >= 0:
            gains += chg
        else:
            losses += -chg
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))


def momentum(values: list[float], lookback: int) -> float:
    """Return over the last `lookback` closes: (last/prev - 1)."""
    if len(values) <= lookback:
        return 0.0
    prev = values[-1 - lookback]
    if prev == 0:
        return 0.0
    return values[-1] / prev - 1.0


def sma(values: list[float], period: int) -> float:
    """Simple moving average over the last `period` closes, or NaN if short."""
    if len(values) < period:
        return float("nan")
    return sum(values[-period:]) / period


# ---------------------------------------------------------------------------
# Feature bucket -> named condition
# ---------------------------------------------------------------------------


@dataclass
class Features:
    """Features computed from a bar series."""

    mom_4h: float = 0.0
    mom_1d: float = 0.0
    rsi: float = 50.0
    ema_20: float = 0.0
    price: float = 0.0


@dataclass
class Signal:
    symbol: str
    timeframe: str
    condition: str
    features: Features = field(default_factory=Features)
    direction: str = "flat"  # "long" | "flat" | "short" (we only go long today)
    raw_score: float = 0.0  # -1..1 rough bull/bear lean


def compute_signal(
    candle_series: list[Candle], symbol: str, timeframe: str, strategy: str = "sma_stack"
) -> Signal:
    """Compute the condition + a rough directional score for the latest bar.

    `strategy` selects the entry rule:
      - "sma_stack": close > SMA(7) > SMA(25) > SMA(50) — the winner of the
        A/B backtest (trend-following). Long when the stack is rising.
      - "rsi_momentum": the original baseline (20-EMA trend + RSI buckets).
        Kept for comparison; NOT the default.
    """
    closes = [c.close for c in candle_series]
    if len(closes) < 2:
        raise ValueError(f"need >=2 bars for {symbol} {timeframe}, got {len(closes)}")
    price = closes[-1]
    f = Features(
        mom_4h=momentum(closes, lookback=4),   # ~ one day
        mom_1d=momentum(closes, lookback=24),  # five 4h days
        rsi=rsi(closes, 14),
        ema_20=ema(closes, 20),
        price=price,
    )

    if strategy == "sma_stack":  # orig name; maps to 7,25,50
        s7 = sma(closes, 7)
        s25 = sma(closes, 25)
        s50 = sma(closes, 50)
        stacked = not any(x != x for x in (s7, s25, s50)) and (price > s7 > s25 > s50)
        take_long = stacked
        cond = "sma_stack_rising" if stacked else "sma_stack_flat"
        if not any(x != x for x in (s7, s25, s50)):
            spread = (s7 / s50 - 1) if s50 else 0
            score = max(-1.0, min(1.0, spread * 60))
        else:
            score = 0.0

    elif strategy == "sma_stack_5_20_50":
        s5 = sma(closes, 5); s20 = sma(closes, 20); s50 = sma(closes, 50)
        stacked = not any(x != x for x in (s5, s20, s50)) and (price > s5 > s20 > s50)
        take_long = stacked
        cond = "sma_stack5_rising" if stacked else "sma_stack5_flat"
        score = max(-1.0, min(1.0, (s5 / s50 - 1) * 60)) if not any(x != x for x in (s5, s20, s50)) else 0.0

    elif strategy == "sma_stack_7_25_50":
        s7 = sma(closes, 7); s25 = sma(closes, 25); s50 = sma(closes, 50)
        stacked = not any(x != x for x in (s7, s25, s50)) and (price > s7 > s25 > s50)
        take_long = stacked
        cond = "sma_stack7_rising" if stacked else "sma_stack7_flat"
        score = max(-1.0, min(1.0, (s7 / s50 - 1) * 60)) if not any(x != x for x in (s7, s25, s50)) else 0.0

    elif strategy.startswith("sma_stack_"):
        # generic sma_stack_<a>_<b>_<c> from the evolution pool (any periods)
        parts = strategy.split("_")
        try:
            periods = tuple(int(p) for p in parts[2:])
        except ValueError:
            periods = (7, 25, 50)
        if len(periods) < 2:
            periods = (7, 25, 50)
        vals = [sma(closes, p) for p in periods]
        stacked = not any(v != v for v in vals) and price > vals[0] and \
            all(vals[j] > vals[j + 1] for j in range(len(vals) - 1))
        take_long = stacked
        cond = "sma_stackgen_rising" if stacked else "sma_stackgen_flat"
        score = max(-1.0, min(1.0, (vals[0] / vals[-1] - 1) * 60)) if not any(v != v for v in vals) else 0.0

    elif strategy == "sma_100_abv":
        s100 = sma(closes, 100)
        above = not (s100 != s100) and price > s100
        take_long = above
        cond = "sma100_above" if above else "sma100_below"
        score = max(-1.0, min(1.0, (price / s100 - 1) * 20)) if not (s100 != s100) else 0.0

    elif strategy == "robust_open":  # stack + RSI>55
        s7 = sma(closes, 7); s25 = sma(closes, 25); s50 = sma(closes, 50)
        stacked = not any(x != x for x in (s7, s25, s50)) and (price > s7 > s25 > s50)
        r = rsi(closes, 20)
        take_long = stacked and r > 55
        cond = "robust_open" if take_long else ("robust_open_cool" if stacked else "robust_open_flat")
        score = (1.0 if stacked else -0.5) + (0.3 if r > 55 else 0)
        score = max(-1.0, min(1.0, score))

    elif strategy == "rsi_trend_50":  # 30-period RSI trending >55
        r = rsi(closes, 30)
        take_long = r > 55 and price > ema(closes, 20)
        cond = "rsi_trend50_high" if r > 55 else "rsi_trend50_low"
        score = max(-1.0, min(1.0, (r - 50) / 20))

    elif strategy.startswith("rsi_"):
        # generic rsi_<period>_<threshold> (e.g. rsi_30_55)
        parts = strategy.split("_")
        try:
            p = int(parts[1]); th = int(parts[2])
        except (ValueError, IndexError):
            p, th = 30, 55
        r = rsi(closes, p)
        take_long = r > th
        cond = f"rsi_{p}_>_{th}" if take_long else f"rsi_{p}_<={th}"
        score = max(-1.0, min(1.0, (r - 50) / 20))

    else:
        # Evaluate against the shared backtest predicate registry if available
        try:
            import hedge_fund.backtest.strategies as bs
            from scripts.sweep import build_pool
            pred_dict = getattr(bs, "_PRED", {})
            if strategy in pred_dict:
                pred = pred_dict[strategy]
            else:
                # search in sweep pool
                pool_map = dict(build_pool())
                pred = pool_map.get(strategy)
            
            if pred:
                i = len(closes) - 1
                take_long = bool(pred(closes, i))
                cond = f"{strategy}_long" if take_long else f"{strategy}_flat"
                score = 0.5 if take_long else -0.5
            else:
                trend_up = price > f.ema_20
                cond = "baseline_flat"
                score = 0.0
                take_long = False
        except Exception:
            trend_up = price > f.ema_20
            cond = "baseline_flat"
            score = 0.0
            take_long = False

    direction = "long" if take_long else "flat"

    return Signal(symbol=symbol, timeframe=timeframe, condition=cond,
                  features=f, direction=direction, raw_score=score)
