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

from hedge_fund.data.binance import CcxtSource
from hedge_fund.trading.store import TradeStore

SYMBOLS = ["BTC/USDT", "ETH/USDT"]
_PRICE_CACHE: dict = {"ts": 0.0, "prices": {}}
CACHE_TTL = 15.0  # seconds


def live_prices(fresh: bool = False) -> dict:
    """Return current prices for the traded symbols, with a short cache."""
    now = time.time()
    if fresh or (now - _PRICE_CACHE["ts"] > CACHE_TTL):
        src = CcxtSource()
        px = {}
        for sym in SYMBOLS:
            try:
                px[sym] = src.fetch_price(sym)
            except Exception:
                px[sym] = None
        _PRICE_CACHE["ts"] = now
        _PRICE_CACHE["prices"] = px
    return _PRICE_CACHE["prices"]


def live_preview(db: str) -> dict:
    """Compute live equity + per-position live P&L from persisted account state.

    Returns a dict the dashboard can render directly.
    """
    from hedge_fund.brokers.paper import PaperBroker

    st = TradeStore(db)
    saved = st.load_account_state()
    if not saved:
        return {"live_equity": None, "positions": [], "prices": {}, "open_lots": 0, "open_lots_unit": "open_lots"}

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
            }
        )

    return {
        "live_equity": round(live_equity, 2) if live_equity else None,
        "cash": round(broker.cash(), 2),
        "positions": positions,
        "prices": {s: round(p, 2) if p else None for s, p in prices.items()},
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "open_lots": len(broker.lots),
        "open_lots_unit": "open_lots",
    }
