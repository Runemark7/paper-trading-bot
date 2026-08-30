"""Last-known sidecar stamps. Not a job runner.

Heartbeat and live_cycle write small JSON files under PAPER_STATE so the
web UI can show last-pass / last-phase times. Absence of a stamp means
no signal — never invent a spinner from that.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from hedge_fund.paths import state_root

PIPELINE_STAMP = "pipeline_stamp.json"
HEARTBEAT_STAMP = "heartbeat.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(name: str) -> Path:
    return state_root() / name


def write_json_stamp(name: str, payload: dict) -> Path:
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path


def read_json_stamp(name: str) -> dict | None:
    path = _path(name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_pipeline_stamp(phase: str, status: str, **extra) -> Path:
    """phase: idle | tournament | run_isolated | collect_live_results | hourly_report."""
    payload = {
        "phase": phase,
        "status": status,  # started | finished
        "at": _now(),
        "source": "live_cycle.py",
        **extra,
    }
    if status == "started":
        payload["started_at"] = payload["at"]
        prev = read_json_stamp(PIPELINE_STAMP) or {}
        # keep prior finished_at so the UI can still show last completion
        if prev.get("finished_at"):
            payload["finished_at"] = prev["finished_at"]
    elif status == "finished":
        payload["finished_at"] = payload["at"]
        prev = read_json_stamp(PIPELINE_STAMP) or {}
        payload["started_at"] = extra.get("started_at") or prev.get("started_at")
    return write_json_stamp(PIPELINE_STAMP, payload)


def write_heartbeat_stamp(*, closed: int, accounts_checked: int, interval_seconds: int) -> Path:
    return write_json_stamp(
        HEARTBEAT_STAMP,
        {
            "last_pass_at": _now(),
            "closed": closed,
            "accounts_checked": accounts_checked,
            "interval_seconds": interval_seconds,
            "source": "hedge_fund.trading.heartbeat",
        },
    )
