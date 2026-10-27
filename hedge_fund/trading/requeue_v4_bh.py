"""One-shot requeue of v4 names whose only fail was discovery-window buy-and-hold.

Gate v4 stopped those names before the sealed holdout, DSR, PBO, neighbour
perturbation, and cost stress ran. Gate v5 no longer treats buy-and-hold as
a fail, and the rescore cannot relabel them: the later inputs were never
stored (``missing_inputs``). This module puts exactly that set back on the
discovery claim queue for one fresh v5 evaluation.

Dry-run is the default. Apply freezes the name list. A second apply does
not rescan, does not move the timestamp, and does not add names.

The names stay in the tested-name index, so the DSR trial census still
counts each one once. Claim leases them ahead of the random mint anyway.
Fail-once still blocks every name that is not pending in this batch.
A stored retest result drops that name back under fail-once.

This module does not read the sealed holdout, does not change a gate
threshold, and does not admit or retire a champion.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from hedge_fund.trading.constants import GATE_RULES, V4_GATE_RULES
from hedge_fund.trading.rescore_v5 import classify_row, is_bh_decision_reason

BATCH_ID = "v4-bh-requeue-20261027"
RETEST_REASON = "v4_discovery_bh_only_missing_later_checks"
STATE_NAME = "discovery_v4_bh_requeue.json"

_LATER_BLOCK_KEYS = (
    "dsr",
    "pbo",
    "n_trials",
    "neighbours",
    "neighbours_pass",
    "cost_sharpe",
)
_HOLDOUT_PRESENT_KEYS = ("sharpe", "trades", "pnl", "sma_pnl")


def is_gate_v4_rules(value: object) -> bool:
    """True for the v4 rules tag. v5 and older tags are not this batch."""
    if not isinstance(value, str) or not value:
        return False
    if value == V4_GATE_RULES:
        return True
    return value.startswith("gate_v4_")


def is_discovery_window_bh_reason(reason: object) -> bool:
    """Buy-and-hold comparison on the discovery windows only.

    Holdout and cost-stress sentences are not this. Those checks already
    ran, so the name is a rescore candidate or an ordinary fail, not a
    retest of a name the later checks never saw.
    """
    if not is_bh_decision_reason(reason):
        return False
    text = str(reason).strip().lower()
    if text.startswith("holdout") or text.startswith("cost "):
        return False
    return True


def later_checks_ran(row: dict) -> bool:
    """True when a holdout or a v4 block field was stored.

    A discovery-window buy-and-hold fail under v4 returned before
    ``finish_gate_v4``, so those fields are absent.
    """
    block = row.get("gate_v4")
    if not isinstance(block, dict):
        return False
    holdout = block.get("holdout")
    if isinstance(holdout, dict):
        for key in _HOLDOUT_PRESENT_KEYS:
            if holdout.get(key) is not None:
                return True
    for key in _LATER_BLOCK_KEYS:
        if block.get(key) is not None:
            return True
    return False


def is_requeue_candidate(row: dict) -> bool:
    """True when this stored row is one of the v4 discovery-window B&H misses.

    The row is unqualified, tagged with a v4 rules id, and every fail reason
    is a discovery-window buy-and-hold comparison. The later checks are not
    on the row, so rescore classifies it ``missing_inputs`` and leaves it.
    """
    if not isinstance(row, dict):
        return False
    if row.get("qualified") or row.get("ops_park") or row.get("retest") is True:
        return False
    if not is_gate_v4_rules(row.get("gate_rules")):
        return False
    reasons = row.get("fail_reasons")
    if not isinstance(reasons, list) or not reasons:
        return False
    if not all(is_discovery_window_bh_reason(reason) for reason in reasons):
        return False
    if later_checks_ran(row):
        return False
    return classify_row(row) == "missing_inputs"


def state_path():
    from hedge_fund.paths import state_root

    return state_root() / STATE_NAME


def load_state() -> dict | None:
    path = state_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("batch_id") != BATCH_ID:
        return None
    return data


def save_state(state: dict) -> None:
    """Caller holds ``paper_state_lock('discovery')``."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, path)


