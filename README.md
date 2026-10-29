# paper-trading-bot

Paper-trading bot for BTC/ETH. The experiment: can an LLM agent (Hermes) state
probabilities that become both *calibrated* (stated = measured reliability) and
*profitable* versus a buy-and-hold baseline?

**Paper only. No real money.** Rules live in [PROTOCOL.md](PROTOCOL.md).

The current live experiment (per the amendment log below) is an
isolated-account paper tournament of combinatorial TA strategies — not the
original LLM-probability study.

Built on **[ai-hedge-fund](https://github.com/virattt/ai-hedge-fund)** (MIT,
© Virat Singh) for Signal models, sim broker, risk limits, portfolio,
backtesting, and the LLM layer. This repo adds a ccxt/Binance data source, a
Beta-Binomial calibration layer, and a paper broker with taker fee + slippage.

## Status

Day-one scaffold is in the tree and in use:

- Binance OHLCV via ccxt (`hedge_fund/data/binance.py`)
- Paper fills: 0.1% taker + 2 bps slippage (`hedge_fund/brokers/paper.py`)
- Calibration store (Beta-Binomial, stated vs measured)
- Risk rules: 1% per trade, 5% max open risk, hard stops, 15% drawdown halt
- Dashboards: static HTML report + React UI (`frontend/`)
- Live-forward paper loop (`hedge_fund/web/live.py`) — not real-money
- [PROTOCOL.md](PROTOCOL.md) honesty contract

Later work on `main` (multi-timeframe resampling, volume-flow indicators, a
small explicit 5m strategy universe, k8s/ArgoCD) does not change the
paper-only rule.

## Run

Python ≥ 3.11. From repo root:

```bash
uv venv .venv
uv pip install -e .
.venv/bin/python -m hedge_fund.trading.run   # one paper cycle; writes state/ + report.html
.venv/bin/python -m hedge_fund.web.server    # API + static dashboard on :8787
```

Compose (API :8787, React UI :8080):

```bash
docker compose up
```

Cluster manifests: `k8s/` (ArgoCD app in `k8s/argocd-app.yaml`).

- HTTP routes: [docs/WEB_SERVICE.md](docs/WEB_SERVICE.md)
- Living mint → farm → eval → prod map (Mermaid): [docs/WORKFLOW.md](docs/WORKFLOW.md)
- Windows / Linux discovery farm: [docs/WINDOWS_DISCOVERY.md](docs/WINDOWS_DISCOVERY.md). Workers claim leases from prod so concurrent hosts do not evaluate the same name.

## Layout

```
hedge_fund/
  brokers/paper.py     paper broker (fees + slippage)
  data/binance.py      ccxt/Binance
  calibration/         Beta-Binomial calibration
  risk/managed.py      1% risk, stops, drawdown breaker
  trading/run.py       paper cycle entrypoint
  web/                 live-forward paper HTTP service
  dashboard/           static HTML report
frontend/              React UI
k8s/                   cluster + ArgoCD
docs/WORKFLOW.md       living mint / farm / eval / prod map
PROTOCOL.md            honesty contract
```

## Amendment log

<details>
<summary>Chronological protocol amendments (2026-08-30 → 2026-10-07)</summary>

