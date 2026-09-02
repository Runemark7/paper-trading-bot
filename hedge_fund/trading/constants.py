"""Live paper-tournament constants. Single source of truth.

Imported by champions, tournament, and dashboard. PROTOCOL amendment
2026-09-02 cites these values (5m live/admit, 300s decision cycle).
Do not document a 4h live book, hourly-only decisions, Sharpe 0.10,
WR 38%, 4-trade minimum, 25-trade graduation, or arena capacity 1000.
"""

from __future__ import annotations

from hedge_fund.data.binance import DEFAULT_TIMEFRAME, SUPPORTED_SYMBOLS

# --- Tape (same game for qualification and live) ---
# Live TradingLoop / CcxtSource / PROTOCOL: 5m bars. 4h history may remain
# on disk unused; it must not admit champions.
QUAL_TIMEFRAME = DEFAULT_TIMEFRAME  # "5m"
QUAL_SYMBOLS = SUPPORTED_SYMBOLS  # BTC/USDT, ETH/USDT
QUAL_N_WINDOWS = 3
# ~90 calendar days of 5m per window (90 * 24 * 12). 2500 was ~1.4y of 4h
# and would be only ~9 days of 5m — too short for OOS to mean anything.
QUAL_WINDOW_DAYS = 90
QUAL_WINDOW_BARS = QUAL_WINDOW_DAYS * 24 * 12  # 25920
QUAL_STRIDE = 1  # native 5m; do not downsample
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
# This is the job interval (5 minutes), aligned with the 5m bar close.
# Sidecar still skips outside 07–21 Europe/Stockholm.
CYCLE_INTERVAL_SECONDS = 300  # 5 minutes (07–21 Stockholm window in the sidecar)
