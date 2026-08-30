"""Live paper-tournament constants. Single source of truth.

Imported by champions, tournament, and dashboard. PROTOCOL amendment
2026-08-30 cites these values — keep 0.10 / 0.38 / 4 / 25 / 1000 unless the
live pool actually uses different numbers.

Comments and docstrings here MUST match the integers. Do not document
Sharpe ≥ 0.8, WR ≥ 45%, capacity 10, or a 10-trade graduation.
"""

from __future__ import annotations

# Discovery qualification (scripts/tournament_engine.py uses hedge_fund.backtest.strategies,
# not fast_quant / fee-free SimBroker).
MIN_BACKTEST_SHARPE = 0.10
MIN_BACKTEST_WIN_RATE = 0.38
MIN_BACKTEST_TRADES = 4

# Paper graduation: TRADE_EVALUATION_LIMIT closed paper trades, then status below.
TRADE_EVALUATION_LIMIT = 25
MAX_ACTIVE_CHAMPIONS = 1000  # arena capacity (not 10)

# GRADUATED_PAPER means finished the paper evaluation window with positive
# paper P&L. It is not authorization to trade real funds.
GRADUATED_PAPER = "GRADUATED_PAPER"
REJECTED_NEGATIVE_PNL = "REJECTED_NEGATIVE_PNL"

# Decision-cycle cadence. k8s cycle sidecar and compose scheduler must match.
CYCLE_INTERVAL_SECONDS = 3600  # 1 hour (07–21 Stockholm window in the sidecar)
