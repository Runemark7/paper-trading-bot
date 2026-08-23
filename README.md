# paper-trading-bot

A paper-trading bot that learns calibrated probabilities for BTC/ETH swing and day trading. The experiment: can an LLM agent (Hermes) state probabilities that become both *calibrated* (stated = measured reliability) and *profitable* over time?

**Paper only. No real money until it proves profitability against a baseline.**

Built on **[ai-hedge-fund](https://github.com/virattt/ai-hedge-fund)** (MIT, © Virat Singh) for the core architecture (Signal models, sim broker, risk limits, portfolio, backtesting, LLM layer) — plus a custom **crypto data source (ccxt/Binance)** and a **probability-calibration layer** that the upstream doesn't have.

## Status

- [x] Scaffold core from ai-hedge-fund (MIT, attribution in LICENSE)
- [ ] ccxt crypto data source (Binance BTC/ETH)
- [ ] Probability-calibration layer (Beta-Binomial, stated-vs-measured)
- [ ] Paper fills with taker fee + slippage
- [ ] Risk rules (1% risk, stops, drawdown breaker)
- [ ] Dashboard (equity vs baseline, trade log, calibration)
- [ ] PROTOCOL.md honesty contract

## Layout

```
hedge_fund/
  models.py              Signal/order models
  brokers/sim.py         paper broker
  risk/limits.py         1% risk, stops, drawdown breaker
  data/                  data client + ccxt crypto source
  signals/               alpha models
  pipeline/              run_cycle, execution
  portfolio/             portfolio construction
  backtesting/           backtest engine
  llm/                   LLM clients (DeepSeek etc.)
  calibration/           probability-calibration layer (ours)
  dashboard/             static HTML reports (ours)
PROTOCOL.md              honesty contract
```

## Run

```
uv venv .venv
uv pip install -p .venv pydantic pandas numpy python-dotenv pyyaml scipy ccxt
.venv/bin/python hedge_fund/run.py
```

## License

MIT. Core architecture © Virat Singh (ai-hedge-fund). All modifications for this project are additional work on top.
