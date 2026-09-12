# PROTOCOL — the honesty contract

This document is the pre-registered rules of the experiment. It exists so
that "I'm profitable" can never be an artifact of luck, overfitting, or
hindsight. **It is written before any meaningful trading and can only be
amended with a dated, explicit change.**

The experiment: *can an LLM agent (Hermes) state probabilities for BTC/ETH
moves that become both calibrated and profitable over time?* Paper trading
only — no real money until profitability is demonstrated against a baseline
over a meaningful sample, AND calibration is demonstrated independently of P&L.

## 1. No lookahead

- Decisions use only data available at decision time. The calibration
  posterior used to quote a probability contains **only trades completed
  before that decision**.
- A trade's outcome is recorded into the calibration layer only when the
  position closes (horizon known) — never earlier.
- Past trades are never revised. A mistake is logged, not rewritten.

## 2. Separate train / test worlds

- **Live-forward paper account**: the account traded from now on. This is
  what the dashboard shows.
- **Held-out backtest window**: a frozen historical window we do not tune
  against. If live-forward performance is not replicated by the held-out
  window once enough trades accumulate, the strategy is overfit and is
  flagged — it is not silently kept.

## 3. Baseline benchmark

- "Profitable" always means **profitable relative to a buy-and-hold
  baseline** of the same assets over the same period, after fees+slippage.
- The dashboard overlays the equity curve on the buy-and-hold baseline.
- A simple momentum rule is a secondary benchmark. Beating neither means the
  "edge" is not a real edge.

## 4. Preregistration

- Strategy, conditions, risk rules, and evaluation metrics are defined here
  and in the code (deterministic engine + risk manager) **before** trading.
- Post-hoc reinterpretation of results is disallowed. Metrics (P&L,
  win-rate, Brier score, calibration) are fixed in advance.

## 5. Calibration is measured separately from P&L

- **Calibration** = is my stated probability equal to my measured frequency?
  (Brier score + reliability buckets + stated-vs-measured table.)
- **P&L** = did the account grow vs baseline?
- These can diverge. A calibrated predictor can still lose (bad timing/sizing,
  market regime) and a miscalibrated one can profit (luck, bull run). Both are
  shown, independently, always.

## 6. Fees and slippage are charged on every fill

- Taker fee (0.1 %) + slippage (2 bps) are applied to every simulated fill.
- No "free" paper profits. Any strategy that only works fee-free is not
  profitable — it is unmeasured.

## 7. Circuit breakers

- 1 % fixed-fractional risk per trade, 5 % max open risk, hard stops on
  every position, 15 % drawdown halt. These live in deterministic code the
  LLM cannot talk its way around.
- A paused loser (drawdown halt) is reported and kept in the log — it is
  data, not a secret.

## 8. The learning loop

- Each signal condition (e.g. `BTC/USDT|4h|uptrend_rsi_mid`) accumulates its
  own Beta-Binomial posterior over outcomes.
- Stated probability = calibrated posterior mean (agent proposal blends in
  only during cold-start warm-up; measured frequency dominates thereafter).
- The prose never overrides the data.

---

### Amendments