| Date | Summary |
|------|---------|
| 2026-08-30 | Named the live experiment: isolated-account paper tournament of combinatorial TA strategies (not the original LLM-probability study). |
| 2026-09-01 | Qualification uses the same tape as live (then 4h); "profitable" vs buy-and-hold is computed on the overlay and at graduation. |
| 2026-09-02 | Live book, admit, and walk-forward are 5m bars; the decision cycle runs every 5 minutes. |
| 2026-09-03 | Donchian / fractal-swing structure atoms exist; they use high/low from the same 5m klines. Double bottom (`dbl_bot_k`) is the new pattern; trend / breakout / momentum already map to `sma_stack`/`sma_abv`, `don_hi_*`, `mom_*`. |
| 2026-09-05 | LazyBear WaveTrend green-dot (`wt_cross_up_os`) on closed 5m HLC3 — not Market Cipher. Decision cycle is 24/7 (no 07–21 Stockholm skip); discovery drains remaining untested names across 5m cycles under a per-cycle time/name budget (no 30-name sample). |
| 2026-09-07 | Incremental discovery log, rotating leftover cursor, honest newest `last_tested_at`. Addendum: 1 name / ~90s per cycle after a prod timeout (not 4 / 150s). |
| 2026-09-10 | A discovery fail parks that name forever (no 24h retest cooldown). Never-tested leftovers still drain 24/7. One discovery eval is cheaper (EMA/WaveTrend series cache, trim 5m history to the window span) without changing OOS gates or windows. |
| 2026-09-11 | After the cheaper eval, the per-cycle slice was bumped to 2 names / ~120s (not a return to 4 / 150s). Addendum: server overloaded — live default is 1 name / ~90s so discovery evaluates only one name per cycle. Leftover discovery names — unused Donchian / swing / `dbl_bot` lookbacks ANDed with existing 5m dip/mom (`NEW_STRUCTURE_ANDS`). Fail-once stays; the parked 60 are not retested. When never-tested leftovers run dry, tournament auto-refills `discovery_extended.json` from a bounded structure-AND recipe (no human PR per batch). |
| 2026-09-12 | Discovery walk-forwards leave the k8s cycle sidecar (`DISCOVERY_ON_CYCLE=0`). Cluster keeps live trading only. The Windows PC runs `scripts/discovery_worker.py` and POSTs results to `/api/discovery/ingest`. Refill recipe adds unused Donchian / near-swing / near-level ANDs (lookbacks through 96, trend×support, standalone `don_lo` / `near_swing_*`). Later: lookbacks through 192 and leftover TREND / `ema_stack` / 3-atom families already in the parser. Refill mint bases broaden past the frozen 3×3 dip/mom (wider lookbacks / `%` thresholds and continuation ANDs so new names can participate in a strong B&H OOS). Walk-forward coverage extends to 8 × ~90d (~720 days); later the same tape is 23 × ~90d (~2070 days). The all-windows non-negative OOS veto is dropped (diagnostic only); beat-B&H stay. do not throttle live k8s. No named candlesticks. |
| 2026-09-13 | Refill mint adds 1% grind bases at unused lookbacks and expands short-MA 3-atoms onto every WIDE mom/dip × `don_hi` / `near_swing_hi` (still mint-only; OOS gates unchanged). Later: causal HTF buyer-regime atoms (`h4_ema_abv_24`, `h4_sma_abv_50`, `h1_ema_abv_24`) AND onto existing 5m DIP/MOM/WIDE/GRIND bases in the refill recipe. Mint/parser only; OOS thresholds and `QUAL_N_WINDOWS` unchanged. Later: HTF densify (`h1_ema_abv_{15,20,30}`, `h4_ema_abv_{12,48}`, `h4_sma_abv_24`) plus `MOM_FILTERS_HTF_DENSE` (`mom_18b_gt4pc` / `mom_18b_gt6pc` / `mom_12b_gt6pc`); recipe is mom-before-dip so HTF×mom mints ahead of HTF×dip. OOS gates unchanged. Token-gated `POST /api/champions/retain` and `POST /api/champions/cull_undated` drop undated names from the active pool only (no trade-DB delete; OOS thresholds unchanged). Later: Windows-farm walk-forward evals cache ATR / SMA / EMA / HTF series across names on the same slices (not `fast_quant`; OOS gates and window lengths unchanged). Later: the Windows worker fail-parks structure lookbacks above 96 (`lookback_too_expensive`) before walk-forward, with a 600s `eval_timeout` backstop — ops/throughput, not a gate softening. Mint no longer emits structure lookbacks above 96. |
| 2026-09-14 | Refill mint densifies the admit island: winner 3-atoms, 4–7 atom role-bucket stacks, then `sma_abv_30` / `ema_abv_30` and `rsi_14_>55`, and never ANDs `mom_*_gt*` with `dip_*`. Later undry leads with `sma_abv_40` / `ema_abv_40` and `rsi_14_>60`. Each window is prefixed with `QUAL_WARMUP_BARS` = 4032 (14d of 5m). No named candlesticks. OOS gates unchanged. |
| 2026-10-02 | Undry admit-island densify #2b. Recipe emits h4 twins of `h1_* & mom_18b_gt2pc & sma_abv_30 / ema_abv_30 & rsi_14_>50` first, led by `h4_ema_abv_20&mom_18b_gt2pc&sma_abv_30&rsi_14_>50` (about 160 never-tested names: sparse h4×mom×`sma_abv_30`, dual h1+h4 5–7 atom stacks). #65 `sma_abv_40` / `rsi_14_>60` stays drained, not the lead. No mom∧dip. OOS gates unchanged. |
| 2026-10-07 | Undry admit-island densify #2c. Recipe emits h1 stacks on `mom_18b_gt2pc` first, led by `h1_ema_abv_50&mom_18b_gt2pc&sma_abv_30&rsi_14_>45` (459 never-tested names: `rsi_14_>45`, gap MAs `sma_abv_35` / 25 / 15 / 60, 5-atom extensions, modest mom neighbors). Island densify #2b h4×mom stays in the stream but is deprioritized after a measured FAIL. `--workers` caps at `os.cpu_count()` (default still 2). No mom∧dip. OOS gates unchanged. |
| 2026-10-07 | Live cycle reads a persistent BTC/ETH 5m tape (`live_tape/*.npy` on the state PVC) instead of 300 bars, and evaluates every active champion. Signals use the last qualification window so HTF atoms such as `h1_ema_abv_60` can fire. `TRADE_EVALUATION_LIMIT` stays the 80-trade graduation bar. OOS gates unchanged. |
| 2026-10-08 | Walk-forward OOS segments tile the tape end to end (23 segments of 87d from 2021-04-20 UTC; the last one runs to the tape end and grows with each new day). Each segment's 63d train and 14d warm-up come strictly before it. Boundaries are fixed timestamps, so a new day no longer shifts every OOS window. Thresholds unchanged. Rules tag `sltp_cap100_bhdsr_tiled87_20261008`. |
| 2026-10-08 | Exits are stop/TP only (no exit on signal flip) in discovery backtests and the live loop. Open notional per account is capped at 100% of equity (new lots shrink or skip; cash never negative). Beat-B&H compares OOS daily-equity Sharpe with B&H daily-equity Sharpe on the same windows and the same 10k-per-symbol capital base, instead of raw net P&L. Records carry `gate_rules`; stale workers get no claims and their rows are rejected. Other gates, fees and `rm_v1` sizing unchanged. |
| 2026-10-27 | Gate v4 (`gate_v4_btc_holdout_dsr_pbo_20261027`). v3 floors stay (Sharpe ≥ 0.30, trades ≥ 30, beat sma_stack, beat B&H daily Sharpe, `QUAL_N_WINDOWS` = 23 as the historical grid). Discovery scores 18 sealed segments that end before 2025-10-01, then one holdout (2025-10-01..2026-10-01) that never feeds mint. Extra floors: deflated Sharpe ≥ 0.95, CSCV PBO ≤ 0.20, ≥70% parameter neighbours, 2× fees + 5 bps still clears Sharpe and B&H. ETH is a soft Sharpe > 0 check and is not traded. Forward-test criteria are stored per champion and only flag. Existing champions are not retired. |
| 2026-10-27 | Gate v5 (`gate_v5_btc_holdout_dsr_pbo_20261027`). No discovery qualification check requires beating buy-and-hold: not the discovery windows, not the sealed holdout, and not the cost-stress re-run. Those B&H figures stay informational. Cost stress still requires stressed Sharpe ≥ 0.30. Every other v4 floor stays. Paper graduation after 80 closed trades is net paper P&L > 0 after taker fee and slippage; B&H P&L stays an informational column. New admissions stop at 5 active forward champions (`FORWARD_CHAMPION_CAP`). A read-only forward-candidate list ranks qualified names by DSR, then daily Sharpe, then lower PBO and proposes the top 5. It does not admit or retire. The 20-slot arena stays deleted. Existing champions are not retired. |
| 2026-10-27 | A newly qualified discovery pass is proposed, awaiting approval. It is not seated. `force_admit` and `promote_candidates` are the seats and still stop at 5 active champions. The two champions already on the book stay. Names whose v4 fail was only a discovery-window buy-and-hold comparison, with no later checks stored, can be queued once (`scripts/requeue_v4_bh.py`, `POST /api/discovery/requeue`, dry-run unless apply) for one v5 evaluation ahead of the random mint. That retest does not add a DSR trial and does not unseal the holdout. The discovery refill keeps a buffer: when unleased untested names are below `max(cap, 4 × recent heartbeat lanes)` a background batch of up to 64 is minted after the claim returns. `DISCOVERY_REFILL_BATCH_SIZE` stays 16. Mint bans and the holdout seal stay. |
| 2026-10-27 | The discovery worker keeps `--workers` evals in flight and posts each result as it finishes. Each name still has its own 600s timeout. The process pool and the loaded tape are reused across claims until the tape hash changes. Shutdown releases leases for names that were not started. |
| 2026-10-28 | No champion cap. Supersedes the gate v5 forward cap of 5 (`FORWARD_CHAMPION_CAP` is deleted). No admission path refuses because of how many champions are active. A newly qualified ingest is still proposed, awaiting approval, and is not seated. `forward_candidates` ranks every qualified name that is not active, graduated, or retired and marks all of them proposed. `force_admit`, `promote_candidates`, and the approved-admit one-shot are the seats. Existing champions are not retired. Gate thresholds, mint, and discovery lease logic are unchanged. |
| 2026-10-28 | Gate v6 (`gate_v6_library_signal_exit_20261028`). Discovery's default queue is a pre-registered library of 15 cited daily BTC strategies (64 configurations, which is the deflated-Sharpe trial count). The `signal_exit` engine is long or flat and all-in: enter when the signal turns on, exit when it turns off, fill at the next open, same state machine in the backtest and the live paper cycle. No take-profit. The existing floors are unchanged (30 OOS trades, Sharpe ≥ 0.30, beat `sma_stack`, holdout, DSR ≥ 0.95, PBO ≤ 0.20, 70% of neighbours, 2× fees + 5 bps). Buy-and-hold stays informational. Random mint remains behind `PAPER_DISCOVERY_SOURCE=random`. A pass is proposed, not seated. The existing 5m champions keep `rm_v1`. A worker on the v5 tag gets no names. |
| 2026-10-29 | Gate v6 warm-up (`gate_v6_library_warmup_20261029`). Daily indicators are computed on BTC history from 2017-08-17. A day whose lookback is not warm fails instead of counting as flat. The 2026-10-28 stamp did that and is not this gate. Daily floors are 8 out-of-sample round-trips and 2 holdout trades. PBO blocks are about 390 days. A pre-registered 20% stop fills at the next open; it is on for every library strategy and is not a second trial, so N stays 64. A fresh book whose signal is already on buys at the first open. The 5m floors stay 30 and 8. The sealed holdout stays 2025-10-01..2026-10-01. Nothing is auto-seated. |
| 2026-10-29 | Library workers download warmed BTC daily bars. `GET /api/discovery/library/daily` (same ingest token) serves the cycle's daily file from 2017-08-17, with ETag and If-Modified-Since. The web process does not resample the 5m tape for that request. A worker that cannot load a warmed series does not claim `lib.*` names. Random mode still uses the canonical 5m tape. Web CPU 500m and memory 1Gi are unchanged. |
| 2026-10-29 | `lib.sma100` is seated once as an unproven paper forward test (`admission=forward_test_unproven`, `qualified=false`). Alexander approved it at 09:30 CET. It fails gate v6 only on DSR 0.848 < 0.95. The row is built from `evaluate_library_record` on the library tape. The discovery log is not set to `qualified=true`. A fresh library book that is already long fills at the next 00:00 UTC open, not at an intraday cycle price. Existing champions and the family rule stay. Web CPU 500m and memory 1Gi are unchanged. |
| 2026-10-29 | Gate v7 (`gate_v7_library_profit_20261029`). Alexander, 10:35 CET. A library name passes when net P&L after fees is positive on the test years and the holdout, including at 2× fees + 5 bps, with at least 8 out-of-sample round-trips, 2 holdout trades, and 70% of neighbours also profitable on those three checks. DSR, PBO, Sharpe, sma_stack, and buy-and-hold are warnings. A pass is proposed, not seated. The 5m path and the existing champions stay. A worker on the v6 tag gets no names. |
| 2026-10-29 | Gate v7b (`gate_v7b_library_profit_20261029`). Alexander, 11:01 CET. The neighbour fraction stays computed and shown, and it is a warning for `lib.*`. `lib.rsi2` and `lib.sma200` are seated once from a qualified verdict under this tag (batch `lib-rsi2-sma200-v7-20261029`), with warnings and `approved_by` Alexander on the row. A fresh library book that is already long fills at the next 00:00 UTC open. `lib.sma100` stays `forward_test_unproven`. The 5m path and the two dip champions stay. A worker on the v7 tag gets no names. `scripts/requeue_library_gate.py` (dry-run unless `--apply`) queues canonical `lib.*` names whose newest log row is an older tag, including seated names. The retest writes one discovery-log row and does not move the champion or the paper account. A pass stays proposed. |

</details>

## License

MIT. Core architecture © Virat Singh (ai-hedge-fund). Modifications for this
project are additional work on top.
