"""Durable tested-name index for discovery skip / fail-once.

``discovery_log.json`` is a newest-first display log capped at
``DISCOVERY_LOG_CAP``. Skip/dedupe used to read that file, so trimming the
oldest row made an already-tested name eligible again. This index is the
source of truth: one entry per strategy name plus a qualified flag,
uncapped, under ``paper_state_lock``. The display log may stay capped.

Startup migration copies whatever rows are still in the log. Names dropped
before the index existed are not recoverable from the cap. Later appends
record the name here before the log is trimmed.

``metrics`` sits beside the bool map. Fail-once still reads only ``names``.
A flag-only save keeps metrics already on disk so a trim of the display
log does not forget the OOS numbers. The metrics read is
``load_tested_metrics``: ``{strategy: {sharpe, trades, ops_park}}``.
That map is not the full eval record.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict

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


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _metric_from_row(row: dict) -> dict | None:
    from hedge_fund.trading.discovery_guard import is_ops_park_record

    item: dict = {}
    sharpe = _finite_number(row.get("sharpe"))
    trades = _finite_number(row.get("trades"))
    if sharpe is not None:
        item["sharpe"] = sharpe
    if trades is not None:
        item["trades"] = int(trades)
    if is_ops_park_record(row):
        item["ops_park"] = True
    return item or None


def _clean_metrics(metrics: dict) -> dict[str, TestedMetric]:
    out: dict[str, dict] = {}
    for name in sorted(metrics):
        raw = metrics.get(name)
        if not isinstance(name, str) or not name or not isinstance(raw, dict):
            continue
        item: dict = {}
        sharpe = _finite_number(raw.get("sharpe"))
        trades = _finite_number(raw.get("trades"))
        if sharpe is not None:
            item["sharpe"] = sharpe
        if trades is not None:
            item["trades"] = int(trades)
        if raw.get("ops_park"):
            item["ops_park"] = True
        if item:
            out[name] = item
    return out


class TestedMetric(TypedDict, total=False):
    """One name in the ``metrics`` map. Only keys that were measured are set.

    ``ops_park`` is present only when the newest row was an ops park.
    """

    sharpe: float
    trades: int
    ops_park: bool


def load_tested_metrics() -> dict[str, TestedMetric]:
    """Read ``discovery_tested.json`` ``metrics``. Missing or corrupt → {}.

    Does not take the lock and does not migrate. Fail-once stays on
    ``load_tested_index``. This returns the slim sharpe/trades/ops_park
    map, not a full evaluation record.
    """
    path = tested_index_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    raw = data.get("metrics") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    return _clean_metrics(raw)


def save_tested_index(index: dict[str, bool], metrics: dict | None = None) -> None:
    """Atomic replace. Caller holds ``paper_state_lock('discovery')``.

    ``metrics is None`` keeps whatever metrics are already on disk so a
    qualified-flag write cannot wipe them.
    """
    if metrics is None:
        metrics = load_tested_metrics()
    path = tested_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "v": 1,
        "paper_only": True,
        "names": {name: 1 if qual else 0 for name, qual in sorted(index.items())},
        "metrics": _clean_metrics(metrics),
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


def merge_tested_metrics(metrics: dict[str, dict], rows: list) -> bool:
    """Overlay newest-first sharpe/trades onto ``metrics``. Does not write.

    The first row for a name wins, matching the display log. Ops-park rows
    are stored so a later trim does not train on them as ordinary zeros.
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
        item = _metric_from_row(row)
        if item is None:
            continue
        if metrics.get(name) != item:
            metrics[name] = item
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
        metrics = load_tested_metrics()
        rows = load_discovery_log()
        flags_changed = merge_tested_rows(index, rows)
        metrics_changed = merge_tested_metrics(metrics, rows)
        if flags_changed or metrics_changed:
            save_tested_index(index, metrics)
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