| Date | Change |
|------|--------|
| 2026-08-30 | Live experiment is the isolated-account paper tournament of combinatorial TA strategies, not the original LLM-probability study. Original §§ 1–8 remain as the historical contract; superseded clauses are named in the amendment below. |
| 2026-09-01 | Qualification uses the same 4h tape and `rm_v1` stop/size policy as live. Arena 20, OOS-only admit bar, 80-trade paper gate vs buy-and-hold. Original §§ 1–8 and the 2026-08-30 amendment remain; superseded clauses are named in the 2026-09-01 amendment. |
| 2026-09-02 | Live book and admit bar move to 5m candles. Decision cycle every 5 minutes (`CYCLE_INTERVAL_SECONDS = 300`). Walk-forward windows rescaled to ~90 calendar days of 5m per window. Strategy lookbacks are bar counts (e.g. `dip_24b` = 2 hours, not 4 days). 4h is superseded for live and admit. Paper only; `GRADUATED_PAPER` meaning unchanged. Original §§ 1–8 and prior amendments remain; superseded clauses are named in the 2026-09-02 amendment. |
| 2026-09-03 | Structure atoms exist (`don_hi_N`, `don_lo_N`, `near_swing_hi_N`, `near_swing_lo_N`). They are OHLC (high/low from the same 5m klines), not close-only. Close-only names still parse. Qual/live still 5m, `rm_v1`, OOS gates unchanged. Still paper. |
| 2026-09-03 | Pattern atoms: `dbl_bot_k` (long). `dbl_top_k` is parsed but not a standalone long. Trend / breakout / momentum already exist as `sma_stack`/`sma_abv`, `don_hi_*`, `mom_*` — not duplicated. Hold band: 1.0% or 1× ATR. Paper only; gates unchanged. |
| 2026-09-05 | Cipher-shaped paper atoms from public LazyBear WaveTrend / VuManChu-inspired green-dot rule (`wt_cross_up_os`). Not Market Cipher; not affiliated. Closed-bar 5m HLC3, LazyBear 10/21/4, OS=−60. No MFI. `rm_v1` / OOS gates unchanged. |
| 2026-09-05 | Night window revoked. Decision cycle runs every 5 minutes around the clock. Discovery drains remaining untested universe names each sweep (no 30-name sample). `GET /api/discovery/summary` is last-known tested / in-flight / leftover-untested (not a live job). OOS gates unchanged. Paper only. No cull of champions. No new strategies. |
| 2026-09-07 | Discovery is budgeted per 5m cycle (time + max names, rotating cursor, 24h retest cooldown) so a leftover drain cannot wedge `run_isolated`. Each finished name is appended to `discovery_log.json` immediately. Summary `last_tested_at` is the newest eval; unique tested ≠ log rows; stale tournament stamp is stuck, not idle. OOS gates unchanged. Paper only. No cull of champions. |
| 2026-09-07 | Addendum: per-cycle discovery slice scaled down after prod timeout. Live default is `DISCOVER_CYCLE_MAX_NAMES = 1` and `DISCOVER_CYCLE_TIME_BUDGET_SECONDS = 90` so `run_isolated` and the web/API stay healthy inside the 300s cycle. Leftover drain still 24/7 across cycles. OOS gates unchanged. Paper only. No cull of champions. |
| 2026-09-10 | A discovery evaluation that does not qualify parks that name forever. No 24h retest cooldown re-walk of rejects. Never-tested leftovers still drain 24/7 under the 1 name / ~90s cycle budget. Existing `discovery_log.json` fails are permanent. Same date: per-name `evaluate_windows` is cheaper (causal EMA / WaveTrend series cache, trim `crypto_history_5m.json` to the 3×90d span, compact discovery log). OOS gates and window lengths unchanged. Paper only. No cull of champions. |
| 2026-09-11 | Addendum: per-cycle discovery slice bumped one cautious notch after cheaper per-name evals (#28). Live default is `DISCOVER_CYCLE_MAX_NAMES = 2` and `DISCOVER_CYCLE_TIME_BUDGET_SECONDS = 120` so more never-tested names can run without returning to the 4 / 150s crash settings. Fail-once, OOS gates, and window lengths unchanged. Paper only. No cull of champions. |
| 2026-09-11 | Leftover universe batch: existing 5m dip/mom ANDed with unused Donchian / swing / `dbl_bot` lookbacks (`NEW_STRUCTURE_ANDS`). Fail-once stays — new names get one shot. No WaveTrend clones, no MFI, no chart-pattern zoo. Champions and the parked 60 untouched. Paper only; OOS gates unchanged. |
| 2026-09-11 | Auto-refill: when never-tested leftovers are empty (or fewer than the 2-name cycle slice), tournament appends the next handful of parseable, non-near-duplicate structure-AND names to `discovery_extended.json`. No human PR per batch. Fail-once stays. Static `generate_universe()` remains inside `UNIVERSE_TARGET_MAX`. Paper only; OOS gates unchanged. |
| 2026-09-11 | Addendum: server overloaded under the 2-name slice. Live default is `DISCOVER_CYCLE_MAX_NAMES = 1` and `DISCOVER_CYCLE_TIME_BUDGET_SECONDS = 90` so discovery evaluates only one name per cycle; most of the 300s stays for `run_isolated` and the web/API. Not a return to 4 / 150s. Fail-once, auto-refill (`DISCOVERY_REFILL_BATCH_SIZE`), OOS gates, and window lengths unchanged. Paper only. No cull of champions. |
| 2026-09-12 | Discovery walk-forwards leave the k8s cycle sidecar. `live_cycle` skips tournament by default (`DISCOVERY_ON_CYCLE=0` / `PAPER_DISCOVERY_MODE=off`). Cluster keeps `run_isolated` → collect → report. Windows PC (`jensa`) is the discovery farm: same fail-once / auto-refill / OOS / `rm_v1` / 5m windows; results POST to `https://trading.runevibe.se/api/discovery/ingest` with a paper-only shared secret. No kubectl tunnel. Paper only. No cull of champions. |
| 2026-09-12 | Structure-AND refill recipe expands: unused Donchian / near-swing / near-level lookbacks (`STRUCTURE_NS` through 96) ANDed with existing 5m dip/mom/trend filters. Farm auto-refill picks them up. Fail-once stays — `near_duplicate_key` must not collapse onto parked fails. No named candlesticks, no WaveTrend spam, no MFI. Static `generate_universe()` stays inside `UNIVERSE_TARGET_MAX`. Paper only; OOS gates unchanged. |
| 2026-09-12 | Windows farm Start/Stop from `/discovery`. Durable `state/discovery_farm.json` flag; `discovery_worker.py` idles (does not exit) while paused and resumes when the flag is on. Same ingest token gates `POST /api/discovery/farm`. Heartbeat on ingest so the UI can say worker not seen. k8s cycle stays `DISCOVERY_ON_CYCLE=0`. Paper only. No OOS / trading changes. |
| 2026-09-12 | Same-date later: refill recipe lookbacks extend through 192 (9h–16h on 5m) and leftover TREND / `ema_stack` / 3-atom families already in `parse_strategy`. Farm was eligible=0 after ~917 unique fails. Fail-once stays. No named candlesticks. Static list unchanged. Paper only; OOS gates unchanged. |
| 2026-09-12 | Same-date later: refill mint bases broaden past the frozen 3×3 dip/mom. Parser-allowed lookbacks + `%` thresholds (`DIP_FILTERS_WIDE` / `MOM_FILTERS_WIDE`) and continuation ANDs (wide mom/shallow dip × `don_hi` / `near_swing_hi`, short MA × breakout). Farm was ~1000 unique / 0 natural pass; beat-B&H ~100%. Fail-once stays. No named candlesticks. Static list unchanged. Paper only; OOS gates unchanged. |
| 2026-09-12 | Walk-forward calendar coverage extends to 8 × ~90d of native 5m (`QUAL_N_WINDOWS` = 8, `QUAL_WINDOW_DAYS` = 90, ~720 calendar days) once multi-year `crypto_history_5m.json` exists. Per-window size stays 90d (honest chronological hold-outs, not one giant in-sample). OOS **thresholds** unchanged: 30 trades, Sharpe ≥ 0.30, beat B&H, beat `sma_stack`, all-windows diagnostic only, fail-once, 5m, `rm_v1`. Fetch default bars track the new span; page cap 2500. Slower evals on jensa; do not throttle live k8s. Still paper. |
| 2026-09-12 | [docs/WORKFLOW.md](docs/WORKFLOW.md) is the canonical living map of mint / farm / eval / prod topology. Update those diagrams in the same PR that changes refill recipe shape, window count, ingest, farm location, or gate meaning. Paper only; OOS thresholds unchanged. |

### Amendment 2026-08-30 — what actually runs

This amendment names the experiment that is live on `main`. Original sections above are the pre-registered contract and are **not rewritten**. Where they no longer describe the live path, they are superseded on this date as listed at the end.

**Paper only.** No real-money broker. Graduation status `READY_FOR_LIVE` means **graduated paper**: the strategy finished the evaluation window with positive paper P&L. It is not authorization to trade real funds. The live code token is `GRADUATED_PAPER` (renamed from `READY_FOR_LIVE`; same meaning).

**What is traded.** BTC/USDT and ETH/USDT, 4h bars. The live book is a tournament of isolated €10k paper accounts, one per champion strategy (`hedge_fund.trading.champions`; capacity `MAX_ACTIVE_CHAMPIONS = 1000`). When the pool is empty, `PAPER_STRATEGY` defaults to `sma_stack`. Candidates are combinatorial technical-analysis rules (MA stacks, RSI bands, momentum/dip, hybrids). Names such as `multi_timeframe_*` are not true multi-timeframe execution: the live cycle still computes signals on a single 4h series.

**How a signal becomes a position.** Each cycle is `TradingLoop.run_cycle`: fetch 4h klines → `compute_signal(strategy)` → if direction is not long, close open lots for that symbol (signal-invalidation exit) and do not enter → else size through `RiskManager` → paper buy with a mandatory stop. Regime gate is **off** (`regime=None`); `/api/regime` is display-only.

The CronJob (`python -m hedge_fund.trading.run`) and POST `/run` (`python -m hedge_fund.trading.run_isolated`) execute **this same cycle** (same loop, same stops/sizing/strategy). They are not two books: `run_isolated` is one `TradingLoop` per champion with its own sqlite and risk budget; `run.py` is the same cycle on a single account (champion override via `resolve_champion()`, else `sma_stack`). Heartbeat reuses `TradingLoop._manage_open_positions` for stop/TP only — it does not open trades.

**Size and stops.** Base risk is 1% of equity per trade, scaled by a confidence multiplier in [0.5×, 2.0×] from stated probability / 0.50. 5% max open risk. 15% drawdown halt. Pyramiding: max 3 lots per symbol; add a lot only if existing lots for that symbol are in profit. Stop = 2.0× ATR(14) below entry, floored at 1.5% and capped at 4.0% of entry (2.5% fallback if ATR is unavailable). Take-profit = 2:1 reward:risk versus that stop distance (not a fixed 5% target).

**Stated probability.** Not a constant 0.60. The live cycle logs `CalibrationStore.calibrated_probability` for the condition key. The proposal fed into that blend is a deterministic RSI/raw-score heuristic (`TradingLoop._propose_probability`), **not an LLM**. Cold-start blends the proposal; after 20 trials the posterior mean dominates. Calibration plumbing is on the live path; LLM-stated probabilities are not.

**"Profitable" vs buy-and-hold.** Original §3 still defines the word: profitable means vs a buy-and-hold of the same assets over the same period, after fees and slippage. That overlay is **not computed today**: `snapshot_equity(..., baseline=None)`, so the dashboard B&H series is empty. This amendment does not add a baseline engine. Beating buy-and-hold cannot be read off the live dashboard until a real overlay exists.

**Fees and circuit breakers.** Unchanged from original §6–7: 0.1% taker + 2 bps slippage on every fill (`PaperBroker`); 1% / 5% / 15% halt (`RiskManager`). Live sizing additionally applies the confidence multiplier and ATR stops, which original §7 does not mention.

**Tournament admission and graduation (code, not file-header comments).** Discovery qualification in `scripts/tournament_engine.py`: Sharpe ≥ 0.10, win rate ≥ 38%, ≥ 4 backtest trades, train PnL > 0 and test PnL > 0. Graduation: `TRADE_EVALUATION_LIMIT = 25` closed paper trades; positive paper P&L → `READY_FOR_LIVE` (**graduated paper**), else `REJECTED_NEGATIVE_PNL`.

**Superseded on this date** (original text kept above for history):

- Opening experiment statement (LLM agent Hermes stating probabilities as the live study) — superseded; the live experiment is the paper TA tournament described here.
- §3 insofar as it states the dashboard overlay as an implemented fact — superseded. The definition of "profitable" remains; the overlay is not computed.
- §4 / §8 insofar as they imply LLM-stated probabilities are what the live loop logs — superseded. Heuristic proposal + Beta-Binomial as above.
- §7 "1% fixed-fractional" as the complete live sizing rule — superseded in part: 1% is still the base; live sizing also uses 0.5–2.0× confidence and ATR stops.

**Not superseded:** §1 no-lookahead (outcomes enter calibration only on close); §2 separate train/test worlds (held-out backtests are used in discovery, not as the live book); §5 calibration vs P&L as separate metrics; §6 fees; the paper-only rule.

### Amendment 2026-09-01 — same game for qualification and live

This amendment does not rewrite original §§ 1–8 or the 2026-08-30 text above. It names what the paper tournament now actually uses for admission, capacity, graduation, and the buy-and-hold overlay. Single source of truth: `hedge_fund/trading/constants.py`. Risk policy frozen as `rm_v1` (`hedge_fund/risk/rm_v1.py`) — the live TradingLoop + RiskManager stop/size/fee rules, not a second engine.

**Why live is 4h (not 5m).** The live book has always been 4h bars: `TradingLoop.timeframe` defaults to `QUAL_TIMEFRAME` / `CcxtSource.DEFAULT_TIMEFRAME = "4h"`, `run_isolated` fetches `"4h"` klines, PROTOCOL 2026-08-30 said BTC/ETH 4h. The 5m tape was **discovery-only** (walk-forward qualification in `scripts/tournament_engine.py`). It is not the live cadence, and it is no longer the admit bar. 5m fetch scripts may remain for research; they must not write champions. The cycle sidecar still wakes hourly (`CYCLE_INTERVAL_SECONDS = 3600`) to manage stops on those 4h bars — that job interval is not a 5m or 4h bar.

**Same game.** Discovery/walk-forward backtests use 4h OHLCV, symbols BTC/USDT and ETH/USDT, the paper fee model (0.1% taker + 2 bps), and `rm_v1`: 1% risk, ATR stop 2.0× floored 1.5% / capped 4% (2.5% ATR fallback), confidence clamp [0.5×, 2.0×] (backtests use confidence 1.0 — no live posterior on history), pyramid max 3 lots / add only if existing lots are in profit, 5% open-risk cap. Empty-pool fallback remains `sma_stack`.

**Small arena.** `MAX_ACTIVE_CHAMPIONS = 20` (was 1000). Replenish only when `len(pool) < 20` after graduation/rejection, and only into free slots. Discover batch `DISCOVER_BATCH_SIZE = 30` untested names (not 150).

**Honest OOS admit bar (become a champion).** Score and gates use **test/OOS only**. Train PnL is logged and is not part of the admission score (no `tot_train_pnl * 0.5`). Every walk-forward window's **test** PnL must be ≥ 0; failed, skipped, or empty OOS windows count against that rule (the old `test_pnl < -50 or trades < 2` skip must not let a candidate pass on the remaining windows). `MIN_BACKTEST_TRADES = 30` OOS trades total. OOS must beat buy-and-hold of the same assets over the same test windows after fees, and must beat `sma_stack` on those windows. Modest OOS Sharpe floor: `MIN_BACKTEST_SHARPE = 0.30` (0.10 is dropped). Win-rate 38% is dropped as an admit bar.

**Paper gate.** `TRADE_EVALUATION_LIMIT = 80` closed paper trades (was 25). Graduation: paper PnL **greater than buy-and-hold** of the same assets over the same period, after fees — not merely PnL > 0. Else `REJECTED_NEGATIVE_PNL`. Status `GRADUATED_PAPER` still means graduated paper only, not real-money authorization.

**Buy-and-hold overlay.** Original §3's definition is now computed. `TradingLoop` writes `snapshot_equity(..., baseline=buy_and_hold_mtm)`: equal-weight long of the symbols present at first snapshot, entry charged at paper taker + slippage, marked at current prices (no exit fee while holding). Graduation prefers that overlay (`last baseline − start cash`); if snapshots have no baseline, it reconstructs a round-trip B&H from first entry / last exit prices per symbol in the closed-trade history, fees both sides. 2026-08-30's statement that the overlay is not computed is superseded on this date.

**Fewer hypotheses.** `generate_universe()` is an explicit ~50-name 4h list (was 3546 combinatorial clones after MTF/MFI were already dropped). No `daily()`/`h1()`/`m5()` wrappers, no MFI. Graduated names are skipped; near-duplicates (same rule, tiny param tweaks) are skipped via `near_duplicate_key`.

**Superseded on this date** (prior text kept above for history):

- 2026-08-30 "What is traded" capacity `MAX_ACTIVE_CHAMPIONS = 1000` — superseded; capacity is 20.
- 2026-08-30 tournament admission (Sharpe ≥ 0.10, win rate ≥ 38%, ≥ 4 trades, train PnL > 0 and test PnL > 0) — superseded by the OOS bar above.
- 2026-08-30 graduation at 25 closed trades with positive paper P&L — superseded; 80 trades vs buy-and-hold.
- 2026-08-30 / original §3 insofar as they state the dashboard overlay is not computed — superseded. The overlay is computed as above. The definition of "profitable" is unchanged.

### Amendment 2026-09-02 — live and admit on 5m bars

This amendment does not rewrite original §§ 1–8 or the 2026-08-30 / 2026-09-01 text above. It names the candle width and decision cadence the paper tournament now uses. Single source of truth: `hedge_fund/trading/constants.py`. Honest OOS gates, arena 20, paper 80 vs buy-and-hold, universe ~50, and `rm_v1` are unchanged in spirit from 2026-09-01.

**Paper only.** No real-money broker. Status `GRADUATED_PAPER` still means graduated paper only — finished the paper evaluation window beating buy-and-hold of the same assets over the same period, after fees. It is not authorization to trade real funds.

**Live book is 5m.** BTC/USDT and ETH/USDT, 5m bars. `TradingLoop.timeframe` defaults to `QUAL_TIMEFRAME` / `CcxtSource.DEFAULT_TIMEFRAME = "5m"`. `run_isolated` fetches `"5m"` klines. Condition keys are `symbol|5m|condition` (e.g. `BTC/USDT|5m|sma_stack_long`). Discovery/walk-forward reads `crypto_history_5m.json` (`QUAL_TIMEFRAME`). A 4h-only history directory must not admit anyone. 4h history may remain on disk unused for admit. Fetch scripts that write 5m (`scripts/fetch_history.py` default, `scripts/fetch_history_5m.py`) are the qualification path.

**Decision cycle every 5 minutes.** `CYCLE_INTERVAL_SECONDS = 300`. The k8s cycle sidecar and compose scheduler `sleep 300` (not 3600). Heartbeat/stop management stays on its own frequent interval (`heartbeat --interval 30`) and does not open trades. The *decision* cycle (`live_cycle.py` → tournament + `run_isolated` / `TradingLoop.run_cycle`) runs on 5m closes. The sidecar still skips outside 07–21 Europe/Stockholm; that night window is unchanged. An hourly sleep with 5m bars would still only trade about once per hour.

**Lookbacks are in bars, not clock-time.** Strategy names such as `dip_24b_lt1pc` mean 24 × 5m = **2 hours**, not 24 × 4h = 4 days. `mom_12b_gt3pc` is 12 × 5m = 1 hour. MA periods (`sma_abv_50`, `sma_stack_20_50_100`) are 50 / 100 five-minute bars (~4.2h / ~8.3h), not 50 / 100 four-hour bars. The 2026-09-01 4h reading of those names is superseded.

**Walk-forward windows.** `QUAL_N_WINDOWS = 3`, `QUAL_WINDOW_DAYS = 90`, `QUAL_WINDOW_BARS = 25920` (~90 calendar days of 5m per window; 90 × 24 × 12). Do not keep 2500 — that was ~1.4y of 4h and would be only ~9 days of 5m. `QUAL_STRIDE = 1` (native 5m). Honest OOS gates are unchanged: 30 OOS trades, all windows test PnL ≥ 0, beat buy-and-hold and `sma_stack`, score OOS-only, Sharpe ≥ 0.30. Arena `MAX_ACTIVE_CHAMPIONS = 20`. Paper gate `TRADE_EVALUATION_LIMIT = 80` vs buy-and-hold. Universe remains the explicit ~50-name list; `rm_v1` is unchanged.

**Same game, new tape.** Fees, `rm_v1` stops/size, empty-pool `sma_stack`, regime=`None`, paper-only, `GRADUATED_PAPER` meaning — unchanged from 2026-09-01. What changed is the bar width, the decision-job interval, and the calendar span of each walk-forward window.

**Superseded on this date** (prior text kept above for history):

- 2026-09-01 "Why live is 4h (not 5m)" and "Same game" insofar as they freeze live/admit on 4h and hourly sidecar sleep — superseded; live and admit are 5m, cycle interval is 300s.
- 2026-09-01 / 2026-08-30 "What is traded" 4h bars — superseded; 5m bars.
- 2026-09-01 `QUAL_WINDOW_BARS = 2500` as a 4h span — superseded; 25920 five-minute bars ≈ 90 days.
- 2026-09-01 statement that the 5m tape must not admit champions — superseded; 5m is the admit tape.
- 2026-09-01 "Fewer hypotheses" insofar as it calls the universe a 4h list — superseded; same names, 5m bars, bar-count lookbacks as above.

### Amendment 2026-09-03 — structure atoms (OHLC, not close-only)

This amendment does not rewrite original §§ 1–8 or the 2026-08-30 / 2026-09-01 / 2026-09-02 text above. It names a small set of **structure** signal atoms and how they are evaluated. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 trades, all windows ≥ 0, beat B&H + `sma_stack`, arena 20, paper 80 vs B&H). **Still paper.** `GRADUATED_PAPER` meaning is unchanged.

