"""Durable tested-name index for discovery skip / fail-once.

``discovery_log.json`` is a newest-first display log capped at
``DISCOVERY_LOG_CAP``. Skip/dedupe used to read that file, so trimming the
oldest row made an already-tested name eligible again. This index is the
source of truth: one entry per strategy name plus a qualified flag,
uncapped, under ``paper_state_lock``. The display log may stay capped.

Startup migration copies whatever rows are still in the log. Names dropped
before the index existed are not recoverable from the cap. Later appends
record the name here before the log is trimmed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from hedge_fund.paths import state_root
from hedge_fund.trading.store import paper_state_lock

DISCOVERY_TESTED_INDEX = "discovery_tested.json"


def tested_index_path() -> Path:
    return state_root() / DISCOVERY_TESTED_INDEX


def load_tested_index() -> dict[str, bool]:
    """Read the index. Missing or corrupt → empty. Does not migrate."""
    path = tested_index_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    raw = data.get("names") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, bool] = {}
    for name, flag in raw.items():
        if isinstance(name, str) and name:
            out[name] = bool(flag)
    return out


def save_tested_index(index: dict[str, bool]) -> None:
    """Atomic replace. Caller holds ``paper_state_lock('discovery')``."""
    path = tested_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "v": 1,
        "paper_only": True,
        "names": {name: 1 if qual else 0 for name, qual in sorted(index.items())},
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    os.replace(tmp, path)


def merge_tested_rows(index: dict[str, bool], rows: list) -> bool:
    """Overlay newest-first rows onto ``index``. Names absent from ``rows`` stay.

    Returns True when ``index`` changed. Does not write.
    """
    seen: set[str] = set()
    changed = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("strategy")
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        qual = bool(row.get("qualified"))
        if name not in index or index[name] != qual:
            index[name] = qual
            changed = True
    return changed


def ensure_tested_index() -> dict[str, bool]:
    """Merge the on-disk display log into the index. Holds the paper-state lock.

    No-ops without creating the state dir when neither file exists, so an
    in-memory log passed to skip helpers does not touch disk.
    """
    from hedge_fund.trading.discovery import discovery_log_path, load_discovery_log

    if not tested_index_path().exists() and not discovery_log_path().exists():
        return {}
    with paper_state_lock("discovery"):
        index = load_tested_index()
        if merge_tested_rows(index, load_discovery_log()):
            save_tested_index(index)
        return index


def flags_for_skip(log: list | None = None) -> dict[str, bool]:
    """Index ∪ newest row of ``log`` (log wins for names it still contains).

    ``log is None`` migrates the on-disk log first. A passed log is overlaid
    on a read of the index and does not by itself create the state dir.
    """
    if log is None:
        return ensure_tested_index()
    flags = dict(load_tested_index())
    merge_tested_rows(flags, log)
    return flags
