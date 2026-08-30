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

### Amendment 2026-08-30 — what actually runs

This amendment names the experiment that is live on `main`. Original sections above are the pre-registered contract and are **not rewritten**. Where they no longer describe the live path, they are superseded on this date as listed at the end.

**Paper only.** No real-money broker. Graduation status `READY_FOR_LIVE` means **graduated paper**: the strategy finished the evaluation window with positive paper P&L. It is not authorization to trade real funds.

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
