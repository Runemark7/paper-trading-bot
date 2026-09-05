"""Discovery log + in-flight batch files. Last-known, not a job runner.

Tournament writes ``discovery_in_flight.json`` at batch start (the leftover
name list) and deletes it when the sweep finishes. ``discovery_log.json``
is appended after evaluations, as before.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.paths import state_root

DISCOVERY_LOG = "discovery_log.json"
DISCOVERY_IN_FLIGHT = "discovery_in_flight.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def discovery_log_path() -> Path:
    return state_root() / DISCOVERY_LOG


def in_flight_path() -> Path:
    return state_root() / DISCOVERY_IN_FLIGHT


def load_discovery_log() -> list[dict]:
    path = discovery_log_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def latest_eval_per_strategy(log: list[dict] | None = None) -> list[dict]:
    """Newest evaluation per strategy name. Log is stored newest-first."""
    if log is None:
        log = load_discovery_log()
    seen: set[str] = set()
    out: list[dict] = []
    for row in log:
        if not isinstance(row, dict):
            continue
        name = row.get("strategy")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(row)
    return out


def write_in_flight(names: list[str]) -> Path:
    """Persist the leftover batch about to be evaluated. Not process liveness."""
    path = in_flight_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "names": list(names),
        "batch_size": len(names),
        "started_at": _now(),
        "source": "tournament_engine.py",
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def clear_in_flight() -> None:
    path = in_flight_path()
    if path.exists():
        path.unlink()


def read_in_flight() -> dict | None:
    path = in_flight_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
