"""How many strategies have actually been backtested.

The deflated Sharpe ratio's N is that count, plus the candidate under
test (it is one more trial). Ops parks that never produced a Sharpe are
not return trials and are reported separately. They are not added to N:
a lookback cap is not a Sharpe estimate, and counting it would invent
trials the formula does not describe.
"""
from __future__ import annotations

import math
import statistics

from hedge_fund.trading.tested_index import load_tested_metrics


def _sharpe(item: object) -> float | None:
    if not isinstance(item, dict) or item.get("ops_park"):
        return None
    value = item.get("sharpe")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def trial_census() -> dict:
    """Trial count and the cross-sectional stdev of stored trade Sharpes.

    ``trade_sharpe_std`` is in trade-count Sharpe units. It is recorded
    for the audit. The DSR itself uses the variance of the daily-return
    Sharpe estimator (same units as the Sharpe being deflated), not this
    cross-section — the stored Sharpes are a different statistic.
    """
    metrics = load_tested_metrics()
    n_ops = 0
    sharpes: list[float] = []
    for item in metrics.values():
        if isinstance(item, dict) and item.get("ops_park"):
            n_ops += 1
            continue
        number = _sharpe(item)
        if number is not None:
            sharpes.append(number)
    n_backtested = len(sharpes)
    std = statistics.stdev(sharpes) if len(sharpes) >= 2 else None
    return {
        "n_metrics": len(metrics),
        "n_ops_park": n_ops,
        "n_backtested": n_backtested,
        # The name being evaluated is one more trial than the index.
        # A retest of a name already in ``n_backtested`` uses that count
        # and does not add one. See ``n_trials_for_retest``.
        "n_trials": n_backtested + 1,
        "trade_sharpe_std": None if std is None else round(float(std), 6),
    }


def n_trials_for_retest(census: object) -> int | None:
    """Trial count for a name that is already in the census.

    ``trial_census()["n_trials"]`` is ``n_backtested + 1`` because a fresh
    candidate is not in the index yet. A retest of a stored name is already
    inside ``n_backtested``. Using the fresh count would count it twice.
    ``None`` when the census has no backtested names: DSR then fails closed
    instead of inventing N=1.
    """
    if not isinstance(census, dict):
        return None
    raw = census.get("n_backtested")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    if raw < 1:
        return None
    return raw
