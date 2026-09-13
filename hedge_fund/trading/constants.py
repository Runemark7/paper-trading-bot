"""Live paper-tournament constants. Single source of truth.

Imported by champions, tournament, and dashboard. PROTOCOL amendments
cite these values (5m live/admit, 300s 24/7 decision cycle, no live-slot
cap, budgeted leftover-universe discovery).
Do not document a 4h live book, hourly-only decisions, Sharpe 0.10,
WR 38%, 4-trade minimum, 25-trade graduation, arena capacity 1000,
a homemade 20-slot live-arena cap, a 07–21 night skip, or a 30-name
discovery sample (shuffle 30 and ignore the rest).
"""

from __future__ import annotations

from hedge_fund.data.binance import DEFAULT_TIMEFRAME, SUPPORTED_SYMBOLS

# --- Tape (same game for qualification and live) ---
# Live TradingLoop / CcxtSource / PROTOCOL: 5m bars. 4h history may remain
# on disk unused; it must not admit champions.
QUAL_TIMEFRAME = DEFAULT_TIMEFRAME  # "5m"
QUAL_SYMBOLS = SUPPORTED_SYMBOLS  # BTC/USDT, ETH/USDT
# More sequential ~90d windows once multi-year 5m tape exists — not one
# giant in-sample. Chronological walk-forward; loader keeps the last
# ``QUAL_WINDOW_BARS * QUAL_N_WINDOWS`` bars (scales with the constant).
# 8 × 90d = 720 calendar days (~2y). 3 × 90d was only ~270d of the same
# tape. Per-window size stays 90d so OOS is still a real hold-out.
QUAL_N_WINDOWS = 8
# ~90 calendar days of 5m per window (90 * 24 * 12). 2500 was ~1.4y of 4h
# and would be only ~9 days of 5m — too short for OOS to mean anything.
QUAL_WINDOW_DAYS = 90
QUAL_WINDOW_BARS = QUAL_WINDOW_DAYS * 24 * 12  # 25920
QUAL_COVERAGE_DAYS = QUAL_N_WINDOWS * QUAL_WINDOW_DAYS  # 720
QUAL_STRIDE = 1  # native 5m; do not downsample
RISK_POLICY = "rm_v1"

# Discovery qualification (scripts/tournament_engine.py). Gates use OOS/test
# only. Train PnL is logged, never scored, never an admit rule.
MIN_BACKTEST_SHARPE = 0.30  # modest OOS floor; 0.10 was a participation trophy
MIN_BACKTEST_TRADES = 30  # OOS trades across all windows (not train+test)
# Win-rate 38% dropped as an admit bar (2026-09-01). Beating B&H + sma_stack
# + 30 OOS trades + Sharpe ≥ 0.30 is the bar. All-windows non-negative is a
# diagnostic only (2026-09-12) — one empty/neg window does not veto.

# Paper graduation: TRADE_EVALUATION_LIMIT closed paper trades, then vs B&H.
TRADE_EVALUATION_LIMIT = 80
# No MAX_ACTIVE_CHAMPIONS. The 2026-09-01 20-slot arena starved prod's 31
# grandfathered names (needed = 20 - 31 <= 0). Admission is OOS 5m / rm_v1
# only. Universe size (~40–120) is the combinatorial bound, not a live cap.
# No DISCOVER_BATCH_SIZE. The old random sample of 30 (shuffle 30 and
# ignore the rest) stays deleted. Leftovers still drain 24/7, but each
# live_cycle tournament invocation takes a time-shared slice so a 60-name
# 5m walk-forward cannot wedge the 300s cycle. After #26 (4 names / 150s)
# still timed out the pod, #27 cut the live default to 1 name / ~90s so
# run_isolated and the web/API keep the rest of the tick. #28 made one
# evaluate_windows cheaper (EMA/WT caches, history trim) without raising
# the budget. #29 bumped a cautious notch to 2 names / ~120s; the server
# overloaded, so live default is back to a single-name slice: 1 name /
# ~90s. Most of the 300s stays for live + API. Do not jump back to 4/150.
# 2026-09-12: live_cycle skips tournament unless DISCOVERY_ON_CYCLE=1.
# The 1/90s slice is only the in-cycle emergency budget. The Windows
# worker is the discovery farm (scripts/discovery_worker.py).
# MIN_BACKTEST_TRADES = 30 remains the OOS trade floor, not a sample size.
# A non-qualified discovery_log eval parks that name forever — there is
# no DISCOVER_RETEST_COOLDOWN re-eligibility timer. Never-tested leftovers
# still drain 24/7 under the cycle budget. When never-tested leftovers
# are empty (or fewer than this slice), tournament auto-refills the next
# handful from a bounded structure-AND recipe into discovery_extended.json
# — not a human PR per batch, not thousands of clones. Static
# generate_universe() stays inside UNIVERSE_TARGET_MAX.
DISCOVER_CYCLE_MAX_NAMES = 1
DISCOVER_CYCLE_TIME_BUDGET_SECONDS = 90  # one eval; leave most of 300s for live + API
DISCOVERY_LOG_CAP = 10000  # newest-first rows; unique names are a separate count
DISCOVERY_REFILL_BATCH_SIZE = 16  # one handful per dry drain; sidecar pending queue
# No new evals for this long, with leftover work remaining → stuck/overdue copy.
DISCOVERY_QUIET_SECONDS = 2 * 3600

PAPER_START_CASH = 10_000.0

# GRADUATED_PAPER means finished the paper evaluation window beating
# buy-and-hold of the same assets over the same period, after fees.
# It is not authorization to trade real funds.
GRADUATED_PAPER = "GRADUATED_PAPER"
REJECTED_NEGATIVE_PNL = "REJECTED_NEGATIVE_PNL"

# Decision-cycle cadence. k8s cycle sidecar and compose scheduler must match.
# This is the job interval (5 minutes), aligned with the 5m bar close.
# Runs around the clock — no 07–21 Europe/Stockholm skip, no slower night
# cadence. Heartbeat is already continuous and does not open trades.
CYCLE_INTERVAL_SECONDS = 300  # 5 minutes, 24/7
