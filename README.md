# paper-trading-bot

Paper-trading bot for BTC/ETH. The experiment: can an LLM agent (Hermes) state
probabilities that become both *calibrated* (stated = measured reliability) and
*profitable* versus a buy-and-hold baseline?

**Paper only. No real money.** Rules live in [PROTOCOL.md](PROTOCOL.md).
Amendment 2026-08-30 names the live experiment: an isolated-account paper
tournament of combinatorial TA strategies (not the original LLM-probability
study). Amendment 2026-09-01: qualification uses the same tape as live
(then 4h); "profitable" vs buy-and-hold is computed on the overlay and at
graduation. Amendment 2026-09-02: live book, admit, and walk-forward are 5m
bars; the decision cycle runs every 5 minutes. Amendment 2026-09-03:
Donchian / fractal-swing structure atoms exist; they use high/low from
the same 5m klines. Double bottom (`dbl_bot_k`) is the new pattern;
trend / breakout / momentum already map to `sma_stack`/`sma_abv`,
`don_hi_*`, `mom_*`. Amendment 2026-09-05: LazyBear WaveTrend green-dot
(`wt_cross_up_os`) on closed 5m HLC3 — not Market Cipher. Same date:
decision cycle is 24/7 (no 07–21 Stockholm skip); discovery drains
remaining untested names across 5m cycles under a per-cycle time/name
budget (no 30-name sample). Amendment 2026-09-07: incremental discovery
log, rotating leftover cursor, honest newest `last_tested_at`. Same-date
addendum: 1 name / ~90s per cycle after a prod timeout (not 4 / 150s).
Amendment 2026-09-10: a discovery fail parks that name forever (no 24h
retest cooldown). Never-tested leftovers still drain 24/7. Same date:
one discovery eval is cheaper (EMA/WaveTrend series cache, trim 5m
history to the window span) without changing OOS gates or windows.
Amendment 2026-09-11: after that cheaper eval, the per-cycle slice was
bumped to 2 names / ~120s (not a return to 4 / 150s). Same-date addendum:
server overloaded — live default is 1 name / ~90s so discovery evaluates
only one name per cycle. Same date: leftover discovery names — unused
Donchian / swing / `dbl_bot` lookbacks ANDed with existing 5m dip/mom
(`NEW_STRUCTURE_ANDS`). Fail-once stays; the parked 60 are not retested.
Same date: when never-tested leftovers run dry, tournament auto-refills
`discovery_extended.json` from a bounded structure-AND recipe (no human PR per batch). Amendment 2026-09-12: discovery walk-forwards leave the k8s cycle sidecar (`DISCOVERY_ON_CYCLE=0`). Cluster keeps live trading only. The Windows PC runs `scripts/discovery_worker.py` and POSTs results to `/api/discovery/ingest`. Same date: refill recipe adds unused Donchian / near-swing / near-level ANDs (lookbacks through 96, trend×support, standalone `don_lo` / `near_swing_*`). Same-date later: lookbacks through 192 and leftover TREND / `ema_stack` / 3-atom families already in the parser. Same-date later: refill mint bases broaden past the frozen 3×3 dip/mom (wider lookbacks / `%` thresholds and continuation ANDs so new names can participate in a strong B&H OOS). Amendment 2026-09-13: refill mint adds 1% grind bases at unused lookbacks and expands short-MA 3-atoms onto every WIDE mom/dip × `don_hi` / `near_swing_hi` (still mint-only; OOS gates unchanged). Same-date later: causal HTF buyer-regime atoms (`h4_ema_abv_24`, `h4_sma_abv_50`, `h1_ema_abv_24`) AND onto existing 5m DIP/MOM/WIDE/GRIND bases in the refill recipe. Mint/parser only; OOS thresholds and `QUAL_N_WINDOWS` unchanged. Same-date later: HTF densify (`h1_ema_abv_{15,20,30}`, `h4_ema_abv_{12,48}`, `h4_sma_abv_24`) plus `MOM_FILTERS_HTF_DENSE` (`mom_18b_gt4pc` / `mom_18b_gt6pc` / `mom_12b_gt6pc`); recipe is mom-before-dip so HTF×mom mints ahead of HTF×dip. OOS gates unchanged. Same date: token-gated `POST /api/champions/retain` and `POST /api/champions/cull_undated` drop undated names from the active pool only (no trade-DB delete; OOS thresholds unchanged). Same-date later: Windows-farm walk-forward evals cache ATR / SMA / EMA / HTF series across names on the same slices (not `fast_quant`; OOS gates and window lengths unchanged). Same-date later: the Windows worker fail-parks structure lookbacks above 96 (`lookback_too_expensive`) before walk-forward, with a 600s `eval_timeout` backstop — ops/throughput, not a gate softening. Same-date later: refill mint no longer emits structure `N>96` (`STRUCTURE_NS` through 96); leftover already-tested names stay parked. Worker fail-park remains the backstop. OOS gates unchanged. Same date: `/discovery` Start/Stop pauses the farm (worker idles, does not exit). Same date: the all-windows non-negative OOS veto is dropped so more names can get a paper book; fail-once and beat-B&H stay. Same-date later: walk-forward calendar coverage extended to 8 × ~90d of native 5m (~720 days) once multi-year history exists; OOS thresholds unchanged (30 trades, Sharpe ≥ 0.30, beat B&H, beat sma_stack, fail-once). Amendment 2026-09-14: refill mint combines winning HTF×mom with continuation 3-atoms, then densifies 4–7 atom role-bucket stacks on that admit island (`parse_strategy` already ANDs `&`; OOS gates unchanged). Same-date later: unused distinct HTF periods / mom lookbacks/% and winner-shaped 3–5 stacks (`sma_abv_30` / `rsi_14_>55`) emit first so dry refill is not idle after ~7200 unique; depth-7 `near_swing` is deprioritized. Same-date later: walk-forward uses the whole jensa 5m tape — 23 × ~90d (~2070 days, ~5.67y); existing 8-window discovery_log admits stay (no automatic re-qualify); OOS thresholds unchanged. Same-date later: each 90d window is prefixed with `QUAL_WARMUP_BARS` (14d of 5m) so EMA/SMA/HTF/ATR are warm at OOS start; OOS trades/PnL/Sharpe exclude the pad. Same-date later: recipe never ANDs `mom_*_gt*` with `dip_*` (contradictory; trades=0 on the full tape); RSI / continuation siblings stay; already-queued mom∧dip names may still drain once. Same-date later: refill mint un-dries from the admit island (`sma_abv_40` / `ema_abv_40`, `rsi_14_>60`, intermediate mom not short-12, `h1_sma_abv_70`, DEEP 4–5 on paid-off 50/60 spines) so dry refill is not idle after ~8726 unique. Longer evals on jensa; do not throttle live k8s. Sync `refill.py` to jensa after merge (out of band). No named candlesticks. Still paper.