def pending_retest_names() -> list[str]:
    """Frozen order. Empty when the batch has not been applied."""
    state = load_state()
    if not state:
        return []
    entries = state.get("entries") if isinstance(state.get("entries"), dict) else {}
    out: list[str] = []
    for name in state.get("order") or []:
        if not isinstance(name, str):
            continue
        entry = entries.get(name)
        if isinstance(entry, dict) and entry.get("status") == "pending":
            out.append(name)
    return out


def retest_entry(name: str) -> dict | None:
    """Pending entry for ``name``, or None when fail-once applies."""
    if not isinstance(name, str) or not name:
        return None
    state = load_state()
    if not state:
        return None
    entries = state.get("entries") if isinstance(state.get("entries"), dict) else {}
    entry = entries.get(name)
    if isinstance(entry, dict) and entry.get("status") == "pending":
        return entry
    return None


def complete_retest_unlocked(name: str, *, tested_at: str | None = None) -> bool:
    """Drop ``name`` back under fail-once. No-op when it was not pending."""
    state = load_state()
    if not state:
        return False
    entries = state.get("entries") if isinstance(state.get("entries"), dict) else {}
    entry = entries.get(name)
    if not isinstance(entry, dict) or entry.get("status") != "pending":
        return False
    entry["status"] = "completed"
    entry["completed_at"] = tested_at or datetime.now(timezone.utc).isoformat()
    state["entries"] = entries
    save_state(state)
    return True


def stamp_retest_result(row: dict, entry: dict) -> None:
    """Mark the fresh v5 result as a retest of the stored v4 fail."""
    row["retest"] = True
    row["retest_reason"] = RETEST_REASON
    row["retest_batch"] = BATCH_ID
    prior_rules = entry.get("prior_gate_rules")
    row["retest_from_rules"] = prior_rules if isinstance(prior_rules, str) else V4_GATE_RULES
    queued = entry.get("requeued_at")
    row["requeued_at"] = queued if isinstance(queued, str) else None
    prior = entry.get("prior_fail_reasons")
    row["prior_fail_reasons"] = [item for item in prior if isinstance(item, str)] if isinstance(prior, list) else []


