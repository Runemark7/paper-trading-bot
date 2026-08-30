"""Probability-calibration layer — the core of this project's experiment.

The question this project answers: can an LLM agent (Hermes) state
probabilities that become both *calibrated* (its stated probability equals
its measured frequency) and *profitable* over time?

Design principles
=================

1. **States never override measured frequencies.** The agent may *propose*
   a probability from its reasoning, but the number that "counts" — the one
   logged against every trade and shown on the dashboard — is the calibrated
   estimate derived from the signal condition's own history.

2. **Beta-Binomial online update.** Each signal condition (e.g. "BTC 4h
   momentum > 0 AND RSI in 35-45") keeps a Beta(alpha, beta) posterior over
   its unknown success rate. Each outcome adds a success (price went the
   predicted way) or a failure. The posterior mean is the quoted probability;
   the width (uncertainty) keeps small samples conservative. This is
   *online* — it updates after every trade, no retraining.

3. **Calibration is tracked separately from P&L.** A strategy can have
   accurate probabilities and still lose money (bad timing/sizing), or be
   miscalibrated yet profitable (dumb luck in a bull run). The dashboard
   shows both, independently, so "am I good at predicting?" is never
   conflated with "did the market go up?".

4. **No lookahead.** The posterior used to make a decision contains only
   trades completed *before* that decision. This is enforced by the caller
   (the decision loop records a trade only after its horizon closes).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Signal condition keys
# ---------------------------------------------------------------------------


def condition_key(symbol: str, timeframe: str, condition: str) -> str:
    """A stable identifier for one (symbol, timeframe, condition) cell.

    `condition` is a short human tag like "mom_pos_rsi_mid" — the strategy
    names its own conditions. The key is what the calibration store keys on.
    """
    return f"{symbol}|{timeframe}|{condition}"


# ---------------------------------------------------------------------------
# Calibration state
# ---------------------------------------------------------------------------


@dataclass
class BinState:
    """Beta-Binomial posterior for one condition.

    mean = alpha / (alpha + beta). Uncertainty shrinks as trials grow.
    """

    alpha: float = 1.0
    beta: float = 1.0  # Jeffreys-ish prior: Beta(1,1) = uniform

    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    def variance(self) -> float:
        total = self.alpha + self.beta
        if total <= 2:
            return 0.25  # max variance for uniform
        return (self.alpha * self.beta) / (total**2 * (total + 1))

    def std(self) -> float:
        return math.sqrt(self.variance())

    def trials(self) -> int:
        return int(self.alpha + self.beta - 2)

    def record(self, success: bool, strength: float = 1.0) -> None:
        """Update posterior with one outcome. `strength` adds pseudo-counts."""
        if success:
            self.alpha += strength
        else:
            self.beta += strength


# ---------------------------------------------------------------------------
# Calibration store
# ---------------------------------------------------------------------------

# Cold-start blend cutoff: after this many trials the posterior mean dominates
# and the proposal is ignored (PROTOCOL amendment 2026-08-30).
WARMUP_TRIALS = 20


class CalibrationStore:
    """Persists per-condition Beta posteriors to a JSON file (state dir).

    This is the "learning state" of the bot — the accumulated evidence
    behind every probability it quotes.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._bins: dict[str, BinState] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        self._bins = {
            k: BinState(alpha=v.get("alpha", 1.0), beta=v.get("beta", 1.0))
            for k, v in data.items()
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            k: {"alpha": round(b.alpha, 3), "beta": round(b.beta, 3)}
            for k, b in sorted(self._bins.items())
        }
        self.path.write_text(json.dumps(data, indent=2))

    def state(self, key: str) -> BinState:
        return self._bins.setdefault(key, BinState())

    def calibrated_probability(
        self, key: str, proposed: float | None = None, min_weight: float = 1.0
    ) -> tuple[float, float]:
        """Return (calibrated_prob, uncertainty).

        The calibrated probability is the posterior mean, shrunk toward the
        proposed value only while the sample is too small to trust (cold
        start). As trials grow, the measured frequency dominates and the
        proposal is ignored — the prose can't override the data.

        `min_weight` = equivalent trials below which the proposal still has
        pull. Concretely:
            eff_trials = trials + min_weight
            prob = (posterior_mean * trials + proposed * min_weight) / eff_trials
        """
        b = self.state(key)
        trials = b.trials()
        if proposed is None or trials >= WARMUP_TRIALS:  # past warm-up, data rules
            return b.mean(), b.std()
        blended = (b.mean() * trials + proposed * min_weight) / (trials + min_weight)
        return blended, b.std()

    def record_outcome(self, key: str, success: bool, strength: float = 1.0) -> None:
        self.state(key).record(success, strength)

    def snapshot(self) -> dict[str, dict]:
        return {
            k: {"mean": round(b.mean(), 3), "trials": b.trials(), "std": round(b.std(), 4)}
            for k, b in sorted(self._bins.items())
        }


# ---------------------------------------------------------------------------
# Calibration metrics (measured reliability)
# ---------------------------------------------------------------------------


@dataclass
class CalibrationMetrics:
    brier: float = 0.0
    n: int = 0
    # Bucketed reliability: for stated probabilities in [lo, hi), what fraction
    # actually resolved the predicted way?
    bins: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "brier": round(self.brier, 4),
            "n": self.n,
            "bins": self.bins,
        }


def compute_calibration(records: list[dict]) -> CalibrationMetrics:
    """Compute Brier score + reliability buckets from outcome records.

    Each record: {"prob": float 0..1, "hit": bool}.
    Brier = mean((prob - hit)^2); lower is better (0 = perfectly calibrated).
    """
    if not records:
        return CalibrationMetrics()
    n = len(records)
    brier = sum((r["prob"] - (1.0 if r["hit"] else 0.0)) ** 2 for r in records) / n

    # Reliability buckets of width 0.1
    buckets: list[dict] = []
    for lo in range(0, 100, 10):
        hi = lo + 10
        slice_ = [r for r in records if lo / 100 <= r["prob"] < hi / 100]
        if not slice_:
            continue
        hit_rate = sum(1 for r in slice_ if r["hit"]) / len(slice_)
        avg_prob = sum(r["prob"] for r in slice_) / len(slice_)
        buckets.append(
            {
                "lo": lo / 100,
                "hi": hi / 100,
                "n": len(slice_),
                "avg_prob": round(avg_prob, 3),
                "hit_rate": round(hit_rate, 3),
            }
        )
    return CalibrationMetrics(brier=brier, n=n, bins=buckets)
