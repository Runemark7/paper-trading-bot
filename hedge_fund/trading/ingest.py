"""Merge Windows-worker discovery evaluations into prod paper state.

Fail-once: a name that already has any discovery_log row is skipped.
Qualified names are admitted the same way as ``replenish_and_evaluate``.
Existing champions are never removed. Paper only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from hedge_fund.trading.champions import load_graduated, load_pool, save_pool
from hedge_fund.trading.constants import QUAL_TIMEFRAME, RISK_POLICY
from hedge_fund.trading.discovery import (
    append_discovery_evaluation,
    clear_in_flight,
    load_discovery_log,
    tested_discovery_names,
    write_in_flight,
)
from hedge_fund.trading.farm import apply_heartbeat_unlocked
from hedge_fund.trading.refill import append_extended_batch, load_extended_names
from hedge_fund.trading.store import paper_state_lock

_EVAL_REQUIRED = ("strategy", "qualified", "tested_at")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_eval(row: Any) -> dict | None:
    if not isinstance(row, dict):
        return None
    name = row.get("strategy")
    if not name or not isinstance(name, str):
        return None
    for key in _EVAL_REQUIRED:
        if key not in row:
            return None
    tf = row.get("timeframe") or QUAL_TIMEFRAME
    if tf != QUAL_TIMEFRAME:
        return None
    policy = row.get("risk_policy") or RISK_POLICY
    if policy != RISK_POLICY:
        return None
    reasons = row.get("fail_reasons")
    if reasons is not None and not isinstance(reasons, list):
        return None
    out = dict(row)
    out["strategy"] = name
    out["qualified"] = bool(row.get("qualified"))
    out["timeframe"] = QUAL_TIMEFRAME
    out["risk_policy"] = RISK_POLICY
    if reasons is None:
        out["fail_reasons"] = []
    return out


def _admit_qualified(st: dict, record: dict, existing: set[str]) -> bool:
    name = record["strategy"]
    if name in existing:
        return False
    admitted_at = record.get("tested_at") or _now()
    st.setdefault("champions", []).append({
        "name": name,
        "closed": 0,
        "pnl": 0.0,
        "wins": 0,
        "sharpe_qual": record.get("sharpe"),
        "winrate_qual": record.get("win_rate_pct"),
        "admitted_at": admitted_at,
        "champion_since": admitted_at,
        "source": "5m_qualification_filter",
        "timeframe": QUAL_TIMEFRAME,
        "risk_policy": RISK_POLICY,
    })
    existing.add(name)
    return True


def ingest_discovery_payload(payload: dict) -> dict:
    """Apply a worker batch. Caller must already have checked the ingest token.

    Returns counts. Never culls champions. Never rewrites an existing log row.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    raw_evals = payload.get("evaluations") or []
    if not isinstance(raw_evals, list):
        raise ValueError("evaluations must be a list")
    raw_extended = payload.get("extended_names") or []
    if raw_extended and not isinstance(raw_extended, list):
        raise ValueError("extended_names must be a list")

    ingested: list[str] = []
    skipped: list[dict] = []
    admitted: list[str] = []
    rejected_invalid: list[str] = []

    with paper_state_lock("discovery"):
        log = load_discovery_log()
        already = tested_discovery_names(log)
        st = load_pool()
        grads = load_graduated()
        existing = {c["name"] for c in (st.get("champions") or []) if c.get("name")}
        existing |= {g["name"] for g in grads if isinstance(g, dict) and g.get("name")}

        for raw in raw_evals:
            rec = _validate_eval(raw)
            if rec is None:
                rejected_invalid.append(
                    str((raw or {}).get("strategy") if isinstance(raw, dict) else "?")
                )
                continue
            name = rec["strategy"]
            if name in existing:
                skipped.append({"strategy": name, "reason": "already_pooled_or_graduated"})
                continue
            if name in already:
                skipped.append({"strategy": name, "reason": "already_tested"})
                continue
            append_discovery_evaluation(rec)
            already.add(name)
            ingested.append(name)
            if rec["qualified"] and _admit_qualified(st, rec, existing):
                admitted.append(name)

        if admitted:
            save_pool(st)

        added_extended: list[str] = []
        if raw_extended:
            names = [n for n in raw_extended if isinstance(n, str) and n]
            if names:
                before = set(load_extended_names())
                append_extended_batch(names)
                added_extended = [n for n in names if n not in before]

        hb = payload.get("heartbeat")
        if isinstance(hb, dict):
            status = hb.get("status")
            apply_heartbeat_unlocked(str(status) if status else None)
        elif hb:
            apply_heartbeat_unlocked(str(hb))

        flight = payload.get("in_flight")
        if payload.get("clear_in_flight"):
            clear_in_flight()
        elif isinstance(flight, dict):
            names = [n for n in (flight.get("names") or []) if isinstance(n, str)]
            remaining = flight.get("remaining")
            completed = flight.get("completed")
            write_in_flight(
                names,
                current=flight.get("current") if isinstance(flight.get("current"), str) else None,
                remaining=[n for n in remaining if isinstance(n, str)] if isinstance(remaining, list) else None,
                completed=[n for n in completed if isinstance(n, str)] if isinstance(completed, list) else None,
                batch_size=flight.get("batch_size"),
                started_at=flight.get("started_at") if isinstance(flight.get("started_at"), str) else None,
                source="windows_worker",
            )

    return {
        "ok": True,
        "paper_only": True,
        "ingested": ingested,
        "skipped": skipped,
        "admitted": admitted,
        "rejected_invalid": rejected_invalid,
        "extended_added": added_extended,
        "source": payload.get("source") or "windows_worker",
    }
