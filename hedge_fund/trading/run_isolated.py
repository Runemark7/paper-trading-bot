"""PaperBot eval runner — run ONE isolated €10k paper account PER champion strategy.

Each champion strategy gets its own PaperBroker + RiskManager (own 5% open-risk
cap, own drawdown halt, own €10k). This isolates performance: strategies no
longer crowd each other out of a shared risk budget, so we can genuinely see
which one performs best. All accounts persist in a per-strategy sqlite.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.brokers.paper import PaperBroker
from hedge_fund.calibration import CalibrationStore
from hedge_fund.data.binance import CcxtSource
from hedge_fund.risk.managed import RiskManager
from hedge_fund.trading.loop import TradingLoop
from hedge_fund.trading.store import TradeStore
from hedge_fund.trading.champions import load_pool, MAX_CHAMPIONS

STATE = Path(os.environ.get("PAPER_STATE", "state"))
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
START_CASH = 10_000.0
LIVE_STRATEGY = os.environ.get("PAPER_STRATEGY", "sma_stack")


def active_strategies() -> list[str]:
    """Champion pool strategies to run isolated accounts for, else default."""
    try:
        pool = load_pool()
        champs = pool.get("champions", [])
        return [c["name"] for c in champs] if champs else [LIVE_STRATEGY]
    except Exception:
        return [LIVE_STRATEGY]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--state", default=str(STATE))
    args = ap.parse_args()

    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)
    data = CcxtSource()
    strategies = active_strategies()
    print(f"[running {len(strategies)} isolated €10k accounts: {strategies}]")

    for strat in strategies:
        # one isolated account per strategy — RESTORE its existing broker so the
        # cycle trades the true open book (not a fresh empty account that would
        # duplicate/re-open and leave phantom rows).
        db = state / f"trades_{strat.replace('/','_').replace(':','_')}.sqlite"
        store = TradeStore(db)
        calib = CalibrationStore(state / f"calibration_{strat.replace('/','_').replace(':','_')}.json")
        broker = PaperBroker(cash=START_CASH, taker_fee=0.001, slippage=0.0002)
        risk = RiskManager(initial_equity=START_CASH)
        saved = store.load_account_state()
        if saved:
            broker.restore_state(saved.get("broker", {}))
            peak = saved.get("risk_peak", START_CASH)
            risk = RiskManager(initial_equity=max(peak, START_CASH))
        loop = TradingLoop(data, broker, risk, calib, store=store,
                           strategy=strat, strategy_file=None, regime=None)
        for i in range(args.cycles):
            try:
                results = loop.run_cycle(SYMBOLS)
                for r in results:
                    print(f"  [{strat}] {r.symbol} {r.action:9s} cond={r.condition} "
                          f"p={r.probability:.2f} eq={r.equity:.0f} {r.reason[:40]}")
            except Exception as exc:
                print(f"  [{strat}] cycle error: {exc}")
        # persist each isolated account so it carries on next run
        store.save_account_state(
            {"broker": broker.to_state(), "risk_peak": risk.peak_equity,
             "saved_at": datetime.now(timezone.utc).isoformat()})
        print(f"  [{strat}] equity: {broker.equity({}) :,.0f} open: "
              f"{ {t: round(broker.quantity(t), 4) for t in set(p.ticker for p in broker.lots)} }")


if __name__ == "__main__":
    main()