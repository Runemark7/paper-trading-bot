"""_sharpe must not emit inf / NaN / astronomical values on tiny variance."""
from __future__ import annotations

import math
import statistics
import unittest

from hedge_fund.backtest.strategies import SHARPE_ABS_CAP, _sharpe, backtest


def _raw_sharpe(pnl_pcts):
    """Un-guarded mean/stdev * sqrt(n) — the formula that blew up in prod."""
    m = statistics.mean(pnl_pcts)
    sd = statistics.stdev(pnl_pcts)
    return m / sd * math.sqrt(len(pnl_pcts))


class SharpeGuardTests(unittest.TestCase):
    def test_short_or_empty_is_zero(self):
        self.assertEqual(_sharpe([]), 0.0)
        self.assertEqual(_sharpe([0.01]), 0.0)

    def test_zero_variance_is_zero(self):
        self.assertEqual(_sharpe([0.01, 0.01, 0.01]), 0.0)
        self.assertEqual(_sharpe([0.0, 0.0]), 0.0)

    def test_tiny_variance_is_zero_not_astronomical(self):
        # Nearly identical trade returns: old formula is finite but insane
        # (prod showed -1.1e15 / -1.9e13 on dbl_bot_168 windows).
        pnl = [-1e-8 + i * 1e-20 for i in range(8)]
        raw = _raw_sharpe(pnl)
        self.assertTrue(math.isfinite(raw))
        self.assertGreater(abs(raw), 1e10)

        got = _sharpe(pnl)
        self.assertEqual(got, 0.0)
        self.assertTrue(math.isfinite(got))
        self.assertLessEqual(abs(got), SHARPE_ABS_CAP)

    def test_non_finite_inputs_are_zero(self):
        self.assertEqual(_sharpe([0.01, float("nan"), 0.02]), 0.0)
        self.assertEqual(_sharpe([0.01, float("inf"), 0.02]), 0.0)
        self.assertEqual(_sharpe([float("-inf"), -0.01, 0.02]), 0.0)

    def test_normal_series_stays_finite_and_signed(self):
        wins = [0.02, 0.03, 0.01, 0.025, 0.015]
        losses = [-0.02, -0.03, -0.01, -0.025, -0.015]
        mixed = [0.01, -0.005, 0.02, -0.01, 0.015]
        self.assertGreater(_sharpe(wins), 0.0)
        self.assertLess(_sharpe(losses), 0.0)
        self.assertNotEqual(_sharpe(mixed), 0.0)
        for series in (wins, losses, mixed):
            got = _sharpe(series)
            self.assertTrue(math.isfinite(got))
            self.assertLessEqual(abs(got), SHARPE_ABS_CAP)
            self.assertAlmostEqual(got, _raw_sharpe(series))

    def test_synthetic_backtest_cannot_emit_huge_sharpe(self):
        # Near-flat grind + always-long: trades share almost the same pnl_pct
        # if any fire. Invariant: backtest never publishes |sharpe| > cap.
        n = 200
        closes = [100.0 + i * 1e-12 for i in range(n)]
        highs = [c + 0.15 for c in closes]
        lows = [c - 0.15 for c in closes]
        r = backtest(closes, highs, lows, lambda *_a, **_k: True)
        self.assertIsNone(r.error)
        self.assertTrue(math.isfinite(r.sharpe))
        self.assertLessEqual(abs(r.sharpe), SHARPE_ABS_CAP)


if __name__ == "__main__":
    unittest.main()
