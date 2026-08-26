"""Champion pool manager — bridges LIVE paper-trade results back into evolution.

Keeps a shortlist of up to MAX_CHAMPIONS strategy champions being evaluated in
live paper trading. When a paper trade closes, its realized P&L is attributed
to the strategy that opened it and recorded. A champion accumulates live
evidence; the cap (MAX_CHAMPIONS) is the 'wait for new evaluations' limit —
candidates only enter when there is room or they beat an existing champion.

State lives in state/champions.json so it survives restarts.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

REPO = Path("/opt/data/paper-trading-bot")
TRADES_DB = REPO / "state/trades.sqlite"
CHAMP_FILE = REPO / "state/champions.json"
MAX_CHAMPIONS = 64


def load_pool() -> dict:
    if CHAMP_FILE.exists():
        try:
            return json.loads(CHAMP_FILE.read_text())
        except Exception:
            pass
    return {"champions": [], "synced_until": ""}


def save_pool(st: dict):
    CHAMP_FILE.write_text(json.dumps(st, indent=2))


def _get_iso(s) -> str:
    return (s or "").replace("Z", "+00:00")


def _derive_strategy(condition: str) -> str:
    c = (condition or "").lower()
    if "rsi_" in c:
        return "rsi_30_57"      # current self-learned champion family
    if "sma_stack5" in c:
        return "sma_stack_5_20_50"
    if "sma_stack" in c:
        return "sma_stack_7_25_50"
    if "sma100" in c:
        return "sma_abv_150"
    return c or "unknown"


def collect_live_results() -> dict:
    """Sync closed paper trades from ALL per-strategy accounts into the pool.

    Each isolated account lives in state/trades_<strategy>.sqlite; the strategy
    is the filename (authoritative), not a guess from the signal condition.
    Returns strat -> [pnls] updates applied.
    """
    st = load_pool()
    synced_until = st.get("synced_until", "")

    updates: dict[str, list[float]] = {}
    max_ts = synced_until

    for db in sorted(REPO.glob("state/trades_*.sqlite")):
        strat = db.name[len("trades_"):-len(".sqlite")]
        try:
            con = sqlite3.connect(str(db))
            cur = con.cursor()
            cur.execute("SELECT id,condition,exit_ts,pnl,pnl_pct,hit,exit_reason "
                        "FROM trades WHERE exit_ts IS NOT NULL AND exit_ts != ''")
            rows = cur.fetchall()
            con.close()
        except Exception:
            continue
        for _tid, _condition, exit_ts, pnl, _pct, _hit, _reason in rows:
            ts = exit_ts or ""
            if synced_until and ts.replace("T", " ")[:19] <= synced_until.replace("T", " ")[:19]:
                continue
            updates.setdefault(strat, []).append(float(pnl or 0.0))
            if ts and ts > max_ts:
                max_ts = ts

    idx = {c["name"]: i for i, c in enumerate(st["champions"])}
    for strat, pnls in updates.items():
        if strat in idx:
            c = st["champions"][idx[strat]]
        else:
            c = {"name": strat, "closed": 0, "pnl": 0.0, "wins": 0}
            st["champions"].append(c)
            idx[strat] = len(st["champions"]) - 1
        for p in pnls:
            c["closed"] += 1
            c["pnl"] += p
            if p > 0:
                c["wins"] += 1

    st["champions"] = st["champions"][:MAX_CHAMPIONS]
    # Mark champions "passed" once they have enough live closes to judge.
    for c in st["champions"]:
        if c.get("closed", 0) >= 10 and not c.get("passed"):
            c["passed"] = True
            c["passed_at"] = "now"
    if max_ts:
        st["synced_until"] = max_ts
    save_pool(st)
    return updates


def promote_candidates(candidates: list[dict]) -> dict:
    """Slot backtest top-candidates into the live pool, respecting the cap."""
    st = load_pool()
    existing = {c["name"] for c in st["champions"]}
    room = MAX_CHAMPIONS - len(st["champions"])
    added = []
    for cand in candidates:
        if room <= 0:
            break
        name = cand.get("strategy")
        if name and name not in existing:
            st["champions"].append({
                "name": name, "closed": 0, "pnl": 0.0, "wins": 0,
                "source": "backtest_promotion",
            })
            existing.add(name)
            room -= 1
            added.append(name)
    st["champions"] = st["champions"][:MAX_CHAMPIONS]
    save_pool(st)
    return {"added": added, "pool_size": len(st["champions"])}


def pool_status() -> dict:
    st = load_pool()
    return {"champions": st["champions"], "count": len(st["champions"]),
            "max": MAX_CHAMPIONS}


if __name__ == "__main__":
    updates = collect_live_results()
    st = load_pool()
    print(f"synced {len(updates)} strategies; champion pool {len(st['champions'])}/{MAX_CHAMPIONS}")
    for c in st["champions"]:
        print(f"  {c['name']:<28} closed={c.get('closed',0):>3} "
              f"pnl={c.get('pnl',0):>7.0f} wins={c.get('wins',0)}")