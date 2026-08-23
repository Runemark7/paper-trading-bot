"""PaperBot entrypoint — run one or more trading cycles and emit the dashboard.

Usage:
    .venv/bin/python -m hedge_fund.trading.run          # one cycle, BTC+ETH
    .venv/bin/python -m hedge_fund.trading.run --cycles 3

Everything is paper. State (sqlite + calibration JSON) lives under state/.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.brokers.paper import PaperBroker
from hedge_fund.calibration import CalibrationStore
from hedge_fund.data.binance import CcxtSource
from hedge_fund.dashboard.report import generate_dashboard
from hedge_fund.regime.gate import RegimeGate
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
    store = TradeStore(state / "trades.sqlite")
    calib = CalibrationStore(state / "calibration.json")
    regime = RegimeGate(state_dir=state, cache_hours=6, top_n=10)

    # Restore the persistent paper account (cash + open positions) so the
    # account accumulates across runs instead of resetting every cycle.
    broker = PaperBroker(cash=START_CASH, taker_fee=0.001, slippage=0.0002)
    saved = store.load_account_state()
    if saved:
        broker.restore_state(saved.get("broker", {}))
        # risk peak continuity: start peak from last known equity if higher
        risk_peak = saved.get("risk_peak", START_CASH)
    else:
        risk_peak = START_CASH
    risk = RiskManager(initial_equity=max(risk_peak, START_CASH))

    loop = TradingLoop(data, broker, risk, calib, store=store, regime=regime)

    for i in range(args.cycles):
        results = loop.run_cycle(SYMBOLS)
        for r in results:
            print(f"[{i}] {r.symbol} {r.action:9s} cond={r.condition} "
                  f"p={r.probability:.2f} eq={r.equity:.0f} {r.reason[:40]}")

    # Persist the account state so the next run continues from here.
    store.save_account_state(
        {"broker": broker.to_state(), "risk_peak": risk.peak_equity,
         "saved_at": datetime.now(timezone.utc).isoformat()}
    )

    out = generate_dashboard(store, args.dashboard, calib_path=str(state / "calibration.json"))
    print(f"\ndashboard -> {out}")
    print(f"equity: {broker.equity({}) :,.0f} | open: "
          f"{ {t: round(p.quantity,4) for t,p in broker.positions.items()} }")


if __name__ == "__main__":
    main()
