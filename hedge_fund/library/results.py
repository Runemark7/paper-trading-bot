"""Stored library evaluations for ``GET /api/discovery/library``.

Ingest writes each library record to ``library_results.json`` (one small
JSON object keyed by name, one entry per canonical strategy). The web request only reads that
file, cached on its stat, and merges it with the static catalog. Nothing
is evaluated on a request.
"""
from __future__ import annotations

import json
import math
import os
import re
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

# Champion cards and the discovery table read this slice. It is the same
# extraction as ``_slim``; the full record stays in the file and the log.
LIBRARY_RESULT_FIELDS = (
    "gate_rules",
    "tested_at",
    "oos_net_pnl",
    "trades",
    "holdout_pnl",
    "holdout_trades",
    "cost_oos_pnl",
    "cost_holdout_pnl",
    "warnings",
)
# Numbers a trimmed discovery-results row keeps for a lib.* name.
LIBRARY_PNL_FIELDS = (
    "oos_net_pnl",
    "holdout_pnl",
    "holdout_trades",
    "cost_oos_pnl",
    "cost_holdout_pnl",
)

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


# Buy-and-hold book P&L is the warning threshold, not the window-sum
# ``bh_oos_pnl``. The first number in a warning is the test-year book;
# a second number is the holdout book.
_BH_NUMBER = re.compile(r"buy-and-hold\s+(-?[0-9]+(?:\.[0-9]+)?)")


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _library_block(record: dict | None) -> dict:
    if not isinstance(record, dict):
        return {}
    lib = record.get("library")
    return lib if isinstance(lib, dict) else {}


def _bh_numbers_from_warnings(record: dict | None) -> list[float]:
    if not isinstance(record, dict):
        return []
    warnings = record.get("warnings")
    if not isinstance(warnings, list):
        return []
    found: list[float] = []
    for item in warnings:
        if not isinstance(item, str):
            continue
        for match in _BH_NUMBER.findall(item):
            number = _finite(float(match))
            if number is not None:
                found.append(number)
    return found


def _check_block(record: dict | None, key: str) -> dict | None:
    if not isinstance(record, dict):
        return None
    nested = _library_block(record).get("checks")
    if isinstance(nested, dict) and isinstance(nested.get(key), dict):
        return nested[key]
    top = record.get("checks")
    if isinstance(top, dict) and isinstance(top.get(key), dict):
        return top[key]
    return None


def _book_bh(record: dict | None) -> float | None:
    """Continuous-book buy-and-hold over the test years.

    ``bh_oos_pnl`` on the row is the sum of rebased windows, which is not
    the book the gate compares. The check threshold is that book.
    """
    block = _check_block(record, "buy_and_hold")
    if isinstance(block, dict):
        number = _finite(block.get("threshold"))
        if number is not None:
            return number
    nums = _bh_numbers_from_warnings(record)
    return nums[0] if nums else None


def _holdout_bh(record: dict | None) -> float | None:
    hold = _library_block(record).get("holdout")
    bh = hold.get("bh") if isinstance(hold, dict) else None
    if isinstance(bh, dict):
        number = _finite(bh.get("net_pnl"))
        if number is not None:
            return number
    nums = _bh_numbers_from_warnings(record)
    return nums[1] if len(nums) > 1 else None


def _gate_number(record: dict | None, key: str) -> float | None:
    if not isinstance(record, dict):
        return None
    gate = record.get("gate_v4")
    if isinstance(gate, dict):
        number = _finite(gate.get(key))
        if number is not None:
            return number
    return _finite(record.get(key))


def _warning_checks(record: dict | None) -> list[str] | None:
    lib = _library_block(record)
    raw = None
    if "warning_checks" in lib:
        raw = lib.get("warning_checks")
    elif isinstance(record, dict) and "warning_checks" in record:
        raw = record.get("warning_checks")
    else:
        return None
    if not isinstance(raw, list):
        return None
    return [item for item in raw if isinstance(item, str) and item]


def _strategy_profile(name: object) -> dict:
    """Static catalog sentence. Not an evaluation and not a gate decision."""
    if not isinstance(name, str) or not name:
        return {}
    from hedge_fund.library.catalog import parse_variant

    try:
        strategy, params = parse_variant(name)
    except ValueError:
        return {}
    link = strategy.citation.link.strip()
    profile = {
        "title": strategy.title,
        "rule": strategy.citation.implemented_rule,
        "citation": strategy.citation.short(),
        "safety_stop_pct": round(float(strategy.stop_frac) * 100.0, 2),
        "params": [
            {"name": p.name, "value": int(params[p.name]), "unit": p.unit}
            for p in strategy.params
        ],
    }
    if link:
        profile["citation_link"] = link
    return profile


