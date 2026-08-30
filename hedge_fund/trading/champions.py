"""Champion pool manager — continuous pipeline.

Rules (hedge_fund.trading.constants — do not document different numbers):
1. Target active capacity: MAX_ACTIVE_CHAMPIONS (1000).
2. Evaluation threshold: TRADE_EVALUATION_LIMIT (25) closed paper trades.
   Positive paper P&L → GRADUATED_PAPER (graduated paper, not live trading).
   Else REJECTED_NEGATIVE_PNL. Results go to graduated.json.
3. If active champions < MAX_ACTIVE_CHAMPIONS, backtest candidates and promote
   the top performers to fill slots.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.paths import state_root
from hedge_fund.trading.constants import (
    GRADUATED_PAPER,
    MAX_ACTIVE_CHAMPIONS,
    REJECTED_NEGATIVE_PNL,
    TRADE_EVALUATION_LIMIT,
)
from hedge_fund.trading.store import connect_sqlite

# Re-export so existing `from hedge_fund.trading.champions import TRADE_EVALUATION_LIMIT` still works.
__all__ = [
    "GRADUATED_PAPER",
    "MAX_ACTIVE_CHAMPIONS",
    "REJECTED_NEGATIVE_PNL",
    "TRADE_EVALUATION_LIMIT",
    "collect_live_results",
    "load_graduated",
    "load_pool",
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


def collect_live_results() -> dict:
    """Sync closed paper trades from ALL per-strategy accounts.

    Graduates strategies with >= TRADE_EVALUATION_LIMIT closed trades and
    removes them from the active testing pool.
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

    # Update active champions
    idx = {c["name"]: i for i, c in enumerate(st["champions"])}
    for strat, trade_list in updates.items():
        if strat in idx:
            c = st["champions"][idx[strat]]
        else:
            c = {"name": strat, "closed": 0, "pnl": 0.0, "wins": 0}
            st["champions"].append(c)
            idx[strat] = len(st["champions"]) - 1
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

            grad_entry = {
                "name": c["name"],
                "closed_trades": c["closed"],
                "total_pnl": round(c["pnl"], 2),
                "wins": c["wins"],
                "win_rate_pct": win_rate,
                "graduated_at": datetime.now(timezone.utc).isoformat(),
                "status": GRADUATED_PAPER if c["pnl"] > 0 else REJECTED_NEGATIVE_PNL,
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

    save_pool(st)
    if graduated_now:
        save_graduated(grad_list)

    return {
        "updates": updates,
        "graduated": graduated_now,
        "active_count": len(st["champions"])
    }


def promote_candidates(candidates: list[dict]) -> dict:
    """Add top backtested candidate strategies until pool reaches MAX_ACTIVE_CHAMPIONS (1000)."""
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
                    "source": "sweep_promotion"
                })
                existing.add(name)
                added.append(name)

    save_pool(st)
    return {"added": added, "active_count": len(st["champions"]), "target": MAX_ACTIVE_CHAMPIONS}


def pool_status() -> dict:
    st = load_pool()
    grad = load_graduated()
    return {
        "active_champions": st["champions"],
        "active_count": len(st["champions"]),
        "target_active": MAX_ACTIVE_CHAMPIONS,
        "evaluation_limit": TRADE_EVALUATION_LIMIT,
        "graduated_count": len(grad),
        "graduated": grad,
        "synced_until": st.get("synced_until") or None,
    }
