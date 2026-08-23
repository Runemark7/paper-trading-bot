"""PaperBot entrypoint — run one or more trading cycles and emit the dashboard.

Usage:
    .venv/bin/python -m hedge_fund.trading.run          # one cycle, BTC+ETH
    .venv/bin/python -m hedge_fund.trading.run --cycles 3

Everything is paper. State (sqlite + calibration JSON) lives under state/.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from hedge_fund.brokers.paper import PaperBroker
from hedge_fund.calibration import CalibrationStore
from hedge_fund.data.binance import CcxtSource
from hedge_fund.dashboard.report import generate_dashboard
from hedge_fund.risk.managed import RiskManager
from hedge_fund.trading.loop import TradingLoop
from hedge_fund.trading.store import TradeStore

STATE = Path(os.environ.get("PAPER_STATE", "state"))
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
START_CASH = 10_000.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--dashboard", default=str(STATE / "report.html"))
    ap.add_argument("--state", default=str(STATE))
    args = ap.parse_args()

    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)

    data = CcxtSource()
    broker = PaperBroker(cash=START_CASH, taker_fee=0.001, slippage=0.0002)
    risk = RiskManager(initial_equity=START_CASH)
    calib = CalibrationStore(state / "calibration.json")
    store = TradeStore(state / "trades.sqlite")

    loop = TradingLoop(data, broker, risk, calib, store=store)

    for i in range(args.cycles):
        results = loop.run_cycle(SYMBOLS)
        for r in results:
            print(f"[{i}] {r.symbol} {r.action:9s} cond={r.condition} "
                  f"p={r.probability:.2f} eq={r.equity:.0f} {r.reason[:40]}")

    out = generate_dashboard(store, args.dashboard)
    print(f"\ndashboard -> {out}")
    print(f"equity: {broker.equity({}) :,.0f} | open: "
          f"{ {t: round(p.quantity,4) for t,p in broker.positions.items()} }")


if __name__ == "__main__":
    main()