def _fill_display(out: dict, record: dict | None, stored: dict | None) -> None:
    """Add plain-language fields. Never replaces a P&L already taken from the log."""
    def first(getter):
        value = getter(record)
        if value is None:
            value = getter(stored)
        return value

    book = first(_book_bh)
    hold_bh = first(_holdout_bh)
    if book is not None:
        out["bh_book_pnl"] = book
    if hold_bh is not None:
        out["bh_holdout_pnl"] = hold_bh
    for key in ("dsr", "pbo"):
        number = first(lambda row, key=key: _gate_number(row, key))
        if number is not None:
            out[key] = number
    sharpe = first(lambda row: _finite(row.get("daily_sharpe")) if isinstance(row, dict) else None)
    if sharpe is None:
        sharpe = first(lambda row: _finite(row.get("sharpe")) if isinstance(row, dict) else None)
    if sharpe is not None:
        out["daily_sharpe"] = sharpe
    hold_sharpe = first(
        lambda row: _finite((_library_block(row).get("holdout") or {}).get("daily_sharpe"))
        if isinstance(_library_block(row).get("holdout"), dict)
        else None
    )
    if hold_sharpe is None:
        hold_sharpe = first(lambda row: _finite(row.get("holdout_sharpe")) if isinstance(row, dict) else None)
    if hold_sharpe is not None:
        out["holdout_sharpe"] = hold_sharpe
    checks = first(_warning_checks)
    if checks:
        out["warning_checks"] = checks
    name = None
    for source in (record, stored):
        if isinstance(source, dict) and isinstance(source.get("strategy"), str):
            name = source["strategy"]
            break
    for key, value in _strategy_profile(name).items():
        out.setdefault(key, value)


def library_result_view(record: dict | None, stored: dict | None = None) -> dict | None:
    """Read-only display slice of one library eval. Does not score a tape.

    Nested ``library.holdout`` / ``gate_v4`` win, matching ``_slim``. A
    trimmed discovery-results row has already copied the five P&L numbers
    to the top level and dropped those objects; those top-level keys are
    the fallback. ``stored`` fills display-only gaps (buy-and-hold book,
    Sharpe, DSR, PBO) from ``library_results.json`` and never overrides
    the log's P&L.
    """
    if not isinstance(record, dict):
        record = stored if isinstance(stored, dict) else None
        stored = None
    if not isinstance(record, dict):
        return None
    slim = _slim(record)
    out = {key: slim.get(key) for key in LIBRARY_RESULT_FIELDS}
    if not isinstance(out.get("warnings"), list):
        out["warnings"] = []
    lib = record.get("library") if isinstance(record.get("library"), dict) else {}
    hold = lib.get("holdout") if isinstance(lib.get("holdout"), dict) else None
    gate = record.get("gate_v4") if isinstance(record.get("gate_v4"), dict) else None
    if not isinstance(out.get("gate_rules"), str) and isinstance(gate, dict):
        rules = gate.get("rules")
        if isinstance(rules, str):
            out["gate_rules"] = rules
    if hold is None:
        if out.get("holdout_pnl") is None and "holdout_pnl" in record:
            out["holdout_pnl"] = record.get("holdout_pnl")
        if out.get("holdout_trades") is None and "holdout_trades" in record:
            out["holdout_trades"] = record.get("holdout_trades")
    if not isinstance(gate, dict) or "cost_oos_pnl" not in gate:
        if out.get("cost_oos_pnl") is None and "cost_oos_pnl" in record:
            out["cost_oos_pnl"] = record.get("cost_oos_pnl")
    if not isinstance(gate, dict) or "cost_holdout_pnl" not in gate:
        if out.get("cost_holdout_pnl") is None and "cost_holdout_pnl" in record:
            out["cost_holdout_pnl"] = record.get("cost_holdout_pnl")
    _fill_display(out, record, stored if isinstance(stored, dict) else None)
    return out


def legacy_backtest_view(champ: dict, logged: dict | None = None) -> dict:
    """Read-only 5-minute qualification fields. Omits anything that is not a real number.

    Does not score a tape and does not decide admission. Missing figures are
    left out so the UI can say they were not stored instead of printing a blank.
    """
    qual = champ.get("qual") if isinstance(champ, dict) and isinstance(champ.get("qual"), dict) else {}
    logged = logged if isinstance(logged, dict) else {}

    def num(*values: object) -> float | None:
        for value in values:
            number = _finite(value)
            if number is not None:
                return number
        return None

    out: dict = {"kind": "legacy_5m"}
    numbers = {
        "sharpe": num(qual.get("sharpe"), champ.get("sharpe_qual") if isinstance(champ, dict) else None, logged.get("sharpe")),
        "win_rate_pct": num(champ.get("winrate_qual") if isinstance(champ, dict) else None, logged.get("win_rate_pct")),
        "test_pnl": num(qual.get("test_pnl"), logged.get("test_pnl")),
        "trades": num(qual.get("trades"), logged.get("trades")),
        "daily_sharpe": num(qual.get("daily_sharpe"), logged.get("daily_sharpe")),
        "bh_daily_sharpe": num(qual.get("bh_daily_sharpe"), logged.get("bh_daily_sharpe")),
        "bh_oos_pnl": num(logged.get("bh_oos_pnl")),
    }
    for key, value in numbers.items():
        if value is None:
            continue
        if key == "trades" and float(value).is_integer():
            out[key] = int(value)
        else:
            out[key] = value
    if "test_pnl" not in out:
        out.pop("bh_oos_pnl", None)
    texts = {
        "timeframe": (champ.get("timeframe") if isinstance(champ, dict) else None) or logged.get("timeframe"),
        "risk_policy": (champ.get("risk_policy") if isinstance(champ, dict) else None) or logged.get("risk_policy"),
        "gate_rules": qual.get("gate_rules") or logged.get("gate_rules"),
    }
    for key, value in texts.items():
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


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
            "and buy-and-hold are warnings. A canonical lib.* pass is seated as a "
            "paper forward-test champion. A fail is not."
        ),
    }
