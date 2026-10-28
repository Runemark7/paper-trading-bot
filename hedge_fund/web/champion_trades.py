"""Read-only champion trade log for GET /api/champions/trades.

One entry event per opened lot and one exit event per closed lot, read
from each champion's isolated ``trades_*.sqlite`` with a read-only
connection. P&L on a closed row is the stored value: net of the entry and
exit taker fee (slippage is already in the fill price).

Per-account reads are cached by the DB file token (main file, WAL, SHM),
so a poll that finds no new writes does not open SQLite. Nothing here
writes a DB, the pool, or any discovery state.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.trading.champions import load_pool
from hedge_fund.trading.constants import PAPER_START_CASH
from hedge_fund.trading.open_lots import account_name_from_db, account_slug, paper_book_dbs
from hedge_fund.trading.store import TradeStore, _db_cache_token

MAX_ROWS_PER_ACCOUNT = 500
DEFAULT_EVENT_LIMIT = 200
MAX_EVENT_LIMIT = 1000

_TRADE_COLUMNS = (
    "id, symbol, condition, entry_ts, entry_price, exit_ts, exit_price, size, "
    "exit_reason, entry_fee, exit_fee, pnl, pnl_pct, lot_id"
)

_cache: dict[str, tuple[tuple, dict]] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 64


def clear_champion_trades_cache() -> None:
    with _cache_lock:
        _cache.clear()


def parse_limit(raw: str | None) -> int:
    if raw is None or str(raw).strip() == "":
        return DEFAULT_EVENT_LIMIT
    try:
        n = int(str(raw).strip())
    except ValueError:
        raise ValueError("limit must be an integer") from None
    if n < 1 or n > MAX_EVENT_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_EVENT_LIMIT}")
    return n


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_account_uncached(path: Path) -> dict:
    store = TradeStore.open_readonly(path)
    try:
        conn = store.conn
        try:
            rows = conn.execute(
                f"SELECT {_TRADE_COLUMNS} FROM trades ORDER BY id DESC LIMIT ?",
                (MAX_ROWS_PER_ACCOUNT,),
            ).fetchall()
            agg = conn.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN exit_ts IS NOT NULL THEN 1 ELSE 0 END) AS closed,
                          SUM(CASE WHEN exit_ts IS NULL THEN 1 ELSE 0 END) AS open,
                          SUM(CASE WHEN exit_ts IS NOT NULL AND hit = 1 THEN 1 ELSE 0 END) AS wins,
                          SUM(CASE WHEN exit_ts IS NOT NULL THEN pnl ELSE 0 END) AS realized,
                          SUM(COALESCE(entry_fee, 0)) + SUM(COALESCE(exit_fee, 0)) AS fees
                   FROM trades"""
            ).fetchone()
        except sqlite3.OperationalError:
            rows, agg = [], None
        try:
            snap = store.last_equity_snapshot()
        except sqlite3.OperationalError:
            snap = None
    finally:
        store.close()
    trades = [dict(r) for r in rows]
    summary = {
        "trades_total": int((agg["total"] if agg else 0) or 0),
        "closed": int((agg["closed"] if agg else 0) or 0),
        "open": int((agg["open"] if agg else 0) or 0),
        "wins": int((agg["wins"] if agg else 0) or 0),
        "realized_pnl": round(float((agg["realized"] if agg else 0) or 0.0), 2),
        "fees_paid": round(float((agg["fees"] if agg else 0) or 0.0), 4),
        "equity": _num(snap["equity"]) if snap is not None else None,
        "equity_at": snap["ts"] if snap is not None else None,
    }
    return {"trades": trades, "summary": summary, "truncated": summary["trades_total"] > len(trades)}


def read_account(path: Path) -> dict | None:
    """Cached read of one account DB. ``None`` when the file is missing."""
    token = _db_cache_token(path)
    if token is None:
        return None
    key = str(path)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == token:
            return hit[1]
    rec = _read_account_uncached(path)
    token_after = _db_cache_token(path) or token
    with _cache_lock:
        _cache[key] = (token_after, rec)
        while len(_cache) > _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
    return rec


def _events_for(name: str, trades: list[dict]) -> list[dict]:
    out: list[dict] = []
    for t in trades:
        size = _num(t.get("size"))
        entry_price = _num(t.get("entry_price"))
        base = {
            "champion": name,
            "trade_id": t.get("id"),
            "lot_id": t.get("lot_id"),
            "symbol": t.get("symbol"),
            "size": size,
            "entry_price": entry_price,
        }
        out.append({
            **base,
            "kind": "entry",
            "ts": t.get("entry_ts"),
            "price": entry_price,
            "notional": None if size is None or entry_price is None else round(size * entry_price, 2),
            "fee": _num(t.get("entry_fee")),
            "pnl": None,
            "pnl_pct": None,
            "reason": "entry signal",
            "open": t.get("exit_ts") is None,
        })
        if t.get("exit_ts"):
            exit_price = _num(t.get("exit_price"))
            out.append({
                **base,
                "kind": "exit",
                "ts": t.get("exit_ts"),
                "price": exit_price,
                "notional": None if size is None or exit_price is None else round(size * exit_price, 2),
                "fee": _num(t.get("exit_fee")),
                "pnl": _num(t.get("pnl")),
                "pnl_pct": _num(t.get("pnl_pct")),
                "reason": t.get("exit_reason"),
                "open": False,
            })
    return out


def champion_trades_payload(name: str | None = None, limit: int = DEFAULT_EVENT_LIMIT) -> dict:
    """Trade events for active champions, or for one named paper account."""
    pool_names = [
        c.get("name")
        for c in (load_pool().get("champions") or [])
        if isinstance(c, dict) and isinstance(c.get("name"), str) and c.get("name")
    ]
    by_slug = {account_name_from_db(p): Path(p) for p in paper_book_dbs()}
    wanted = name.strip() if isinstance(name, str) and name.strip() else None
    targets = [wanted] if wanted else pool_names

    champions: list[dict] = []
    events: list[dict] = []
    for champ in targets:
        path = by_slug.get(account_slug(champ))
        rec = read_account(path) if path is not None else None
        summary = dict(rec["summary"]) if rec else {
            "trades_total": 0, "closed": 0, "open": 0, "wins": 0,
            "realized_pnl": 0.0, "fees_paid": 0.0, "equity": None, "equity_at": None,
        }
        champ_events = _events_for(champ, rec["trades"]) if rec else []
        open_entries = [e for e in champ_events if e["kind"] == "entry" and e["open"]]
        champions.append({
            "name": champ,
            "active": champ in pool_names,
            "has_account": rec is not None,
            "start_cash": PAPER_START_CASH,
            "truncated": bool(rec and rec["truncated"]),
            "open_entries": open_entries,
            **summary,
        })
        events.extend(champ_events)

    events.sort(key=lambda e: (str(e.get("ts") or ""), 1 if e["kind"] == "exit" else 0), reverse=True)
    return {
        "paper_only": True,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "name": wanted,
        "found": bool(champions and champions[0]["has_account"]) if wanted else True,
        "limit": limit,
        "total_events": len(events),
        "events": events[:limit],
        "champions": champions,
        "pnl_note": (
            "pnl is net of the entry and exit taker fee; slippage is inside the fill price. "
            "Equity is the account's last snapshot from the live cycle."
        ),
    }
