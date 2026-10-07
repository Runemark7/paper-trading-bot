"""PaperBot eval runner — run ONE isolated €10k paper account PER champion strategy.

Each champion strategy gets its own PaperBroker + RiskManager (own 5% open-risk
cap, own drawdown halt, own €10k). This isolates performance: strategies no
longer crowd each other out of a shared risk budget, so we can genuinely see
which one performs best. All accounts persist in a per-strategy sqlite.

The decision cycle is TradingLoop.run_cycle — the same loop, stops, sizing,
and strategy path as hedge_fund.trading.run (the cycle sidecar). This module is
not a second hardcoded book.
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.paths import state_root
from hedge_fund.brokers.paper import PaperBroker, SLIPPAGE, TAKER_FEE
from hedge_fund.calibration import CalibrationStore
from hedge_fund.data.binance import CcxtSource
from hedge_fund.risk.managed import RiskManager
from hedge_fund.trading.loop import TradingLoop
from hedge_fund.trading.store import TradeStore
from hedge_fund.trading.champions import load_pool
from hedge_fund.trading.constants import QUAL_SYMBOLS, live_signal_eval_bars
from hedge_fund.trading.live_tape import CachedMarket, refresh_cycle_market

SYMBOLS = list(QUAL_SYMBOLS)
START_CASH = 10_000.0
LIVE_STRATEGY = os.environ.get("PAPER_STRATEGY", "sma_stack")


def active_strategies() -> list[str]:
    """Every champion in the pool, else the PAPER_STRATEGY fallback.

    ``TRADE_EVALUATION_LIMIT`` is how many closed paper trades graduate an
    account. It does not slice this list — a pool larger than that bar is
    still evaluated in full each cycle.
    """
    try:
        pool = load_pool()
        champs = pool.get("champions", [])
        names = [c["name"] for c in champs if isinstance(c, dict) and c.get("name")]
        return names if names else [LIVE_STRATEGY]
    except Exception:
        return [LIVE_STRATEGY]


def _fetch_snapshot(data: CcxtSource, state: Path) -> CachedMarket:
    """Persistent 5m tape (qual span) plus one price snapshot for every account."""
    return refresh_cycle_market(data, state_dir=state)


def _run_one_account(strat: str, state: Path, market: CachedMarket, now: str) -> None:
    slug = strat.replace("/", "_").replace(":", "_")
    db = state / f"trades_{slug}.sqlite"
    store = TradeStore(db)
    calib = CalibrationStore(state / f"calibration_{slug}.json")
    broker = PaperBroker(cash=START_CASH, taker_fee=TAKER_FEE, slippage=SLIPPAGE)
    risk = RiskManager(initial_equity=START_CASH)
    saved = store.load_account_state()
    if saved:
        broker.restore_state(saved.get("broker", {}))
        peak = saved.get("risk_peak", START_CASH)
        risk = RiskManager(initial_equity=max(peak, START_CASH))

    loop = TradingLoop(
        market, broker, risk, calib, store=store,
        strategy=strat, strategy_file=None, regime=None,
        kline_limit=live_signal_eval_bars(),
    )
    loop.run_cycle(SYMBOLS)
    store.save_account_state({
        "broker": broker.to_state(),
        "risk_peak": risk.peak_equity,
        "saved_at": now,
    })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--state", default=str(state_root()))
    args = ap.parse_args()

    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)
    data = CcxtSource()
    strategies = active_strategies()
    print(f"[running {len(strategies)} isolated €10k accounts in arena]")

    for _ in range(args.cycles):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        t0 = time.perf_counter()
        market = _fetch_snapshot(data, state)
        fetch_s = time.perf_counter() - t0
        t1 = time.perf_counter()
        for strat in strategies:
            _run_one_account(strat, state, market, now)
        eval_s = time.perf_counter() - t1
        print(
            f"[isolated cycle evaluated {len(strategies)} champions "
            f"fetch_s={fetch_s:.2f} eval_s={eval_s:.2f} "
            f"total_s={fetch_s + eval_s:.2f}]",
            flush=True,
        )


if __name__ == "__main__":
    main()
