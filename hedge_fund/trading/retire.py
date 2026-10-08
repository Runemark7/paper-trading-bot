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
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
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


def start_auto_retire() -> None:
    """Server startup: apply ``RETIRE_AUTO_BATCH`` once on a daemon thread."""

    def run() -> None:
        try:
            run_auto_retire()
        except Exception:
            log.exception("auto retire failed")

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
        })
    rows.sort(key=lambda r: (str(r.get("retired_at") or ""), r["name"]))
    return {
        "paper_only": True,
        "retired_count": len(rows),
        "applied": state["applied"],
        "batches": state["batches"],
        "retired": rows,
    }
