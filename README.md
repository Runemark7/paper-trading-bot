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
- Windows discovery farm: [docs/WINDOWS_DISCOVERY.md](docs/WINDOWS_DISCOVERY.md)

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
<summary>Chronological protocol amendments (2026-08-30 → 2026-09-13)</summary>

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
| 2026-09-12 | Discovery walk-forwards leave the k8s cycle sidecar (`DISCOVERY_ON_CYCLE=0`). Cluster keeps live trading only. The Windows PC runs `scripts/discovery_worker.py` and POSTs results to `/api/discovery/ingest`. Refill recipe adds unused Donchian / near-swing / near-level ANDs (lookbacks through 96, trend×support, standalone `don_lo` / `near_swing_*`). Later: lookbacks through 192 and leftover TREND / `ema_stack` / 3-atom families already in the parser. Refill mint bases broaden past the frozen 3×3 dip/mom (wider lookbacks / `%` thresholds and continuation ANDs so new names can participate in a strong B&H OOS). |
| 2026-09-13 | Refill mint adds 1% grind bases at unused lookbacks and expands short-MA 3-atoms onto every WIDE mom/dip × `don_hi` / `near_swing_hi` (still mint-only; OOS gates unchanged). Later: causal HTF buyer-regime atoms (`h4_ema_abv_24`, `h4_sma_abv_50`, `h1_ema_abv_24`) AND onto existing 5m DIP/MOM/WIDE/GRIND bases in the refill recipe. Mint/parser only; OOS thresholds and `QUAL_N_WINDOWS` unchanged. Later: HTF densify (`h1_ema_abv_{15,20,30}`, `h4_ema_abv_{12,48}`, `h4_sma_abv_24`) plus `MOM_FILTERS_HTF_DENSE` (`mom_18b_gt4pc` / `mom_18b_gt6pc` / `mom_12b_gt6pc`); recipe is mom-before-dip so HTF×mom mints ahead of HTF×dip. OOS gates unchanged. Token-gated `POST /api/champions/retain` and `POST /api/champions/cull_undated` drop undated names from the active pool only (no trade-DB delete; OOS thresholds unchanged). Later: Windows-farm walk-forward evals cache ATR / SMA / EMA / HTF series across names on the same slices (not `fast_quant`; OOS gates and window lengths unchanged). Later: the Windows worker fail-parks structure lookbacks above 96 (`lookback_too_expensive`) before walk-forward, with a 600s `eval_timeout` backstop — ops/throughput, not a gate softening. |

</details>

## License

MIT. Core architecture © Virat Singh (ai-hedge-fund). Modifications for this
project are additional work on top.
