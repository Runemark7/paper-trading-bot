"""Read-only discovery insights for GET /api/discovery/insights.

Built from the capped display log (newest evaluation per name) that the
discovery summary already holds in memory. It reports:

* how many names under the current gate reached each stage,
* which gate checks reject names most often,
* the closest near-misses (cleared the base floors, then failed the
  fewest robustness checks) with the checks they failed,
* the newest evaluations, slim.

Cached by the display-log fingerprint and rebuilt at most every
``MIN_REBUILD_SECONDS``. Does not read the results jsonl, mint, claim, or
touch the gate.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from hedge_fund.paths import state_root
from hedge_fund.trading.constants import GATE_RULES

MIN_REBUILD_SECONDS = 30.0
NEAR_MISS_LIMIT = 8
RECENT_LIMIT = 12

BASE_GATES = ("trades", "sharpe", "baseline", "windows")
ROBUST_GATES = ("holdout", "dsr", "pbo", "perturb", "cost")
GATE_ORDER = BASE_GATES[:3] + ROBUST_GATES + ("windows", "bh_legacy", "ops")

_lock = threading.Lock()
_cached: dict = {"key": None, "built": 0.0, "payload": None}


def clear_insights_cache() -> None:
    with _lock:
        _cached.update({"key": None, "built": 0.0, "payload": None})


def classify_reason(reason: str) -> str:
    """Map one stored fail-reason string to a gate key."""
    r = str(reason or "").strip().lower()
    if r.startswith("holdout"):
        return "holdout"
    if r.startswith("oos_trades"):
        return "trades"
    if r.startswith("oos_sharpe"):
        return "sharpe"
    if "sma_stack" in r:
        return "baseline"
    if r.startswith("dsr"):
        return "dsr"
    if r.startswith("pbo"):
        return "pbo"
    if "neighbour" in r or "neighbor" in r:
        return "perturb"
    if r.startswith("cost stress"):
        return "cost"
    if r.startswith("windows"):
        return "windows"
    if "b&h" in r or "buy-and-hold" in r or "buy_and_hold" in r or r.startswith("bh"):
        return "bh_legacy"
    return "ops"


def failed_gates(row: dict) -> list[str]:
    reasons = row.get("fail_reasons")
    if not isinstance(reasons, list):
        return []
    seen: list[str] = []
    for reason in reasons:
        key = classify_reason(reason)
        if key not in seen:
            seen.append(key)
    return seen


def _finite(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _gate_numbers(row: dict) -> dict | None:
    block = row.get("gate_v4")
    if not isinstance(block, dict):
        return None
    holdout = block.get("holdout") if isinstance(block.get("holdout"), dict) else {}
    return {
        "dsr": _finite(block.get("dsr")),
        "pbo": _finite(block.get("pbo")),
        "neighbours": block.get("neighbours"),
        "neighbours_pass": block.get("neighbours_pass"),
        "cost_sharpe": _finite(block.get("cost_sharpe")),
        "holdout_sharpe": _finite(holdout.get("sharpe")),
        "holdout_trades": holdout.get("trades"),
    }


def _slim(row: dict) -> dict:
    gates = failed_gates(row)
    return {
        "strategy": row.get("strategy"),
        "tested_at": row.get("tested_at"),
        "qualified": bool(row.get("qualified")),
        "sharpe": _finite(row.get("sharpe")),
        "daily_sharpe": _finite(row.get("daily_sharpe")),
        "trades": row.get("trades"),
        "test_pnl": _finite(row.get("test_pnl")),
        "failed_gates": gates,
        "fail_reasons": [str(r) for r in (row.get("fail_reasons") or []) if isinstance(r, str)],
        "gate": _gate_numbers(row),
        "gate_rules": row.get("gate_rules"),
    }


def build_insights(log: list[dict]) -> dict:
    from hedge_fund.trading.discovery import latest_eval_per_strategy

    latest = latest_eval_per_strategy(log)
    current = [r for r in latest if r.get("gate_rules") == GATE_RULES]
    gate_counts = {key: 0 for key in GATE_ORDER}
    reached_robust = 0
    passed = 0
    near: list[tuple[tuple, dict]] = []
    for row in current:
        gates = failed_gates(row)
        for key in gates:
            gate_counts[key] = gate_counts.get(key, 0) + 1
        if row.get("qualified"):
            passed += 1
            reached_robust += 1
            continue
        if any(g in BASE_GATES for g in gates):
            continue
        reached_robust += 1
        robust_fails = [g for g in gates if g in ROBUST_GATES]
        numbers = _gate_numbers(row) or {}
        dsr = numbers.get("dsr")
        sharpe = _finite(row.get("sharpe"))
        rank = (
            len(robust_fails) or 99,
            -(dsr if dsr is not None else -1.0),
            -(sharpe if sharpe is not None else -99.0),
        )
        near.append((rank, row))
    near.sort(key=lambda item: item[0])

    newest = sorted(
        (r for r in latest if isinstance(r.get("tested_at"), str)),
        key=lambda r: r["tested_at"],
        reverse=True,
    )[:RECENT_LIMIT]

    return {
        "paper_only": True,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gate_rules": GATE_RULES,
        "scope": "display_log_latest_per_name",
        "rows_scanned": len(latest),
        "current_gate": {
            "tested": len(current),
            "cleared_base_floors": reached_robust,
            "passed": passed,
        },
        "gate_failures": [
            {"gate": key, "count": gate_counts[key]}
            for key in GATE_ORDER
            if gate_counts.get(key)
        ],
        "near_misses": [_slim(row) for _rank, row in near[:NEAR_MISS_LIMIT]],
        "recent": [_slim(row) for row in newest],
        "note": (
            "Counts cover the newest evaluation per name in the capped display log "
            "under the current gate tag. A name can fail several checks. "
            "Buy-and-hold figures are informational and never a pass rule."
        ),
    }


def discovery_insights_payload() -> dict:
    from hedge_fund.trading.discovery import discovery_log_fingerprint, load_discovery_log

    key = (str(state_root().resolve()), discovery_log_fingerprint())
    now = time.monotonic()
    with _lock:
        payload = _cached["payload"]
        fresh = _cached["key"] == key
        recent = now - _cached["built"] < MIN_REBUILD_SECONDS
        if payload is not None and (fresh or recent) and _cached["key"][0] == key[0]:
            return payload
    payload = build_insights(load_discovery_log())
    with _lock:
        _cached.update({"key": key, "built": time.monotonic(), "payload": payload})
    return payload
