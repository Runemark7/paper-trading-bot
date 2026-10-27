"""DSR against the Bailey & Lopez de Prado 2014 formula, not a copied table."""
from __future__ import annotations

import math
import unittest

from scipy.stats import norm

from hedge_fund.trading.multiple_testing import (
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
)

# Euler-Mascheroni, same constant the paper uses for E[max SR].
_GAMMA = 0.5772156649015328606
_N_OBS = 250
_SR = 0.05
_SKEW = 0.0
_KURT = 3.0


def _estimator_std() -> float:
    inside = 1.0 - _SKEW * _SR + ((_KURT - 1.0) / 4.0) * _SR * _SR
    return math.sqrt(inside / (_N_OBS - 1))


def _sr_star(n_trials: int) -> float:
    if n_trials == 1:
        return 0.0
    std = _estimator_std()
    z1 = float(norm.ppf(1.0 - 1.0 / n_trials))
    z2 = float(norm.ppf(1.0 - 1.0 / (n_trials * math.e)))
    return std * ((1.0 - _GAMMA) * z1 + _GAMMA * z2)


def _psr(n_trials: int) -> float:
    bench = _sr_star(n_trials)
    inside = 1.0 - _SKEW * _SR + ((_KURT - 1.0) / 4.0) * _SR * _SR
    z = (_SR - bench) * math.sqrt(_N_OBS - 1) / math.sqrt(inside)
    return float(norm.cdf(z))


class DeflatedSharpeTests(unittest.TestCase):
    def test_matches_the_paper_formula_and_locked_values(self):
        std = _estimator_std()
        self.assertAlmostEqual(std, 0.063412020448, places=9)
        self.assertAlmostEqual(_sr_star(2), 0.032958736520, places=9)
        self.assertAlmostEqual(_sr_star(100), 0.160470642411, places=9)
        for n_trials, locked in ((2, 0.605934612902), (100, 0.040744699110)):
            got = deflated_sharpe_ratio(
                sharpe=_SR,
                n_obs=_N_OBS,
                n_trials=n_trials,
                skew=_SKEW,
                kurtosis=_KURT,
            )
            self.assertAlmostEqual(got, _psr(n_trials), places=12)
            self.assertAlmostEqual(got, locked, places=9)

    def test_one_trial_has_no_selection_haircut(self):
        self.assertEqual(expected_maximum_sharpe(1, 1.0), 0.0)
        got = deflated_sharpe_ratio(
            sharpe=_SR, n_obs=_N_OBS, n_trials=1, skew=_SKEW, kurtosis=_KURT,
        )
        self.assertAlmostEqual(got, _psr(1), places=12)

    def test_expected_maximum_at_unit_variance(self):
        self.assertAlmostEqual(expected_maximum_sharpe(2, 1.0), 0.519755344281, places=9)
        self.assertAlmostEqual(float(norm.ppf(1.0 - 1.0 / (2.0 * math.e))), 0.900452596638, places=9)


if __name__ == "__main__":
    unittest.main()