**Structure atoms exist** in the DSL (`hedge_fund/signals/structure.py`, parsed by `parse_strategy`):

- `don_hi_N` — close breaks the prior N-bar Donchian high.
- `don_lo_N` — close is within the documented near-band of the prior N-bar Donchian low (dip-at-support tag).
- `near_swing_hi_N` / `near_swing_lo_N` — close is within that same near-band of the last confirmed fractal swing high/low (half-window `N`).

Near-band: `max(0.20% of close, 0.25 × ATR(14))`. Documented in `structure.py`.

**OHLC, not close-only.** Live and `parse_strategy` were close-only for SMA/RSI/mom/dip. Real resistance is a high, not a close. `TradingLoop` already fetches klines (OHLCV). Highs and lows from those same bars are threaded into signal eval for these atoms only. Old close-only names (`dip_24b_lt1pc`, `sma_abv_50`, …) still parse and still ignore high/low. Calling a structure atom without highs/lows raises — close is not silently used as high/low.

**No lookahead.** Donchian uses bars **before** the decision bar (`highs[i-N:i]`, current bar excluded). The current bar's high cannot be the level that is being broken; a wick through the prior high with close still below is not a break. Swing pivots need `N` bars to the right before they exist.

**Handful of AND gates**, not a cartesian product, added to `generate_universe()` (still inside `UNIVERSE_TARGET_MAX` = 120): `dip_6b_lt2pc&near_swing_lo_12`, `dip_12b_lt3pc&don_lo_24`, `mom_12b_gt3pc&don_hi_24`, `sma_abv_50&don_hi_24`, plus a few similar names.

**Refused.** Head-and-shoulders, flags, triangles, FVGs, order blocks, 40 candlestick names, screenshot vision, price-action-lib kitchen sink. Round-number psychological levels (`near_round_100`) skipped: a $100 step is not the same game on BTC vs ETH.

**Superseded on this date** (prior text kept above for history):

- 2026-09-02 / earlier statements that live and `parse_strategy` are close-only *for every atom* — superseded in part: close-only names remain close-only; structure atoms are OHLC on the same 5m series.
- 2026-09-02 "Universe remains the explicit ~50-name list" insofar as it freezes that list — superseded; a handful of structure names are added, still inside the 40–120 band.

### Amendment 2026-09-03 — double bottom / double top (pattern only)

This amendment does not rewrite original §§ 1–8 or prior amendments, including the 2026-09-03 structure-atom amendment above. It names one new **pattern** family. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged. **Still paper.** `GRADUATED_PAPER` meaning is unchanged.

**Already exist — not duplicated.** Trend following, breakout, and momentum already map to existing atoms:

- Momentum: `mom_*` (e.g. `mom_12b_gt3pc`)
- Breakout: `don_hi_*` (e.g. `don_hi_24`)
- Trend: `sma_stack_*` / `sma_abv_*` (e.g. `sma_stack_20_50_100`, `sma_abv_50`)

`mom_12b_gt3pc&don_hi_24` is already in the universe. This amendment does not add a second momentum / breakout / trend stack.

**New pattern atoms** (`hedge_fund/signals/structure.py`), same Williams fractal as `near_swing_*_k`, OHLC, no lookahead:

- `dbl_bot_k` — two confirmed swing lows. The second low **holds**: within `max(HOLD_PCT × first low, HOLD_ATR_MULT × ATR(14))` = `max(1.0% of the first low, 1 × ATR(14))` of the first (not a much lower low). Close has recovered off the second low (`close >` second swing low). The decision bar cannot be the unconfirmed pivot: `k` bars must exist to the right of the second swing (`j + k <= i`).
- `dbl_top_k` — two confirmed swing highs, second not much higher (same hold band), close has broken down (`close <` second swing high). Long-only book: **not** listed as a standalone long. The combinator has no NOT; we do not emit `dbl_top` longs or invent a NOT gate to use it as a veto.

**Universe** (still inside `UNIVERSE_TARGET_MAX` = 120): `dbl_bot_12`, `dbl_bot_12&sma_abv_50` (pattern + trend), `dbl_bot_12&don_lo_24`, `dbl_bot_12&sma_stack_20_50_100`, and `sma_stack_20_50_100&don_hi_24` (existing families, one missing AND). No head-and-shoulders, flags, triangles, or candlestick packs.

**Superseded on this date** (prior text kept above for history):

- 2026-09-03 structure-atom universe list insofar as it froze that handful — a few pattern names are added, still inside the 40–120 band.

### Amendment 2026-09-03 — 20-slot arena revoked

This amendment does not rewrite original §§ 1–8 or prior amendments. It removes the homemade live-slot cap. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 trades, all windows ≥ 0, beat B&H + `sma_stack`, paper 80 vs B&H). **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing paper accounts.

**No live-slot cap.** `MAX_ACTIVE_CHAMPIONS` is deleted. `promote_candidates` admits every 5m-qualified name that is not already in the active pool or `graduated.json`. Discovery/tournament replenish still runs while the universe has untested names — not only when `len(pool) < 20`. Prod's grandfathered names (31 at this writing) must not starve new admits such as `dbl_bot_*` / structure ANDs.

**What still bounds the book.** Universe size (`UNIVERSE_TARGET_MIN` / `UNIVERSE_TARGET_MAX`, ~40–120 names) is the combinatorial bound, not a live-slot cap. `DISCOVER_BATCH_SIZE = 30` is a sweep batch size, not an arena cap. Graduation at `TRADE_EVALUATION_LIMIT = 80` vs buy-and-hold is unchanged.

**Superseded on this date** (prior text kept above for history):

