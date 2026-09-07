"""Discovery log, in-flight cycle batch, and rotating leftover cursor.

Last-known files, not a job runner. Tournament appends one evaluation to
``discovery_log.json`` as soon as that name finishes (newest-first, capped).
``discovery_in_flight.json`` lists this cycle's budget names and shrinks as
they complete. ``discovery_cursor.json`` remembers where the leftover drain
left off so the next live_cycle continues fairly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.paths import state_root
from hedge_fund.trading.constants import (
    DISCOVER_CYCLE_MAX_NAMES,
    DISCOVER_RETEST_COOLDOWN_SECONDS,
    DISCOVERY_LOG_CAP,
)

DISCOVERY_LOG = "discovery_log.json"
DISCOVERY_IN_FLIGHT = "discovery_in_flight.json"
DISCOVERY_CURSOR = "discovery_cursor.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def discovery_log_path() -> Path:
    return state_root() / DISCOVERY_LOG


def in_flight_path() -> Path:
    return state_root() / DISCOVERY_IN_FLIGHT


def cursor_path() -> Path:
    return state_root() / DISCOVERY_CURSOR


def parse_tested_at(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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


def newest_eval(rows: list[dict] | None = None) -> dict | None:
    """Row with the latest tested_at. Does not use list order or alpha sort."""
    if rows is None:
        rows = load_discovery_log()
    best: dict | None = None
    best_ts: datetime | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = parse_tested_at(row.get("tested_at") if isinstance(row.get("tested_at"), str) else None)
        if ts is None:
            continue
        if best_ts is None or ts > best_ts:
            best_ts = ts
            best = row
    return best


def evals_on_utc_date(log: list[dict], day: datetime | None = None) -> int:
    """Count log rows whose tested_at falls on the given UTC calendar day."""
    if day is None:
        day = datetime.now(timezone.utc)
    day = day.astimezone(timezone.utc)
    key = day.date().isoformat()
    n = 0
    for row in log:
        if not isinstance(row, dict):
            continue
        ts = parse_tested_at(row.get("tested_at") if isinstance(row.get("tested_at"), str) else None)
        if ts is not None and ts.date().isoformat() == key:
            n += 1
    return n


def append_discovery_evaluations(eval_records: list[dict], *, cap: int = DISCOVERY_LOG_CAP) -> list[dict]:
    """Prepend records (newest-first) and cap. Writes immediately."""
    if not eval_records:
        return load_discovery_log()
    path = discovery_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    log = load_discovery_log()
    log = list(eval_records) + log
    log = log[:cap]
    path.write_text(json.dumps(log, indent=2))
    return log


def append_discovery_evaluation(record: dict, *, cap: int = DISCOVERY_LOG_CAP) -> list[dict]:
    """Append one finished evaluation now. Crash-safe mid-batch progress."""
    return append_discovery_evaluations([record], cap=cap)


def write_in_flight(
    names: list[str],
    *,
    current: str | None = None,
    remaining: list[str] | None = None,
    completed: list[str] | None = None,
    batch_size: int | None = None,
    started_at: str | None = None,
) -> Path:
    """Persist this cycle's budget names. Not process liveness.

    ``names`` is the still-outstanding set (current + remaining). ``batch_size``
    stays the original planned size for this cycle.
    """
    path = in_flight_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = read_in_flight()
    started = started_at or ((prev or {}).get("started_at") if prev else None) or _now()
    rem = list(remaining) if remaining is not None else [n for n in names if n != current]
    outstanding = list(names)
    if current and current not in outstanding:
        outstanding = [current] + outstanding
    planned = batch_size
    if planned is None:
        planned = (prev or {}).get("batch_size")
    if planned is None:
        planned = len(outstanding)
    payload = {
        "names": outstanding,
        "batch_size": planned,
        "started_at": started,
        "source": "tournament_engine.py",
        "current": current,
        "remaining": rem,
        "completed": list(completed or []),
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


def load_cursor() -> dict:
    path = cursor_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cursor(next_name: str | None, **extra) -> Path:
    path = cursor_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "next_name": next_name,
        "updated_at": _now(),
        "source": "tournament_engine.py",
        **extra,
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def prioritize_leftovers(
    leftovers: list[str],
    log: list[dict] | None = None,
    *,
    now: datetime | None = None,
    cooldown_seconds: int = DISCOVER_RETEST_COOLDOWN_SECONDS,
) -> list[str]:
    """Never-tested first (stable leftover order), then oldest tested past cooldown.

    Names tested inside the cooldown are skipped so we do not thrash the same
    rejects. This is not a random sample and does not drop the rest forever.
    """
    if log is None:
        log = load_discovery_log()
    if now is None:
        now = datetime.now(timezone.utc)
    latest = {r["strategy"]: r for r in latest_eval_per_strategy(log) if r.get("strategy")}
    never: list[str] = []
    cooled: list[tuple[datetime, str]] = []
    for name in leftovers:
        row = latest.get(name)
        if not row:
            never.append(name)
            continue
        ts = parse_tested_at(row.get("tested_at") if isinstance(row.get("tested_at"), str) else None)
        if ts is None:
            never.append(name)
            continue
        if (now - ts).total_seconds() >= cooldown_seconds:
            cooled.append((ts, name))
    cooled.sort(key=lambda item: item[0])
    return never + [name for _, name in cooled]


def rotate_from_cursor(names: list[str], cursor_name: str | None) -> list[str]:
    """Start at cursor_name (inclusive) and wrap. Missing cursor → original order."""
    if not names:
        return []
    if not cursor_name or cursor_name not in names:
        return list(names)
    i = names.index(cursor_name)
    return names[i:] + names[:i]


def select_cycle_batch(
    leftovers: list[str],
    log: list[dict] | None = None,
    *,
    max_names: int = DISCOVER_CYCLE_MAX_NAMES,
    cooldown_seconds: int = DISCOVER_RETEST_COOLDOWN_SECONDS,
    now: datetime | None = None,
    cursor_name: str | None = None,
) -> tuple[list[str], list[str]]:
    """Planned names for this cycle and the full rotated eligible list."""
    eligible = prioritize_leftovers(
        leftovers, log, now=now, cooldown_seconds=cooldown_seconds,
    )
    rotated = rotate_from_cursor(eligible, cursor_name)
    n = max(0, int(max_names))
    return rotated[:n], rotated
