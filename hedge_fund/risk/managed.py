"""Risk rules — hard, deterministic, outside the LLM's reach.

The decision loop (LLM) proposes trades; this module is the gate. The rules
below are *not* suggestions the agent can talk its way around — they are
enforced before any order reaches the broker, and a breach of the drawdown
breaker halts trading entirely.

Rules
=====
- **1 % fixed-fractional risk per trade**: the amount of equity you're willing
  to lose on a single trade (entry to stop). Determines position size:
      risk_per_trade = equity * risk_frac
      size = risk_per_trade / (entry - stop)
- **5 % max open risk**: the sum of (entry - stop) * size across open positions
  may not exceed this fraction of equity. Prevents stacking correlated bets.
- **15 % drawdown circuit breaker**: if equity falls 15 % from the running
  peak, trading is halted (positions may still be closed) until the account
  is manually re-armed. A paused loser is data, not a secret.

Stops are required on every long position; a missing stop is a hard rejection.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_REASON_LEN = 120


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    size: float = 0.0
    stop: float = 0.0


class RiskManager:
    def __init__(
        self,
        risk_frac: float = 0.01,      # 1 % risked per trade
        max_open_risk_frac: float = 0.05,  # 5 % max open risk
        max_drawdown: float = 0.15,   # 15 % halt
        initial_equity: float = 10_000.0,
    ) -> None:
        self.risk_frac = risk_frac
        self.max_open_risk_frac = max_open_risk_frac
        self.max_drawdown = max_drawdown
        self.peak_equity = initial_equity
        self.halted = False
        self.halt_reason: str | None = None

    def update_equity(self, equity: float) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity
        if self.peak_equity > 0:
            dd = (self.peak_equity - equity) / self.peak_equity
            if dd >= self.max_drawdown:
                self.halted = True
                self.halt_reason = (
                    f"drawdown {dd:.1%} >= {self.max_drawdown:.0%} "
                    f"(peak {self.peak_equity:.0f} -> {equity:.0f})"
                )

    def is_halted(self) -> bool:
        return self.halted

    def open_risk(self, positions: list[tuple[float, float, float]]) -> float:
        """sum of (entry - stop) * size across positions, in cash terms."""
        return max(0.0, sum((entry - stop) * size for entry, stop, size in positions))

    def size_position(
        self,
        equity: float,
        entry: float,
        stop: float,
        open_positions: list[tuple[float, float, float]] | None = None,
    ) -> RiskDecision:
        """Compute the position size for a proposed long.

        open_positions: list of (entry_price, stop_loss, quantity) for risks
        already on. Returns a RiskDecision.
        """
        if self.halted:
            return RiskDecision(False, self.halt_reason or "trading halted")

        if stop <= 0 or entry <= stop:
            return RiskDecision(False, "stop must be below entry for a long")

        risk_cash = equity * self.risk_frac
        size = risk_cash / (entry - stop)

        # open risk cap
        if open_positions:
            current_open = self.open_risk(open_positions)
            this_risk = (entry - stop) * size
            cap = equity * self.max_open_risk_frac
            if current_open + this_risk > cap:
                return RiskDecision(
                    False,
                    f"open risk {current_open + this_risk:.0f} > cap {cap:.0f}",
                )

        return RiskDecision(True, "ok", size=size, stop=stop)

    def take_profit_price(self, entry: float, stop: float, rr: float = 2.0) -> float:
        """Set a take-profit at rr:1 reward:risk (default 2:1)."""
        risk = entry - stop
        return entry + rr * risk