- 2026-09-01 "Small arena" `MAX_ACTIVE_CHAMPIONS = 20` and replenish-only-into-free-slots.
- 2026-09-02 / earlier 2026-09-03 text insofar as it freezes "arena 20" as the live rule.
- 2026-08-30 capacity 1000 insofar as any later text still treated a homemade cap as current.

### Amendment 2026-09-05 — LazyBear WaveTrend green-dot (not Market Cipher)

This amendment does not rewrite original §§ 1–8 or prior amendments. It names one new **oscillator** family on the same 5m OHLC series as live. Qual/live remain 5m (`QUAL_TIMEFRAME`), risk policy remains `rm_v1`, OOS gates are unchanged (30 trades, all windows ≥ 0, beat B&H + `sma_stack`, paper 80 vs B&H). No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions.

**Honesty.** Official Market Cipher (CF Strategies) is invite-only and not algo-ready. We do **not** scrape that Pine, do **not** webhook the VuManChu panel, and are **not affiliated** with Market Cipher. These atoms port the **public LazyBear WaveTrend** oscillator and the **VuManChu Cipher B–inspired green-dot long**: WT1 crosses above WT2 while WT2 is oversold. Credit LazyBear; inspired by the public green-dot family; not Market Cipher.

**Frozen defaults** (`hedge_fund/signals/wavetrend.py`) — LazyBear-classic, not VuManChu 9/12/3:

- Source = HLC3 = `(high + low + close) / 3` on the same 5m bars as live (not close-only; close is not silently used as high/low).
- Channel length `n1 = 10`, average `n2 = 21`, signal SMA = 4.
- `esa = EMA(HLC3, 10)`, `d = EMA(|HLC3 − esa|, 10)`, `CI = (HLC3 − esa) / (0.015 × d)`, `WT1 = EMA(CI, 21)`, `WT2 = SMA(WT1, 4)`.
- Oversold / overbought bands: −60 / +60 (LazyBear `osLevel1` / `obLevel1`).

**Closed-bar only.** Decision bar `i` is a closed 5m bar. Cross uses WT1/WT2 at `i−1` and `i` (both closed). No intrabar flicker, no Heikin-Ashi internal rewrite, no `request.security` higher-TF with lookahead.

**Long predicates only** (long-only book):

- `wt_cross_up_os` — WT1 crosses above WT2 while WT2 ≤ −60 (classic green-dot family).
- `wt_below_os` — WT2 still oversold (filter atom for ANDs; not a standalone universe name).
- `wt_cross_down_ob` — parsed for tests (WT1 crosses below WT2 while WT2 ≥ +60). **Not** a standalone long. No short-side longs.

**Universe** (still inside `UNIVERSE_TARGET_MIN` / `UNIVERSE_TARGET_MAX`, ~40–120): `wt_cross_up_os`, `wt_cross_up_os&sma_abv_50`, `wt_cross_up_os&sma_stack_20_50_100`, `wt_cross_up_os&don_lo_24`. Handful of ANDs, not a cartesian product. No MFI / CMF / VWAP money-flow atoms (live cycle still treats volume as not-honest for the universe; PROTOCOL/universe refuse MFI). No VuManChu Sommi flag/diamond, gold-dot kitchen sink, or divergence zoo in v1.

**Superseded on this date** (prior text kept above for history):

- 2026-09-03 universe list insofar as it froze that handful — a few WaveTrend names are added, still inside the 40–120 band.

### Amendment 2026-09-05 — 24/7 cycle and leftover-universe discovery

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 OOS trades, all windows ≥ 0, beat B&H + `sma_stack`, Sharpe ≥ 0.30, paper 80 vs B&H). No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. No new strategies.

**Night window revoked.** The k8s cycle sidecar and compose scheduler run `live_cycle.py` (tournament → `run_isolated` → `collect_live_results` → report) on every `CYCLE_INTERVAL_SECONDS = 300` tick, including 02:00 Europe/Stockholm. There is no 07–21 skip and no slower night cadence. Heartbeat is already continuous and does not open trades.

**Discovery drains leftovers.** Each tournament/replenish sweep evaluates **all** remaining untested universe names (not already in `champions.json` or `graduated.json`), still skipping near-duplicates of blocked names via `near_duplicate_key` / `untested_candidates`. The homemade `DISCOVER_BATCH_SIZE = 30` random sample is deleted. `MIN_BACKTEST_TRADES = 30` is the OOS trade floor, not a discovery sample size. Universe size (~40–120) remains the combinatorial bound. The cycle waits on tournament; there is no leftover sample as the product rule.

**Discovery buckets.** `GET /api/discovery/summary` is last-known tested / in-flight / leftover-untested (from `discovery_log.json`, `discovery_in_flight.json` written at batch start, champions, graduated). It does not claim a process is alive.

**Superseded on this date** (prior text kept above for history):

- 2026-09-02 "The sidecar still skips outside 07–21 Europe/Stockholm; that night window is unchanged."
- 2026-09-01 / 2026-09-03 `DISCOVER_BATCH_SIZE = 30` as a sweep batch size.

### Amendment 2026-09-07 — budgeted discovery, incremental log, honest last eval

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 OOS trades, all windows ≥ 0, beat B&H + `sma_stack`, Sharpe ≥ 0.30, paper 80 vs B&H). Cycle remains 24/7 (`CYCLE_INTERVAL_SECONDS = 300`); there is no 07–21 window. No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. No new strategies. The homemade `DISCOVER_BATCH_SIZE = 30` random sample stays deleted — this is not "shuffle 30 and ignore the rest."

**Why.** Evaluating every leftover untested name in one `live_cycle` tournament blocked `run_isolated` for hours. Mid-batch hang/kill wrote no log rows (`log_discovery_evaluations` ran only after the full leftover list). `/api/discovery/summary` `tested` is latest-eval-per-name, so a unique count of ~60 can look frozen while the same names retest. `last_tested_at` used the first latest-per-name row (oldest/alpha of a batch append), not the newest `tested_at`.

**Per-cycle budget.** Each tournament invocation from `live_cycle` evaluates a leftover slice, then returns so `run_isolated` can run in the same 300s cycle:

- At most `DISCOVER_CYCLE_MAX_NAMES` (4) names.
- Wall-clock `DISCOVER_CYCLE_TIME_BUDGET_SECONDS` (150s, ~2.5 minutes).
- Rotating cursor in `discovery_cursor.json` continues where the last cycle left off (fair drain over time).
- Still skip champions, graduated names, and near-duplicates (`near_duplicate_key` / `untested_candidates`).
- Prefer never-tested leftovers, then oldest tested. Names evaluated in the last `DISCOVER_RETEST_COOLDOWN_SECONDS` (24h) are skipped so we do not only thrash the same rejects.

The leftover universe still drains 24/7 across cycles. `MIN_BACKTEST_TRADES = 30` remains the OOS trade floor, not a discovery sample size.

**Incremental discovery log.** After **each** name finishes `evaluate_windows` + `qualification_decision`, append that one record to `discovery_log.json` immediately (newest-first, cap `DISCOVERY_LOG_CAP` = 1000). Do not wait for the leftover list. A mid-batch crash must still leave partial progress visible.

**In-flight honesty.** `discovery_in_flight.json` lists names **for this cycle's budget**, with `current` / `remaining` / `completed`, and shrinks as names complete. Cleared when the invocation finishes. If the pipeline tournament stamp is stale (started, no finish), the UI says discovery stuck / cycle overdue — not idle with a wrong last sweep.

**Summary / UX.** `GET /api/discovery/summary` `last_tested_at` / `last_strategy` are the **newest** `tested_at` across the log, not alphabetical/min among latest-per-name. Counts include universe size, champions, tested pass/fail (unique names), queued/untested, log rows, evals today, and last-eval age. `counts.tested` is unique strategies (latest eval per name), not "only N log rows ever." Champions DiscoveryBuckets banners a stale stamp or quiet leftover drain.

**Superseded on this date** (prior text kept above for history):

- 2026-09-05 "Discovery drains leftovers" insofar as it required each tournament/replenish sweep to evaluate **all** remaining untested universe names in one blocking invocation, and "the cycle waits on tournament" as a full leftover drain.
- 2026-09-05 discovery buckets insofar as `discovery_in_flight.json` was the full leftover list written only at batch start and the log was appended only after the full batch.

### Amendment 2026-09-07 addendum — lighter per-cycle discovery slice

This addendum does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 OOS trades, all windows ≥ 0, beat B&H + `sma_stack`, Sharpe ≥ 0.30, paper 80 vs B&H). Cycle remains 24/7 (`CYCLE_INTERVAL_SECONDS = 300`); there is no 07–21 window. No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. No new strategies. The homemade `DISCOVER_BATCH_SIZE = 30` random sample stays deleted — this is not "shuffle 30 and ignore the rest." Incremental log, rotating cursor, 24h retest cooldown, and stuck/overdue UX from the 2026-09-07 amendment stay.

**Why.** After PR #26 (`f13a0e4`) rolled, prod (`trading.runevibe.se`) crashed / timed out. Four names × full 5m walk-forward OOS (3×~90d windows, `rm_v1`) inside `DISCOVER_CYCLE_TIME_BUDGET_SECONDS = 150` was still too heavy for the pod (CPU/RAM) and could wedge or OOM the process before `run_isolated` and the dashboard finished the 300s tick.

**Live slice.** Each `live_cycle` tournament invocation still takes a leftover slice, then returns:

- At most `DISCOVER_CYCLE_MAX_NAMES` (1) name.
- Wall-clock `DISCOVER_CYCLE_TIME_BUDGET_SECONDS` (90s). Enough for one eval; most of the 300s stays for `run_isolated` and the web/API. If one name alone commonly exceeds ~90s, keep max names at 1 with this modest budget rather than raising names.

The leftover universe still drains 24/7 across cycles. `MIN_BACKTEST_TRADES = 30` remains the OOS trade floor, not a discovery sample size.

**Superseded on this date** (prior text kept above for history):

- 2026-09-07 "Per-cycle budget" insofar as it set `DISCOVER_CYCLE_MAX_NAMES` (4) and `DISCOVER_CYCLE_TIME_BUDGET_SECONDS` (150s, ~2.5 minutes).

