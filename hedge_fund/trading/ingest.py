"""Merge Windows-worker discovery evaluations into prod paper state.

Fail-once: a name that already has any discovery_log row is skipped
(no second walk-forward), except one pending v4 buy-and-hold retest
(``requeue_v4_bh``), one pending library-gate retest
(``requeue_library_gate``), and one corrected-signal retest
(``requeue_library_signal``) for ``lib.td_cd``, ``lib.pierce``, and
``lib.soldiers`` until the newest row carries ``signal_revision``.
A library retest of a name that is already
seated appends the discovery-log row and does not rewrite the champion
or the paper account. The corrected-signal retest also leaves the old
row in place. A 5m qualified pass is logged and proposed. It is not seated. A
canonical ``lib.*`` pass under gate v7b is seated on this ingest
(``seat_library_passes_unlocked``), including a corrected-signal pass
that this ingest actually wrote. That seat is idempotent: a name
already in the pool or graduated is not written again, and a retired
name comes back only when this ingest is a new passing evaluation.
On ingest, parked rows that now pass on stored aggregates
(Sharpe / trades / sma) are flipped the same way and stay proposals
unless they are a library pass, which is seated by the same rule.
Buy-and-hold is not a fail reason. ``force_admit`` (token gated) and
``promote_candidates`` remain the explicit seats for a 5m row. Neither
refuses because of how many champions are active. Champions are only removed by the
one-per-family rule (``families.py``): a twin is parked to
``retired.json``. Paper only. The random farm is unchanged.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from hedge_fund.trading.champions import load_graduated, load_pool, save_pool
from hedge_fund.trading.constants import GATE_RULES, QUAL_TIMEFRAME, RISK_POLICY
from hedge_fund.trading.discovery import (
    append_discovery_evaluation,
    clear_in_flight,
    load_discovery_log,
    save_discovery_log,
    tested_discovery_names,
    write_in_flight,
)
from hedge_fund.trading.families import (
    admit_blocked_names,
    enforce_champion_families_unlocked,
    qual_metrics,
    unpark_unlocked,
)
from hedge_fund.trading.farm import apply_heartbeat_unlocked
from hedge_fund.trading.leases import clear_leases_unlocked, touch_worker_unlocked
from hedge_fund.trading.qualify import requalify_parked_log
from hedge_fund.trading.requeue_library_gate import (
    complete_library_retest_unlocked,
    library_retest_entry,
    stamp_library_retest_result,
)
from hedge_fund.trading.requeue_library_signal import (
    REVISION as SIGNAL_REVISION,
    is_signal_correction,
    signal_correction_pending,
    stamp_signal_correction,
)
from hedge_fund.trading.requeue_v4_bh import (
    complete_retest_unlocked,
    retest_entry,
    stamp_retest_result,
)
from hedge_fund.trading.refill import append_extended_batch, load_extended_names
from hedge_fund.trading.requalify import (
    load_requalify_state,
    record_requalify_unlocked,
    requalify_path,
    save_requalify_state,
)
from hedge_fund.paths import state_root
from hedge_fund.trading.store import paper_state_lock

# One parked-row scan per paper-state dir. A later fail-only ingest does
# not walk the display log again. A qualified row or force_admit still scans.
_park_scan_dir: str | None = None

_EVAL_REQUIRED = ("strategy", "qualified", "tested_at")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_library_eval(row: dict, name: str) -> dict | None:
    """A library row: canonical name, signal_exit engine, this gate, this library."""
    from hedge_fund.library.catalog import claim_names, library_trial_count
    from hedge_fund.library.engine import ENGINE_ID, RISK_POLICY as LIB_RISK_POLICY
    from hedge_fund.library.engine import TIMEFRAME as LIB_TIMEFRAME

    if name not in claim_names():
        return None
    if row.get("timeframe") != LIB_TIMEFRAME or row.get("risk_policy") != LIB_RISK_POLICY:
        return None
    if row.get("engine") != ENGINE_ID or row.get("gate_rules") != GATE_RULES:
        return None
    block = row.get("gate_v4")
    # A worker with a different library computed DSR with another N.
    if not isinstance(block, dict) or block.get("n_trials") != library_trial_count():
        return None
    if bool(row.get("qualified")):
        from hedge_fund.library.evaluate import library_attestation_ok

        # Vetoes only. A DSR or PBO miss is a warning and still proposes.
        if not library_attestation_ok(row):
            return None
    reasons = row.get("fail_reasons")
    warnings = row.get("warnings")
    if reasons is not None and not isinstance(reasons, list):
        return None
    if warnings is not None and not isinstance(warnings, list):
        return None
    out = dict(row)
    out["qualified"] = bool(row.get("qualified"))
    if reasons is None:
        out["fail_reasons"] = []
    if warnings is None:
        out["warnings"] = []
    return out


def _validate_eval(row: Any) -> dict | None:
    if not isinstance(row, dict):
        return None
    name = row.get("strategy")
    if not name or not isinstance(name, str):
        return None
    for key in _EVAL_REQUIRED:
        if key not in row:
            return None
    from hedge_fund.library.catalog import is_library_name

    if is_library_name(name):
        return _validate_library_eval(row, name)
    tf = row.get("timeframe") or QUAL_TIMEFRAME
    if tf != QUAL_TIMEFRAME:
        return None
    policy = row.get("risk_policy") or RISK_POLICY
    if policy != RISK_POLICY:
        return None
    # A worker on an older checkout still exits on signal flips and gates on
    # raw P&L vs B&H. Reject its rows: no log row, no admit, lease stays so
    # the name is re-evaluated once the worker pulls the new rules.
    if row.get("gate_rules") != GATE_RULES:
        return None
    # A qualified row without the v4 block was not measured under this gate.
    # Reject it (no log row) so the lease can be retried by a current worker.
    if bool(row.get("qualified")):
        from hedge_fund.trading.gate_v4 import v4_attestation_ok

        if not v4_attestation_ok(row):
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
    row = {
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
    }
    library = record.get("library")
    if isinstance(library, dict):
        # The live cycle routes this name to the signal_exit engine.
        row.update({
            "source": "library_qualification",
            "timeframe": record.get("timeframe"),
            "risk_policy": record.get("risk_policy"),
            "engine": record.get("engine"),
            "library": {
                key: library.get(key)
                for key in ("key", "title", "params", "citation", "safety_stop_pct")
            },
        })
    qual = qual_metrics(record)
    if qual:
        # Ranking fields for the one-per-family rule (families.py).
        row["qual"] = qual
    from hedge_fund.trading.forward_test import registration_for

    row["forward_test"] = registration_for(record, admitted_at)
    gate = record.get("gate_v4")
    if isinstance(gate, dict):
        # Compact summary. Holdout fields stay off sharpe / test_pnl.
        row["gate_v4"] = {
            key: gate.get(key)
            for key in (
                "passed",
                "rules",
                "dsr",
                "pbo",
                "n_trials",
                "neighbours",
                "neighbours_pass",
                "cross_asset_sharpe",
                "cross_asset_pass",
                "cross_asset_note",
                "backtest_max_dd",
                "cost_sharpe",
                "cost_daily_sharpe",
            )
        }
    st.setdefault("champions", []).append(row)
    existing.add(name)
    return True


def _latest_log_row(log: list[dict], name: str) -> dict | None:
    """Newest-first log: first matching strategy row."""
    for row in log:
        if isinstance(row, dict) and row.get("strategy") == name:
            return row
    return None


def _apply_force_admit(
    names: list[Any],
    *,
    source: str,
    st: dict,
    existing: set[str],
    log: list[dict],
    skipped: list[dict],
) -> list[str]:
    """Seat named parked rows. Does not change OOS gates. Never culls."""
    admitted: list[str] = []
    stamp = _now()
    for raw in names:
        if not isinstance(raw, str) or not raw.strip():
            skipped.append({"strategy": str(raw), "reason": "invalid_name"})
            continue
        name = raw.strip()
        if name in existing:
            skipped.append({"strategy": name, "reason": "already_pooled_or_graduated"})
            continue
        row = _latest_log_row(log, name)
        if row is None:
            skipped.append({"strategy": name, "reason": "not_in_discovery_log"})
            continue
        row["qualified"] = True
        row["force_admitted_at"] = stamp
        row["force_admit_source"] = source
        if _admit_qualified(st, row, existing):
            admitted.append(name)
            complete_retest_unlocked(name, tested_at=stamp)
    return admitted


def _park_scan_due(*, force: bool) -> bool:
    """True when this ingest should walk the display log for parked flips."""
    if force:
        return True
    return _park_scan_dir != str(state_root())


def _mark_park_scanned() -> None:
    global _park_scan_dir
    _park_scan_dir = str(state_root())


def _touch_heartbeat_and_flight(payload: dict) -> None:
    """Lease heartbeat and in-flight file. Caller holds the discovery lock."""
    hb = payload.get("heartbeat")
    status = None
    worker_id = None
    evaluating = None
    queued = None
    if isinstance(hb, dict):
        status = hb.get("status")
        raw_id = hb.get("worker_id")
        if isinstance(raw_id, str):
            worker_id = raw_id
        raw_evaluating = hb.get("evaluating")
        if isinstance(raw_evaluating, list):
            evaluating = raw_evaluating
        raw_queued = hb.get("queued")
        if isinstance(raw_queued, list):
            queued = raw_queued
    elif hb:
        status = str(hb)
    if not worker_id and isinstance(payload.get("worker_id"), str):
        worker_id = payload.get("worker_id")
    if isinstance(hb, dict) or hb or worker_id:
        apply_heartbeat_unlocked(str(status) if status else None)
    if worker_id:
        touch_worker_unlocked(
            worker_id,
            str(status) if status else None,
            evaluating=evaluating,
            queued=queued,
        )

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


def _timing(started: float, phases: dict) -> dict:
    phases["total"] = round((time.perf_counter() - started) * 1000, 1)
    from hedge_fund.trading.handler_timing import current_frame

    frame = current_frame()
    if frame is not None:
        snap = frame.snapshot()
        phases["lock_wait"] = snap["lock_wait"]
        phases["io"] = snap["io"]
        phases["work"] = snap["work"]
        phases["lock_hold"] = snap["lock_hold"]
    return phases


def ingest_discovery_payload(payload: dict) -> dict:
    """Apply a worker batch. Caller must already have checked the ingest token.

    Returns counts. A 5m qualified pass is listed under ``proposed`` and
    is not seated. A canonical ``lib.*`` pass under the current gate is
    seated (``auto_admitted``) and also listed under ``proposed``. A
    second ingest of that name does not add another champion. A retired
    library name is seated only when this payload is a new passing
    evaluation. ``force_admit`` remains the explicit seat for a row this
    path did not seat, and it does not refuse because of how many
    champions are active. Parks family twins of names that were seated
    (families.py); never deletes. May flip a parked 5m log row to
    qualified when stored aggregates now pass (all-windows veto dropped).
    That flip stays a proposal.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    raw_evals = payload.get("evaluations") or []
    if not isinstance(raw_evals, list):
        raise ValueError("evaluations must be a list")
    raw_extended = payload.get("extended_names") or []
    if raw_extended and not isinstance(raw_extended, list):
        raise ValueError("extended_names must be a list")
    raw_force = payload.get("force_admit")
    if raw_force is not None and not isinstance(raw_force, list):
        raise ValueError("force_admit must be a list")

    started = time.perf_counter()
    phases: dict[str, float] = {}

    def mark(label: str, t: float) -> None:
        phases[label] = round((time.perf_counter() - t) * 1000, 1)

    ingested: list[str] = []
    skipped: list[dict] = []
    admitted: list[str] = []
    proposed: list[str] = []
    rejected_invalid: list[str] = []
    requalified: list[str] = []
    force_admitted: list[str] = []
    added_extended: list[str] = []
    requalify_recorded: list[str] = []

    qualified_in = any(isinstance(row, dict) and row.get("qualified") is True for row in raw_evals)
    heartbeat_only = not raw_evals and not raw_extended and not raw_force
    if heartbeat_only and not _park_scan_due(force=False):
        t_hb = time.perf_counter()
        with paper_state_lock("discovery"):
            _touch_heartbeat_and_flight(payload)
        mark("heartbeat", t_hb)
        return {
            "ok": True,
            "paper_only": True,
            "ingested": [],
            "skipped": [],
            "admitted": [],
            "proposed": [],
            "auto_admitted": [],
            "admission": "manual",
            "requalified": [],
            "requalify_recorded": [],
            "force_admitted": [],
            "rejected_invalid": [],
            "extended_added": [],
            "family_parked": {},
            "source": payload.get("source") or "windows_worker",
            "timing_ms": _timing(started, phases),
        }

    to_append: list[tuple[dict, str | None]] = []
    library_retests: set[str] = set()
    signal_corrections: set[str] = set()
    written: list[dict] = []
    auto_admitted: list[str] = []
    with paper_state_lock("discovery"):
        requal_state = load_requalify_state() if requalify_path().exists() else None
        # Fail-once is the index union the display log (including the delta).
        # The parsed log is cached, so this is not a fresh multi-megabyte parse
        # on every result.
        already = tested_discovery_names()
        st = load_pool()
        grads = load_graduated()
        existing = {c["name"] for c in (st.get("champions") or []) if c.get("name")}
        existing |= {g["name"] for g in grads if isinstance(g, dict) and g.get("name")}
        # Retired / parked names stay blocked; a parked twin whose family
        # winner was retired is reconsiderable (families.py).
        existing |= admit_blocked_names()

        for raw in raw_evals:
            rec = _validate_eval(raw)
            if rec is None:
                rejected_invalid.append(
                    str((raw or {}).get("strategy") if isinstance(raw, dict) else "?")
                )
                continue
            name = rec["strategy"]
            # Library gate retest, including a name that is already seated.
            # One fresh log row. A name that is already seated keeps its
            # champion row and its paper account. A pass for a name that
            # is not seated, including a retired name, is seated below.
            lib_entry = library_retest_entry(name)
            if lib_entry is not None:
                stamp_library_retest_result(rec, lib_entry)
                if is_signal_correction(name):
                    rec["signal_revision"] = SIGNAL_REVISION
                library_retests.add(name)
                to_append.append((rec, name))
                ingested.append(name)
                if rec["qualified"]:
                    proposed.append(name)
                continue
            # Corrected signal. The old fail stays in the log. One new row
            # is enough, and only while the newest stored row lacks the
            # revision. A first test of the name is not this branch: it is
            # untested, and the revision is stamped on the way in.
            if is_signal_correction(name):
                rec["signal_revision"] = SIGNAL_REVISION
            if is_signal_correction(name) and signal_correction_pending(name):
                stamp_signal_correction(rec)
                signal_corrections.add(name)
                to_append.append((rec, name))
                ingested.append(name)
                if rec["qualified"]:
                    proposed.append(name)
                continue
            # Requalify lane: record separately. No log row, no admit, no cull.
            if requal_state is not None and record_requalify_unlocked(
                requal_state, rec, datetime.now(timezone.utc)
            ):
                requalify_recorded.append(name)
                continue
            if name in existing:
                skipped.append({"strategy": name, "reason": "already_pooled_or_graduated"})
                continue
            if name in already:
                entry = retest_entry(name)
                if entry is None:
                    skipped.append({"strategy": name, "reason": "already_tested"})
                    continue
                # One fresh v5 row. The name stays a single census entry.
                # A pass is proposed, not seated. The index write and the
                # pending-retest clear share one lock (after_commit) so a
                # claim cannot lease the name again between them.
                stamp_retest_result(rec, entry)
                to_append.append((rec, name))
                ingested.append(name)
                if rec["qualified"]:
                    proposed.append(name)
                continue
            to_append.append((rec, None))
            already.add(name)
            ingested.append(name)
            if rec["qualified"]:
                proposed.append(name)

        if requalify_recorded:
            save_requalify_state(requal_state)

    # The fail-once check above ran under an earlier lock. Re-check it under
    # the append lock so a concurrent ingest (e.g. a retried post) of the
    # same name cannot write a second row.
    t_append = time.perf_counter()
    for rec, retest_name in to_append:
        name = rec["strategy"]
        if retest_name is None:

            def _still_untested(name=name):
                # The index cache is updated before the append lock is
                # released, so a retried post sees the name without
                # merging the display log again.
                from hedge_fund.trading.discovery import cached_tested_names
                from hedge_fund.trading.tested_index import tested_name_set

                if name in tested_name_set():
                    return False
                return name not in cached_tested_names()

            wrote = append_discovery_evaluation(rec, still_valid=_still_untested)
        elif retest_name in library_retests:
            tested_at = rec.get("tested_at")

            def _close_library(name=retest_name, stamp=tested_at):
                complete_library_retest_unlocked(name, tested_at=stamp)

            def _still_library(name=retest_name):
                return library_retest_entry(name) is not None

            wrote = append_discovery_evaluation(
                rec, after_commit=_close_library, still_valid=_still_library
            )
        elif retest_name in signal_corrections:

            def _still_correction(name=retest_name):
                return signal_correction_pending(name)

            wrote = append_discovery_evaluation(rec, still_valid=_still_correction)
        else:
            tested_at = rec.get("tested_at")

            def _close_retest(name=retest_name, stamp=tested_at):
                complete_retest_unlocked(name, tested_at=stamp)

            def _still_pending(name=retest_name):
                return retest_entry(name) is not None

            wrote = append_discovery_evaluation(
                rec, after_commit=_close_retest, still_valid=_still_pending
            )
        if wrote is None:
            if name in ingested:
                ingested.remove(name)
            if name in proposed:
                proposed.remove(name)
            skipped.append({"strategy": name, "reason": "already_tested"})
            continue
        # A corrected-signal row is in this list only when the append
        # landed. A pass here is seated below, the same as any other lib.* pass.
        written.append(rec)
        if isinstance(rec.get("library"), dict):
            from hedge_fund.library.results import save_library_result

            save_library_result(rec)
    mark("append", t_append)

    with paper_state_lock("discovery"):
        scan = _park_scan_due(force=qualified_in or bool(raw_force))
        t_scan = time.perf_counter()
        if scan:
            log = load_discovery_log()
            requal_rows, requal_names = requalify_parked_log(log, existing_names=existing)
            _mark_park_scanned()
        else:
            log = None
            requal_rows, requal_names = [], []
        mark("park_scan", t_scan)
        if requal_names:
            # The 5m flip is a proposal. It does not seat. Library rows
            # are not flipped by this scan.
            for rec in requal_rows:
                requalified.append(rec["strategy"])
                if rec["strategy"] not in proposed:
                    proposed.append(rec["strategy"])

        from hedge_fund.trading.approved_admit import seat_library_passes_unlocked

        # A row written by this ingest is a new evaluation. Replaying a
        # stored pass does not land in ``written``, so a retired name is
        # not put back from the old row.
        auto_admitted = seat_library_passes_unlocked(
            st,
            existing,
            written,
            reevaluated={
                rec["strategy"]
                for rec in written
                if isinstance(rec.get("strategy"), str)
            },
        )
        for name in auto_admitted:
            if name not in admitted:
                admitted.append(name)

        if raw_force:
            if log is None:
                log = load_discovery_log()
            force_source = str(payload.get("source") or "manual_force_admit")
            force_admitted = _apply_force_admit(
                raw_force,
                source=force_source,
                st=st,
                existing=existing,
                log=log,
                skipped=skipped,
            )
            for name in force_admitted:
                if name not in admitted:
                    admitted.append(name)

        if (requal_names or force_admitted) and log is not None:
            save_discovery_log(log)

        families = None
        if admitted:
            save_pool(st)
            unpark_unlocked(admitted)
        # One champion per near-identical family: a new pass is parked or
        # replaces (and parks) its family winner. Also retries deferred twins.
        families = enforce_champion_families_unlocked(datetime.now(timezone.utc))

        if raw_extended:
            names = [n for n in raw_extended if isinstance(n, str) and n]
            if names:
                before = set(load_extended_names())
                append_extended_batch(names)
                added_extended = [n for n in names if n not in before]

        clear_names = list(ingested)
        for row in skipped:
            reason = row.get("reason")
            if reason in {"already_tested", "already_pooled_or_graduated"} and row.get("strategy"):
                clear_names.append(row["strategy"])
        if clear_names:
            clear_leases_unlocked(clear_names)

        t_hb = time.perf_counter()
        _touch_heartbeat_and_flight(payload)
        mark("heartbeat", t_hb)

    return {
        "ok": True,
        "paper_only": True,
        "ingested": ingested,
        "skipped": skipped,
        "admitted": admitted,
        "proposed": proposed,
        "auto_admitted": auto_admitted,
        "admission": "auto" if auto_admitted else "manual",
        "requalified": requalified,
        "requalify_recorded": requalify_recorded,
        "force_admitted": force_admitted,
        "rejected_invalid": rejected_invalid,
        "extended_added": added_extended,
        "family_parked": (families or {}).get("parked") or {},
        "source": payload.get("source") or "windows_worker",
        "timing_ms": _timing(started, phases),
    }
