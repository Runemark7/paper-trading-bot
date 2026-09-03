"""Live preview — current market prices + live paper equity/P&L.

The dashboard is normally a static snapshot regenerated after each
5m cycle. This module lets the web service re-fetch live prices and
recompute the account's real-time value on every page load, so the dashboard
feels live between cycles.

Prices are cached briefly (default 15s) so a page refresh doesn't hammer the
exchange, but a fresh fetch with each page view keeps it current.
"""

from __future__ import annotations

import time

from hedge_fund.risk.managed import RiskManager
from hedge_fund.risk.rm_v1 import TAKE_PROFIT_RR
from hedge_fund.trading.store import TradeStore

SYMBOLS = ["BTC/USDT", "ETH/USDT"]
_PRICE_CACHE: dict = {"ts": 0.0, "prices": {}}
CACHE_TTL = 15.0  # seconds


def take_profit_price(entry: float, stop: float) -> float:
    """Same 2:1 TP as TradingLoop: ``RiskManager.take_profit_price(..., TAKE_PROFIT_RR)``.

    Do not hardcode a 5% target — live TP is stop-distance × rr.
    """
    return RiskManager.take_profit_price(None, float(entry), float(stop), TAKE_PROFIT_RR)


def _open_lot_entry_times(st: TradeStore) -> dict[tuple, str]:
    """Map (symbol, lot_id) → entry_ts for still-open store rows."""
    out: dict[tuple, str] = {}
    try:
        rows = st.conn.execute(
            "SELECT symbol, lot_id, entry_ts FROM trades WHERE exit_ts IS NULL"
        ).fetchall()
    except Exception:
        return out
    for r in rows:
        if r["lot_id"] is None:
            continue
        out[(r["symbol"], r["lot_id"])] = r["entry_ts"]
    return out


def serialize_open_lot(
    lot,
    times: dict | None = None,
    account: str | None = None,
    mark: float | None = None,
    candles: list | None = None,
) -> dict:
    """One pyramid lot: entry / stop / TP plus this-bar signal and path-in-trade."""
    from hedge_fund.web.lot_health import annotate_open_lot

    times = times or {}
    entry = float(lot.entry_price)
    stop = float(lot.stop_loss)
    tp = take_profit_price(entry, stop)
    row = {
        "lot_id": lot.lot_id,
        "symbol": lot.ticker,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "take_profit": round(tp, 2),
        "quantity": round(lot.quantity, 5),
        "condition": lot.entry_condition,
        "entry_ts": times.get((lot.ticker, lot.lot_id)),
        "account": account,
    }
    return annotate_open_lot(
        row,
        mark=mark,
        candles=candles,
        strategy_account=account,
    )


def live_prices(fresh: bool = False) -> dict:
    """Return current prices for the traded symbols, with a short cache.

    Reuses the paper-chart ``CcxtSource`` (never a new ``ccxt.binance()`` per
    request). If the venue is busy paging chart history, keep the stale cache
    or mark lots at entry — do not wait on a 3–7 day kline fetch.
    Display only — not a live broker.
    """
    now = time.time()
    cached = _PRICE_CACHE["prices"]
    if not fresh and cached and (now - _PRICE_CACHE["ts"] <= CACHE_TTL):
        return cached
    from hedge_fund.web.candles import try_live_prices

    try:
        px = try_live_prices(SYMBOLS)
    except Exception:
        px = {}
    if px:
        _PRICE_CACHE["ts"] = now
        _PRICE_CACHE["prices"] = px
        return px
    if cached:
        return cached
    return {s: None for s in SYMBOLS}


def live_preview(db: str, closes_by_symbol: dict | None = None) -> dict:
    """Compute live equity + per-position live P&L from persisted account state.

    ``closes_by_symbol`` is 5m OHLCV fetched once by ``/api/live`` (BTC + ETH).
    When omitted or a symbol is missing, ``signal`` is ``unknown`` and ``path``
    still comes from the last mark vs this lot's stop/TP.

    Returns a dict the dashboard can render directly.
    """
    from hedge_fund.brokers.paper import PaperBroker
    from hedge_fund.trading.open_lots import account_name_from_db

    closes_by_symbol = closes_by_symbol or {}
    account = account_name_from_db(db)
    st = TradeStore(db)
    saved = st.load_account_state()
    if not saved:
        return {
            "live_equity": None,
            "positions": [],
            "lots": [],
            "prices": {},
            "open_lots": 0,
            "open_lots_unit": "open_lots",
        }

    broker = PaperBroker(cash=10000)
    broker.restore_state(saved.get("broker", {}))
    prices = live_prices()
    # prices.get(sym) can be None when the exchange fetch failed; the key
    # still exists, so PaperBroker.equity would do float * None. Mark those
    # lots at entry so the position still renders.
    marks = {}
    for lot in broker.lots:
        px = prices.get(lot.ticker)
        marks[lot.ticker] = px if px is not None else lot.entry_price
    for sym, px in prices.items():
        if sym not in marks:
            marks[sym] = px if px is not None else 0.0

    # live equity = cash + open positions at current (or entry) price
    live_equity = broker.equity(marks)
    times = _open_lot_entry_times(st)

    positions = []
    # group lots by symbol -> aggregate, and list lots individually
    from collections import defaultdict
    by_sym: dict[str, dict] = defaultdict(lambda: {"qty": 0.0, "lots": [], "entry_w": 0.0})
    for lot in broker.lots:
        sym = lot.ticker
        b = by_sym[sym]
        b["qty"] += lot.quantity
        b["entry_w"] += lot.entry_price * lot.quantity
        b["lots"].append(lot)
    for sym, b in by_sym.items():
        cur = prices.get(sym)
        avg_entry = b["entry_w"] / b["qty"] if b["qty"] else 0.0
        value = cur * b["qty"] if cur else None
        entry_val = avg_entry * b["qty"]
        unrealized = (value - entry_val) if cur else None
        upnl_pct = (cur / avg_entry - 1) if cur else None
        entry = round(avg_entry, 2)
        stop = round(min((l.stop_loss for l in b["lots"]), default=0), 2)
        upnl = round(unrealized, 2) if unrealized is not None else None
        upct = round(upnl_pct, 4) if upnl_pct is not None else None
        lot_rows = [
            serialize_open_lot(
                l,
                times,
                account=account,
                mark=prices.get(l.ticker),
                candles=closes_by_symbol.get(l.ticker),
            )
            for l in b["lots"]
        ]
        positions.append(
            {
                "symbol": sym,
                "quantity": round(b["qty"], 5),
                "entry": entry,
                "entry_price": entry,  # alias for the React client
                "stop": stop,
                "stop_loss": stop,
                "current": round(cur, 2) if cur else None,
                "value": round(value, 2) if value else None,
                "unrealized_pnl": upnl,
                "unrealized_pct": upct,
                "pnl": upnl,
                "pnl_pct": upct,
                "condition": b["lots"][0].entry_condition if b["lots"] else "",
                "lot_count": len(b["lots"]),
                "lots": lot_rows,
            }
        )

    flat_lots = [lot for p in positions for lot in p.get("lots") or []]
    return {
        "live_equity": round(live_equity, 2) if live_equity else None,
        "cash": round(broker.cash(), 2),
        "positions": positions,
        "lots": flat_lots,
        "prices": {s: round(p, 2) if p else None for s, p in prices.items()},
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "open_lots": len(broker.lots),
        "open_lots_unit": "open_lots",
    }
