"""Combinatorial paper-strategy universe.

The live cycle fetches a single 4h close series (no 1h/5m bars, and
parse_strategy is close-only). This generator therefore does not emit
daily()/h1()/m5() wrappers or MFI rules — those would be silent lies.
"""
from __future__ import annotations

import itertools


def generate_5000_universe() -> list[str]:
    universe = set()

    # 1. Moving Average Stacks (SMA & EMA)
    ma_combos = [
        (3, 8, 21), (4, 9, 18), (5, 10, 20), (5, 13, 34), (5, 20, 50),
        (6, 12, 24), (7, 14, 28), (7, 21, 35), (7, 25, 50), (8, 16, 32),
        (8, 21, 55), (9, 18, 36), (9, 28, 51), (10, 20, 50), (10, 30, 60),
        (10, 50, 100), (12, 24, 48), (12, 26, 60), (13, 34, 89), (14, 28, 56),
        (15, 30, 60), (16, 32, 64), (20, 40, 80), (20, 50, 100), (21, 55, 144),
        (25, 50, 100), (30, 60, 120), (50, 100, 200)
    ]
    for c in ma_combos:
        s_name = "_".join(map(str, c))
        universe.add(f"sma_stack_{s_name}")
        universe.add(f"ema_stack_{s_name}")

    # 2. Single MA Breakouts & Regime Lines
    for ma in [5, 8, 10, 12, 14, 16, 20, 25, 30, 35, 40, 45, 50, 60, 70, 75, 80, 90, 100, 120, 144, 150, 180, 200, 250, 300]:
        universe.add(f"sma_abv_{ma}")
        universe.add(f"ema_abv_{ma}")

    # 3. RSI Bands & Breakouts
    rsi_periods = [5, 7, 9, 10, 12, 14, 18, 21, 25, 28, 30]
    rsi_thresholds = [30, 35, 38, 40, 42, 45, 48, 50, 52, 55, 57, 60, 65]
    rsi_overbought = [70, 75, 80, 85, 90, 95]
    for p in rsi_periods:
        for th in rsi_thresholds:
            universe.add(f"rsi_{p}_>{th}")
            for ov in rsi_overbought:
                if ov > th:
                    universe.add(f"rsi_{p}_>{th}_<{ov}")

    # 4. Momentum Velocity Thrusts
    for lb in [2, 3, 4, 6, 8, 10, 12, 15, 18, 20, 24, 30, 36, 48, 60, 72, 96, 120]:
        for thr in [1, 2, 3, 4, 5, 6, 8, 10]:
            universe.add(f"mom_{lb}b_gt{thr}pc")

    # 5. Mean-Reversion Dip Pullbacks
    for lb in [2, 3, 4, 6, 8, 10, 12, 15, 18, 20, 24, 30, 36, 48, 60, 72, 96, 120]:
        for thr in [1, 2, 3, 4, 5, 6, 8, 10]:
            universe.add(f"dip_{lb}b_lt{thr}pc")

    # 6. Volatility Squeeze
    for s_lb in [10, 15, 20, 25, 30, 40, 50]:
        for l_lb in [45, 60, 75, 90, 120, 150]:
            if l_lb > s_lb * 2:
                universe.add(f"vol_lowsm_{s_lb}_{l_lb}")

    # 6c. Bollinger Band Breakouts (close-only; no volume required)
    for p in [14, 20, 30]:
        universe.add(f"bb_lower_{p}_2")
        universe.add(f"bb_upper_{p}_2")

    # 7. Combinatorial AND pairs: Trend Gate + (Dip / Momentum / RSI / BB)
    trend_gates = [f"sma_abv_{ma}" for ma in [20, 30, 40, 50, 75, 100, 150, 200]] + \
                  [f"ema_abv_{ma}" for ma in [20, 30, 50, 100]] + \
                  [f"sma_stack_{'_'.join(map(str, c))}" for c in [(5, 20, 50), (7, 25, 50), (9, 28, 51), (10, 20, 50), (20, 50, 100)]]

    dip_signals = [f"dip_{lb}b_lt{thr}pc" for lb in [3, 6, 9, 12, 18, 24, 36, 48] for thr in [1, 2, 3, 4, 5, 6]]
    mom_signals = [f"mom_{lb}b_gt{thr}pc" for lb in [3, 6, 12, 18, 24, 36, 48] for thr in [1, 2, 3, 5]]
    rsi_signals = [f"rsi_{p}_>{th}" for p in [7, 10, 14, 21, 28] for th in [40, 45, 50, 55, 60]]
    bb_signals = [f"bb_lower_{p}_2" for p in [14, 20]]

    for t, d in itertools.product(trend_gates, dip_signals):
        universe.add(f"{d}&{t}")

    for t, m in itertools.product(trend_gates, mom_signals):
        universe.add(f"{m}&{t}")

    for t, r in itertools.product(trend_gates, rsi_signals):
        universe.add(f"{r}&{t}")

    for t, b in itertools.product(trend_gates, bb_signals):
        universe.add(f"{b}&{t}")

    rsi_oversold = [f"rsi_{p}_>{th}_<{ov}" for p in [7, 14] for th in [30, 35, 40] for ov in [60, 70]]
    for r, d in itertools.product(rsi_oversold, dip_signals[:30]):
        universe.add(f"{d}&{r}")

    for m1, m2 in itertools.product(mom_signals[:30], mom_signals[30:70]):
        universe.add(f"{m1}&{m2}")

    return sorted(list(universe))
