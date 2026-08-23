"""Historical replay — walk real past klines bar-by-bar through the loop.

This is the "held-out test world" of the PROTOCOL: a frozen historical window
replayed through the same deterministic engine, risk rules, broker, and
calibration layer as live trading. It proves the self-learning loop actually
works (positions close, outcomes flow into the Beta posteriors, dashboard
populates) without waiting weeks of live swings.

No lookahead: at each bar, decisions use only bars up to and including it.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from hedge_fund.brokers.paper import PaperBroker, Order
from hedge_fund.calibration import CalibrationStore, condition_key
from hedge_fund.data.binance import CcxtSource
from hedge_fund.risk.managed import RiskManager
from hedge_fund.signals.momentum import compute_signal
from hedge_fund.trading.store import TradeStore

TAKE_PROFIT_RR = 2.0
STOP_PCT = 0.025


def replay(data: CcxtSource, symbol: str, timeframe: str, limit: int,
           broker: PaperBroker, risk: RiskManager, calib: CalibrationStore,
           store: TradeStore) -> None:
    klines = data.fetch_klines(symbol, timeframe, limit=limit)
    window = 60  # bars of lookback for indicators
    for i in range(window, len(klines)):
        series = klines[i - window : i + 1]
        sig = compute_signal(series, symbol, timeframe)
        cur = klines[i].close
        px = {symbol: cur}

        # manage open position: stop / take-profit (per lot, may pyramid)
        for pos in list(broker.lots_for(symbol)):
            if cur <= pos.stop_loss:
                fill = broker.close_lot(pos, cur)
                _close(store, calib, symbol, pos, fill, "stop_loss")
            else:
                tp = risk.take_profit_price(pos.entry_price, pos.stop_loss, TAKE_PROFIT_RR)
                if cur >= tp:
                    fill = broker.close_lot(pos, cur)
                    _close(store, calib, symbol, pos, fill, "take_profit")

        if sig.direction != "long" or risk.is_halted():
            continue

        key = condition_key(symbol, timeframe, sig.condition)
        proposed = 0.55 + 0.15 * abs(sig.raw_score)
        prob, _ = calib.calibrated_probability(key, proposed=proposed)
        equity = broker.equity(px)
        entry = cur
        stop = entry * (1 - STOP_PCT)
        open_pos = [(p.entry_price, p.stop_loss, p.quantity)
                    for p in broker.lots]
        rd = risk.size_position(equity, entry, stop, open_pos)
        if not rd.approved:
            continue
        fill = broker.place_order(
            Order(symbol, "buy", rd.size, entry, stop_loss=stop, condition=key),
            market_price=entry,
        )
        new_lot = broker.lots[-1] if broker.lots else None
        store.open_trade(symbol, timeframe, sig.condition, prob, entry, rd.size,
                         entry_fee=fill.fee,
                         lot_id=new_lot.lot_id if new_lot else None)

    # finish: close any still-open lot at last price
    for pos in list(broker.lots_for(symbol)):
        last = klines[-1].close
        fill = broker.close_lot(pos, last)
        _close(store, calib, symbol, pos, fill, "manual_eod")


def _close(store, calib, symbol, pos, fill, reason) -> None:
    """Close a trade: record outcome in calibration + P&L in store."""
    entry = pos.entry_price
    exit_ = fill.price
    success = (exit_ > entry) if reason in ("take_profit", "manual_eod") else False
    key = pos.entry_condition or condition_key(symbol, "closed", "any")
    calib.record_outcome(key, success)
    calib.save()
    # find the open trade for this symbol in the store and close it
    for t in store.open_trade_ids():
        if t["symbol"] == symbol:
            pnl = (exit_ - entry) * t["size"] - fill.fee - t["entry_fee"]
            pnl_pct = (exit_ - entry) / entry if entry else 0.0
            store.close_trade(t["id"], exit_, reason, fill.fee, pnl, pnl_pct,
                              hit=1 if success else 0)
            break


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--bars", type=int, default=1500)  # ~8 months of 4h
    ap.add_argument("--state", default="state")
    args = ap.parse_args()

    from pathlib import Path
    state = Path(args.state)
    state.mkdir(exist_ok=True)

    data = CcxtSource()
    broker = PaperBroker(cash=10_000, taker_fee=0.001, slippage=0.0002)
    risk = RiskManager(initial_equity=10_000)
    calib = CalibrationStore(state / "calibration.json")
    store = TradeStore(state / "replay.sqlite")

    replay(data, args.symbol, args.timeframe, args.bars, broker, risk, calib, store)

    stats = store.stats()
    print(f"replay {args.symbol} {args.timeframe} {args.bars} bars")
    print(f"  closed trades: {stats['closed']}  win rate: "
          f"{(stats['hits']/stats['closed']):.0%}" if stats['closed'] else "  none closed")
    print(f"  total P&L: {stats['total_pnl']:.2f}  final equity: {broker.equity({}):.0f}")
    print("  calibration:", calib.snapshot())


if __name__ == "__main__":
    main()
