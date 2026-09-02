"""Frozen live stop/size policy — ``rm_v1``.

This is the TradingLoop + RiskManager rule set used on the paper book
(PROTOCOL amendment 2026-08-30, frozen 2026-09-01). Discovery
qualification must call these helpers rather than a second engine with
different ATR multiples, floors, or fee assumptions.

Live loop uses 5m bars. Confidence in backtests defaults to 1.0 (no calibrated
posterior on the historical tape).
"""
from __future__ import annotations

import math

from hedge_fund.brokers.paper import SLIPPAGE, TAKER_FEE
from hedge_fund.risk.managed import (
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    MAX_DRAWDOWN,
    MAX_OPEN_RISK_FRAC,
    RISK_FRAC,
)

POLICY_ID = "rm_v1"

# Stop / pyramid — identical to hedge_fund.trading.loop (re-exported from here).
TAKE_PROFIT_RR = 2.0
MAX_LOTS_PER_SYMBOL = 3
ATR_PERIOD = 14
ATR_STOP_MULT = 2.0
STOP_FLOOR_FRAC = 0.015  # 1.5% of entry
STOP_CAP_FRAC = 0.040    # 4.0% of entry
STOP_FALLBACK_FRAC = 0.025  # when ATR is unavailable
CONFIDENCE_REF_PROB = 0.50

# Paper fee model (same as PaperBroker).
FEE_TAKER = TAKER_FEE  # 0.1%
FEE_SLIPPAGE = SLIPPAGE  # 2 bps


def atr_stop_distance(entry: float, atr_value: float) -> float:
    """Distance from entry to stop under rm_v1 (ATR × 2, floored 1.5%, capped 4%)."""
    if atr_value is None or (isinstance(atr_value, float) and (math.isnan(atr_value) or atr_value <= 0)):
        return entry * STOP_FALLBACK_FRAC
    return max(entry * STOP_FLOOR_FRAC, min(entry * STOP_CAP_FRAC, ATR_STOP_MULT * atr_value))


def atr_stop_price(entry: float, atr_value: float) -> float:
    """Stop price for a long under rm_v1."""
    return entry - atr_stop_distance(entry, atr_value)


def confidence_multiplier(probability: float, ref: float = CONFIDENCE_REF_PROB) -> float:
    """Live-loop clamp: stated p / 0.50, bounded [0.5×, 2.0×]."""
    if ref <= 0:
        return 1.0
    return max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, float(probability) / ref))


__all__ = [
    "POLICY_ID",
    "TAKE_PROFIT_RR",
    "MAX_LOTS_PER_SYMBOL",
    "ATR_PERIOD",
    "ATR_STOP_MULT",
    "STOP_FLOOR_FRAC",
    "STOP_CAP_FRAC",
    "STOP_FALLBACK_FRAC",
    "CONFIDENCE_REF_PROB",
    "FEE_TAKER",
    "FEE_SLIPPAGE",
    "RISK_FRAC",
    "MAX_OPEN_RISK_FRAC",
    "MAX_DRAWDOWN",
    "CONFIDENCE_MIN",
    "CONFIDENCE_MAX",
    "atr_stop_distance",
    "atr_stop_price",
    "confidence_multiplier",
]