### Amendment 2026-09-10 — fail once, never retest

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 OOS trades, all windows ≥ 0, beat B&H + `sma_stack`, Sharpe ≥ 0.30, paper 80 vs B&H). Cycle remains 24/7 (`CYCLE_INTERVAL_SECONDS = 300`); there is no 07–21 window. No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. No new strategies. The homemade `DISCOVER_BATCH_SIZE = 30` random sample stays deleted — this is not "shuffle 30 and ignore the rest." Per-cycle budget (1 name / ~90s), incremental log, rotating cursor, and stuck/overdue UX from the 2026-09-07 amendment stay. Passes still admit to champions.

**Why.** After the 2026-09-07 24h retest cooldown, a leftover reject stayed a leftover. `prioritize_leftovers` put it back in the drain queue once `DISCOVER_RETEST_COOLDOWN_SECONDS` (24h) elapsed. Prod wrote ~481 `discovery_log.json` rows for ~60 unique names. Discovery reads `crypto_history_5m.json` (does not fetch Binance per eval). Per-cycle budget remains 1 name / ~90s. OOS gates unchanged. Retesting rejects wasted the 24/7 drain.

**Fail once.** A name whose latest or any prior discovery evaluation did not qualify is permanently ineligible for another `evaluate_windows` / discovery run. Cycle-batch selection excludes any strategy that already has a non-qualified row in `discovery_log.json`. Existing prod fails are parked. `DISCOVER_RETEST_COOLDOWN_SECONDS` is deleted — it is not a re-eligibility timer.

**Never-tested leftovers still drain** 24/7 under the existing cycle budget (`DISCOVER_CYCLE_MAX_NAMES` = 1, `DISCOVER_CYCLE_TIME_BUDGET_SECONDS` = 90, rotating cursor, incremental log). Champions, graduated names, and near-duplicates of blocked names are still skipped. Already tested · rejected on `GET /api/discovery/summary` means parked forever, not "will come back after cooldown." There is no retest-queue / cooldown-as-re-eligibility semantic for fails.

**Superseded on this date** (prior text kept above for history):

- 2026-09-07 "Prefer never-tested leftovers, then oldest tested. Names evaluated in the last `DISCOVER_RETEST_COOLDOWN_SECONDS` (24h) are skipped so we do not only thrash the same rejects." insofar as cooled rejects re-entered the drain.
- 2026-09-07 addendum insofar as "24h retest cooldown … stay" kept cooldown as re-eligibility for fails.

### Amendment 2026-09-10 addendum — cheaper per-name discovery eval

This addendum does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 OOS trades, all windows ≥ 0, beat B&H + `sma_stack`, Sharpe ≥ 0.30, paper 80 vs B&H). Windows remain 3 × ~90 calendar days of native 5m (`QUAL_WINDOW_BARS` = 25920, `QUAL_STRIDE` = 1). Fail-once never-retest from the 2026-09-10 amendment stays. Cycle budget stays 1 name / ~90s. **Still paper.** Do not cull existing champions.

**Why.** Prod crashed under multi-name walk-forwards; PR #27 already cut the cycle to 1 name / ~90s on a 1 CPU / 1.5GiB sidecar. The remaining cost is one `evaluate_windows` name: loading `crypto_history_5m.json` (fetch file can hold unused SOL/XRP and years of extra bars) and a pure-Python sim of ~3×25920 bars × 2 symbols. Per-bar EMA and LazyBear WaveTrend rebuilt the whole prefix every bar (O(n²)). Pretty-printed `discovery_log.json` was re-read and re-written in full after each name.

**Behavior-preserving speed/RSS (this addendum):**

- Causal EMA series cache in `hedge_fund.signals.dynamic` (same SMA-seed recurrence; bar i ignores i+1…).
- Causal WaveTrend series cache: `wt_cross_up_os` / `wavetrend_at` index a once-built WT1/WT2 series instead of `wavetrend_series(prefix[:i+1])` every bar. Prefix-stable / no lookahead — same numbers.
- Qualification history load keeps only BTC/ETH and the last `window_size * n_windows` bars (the span `_window_slices` already used). Extra symbols and older bars are dropped after parse.
- Window OHLC is extracted once per window (train/test share arrays + cut). B&H / `sma_stack` OOS still computed once per cycle when a name is planned.
- `discovery_log.json` append is compact JSON (no indent). Same newest-first cap.

**Deliberately unchanged:** OOS gates, window length, native 5m stride, `rm_v1` fees/stops, train/test 70/30 split (test series does not see train bars), no silent downsample, no champion cull. Structure `dbl_bot` / `confirmed_swings` is still O(n²-ish) per name — not rewritten here because a faster swing scan could change which pivots fire.

**Superseded on this date:** none of the admit math. This addendum only names how one eval is computed, not what it must beat.

### Amendment 2026-09-11 — cautious per-cycle discovery bump

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 OOS trades, all windows ≥ 0, beat B&H + `sma_stack`, Sharpe ≥ 0.30, paper 80 vs B&H). Windows remain 3 × ~90 calendar days of native 5m (`QUAL_WINDOW_BARS` = 25920, `QUAL_STRIDE` = 1). Fail-once never-retest from the 2026-09-10 amendment stays. Cycle remains 24/7 (`CYCLE_INTERVAL_SECONDS = 300`); there is no 07–21 window. No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. No new strategies. The homemade `DISCOVER_BATCH_SIZE = 30` random sample stays deleted — this is not "shuffle 30 and ignore the rest." Incremental log, rotating cursor, and stuck/overdue UX from the 2026-09-07 amendment stay.

**Why.** PR #26 (`f13a0e4`) rolled 4 names / 150s and prod timed out / crashed. PR #27 cut the live slice to 1 name / ~90s so `run_isolated` and the web/API kept the rest of the 300s tick. PR #28 made one `evaluate_windows` cheaper (causal EMA / WaveTrend series cache, trim `crypto_history_5m.json`, compact discovery log) but left the cycle budget at 1 / 90s. That headroom should drain more never-tested leftovers — not sit unused, and not jump back to the crash settings.

**Live slice.** Each `live_cycle` tournament invocation still takes a leftover slice, then returns:

- At most `DISCOVER_CYCLE_MAX_NAMES` (2) names.
- Wall-clock `DISCOVER_CYCLE_TIME_BUDGET_SECONDS` (120s). Enough for two cheaper evals; most of the 300s stays for `run_isolated` and the web/API (~180s remaining). Not 4 names. Not 150s.

The leftover universe still drains 24/7 across cycles. `MIN_BACKTEST_TRADES = 30` remains the OOS trade floor, not a discovery sample size. A non-qualified eval still parks that name forever.

**Superseded on this date** (prior text kept above for history):

- 2026-09-07 addendum "Live slice" insofar as it set `DISCOVER_CYCLE_MAX_NAMES` (1) and `DISCOVER_CYCLE_TIME_BUDGET_SECONDS` (90s).
- 2026-09-10 insofar as "Per-cycle budget (1 name / ~90s)" and "cycle budget stays 1 name / ~90s" named the live defaults.

### Amendment 2026-09-11 — structure-window leftovers (never-tested names)

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 OOS trades, all windows ≥ 0, beat B&H + `sma_stack`, Sharpe ≥ 0.30, paper 80 vs B&H). Windows remain 3 × ~90 calendar days of native 5m (`QUAL_WINDOW_BARS` = 25920, `QUAL_STRIDE` = 1). Fail-once never-retest from the 2026-09-10 amendment stays — a fail parks that name forever; this batch is **new names only**, one shot each. Cycle budget stays 2 names / ~120s from the same-date discovery bump. Cycle remains 24/7. No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. Do not re-enable 24h retests of the parked leftover list.

**Why.** After PR #28 fail-once, prod leftover discovery was empty: eligible=0, untested=0, rejected_parked=60, tested_pass=0. Champions and graduated stay. The parked 60 are not retested. Discovery needs never-tested parseable names whose `near_duplicate_key` does not collapse onto those fails.

**What was added** (`NEW_STRUCTURE_ANDS` in `hedge_fund/trading/universe.py`, still inside `UNIVERSE_TARGET_MIN` / `UNIVERSE_TARGET_MAX`, ~40–120). Existing 5m dip/mom atoms ANDed with Donchian / near-swing / double-bottom at lookbacks that do not share `near_duplicate_key` with the parked 24/12 handful. `near_swing_hi` (already parsed) is used as a breakout tag. Handful of ANDs, not a cartesian product:

- Dip at unused support: `dip_6b_lt2pc&don_lo_12`, `dip_12b_lt3pc&don_lo_12`, `dip_24b_lt5pc&don_lo_36`, `dip_6b_lt2pc&near_swing_lo_18`, `dip_12b_lt3pc&near_swing_lo_12`, `dip_24b_lt5pc&near_swing_lo_18`
- Momentum at unused breakout: `mom_6b_gt2pc&don_hi_12`, `mom_12b_gt3pc&don_hi_12`, `mom_12b_gt3pc&don_hi_36`, `mom_24b_gt5pc&don_hi_36`, `mom_6b_gt2pc&near_swing_hi_12`, `mom_12b_gt3pc&near_swing_hi_18`, `mom_24b_gt5pc&near_swing_hi_12`
- Double bottom at unused fractal `k`: `dbl_bot_18`, `dbl_bot_18&sma_abv_50`, `dbl_bot_18&don_lo_24`, `dbl_bot_24`, `dbl_bot_24&sma_stack_20_50_100`, `dbl_bot_18&ema_abv_50`
- Standalone Donchian at unused N (1h / 3h on 5m): `don_hi_12`, `don_hi_36`

**Refused.** No WaveTrend / Market Cipher clones (`wt_*` already failed OOS — do not add more). No MFI until honest 5m volume. No head-and-shoulders, flags, triangles, candlestick encyclopedia, FVGs, or order blocks. No 24h retest of the parked 60.

**Superseded on this date** (prior text kept above for history):

- 2026-09-05 / 2026-09-07 / 2026-09-10 / same-date cycle-bump text insofar as "No new strategies" froze the universe list. Cycle budget, fail-once, and OOS gates are not superseded.

### Amendment 2026-09-11 — auto-refill never-tested names (no PR per batch)

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 OOS trades, all windows ≥ 0, beat B&H + `sma_stack`, Sharpe ≥ 0.30, paper 80 vs B&H). Windows remain 3 × ~90 calendar days of native 5m (`QUAL_WINDOW_BARS` = 25920, `QUAL_STRIDE` = 1). Fail-once never-retest from the 2026-09-10 amendment stays — a fail parks that name forever; each auto-refilled name gets **one shot**. Cycle budget stays 2 names / ~120s. Cycle remains 24/7. No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. Do not retest parked fails.

