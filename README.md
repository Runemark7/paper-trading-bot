# paper-trading-bot

Paper-trading bot for BTC/ETH. The experiment: can an LLM agent (Hermes) state
probabilities that become both *calibrated* (stated = measured reliability) and
*profitable* versus a buy-and-hold baseline?

**Paper only. No real money.** Rules live in [PROTOCOL.md](PROTOCOL.md).
Amendment 2026-08-30 names the live experiment: an isolated-account paper
tournament of combinatorial TA strategies (not the original LLM-probability
study). Amendment 2026-09-01: qualification uses the same 4h tape as live;
"profitable" vs buy-and-hold is computed on the overlay and at graduation.

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
small explicit 4h strategy universe, k8s/ArgoCD) does not change the paper-only rule.

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
[docs/WEB_SERVICE.md](docs/WEB_SERVICE.md).

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
PROTOCOL.md            honesty contract
```

## License

MIT. Core architecture © Virat Singh (ai-hedge-fund). Modifications for this
project are additional work on top.
