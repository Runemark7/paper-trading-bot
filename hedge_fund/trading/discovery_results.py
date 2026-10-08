"""Append-only full evaluation records, paged by seek.

``discovery_tested.json`` stays the fail-once bool map plus the slim
metrics map. This module does not replace either one.

``discovery_results.jsonl`` is the append-only body. ``discovery_results.ix``
is a fixed-width offset table: a 24-byte header (unique / pass / fail)
and then newest-first refs. Summary reads only that header. A results
page seeks to ``offset`` and reads ``limit`` refs, then seeks those
spans in the jsonl. Neither path loads the jsonl or the tested-name index.

Startup backfill runs on a daemon thread. It does not take
``paper_state_lock('discovery')`` and it does not block ``serve_forever``.
"""
from __future__ import annotations

import json
import os
import struct
import threading
from pathlib import Path

from hedge_fund.paths import state_root

DISCOVERY_RESULTS = "discovery_results.jsonl"
DISCOVERY_RESULTS_IX = "discovery_results.ix"
# Tag written on a freshly appended eval. Historical copies use "backfill".
DISCOVERY_GATE_TAG = "aggregate_oos_no_window_veto"

_HEADER = struct.Struct("<4sIIIII")  # magic, version, count, npass, nfail, reserved
_REF = struct.Struct("<QIqI")  # jsonl offset, length, tested_at unix, flags
_MAGIC = b"DRIX"
_VERSION = 1
_FLAG_QUALIFIED = 1
_CORE = (
    "qualified",
    "fail_reasons",
    "sharpe",
    "trades",
    "test_pnl",
    "bh_oos_pnl",
    "sma_stack_oos_pnl",
    "tested_at",
    "windows",
    "all_windows_nonneg",
)

_lock = threading.Lock()
_backfill_once = threading.Lock()
_backfill_started = False
# In-process map for the write path only. Keyed by state dir so tests
# that switch PAPER_STATE do not reuse another directory's records.
_bound: str | None = None
_map: dict[str, dict] | None = None
_counts_key: tuple | None = None
_counts: dict | None = None


def discovery_results_path() -> Path:
    return state_root() / DISCOVERY_RESULTS


def discovery_results_index_path() -> Path:
    return state_root() / DISCOVERY_RESULTS_IX


def _reset_if_moved() -> None:
    global _bound, _map, _counts_key, _counts
    root = str(state_root())
    if _bound == root:
        return
    _bound = root
    _map = None
    _counts_key = None
    _counts = None


def _invalidate_counts() -> None:
    global _counts_key, _counts
    _counts_key = None
    _counts = None


def peek_result_counts() -> dict | None:
    """Unique / pass / fail from the 24-byte index header.

    ``None`` when the index has not been published yet. Does not open
    the jsonl or ``discovery_tested.json``.
    """
    global _counts_key, _counts
    _reset_if_moved()
    path = discovery_results_index_path()
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if _counts_key == key and _counts is not None:
        return dict(_counts)
    try:
        with path.open("rb") as fh:
            blob = fh.read(_HEADER.size)
    except OSError:
        return None
    if len(blob) < _HEADER.size:
        return None
    magic, version, count, npass, nfail, _reserved = _HEADER.unpack(blob)
    if magic != _MAGIC or version != _VERSION:
        return None
    data = {
        "unique": int(count),
        "tested_pass": int(npass),
        "tested_fail": int(nfail),
    }
    _counts_key = key
    _counts = data
    return dict(data)


