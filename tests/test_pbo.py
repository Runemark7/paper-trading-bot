"""CSCV PBO against two hand-checkable matrices (Bailey et al. 2017)."""
from __future__ import annotations

import unittest

import numpy as np

from hedge_fund.trading.multiple_testing import probability_of_backtest_overfitting


class PboTests(unittest.TestCase):
    def test_dominating_strategy_has_pbo_zero(self):
        # Column 0 wins every window by enough to keep variance (a constant
        # series is not a Sharpe and must not be the fixture).
        perf = np.array(
            [
                [0.10, -0.02],
                [0.08, 0.01],
                [0.12, -0.03],
                [0.05, 0.00],
            ],
            dtype=np.float64,
        )
        self.assertEqual(probability_of_backtest_overfitting(perf), 0.0)

    def test_two_window_reversal_has_pbo_one(self):
        perf = np.array([[0.05, -0.02], [-0.02, 0.05]], dtype=np.float64)
        self.assertEqual(probability_of_backtest_overfitting(perf), 1.0)

    def test_undefined_when_one_config_or_one_window(self):
        self.assertIsNone(probability_of_backtest_overfitting(np.array([[0.1], [0.2]])))
        self.assertIsNone(probability_of_backtest_overfitting(np.array([[0.1, 0.2]])))


if __name__ == "__main__":
    unittest.main()
