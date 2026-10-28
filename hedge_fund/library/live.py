"""Paper-trade an admitted library champion with the ``signal_exit`` engine.

The live cycle calls :func:`run_library_account` once per run for each
library champion. It uses the same pieces as the backtest:

* daily bars from the same BTC/USDT 5m tape (``live_daily_bars``), so only
  completed UTC days produce a signal;
* the catalog's signal function on those bars;
* ``engine.step`` / ``engine.stop_out`` for every decision;
* ``engine.buy_fill`` sizing (all-in) and the paper broker's 0.1% taker +
  2 bps slippage on every fill.

The decision for day ``d`` is taken by the first run after ``d`` has
closed, at that run's price, which is the live counterpart of the
backtest's next-open fill. Days that closed while the cycle was down are
replayed through ``engine.step`` in order; only the resulting position
change is traded, at the current price. The optional safety stop is
checked against the current price on every run and by the heartbeat
(``library_stop_check``). There is no take-profit: the heartbeat's 2:1 rule
never touches these accounts.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from hedge_fund.brokers.paper import SLIPPAGE, TAKER_FEE, Order, PaperBroker
from hedge_fund.library.catalog import parse_variant
from hedge_fund.library.daily import DAY_MS, DailyBars, daily_from_tape, empty_daily
from hedge_fund.library.engine import (
    ENGINE_ID,
    EXIT,
    ENTER,
    REASON_SIGNAL,
    REASON_STOP,
    TIMEFRAME,
    Costs,
    MachineState,
    buy_fill,
    step,
    stop_out,
    stop_price,
)
from hedge_fund.trading.constants import PAPER_START_CASH, QUAL_CANONICAL_START_MS, TRADE_SYMBOLS

SYMBOL = TRADE_SYMBOLS[0]
STATE_KEY = "library"
LOCK_NAME = "library-account"
DAILY_CACHE = "library_daily_BTC_USDT.npz"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def account_path(state: Path, name: str) -> Path:
    from hedge_fund.trading.open_lots import account_slug

    return Path(state) / f"trades_{account_slug(name)}.sqlite"


# --- daily bars from the live tape ---------------------------------------


def _tape_source() -> str:
    from hedge_fund.trading.live_tape import _db_selected

    return "candles" if _db_selected() else "live_tape"


def _newest_5m_ts(state: Path, source: str) -> int | None:
    if source == "candles":
        from hedge_fund.data.candle_store import latest_open_ms

        return latest_open_ms(SYMBOL)
    from hedge_fund.trading.live_tape import load_tail

    tail = load_tail(SYMBOL, 1, state)
    return int(tail["ts"][-1]) if len(tail) else None


def _full_tape(state: Path, source: str) -> np.ndarray:
    if source == "candles":
        from hedge_fund.data.candle_store import load_tape

        return load_tape(SYMBOL, since_ms=QUAL_CANONICAL_START_MS)
    from hedge_fund.trading.live_tape import load_array

    arr = load_array(SYMBOL, state)
    lo = int(np.searchsorted(arr["ts"], np.int64(QUAL_CANONICAL_START_MS), side="left"))
    return arr[lo:]


def _read_cache(path: Path) -> tuple[DailyBars, dict] | None:
    try:
        with np.load(path) as packed:
            meta = json.loads(str(packed["meta"]))
            bars = DailyBars(
                ts=np.asarray(packed["ts"], dtype=np.int64),
                open=np.asarray(packed["open"], dtype=np.float64),
                high=np.asarray(packed["high"], dtype=np.float64),
                low=np.asarray(packed["low"], dtype=np.float64),
                close=np.asarray(packed["close"], dtype=np.float64),
            )
    except (OSError, KeyError, ValueError):
        return None
    return bars, meta


def _write_cache(path: Path, bars: DailyBars, meta: dict) -> None:
    tmp = path.with_name(path.name + ".tmp.npz")
    np.savez(
        tmp, ts=bars.ts, open=bars.open, high=bars.high, low=bars.low, close=bars.close,
        meta=np.asarray(json.dumps(meta)),
    )
    os.replace(tmp, path)


def live_daily_bars(state: Path) -> DailyBars:
    """Completed UTC days of the BTC tape the cycle keeps. Rebuilt once per day.

    The newest 5m bar decides the newest completed day. When the cached
    file already ends on that day it is reused; otherwise every day is
    rebuilt from the full tape (since the canonical start), so a repaired
    historical bar is picked up the next day.
    """
    state = Path(state)
    source = _tape_source()
    newest = _newest_5m_ts(state, source)
    if newest is None:
        return empty_daily()
    want_last = newest - (newest % DAY_MS) - DAY_MS
    path = state / DAILY_CACHE
    cached = _read_cache(path) if path.exists() else None
    if cached is not None:
        bars, meta = cached
        if meta.get("source") == source and bars.last_ts == want_last:
            return bars
    bars = daily_from_tape(_full_tape(state, source))
    try:
        _write_cache(path, bars, {"source": source, "last_ts": bars.last_ts, "built_at": _now()})
    except OSError as exc:
        print(f"[library] daily cache not written: {exc}", flush=True)
    return bars


# --- account ---------------------------------------------------------------


def _open_account(state: Path, name: str):
    from hedge_fund.trading.store import TradeStore

    store = TradeStore(account_path(state, name))
    broker = PaperBroker(cash=PAPER_START_CASH, taker_fee=TAKER_FEE, slippage=SLIPPAGE)
    saved = store.load_account_state() or {}
    if saved.get("broker"):
        broker.restore_state(saved["broker"])
    raw = saved.get(STATE_KEY) if isinstance(saved.get(STATE_KEY), dict) else {}
    machine = MachineState.from_dict(raw.get("machine"))
    held = [lot for lot in broker.lots if lot.ticker == SYMBOL]
    # The broker is the source of truth for the position.
    machine.long = bool(held)
    return store, broker, saved, raw, machine


def _save(store, broker: PaperBroker, saved: dict, raw: dict, machine: MachineState, now: str) -> None:
    lib = dict(raw)
    lib["machine"] = machine.to_dict()
    lib["engine"] = ENGINE_ID
    out = dict(saved)
    out["broker"] = broker.to_state()
    out[STATE_KEY] = lib
    out["saved_at"] = now
    store.save_account_state(out)


def _costs(broker: PaperBroker) -> Costs:
    return Costs(taker_fee=broker.taker_fee, slippage=broker.slippage)


def _buy(store, broker: PaperBroker, price: float, stop_frac: float, condition: str) -> dict:
    qty, fill_px, _fee = buy_fill(broker.cash(), price, _costs(broker))
    if qty <= 0:
        return {"action": "REJECTED", "reason": "no cash"}
    stop = stop_price(fill_px, stop_frac)
    fill = broker.place_order(
        Order(SYMBOL, "buy", qty, price, stop_loss=0.0 if stop is None else stop, condition=condition),
        market_price=price,
    )
    lot = broker.lots[-1]
    store.open_trade(SYMBOL, TIMEFRAME, condition, 0.5, fill.price, qty, entry_fee=fill.fee, lot_id=lot.lot_id)
    return {"action": "ENTER", "reason": "signal on", "size": qty, "entry": fill.price, "stop": stop}


def _sell(store, broker: PaperBroker, price: float, reason: str) -> dict | None:
    lots = [lot for lot in broker.lots if lot.ticker == SYMBOL]
    if not lots:
        return None
    out = None
    for lot in lots:
        fill = broker.close_lot(lot, price)
        tid = None
        for row in store.open_trade_ids():
            if row["symbol"] == SYMBOL and row.get("lot_id") == lot.lot_id:
                tid = row["id"]
                break
        if tid is None:
            for row in store.open_trade_ids():
                if row["symbol"] == SYMBOL:
                    tid = row["id"]
                    break
        pnl = (fill.price - lot.entry_price) * lot.quantity - fill.fee - lot.entry_fee
        pnl_pct = fill.price / lot.entry_price - 1.0 if lot.entry_price else 0.0
        if tid is not None:
            store.close_trade(tid, fill.price, reason, fill.fee, pnl, pnl_pct, hit=1 if pnl > 0 else 0)
        out = {"action": "CLOSE_STOP" if reason == REASON_STOP else "CLOSE_SIGNAL", "reason": reason,
               "exit": fill.price, "pnl": pnl}
    return out


def _stop_level(broker: PaperBroker) -> float | None:
    for lot in broker.lots:
        if lot.ticker == SYMBOL and lot.stop_loss and lot.stop_loss > 0:
            return float(lot.stop_loss)
    return None


def _snapshot(store, broker: PaperBroker, price: float) -> float:
    from hedge_fund.trading.buy_and_hold import overlay_equity

    equity = broker.equity({SYMBOL: price})
    store.snapshot_equity(equity, baseline=overlay_equity(store, {SYMBOL: price}, PAPER_START_CASH))
    return equity


def run_library_account(
    name: str,
    state: Path,
    *,
    bars: DailyBars,
    price: float | None,
    now: str | None = None,
) -> dict:
    """One live decision for library champion ``name``. Returns what happened."""
    from hedge_fund.trading.store import paper_state_lock

    strategy, params = parse_variant(name)
    stamp = now or _now()
    if price is None or not np.isfinite(price) or price <= 0:
        return {"name": name, "actions": [], "reason": "no price"}
    with paper_state_lock(LOCK_NAME):
        store, broker, saved, raw, machine = _open_account(Path(state), name)
        try:
            condition = f"{SYMBOL}|{TIMEFRAME}|{strategy.key}"
            actions: list[dict] = []
            stop = _stop_level(broker)
            if machine.long and stop is not None and price <= stop:
                done = _sell(store, broker, price, REASON_STOP)
                if done:
                    actions.append(done)
                stop_out(machine)
            last_day = raw.get("last_day")
            signal_now = None
            if len(bars):
                signal = strategy.signal(bars, params)
                if last_day is None:
                    pending = [len(bars) - 1]
                else:
                    pending = [i for i in range(len(bars)) if int(bars.ts[i]) > int(last_day)]
                was_long = machine.long
                for i in pending:
                    step(machine, bool(signal[i]))
                if pending:
                    raw["last_day"] = int(bars.ts[pending[-1]])
                    raw["last_signal"] = bool(signal[pending[-1]])
                signal_now = bool(signal[-1])
                if machine.long and not was_long:
                    actions.append(_buy(store, broker, price, strategy.stop_frac, condition))
                    if actions[-1].get("action") != "ENTER":
                        machine.long = False
                elif was_long and not machine.long and broker.lots:
                    done = _sell(store, broker, price, REASON_SIGNAL)
                    if done:
                        actions.append(done)
            equity = _snapshot(store, broker, price)
            decisions = actions or [{
                "action": "HOLD" if machine.long else "FLAT",
                "reason": "no completed day" if not len(bars) else (
                    f"signal {'on' if signal_now else 'off'}" + (" (locked after stop)" if machine.lockout else "")
                ),
            }]
            for d in decisions:
                store.record_decision(
                    SYMBOL, TIMEFRAME, condition, None, None,
                    "long" if machine.long else "flat", d.get("action"), d.get("reason"),
                    equity, d.get("size") or 0.0, d.get("entry") or 0.0,
                    raw_signal={"engine": ENGINE_ID, "signal": signal_now, "last_day": raw.get("last_day")},
                )
            _save(store, broker, saved, raw, machine, stamp)
        finally:
            store.close()
    return {"name": name, "actions": actions, "long": machine.long, "equity": equity, "signal": signal_now}


def library_stop_check(name: str, state: Path, price: float | None, now: str | None = None) -> int:
    """Heartbeat path: sell on a safety-stop breach. Returns lots closed (0 or 1)."""
    from hedge_fund.trading.store import paper_state_lock

    if price is None or not np.isfinite(price) or price <= 0:
        return 0
    with paper_state_lock(LOCK_NAME):
        store, broker, saved, raw, machine = _open_account(Path(state), name)
        try:
            stop = _stop_level(broker)
            if not machine.long or stop is None or price > stop:
                return 0
            done = _sell(store, broker, price, REASON_STOP)
            stop_out(machine)
            _snapshot(store, broker, price)
            if done:
                store.record_decision(
                    SYMBOL, TIMEFRAME, f"{SYMBOL}|{TIMEFRAME}|safety_stop", None, None, "flat",
                    done["action"], done["reason"], broker.equity({SYMBOL: price}), 0.0, 0.0,
                    raw_signal={"engine": ENGINE_ID, "stop": stop},
                )
            _save(store, broker, saved, raw, machine, now or _now())
            return 1 if done else 0
        finally:
            store.close()


__all__ = [
    "ENTER",
    "EXIT",
    "account_path",
    "library_stop_check",
    "live_daily_bars",
    "run_library_account",
]
