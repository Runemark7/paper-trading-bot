"""One-shot admits a human approved by name. Paper only.

The requalify lane records verdicts and never admits. When Alexander
approves a requalify pass by name, it is listed in ``APPROVED_ADMITS``
and seated once on server start from the stored verdict. That path still
requires a qualified verdict. The OOS gate is not re-run or changed.

``FORWARD_TEST_ADMITS`` is a different kind. Alexander can seat a library
name that failed gate v6 as an unproven paper forward test. The champion
row is built from ``evaluate_library_record`` on the library tape. It is
stored with ``admission="forward_test_unproven"`` and ``qualified=False``.
This path does not call ``force_admit`` and does not write the discovery
log, so a stored ``qualified`` flag stays as it was.

The marker in ``approved_admits.json`` makes each batch idempotent across
restarts. Only the listed name is touched: no cull, no retire, no change
to the requalify state, the discovery log, the tested index, or the
one-per-family rule.

``LIBRARY_APPROVED_ADMITS`` is the qualified branch of that same path for
``lib.*``. Alexander approved ``lib.rsi2`` and ``lib.sma200`` at
2026-10-29 11:01 CET. Each is seated only when ``evaluate_library_record``
is a pass under the current ``GATE_RULES``. Warnings and ``approved_by``
are stored on the champion row. ``qualified`` stays true. ``lib.sma100``
stays on its unproven forward-test batch and is not rewritten.
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
from hedge_fund.trading.constants import GATE_RULES, QUAL_TIMEFRAME, RISK_POLICY, V3_GATE_RULES
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
# Alexander, 2026-10-29 09:30 CET: paper-trade lib.sma100 even though gate
# v6 fails, and only on DSR. This is not a qualified admit.
FORWARD_TEST_ADMISSION = "forward_test_unproven"
FORWARD_TEST_BATCH = "lib-sma100-forward-test-20261029"
FORWARD_TEST_ADMITS: tuple[dict, ...] = (
    {
        "name": "lib.sma100",
        "batch": FORWARD_TEST_BATCH,
        "approved_by": "Alexander",
        "approved_at": "2026-10-29T09:30+01:00",
        "note": "unproven paper forward-test; fails gate v6 only on DSR 0.848 < 0.95",
    },
)
# Alexander, 2026-10-29 11:01 CET: seat the two library names that pass
# the profit gate. This is the qualified approved-admit path. A missing
# or unqualified verdict leaves no marker, so a later start can retry.
# lib.sma100 is not in this list.
LIBRARY_APPROVED_BATCH = "lib-rsi2-sma200-v7-20261029"
LIBRARY_APPROVED_ADMITS: tuple[dict, ...] = (
    {
        "name": "lib.rsi2",
        "approved_by": "Alexander",
        "approved_at": "2026-10-29T11:01+01:00",
        "note": "qualified paper forward-test; gate v7b profit pass",
    },
    {
        "name": "lib.sma200",
        "approved_by": "Alexander",
        "approved_at": "2026-10-29T11:01+01:00",
        "note": "qualified paper forward-test; gate v7b profit pass",
    },
)
# Alexander, 2026-10-30 09:59 CET: seat the four batch-2 library names
# that pass gate v7b as qualified paper forward tests. Same qualified
# path and rules as LIBRARY_APPROVED_BATCH. Paper only; no champion cap.
LIBRARY_APPROVED_BATCH_2 = "lib-batch2-four-v7b-20261030"
LIBRARY_APPROVED_ADMITS_2: tuple[dict, ...] = tuple(
    {
        "name": name,
        "approved_by": "Alexander",
        "approved_at": "2026-10-30T09:59+01:00",
        "note": "qualified paper forward-test; gate v7b profit pass (library batch 2)",
    }
    for name in ("lib.ichimoku", "lib.rsi14", "lib.tsmom180d", "lib.rsi2_10")
)
# Qualified library batches, applied in this order.
LIBRARY_APPROVED_BATCHES: tuple[tuple[str, tuple[dict, ...]], ...] = (
    (LIBRARY_APPROVED_BATCH, LIBRARY_APPROVED_ADMITS),
    (LIBRARY_APPROVED_BATCH_2, LIBRARY_APPROVED_ADMITS_2),
)
_GATE_V4_KEYS = (
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
    """The stored verdict for ``batch_id`` if it is a pass on an accepted tag.

    Current ``GATE_RULES`` always matches. The historical rules-v3 batch
    also matches ``V3_GATE_RULES`` so a wiped marker still seats the
    2026-10-09 decision and does not wait for a v4 re-run.
    """
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
        rules = result.get("gate_rules")
        # The 2026-10-09 one-shot was measured on the pre-holdout tag.
        # A wiped marker must still match that stored verdict. It is not
        # a v4 admit: the batch id is the historical rules-v3 batch.
        if rules != GATE_RULES and not (
            batch_id == "rules-v3-tiled-20261008" and rules == V3_GATE_RULES
        ):
            continue
        return result
    return None


def _library_forward_record(name: str, tested_at: str) -> dict | None:
    """One library evaluation on the pod tape. None until that tape can be scored.

    The copy is forced to ``qualified=False``. Nothing here writes the
    discovery log. A database error is logged by type only, so a URL in
    the exception text cannot land in the log.
    """
    from hedge_fund.library.evaluate import build_library_data, evaluate_library_record
    from hedge_fund.library.tape import load_library_daily

    try:
        bars = load_library_daily()
    except Exception as exc:
        log.warning("forward-test admit: library tape unreadable for %s (%s)", name, type(exc).__name__)
        return None
    if len(bars) == 0:
        log.info("forward-test admit: library tape empty; %s waits for the next start", name)
        return None
    try:
        data = build_library_data(bars)
        record = evaluate_library_record(name, data, tested_at=tested_at)
    except Exception as exc:
        log.warning("forward-test admit: %s not evaluated (%s)", name, type(exc).__name__)
        return None
    if not isinstance(record, dict) or record.get("strategy") != name:
        log.warning("forward-test admit: evaluation for %s did not return that name", name)
        return None
    out = dict(record)
    # The gate verdict is not an admission. force_admit would rewrite this
    # to True in the discovery log; this path never does that.
    out["qualified"] = False
    gate = out.get("gate_v4")
    if isinstance(gate, dict):
        summarised = {key: gate.get(key) for key in _GATE_V4_KEYS}
        summarised["passed"] = bool(gate.get("passed"))
        reasons = out.get("fail_reasons")
        summarised["fail_reasons"] = list(reasons) if isinstance(reasons, list) else []
        out["gate_v4"] = summarised
    return out


def _label_forward_champion(row: dict, spec: dict, record: dict) -> None:
    """Mark the seated row as an unproven forward test. ``qualified`` stays false.

    ``_admit_qualified`` copies the library gate summary and omits
    ``fail_reasons``. Put those reasons on the summary here. This does not
    write ``qualified=True`` onto the discovery log.
    """
    row["source"] = FORWARD_TEST_ADMISSION
    row["admission"] = FORWARD_TEST_ADMISSION
    row["qualified"] = False
    gate = dict(row.get("gate_v4") or {})
    recorded = record.get("gate_v4") if isinstance(record.get("gate_v4"), dict) else {}
    gate["passed"] = bool(recorded.get("passed"))
    reasons = record.get("fail_reasons")
    gate["fail_reasons"] = list(reasons) if isinstance(reasons, list) else []
    row["gate_v4"] = gate
    row["provenance"] = {
        "admit_batch": spec["batch"],
        "approved_by": spec["approved_by"],
        "approved_at": spec["approved_at"],
        "note": spec["note"],
        "admission": FORWARD_TEST_ADMISSION,
    }


def _library_verdict_qualified(record: object, name: str) -> bool:
    """True when ``record`` is a pass for ``name`` on the current gate tag."""
    if not isinstance(record, dict) or record.get("strategy") != name:
        return False
    if record.get("qualified") is not True or record.get("gate_rules") != GATE_RULES:
        return False
    gate = record.get("gate_v4")
    return isinstance(gate, dict) and gate.get("passed") is True and gate.get("rules") == GATE_RULES


def _score_qualified_library(name: str, tested_at: str) -> dict | None:
    """One library evaluation. None when the tape cannot be scored.

    The returned record is the evaluation, including ``qualified=False``.
    Nothing here writes the discovery log. A database error is logged by
    type only, so a URL in the exception text cannot land in the log.
    """
    from hedge_fund.library.evaluate import build_library_data, evaluate_library_record
    from hedge_fund.library.tape import load_library_daily

    try:
        bars = load_library_daily()
    except Exception as exc:
        log.warning("library approved admit: tape unreadable for %s (%s)", name, type(exc).__name__)
        return None
    if len(bars) == 0:
        log.info("library approved admit: library tape empty; %s waits for the next start", name)
        return None
    try:
        data = build_library_data(bars)
        record = evaluate_library_record(name, data, tested_at=tested_at)
    except Exception as exc:
        log.warning("library approved admit: %s not evaluated (%s)", name, type(exc).__name__)
        return None
    if not isinstance(record, dict):
        return None
    return record


def _warning_lines(record: dict) -> list[str]:
    raw = record.get("warnings")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str)]


def _label_qualified_library(
    row: dict, spec: dict, record: dict, batch_id: str = LIBRARY_APPROVED_BATCH
) -> None:
    """Record the approval on a library champion that passed the gate.

    ``qualified`` stays true. ``lib.sma100`` is not passed through here, so
    its ``forward_test_unproven`` label is not rewritten.
    """
    warnings = _warning_lines(record)
    row["qualified"] = True
    row["warnings"] = warnings
    row["provenance"] = {
        "admit_batch": batch_id,
        "approved_by": spec["approved_by"],
        "approved_at": spec["approved_at"],
        "note": spec["note"],
        "gate_rules": record.get("gate_rules"),
        "warnings": warnings,
    }


def _apply_requalify_unlocked(now: datetime, marker: dict) -> dict | None:
    """Seat ``APPROVED_ADMIT_BATCH`` once. Caller holds the discovery lock.

    Returns None (and leaves the marker unchanged) if any listed name lacks
    a stored qualified pass, so a later start can retry.
    """
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


def _apply_forward_unlocked(
    now: datetime,
    marker: dict,
    forward_record: dict | None,
    forward_checked: bool,
) -> dict | None:
    """Seat ``FORWARD_TEST_BATCH`` once. Does not require a qualified verdict.

    Returns None and writes no marker when the library tape cannot be
    scored, so the next server start tries again. A scored name is seated
    with ``qualified=False`` even when the evaluation object said otherwise.
    """
    from hedge_fund.trading.ingest import _admit_qualified

    stamp = now.astimezone(timezone.utc).isoformat()
    records: dict[str, dict] = {}
    for spec in FORWARD_TEST_ADMITS:
        if forward_checked:
            record = None
            if isinstance(forward_record, dict) and forward_record.get("strategy") == spec["name"]:
                record = forward_record
        else:
            record = _library_forward_record(spec["name"], stamp)
        if record is None:
            return None
        records[spec["name"]] = record

    st = load_pool()
    pooled = {c.get("name") for c in (st.get("champions") or []) if isinstance(c, dict)}
    graduated = {g.get("name") for g in load_graduated() if isinstance(g, dict)}
    retired = retired_names()
    existing = set(pooled) | set(graduated) | set(retired)
    admitted: list[str] = []
    skipped: list[dict] = []
    for spec in FORWARD_TEST_ADMITS:
        name = spec["name"]
        if name in retired:
            skipped.append({"strategy": name, "reason": "retired"})
            continue
        if name in pooled or name in graduated:
            skipped.append({"strategy": name, "reason": "already_pooled_or_graduated"})
            continue
        record = dict(records[name])
        record["qualified"] = False
        if not _admit_qualified(st, record, existing):
            skipped.append({"strategy": name, "reason": "not_seated"})
            continue
        seated = st["champions"][-1]
        _label_forward_champion(seated, spec, record)
        pooled.add(name)
        admitted.append(name)
    if admitted:
        save_pool(st)
    marker["applied"].append(FORWARD_TEST_BATCH)
    marker["batches"].append({
        "batch_id": FORWARD_TEST_BATCH,
        "applied_at": stamp,
        "admitted": admitted,
        "skipped": skipped,
        "admission": FORWARD_TEST_ADMISSION,
    })
    _save_marker(marker)
    log.info("forward-test admit: batch %s admitted %s", FORWARD_TEST_BATCH, admitted)
    return {"batch_id": FORWARD_TEST_BATCH, "admitted": admitted, "skipped": skipped}


def _apply_library_approved_unlocked(
    now: datetime,
    marker: dict,
    records: dict | None,
    checked: bool,
    batch_id: str = LIBRARY_APPROVED_BATCH,
    admits: tuple[dict, ...] = LIBRARY_APPROVED_ADMITS,
) -> dict | None:
    """Seat one qualified library batch once, from qualified library verdicts.

    Returns None and writes no marker when the tape cannot be scored or a
    listed name that is not already on the book lacks a current-tag pass.
    A later start tries again. Already-pooled names are not duplicated.
    """
    from hedge_fund.trading.ingest import _admit_qualified

    stamp = now.astimezone(timezone.utc).isoformat()
    scored: dict[str, dict]
    if checked:
        if not isinstance(records, dict):
            return None
        scored = records
    else:
        scored = {}
        for spec in admits:
            record = _score_qualified_library(spec["name"], stamp)
            if record is None:
                return None
            scored[spec["name"]] = record

    st = load_pool()
    pooled = {c.get("name") for c in (st.get("champions") or []) if isinstance(c, dict)}
    graduated = {g.get("name") for g in load_graduated() if isinstance(g, dict)}
    retired = retired_names()
    existing = set(pooled) | set(graduated) | set(retired)
    pending: list[tuple[dict, dict]] = []
    skipped: list[dict] = []
    for spec in admits:
        name = spec["name"]
        if name in retired:
            skipped.append({"strategy": name, "reason": "retired"})
            continue
        if name in pooled or name in graduated:
            skipped.append({"strategy": name, "reason": "already_pooled_or_graduated"})
            continue
        record = scored.get(name)
        if not _library_verdict_qualified(record, name):
            return None
        pending.append((spec, record))

    admitted: list[str] = []
    for spec, record in pending:
        if not _admit_qualified(st, dict(record), existing):
            skipped.append({"strategy": spec["name"], "reason": "not_seated"})
            continue
        _label_qualified_library(st["champions"][-1], spec, record, batch_id)
        admitted.append(spec["name"])
    if admitted:
        save_pool(st)
    marker["applied"].append(batch_id)
    marker["batches"].append({
        "batch_id": batch_id,
        "applied_at": stamp,
        "admitted": admitted,
        "skipped": skipped,
        "qualified": True,
    })
    _save_marker(marker)
    log.info("library approved admit: batch %s admitted %s", batch_id, admitted)
    return {"batch_id": batch_id, "admitted": admitted, "skipped": skipped}


def ensure_approved_admits_unlocked(
    now: datetime,
    *,
    forward_record: dict | None = None,
    forward_checked: bool = False,
    library_records: dict | None = None,
    library_checked: bool = False,
) -> dict | None:
    """Apply each approved batch once. Caller holds the discovery lock.

    The requalify batch still waits for a qualified verdict and leaves no
    marker until that verdict exists. The forward-test batch waits for a
    library-tape evaluation and also leaves no marker until that exists.
    Each qualified library batch (``LIBRARY_APPROVED_BATCHES``) waits for
    a current-tag pass for every name in it that is not already seated. When more than one batch runs, the
    requalify result is the return value so existing callers keep seeing
    that batch. The forward-test result is next.
    """
    marker = _load_marker()
    requalify_out = None
    if APPROVED_ADMIT_BATCH not in marker["applied"]:
        requalify_out = _apply_requalify_unlocked(now, marker)
    forward_out = None
    if FORWARD_TEST_BATCH not in marker["applied"]:
        forward_out = _apply_forward_unlocked(now, marker, forward_record, forward_checked)
    library_out = None
    for batch_id, admits in LIBRARY_APPROVED_BATCHES:
        if batch_id in marker["applied"] or not admits:
            continue
        # ``library_records`` maps batch id -> pre-scored records (or None
        # when that tape could not be scored) when ``library_checked``.
        records = library_records.get(batch_id) if isinstance(library_records, dict) else None
        out = _apply_library_approved_unlocked(
            now, marker, records, library_checked, batch_id, admits
        )
        if library_out is None:
            library_out = out
    if requalify_out is not None:
        return requalify_out
    if forward_out is not None:
        return forward_out
    return library_out


def _score_library_batch(
    tested_at: str, admits: tuple[dict, ...] = LIBRARY_APPROVED_ADMITS
) -> dict | None:
    """Evaluations for every name in one qualified-library batch, or None if the tape is not ready."""
    scored: dict[str, dict] = {}
    for spec in admits:
        record = _score_qualified_library(spec["name"], tested_at)
        if record is None:
            return None
        scored[spec["name"]] = record
    return scored


def run_approved_admits() -> dict | None:
    from hedge_fund.trading.families import enforce_champion_families_unlocked

    now = datetime.now(timezone.utc)
    forward_checked = False
    forward_record = None
    library_checked = False
    library_records = None
    applied = _load_marker()["applied"]
    # Score outside the discovery lock. The web process does this once per
    # start, and only until the marker exists.
    if FORWARD_TEST_BATCH not in applied and FORWARD_TEST_ADMITS:
        forward_checked = True
        forward_record = _library_forward_record(FORWARD_TEST_ADMITS[0]["name"], now.isoformat())
    pending = [(b, a) for b, a in LIBRARY_APPROVED_BATCHES if b not in applied and a]
    if pending:
        library_checked = True
        library_records = {b: _score_library_batch(now.isoformat(), a) for b, a in pending}
    with paper_state_lock("discovery"):
        out = ensure_approved_admits_unlocked(
            now,
            forward_record=forward_record,
            forward_checked=forward_checked,
            library_records=library_records,
            library_checked=library_checked,
        )
        # Startup pass of the one-per-family rule (idempotent; runs after the
        # approved admits so it sees them). The rule itself is unchanged.
        families = enforce_champion_families_unlocked(now)
    if families and families.get("parked"):
        log.info("families: parked %s", families["parked"])
    if out is not None and out.get("batch_id") == APPROVED_ADMIT_BATCH:
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
