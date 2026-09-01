"""Live paper-tournament constants. Single source of truth.

Imported by champions, tournament, and dashboard. PROTOCOL amendment
2026-09-01 cites these values. Do not document a 5m admit bar, Sharpe 0.10,
WR 38%, 4-trade minimum, 25-trade graduation, or arena capacity 1000.
"""

from __future__ import annotations

from hedge_fund.data.binance import DEFAULT_TIMEFRAME, SUPPORTED_SYMBOLS

# --- Tape (same game for qualification and live) ---
# Live TradingLoop / CcxtSource / PROTOCOL: 4h bars. 5m is discovery-history
# only and must not admit champions.
QUAL_TIMEFRAME = DEFAULT_TIMEFRAME  # "4h"
QUAL_SYMBOLS = SUPPORTED_SYMBOLS  # BTC/USDT, ETH/USDT
QUAL_N_WINDOWS = 3
QUAL_WINDOW_BARS = 2500  # ~1.4y of 4h per window; total span = this × n_windows
QUAL_STRIDE = 1  # native 4h; do not downsample a 5m tape
RISK_POLICY = "rm_v1"

# Discovery qualification (scripts/tournament_engine.py). Gates use OOS/test
# only. Train PnL is logged, never scored, never an admit rule.
MIN_BACKTEST_SHARPE = 0.30  # modest OOS floor; 0.10 was a participation trophy
MIN_BACKTEST_TRADES = 30  # OOS trades across all windows (not train+test)
# Win-rate 38% dropped as an admit bar (2026-09-01). Beating B&H + sma_stack
# + 30 OOS trades + all windows non-negative is the bar.

# Paper graduation: TRADE_EVALUATION_LIMIT closed paper trades, then vs B&H.
TRADE_EVALUATION_LIMIT = 80
MAX_ACTIVE_CHAMPIONS = 20  # small arena; replenish only into free slots
DISCOVER_BATCH_SIZE = 30  # untested names per sweep (20–40), not 150

PAPER_START_CASH = 10_000.0

# GRADUATED_PAPER means finished the paper evaluation window beating
# buy-and-hold of the same assets over the same period, after fees.
# It is not authorization to trade real funds.
GRADUATED_PAPER = "GRADUATED_PAPER"
REJECTED_NEGATIVE_PNL = "REJECTED_NEGATIVE_PNL"

# Decision-cycle cadence. k8s cycle sidecar and compose scheduler must match.
# This is the job interval (hourly), not the bar timeframe (4h).
CYCLE_INTERVAL_SECONDS = 3600  # 1 hour (07–21 Stockholm window in the sidecar)