**Why.** After `NEW_STRUCTURE_ANDS` (#30) the 21 new names all failed in one morning. Prod: universe=88, rejected_parked=81, eligible=0, untested=0. The 300s live cycle still ran but tournament had nothing to evaluate, so `last_tested_at` froze. Opening another leftover-list PR each time eligible hits 0 is not the drain.

**Pending queue sidecar, not a bigger static list.** `generate_universe()` stays inside `UNIVERSE_TARGET_MIN` / `UNIVERSE_TARGET_MAX` (~40–120). Raising that cap and dumping the recipe into the compiled list would re-create a combinatorial clone dump and bloat leftover scans. Instead `hedge_fund.trading.refill` walks a **bounded** recipe of allowed atoms and appends the next `DISCOVERY_REFILL_BATCH_SIZE` (16) parseable, non-near-duplicate names to `discovery_extended.json` on the paper-state PVC. After a refill, never-tested extras are one handful — the 2 / 120s eval budget is unchanged, so the 1 CPU / 1.5GiB cycle sidecar does not OOM. Recipe generation is string-only (no history load).

**When.** Each `discover_and_qualify` invocation: if the never-tested eligible slice is empty or smaller than this cycle's name cap (`DISCOVER_CYCLE_MAX_NAMES` = 2 — "about to be" empty), append the next batch and include those names in the leftover drain. GET `/api/discovery/summary` is still last-known (it does not write the sidecar).

**Recipe (allowed atoms only).** Small OHLC structure atoms (`don_hi_N` / `don_lo_N` / `near_swing_hi_N` / `near_swing_lo_N` / `dbl_bot_N`) with N in `{6,12,18,24,30,36,42,48,54,60,66,72}` (multiples of 6 so `near_duplicate_key` is the identity) **AND** existing 5m dip/mom/sma/ema/rsi filters (`dip_6b_lt2pc`, `dip_12b_lt3pc`, `dip_24b_lt5pc`, `mom_6b_gt2pc`, `mom_12b_gt3pc`, `mom_24b_gt5pc`, `sma_abv_50`/`100`/`200`, `ema_abv_50`, `sma_stack_20_50_100`, `rsi_14_>50`). Dip tags support; mom tags breakout. A few 3-atom ANDs (structure + dip/mom + `sma_abv_50`) exist so more than one batch can be produced over time. The stream is finite and well under a thousand names before near-dup collapse — not a cartesian of every atom.

**Skipped.** Exact names and `near_duplicate_key` collisions against champions, graduated, the static universe, already-emitted extended names, and any `discovery_log.json` row (pass or fail). Refused families stay out: no H&S / flags / triangles / candlestick encyclopedia / chart_patterns zoo; no Market Cipher scrape; no new `wt_*` WaveTrend spam; no MFI until honest 5m volume; no `dbl_top` longs; no `daily()`/`h1()`/`m5()` wrappers.

**When the recipe is exhausted.** Refill returns nothing. Discovery idles honestly (eligible=0) until the recipe is amended. That is not a silent retest of parked fails.

**Superseded on this date** (prior text kept above for history):

- Same-date structure-window leftovers insofar as a new never-tested batch required a human PR / a frozen `NEW_STRUCTURE_ANDS` list. Fail-once, cycle budget, OOS gates, and the static 40–120 compiled-list band are not superseded.

### Amendment 2026-09-11 addendum — single-name discovery slice

This addendum does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 OOS trades, all windows ≥ 0, beat B&H + `sma_stack`, Sharpe ≥ 0.30, paper 80 vs B&H). Windows remain 3 × ~90 calendar days of native 5m (`QUAL_WINDOW_BARS` = 25920, `QUAL_STRIDE` = 1). Fail-once never-retest from the 2026-09-10 amendment stays. Auto-refill (`DISCOVERY_REFILL_BATCH_SIZE` = 16, `discovery_extended.json`) from the same-date auto-refill amendment stays. Cycle remains 24/7 (`CYCLE_INTERVAL_SECONDS` = 300); there is no 07–21 window. No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. No new strategies. The homemade `DISCOVER_BATCH_SIZE` = 30 random sample stays deleted — this is not "shuffle 30 and ignore the rest." Incremental log, rotating cursor, and stuck/overdue UX from the 2026-09-07 amendment stay.

**Why.** After PR #29 the live slice was 2 names / ~120s. Prod (`trading.runevibe.se`) overloaded: two walk-forward OOS evals in one 300s tick still crowded `run_isolated` and the web/API. Discovery must evaluate **only one name at a time** per cycle. Do not return to the 4 / 150s crash settings.

**Live slice.** Each `live_cycle` tournament invocation still takes a leftover slice, then returns:

- At most `DISCOVER_CYCLE_MAX_NAMES` (1) name.
- Wall-clock `DISCOVER_CYCLE_TIME_BUDGET_SECONDS` (90s). Enough for one eval; most of the 300s stays for `run_isolated` and the web/API (~210s remaining). Not 2 names. Not 4 names. Not 150s.

The leftover universe still drains 24/7 across cycles. `MIN_BACKTEST_TRADES` = 30 remains the OOS trade floor, not a discovery sample size. A non-qualified eval still parks that name forever.

**Auto-refill still compares eligible to the live name cap.** Each `discover_and_qualify` invocation refills when the never-tested eligible slice is empty or smaller than `DISCOVER_CYCLE_MAX_NAMES` (now 1). Eligible = 0 → append the next `DISCOVERY_REFILL_BATCH_SIZE` handful. Eligible = 1 already feeds this cycle's slice — do not refill. `DISCOVERY_REFILL_BATCH_SIZE` (16) is unchanged.

**Superseded on this date** (prior text kept above for history):

- Same-date "cautious per-cycle discovery bump" "Live slice" insofar as it set `DISCOVER_CYCLE_MAX_NAMES` (2) and `DISCOVER_CYCLE_TIME_BUDGET_SECONDS` (120s).
- Same-date structure-window leftovers / auto-refill insofar as "Cycle budget stays 2 names / ~120s" and "`DISCOVER_CYCLE_MAX_NAMES` = 2" named the live defaults. Fail-once, auto-refill trigger vs the name cap, OOS gates, and window lengths are not superseded.

### Amendment 2026-09-12 — discovery farm on the Windows PC

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 OOS trades, all windows ≥ 0, beat B&H + `sma_stack`, Sharpe ≥ 0.30, paper 80 vs B&H). Windows remain 3 × ~90 calendar days of native 5m (`QUAL_WINDOW_BARS` = 25920, `QUAL_STRIDE` = 1). Fail-once never-retest from the 2026-09-10 amendment stays. Auto-refill (`DISCOVERY_REFILL_BATCH_SIZE` = 16, `discovery_extended.json`) stays. Cycle remains 24/7 (`CYCLE_INTERVAL_SECONDS` = 300). No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. No pattern zoo / WaveTrend spam / MFI. The homemade `DISCOVER_BATCH_SIZE` = 30 random sample stays deleted.

**Why.** The prod k8s cycle sidecar (1 CPU / 1.5GiB) overloaded under discovery walk-forwards. Auto-refill + 1 name / ~90s still fought `run_isolated` and the web/API. Discovery must leave the cluster.

**Cluster `live_cycle`.** Tournament / `discover_and_qualify` is **off by default**. Set `DISCOVERY_ON_CYCLE=0` or `PAPER_DISCOVERY_MODE=off` (unset also means off). The sidecar still runs `run_isolated` → `collect_live_results` → report on every 300s tick. Heartbeat and the web/API stay. Re-enable in-cycle discovery only with `DISCOVERY_ON_CYCLE=1` / `PAPER_DISCOVERY_MODE=on` (dev / emergency). The 1 name / ~90s slice remains the rule **if** someone turns tournament back on inside `live_cycle`. It is not the Windows farm budget.

**Windows discovery worker.** Alexander's home PC (`jensa`, i5-6600K / 16GB / GTX 1070) is the discovery farm. GPU is unused (no CUDA rewrite). `scripts/discovery_worker.py` (Docker Compose preferred; `python scripts/discovery_worker.py --workers N` also works) uses a **local** `PAPER_STATE` with `crypto_history_5m.json` (fetch via `scripts/fetch_history.py`). Same fail-once / auto-refill / OOS gates / `rm_v1` / 5m windows as `scripts/tournament_engine.py`. Modest parallelism: 2–4 CPU workers.

**Results land on prod.** The worker POSTs evaluations (and qualified admits) to `https://trading.runevibe.se/api/discovery/ingest`. Nginx already proxies `/api/`. The route is protected by `PAPER_DISCOVERY_INGEST_TOKEN` (shared secret from env / k8s secret `paper-discovery-ingest`). Fail-closed: missing token → ingest disabled (503). Fail-once on the server: an already-logged name is skipped. Qualified names are admitted the same way as `replenish_and_evaluate`. Existing champions are never removed. Do not require a long-lived kubectl tunnel.

**Superseded on this date** (prior text kept above for history):

- 2026-09-05 / 2026-09-07 / 2026-09-11 text insofar as each `live_cycle` tick **must** run tournament / leftover walk-forwards on the k8s sidecar. Fail-once, auto-refill, OOS gates, window lengths, and the 1 / 90s *if-enabled* slice are not superseded.

### Amendment 2026-09-12 — more structure-AND recipe names (farm refill)

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 OOS trades, all windows ≥ 0, beat B&H + `sma_stack`, Sharpe ≥ 0.30, paper 80 vs B&H). Windows remain 3 × ~90 calendar days of native 5m (`QUAL_WINDOW_BARS` = 25920, `QUAL_STRIDE` = 1). Fail-once never-retest from the 2026-09-10 amendment stays — a fail parks that name forever; each auto-refilled name gets **one shot**. Auto-refill (`DISCOVERY_REFILL_BATCH_SIZE` = 16, `discovery_extended.json`) stays. Discovery walk-forwards stay on the Windows farm (`scripts/discovery_worker.py` → `/api/discovery/ingest`); cluster `live_cycle` keeps discovery off. Cycle remains 24/7. No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. Do not retest parked fails.

**Why.** Parser already has OHLC structure atoms (`don_hi_N` / `don_lo_N` / `near_swing_hi_N` / `near_swing_lo_N` / `dbl_bot_N`). The 2026-09-11 recipe only ANDed a slice of those (dip×support, mom×breakout, trend×`don_hi`, same-N `dbl_bot`). `don_lo` and `near_swing_*` are already the near-level tags (documented near-band). Those combinations and lookbacks past 72 were underused. Opening another frozen `NEW_STRUCTURE_ANDS` PR is not the drain — the farm already walks `iter_recipe_names`.

