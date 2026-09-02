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
from hedge_fund.trading.constants import QUAL_TIMEFRAME

SYMBOLS = ["BTC/USDT", "ETH/USDT"]
START_CASH = 10_000.0
LIVE_STRATEGY = os.environ.get("PAPER_STRATEGY", "sma_stack")


class _CachedMarket:
    """Duck-typed CcxtSource over one pre-fetched snapshot.

    Every champion account must run the same TradingLoop cycle on the same
    bars/prices; caching avoids re-fetching Binance once per strategy.
    """

    def __init__(self, prices: dict, klines: dict) -> None:
        self._prices = prices
        self._klines = klines

    def fetch_price(self, symbol: str) -> float:
        px = self._prices.get(symbol)
        if px is None:
            raise RuntimeError(f"no cached price for {symbol}")
        return px

    def fetch_klines(self, symbol: str, timeframe: str = QUAL_TIMEFRAME, limit: int = 300,
                     since: int | None = None):
        bars = self._klines.get(symbol)
        if not bars:
            raise RuntimeError(f"no cached klines for {symbol}")
        return bars[-limit:] if limit else bars


def active_strategies() -> list[str]:
    """Champion pool strategies to run isolated accounts for, else default."""
    try:
        pool = load_pool()
        champs = pool.get("champions", [])
        return [c["name"] for c in champs] if champs else [LIVE_STRATEGY]
    except Exception:
        return [LIVE_STRATEGY]


def _fetch_snapshot(data: CcxtSource) -> _CachedMarket:
    prices: dict = {}
    klines: dict = {}
    for sym in SYMBOLS:
        try:
            prices[sym] = data.fetch_price(sym)
            klines[sym] = data.fetch_klines(sym, QUAL_TIMEFRAME, limit=300)
        except Exception as e:
            print(f"[data error for {sym}]: {e}")
    return _CachedMarket(prices, klines)


def _run_one_account(strat: str, state: Path, market: _CachedMarket, now: str) -> None:
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
        market = _fetch_snapshot(data)
        for strat in strategies:
            _run_one_account(strat, state, market, now)


if __name__ == "__main__":
    main()
