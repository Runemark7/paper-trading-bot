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
    candle_series: list[Candle], symbol: str, timeframe: str
) -> Signal:
    """Compute the condition + a rough directional score for the latest bar."""
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

    # Trend: price vs 20-EMA
    trend_up = price > f.ema_20

    # Condition name — readable, hand-authored buckets
    if trend_up:
        if f.rsi >= 70:
            cond = "uptrend_rsi_high"
        elif f.rsi >= 55:
            cond = "uptrend_rsi_mid"
        else:
            cond = "uptrend_rsi_low"
    else:
        if f.rsi <= 30:
            cond = "downtrend_rsi_low"
        elif f.rsi <= 45:
            cond = "downtrend_rsi_mid"
        else:
            cond = "downtrend_rsi_high"

    # Raw directional score: trend + momentum + RSI positioning, -1..1
    score = 0.0
    score += 1.0 if trend_up else -1.0
    score += 0.5 * math.tanh(f.mom_4h * 20)
    score += 0.5 * math.tanh(f.mom_1d * 10)
    score = max(-1.0, min(1.0, score))

    # We only take longs in this first version (avoid short-side complexity);
    # score only decides WHETHER to enter, not direction.
    direction = "long" if score > 0.15 else "flat"

    return Signal(symbol=symbol, timeframe=timeframe, condition=cond,
                  features=f, direction=direction, raw_score=score)
