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
# History tape still carries both symbols (cross-asset check, shared store).
# Discovery, qualification, and new live entries use BTC/USDT only.
QUAL_SYMBOLS = SUPPORTED_SYMBOLS  # BTC/USDT, ETH/USDT — tape, not the book
TRADE_SYMBOLS = ("BTC/USDT",)
CROSS_ASSET_SYMBOL = "ETH/USDT"
# ~90 calendar days of 5m per window (90 * 24 * 12). 2500 was ~1.4y of 4h
# and would be only ~9 days of 5m — too short for OOS to mean anything.
QUAL_WINDOW_DAYS = 90
QUAL_WINDOW_BARS = QUAL_WINDOW_DAYS * 24 * 12  # 25920


def qual_n_windows_for_bars(n_bars: int, window_bars: int = QUAL_WINDOW_BARS) -> int:
    """Whole ~90d windows that fit in ``n_bars``. No partial window.

    Sizes ``QUAL_N_WINDOWS`` from tape length so a deeper fetch expands
    coverage with the same formula. Per-window size stays ``QUAL_WINDOW_BARS``.
    """
    wb = int(window_bars)
    if wb <= 0:
        return 0
    return max(0, int(n_bars) // wb)


# Chronological walk-forward — not one giant in-sample. Loader keeps the
# last ``QUAL_WINDOW_BARS * QUAL_N_WINDOWS + QUAL_WARMUP_BARS`` bars (scales
# with the constants).
# jensa ``crypto_history_5m.json`` 2026-09-14: 600787 5m bars/symbol
# (2020-12-26 → 2026-09-12 ≈ 2086.8d ≈ 5.71y). floor(600787/25920)=23;
# 23×25920=596160. 24 would overshoot. 8 × 90d was only ~720d (~2y) of
# the same tape. Per-window size stays 90d so OOS is still a real hold-out.
# Update ``QUAL_TAPE_BARS_MEASURED`` when a deeper fetch lands.
QUAL_TAPE_BARS_MEASURED = 600787
QUAL_N_WINDOWS = qual_n_windows_for_bars(QUAL_TAPE_BARS_MEASURED)  # 23
QUAL_COVERAGE_DAYS = QUAL_N_WINDOWS * QUAL_WINDOW_DAYS  # 2070
QUAL_STRIDE = 1  # native 5m; do not downsample
RISK_POLICY = "rm_v1"
# Amendment 2026-10-08 (Alexander): exits are stop/TP only, open notional is
# capped at 100% of equity, and beat-B&H compares daily-equity Sharpe (OOS,
# same windows, after fees) instead of raw net P&L. Every discovery record
# carries this stamp; prod ingest rejects rows from a worker on older rules
# so a stale checkout cannot park names under the old engine.
# Amendment 2026-10-08 18:08 (Alexander, option b): OOS segments are
# contiguous, non-overlapping and tile the tape end to end, anchored to a
# fixed start so a new day only extends the last segment. Bumped so a
# worker still on end-aligned windows is paused and its rows are refused.
# Amendment 2026-10-27 (Alexander): gate v4. Same v3 floors, plus a sealed
# holdout, BTC-only entries, deflated Sharpe, CSCV PBO, parameter
# neighbours, and a fee stress. Workers on the 2026-10-08 tag are paused.
# Amendment 2026-10-27 later the same day (Alexander): gate v5. No
# discovery qualification check fails for losing to buy-and-hold: not the
# discovery windows, not the sealed holdout, and not the cost-stress
# re-run. Those figures stay on the row as informational metrics. Cost
# stress still requires stressed Sharpe >= 0.30. Workers on the v4 tag
# are paused.
# Amendment 2026-10-28 (Alexander, 20:39 CET): gate v6. Discovery restarts
# on a pre-registered strategy library (hedge_fund.library.catalog) scored
# by the signal_exit engine on completed daily bars: long or flat, all-in,
# exit when the signal turns off. Daily floors are 8 OOS round-trips and
# 2 holdout trades; the 5m floors stay 30 and 8. DSR trial count = every
# grid point in the library. The 5m rm_v1 engine is unchanged for the
# existing champions. Workers on an older tag are paused.
# Amendment 2026-10-29 (Alexander, 10:35 CET): gate v7 for lib.* only.
# A library name passes when net P&L after fees and slippage is positive
# on the warmed out-of-sample book (2018-12-01..2025-09-30) and on the
# sealed holdout, when the same is true at 2× fees + 5 bps on both spans,
# when the 8 / 2 trade floors hold, and when at least 70% of parameter
# neighbours clear those three profit checks. DSR, PBO, Sharpe floors,
# sma_stack, and buy-and-hold are warnings. The 5m path is unchanged.
V4_GATE_RULES = "gate_v4_btc_holdout_dsr_pbo_20261027"
V5_GATE_RULES = "gate_v5_btc_holdout_dsr_pbo_20261027"
# First library stamp. Superseded before merge: indicators were read from
# 2021-01-26, so a NaN lookback was scored as flat. Kept so a row written
# on that stamp is not mistaken for the warmed gate.
V6_GATE_RULES = "gate_v6_library_signal_exit_20261028"
# Warmed library stamp. Superseded by gate v7: DSR, PBO, Sharpe, and
# sma_stack were vetoes. Kept so a v6 row is not re-read as a v7 pass.
V6_WARMUP_GATE_RULES = "gate_v6_library_warmup_20261029"
GATE_RULES = "gate_v7_library_profit_20261029"
# 5m rm_v1 rows under these tags measure the same engine with the same
# floors, so the exploratory atom reports may pool them. The v7 tag is
# the worker stamp; random mode still applies the 5m floors.
ATOM_GATE_RULES = frozenset({
    V5_GATE_RULES,
    V6_GATE_RULES,
    V6_WARMUP_GATE_RULES,
    GATE_RULES,
})
# Shown on the discovery API and the Discovery page.
GATE_CRITERIA = (
    "Gate v7 (paper only, daily library): BTC-only. A pre-registered "
    "library is scored with signal_exit on completed daily bars (long or "
    "flat, all-in, next-open fill after 0.1% fee + 2 bps, 20% stop at the "
    "next open). Indicators use BTC history from 2017-08-17; a day that is "
    "not warm fails. The sealed holdout stays 2025-10-01..2026-10-01. "
    "Hard vetoes: out-of-sample net P&L > 0 after fees and slippage "
    "(2018-12-01 through 2025-09-30), holdout net P&L > 0, the same P&L > 0 "
    "on both spans at 2× fees + 5 bps, at least 8 out-of-sample round-trips, "
    "at least 2 holdout trades, and at least 70% of parameter neighbours "
    "also profitable on those three profit checks. DSR (N = every library "
    "grid point), PBO, Sharpe floors, sma_stack, and buy-and-hold stay "
    "informational warnings. They are computed, stored, and shown. They "
    "do not reject a name. "
    "A pass is proposed, not seated. The 5m floors (30 trades, Sharpe, "
    "sma_stack, holdout Sharpe, DSR, PBO, 8 holdout trades, 18×87-day grid) "
    "are unchanged. The existing 5m champions keep the rm_v1 engine."
)
BH_METRIC_ROLE = "informational"
# Historical full-tape grid (23 x 87d, last segment open). Discovery no
# longer scores that grid: the holdout and everything after it are sealed.
# Kept so the tiled-layout tests and the pre-v4 segment dates stay honest.
V3_GATE_RULES = "sltp_cap100_bhdsr_tiled87_20261008"
# Prior bars fed into each window so EMA/SMA/HTF/ATR are warm when scored
# bars begin. Binding HTF: parser-allowed ``h4_ema_abv_N`` with N=70 needs
# 70 completed 4h closes = 70×48=3360 five-minute bars, plus up to 47 for
# an incomplete 4h bucket. 14 calendar days = 4032. Structure lookbacks
# (≤96) and ``sma_abv_200`` are far smaller. Loader keeps
# ``QUAL_WINDOW_BARS * QUAL_N_WINDOWS + QUAL_WARMUP_BARS``. First window
# uses whatever prefix exists if the file is short (partial warm-up).
# OOS trades/PnL/Sharpe exclude the pad; B&H stays on the scored OOS
# closes only. Does not steal 90d window length — pad is extra prefix.
QUAL_WARMUP_DAYS = 14
QUAL_WARMUP_BARS = QUAL_WARMUP_DAYS * 24 * 12  # 4032


# Tiled OOS layout (amendment 2026-10-08 18:08). Everything is fixed by
# timestamp, not by distance from the tape end:
#   lead-in   = QUAL_WARMUP_BARS (indicator seed) + QUAL_TRAIN_BARS (train,
#               logged only) of bars right before each OOS segment
#   segment k = [QUAL_OOS_START_MS + k*QUAL_SEGMENT_MS, +QUAL_SEGMENT_MS)
#   last      = [QUAL_OOS_START_MS + (N-1)*QUAL_SEGMENT_MS, tape end]
# Segments touch end to end, never overlap, and each one only sees bars
# before its own end (train/warm-up strictly before its OOS start). A new
# UTC day appends to the last segment; earlier segments never move.
# 23 x 90d + 77d lead-in does not fit in the shared store (it starts
# 2021-01-23), so segments are 87d: 22 full segments + the open-ended last
# one (2026-07-17 ->) fit with a week of margin before QUAL_TAPE_START_MS.
TF_MS_5M = 300_000
QUAL_TRAIN_BARS = int(QUAL_WINDOW_BARS * 0.70)  # 18144 = old 63d train span
QUAL_SEGMENT_DAYS = 87
QUAL_SEGMENT_BARS = QUAL_SEGMENT_DAYS * 24 * 12  # 25056
QUAL_SEGMENT_MS = QUAL_SEGMENT_DAYS * 86_400_000
QUAL_TAPE_START_MS = 1_612_224_000_000  # 2021-02-02 00:00 UTC
QUAL_OOS_START_MS = QUAL_TAPE_START_MS + (QUAL_TRAIN_BARS + QUAL_WARMUP_BARS) * TF_MS_5M
# 2021-04-20 00:00 UTC. Last segment starts 2026-07-17 00:00 UTC.
QUAL_LAST_SEGMENT_START_MS = QUAL_OOS_START_MS + (QUAL_N_WINDOWS - 1) * QUAL_SEGMENT_MS

# Sealed holdout (Alexander, 2026-10-27). Fixed, not rolling.
# Last 12 months before 2026-10-01: [2025-10-01, 2026-10-01).
# Discovery / mint / densify / lift / refill never score these bars.
# Bars at or after HOLDOUT_END_MS are forward/live only.
HOLDOUT_START_MS = 1_759_276_800_000  # 2025-10-01 00:00 UTC
HOLDOUT_END_MS = 1_790_812_800_000  # 2026-10-01 00:00 UTC
# Full 87d segments that end at or before the holdout. 18 x 87d ends
# 2025-08-03; the leftover 59d (through 2025-09-30) is not a segment and
# is not scored. It is also not part of the holdout.
DISCOVERY_N_WINDOWS = (HOLDOUT_START_MS - QUAL_OOS_START_MS) // QUAL_SEGMENT_MS

# Gate v4 thresholds that v5 keeps. v3 floors that remain are Sharpe,
# trades, and sma_stack. Buy-and-hold is informational under v5 on the
# discovery windows, the holdout, and the cost-stress re-run. Cost stress
# still requires stressed Sharpe >= MIN_BACKTEST_SHARPE. These extras stay.
# Bailey & Lopez de Prado 2014: DSR is the probability the true Sharpe
# clears the expected maximum of N trials. 0.95 is their usual confidence.
DSR_MIN_PROBABILITY = 0.95
# Bailey, Borwein, Lopez de Prado, Zhu 2017: PBO is the probability the
# in-sample winner is below the OOS median. 0.5 is a coin flip; 0.20 is
# the pre-registered ceiling (at most one CSCV split in five overfits).
PBO_MAX = 0.20
NEIGHBOR_RELATIVE_STEPS = (0.10, 0.20)
NEIGHBOR_PASS_MIN_FRACTION = 0.70
COST_STRESS_FEE_MULTIPLE = 2.0
COST_STRESS_EXTRA_SLIPPAGE = 0.0005  # +5 bps per side on top of the 2 bps model
# Soft: recorded, does not block admission. ETH is not a pass requirement.
CROSS_ASSET_MIN_SHARPE = 0.0
# Holdout trade floor. v3 asks for 30 trades over 18 x 87d (~4.29y),
# about 7 trades per year. One holdout year at 8 trades is not a lower
# annual rate than that floor.
HOLDOUT_MIN_TRADES = 8
# Daily signal-exit floors (Alexander, 2026-10-29). These do not replace
# the 5m floors above. The library scores one continuous book and slices
# PBO on longer blocks; 30 and 8 were sized for rules that trade constantly.
LIBRARY_TAPE_START_MS = 1_502_928_000_000  # 2017-08-17 00:00 UTC, Binance BTCUSDT
# The first 5m bar can print a couple of hours after midnight. Two days
# of slack still rejects a tape that only starts in 2021.
LIBRARY_TAPE_START_SLACK_MS = 2 * 86_400_000
LIBRARY_MIN_OOS_TRADES = 8
LIBRARY_HOLDOUT_MIN_TRADES = 2
LIBRARY_BLOCK_TARGET_DAYS = 390
# Pre-registered before any warmed result was read. One stop for every
# library strategy. A with-stop and no-stop pair would double N, so the
# stop is on and the no-stop book is not a trial.
LIBRARY_STOP_FRAC = 0.20
FORWARD_MIN_DAYS = 90
FORWARD_MIN_CLOSED_TRADES = 20
FORWARD_MAX_DD_MULTIPLE = 1.5
FORWARD_SHARPE_FLOOR = 0.0
# Store keeps this much tape before QUAL_TAPE_START_MS so gaps never cut
# into segment 0's lead-in.
QUAL_STORE_MARGIN_BARS = 7 * 24 * 12
# Canonical (hashed, shared) tape starts here: the margin before segment 0's
# lead-in. Cut by timestamp, so the hash does not depend on store length.
QUAL_CANONICAL_START_MS = QUAL_TAPE_START_MS - QUAL_STORE_MARGIN_BARS * TF_MS_5M


def qual_segment_bounds(k: int, n_windows: int | None = None) -> tuple[int, int | None]:
    """[start_ms, end_ms) of OOS segment ``k``; the last one is open-ended (None)."""
    n = QUAL_N_WINDOWS if n_windows is None else int(n_windows)
    if not 0 <= int(k) < n:
        raise ValueError(f"segment {k} outside 0..{n - 1}")
    start = QUAL_OOS_START_MS + int(k) * QUAL_SEGMENT_MS
    end = None if int(k) == n - 1 else start + QUAL_SEGMENT_MS
    return start, end


def qual_store_bars(now_ms: int | None = None) -> int:
    """Live-tape / loader bar budget: every 5m slot since QUAL_TAPE_START_MS + margin.

    Grows one bar per 5 minutes, so the store appends instead of trimming the
    oldest bars, and the fixed segment-0 lead-in never falls off the front.
    """
    import time as _time

    now = int(_time.time() * 1000) if now_ms is None else int(now_ms)
    slots = max(0, (now - QUAL_TAPE_START_MS) // TF_MS_5M + 1)
    return int(slots + QUAL_STORE_MARGIN_BARS)


def library_store_bars(now_ms: int | None = None) -> int:
    """BTC file budget that keeps the library prefix from 2017-08-17.

    ETH and the hashed 5m tape stay on ``qual_store_bars``. A caller that
    passes an explicit target still uses that target for both symbols.
    """
    import time as _time

    now = int(_time.time() * 1000) if now_ms is None else int(now_ms)
    slots = max(0, (now - LIBRARY_TAPE_START_MS) // TF_MS_5M + 1)
    return max(qual_store_bars(now), int(slots))


def qual_keep_bars(n_windows: int | None = None, warmup_bars: int | None = None) -> int:
    """Loader/fetch span: scored windows plus warm-up prefix."""
    n = QUAL_N_WINDOWS if n_windows is None else n_windows
    w = QUAL_WARMUP_BARS if warmup_bars is None else warmup_bars
    return QUAL_WINDOW_BARS * int(n) + int(w)


def live_signal_eval_bars() -> int:
    """Trailing 5m bars the live cycle feeds indicators.

    The last walk-forward window is exactly one scored ``QUAL_WINDOW_BARS``
    span plus the ``QUAL_WARMUP_BARS`` prefix (EMA/HTF seeded at the start
    of that slice). Latest-bar values match qualification only on that
    same slice — not on a 300-bar tail, and not on a shorter lookback-only
    tail (EMA is path-dependent from its seed).
    """
    return QUAL_WINDOW_BARS + QUAL_WARMUP_BARS

# Discovery qualification (scripts/tournament_engine.py). Gates use OOS/test
# only. Train PnL is logged, never scored, never an admit rule.
MIN_BACKTEST_SHARPE = 0.30  # modest OOS floor; 0.10 was a participation trophy
MIN_BACKTEST_TRADES = 30  # OOS trades across all windows (not train+test)
# Win-rate 38% dropped as an admit bar (2026-09-01). The discovery bar is
# 30 OOS trades, Sharpe ≥ 0.30, and beat sma_stack. Buy-and-hold daily
# Sharpe and B&H P&L are informational (2026-10-27, gate v5). All-windows
# non-negative is a diagnostic only (2026-09-12) — one empty/neg window
# does not veto.

# Paper graduation: TRADE_EVALUATION_LIMIT closed paper trades, then net
# paper P&L > 0 after taker fee and slippage (the stored trade P&L).
# Buy-and-hold P&L is recorded and is not the graduation rule (2026-10-27).
# Not a cap on how many champions the live cycle evaluates. run_isolated
# runs every active name each cycle.
TRADE_EVALUATION_LIMIT = 80
# No MAX_ACTIVE_CHAMPIONS. The 2026-09-01 20-slot arena starved prod's 31
# grandfathered names (needed = 20 - 31 <= 0) and stays deleted.
# No FORWARD_CHAMPION_CAP. Alexander, 2026-10-28 20:09 CET: there is no
# limit on champions. The 2026-10-27 cap of 5 is deleted. No admission
# path refuses because of how many champions are active. Admission stays
# a manual owner decision. Names already on the book are not retired.
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

# GRADUATED_PAPER means the paper evaluation window finished with net paper
# P&L > 0 after taker fee and slippage. Buy-and-hold P&L is informational.
# It is not authorization to trade real funds. Paper prices only.
GRADUATED_PAPER = "GRADUATED_PAPER"
REJECTED_NEGATIVE_PNL = "REJECTED_NEGATIVE_PNL"

# Decision-cycle cadence. k8s cycle sidecar and compose scheduler must match.
# This is the job interval (5 minutes), aligned with the 5m bar close.
# Runs around the clock — no 07–21 Europe/Stockholm skip, no slower night
# cadence. Heartbeat is already continuous and does not open trades.
CYCLE_INTERVAL_SECONDS = 300  # 5 minutes, 24/7
