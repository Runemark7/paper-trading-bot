"""Champion pool manager — continuous pipeline.

Rules (hedge_fund.trading.constants — do not document different numbers):
1. Target active capacity: MAX_ACTIVE_CHAMPIONS (20). Replenish only into free slots.
2. Evaluation threshold: TRADE_EVALUATION_LIMIT (80) closed paper trades.
   Paper PnL greater than buy-and-hold of the same assets over the same
   period (after fees) → GRADUATED_PAPER (graduated paper, not live trading).
   Else REJECTED_NEGATIVE_PNL. Results go to graduated.json.
3. If active champions < MAX_ACTIVE_CHAMPIONS, 5m-qualified candidates fill
   free slots only — never a 4h admit bar.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.paths import state_root
from hedge_fund.trading.buy_and_hold import buy_and_hold_from_trades
from hedge_fund.trading.constants import (
    GRADUATED_PAPER,
    MAX_ACTIVE_CHAMPIONS,
    PAPER_START_CASH,
    REJECTED_NEGATIVE_PNL,
    TRADE_EVALUATION_LIMIT,
)
from hedge_fund.trading.open_lots import account_slug, attach_open_lots
from hedge_fund.trading.store import TradeStore, connect_sqlite

# Re-export so existing `from hedge_fund.trading.champions import TRADE_EVALUATION_LIMIT` still works.
__all__ = [
    "GRADUATED_PAPER",
    "MAX_ACTIVE_CHAMPIONS",
    "REJECTED_NEGATIVE_PNL",
    "TRADE_EVALUATION_LIMIT",
    "attach_open_lots",
    "backfill_champion_since",
    "collect_live_results",
    "infer_champion_since",
    "load_graduated",
    "load_pool",
    "paper_beats_buy_and_hold",
    "pool_status",
    "promote_candidates",
    "save_graduated",
    "save_pool",
]


def _champ_file() -> Path:
    return state_root() / "champions.json"


def _graduated_file() -> Path:
    return state_root() / "graduated.json"


def load_pool() -> dict:
    path = _champ_file()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"champions": [], "synced_until": ""}


def save_pool(st: dict):
    path = _champ_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    prev_by_name: dict[str, dict] = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text())
            prev_by_name = {
                c["name"]: c
                for c in (prev.get("champions") or [])
                if isinstance(c, dict) and c.get("name")
            }
        except Exception:
            prev_by_name = {}
    for c in st.get("champions") or []:
        if not isinstance(c, dict):
            continue
        prev = prev_by_name.get(c.get("name"))
        if prev and prev.get("champion_since"):
            c["champion_since"] = prev["champion_since"]
    path.write_text(json.dumps(st, indent=2))


def load_graduated() -> list[dict]:
    path = _graduated_file()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return []


def save_graduated(grad_list: list[dict]):
    path = _graduated_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(grad_list, indent=2))


def paper_beats_buy_and_hold(
    paper_pnl: float,
    trades: list[dict],
    db_path: Path | None = None,
    start_cash: float = PAPER_START_CASH,
) -> tuple[bool, float | None]:
    """True iff paper PnL strictly exceeds B&H of the same assets / period after fees.

    Prefers the live overlay on equity_snapshots; falls back to first-entry /
    last-exit reconstruction from the closed-trade history.
    """
    bh_pnl: float | None = None
    if db_path and db_path.exists():
        try:
            store = TradeStore(db_path)
            snaps = store.equity_history()
            if snaps:
                last = snaps[-1]
                baseline = last["baseline"] if "baseline" in last.keys() else None
                if baseline is not None:
                    bh_pnl = float(baseline) - start_cash
        except Exception:
            bh_pnl = None
    if bh_pnl is None:
        bh_pnl = buy_and_hold_from_trades(trades, start_cash)
    if bh_pnl is None:
        return False, None
    return paper_pnl > bh_pnl, bh_pnl


def collect_live_results() -> dict:
    """Sync closed paper trades from ALL per-strategy accounts.

    Graduates strategies with >= TRADE_EVALUATION_LIMIT closed trades and
    removes them from the active testing pool. Graduation requires beating
    buy-and-hold, not merely paper PnL > 0.
    """
    st = load_pool()
    synced_until = st.get("synced_until", "")
    root = state_root()

    updates: dict[str, list[dict]] = {}
    max_ts = synced_until

    for db in sorted(root.glob("trades_*.sqlite")):
        strat = db.name[len("trades_"):-len(".sqlite")]
        try:
            con = connect_sqlite(db)
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT id, symbol, entry_price, exit_price, exit_ts, pnl, pnl_pct, hit, exit_reason "
                               "FROM trades WHERE exit_ts IS NOT NULL AND exit_ts != ''").fetchall()
            con.close()
        except Exception:
            continue
        for r in rows:
            ts = r["exit_ts"] or ""
            if synced_until and ts.replace("T", " ")[:19] <= synced_until.replace("T", " ")[:19]:
                continue
            updates.setdefault(strat, []).append({
                "id": r["id"],
                "symbol": r["symbol"],
                "entry_price": float(r["entry_price"] or 0.0),
                "exit_price": float(r["exit_price"] or 0.0),
                "exit_ts": ts,
                "pnl": float(r["pnl"] or 0.0),
                "pnl_pct": float(r["pnl_pct"] or 0.0),
                "hit": r["hit"],
                "reason": r["exit_reason"]
            })
            if ts and ts > max_ts:
                max_ts = ts

    # Update active champions only — stray DBs must not stuff the 20-slot pool.
    idx = {c["name"]: i for i, c in enumerate(st["champions"])}
    for strat, trade_list in updates.items():
        if strat not in idx:
            continue
        c = st["champions"][idx[strat]]
        for t in trade_list:
            c["closed"] += 1
            c["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                c["wins"] += 1

    graduated_now = []
    remaining_champions = []
    grad_list = load_graduated()
    existing_grad_names = {g["name"] for g in grad_list}

    for c in st["champions"]:
        if c.get("closed", 0) >= TRADE_EVALUATION_LIMIT:
            win_rate = round((c["wins"] / c["closed"]) * 100, 1) if c["closed"] else 0.0

            strat_history = []
            db_path = root / f"trades_{c['name'].replace('/','_').replace(':','_')}.sqlite"
            if db_path.exists():
                try:
                    con = connect_sqlite(db_path)
                    con.row_factory = sqlite3.Row
                    all_rows = con.execute("SELECT symbol, entry_price, exit_price, entry_ts, exit_ts, size, pnl, pnl_pct, hit, exit_reason FROM trades WHERE exit_ts IS NOT NULL ORDER BY id ASC").fetchall()
                    con.close()
                    for ar in all_rows:
                        strat_history.append({
                            "symbol": ar["symbol"],
                            "entry_price": ar["entry_price"],
                            "exit_price": ar["exit_price"],
                            "entry_ts": ar["entry_ts"],
                            "exit_ts": ar["exit_ts"],
                            "size": ar["size"],
                            "pnl": round(ar["pnl"], 2) if ar["pnl"] is not None else 0.0,
                            "pnl_pct": round(ar["pnl_pct"], 4) if ar["pnl_pct"] is not None else 0.0,
                            "hit": ar["hit"],
                            "exit_reason": ar["exit_reason"]
                        })
                except Exception:
                    pass

            beats, bh_pnl = paper_beats_buy_and_hold(
                float(c["pnl"]), strat_history, db_path=db_path if db_path.exists() else None,
            )
            grad_entry = {
                "name": c["name"],
                "closed_trades": c["closed"],
                "total_pnl": round(c["pnl"], 2),
                "buy_and_hold_pnl": None if bh_pnl is None else round(bh_pnl, 2),
                "wins": c["wins"],
                "win_rate_pct": win_rate,
                "graduated_at": datetime.now(timezone.utc).isoformat(),
                "status": GRADUATED_PAPER if beats else REJECTED_NEGATIVE_PNL,
                "trade_history": strat_history
            }
            if c["name"] not in existing_grad_names:
                grad_list.append(grad_entry)
                existing_grad_names.add(c["name"])
            else:
                for i_g, g_item in enumerate(grad_list):
                    if g_item["name"] == c["name"]:
                        grad_list[i_g] = grad_entry
                        break
            graduated_now.append(grad_entry)
        else:
            remaining_champions.append(c)

    st["champions"] = remaining_champions
    if max_ts:
        st["synced_until"] = max_ts

    backfill_champion_since(st)
    save_pool(st)
    if graduated_now:
        save_graduated(grad_list)

    return {
        "updates": updates,
        "graduated": graduated_now,
        "active_count": len(st["champions"])
    }


def promote_candidates(candidates: list[dict]) -> dict:
    """Add 5m-qualified names until pool reaches MAX_ACTIVE_CHAMPIONS (20)."""
    st = load_pool()
    grad_list = load_graduated()
    existing = {c["name"] for c in st["champions"]}.union({g["name"] for g in grad_list})

    needed = MAX_ACTIVE_CHAMPIONS - len(st["champions"])
    added = []
    if needed > 0:
        for cand in candidates:
            if len(added) >= needed:
                break
            name = cand.get("strategy")
            if name and name not in existing:
                st["champions"].append({
                    "name": name,
                    "closed": 0,
                    "pnl": 0.0,
                    "wins": 0,
                    "source": cand.get("source") or "sweep_promotion",
                    "champion_since": datetime.now(timezone.utc).isoformat(),
                })
                existing.add(name)
                added.append(name)

    backfill_champion_since(st)
    save_pool(st)
    return {"added": added, "active_count": len(st["champions"]), "target": MAX_ACTIVE_CHAMPIONS}


def _load_discovery_log() -> list[dict]:
    path = state_root() / "discovery_log.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _earliest_trade_entry_ts(name: str) -> str | None:
    """Earliest entry_ts on this account (open or closed). Not file mtime."""
    if not name:
        return None
    db = state_root() / f"trades_{account_slug(name)}.sqlite"
    if not db.exists():
        return None
    try:
        con = connect_sqlite(db)
        row = con.execute(
            "SELECT MIN(entry_ts) FROM trades "
            "WHERE entry_ts IS NOT NULL AND entry_ts != ''"
        ).fetchone()
        con.close()
    except Exception:
        return None
    ts = row[0] if row else None
    return ts or None


def _qualified_discovery_tested_at(name: str, log: list[dict]) -> str | None:
    found: list[str] = []
    for row in log:
        if not isinstance(row, dict):
            continue
        if row.get("strategy") != name or not row.get("qualified"):
            continue
        ts = row.get("tested_at")
        if ts:
            found.append(str(ts))
    return min(found) if found else None


def infer_champion_since(name: str, discovery_log: list[dict] | None = None) -> str | None:
    """Honest start date: first paper trade, else qualified discovery tested_at."""
    ts = _earliest_trade_entry_ts(name)
    if ts:
        return ts
    if discovery_log is None:
        discovery_log = _load_discovery_log()
    return _qualified_discovery_tested_at(name, discovery_log)


def backfill_champion_since(st: dict) -> bool:
    """Fill missing champion_since from trades / discovery. Never stamps now()."""
    changed = False
    log: list[dict] | None = None
    for c in st.get("champions") or []:
        if not isinstance(c, dict) or c.get("champion_since"):
            continue
        if log is None:
            log = _load_discovery_log()
        ts = infer_champion_since(c.get("name") or "", discovery_log=log)
        if ts:
            c["champion_since"] = ts
            changed = True
    return changed


def pool_status() -> dict:
    st = load_pool()
    if backfill_champion_since(st):
        save_pool(st)
    champs = []
    for c in st["champions"]:
        row = dict(c)
        row["champion_since"] = c.get("champion_since") or None
        champs.append(row)
    grad = load_graduated()
    return attach_open_lots({
        "active_champions": champs,
        "active_count": len(st["champions"]),
        "target_active": MAX_ACTIVE_CHAMPIONS,
        "evaluation_limit": TRADE_EVALUATION_LIMIT,
        "graduated_count": len(grad),
        "graduated": grad,
        "synced_until": st.get("synced_until") or None,
    })
