"""One definition of open paper lots for the dashboard APIs.

Overview, Positions, Champions, StatusBar, /api/status, /api/live, and
/api/champions must use this helper so they cannot drift:

* Unit is an open **lot** (BTC + ETH on the same account = 2).
* Source is the same PAPER_STATE DBs /api/live already reads
  (isolated ``trades_*.sqlite``, else legacy ``trades.sqlite``).
"""
from __future__ import annotations

from pathlib import Path

from hedge_fund.paths import state_root
from hedge_fund.trading.store import TradeStore


def account_name_from_db(path: str | Path) -> str:
    p = Path(path)
    name = p.name
    if name.startswith("trades_") and name.endswith(".sqlite"):
        return name[len("trades_") : -len(".sqlite")]
    if name == "trades.sqlite":
        return "legacy"
    return p.stem


def account_slug(name: str) -> str:
    return name.replace("/", "_").replace(":", "_")


def paper_book_dbs() -> list[str]:
    """The paper-book DBs /api/live uses: isolated accounts, else legacy."""
    root = state_root()
    per_strategy = sorted(root.glob("trades_*.sqlite"))
    if per_strategy:
        return [str(p) for p in per_strategy]
    legacy = root / "trades.sqlite"
    if legacy.exists():
        return [str(legacy)]
    return []


def open_lot_count_from_saved(saved: dict | None) -> int:
    """Count broker lots in a persisted account_state blob."""
    if not saved:
        return 0
    broker = saved.get("broker") or {}
    lots = broker.get("lots") or broker.get("positions") or []
    return len(lots)


def lots_for_account(by_account: dict[str, int], name: str) -> int:
    if name in by_account:
        return by_account[name]
    return by_account.get(account_slug(name), 0)


def open_lots_by_account() -> dict[str, int]:
    out: dict[str, int] = {}
    for db in paper_book_dbs():
        name = account_name_from_db(db)
        try:
            st = TradeStore(db)
            out[name] = open_lot_count_from_saved(st.load_account_state())
        except Exception:
            out[name] = 0
    return out


def open_lots_snapshot() -> dict:
    by_account = open_lots_by_account()
    return {
        "unit": "open_lots",
        "by_account": by_account,
        "open_lots": sum(by_account.values()),
        "accounts": list(by_account.keys()),
    }


def attach_open_lots(payload: dict) -> dict:
    """Stamp a /api/champions-style payload with the shared lot counts."""
    snap = open_lots_snapshot()
    by_account = snap["by_account"]
    for row in payload.get("active_champions") or []:
        name = row.get("name") or ""
        row["open_lots"] = lots_for_account(by_account, name)
    payload["open_lots"] = snap["open_lots"]
    payload["open_lots_by_account"] = by_account
    payload["open_lots_unit"] = "open_lots"
    return payload
