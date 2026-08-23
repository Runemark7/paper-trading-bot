"""Live preview — current market prices + live paper equity/P&L.

The dashboard is normally a static snapshot regenerated after each cron
cycle (every 6h). This module lets the web service re-fetch live prices and
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
        return {"live_equity": None, "positions": [], "prices": {}}

    broker = PaperBroker(cash=10000)
    broker.restore_state(saved.get("broker", {}))
    prices = live_prices()

    # live equity = cash + open positions at current price
    live_equity = broker.equity(prices)

    positions = []
    for sym, pos in broker.positions.items():
        cur = prices.get(sym)
        value = cur * pos.quantity if cur else None
        entry_val = pos.entry_price * pos.quantity
        unrealized = (value - entry_val) if cur else None
        upnl_pct = (cur / pos.entry_price - 1) if cur else None
        positions.append(
            {
                "symbol": sym,
                "quantity": round(pos.quantity, 5),
                "entry": round(pos.entry_price, 2),
                "stop": round(pos.stop_loss, 2),
                "current": round(cur, 2) if cur else None,
                "value": round(value, 2) if value else None,
                "unrealized_pnl": round(unrealized, 2) if unrealized is not None else None,
                "unrealized_pct": round(upnl_pct, 4) if upnl_pct is not None else None,
                "condition": pos.entry_condition,
            }
        )

    return {
        "live_equity": round(live_equity, 2) if live_equity else None,
        "cash": round(broker.cash(), 2),
        "positions": positions,
        "prices": {s: round(p, 2) if p else None for s, p in prices.items()},
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    }
