"""Deflated Sharpe ratio and CSCV probability of backtest overfitting.

Bailey and Lopez de Prado, "The Deflated Sharpe Ratio: Correcting for
Selection Bias, Backtest Overfitting, and Non-Normality", Journal of
Portfolio Management (2014).

Bailey, Borwein, Lopez de Prado, and Zhu, "The Probability of Backtest
Overfitting", Journal of Computational Finance (2017).

Sharpe ratios here are per-observation (not annualized): mean / sample
standard deviation of the return series the gate actually scored.
Mixing an annualized Sharpe into these formulas is a unit error.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

# Euler-Mascheroni constant. Bailey & Lopez de Prado (2014) eq. for E[max].
EULER_MASCHERONI = 0.5772156649015328606


def return_moments(returns: list[float]) -> dict | None:
    """Per-observation Sharpe plus skew and raw kurtosis (normal = 3).

    Moments use the biased divisor ``n`` (the estimator in the DSR
    derivation). ``None`` when fewer than two finite returns exist or the
    series has no variance.
    """
    vals = [float(x) for x in returns if x is not None and math.isfinite(float(x))]
    n = len(vals)
    if n < 2:
        return None
    mean = sum(vals) / n
    m2 = sum((x - mean) ** 2 for x in vals) / n
    if m2 <= 0.0 or not math.isfinite(m2):
        return None
    std = math.sqrt(m2)
    m3 = sum((x - mean) ** 3 for x in vals) / n
    m4 = sum((x - mean) ** 4 for x in vals) / n
    sharpe = mean / std
    # Sample stdev (ddof=1) is what a reported Sharpe usually divides by.
    # The DSR standard error uses that same Sharpe, so recompute it with
    # the unbiased stdev. Skew/kurt stay on the raw moments above.
    var_u = sum((x - mean) ** 2 for x in vals) / (n - 1)
    if var_u <= 0.0:
        return None
    sharpe = mean / math.sqrt(var_u)
    return {
        "n": n,
        "mean": mean,
        "std": math.sqrt(var_u),
        "sharpe": sharpe,
        "skew": m3 / (m2 ** 1.5),
        "kurtosis": m4 / (m2 ** 2),
    }


def sharpe_estimator_std(sharpe: float, n_obs: int, skew: float, kurtosis: float) -> float:
    """Stdev of the Sharpe estimator. Bailey & Lopez de Prado (2014).

    ``kurtosis`` is the raw 4th standardized moment (Gaussian = 3), not
    the excess kurtosis.
    """
    if n_obs < 2:
        return 0.0
    inside = 1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe
    if inside <= 0.0 or not math.isfinite(inside):
        return 0.0
    return math.sqrt(inside / (n_obs - 1))


def expected_maximum_sharpe(n_trials: int, sharpe_std: float) -> float:
    """SR* — expected maximum Sharpe under the null given ``n_trials``.

    ``n_trials == 1`` has no selection bias, so the benchmark is 0.
    ``sharpe_std`` is the cross-sectional (or estimator) stdev of the
    Sharpe, in the same units as the observed Sharpe.
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if sharpe_std < 0 or not math.isfinite(sharpe_std):
        raise ValueError("sharpe_std must be a non-negative finite number")
    if n_trials == 1 or sharpe_std == 0.0:
        return 0.0
    z1 = float(norm.ppf(1.0 - 1.0 / n_trials))
    z2 = float(norm.ppf(1.0 - 1.0 / (n_trials * math.e)))
    return float(
        sharpe_std * ((1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2)
    )


def probabilistic_sharpe_ratio(
    sharpe: float,
    sr_benchmark: float,
    n_obs: int,
    skew: float,
    kurtosis: float,
) -> float:
    """PSR(SR*) = Φ( (SR - SR*) √(n-1) / √(1 - γ3 SR + (γ4-1)/4 SR²) )."""
    if n_obs < 2:
        return 0.0
    inside = 1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe
    if inside <= 0.0 or not math.isfinite(inside):
        return 0.0
    z = (sharpe - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(inside)
    if not math.isfinite(z):
        return 0.0
    return float(norm.cdf(z))


def deflated_sharpe_ratio(
    *,
    sharpe: float,
    n_obs: int,
    n_trials: int,
    skew: float,
    kurtosis: float,
    sharpe_std: float | None = None,
) -> float:
    """Probability the true Sharpe exceeds the multiple-testing haircut.

    When ``sharpe_std`` is omitted, the variance of the Sharpe estimator
    is taken from this series (Bailey & Lopez de Prado 2014), not from a
    cross-section of a different Sharpe definition.
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    std = (
        sharpe_estimator_std(sharpe, n_obs, skew, kurtosis)
        if sharpe_std is None
        else float(sharpe_std)
    )
    benchmark = expected_maximum_sharpe(int(n_trials), std)
    return probabilistic_sharpe_ratio(sharpe, benchmark, n_obs, skew, kurtosis)


def probability_of_backtest_overfitting(perf: np.ndarray) -> float | None:
    """CSCV PBO. ``perf`` is shape (T, N): window performance × configurations.

    T is split into S = T (or T-1 when T is odd) contiguous rows, one block
    per row. Every combination of S/2 blocks is in-sample; the complement
    is out-of-sample. The in-sample winner's OOS relative rank is
    ``rank / (N + 1)`` with rank 1 = worst (stable argsort, lowest column
    index wins an in-sample tie). PBO is the fraction of splits with
    relative rank <= 0.5 (logit <= 0), i.e. the selected config lands at
    or below the OOS median.

    ``None`` when N < 2 or S < 2 — PBO is undefined, and the gate treats
    that as a fail rather than a pass.
    """
    arr = np.asarray(perf, dtype=np.float64)
    if arr.ndim != 2:
        return None
    t_rows, n_cols = arr.shape
    if n_cols < 2 or t_rows < 2:
        return None
    s = t_rows if t_rows % 2 == 0 else t_rows - 1
    if s < 2:
        return None
    block = arr[:s]
    k = s // 2
    # All C(S, S/2) index rows. Symmetry double-counts each partition and
    # its swap; both directions are valid CSCV splits (Bailey et al. 2017).
    combos = _combinations(s, k)
    if combos.size == 0:
        return None
    is_block = block[combos]  # (C, K, N)
    is_sum = is_block.sum(axis=1)
    is_sumsq = np.square(is_block).sum(axis=1)
    total_sum = block.sum(axis=0)
    total_sumsq = np.square(block).sum(axis=0)
    oos_k = s - k
    oos_sum = total_sum - is_sum
    oos_sumsq = total_sumsq - is_sumsq
    is_sh = _sharpe_from_sums(is_sum, is_sumsq, k)
    oos_sh = _sharpe_from_sums(oos_sum, oos_sumsq, oos_k)
    # Lowest index wins a tie (np.argmax).
    best = np.argmax(is_sh, axis=1)
    # Rank 1 = worst. mergesort is stable, so equal OOS Sharpes keep column order.
    order = np.argsort(oos_sh, axis=1, kind="mergesort")
    # position of `best` in `order`
    n = n_cols
    rows = np.arange(order.shape[0])
    # where order == best
    match = order == best[:, None]
    rank = match.argmax(axis=1) + 1  # 1-based; argmax finds the first True
    relative = rank / (n + 1.0)
    return float(np.mean(relative <= 0.5))


def _sharpe_from_sums(sum_: np.ndarray, sumsq: np.ndarray, count: int) -> np.ndarray:
    """Sample Sharpe from sum and sum of squares. Shape preserved."""
    count_f = float(count)
    mean = sum_ / count_f
    if count < 2:
        return np.where(mean > 1e-15, 1e6, np.where(mean < -1e-15, -1e6, 0.0))
    # population second moment, then Bessel's correction
    var_pop = sumsq / count_f - np.square(mean)
    var = var_pop * (count_f / (count_f - 1.0))
    var = np.maximum(var, 0.0)
    std = np.sqrt(var)
    out = np.zeros(mean.shape, dtype=np.float64)
    tiny = std <= 1e-12
    out = np.where(tiny & (mean > 1e-15), 1e6, out)
    out = np.where(tiny & (mean < -1e-15), -1e6, out)
    ok = ~tiny
    out = np.where(ok, mean / np.maximum(std, 1e-12), out)
    return out


def _combinations(n: int, k: int) -> np.ndarray:
    """Integer combinations C(n, k) as an array of shape (C, k)."""
    from itertools import combinations

    rows = list(combinations(range(n), k))
    if not rows:
        return np.zeros((0, k), dtype=np.int64)
    return np.asarray(rows, dtype=np.int64)