**What was added** (`hedge_fund.trading.refill.iter_recipe_names`). Still a bounded stream, well under a thousand names, not a cartesian of every atom. Legacy 2026-09-11 families stay first so a mid-drain farm continues; new families follow. `STRUCTURE_NS` is `{6,12,18,24,30,36,42,48,54,60,66,72,84,96}` (84 = 7h and 96 = 8h on 5m; still multiples of 6 so `near_duplicate_key` is the identity). New AND families, existing 5m dip/mom/trend filters only:

- Standalone near-level tags: `don_lo_N`, `near_swing_lo_N`, `near_swing_hi_N`
- Trend at support / near swing (`LEVEL_TRENDS` = `sma_abv_50`, `ema_abv_50`, `sma_stack_20_50_100`): `sma_abv_50&don_lo_N`, `ema_abv_50&near_swing_lo_N`, `sma_stack_20_50_100&near_swing_hi_N`, plus the other LEVEL_TRENDS pairings
- 3-atom parity: `mom_*&near_swing_hi_N&sma_abv_50` / `&ema_abv_50` (mom×`don_hi` already had the sma 3-atom); `dip_*&don_lo_N&ema_abv_50`, `dip_*&near_swing_lo_N&ema_abv_50`, `mom_*&don_hi_N&ema_abv_50`
- `dbl_bot` extras already in TREND_FILTERS: `dbl_bot_N&rsi_14_>50`, `dbl_bot_N&sma_abv_100`

**Static list unchanged.** `generate_universe()` / `NEW_STRUCTURE_ANDS` stay inside `UNIVERSE_TARGET_MIN` / `UNIVERSE_TARGET_MAX` (~40–120). The farm picks new names from the recipe via `discovery_extended.json`.

**Skipped.** Exact names and `near_duplicate_key` collisions against champions, graduated, the static universe, already-emitted extended names, and any `discovery_log.json` row (pass or fail). Refused families stay out: no H&S / flags / triangles / engulfing / hammer / doji / morning_star / evening_star / candlestick encyclopedia / chart_patterns zoo; no Market Cipher scrape; no new `wt_*` WaveTrend spam; no MFI until honest 5m volume; no `dbl_top` longs; no `daily()`/`h1()`/`m5()` wrappers.

**When the recipe is exhausted.** Refill returns nothing. Discovery idles honestly (eligible=0) until the recipe is amended again. That is not a silent retest of parked fails.

**Superseded on this date** (prior text kept above for history):

- Same-date auto-refill / farm text insofar as `STRUCTURE_NS` froze at 72 and the recipe omitted standalone / trend×support / near-swing 3-atom families. Fail-once, farm ingest, cycle budget, OOS gates, and the static 40–120 compiled-list band are not superseded.

### Amendment 2026-09-12 — farm Start/Stop from the Discovery page

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged. Cluster `live_cycle` stays live-only (`DISCOVERY_ON_CYCLE=0`). Walk-forwards stay on the Windows farm. **Still paper.** Do not cull existing champions. Do not retest parked fails.

**Why.** Killing `discovery_worker.py` on jensa to free CPU for games left orphaned multiprocessing children. Start from the website must work without SSH as long as the process was left running.

**Durable flag.** Prod stores `state/discovery_farm.json` (`enabled` true/false). `POST /api/discovery/farm` is **authenticated** with the same `PAPER_DISCOVERY_INGEST_TOKEN` as ingest (`Authorization: Bearer`, `X-Discovery-Token`, or `X-Paper-Discovery-Token`). No/wrong token → 401. The public site cannot pause the farm. The UI prompts once and keeps the token in `sessionStorage` for that tab — it is not in the JS bundle. `GET /api/discovery/summary` includes `farm` (status Running / Paused / Worker idle / Worker not seen, last heartbeat) and stays public. Missing file defaults to enabled.

**Worker.** Before each batch, `scripts/discovery_worker.py` polls prod. When paused: clear in-flight, post a heartbeat, sleep 10–30s, do not exit. When enabled again, resume batches. A batch already running may finish first.

**UI.** `/discovery` has Start / Stop plus status. Champions teaser is one line + link. If the worker is unseen, Start will not relaunch it — relaunch on jensa.

**Superseded on this date** (prior text kept above for history):

- Same-date Windows farm text insofar as it implied the only control surface was SSH / killing the process. Fail-once, ingest, OOS gates, and `DISCOVERY_ON_CYCLE=0` are not superseded.

### Amendment 2026-09-12 — longer structure lookbacks and leftover AND families

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`, OOS gates are unchanged (30 OOS trades, all windows ≥ 0, beat B&H + `sma_stack`, Sharpe ≥ 0.30, paper 80 vs B&H). Windows remain 3 × ~90 calendar days of native 5m (`QUAL_WINDOW_BARS` = 25920, `QUAL_STRIDE` = 1). Fail-once never-retest from the 2026-09-10 amendment stays — a fail parks that name forever; each auto-refilled name gets **one shot**. Auto-refill (`DISCOVERY_REFILL_BATCH_SIZE` = 16, `discovery_extended.json`) stays. Discovery walk-forwards stay on the Windows farm (`scripts/discovery_worker.py` → `/api/discovery/ingest`); cluster `live_cycle` keeps discovery off. Cycle remains 24/7. No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. Do not retest parked fails.

**Why.** The Windows farm (`jensa`) was healthy (heartbeat idle, farm enabled) but eligible=0: ~917 unique names tested, 0 passes. `maybe_refill_discovery` / `next_refill_batch` returned an empty batch because the morning `STRUCTURE_NS` (6…96) structure-AND recipe was exhausted versus taken/tested. Opening another frozen leftover-list PR is not the drain.

**What was added** (`hedge_fund.trading.refill.iter_recipe_names`). Still a bounded stream — a couple of thousand names, not tens of thousands of near-clones. Legacy 2026-09-11 families stay first; morning near-level families stay second; leftover TREND / `ema_stack` / 3-atom families follow. `STRUCTURE_NS` is `{6,12,18,24,30,36,42,48,54,60,66,72,84,96,108,120,132,144,156,168,180,192}` (108 = 9h through 192 = 16h on 5m; step of 12 after 72; still multiples of 6 so `near_duplicate_key` is the identity). New AND families, atoms already in `parse_strategy` / `_ALLOWED_ATOM_RES` only:

- Longer unused Donchian / swing / `dbl_bot` lookbacks on the existing dip/mom/trend / near-level families
- Leftover `TREND_FILTERS` on support / near-swing (`sma_abv_100` / `sma_abv_200` / `rsi_14_>50` were `don_hi`-only) plus `ema_abv_100` (parser + static universe; morning recipe skipped it)
- `ema_stack_20_50_100` × `don_hi` / `don_lo` / `near_swing_*` (`ema_stack` already matched the allow-list)
- `dbl_bot_N&near_swing_lo_N` (same fractal as the pattern atom) and leftover `dbl_bot` trends (`sma_abv_200`, `ema_abv_100`, `ema_stack_20_50_100`)
- 3-atom extras: dip×support and mom×breakout with `sma_abv_100` / `sma_stack_20_50_100` (morning already had `sma_abv_50` / `ema_abv_50`)

**Static list unchanged.** `generate_universe()` / `NEW_STRUCTURE_ANDS` stay inside `UNIVERSE_TARGET_MIN` / `UNIVERSE_TARGET_MAX` (~40–120). The farm picks new names from the recipe via `discovery_extended.json`, one `DISCOVERY_REFILL_BATCH_SIZE` handful at a time.

**Skipped.** Exact names and `near_duplicate_key` collisions against champions, graduated, the static universe, already-emitted extended names, and any `discovery_log.json` row (pass or fail). Refused families stay out: no H&S / flags / triangles / engulfing / hammer / doji / morning_star / evening_star / candlestick encyclopedia / chart_patterns zoo; no Market Cipher scrape; no new `wt_*` WaveTrend spam; no MFI until honest 5m volume; no `dbl_top` longs; no `daily()`/`h1()`/`m5()` wrappers.

**When the recipe is exhausted.** Refill returns nothing. Discovery idles honestly (eligible=0) until the recipe is amended again. That is not a silent retest of parked fails.

**Superseded on this date** (prior text kept above for history):

- Same-date "more structure-AND recipe names" insofar as `STRUCTURE_NS` froze at 96 and the recipe omitted leftover TREND × support / `ema_stack` / extra 3-atom families. Fail-once, farm ingest, cycle budget, OOS gates, and the static 40–120 compiled-list band are not superseded.

### Amendment 2026-09-12 — all-windows OOS veto dropped

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`. Windows remain 3 × ~90 calendar days of native 5m (`QUAL_WINDOW_BARS` = 25920, `QUAL_STRIDE` = 1). Fail-once never-retest from the 2026-09-10 amendment stays — a fail on the remaining gates parks that name forever; this is **not** a walk-forward retest. Beat buy-and-hold and beat `sma_stack` stay. Sharpe ≥ 0.30 and `MIN_BACKTEST_TRADES` = 30 stay. Discovery walk-forwards stay on the Windows farm (`scripts/discovery_worker.py` → `/api/discovery/ingest`); cluster `live_cycle` keeps discovery off. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. No named candlesticks. No WaveTrend spam. No MFI.

**Why.** Alexander chose to soften the all-windows non-negative OOS rule so more names can get a running paper book, rather than a few "perfect window" passes. A name like `dbl_bot_120` (Sharpe 0.58, 296 OOS trades, +946 test PnL) was parked because one window was empty/neg even when the aggregate OOS was fine. Prefer more paper tests.

**New admit bar.** `qualification_decision` (shared by `scripts/tournament_engine.py` and the Windows worker via `evaluate_strategy_record`) uses **aggregate OOS only**:

- `len(windows) == expected_windows` (the three slices exist; `regimes_tested` is the stored equivalent)
- total OOS trades ≥ `MIN_BACKTEST_TRADES` (30)
- average OOS Sharpe ≥ `MIN_BACKTEST_SHARPE` (0.30)
- `tot_test_pnl` > `bh_oos_pnl`
- `tot_test_pnl` > `sma_stack_oos_pnl`

