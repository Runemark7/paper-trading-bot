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
from hedge_fund.trading.champions import load_pool, MAX_ACTIVE_CHAMPIONS

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
    print(f"[running {len(strategies)} isolated €10k accounts in arena]")

    # Single batch fetch of klines and current prices per symbol to keep cycles sub-second
    cached_klines = {}
    cached_prices = {}
    for sym in SYMBOLS:
        try:
            cached_prices[sym] = data.fetch_price(sym)
            cached_klines[sym] = data.fetch_klines(sym, "4h", limit=300)
        except Exception as e:
            print(f"[data error for {sym}]: {e}")

    for strat in strategies:
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
        
        # Override data source with cached klines/prices during cycle
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        loop._manage_open_positions(cached_prices, now)

        for sym in SYMBOLS:
            klines = cached_klines.get(sym)
            if not klines:
                continue
            from hedge_fund.signals.momentum import compute_signal
            sig = compute_signal(klines, sym, loop.timeframe, strategy=strat)

            # Signal Invalidation Exit
            if sig.direction != "long":
                lots_to_close = [lot for lot in loop.broker.lots if lot.ticker == sym]
                if lots_to_close:
                    cur = cached_prices.get(sym)
                    if cur is not None:
                        for lot in lots_to_close:
                            fill = loop.broker.close_lot(lot, cur)
                            fee = fill.fee if fill else 0.0
                            pnl = (cur - lot.entry_price) * lot.quantity - fee
                            loop._record_outcome(sym, lot, fill, tp=(pnl > 0))
                            loop._close_store_lot(lot, fill, f"signal_exit_{sig.condition}")

            if sig.direction != "long":
                continue

            entry = cached_prices.get(sym)
            if entry is None:
                continue

            stop = entry * (1 - 0.025)
            existing_sym_lots = [lot for lot in loop.broker.lots if lot.ticker == sym]
            if len(existing_sym_lots) >= 3:
                continue
            if existing_sym_lots:
                unrealized_lots_pnl = [(entry - lot.entry_price) * lot.quantity for lot in existing_sym_lots]
                if any(p <= 0 for p in unrealized_lots_pnl):
                    continue

            key = f"{sym}|4h|{sig.condition}"
            prob = 0.60
            conf_mult = max(0.5, min(2.0, (prob / 0.50)))
            open_pos = [(p.entry_price, p.stop_loss, p.quantity) for p in loop.broker.lots]
            rd = loop.risk.size_position(loop.broker.equity(cached_prices), entry, stop, open_pos, confidence=conf_mult)
            if not rd.approved:
                continue

            from hedge_fund.brokers.paper import Order
            fill = loop.broker.place_order(Order(sym, "buy", rd.size, entry, stop_loss=stop, condition=key), market_price=entry)
            fee = fill.fee if fill else 0.0
            if loop.store is not None and loop.broker.lots:
                new_lot = loop.broker.lots[-1]
                loop.store.open_trade(sym, "4h", key, prob, entry, rd.size, fee, lot_id=new_lot.lot_id)

        store.save_account_state({
            "broker": broker.to_state(),
            "risk_peak": risk.peak_equity,
            "saved_at": now
        })


if __name__ == "__main__":
    main()