Built on **[ai-hedge-fund](https://github.com/virattt/ai-hedge-fund)** (MIT, © Virat Singh)
for Signal models, sim broker, risk limits, portfolio, backtesting, and the LLM layer.
This repo adds a ccxt/Binance data source, a Beta-Binomial calibration layer, and a
paper broker with taker fee + slippage.

## Status

Day-one scaffold is in the tree and in use:

- Binance OHLCV via ccxt (`hedge_fund/data/binance.py`)
- Paper fills: 0.1% taker + 2 bps slippage (`hedge_fund/brokers/paper.py`)
- Calibration store (Beta-Binomial, stated vs measured)
- Risk rules: 1% per trade, 5% max open risk, hard stops, 15% drawdown halt
- Dashboards: static HTML report + React UI (`frontend/`)
- [PROTOCOL.md](PROTOCOL.md) honesty contract
- Live-forward paper loop (`hedge_fund/web/live.py`) — not real-money

Later work on `main` (multi-timeframe resampling, volume-flow indicators, a
small explicit 5m strategy universe, k8s/ArgoCD) does not change the paper-only rule.

## Run

Python ≥ 3.11. From repo root:

```
uv venv .venv
uv pip install -e .
.venv/bin/python -m hedge_fund.trading.run          # one paper cycle; writes state/ + report.html
.venv/bin/python -m hedge_fund.web.server           # API + static dashboard on :8787
```

Compose (API :8787, React UI :8080):

```
docker compose up
```

Cluster manifests: `k8s/` (ArgoCD app in `k8s/argocd-app.yaml`). HTTP routes:
[docs/WEB_SERVICE.md](docs/WEB_SERVICE.md). Living mint → farm → eval → prod
map (Mermaid): [docs/WORKFLOW.md](docs/WORKFLOW.md). Windows discovery farm:
[docs/WINDOWS_DISCOVERY.md](docs/WINDOWS_DISCOVERY.md).

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

## License

MIT. Core architecture © Virat Singh (ai-hedge-fund). Modifications for this
project are additional work on top.