Do **not** fail on a per-window skipped / empty / negative test. Do **not** emit `window[i] failed/skipped/neg/empty` or `not all windows non-negative` as `fail_reasons`. `all_windows_nonneg` stays as a diagnostic flag so the log/UI can still show that a window was empty.

**Re-qualify from stored aggregates.** Ingest may flip a parked `discovery_log.json` row to qualified and admit it when those stored fields (`sharpe`, `trades`, `test_pnl`, `bh_oos_pnl`, `sma_stack_oos_pnl`, `regimes_tested`) now pass — no second walk-forward. Names that still lose to B&H / sma, miss Sharpe, or miss trade count stay parked (fail-once). A dry count on current prod-like logs: 0 existing names pass after this change alone (`dbl_bot_120` still loses to B&H).

**Superseded on this date** (prior text kept above for history):

- 2026-09-01 / 2026-09-02 and later "OOS gates are unchanged" text insofar as they required every window's test PnL ≥ 0 or treated a skipped/empty/negative window as a fail. Fail-once, beat-B&H, beat-`sma_stack`, Sharpe 0.30, 30 OOS trades, 5m, and `rm_v1` are not superseded.

### Amendment 2026-09-12 — wider dip/mom mint bases and continuation ANDs

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`. OOS gates are unchanged: aggregate OOS only, 30 OOS trades, Sharpe ≥ 0.30, beat buy-and-hold, beat `sma_stack`. All-windows non-negative is diagnostic only. Fail-once never-retest from the 2026-09-10 amendment stays — a fail parks that name forever; each auto-refilled name gets **one shot**. Auto-refill (`DISCOVERY_REFILL_BATCH_SIZE` = 16, `discovery_extended.json`) stays. Discovery walk-forwards stay on the Windows farm (`scripts/discovery_worker.py` → `/api/discovery/ingest`); cluster `live_cycle` keeps discovery off. Cycle remains 24/7. No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. Do not retest parked fails.

**Why.** Prod 2026-09-12 (~19:35Z): ~1000 unique tested, `tested_pass` = 0 natural qualifies. Fail mix (diagnosis for minting, not a request to lower bars): beat-B&H ~100%, Sharpe below ~95%, trades below ~49%, beat `sma_stack` ~17%. Only three near-misses clear Sharpe ≥ 0.30 and trades ≥ 30; all fail beat-B&H with negative OOS PnL while B&H OOS ≈ +2122 (`mom_6b_gt2pc` family and `dbl_bot_132`). High-Sharpe structure ANDs often under-trade (trades ≪ 30). The farm on `jensa` is running (eligible ~12); auto-refill exists. Bottleneck is recipe/search quality under the frozen gate. `DIP_FILTERS` / `MOM_FILTERS` were only three names each; the 3×3 is too narrow and too mean-reversion-heavy (dip×support) for a strong B&H OOS window. Parser regex already allows `mom_\\d+b_gt\\d+pc` and `dip_\\d+b_lt\\d+pc`.

**What was added** (`hedge_fund.trading.refill.iter_recipe_names`). Still a bounded stream — a few thousand names, not tens of thousands of near-clones. Continuation / wide dip-mom families are first so a mid-drain farm mints them on the next dry refill. Legacy 2026-09-11 families, morning near-level, and leftover TREND / `ema_stack` / 3-atom families stay in the stream (frozen 3×3 dip/mom). `STRUCTURE_NS` is unchanged (`{6…192}`). New mint bases and AND families, atoms already in `parse_strategy` / `_ALLOWED_ATOM_RES` only:

- `DIP_FILTERS_WIDE`: `dip_12b_lt2pc`, `dip_18b_lt2pc`, `dip_24b_lt2pc`, `dip_36b_lt2pc`, `dip_48b_lt2pc`, `dip_36b_lt4pc` (shallower / longer than the crash-dip 3×3; `near_duplicate_key` does not collapse onto `dip_6b_lt2pc` / `dip_12b_lt3pc` / `dip_24b_lt5pc`)
- `MOM_FILTERS_WIDE`: `mom_12b_gt2pc`, `mom_18b_gt2pc`, `mom_24b_gt2pc`, `mom_36b_gt2pc`, `mom_48b_gt2pc`, `mom_72b_gt2pc`, `mom_36b_gt4pc`, `mom_48b_gt6pc` (longer lookback, modest `%` so names stay in a grind-up; not another `mom_6b_gt2pc` clone)
- Continuation 2-atoms: wide mom × `don_hi_N` / `near_swing_hi_N`; shallow dip × `don_hi_N` / `near_swing_hi_N` (pullback-then-breakout, not dip×support)
- Short MA × breakout: `sma_abv_20` / `ema_abv_20` × `don_hi_N` / `near_swing_hi_N` (more time above than `sma_abv_200`)
- Loose 3-atoms: wide 2% mom or shallow 2% dip × `don_hi_N` × `sma_abv_20`

**Why this should help under the frozen gate.** Beat-B&H is the near-universal fail on a +2122 B&H OOS: mean-reversion sits out the trend and short-horizon mom prints negative PnL. Longer/shallower mom and continuation ANDs participate in the upside. 2-atoms (and only loose 3-atoms) aim at the trades ≥ 30 floor that tight structure ANDs miss. Sharpe is still the honest 0.30 bar — we do not mint a pattern zoo to game it.

**Static list unchanged.** `generate_universe()` / `NEW_STRUCTURE_ANDS` stay inside `UNIVERSE_TARGET_MIN` / `UNIVERSE_TARGET_MAX` (~40–120). The farm picks new names from the recipe via `discovery_extended.json`, one `DISCOVERY_REFILL_BATCH_SIZE` handful at a time.

**Skipped.** Exact names and `near_duplicate_key` collisions against champions, graduated, the static universe, already-emitted extended names, and any `discovery_log.json` row (pass or fail). Refused families stay out: no H&S / flags / triangles / engulfing / hammer / doji / morning_star / evening_star / candlestick encyclopedia / chart_patterns zoo; no Market Cipher scrape; no new `wt_*` WaveTrend spam; no MFI until honest 5m volume; no `dbl_top` longs; no `daily()`/`h1()`/`m5()` wrappers.

**When the recipe is exhausted.** Refill returns nothing. Discovery idles honestly (eligible=0) until the recipe is amended again. That is not a silent retest of parked fails.

**How to evaluate after merge.** Natural `tested_pass` / unique tested, plus the fail-reason mix (beat-B&H, Sharpe, trades, beat `sma_stack`). Do not change gate constants to move those numbers.

**Superseded on this date** (prior text kept above for history):

- Same-date "longer structure lookbacks and leftover AND families" insofar as `DIP_FILTERS` / `MOM_FILTERS` froze at three names each and the recipe omitted continuation / wide-base families. Fail-once, farm ingest, cycle budget, OOS gates, and the static 40–120 compiled-list band are not superseded.

### Amendment 2026-09-12 — multi-year walk-forward calendar coverage

This amendment does not rewrite original §§ 1–8 or prior amendments. Qual/live remain 5m, risk policy remains `rm_v1`. OOS **thresholds** are unchanged: `MIN_BACKTEST_TRADES` = 30, `MIN_BACKTEST_SHARPE` = 0.30, must beat buy-and-hold, must beat `sma_stack`, all-windows non-negative is diagnostic only, fail-once never-retest stays. Discovery walk-forwards stay on the Windows farm (`scripts/discovery_worker.py` → `/api/discovery/ingest`); cluster `live_cycle` keeps discovery off (`DISCOVERY_ON_CYCLE=0`). Do not raise the k8s cycle discovery budget — longer tape is a jensa cost. Cycle remains 24/7. No live-slot cap. **Still paper.** `GRADUATED_PAPER` meaning is unchanged. Do not cull existing champions. Do not retest parked fails.

**Why.** Alexander wants discovery OOS tested on **years** of 5m history, not ~280 days. `crypto_history_5m.json` on jensa was ~81k bars (~281 days) because `scripts/fetch_history.py` defaulted to `QUAL_WINDOW_BARS * QUAL_N_WINDOWS + slack` and the page loop capped at `pages < 300`. Qual loaded only the last `QUAL_WINDOW_BARS * QUAL_N_WINDOWS` bars. With `QUAL_N_WINDOWS` = 3 and `QUAL_WINDOW_DAYS` = 90 that was ~270 calendar days — too short for a multi-year tape.

**What changed.** More sequential ~90d windows, same per-window size (honest walk-forward: train/test split inside each window, chronological, no single giant in-sample):

- `QUAL_N_WINDOWS` = 8 (was 3)
- `QUAL_WINDOW_DAYS` = 90 (unchanged)
- `QUAL_WINDOW_BARS` = 25920 (unchanged; 90 × 24 × 12)
- `QUAL_COVERAGE_DAYS` = 720 (8 × 90)
- `QUAL_STRIDE` = 1 (unchanged)

`_load_qual_history` / the Windows worker still keep the last `window_size * n_windows` bars. That pattern scales with the constants: more windows → more calendar tape loaded from the same file; extra years on disk past 720d are trimmed.

**Fetch.** `scripts/fetch_history.py` `_DEFAULT_BARS` still tracks `QUAL_WINDOW_BARS * QUAL_N_WINDOWS + 3000` (~210k five-minute bars for the 8 × 90d span). The pagination cap is `HIST_FETCH_PAGE_CAP` = 2500 (was 300) so a multi-year deep fetch is possible (~600k bars / ~5y). `scripts/fetch_history_5m.py` uses the same 2500 page cap. Re-run fetch on jensa so `crypto_history_5m.json` actually covers the new span.

**What did not change.** Sharpe 0.30, 30 OOS trades, beat B&H, beat `sma_stack`, 5m, `rm_v1`, all-windows diagnostic only, fail-once. Parked 3-window evals stay parked — this is not a re-walk of the leftover list. Ingest requalify still requires stored `regimes_tested` to match the current window count (8). Existing champions are not culled.

**Cost.** Eight 90d windows is slower than three on jensa (more bars per `evaluate_windows`). Mention it; do not throttle the live k8s sidecar to compensate. Discovery stays off-cluster.

**Superseded on this date** (prior text kept above for history):

- 2026-09-02 / later "Windows remain 3 × ~90 calendar days" insofar as they froze `QUAL_N_WINDOWS` = 3 and ~270d of tape. Per-window 90d, `QUAL_WINDOW_BARS` = 25920, `QUAL_STRIDE` = 1, and the OOS **thresholds** are not superseded.


