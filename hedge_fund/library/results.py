"""Stored library evaluations for ``GET /api/discovery/library``.

Ingest writes each library record to ``library_results.json`` (one small
JSON object keyed by name, ~15 entries). The web request only reads that
file, cached on its stat, and merges it with the static catalog. Nothing
is evaluated on a request.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from hedge_fund.library.catalog import (
    claim_names,
    library_manifest,
    library_trial_count,
)
from hedge_fund.paths import state_root

LIBRARY_RESULTS = "library_results.json"
_CACHE: dict = {"key": None, "value": None}
_CACHE_LOCK = threading.Lock()

# Fields the table needs; the full record stays in the file and the log.
_ROW_KEYS = (
    "strategy",
    "tested_at",
    "qualified",
    "fail_reasons",
    "daily_sharpe",
    "trades",
    "test_pnl",
    "sma_stack_oos_pnl",
    "bh_daily_sharpe",
    "bh_oos_pnl",
    "backtest_max_dd",
    "avg_hold_hours",
    "worker_id",
    "data_end",
    "gate_rules",
    "warnings",
    "oos_net_pnl",
)


def results_path() -> Path:
    return state_root() / LIBRARY_RESULTS


def _stat_key(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path), st.st_mtime_ns, st.st_size, st.st_ino)


def load_library_results() -> dict[str, dict]:
    path = results_path()
    key = _stat_key(path)
    with _CACHE_LOCK:
        if key is not None and _CACHE["key"] == key and isinstance(_CACHE["value"], dict):
            return _CACHE["value"]
    if key is None:
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    value = data.get("results") if isinstance(data, dict) else None
    value = value if isinstance(value, dict) else {}
    with _CACHE_LOCK:
        _CACHE["key"] = key
        _CACHE["value"] = value
    return value


def save_library_result(record: dict) -> None:
    """Store one library evaluation. Atomic replace under its own lock."""
    from hedge_fund.trading.store import paper_state_lock

    name = record.get("strategy") if isinstance(record, dict) else None
    if not isinstance(name, str) or not name:
        return
    path = results_path()
    with paper_state_lock("library-results"):
        current = dict(load_library_results())
        current[name] = record
        payload = {"paper_only": True, "results": current}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":"), default=str))
        os.replace(tmp, path)


def library_progress(tested: set[str] | None = None, leased: set[str] | None = None) -> dict:
    names = claim_names()
    tested = tested or set()
    leased = leased or set()
    done = [n for n in names if n in tested]
    active = [n for n in names if n in leased and n not in tested]
    stored = load_library_results()
    passed = [n for n in done if bool((stored.get(n) or {}).get("qualified"))]
    return {
        "strategies": len(names),
        "tested": len(done),
        "leased": len(active),
        "queued": len(names) - len(done) - len(active),
        "passed": len(passed),
        "trial_count": library_trial_count(),
    }


def _slim(record: dict) -> dict:
    row = {key: record.get(key) for key in _ROW_KEYS if key in record}
    lib = record.get("library") if isinstance(record.get("library"), dict) else {}
    gate = record.get("gate_v4") if isinstance(record.get("gate_v4"), dict) else {}
    hold = lib.get("holdout") if isinstance(lib.get("holdout"), dict) else {}
    row.update({
        "checks": lib.get("checks") or {},
        "failing_checks": lib.get("failing_checks") or [],
        "warning_checks": lib.get("warning_checks") or [],
        "warnings": record.get("warnings") or [],
        "count_only": bool(lib.get("count_only")),
        "exposure": (lib.get("discovery") or {}).get("exposure"),
        "bh_max_dd": (lib.get("bh") or {}).get("max_drawdown"),
        "holdout_sharpe": hold.get("daily_sharpe"),
        "holdout_trades": hold.get("trades"),
        "holdout_pnl": hold.get("pnl"),
        "holdout_bh_sharpe": (hold.get("bh") or {}).get("daily_sharpe"),
        "oos_net_pnl": gate.get("oos_pnl", record.get("oos_net_pnl")),
        "cost_oos_pnl": gate.get("cost_oos_pnl"),
        "cost_holdout_pnl": gate.get("cost_holdout_pnl"),
        "cost_sharpe": gate.get("cost_sharpe"),
        "dsr": gate.get("dsr"),
        "n_trials": gate.get("n_trials"),
        "pbo": gate.get("pbo"),
        "neighbours": gate.get("neighbours"),
        "neighbours_pass": gate.get("neighbours_pass"),
        "dsr_required_sharpe": (lib.get("dsr") or {}).get("required_annual_sharpe"),
        "dsr_days_needed": (lib.get("dsr") or {}).get("days_needed"),
    })
    return row


def library_table_payload(*, tested: set[str] | None = None, leased: set[str] | None = None) -> dict:
    stored = load_library_results()
    tested = set(tested or ()) | set(stored)
    leased = set(leased or ())
    rows = []
    for item in library_manifest():
        name = item["name"]
        record = stored.get(name)
        if isinstance(record, dict):
            status = "tested"
        elif name in leased:
            status = "testing"
        else:
            status = "queued"
        rows.append({**item, "status": status, "result": _slim(record) if isinstance(record, dict) else None})
    return {
        "paper_only": True,
        "progress": library_progress(tested, leased),
        "strategies": rows,
        "note": (
            "Pre-registered strategy library (gate v7b). A name passes when net P&L after "
            "fees and slippage is positive on the warmed out-of-sample book and on the "
            "sealed holdout, including at 2× fees + 5 bps, with at least 8 out-of-sample "
            "round-trips and 2 holdout trades. DSR, PBO, Sharpe, neighbours, sma_stack, "
            "and buy-and-hold are warnings. A pass is proposed, awaiting approval; it is "
            "not seated."
        ),
    }
