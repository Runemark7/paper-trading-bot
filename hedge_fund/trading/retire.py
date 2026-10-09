"""Retire champions that failed a requalify batch. Archive, never delete.

Retire means: the name leaves ``champions.json`` / ``graduated.json`` and its
row moves to ``retired.json`` with a reason and batch id. Trade DBs
(``trades_*.sqlite``), discovery_log, discovery_tested, the results log, and
fail-once stay untouched. A retired name is blocked from auto re-admit and
from re-mint like any pooled name, and ``paper_book_dbs`` leaves its account
off the live book.

``RETIRE_AUTO_BATCH`` runs once at server start (no token), like the
requalify autoseed. It retires every champion / graduated name that finished
requalify batch ``RETIRE_FROM_REQUALIFY_BATCH`` and did not pass. The batch
id is recorded in ``retired.json["applied"]`` so it never runs again. If the
requalify batch has no finished failures yet, nothing is recorded and the
next start tries again. Gate thresholds are not read or changed here.

``ORPHAN_LIVE_BATCH`` runs once the same way. It archives paper accounts that
still sit on the live book with no champion, graduated or retired record and
are not the ``PAPER_STRATEGY`` fallback (rows dropped from the pool by the
2026-09-13 cull_undated). It only touches names in ``ORPHAN_LIVE_NAMES`` and
skips any account that still holds an open lot.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from hedge_fund.trading.champions import (
    _retired_file,
    load_graduated,
    load_pool,
    load_retired,
    save_graduated,
    save_pool,
)
from hedge_fund.trading.store import paper_state_lock

# Alexander, 2026-10-08: retire all 127 (0/127 pass the 23-window gate).
RETIRE_AUTO_BATCH = "requalify-gate23-20261008"
RETIRE_FROM_REQUALIFY_BATCH = "gate23-20261008"
RETIRE_REASON = "failed gate23 requalify 2026-10-08"

# Alexander, 2026-10-08: archive the 16 dip_* accounts left on /api/live
# after the 2026-09-13 cull_undated. Explicit list from prod so the rule
# below cannot reach anything else.
ORPHAN_LIVE_BATCH = "orphan-live-20261008"
ORPHAN_LIVE_REASON = "orphan live account after cull_undated 2026-09-13"
ORPHAN_LIVE_NAMES = frozenset({
    "dip_10b_lt5pc",
    "dip_12b_lt3pc&ema_abv_50",
    "dip_12b_lt3pc&sma_abv_50",
    "dip_12b_lt4pc&sma_abv_75",
    "dip_12b_lt5pc",
    "dip_15b_lt5pc",
    "dip_15b_lt6pc",
    "dip_18b_lt5pc",
    "dip_20b_lt5pc",
    "dip_2b_lt4pc",
    "dip_3b_lt5pc",
    "dip_4b_lt5pc",
    "dip_6b_lt5pc",
    "dip_6b_lt6pc",
    "dip_8b_lt4pc",
    "dip_8b_lt5pc",
})

log = logging.getLogger(__name__)


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def save_retired(state: dict) -> None:
    """Caller holds ``paper_state_lock('discovery')``."""
    path = _retired_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "retired": state.get("retired") or {},
        "applied": state.get("applied") or [],
        "batches": state.get("batches") or [],
        "paper_only": True,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def requalify_failed_names(batch_id: str) -> dict[str, dict]:
    """Names that finished ``batch_id`` and did not pass, with their result."""
    from hedge_fund.trading.requalify import load_requalify_state

    out: dict[str, dict] = {}
    for name, meta in load_requalify_state()["names"].items():
        if not isinstance(name, str) or not isinstance(meta, dict):
            continue
        if meta.get("batch_id") != batch_id or meta.get("status") != "done":
            continue
        result = meta.get("result") if isinstance(meta.get("result"), dict) else {}
        if result.get("qualified"):
            continue
        out[name] = result
    return out


def _requalify_note(result: dict | None) -> dict:
    if not result:
        return {}
    keys = ("trades", "sharpe", "test_pnl", "bh_oos_pnl", "fail_reasons", "regimes_tested", "tested_at")
    return {k: result.get(k) for k in keys if k in result}


def retire_names_unlocked(
    names: Iterable[str],
    *,
    batch_id: str,
    reason: str,
    now: datetime,
    requalify: dict[str, dict] | None = None,
) -> dict:
    """Move ``names`` from the pool / graduated list to ``retired.json``.

    Caller holds the discovery lock. Unknown names are ignored. Returns the
    retired names per role.
    """
    wanted = {n.strip() for n in names if isinstance(n, str) and n.strip()}
    stamp = _iso(now)
    state = load_retired()
    pool = load_pool()
    grads = load_graduated()

    retired_champions: list[str] = []
    kept_champions: list[dict] = []
    for row in pool.get("champions") or []:
        name = row.get("name") if isinstance(row, dict) else None
        if isinstance(name, str) and name in wanted:
            state["retired"][name] = {
                "name": name,
                "role": "champion",
                "retired_at": stamp,
                "batch_id": batch_id,
                "reason": reason,
                "requalify": _requalify_note((requalify or {}).get(name)),
                "record": row,
            }
            retired_champions.append(name)
        else:
            kept_champions.append(row)

    retired_graduated: list[str] = []
    kept_grads: list[dict] = []
    for row in grads:
        name = row.get("name") if isinstance(row, dict) else None
        if isinstance(name, str) and name in wanted:
            state["retired"][name] = {
                "name": name,
                "role": "graduated",
                "retired_at": stamp,
                "batch_id": batch_id,
                "reason": reason,
                "requalify": _requalify_note((requalify or {}).get(name)),
                "record": row,
            }
            retired_graduated.append(name)
        else:
            kept_grads.append(row)

    # Archive first so a crash between writes never loses a row.
    state["batches"].append({
        "batch_id": batch_id,
        "retired_at": stamp,
        "reason": reason,
        "champions": len(retired_champions),
        "graduated": len(retired_graduated),
    })
    save_retired(state)
    if retired_champions:
        pool["champions"] = kept_champions
        save_pool(pool)
    if retired_graduated:
        save_graduated(kept_grads)
    return {
        "batch_id": batch_id,
        "reason": reason,
        "retired_champions": retired_champions,
        "retired_graduated": retired_graduated,
        "active_count": len(kept_champions),
        "graduated_count": len(kept_grads),
    }


def ensure_auto_retire_unlocked(now: datetime) -> dict | None:
    """Apply ``RETIRE_AUTO_BATCH`` once. Caller holds the discovery lock."""
    if RETIRE_AUTO_BATCH in load_retired()["applied"]:
        return None
    failed = requalify_failed_names(RETIRE_FROM_REQUALIFY_BATCH)
    if not failed:
        return None
    out = retire_names_unlocked(
        failed,
        batch_id=RETIRE_AUTO_BATCH,
        reason=RETIRE_REASON,
        now=now,
        requalify=failed,
    )
    state = load_retired()
    state["applied"].append(RETIRE_AUTO_BATCH)
    save_retired(state)
    return out


def run_auto_retire() -> dict | None:
    with paper_state_lock("discovery"):
        out = ensure_auto_retire_unlocked(datetime.now(timezone.utc))
    if out is not None:
        log.info(
            "retire: batch %s retired %d champions, %d graduated",
            RETIRE_AUTO_BATCH,
            len(out["retired_champions"]),
            len(out["retired_graduated"]),
        )
    return out


def _account_record(db: Path) -> dict:
    """Summary of an archived account. The DB itself stays on disk."""
    from hedge_fund.trading.open_lots import open_lot_count_from_saved
    from hedge_fund.trading.store import TradeStore

    record: dict = {"account_db": db.name}
    try:
        with TradeStore.open_readonly(db) as st:
            saved = st.load_account_state()
            stats = st.stats()
    except Exception:
        record["open_lots"] = None
        return record
    record["open_lots"] = open_lot_count_from_saved(saved)
    record["closed"] = int(stats.get("closed") or 0)
    record["hits"] = int(stats.get("hits") or 0)
    record["pnl"] = round(float(stats.get("total_pnl") or 0.0), 2)
    return record


def orphan_live_accounts(allow: Iterable[str] = ORPHAN_LIVE_NAMES) -> dict[str, dict]:
    """Live-book accounts with no champion / graduated / retired record.

    Rule: an isolated ``trades_*.sqlite`` account whose name is not a pooled
    champion, not graduated, not already retired and not the fallback
    ``PAPER_STRATEGY`` account. Restricted to ``allow``. Accounts that still
    hold an open lot are left alone.
    """
    from hedge_fund.paths import state_root
    from hedge_fund.trading.open_lots import account_name_from_db, account_slug

    allowed = {n for n in allow if isinstance(n, str) and n}
    # The fallback account (run_isolated.LIVE_STRATEGY) always stays.
    keep = {account_slug(os.environ.get("PAPER_STRATEGY", "sma_stack")), "sma_stack"}
    for row in load_pool().get("champions") or []:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            keep.add(account_slug(row["name"]))
    for row in load_graduated():
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            keep.add(account_slug(row["name"]))
    keep |= {account_slug(n) for n in load_retired()["retired"] if isinstance(n, str)}

    out: dict[str, dict] = {}
    for db in sorted(state_root().glob("trades_*.sqlite")):
        name = account_name_from_db(db)
        if name in keep or name not in allowed:
            continue
        record = _account_record(db)
        if record.get("open_lots") != 0:
            log.warning("retire: orphan %s skipped (open_lots=%s)", name, record.get("open_lots"))
            continue
        out[name] = record
    return out


def ensure_orphan_live_retire_unlocked(now: datetime) -> dict | None:
    """Apply ``ORPHAN_LIVE_BATCH`` once. Caller holds the discovery lock."""
    state = load_retired()
    if ORPHAN_LIVE_BATCH in state["applied"]:
        return None
    orphans = orphan_live_accounts()
    if not orphans:
        return None
    stamp = _iso(now)
    for name, record in orphans.items():
        state["retired"][name] = {
            "name": name,
            "role": "orphan_live",
            "retired_at": stamp,
            "batch_id": ORPHAN_LIVE_BATCH,
            "reason": ORPHAN_LIVE_REASON,
            "requalify": {},
            "record": record,
        }
    state["batches"].append({
        "batch_id": ORPHAN_LIVE_BATCH,
        "retired_at": stamp,
        "reason": ORPHAN_LIVE_REASON,
        "champions": 0,
        "graduated": 0,
        "orphan_live": len(orphans),
    })
    state["applied"].append(ORPHAN_LIVE_BATCH)
    save_retired(state)
    return {
        "batch_id": ORPHAN_LIVE_BATCH,
        "reason": ORPHAN_LIVE_REASON,
        "retired_orphans": sorted(orphans),
    }


def run_orphan_live_retire() -> dict | None:
    with paper_state_lock("discovery"):
        out = ensure_orphan_live_retire_unlocked(datetime.now(timezone.utc))
    if out is not None:
        log.info(
            "retire: batch %s archived %d orphan live accounts",
            ORPHAN_LIVE_BATCH,
            len(out["retired_orphans"]),
        )
    return out


def start_auto_retire() -> None:
    """Server startup: apply the one-shot retire batches on a daemon thread."""

    def run() -> None:
        try:
            run_auto_retire()
        except Exception:
            log.exception("auto retire failed")
        try:
            run_orphan_live_retire()
        except Exception:
            log.exception("orphan live retire failed")

    threading.Thread(target=run, name="auto-retire", daemon=True).start()


def retired_payload() -> dict:
    """``GET /api/champions/retired``. Read-only; trade history is summarized."""
    state = load_retired()
    rows = []
    for name, row in state["retired"].items():
        if not isinstance(row, dict):
            continue
        record = row.get("record") if isinstance(row.get("record"), dict) else {}
        rows.append({
            "name": name,
            "role": row.get("role"),
            "retired_at": row.get("retired_at"),
            "batch_id": row.get("batch_id"),
            "reason": row.get("reason"),
            "requalify": row.get("requalify") or {},
            "closed": record.get("closed", record.get("closed_trades")),
            "pnl": record.get("pnl", record.get("total_pnl")),
            "champion_since": record.get("champion_since"),
            "graduated_at": record.get("graduated_at"),
            "status": record.get("status"),
            "family_winner": row.get("family_winner"),
        })
    rows.sort(key=lambda r: (str(r.get("retired_at") or ""), r["name"]))
    return {
        "paper_only": True,
        "retired_count": len(rows),
        "applied": state["applied"],
        "batches": state["batches"],
        "retired": rows,
    }
