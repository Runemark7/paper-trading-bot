"""Rapid position heartbeat — enforce stop-loss / take-profit fast.

Runs frequently (a few minutes cadence) to re-price open paper positions and
close any lot that breaches its stop or hits take-profit IMMEDIATELY (not 6h
later). This is the essential risk-management heartbeat: a 2.5% stop can be
breached in minutes in crypto, and a slow check lets it blow straight through.

It reuses TradingLoop's own _manage_open_positions so close logic + P&L
persistence + calibration updates are identical to a normal cycle — no drift.
After closes, it syncs results into the champion pool.

It does NOT open new trades (that stays with the decision cycle). Pure $ /
no-agent script. Run continuously or on a short cron.

Usage:
  .venv/bin/python -m hedge_fund.trading.heartbeat [--once]
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.brokers.paper import PaperBroker
from hedge_fund.calibration import CalibrationStore
from hedge_fund.data.binance import CcxtSource
from hedge_fund.risk.managed import RiskManager
from hedge_fund.trading.store import TradeStore
from hedge_fund.trading.loop import TradingLoop, CycleResult

STATE = Path(os.environ.get("PAPER_STATE", "state"))
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
START_CASH = 10_000.0
LIVE_STRATEGY = os.environ.get("PAPER_STRATEGY", "sma_stack")
HEARTBEAT_SECONDS = 30


def _build_for(db_path: str, strat: str):
    """Build an isolated loop for one per-strategy account."""
    data = CcxtSource()
    store = TradeStore(db_path)
    calib_path = str(Path(db_path).with_suffix(".json").as_posix()).replace("trades_", "calibration_")
    calib = CalibrationStore(calib_path)
    risk = RiskManager(initial_equity=START_CASH)
    broker = PaperBroker(cash=START_CASH, taker_fee=0.001, slippage=0.0002)
    saved = store.load_account_state()
    if saved:
        broker.restore_state(saved.get("broker", {}))
    loop = TradingLoop(data, broker, risk, calib, store=store, strategy=strat)
    return loop, data, store


def _account_dbs() -> list[tuple[str, str]]:
    """All per-strategy sqlite dbs -> (db_path, strategy_name)."""
    dbs = []
    for p in STATE.glob("trades_*.sqlite"):
        strat = p.name[len("trades_"):-len(".sqlite")]
        dbs.append((str(p), strat))
    return dbs


def heartbeat_once() -> int:
    closed_total = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    accounts = _account_dbs() or [(str(STATE / "trades.sqlite"), LIVE_STRATEGY)]
    data = CcxtSource()
    # one shared price fetch
    px = {}
    for sym in SYMBOLS:
        try:
            px[sym] = data.fetch_price(sym)
        except Exception:
            px[sym] = None
    for db_path, strat in accounts:
        try:
            loop, _data, store = _build_for(db_path, strat)
            if not loop.broker.lots:
                continue
            before = len(loop.broker.lots)
            loop._manage_open_positions(px, now)
            after = len(loop.broker.lots)
            if after < before:
                store.save_account_state({"broker": loop.broker.to_state(),
                                          "risk_peak": loop.risk.peak_equity,
                                          "saved_at": now})
                closed_total += before - after
        except Exception:
            continue
    if closed_total:
        try:
            from hedge_fund.trading.champions import collect_live_results
            collect_live_results()
        except Exception:
            pass
    return closed_total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one pass and exit")
    ap.add_argument("--interval", type=int, default=HEARTBEAT_SECONDS)
    args = ap.parse_args()

    if args.once:
        n = heartbeat_once()
        print(f"heartbeat: closed {n} positions")
        return

    # continuous loop
    while True:
        try:
            n = heartbeat_once()
            if n:
                print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} "
                      f"closed {n} position(s)")
        except Exception as e:
            print(f"heartbeat error: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()