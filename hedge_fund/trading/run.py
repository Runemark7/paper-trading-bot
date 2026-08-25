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
from hedge_fund.risk.managed import RiskManager
from hedge_fund.trading.loop import TradingLoop
from hedge_fund.trading.store import TradeStore
from hedge_fund.trading.champions import load_pool

STATE = Path(os.environ.get("PAPER_STATE", "state"))
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
START_CASH = 10_000.0
# Strategy the live loop runs. Default is the A/B backtest winner (sma_stack),
# but the SELF-LEARNED CHAMPION above overrides it when present.
LIVE_STRATEGY = os.environ.get("PAPER_STRATEGY", "sma_stack")


def resolve_champion() -> str | None:
    """Prefer the live champion from the evolution pool, else None."""
    try:
        pool = load_pool()
        champs = pool.get("champions", [])
        # prefer a champion that has closed trades (live-validated), else first
        for c in champs:
            if c.get("closed", 0) > 0:
                return c["name"]
        if champs:
            return champs[0]["name"]
    except Exception:
        pass
    return None


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
    # Regime gate is served read-only via /api/regime for the UI. The live
    # decision cycle does NOT gate on it: computing the regime scans the full
    # coin universe (40s+) which stalls every cycle. Strategy signals + risk
    # limits govern entries; the regime zone remains visible in the dashboard.
    regime = None

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

    # The live loop trades the self-learned champion when one exists,
    # falling back to PAPER_STRATEGY (sma_stack) otherwise.
    active = resolve_champion() or LIVE_STRATEGY
    print(f"[active strategy: {active}]")
    loop = TradingLoop(data, broker, risk, calib, store=store, regime=regime,
                       strategy=active)

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
    print(f"\n[dashboard -> {out}]")
    print(f"[strategy: {active}] equity: {broker.equity({}) :,.0f} | open: "
          f"{ {t: round(broker.quantity(t), 4) for t in set(p.ticker for p in broker.lots)} }")


if __name__ == "__main__":
    main()
