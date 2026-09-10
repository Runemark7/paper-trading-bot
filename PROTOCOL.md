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
| 2026-09-10 | A discovery evaluation that does not qualify parks that name forever. No 24h retest cooldown re-walk of rejects. Never-tested leftovers still drain 24/7 under the 1 name / ~90s cycle budget. Existing `discovery_log.json` fails are permanent. OOS gates unchanged. Paper only. No cull of champions. |

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


