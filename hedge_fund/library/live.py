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
change is traded, at the current price. A day whose lookback is not warm
is skipped: it is not a flat signal. The 20% safety stop is filled on the
run that first includes the breach day's low (that run is the next open).
The heartbeat does not sell these accounts on an intraday price, and its
2:1 take-profit never touches them. A fresh book whose latest close is
already long buys at that run's price.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from hedge_fund.brokers.paper import SLIPPAGE, TAKER_FEE, Order, PaperBroker
from hedge_fund.library.catalog import parse_variant, signal_ready
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
from hedge_fund.trading.constants import (
    LIBRARY_TAPE_START_MS,
    LIBRARY_TAPE_START_SLACK_MS,
    PAPER_START_CASH,
    TRADE_SYMBOLS,
)

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
    """BTC 5m bars from the Binance listing. ``source`` selects the store."""
    del source  # load_library_tape picks the store the same way _tape_source does
    from hedge_fund.library.tape import load_library_tape

    return load_library_tape(state)


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
    file already ends on that day and starts at the library tape
    (``LIBRARY_TAPE_START_MS``, 2017-08-17) it is reused; otherwise every
    day is rebuilt from the full tape, so a repaired historical bar is
    picked up the next day. The 5m canonical cut (2021-01-26) is not used.
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
        starts = (
            len(bars) > 0
            and int(bars.ts[0]) <= LIBRARY_TAPE_START_MS + LIBRARY_TAPE_START_SLACK_MS
            and meta.get("tape_start") == LIBRARY_TAPE_START_MS
        )
        if meta.get("source") == source and bars.last_ts == want_last and starts:
            return bars
    bars = daily_from_tape(_full_tape(state, source))
    try:
        _write_cache(
            path, bars,
            {
                "source": source,
                "last_ts": bars.last_ts,
                "built_at": _now(),
                "tape_start": LIBRARY_TAPE_START_MS,
            },
        )
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


def _position_ready(strategy, bars: DailyBars, params: dict) -> np.ndarray:
    """Finite-input mask. A stub signal (tests) is treated as already warm.

    The catalog function is the production path: a NaN lookback stays out
    of the decision instead of being read as flat.
    """
    from hedge_fund.library.catalog import strategy_by_key

    canonical = strategy_by_key(strategy.key)
    if strategy.signal is not canonical.signal:
        return np.ones(len(bars), dtype=bool)
    return signal_ready(strategy, bars, params)


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
            last_day = raw.get("last_day")
            signal_now = None
            if len(bars):
                signal = strategy.signal(bars, params)
                ready = _position_ready(strategy, bars, params)
                if last_day is None:
                    pending = [len(bars) - 1]
                else:
                    pending = [i for i in range(len(bars)) if int(bars.ts[i]) > int(last_day)]
                was_long = machine.long
                exit_reason = None
                skipped = False
                # A lot opened during this replay fills only at the end price,
                # so earlier lows do not stop it. The stored stop belongs to
                # the position that was already on.
                lot_stop = _stop_level(broker) if was_long else None
                for i in pending:
                    if machine.stop_pending and machine.long and was_long:
                        stop_out(machine)
                        exit_reason = REASON_STOP
                    if bool(ready[i]):
                        step(machine, bool(signal[i]))
                    else:
                        skipped = True
                    if (
                        was_long
                        and machine.long
                        and lot_stop is not None
                        and float(bars.low[i]) <= lot_stop
                    ):
                        if i == pending[-1]:
                            # This run is the next daily open.
                            stop_out(machine)
                            exit_reason = REASON_STOP
                        else:
                            machine.stop_pending = True
                if skipped:
                    print(
                        f"[library] {name}: skipped an unwarmed day; "
                        "a missing lookback is not a flat signal",
                        flush=True,
                    )
                if pending:
                    raw["last_day"] = int(bars.ts[pending[-1]])
                    for i in reversed(pending):
                        if bool(ready[i]):
                            raw["last_signal"] = bool(signal[i])
                            break
                if bool(ready[-1]):
                    signal_now = bool(signal[-1])
                elif raw.get("last_signal") is not None:
                    signal_now = bool(raw["last_signal"])
                if machine.long and not was_long:
                    actions.append(_buy(store, broker, price, strategy.stop_frac, condition))
                    if actions[-1].get("action") != "ENTER":
                        machine.long = False
                elif was_long and not machine.long and broker.lots:
                    done = _sell(store, broker, price, exit_reason or REASON_SIGNAL)
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
    """Heartbeat hook. The daily bar owns the catastrophic stop, so this returns 0.

    Selling on an intraday price would exit minutes after the low, not at
    the next daily open. The heartbeat still calls this so a ``lib.*``
    account is not sent through the 2:1 take-profit path.
    """
    del name, state, price, now
    return 0


__all__ = [
    "ENTER",
    "EXIT",
    "account_path",
    "library_stop_check",
    "live_daily_bars",
    "run_library_account",
]