def write_result_counts_header(*, tested_pass: int, tested_fail: int) -> None:
    """Publish totals for summary without writing result bodies."""
    path = discovery_results_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = _HEADER.pack(
        _MAGIC,
        _VERSION,
        int(tested_pass) + int(tested_fail),
        int(tested_pass),
        int(tested_fail),
        0,
    )
    tmp = path.with_suffix(".ix.tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, path)
    _reset_if_moved()
    _invalidate_counts()


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _fail_reasons(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _tested_unix(value: object) -> int:
    if not isinstance(value, str) or not value:
        return 0
    from hedge_fund.trading.discovery import parse_tested_at

    ts = parse_tested_at(value)
    if ts is None:
        return 0
    return int(ts.timestamp())


def _record_from_row(row: dict, *, gate: str) -> dict:
    record = {
        "strategy": row["strategy"],
        "qualified": bool(row.get("qualified")),
        "fail_reasons": _fail_reasons(row.get("fail_reasons")),
        "test_pnl": _finite_number(row.get("test_pnl")),
        "bh_oos_pnl": _finite_number(row.get("bh_oos_pnl")),
        "sma_stack_oos_pnl": _finite_number(row.get("sma_stack_oos_pnl")),
        "tested_at": row.get("tested_at") if isinstance(row.get("tested_at"), str) else None,
        "gate": gate,
    }
    sharpe = _finite_number(row.get("sharpe"))
    trades = _finite_number(row.get("trades"))
    if sharpe is not None:
        record["sharpe"] = sharpe
    if trades is not None:
        record["trades"] = int(trades)
    windows = row.get("windows")
    if isinstance(windows, list):
        record["windows"] = windows
    if "all_windows_nonneg" in row and isinstance(row.get("all_windows_nonneg"), bool):
        record["all_windows_nonneg"] = row["all_windows_nonneg"]
    return record


def _stub(name: str, qualified: bool) -> dict:
    return {
        "strategy": name,
        "qualified": bool(qualified),
        "fail_reasons": [],
        "test_pnl": None,
        "bh_oos_pnl": None,
        "sma_stack_oos_pnl": None,
        "tested_at": None,
        "gate": "index_only",
        "partial": True,
    }


def _same_core(left: dict, right: dict) -> bool:
    return all(left.get(key) == right.get(key) for key in _CORE)


def _gate_for(row: dict, name: str, fresh: set[str], prev: dict | None) -> str:
    source = row.get("requalify_source")
    if isinstance(source, str) and source.strip():
        return source.strip()
    if name in fresh:
        return DISCOVERY_GATE_TAG
    if isinstance(prev, dict):
        prev_gate = prev.get("gate")
        if isinstance(prev_gate, str) and prev_gate not in ("", "index_only"):
            return prev_gate
    return "backfill"


def _scan_jsonl() -> dict[str, dict]:
    """Latest line per name, remembering the byte span. Write path only."""
    path = discovery_results_path()
    found: dict[str, dict] = {}
    if not path.exists():
        return found
    try:
        with path.open("rb") as fh:
            while True:
                offset = fh.tell()
                line = fh.readline()
                if not line:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                name = row.get("strategy")
                if not isinstance(name, str) or not name:
                    continue
                row["_offset"] = offset
                row["_length"] = len(line)
                found[name] = row
    except OSError:
        return {}
    return found


def _load_map() -> dict[str, dict]:
    """Caller holds ``_lock``. Scans the jsonl at most once per state dir."""
    global _map
    _reset_if_moved()
    if _map is None:
        _map = _scan_jsonl()
    return _map


def _sort_key(item: tuple[str, dict]) -> tuple:
    name, row = item
    unix = _tested_unix(row.get("tested_at"))
    return (unix <= 0, -unix, name)


def _write_index(rows: dict[str, dict]) -> None:
    ordered = sorted(rows.items(), key=_sort_key)
    passed = [item for item in ordered if item[1].get("qualified")]
    failed = [item for item in ordered if not item[1].get("qualified")]
    parts = [_HEADER.pack(_MAGIC, _VERSION, len(ordered), len(passed), len(failed), 0)]
    for group in (ordered, passed, failed):
        for _name, row in group:
            unix = _tested_unix(row.get("tested_at"))
            flags = _FLAG_QUALIFIED if row.get("qualified") else 0
            parts.append(_REF.pack(int(row.get("_offset") or 0), int(row.get("_length") or 0), unix, flags))
    path = discovery_results_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".ix.tmp")
    tmp.write_bytes(b"".join(parts))
    os.replace(tmp, path)
    _invalidate_counts()


def _index_current(rows: dict[str, dict]) -> bool:
    published = peek_result_counts()
    if published is None:
        return not rows
    passed = sum(1 for row in rows.values() if row.get("qualified"))
    return (
        published["unique"] == len(rows)
        and published["tested_pass"] == passed
        and published["tested_fail"] == len(rows) - passed
    )


def _append_records(pending: list[dict], rows: dict[str, dict]) -> None:
    path = discovery_results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as fh:
        for record in pending:
            payload = json.dumps(
                {key: value for key, value in record.items() if not key.startswith("_")},
                separators=(",", ":"),
            ).encode() + b"\n"
            offset = fh.tell()
            fh.write(payload)
            stored = json.loads(payload)
            stored["_offset"] = offset
            stored["_length"] = len(payload)
            name = stored.get("strategy")
            if isinstance(name, str) and name:
                rows[name] = stored


def _apply_rows(
    rows: dict[str, dict],
    incoming: list,
    *,
    fresh: set[str] | None = None,
    index: dict[str, bool] | None = None,
) -> bool:
    """Merge ``incoming`` into ``rows``. Returns True when the jsonl grew."""
    fresh_names = fresh or set()
    pending: list[dict] = []
    seen: set[str] = set()
    for row in incoming:
        if not isinstance(row, dict):
            continue
        name = row.get("strategy")
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        prev = rows.get(name)
        record = _record_from_row(row, gate=_gate_for(row, name, fresh_names, prev))
        if index is not None and name in index:
            record["qualified"] = bool(index[name])
        if prev is not None and _same_core(prev, record):
            continue
        pending.append(record)
    if index is not None:
        for name, qual in index.items():
            if not isinstance(name, str) or not name or name in seen:
                continue
            prev = rows.get(name)
            if prev is None:
                pending.append(_stub(name, bool(qual)))
                continue
            if bool(prev.get("qualified")) != bool(qual):
                updated = {key: value for key, value in prev.items() if not key.startswith("_")}
                updated["qualified"] = bool(qual)
                pending.append(updated)
    if not pending:
        return False
    _append_records(pending, rows)
    return True


def sync_discovery_results(rows: list, *, fresh: set[str] | None = None) -> bool:
    """Append ``rows`` that changed. Does not take the discovery lock.

    Callers that already updated the bool index should pass only the new
    or rewritten evals, not the historical log.
    """
    with _lock:
        current = _load_map()
        changed = _apply_rows(current, rows, fresh=fresh)
        if changed or not _index_current(current):
            _write_index(current)
            return True
        return False


def backfill_discovery_results() -> None:
    """Copy the display log and any index-only names into the results log.

    Reads those files without ``paper_state_lock``. Safe to call from a
    daemon thread while ingest holds the discovery lock.
    """
    from hedge_fund.trading.discovery import discovery_log_path, load_discovery_log
    from hedge_fund.trading.tested_index import load_tested_index, tested_index_path

    if (
        not tested_index_path().exists()
        and not discovery_log_path().exists()
        and not discovery_results_path().exists()
    ):
        return
    log_rows = load_discovery_log()
    index = load_tested_index()
    with _lock:
        current = _load_map()
        changed = _apply_rows(current, log_rows, index=index)
        if changed or not _index_current(current):
            _write_index(current)


def start_results_backfill() -> None:
    """Start the one-shot backfill thread and return. Does not block."""
    global _backfill_started
    with _backfill_once:
        if _backfill_started:
            return
        _backfill_started = True

    def _run() -> None:
        try:
            backfill_discovery_results()
        except Exception as exc:
            import sys

            print(f"[discovery-results] backfill failed: {exc}", file=sys.stderr, flush=True)

    threading.Thread(target=_run, name="discovery-results-backfill", daemon=True).start()


def _section_start(status: str | None, count: int, npass: int, nfail: int) -> tuple[int, int]:
    """Byte offset of the first ref in the section, and the section length."""
    if status == "pass":
        return _HEADER.size + count * _REF.size, npass
    if status == "fail":
        return _HEADER.size + (count + npass) * _REF.size, nfail
    return _HEADER.size, count


def page_result_records(
    *,
    offset: int,
    limit: int,
    status: str | None = None,
) -> tuple[list[dict], int, dict]:
    """Return ``limit`` records starting at ``offset`` without reading the rest.

    The third value is the published header (zeros when no index exists).
    """
    published = peek_result_counts() or {"unique": 0, "tested_pass": 0, "tested_fail": 0}
    if status == "pass":
        total = published["tested_pass"]
    elif status == "fail":
        total = published["tested_fail"]
    else:
        total = published["unique"]
    if limit <= 0 or offset >= total or total <= 0:
        return [], total, published
    path = discovery_results_index_path()
    body = discovery_results_path()
    try:
        with path.open("rb") as fh:
            blob = fh.read(_HEADER.size)
            if len(blob) < _HEADER.size:
                return [], total, published
            _magic, _version, count, npass, nfail, _reserved = _HEADER.unpack(blob)
            start, section_len = _section_start(status, count, npass, nfail)
            if offset >= section_len:
                return [], total, published
            want = min(limit, section_len - offset)
            fh.seek(start + offset * _REF.size)
            raw_refs = fh.read(want * _REF.size)
    except OSError:
        return [], total, published
    refs = []
    for pos in range(0, len(raw_refs) - _REF.size + 1, _REF.size):
        refs.append(_REF.unpack_from(raw_refs, pos))
    records: list[dict] = []
    if not refs or not body.exists():
        return records, total, published
    try:
        with body.open("rb") as fh:
            for file_offset, length, _unix, _flags in refs:
                if length <= 0:
                    continue
                fh.seek(file_offset)
                piece = fh.read(length)
                try:
                    row = json.loads(piece)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    records.append(row)
    except OSError:
        return records, total, published
    return records, total, published


def records_by_name() -> dict[str, dict]:
    """Write-path snapshot. Not for request handlers."""
    with _lock:
        current = _load_map()
        return {
            name: {key: value for key, value in row.items() if not str(key).startswith("_")}
            for name, row in current.items()
        }
