"""One-shot requeue of canonical library names still stamped with an older gate.

Prod scored the 15 ``lib.*`` names under gate v6. Claims only hand out
names that are not in the tested-name index, so the v7 and v7b tags never
saw them. This module puts back on the library claim queue every canonical
catalog name whose newest discovery-log row is not ``GATE_RULES``
(``gate_v7b_library_profit_20261029``).

A name with no discovery-log row is left alone. It is untested, so a
library claim already leases it. A 5m name is never selected. A neighbour
variant (``lib.sma100.90``) is not a claim name and is not selected.
A name whose newest row is already the current tag is not selected, so a
wiped state file does not queue it again.

Seated names are included. ``lib.sma100`` is already on the book, and
``lib.rsi2`` / ``lib.sma200`` are seated by the approved-admit batch.
The retest writes one new discovery-log row under the current tag. It
does not rewrite the champion row, does not open or close the paper
account, and does not retire or un-retire anyone. A pass is proposed.
Seating stays on ``LIBRARY_APPROVED_ADMITS`` / ``FORWARD_TEST_ADMITS``.

Dry-run is the default. Apply freezes the name list. A second apply does
not rescan and does not move the timestamp. One stored result, pass or
fail, drops that name back under fail-once.

Random mode does not lease this queue. Library mode leases pending names
ahead of never-tested catalog names, including names that are already
tested or already on the book.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from hedge_fund.trading.constants import GATE_RULES

BATCH_ID = "library-gate-v7b-requeue-20261029"
RETEST_REASON = "library_gate_tag_older_than_current"
STATE_NAME = "discovery_library_gate_requeue.json"


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


def pending_library_retest_names() -> list[str]:
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


def library_retest_entry(name: str) -> dict | None:
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


def complete_library_retest_unlocked(name: str, *, tested_at: str | None = None) -> bool:
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


def stamp_library_retest_result(row: dict, entry: dict) -> None:
    """Mark the fresh row as a library-gate retest. Does not change qualification."""
    row["retest"] = True
    row["retest_reason"] = RETEST_REASON
    row["retest_batch"] = BATCH_ID
    prior_rules = entry.get("prior_gate_rules")
    row["retest_from_rules"] = prior_rules if isinstance(prior_rules, str) else None
    queued = entry.get("requeued_at")
    row["requeued_at"] = queued if isinstance(queued, str) else None
    prior = entry.get("prior_fail_reasons")
    row["prior_fail_reasons"] = [item for item in prior if isinstance(item, str)] if isinstance(prior, list) else []
    row["retest_keeps_seat"] = entry.get("seated") is True


def _latest_by_name() -> dict[str, dict]:
    from hedge_fund.trading.discovery import latest_eval_per_strategy, load_discovery_log

    out: dict[str, dict] = {}
    for row in latest_eval_per_strategy(load_discovery_log()):
        name = row.get("strategy")
        if isinstance(name, str) and isinstance(row, dict):
            out[name] = row
    return out


def _seat_names() -> set[str]:
    """Active and graduated champions. Retired names are not seats."""
    from hedge_fund.trading.champions import load_graduated, load_pool

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
    return {name for name in names if isinstance(name, str)}


def _older_than_current(rules: object) -> bool:
    return not (isinstance(rules, str) and rules == GATE_RULES)


def _select(latest: dict[str, dict], seats: set[str]) -> list[tuple[str, dict, bool]]:
    from hedge_fund.library.catalog import claim_names

    chosen: list[tuple[str, dict, bool]] = []
    for name in claim_names():
        row = latest.get(name)
        if not isinstance(row, dict):
            continue
        if not _older_than_current(row.get("gate_rules")):
            continue
        chosen.append((name, row, name in seats))
    return chosen


def _entry_for(row: dict, stamp: str, *, seated: bool) -> dict:
    reasons = row.get("fail_reasons") if isinstance(row.get("fail_reasons"), list) else []
    rules = row.get("gate_rules")
    return {
        "status": "pending",
        "requeued_at": stamp,
        "prior_gate_rules": rules if isinstance(rules, str) else None,
        "prior_fail_reasons": [item for item in reasons if isinstance(item, str)],
        "seated": seated,
        "completed_at": None,
    }


def _by_rules(pairs: list[tuple[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _name, rules in pairs:
        key = rules if isinstance(rules, str) and rules else "missing"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _counts(state: dict | None) -> tuple[list[str], list[str], int, int, dict[str, int]]:
    if not state:
        return [], [], 0, 0, {}
    order = [name for name in (state.get("order") or []) if isinstance(name, str)]
    entries = state.get("entries") if isinstance(state.get("entries"), dict) else {}
    pending = 0
    completed = 0
    seated: list[str] = []
    tagged: list[tuple[str, object]] = []
    for name in order:
        entry = entries.get(name)
        if not isinstance(entry, dict):
            continue
        tagged.append((name, entry.get("prior_gate_rules")))
        if entry.get("seated") is True:
            seated.append(name)
        if entry.get("status") == "completed":
            completed += 1
        elif entry.get("status") == "pending":
            pending += 1
    return order, seated, pending, completed, _by_rules(tagged)


def _report(
    names: list[str],
    *,
    seated: list[str],
    dry_run: bool,
    wrote: bool,
    already: bool,
    stamp: str | None,
    pending: int,
    completed: int,
    by_rules: dict[str, int],
) -> dict:
    return {
        "ok": True,
        "paper_only": True,
        "dry_run": dry_run,
        "applied": wrote,
        "already_applied": already,
        "batch_id": BATCH_ID,
        "reason": RETEST_REASON,
        "gate_to": GATE_RULES,
        "count": len(names),
        "by_rules": dict(by_rules),
        "names": list(names),
        "seated": list(seated),
        "pending": pending,
        "completed": completed,
        "admitted": [],
        "retired": [],
        "seats_unchanged": True,
        "applied_at": stamp,
        "note": (
            "One shot. Canonical lib.* names whose newest discovery-log "
            "row is not gate_v7b_library_profit_20261029. A seated name "
            "(lib.sma100, and lib.rsi2 / lib.sma200 once admitted) is "
            "queued too. The retest appends one discovery-log row and "
            "does not change the champion row, the paper account, or "
            "retirement. A pass is proposed, not seated. 5m names are "
            "not in this list. Dry-run does not write. A second apply "
            "does not rescan."
        ),
    }


def _report_state(state: dict, *, dry_run: bool, wrote: bool, already: bool) -> dict:
    names, seated, pending, completed, by_rules = _counts(state)
    return _report(
        names,
        seated=seated,
        dry_run=dry_run,
        wrote=wrote,
        already=already,
        stamp=state.get("applied_at") if isinstance(state.get("applied_at"), str) else None,
        pending=pending,
        completed=completed,
        by_rules=by_rules,
    )


def requeue_library_gate(*, apply: bool = False, now: str | None = None) -> dict:
    """List canonical library names on an older gate tag. Write only when asked.

    A batch that is already on disk is returned as-is, including its
    timestamp. Dry-run after apply reports that frozen list and does not
    write.
    """
    existing = load_state()
    if existing and existing.get("applied_at"):
        return _report_state(existing, dry_run=not apply, wrote=False, already=True)

    latest = _latest_by_name()
    seats = _seat_names()
    selected = _select(latest, seats)
    names = [name for name, _row, _seated in selected]
    seated = [name for name, _row, is_seated in selected if is_seated]
    by_rules = _by_rules([(name, row.get("gate_rules")) for name, row, _seated in selected])
    if not apply:
        return _report(
            names,
            seated=seated,
            dry_run=True,
            wrote=False,
            already=False,
            stamp=None,
            pending=0,
            completed=0,
            by_rules=by_rules,
        )

    stamp = now or datetime.now(timezone.utc).isoformat()
    from hedge_fund.trading.store import paper_state_lock

    with paper_state_lock("discovery"):
        existing = load_state()
        if existing and existing.get("applied_at"):
            return _report_state(existing, dry_run=False, wrote=False, already=True)
        latest = _latest_by_name()
        seats = _seat_names()
        selected = _select(latest, seats)
        names = [name for name, _row, _seated in selected]
        state = {
            "batch_id": BATCH_ID,
            "paper_only": True,
            "reason": RETEST_REASON,
            "gate_to": GATE_RULES,
            "applied_at": stamp,
            "order": names,
            "entries": {
                name: _entry_for(row, stamp, seated=is_seated)
                for name, row, is_seated in selected
            },
            "seats_unchanged": True,
            "note": (
                "Fail-once is lifted only while a name in this list is "
                "pending, and only for a library-mode claim. One stored "
                "result puts it back. The champion row and the paper "
                "account are not written by this batch."
            ),
        }
        save_state(state)
    return _report_state(state, dry_run=False, wrote=True, already=False)
