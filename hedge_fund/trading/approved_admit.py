"""One-shot admits a human approved from a requalify pass. Paper only.

The requalify lane records verdicts and never admits. When Alexander
approves a requalify pass by name, it is listed in ``APPROVED_ADMITS``
and seated once on server start from the stored verdict. The OOS gate is
not re-run or changed. The marker in ``approved_admits.json`` makes the
batch idempotent across restarts. Only the listed name is touched: no
cull, no retire, no change to the requalify state, the discovery log or
the tested index.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.paths import state_root
from hedge_fund.trading.champions import load_graduated, load_pool, retired_names, save_pool
from hedge_fund.trading.constants import GATE_RULES, QUAL_TIMEFRAME, RISK_POLICY
from hedge_fund.trading.requalify import load_requalify_state
from hedge_fund.trading.store import paper_state_lock

log = logging.getLogger(__name__)

APPROVED_ADMITS_FILE = "approved_admits.json"
APPROVED_ADMIT_SOURCE = "requalify_approved_admit"
# Alexander, 2026-10-09 07:41 Stockholm: admit the single rules-v3 pass.
APPROVED_ADMIT_BATCH = "rules-v3-admit-20261009"
APPROVED_ADMITS: tuple[dict, ...] = (
    {
        "name": "dip_204b_lt8pc&h4_ema_abv_150",
        "requalify_batch": "rules-v3-tiled-20261008",
        "approved_by": "Alexander",
        "approved_at": "2026-10-09T07:41:00+02:00",
        "note": "rules-v3 tiled87 requalify pass; admitted on Alexander's OK",
    },
)
_PROVENANCE_KEYS = (
    "gate_rules",
    "trades",
    "sharpe",
    "daily_sharpe",
    "bh_daily_sharpe",
    "test_pnl",
    "bh_oos_pnl",
    "avg_hold_hours",
    "regimes_tested",
    "tested_at",
    "worker_id",
    "data_end",
)


def approved_admits_path() -> Path:
    return state_root() / APPROVED_ADMITS_FILE


def _load_marker() -> dict:
    path = approved_admits_path()
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    applied = data.get("applied") if isinstance(data.get("applied"), list) else []
    batches = data.get("batches") if isinstance(data.get("batches"), list) else []
    return {"applied": [a for a in applied if isinstance(a, str)], "batches": batches}


def _save_marker(marker: dict) -> None:
    path = approved_admits_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({**marker, "paper_only": True}, indent=2))
    os.replace(tmp, path)


def _requalify_pass(meta: object, batch_id: str) -> dict | None:
    """The stored verdict for ``batch_id`` if it is a pass on the current tag."""
    if not isinstance(meta, dict):
        return None
    candidates = [meta]
    if isinstance(meta.get("previous"), dict):
        candidates.append(meta["previous"])
    for entry in candidates:
        if entry.get("batch_id") != batch_id:
            continue
        if entry is meta and meta.get("status") != "done":
            continue
        result = entry.get("result")
        if not isinstance(result, dict) or not result.get("qualified"):
            continue
        if result.get("gate_rules") != GATE_RULES:
            continue
        return result
    return None


def ensure_approved_admits_unlocked(now: datetime) -> dict | None:
    """Apply ``APPROVED_ADMIT_BATCH`` once. Caller holds the discovery lock.

    Returns None (and leaves no marker) if any listed name lacks a stored
    current-tag pass, so a later start can retry once the verdict exists.
    """
    marker = _load_marker()
    if APPROVED_ADMIT_BATCH in marker["applied"]:
        return None
    names = load_requalify_state()["names"]
    verdicts: dict[str, dict] = {}
    for spec in APPROVED_ADMITS:
        result = _requalify_pass(names.get(spec["name"]), spec["requalify_batch"])
        if result is None:
            return None
        verdicts[spec["name"]] = result

    stamp = now.astimezone(timezone.utc).isoformat()
    st = load_pool()
    pooled = {c.get("name") for c in (st.get("champions") or []) if isinstance(c, dict)}
    graduated = {g.get("name") for g in load_graduated() if isinstance(g, dict)}
    retired = retired_names()
    admitted: list[str] = []
    skipped: list[dict] = []
    for spec in APPROVED_ADMITS:
        name = spec["name"]
        if name in retired:
            skipped.append({"strategy": name, "reason": "retired"})
            continue
        if name in pooled or name in graduated:
            skipped.append({"strategy": name, "reason": "already_pooled_or_graduated"})
            continue
        result = verdicts[name]
        provenance = {
            "admit_batch": APPROVED_ADMIT_BATCH,
            "requalify_batch": spec["requalify_batch"],
            "approved_by": spec["approved_by"],
            "approved_at": spec["approved_at"],
            "note": spec["note"],
        }
        provenance.update({k: result.get(k) for k in _PROVENANCE_KEYS if k in result})
        st.setdefault("champions", []).append({
            "name": name,
            "closed": 0,
            "pnl": 0.0,
            "wins": 0,
            "sharpe_qual": result.get("sharpe"),
            "winrate_qual": result.get("win_rate_pct"),
            "admitted_at": stamp,
            "champion_since": stamp,
            "source": APPROVED_ADMIT_SOURCE,
            "timeframe": QUAL_TIMEFRAME,
            "risk_policy": RISK_POLICY,
            "provenance": provenance,
        })
        pooled.add(name)
        admitted.append(name)
    if admitted:
        save_pool(st)
    marker["applied"].append(APPROVED_ADMIT_BATCH)
    marker["batches"].append({
        "batch_id": APPROVED_ADMIT_BATCH,
        "applied_at": stamp,
        "admitted": admitted,
        "skipped": skipped,
    })
    _save_marker(marker)
    return {"batch_id": APPROVED_ADMIT_BATCH, "admitted": admitted, "skipped": skipped}


def run_approved_admits() -> dict | None:
    with paper_state_lock("discovery"):
        out = ensure_approved_admits_unlocked(datetime.now(timezone.utc))
    if out is not None:
        log.info("approved admit: batch %s admitted %s", APPROVED_ADMIT_BATCH, out["admitted"])
    return out


def start_approved_admits() -> None:
    """Server startup: apply the one-shot approved admits on a daemon thread."""

    def run() -> None:
        try:
            run_approved_admits()
        except Exception:
            log.exception("approved admit failed")

    threading.Thread(target=run, name="approved-admit", daemon=True).start()
