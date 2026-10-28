"""Structure atoms stay bit-identical and no longer scan every bar."""
from __future__ import annotations

import random
import time
import unittest

from hedge_fund.backtest.strategies import backtest
from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.signals.structure import (
    clear_structure_cache,
    confirmed_swings,
    dbl_bot,
    near_swing_lo,
)


def _reference_swings(values, k, i, want_high, limit=None):
    """The pre-cache scan. Newest first, strict unique extreme, no lookahead."""
    out = []
    if k < 1 or i < 2 * k:
        return out
    for j in range(i - k, k - 1, -1):
        extreme = values[j]
        unique = True
        for t in range(j - k, j + k + 1):
            if t == j:
                continue
            if want_high:
                if values[t] >= extreme:
                    unique = False
                    break
            elif values[t] <= extreme:
                unique = False
                break
        if unique:
            out.append((j, float(extreme)))
            if limit is not None and len(out) >= limit:
                break
    return out


def _tape(n: int, seed: int):
    rng = random.Random(seed)
    price = 100.0
    closes = []
    for _ in range(n):
        price *= 1.0 + (rng.random() - 0.48) * 0.004
        closes.append(price)
    highs = [c * (1.0 + rng.random() * 0.002) for c in closes]
    lows = [c * (1.0 - rng.random() * 0.002) for c in closes]
    return closes, highs, lows


def _pack(result):
    return (
        result.trades,
        round(result.total_pnl, 8),
        round(result.sharpe, 8),
        round(result.fees_paid, 8),
        round(result.max_drawdown, 8),
        result.wins,
    )


class SwingParityTests(unittest.TestCase):
    def test_pivots_match_the_scan_including_ties(self):
        rng = random.Random(7)
        for n, k in ((120, 1), (400, 12), (700, 30), (500, 60), (360, 90)):
            values = [100.0 + rng.random() * 10 for _ in range(n)]
            values[n // 2] = values[n // 2 - 1]
            clear_structure_cache()
            for i in range(0, n, 11):
                for want_high in (False, True):
                    for limit in (None, 1, 2):
                        self.assertEqual(
                            confirmed_swings(values, k, i, want_high, limit),
                            _reference_swings(values, k, i, want_high, limit),
                            f"n={n} k={k} i={i} high={want_high} limit={limit}",
                        )

    def test_timeout_names_match_the_scan_and_finish_quickly(self):
        names = (
            "near_swing_lo_90&sma_stack_30_50_200",
            "dbl_bot_60&h1_sma_abv_120&mom_30b_gt2pc",
            "don_hi_48&near_swing_hi_24",
        )
        closes, highs, lows = _tape(1400, seed=11)
        import hedge_fund.signals.structure as structure

        fast = []
        for name in names:
            clear_structure_cache()
            fast.append(backtest(closes, highs, lows, name))
        original = structure.confirmed_swings
        structure.confirmed_swings = _reference_swings
        try:
            slow = [backtest(closes, highs, lows, name) for name in names]
        finally:
            structure.confirmed_swings = original
        self.assertEqual([_pack(r) for r in fast], [_pack(r) for r in slow])

        # One discovery-sized window. The old scan on this length is the
        # multi-minute path; the cached series has to stay interactive.
        closes, highs, lows = _tape(20000, seed=12)
        clear_structure_cache()
        started = time.perf_counter()
        hits = sum(1 for i in range(len(closes)) if near_swing_lo(closes, highs, lows, 90, i))
        swing_s = time.perf_counter() - started
        started = time.perf_counter()
        bots = sum(1 for i in range(len(closes)) if dbl_bot(closes, highs, lows, 60, i))
        bot_s = time.perf_counter() - started
        print(
            f"TIMING near_swing_lo_90 n=20000 {swing_s*1000:.1f}ms hits={hits} "
            f"dbl_bot_60 {bot_s*1000:.1f}ms hits={bots}"
        )
        self.assertLess(swing_s, 2.0)
        self.assertLess(bot_s, 2.0)
        # A second name on the same tape reuses the pivot list.
        started = time.perf_counter()
        pred = parse_strategy("near_swing_lo_90")
        for i in range(len(closes)):
            pred(closes, i, highs=highs, lows=lows)
        cached_s = time.perf_counter() - started
        print(f"TIMING cached near_swing_lo_90 {cached_s*1000:.1f}ms")
        self.assertLess(cached_s, 2.0)


class StructureCacheIdentityTests(unittest.TestCase):
    def test_atr_matches_fresh_window_sum_bitwise(self):
        from hedge_fund.signals.structure import atr_at

        closes, highs, lows = _tape(3000, seed=3)
        clear_structure_cache()
        for i in range(14, len(closes), 7):
            trs = []
            for j in range(i - 13, i + 1):
                h, l, pc = highs[j], lows[j], closes[j - 1]
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
            self.assertEqual(atr_at(highs, lows, closes, 14, i), sum(trs) / len(trs))

    def test_new_same_length_tape_never_reuses_series(self):
        """live_tape rebuilds same-length lists each tick; freed ids get reused."""
        from hedge_fund.signals.structure import atr_at, prior_donchian_high

        clear_structure_cache()
        for seed in range(40):
            closes, highs, lows = _tape(300, seed=100 + seed)
            i = 250
            trs = []
            for j in range(i - 13, i + 1):
                h, l, pc = highs[j], lows[j], closes[j - 1]
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
            self.assertEqual(atr_at(highs, lows, closes, 14, i), sum(trs) / len(trs))
            self.assertEqual(prior_donchian_high(highs, 48, i), max(highs[i - 48 : i]))
            self.assertEqual(
                confirmed_swings(lows, 12, i, False, 2), _reference_swings(lows, 12, i, False, 2)
            )
            del closes, highs, lows


if __name__ == "__main__":
    unittest.main()