def _book_names() -> set[str]:
    from hedge_fund.trading.champions import load_graduated, load_pool, retired_names

    names = {
        row.get("name")
        for row in (load_pool().get("champions") or [])
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    names |= {
        row.get("name")
        for row in load_graduated()
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    names |= retired_names()
    return {name for name in names if isinstance(name, str)}


def _stored_by_name() -> dict[str, dict]:
    from hedge_fund.trading.discovery import load_discovery_log
    from hedge_fund.trading.rescore_v5 import _latest

    by_name, _extras = _latest(load_discovery_log())
    return by_name


def _select(by_name: dict[str, dict]) -> list[tuple[str, dict]]:
    blocked = _book_names()
    chosen: list[tuple[str, dict]] = []
    for name in sorted(by_name):
        if name in blocked:
            continue
        row = by_name[name]
        if is_requeue_candidate(row):
            chosen.append((name, row))
    return chosen


def _entry_for(row: dict, stamp: str) -> dict:
    reasons = row.get("fail_reasons") if isinstance(row.get("fail_reasons"), list) else []
    rules = row.get("gate_rules")
    return {
        "status": "pending",
        "requeued_at": stamp,
        "prior_gate_rules": rules if isinstance(rules, str) else V4_GATE_RULES,
        "prior_fail_reasons": [item for item in reasons if isinstance(item, str)],
        "completed_at": None,
    }


def _counts(state: dict | None) -> tuple[list[str], int, int]:
    if not state:
        return [], 0, 0
    order = [name for name in (state.get("order") or []) if isinstance(name, str)]
    entries = state.get("entries") if isinstance(state.get("entries"), dict) else {}
    pending = 0
    completed = 0
    for name in order:
        entry = entries.get(name)
        if not isinstance(entry, dict):
            continue
        if entry.get("status") == "completed":
            completed += 1
        elif entry.get("status") == "pending":
            pending += 1
    return order, pending, completed


def _report(
    names: list[str],
    *,
    dry_run: bool,
    wrote: bool,
    already: bool,
    stamp: str | None,
    pending: int,
    completed: int,
) -> dict:
    return {
        "ok": True,
        "paper_only": True,
        "dry_run": dry_run,
        "applied": wrote,
        "already_applied": already,
        "batch_id": BATCH_ID,
        "reason": RETEST_REASON,
        "gate_from": V4_GATE_RULES,
        "gate_to": GATE_RULES,
        "count": len(names),
        "names": list(names),
        "pending": pending,
        "completed": completed,
        "admitted": [],
        "retired": [],
        "holdout_unsealed": False,
        "rescored_at": None,
        "applied_at": stamp,
        "note": (
            "One shot. Names whose stored fail is only a discovery-window "
            "buy-and-hold comparison under a gate_v4 tag, and whose holdout, "
            "DSR, PBO, neighbour, and cost-stress inputs were never stored. "
            "Dry-run does not write. Apply puts that frozen list ahead of "
            "the random mint. Each name already counts once in the DSR trial "
            "census; the retest does not add a trial. Fail-once still blocks "
            "every other name. The holdout stays sealed. Nothing is admitted "
            "or retired."
        ),
    }


def _report_state(state: dict, *, dry_run: bool, wrote: bool, already: bool) -> dict:
    names, pending, completed = _counts(state)
    return _report(
        names,
        dry_run=dry_run,
        wrote=wrote,
        already=already,
        stamp=state.get("applied_at") if isinstance(state.get("applied_at"), str) else None,
        pending=pending,
        completed=completed,
    )


def _ensure_counted(by_name: dict[str, dict], names: list[str]) -> None:
    """Keep each queued name as one census row. Does not add a second trial.

    A name that already has a finite Sharpe stays one entry. A name whose
    row has a Sharpe but whose metrics map missed it is recorded once now,
    so the later retest updates that entry instead of incrementing N.
    """
    from hedge_fund.trading.tested_index import (
        load_index_and_metrics,
        merge_tested_metrics,
        merge_tested_rows,
        save_tested_index,
    )

    rows = [by_name[name] for name in names if name in by_name]
    if not rows:
        return
    index, metrics = load_index_and_metrics()
    flags_changed = merge_tested_rows(index, rows)
    metrics_changed = merge_tested_metrics(metrics, rows)
    if flags_changed or metrics_changed:
        save_tested_index(index, metrics)


def requeue_v4_bh(*, apply: bool = False, now: str | None = None) -> dict:
    """List the v4 discovery-window B&H misses. Write the queue only when asked.

    A batch that is already on disk is returned as-is, including its
    timestamp. Dry-run after apply reports that frozen list and does not
    write.
    """
    existing = load_state()
    if existing and existing.get("applied_at"):
        return _report_state(existing, dry_run=not apply, wrote=False, already=True)

    by_name = _stored_by_name()
    selected = _select(by_name)
    names = [name for name, _row in selected]
    if not apply:
        return _report(
            names,
            dry_run=True,
            wrote=False,
            already=False,
            stamp=None,
            pending=0,
            completed=0,
        )

    stamp = now or datetime.now(timezone.utc).isoformat()
    from hedge_fund.trading.store import paper_state_lock

    with paper_state_lock("discovery"):
        # Another apply may have landed while the scan ran.
        existing = load_state()
        if existing and existing.get("applied_at"):
            return _report_state(existing, dry_run=False, wrote=False, already=True)
        by_name = _stored_by_name()
        selected = _select(by_name)
        names = [name for name, _row in selected]
        state = {
            "batch_id": BATCH_ID,
            "paper_only": True,
            "reason": RETEST_REASON,
            "gate_from": V4_GATE_RULES,
            "gate_to": GATE_RULES,
            "applied_at": stamp,
            "order": names,
            "entries": {name: _entry_for(row, stamp) for name, row in selected},
            "holdout_unsealed": False,
            "note": (
                "Fail-once is lifted only for pending names in this list, "
                "and only until one v5 result is stored. The trial census "
                "is not incremented for the retest."
            ),
        }
        _ensure_counted(by_name, names)
        save_state(state)
    return _report_state(state, dry_run=False, wrote=True, already=False)
