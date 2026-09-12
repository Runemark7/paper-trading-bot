"""Durable Windows discovery-farm pause flag + worker heartbeat.

``state/discovery_farm.json`` is last-known, not process liveness.
Stop sets ``enabled=false``; Start sets ``enabled=true``. The Windows
worker polls GET /api/discovery/summary and idles instead of exiting.
A recent heartbeat means the process is still up; Start cannot relaunch
a dead worker.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hedge_fund.paths import state_root
from hedge_fund.trading.discovery import parse_tested_at
from hedge_fund.trading.store import paper_state_lock

DISCOVERY_FARM = "discovery_farm.json"
FARM_HEARTBEAT_STALE_SECONDS = 900  # 15 min; one walk-forward can take a while
FARM_STATUSES = ("running", "paused", "worker_idle", "worker_unseen")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def farm_path() -> Path:
    return state_root() / DISCOVERY_FARM


def default_farm() -> dict:
    return {
        "enabled": True,
        "updated_at": None,
        "heartbeat_at": None,
        "heartbeat_status": None,
        "source": "windows_worker",
        "paper_only": True,
    }


def _read_farm_unlocked() -> dict:
    path = farm_path()
    data = default_farm()
    if not path.exists():
        return data
    try:
        raw = path.read_text()
        parsed = json.loads(raw)
    except (OSError, ValueError):
        return data
    if not isinstance(parsed, dict):
        return data
    if "enabled" in parsed:
        data["enabled"] = bool(parsed["enabled"])
    for key in ("updated_at", "heartbeat_at", "heartbeat_status", "source"):
        if key in parsed:
            data[key] = parsed[key]
    return data


def _write_farm_unlocked(data: dict) -> dict:
    path = farm_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": bool(data.get("enabled", True)),
        "updated_at": data.get("updated_at"),
        "heartbeat_at": data.get("heartbeat_at"),
        "heartbeat_status": data.get("heartbeat_status"),
        "source": data.get("source") or "windows_worker",
        "paper_only": True,
    }
    path.write_text(json.dumps(payload, indent=2))
    return payload


def load_farm() -> dict:
    return _read_farm_unlocked()


def set_farm_enabled(enabled: bool) -> dict:
    """UI Start/Stop. Does not kill the Windows process."""
    with paper_state_lock("discovery"):
        data = _read_farm_unlocked()
        data["enabled"] = bool(enabled)
        data["updated_at"] = _now()
        _write_farm_unlocked(data)
    return farm_status_block()


def apply_heartbeat_unlocked(status: str | None = None) -> dict:
    """Caller already holds ``paper_state_lock('discovery')``."""
    data = _read_farm_unlocked()
    data["heartbeat_at"] = _now()
    if status:
        data["heartbeat_status"] = str(status)
    return _write_farm_unlocked(data)


def record_heartbeat(status: str | None = None) -> dict:
    with paper_state_lock("discovery"):
        return apply_heartbeat_unlocked(status)


def farm_status_block(
    *,
    in_flight_active: bool = False,
    now: datetime | None = None,
) -> dict:
    """Public farm slice for GET /api/discovery/summary."""
    raw = _read_farm_unlocked()
    enabled = bool(raw.get("enabled", True))
    hb_at = raw.get("heartbeat_at")
    hb_ts = parse_tested_at(hb_at if isinstance(hb_at, str) else None)
    clock = now or datetime.now(timezone.utc)
    age = (clock - hb_ts).total_seconds() if hb_ts else None
    seen = age is not None and age <= FARM_HEARTBEAT_STALE_SECONDS

    if not enabled:
        status = "paused"
    elif not seen:
        status = "worker_unseen"
    elif in_flight_active:
        status = "running"
    else:
        status = "worker_idle"

    if not seen:
        unseen = (
            "Worker not seen — Start will not relaunch the process. "
            "On jensa run: python scripts/discovery_worker.py --workers 2"
        )
        if age is None:
            unseen = (
                "Worker not seen — no heartbeat yet. Start will not relaunch "
                "the process. On jensa run: python scripts/discovery_worker.py --workers 2"
            )
        note = ("Paused. " + unseen) if not enabled else unseen
    elif not enabled:
        note = (
            "Paused — worker idles with near-zero CPU so the PC can be used "
            "for games. Start resumes the next batch. A batch already running "
            "may finish first."
        )
    elif status == "running":
        note = "Running — Windows worker is evaluating never-tested names."
    else:
        note = "Worker idle — process is up, no batch in flight."

    return {
        "enabled": enabled,
        "status": status,
        "worker_seen": seen,
        "updated_at": raw.get("updated_at"),
        "heartbeat_at": hb_at,
        "heartbeat_age_seconds": age,
        "heartbeat_status": raw.get("heartbeat_status"),
        "source": raw.get("source") or "windows_worker",
        "note": note,
        "paper_only": True,
    }


def farm_enabled_from_summary(summary: Any, last_known: bool = True) -> bool:
    """Worker helper: read the durable flag from a prod summary payload."""
    if not isinstance(summary, dict):
        return last_known
    farm = summary.get("farm")
    if not isinstance(farm, dict) or "enabled" not in farm:
        return last_known
    return bool(farm["enabled"])
