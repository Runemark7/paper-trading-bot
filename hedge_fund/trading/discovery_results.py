"""Append-only full evaluation records, paged by seek.

``discovery_tested.json`` stays the fail-once bool map plus the slim
metrics map. This module does not replace either one.

``discovery_results.jsonl`` is the append-only body. ``discovery_results.ix``
is a fixed-width offset table: a 24-byte header (unique / pass / fail)
and then newest-first refs. An ingest does not rebuild that table. It
appends one fixed-width record to ``discovery_results.ixj`` under a
cross-process file lock. Summary counts are the header plus that
journal. A results page merges the journal (newest first) onto the
base refs. An empty journal is the previous seek path.

The journal's header names the base generation (the index header's
reserved field) it extends. Compaction rewrites the index, bumps that
generation, and deletes the journal, off the request. A journal for
another generation is ignored, so a crash between the rewrite and the
unlink cannot apply the same rows twice. A short tail that is not a
whole record is ignored.

Startup backfill runs on a daemon thread. It does not take
``paper_state_lock('discovery')`` and it does not block ``serve_forever``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import threading
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[misc, assignment]

from hedge_fund.paths import state_root

DISCOVERY_RESULTS = "discovery_results.jsonl"
DISCOVERY_RESULTS_IX = "discovery_results.ix"
DISCOVERY_RESULTS_JOURNAL = "discovery_results.ixj"
# Fold the journal back into the base index off the request, same budget
# as the display-log delta. A record is 32 bytes, so this is a few
# thousand appends, not the whole book.
RESULTS_JOURNAL_MAX_BYTES = 256 * 1024
# Tag written on a freshly appended eval. Historical copies use "backfill".
DISCOVERY_GATE_TAG = "aggregate_oos_no_window_veto"

_HEADER = struct.Struct("<4sIIIII")  # magic, version, count, npass, nfail, generation
_REF = struct.Struct("<QIqI")  # jsonl offset, length, tested_at unix, flags
_JHDR = struct.Struct("<4sIII")  # magic, version, generation, reserved
_JREC = struct.Struct("<QIqIQ")  # offset, length, unix, flags, name hash
_MAGIC = b"DRIX"
_VERSION = 1
_JMAGIC = b"DRIJ"
_JVERSION = 1
_FLAG_QUALIFIED = 1
# Journal only. NEW adds a name; without NEW the record replaces one
# already in the base (or an earlier journal record). PREV_QUALIFIED is
# the qualification before this record, so counts move without a scan.
_FLAG_NEW = 2
_FLAG_PREV_QUALIFIED = 4
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
    "regimes_tested",
)
# Provenance is not part of _CORE. A deploy must not rewrite every
# historical line just to stamp worker_id / data_end / data_hash.
_PROVENANCE_UNKNOWN = "unknown"
# A retest writes a new line when these change, even if the scored
# core matches. Absent on every historical row, so a backfill that
# does not carry them does not rewrite the file.
_RETEST_FIELDS = (
    "retest",
    "retest_reason",
    "retest_batch",
    "retest_from_rules",
    "requeued_at",
    "prior_fail_reasons",
)

_lock = threading.Lock()
_backfill_once = threading.Lock()
_backfill_started = False
# This thread already holds the results file lock. flock is not re-entrant
# across two opens, and peek runs while a writer still holds the lock.
_flock_local = threading.local()
# In-process map for the write path only. Keyed by state dir so tests
# that switch PAPER_STATE do not reuse another directory's records.
_bound: str | None = None
_map: dict[str, dict] | None = None
# (inode, bytes scanned) of the jsonl behind ``_map``. Another process may
# append between our calls; the next locked call scans only the new tail.
_map_pos: tuple[int, int] | None = None
_counts_key: tuple | None = None
_counts: dict | None = None
# Slim rows for densify seeds. Published by the write path and backfill.
# Claim reads this and does not open the jsonl.
_seed_view: dict[str, dict] | None = None


def discovery_results_path() -> Path:
    return state_root() / DISCOVERY_RESULTS


def discovery_results_index_path() -> Path:
    return state_root() / DISCOVERY_RESULTS_IX


def discovery_results_journal_path() -> Path:
    return state_root() / DISCOVERY_RESULTS_JOURNAL


def _results_lock_path() -> Path:
    return state_root() / ".discovery_results.lock"


def _reset_if_moved() -> None:
    global _bound, _map, _counts_key, _counts, _seed_view
    root = str(state_root())
    if _bound == root:
        return
    _bound = root
    _map = None
    global _map_pos
    _map_pos = None
    _counts_key = None
    _counts = None
    _seed_view = None


def _invalidate_counts() -> None:
    global _counts_key, _counts
    _counts_key = None
    _counts = None


def _file_stat(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path), st.st_mtime_ns, st.st_size, st.st_ino)


class _ResultsLock:
    """Cross-process lock for the results jsonl and its index journal.

    The in-process ``_lock`` is acquired by writers before this one.
    Readers take only this lock, shared. A nested acquire on the same
    thread reuses the open lock (peek while installing).
    """

    def __init__(self, exclusive: bool) -> None:
        self.exclusive = exclusive
        self._fh = None
        self._nested = False

    def __enter__(self) -> "_ResultsLock":
        depth = getattr(_flock_local, "depth", 0)
        if depth:
            _flock_local.depth = depth + 1
            self._nested = True
            return self
        path = _results_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+")
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH)
        self._fh = fh
        _flock_local.depth = 1
        return self

    def __exit__(self, *exc: object) -> None:
        if self._nested:
            _flock_local.depth = getattr(_flock_local, "depth", 1) - 1
            return
        _flock_local.depth = 0
        fh = self._fh
        self._fh = None
        if fh is None:
            return
        if fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def _name_hash(name: str) -> int:
    digest = hashlib.blake2s(name.encode("utf-8"), digest_size=8).digest()
    return struct.unpack("<Q", digest)[0]


def _read_header(path: Path) -> tuple[int, int, int, int] | None:
    """``(count, npass, nfail, generation)`` or ``None`` when unpublished."""
    try:
        with path.open("rb") as fh:
            blob = fh.read(_HEADER.size)
    except OSError:
        return None
    if len(blob) < _HEADER.size:
        return None
    magic, version, count, npass, nfail, generation = _HEADER.unpack(blob)
    if magic != _MAGIC or version != _VERSION:
        return None
    return int(count), int(npass), int(nfail), int(generation)


def _generation() -> int:
    header = _read_header(discovery_results_index_path())
    if header is None:
        return 0
    return header[3]


def _read_journal_records(generation: int) -> list[tuple]:
    """Whole records that extend ``generation``. A short tail is dropped."""
    path = discovery_results_journal_path()
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if len(raw) < _JHDR.size:
        return []
    magic, version, gen, _reserved = _JHDR.unpack_from(raw, 0)
    if magic != _JMAGIC or version != _JVERSION or int(gen) != int(generation):
        return []
    body = raw[_JHDR.size:]
    usable = len(body) - (len(body) % _JREC.size)
    return [_JREC.unpack_from(body, pos) for pos in range(0, usable, _JREC.size)]


def _fold_counts(count: int, npass: int, nfail: int, records: list[tuple]) -> tuple[int, int, int]:
    for _offset, _length, _unix, flags, _name_hash in records:
        qualified = bool(flags & _FLAG_QUALIFIED)
        if flags & _FLAG_NEW:
            count += 1
            if qualified:
                npass += 1
            else:
                nfail += 1
            continue
        prev = bool(flags & _FLAG_PREV_QUALIFIED)
        if prev and not qualified:
            npass -= 1
            nfail += 1
        elif qualified and not prev:
            npass += 1
            nfail -= 1
    return count, npass, nfail


def _counts_fingerprint() -> tuple:
    return (
        str(state_root()),
        _file_stat(discovery_results_index_path()),
        _file_stat(discovery_results_journal_path()),
    )


def _peek_unlocked() -> dict | None:
    """Caller holds the results file lock (shared or exclusive)."""
    global _counts_key, _counts
    key = _counts_fingerprint()
    if _counts_key == key and _counts is not None:
        return dict(_counts)
    header = _read_header(discovery_results_index_path())
    if header is None:
        _counts_key = key
        _counts = None
        return None
    count, npass, nfail, generation = header
    count, npass, nfail = _fold_counts(count, npass, nfail, _read_journal_records(generation))
    data = {
        "unique": count,
        "tested_pass": npass,
        "tested_fail": nfail,
    }
    _counts_key = key
    _counts = data
    return dict(data)


def peek_result_counts() -> dict | None:
    """Unique / pass / fail from the index header plus the append journal.

    ``None`` when the index has not been published yet. Does not open
    the jsonl or ``discovery_tested.json``. An empty journal is the header.
    """
    _reset_if_moved()
    with _ResultsLock(exclusive=False):
        return _peek_unlocked()


def write_result_counts_header(*, tested_pass: int, tested_fail: int) -> None:
    """Publish totals for summary without writing result bodies.

    Drops a journal so the header is the whole count. Callers that only
    need a number (no rows) use this; an ingest does not.
    """
    path = discovery_results_index_path()
    with _ResultsLock(exclusive=True):
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = _HEADER.pack(
            _MAGIC,
            _VERSION,
            int(tested_pass) + int(tested_fail),
            int(tested_pass),
            int(tested_fail),
            (_generation() + 1) & 0xFFFFFFFF,
        )
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, path)
        journal = discovery_results_journal_path()
        try:
            journal.unlink()
        except OSError:
            pass
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


def _regimes_tested(row: dict) -> int | None:
    value = row.get("regimes_tested")
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        number = int(value)
        if number >= 0:
            return number
    windows = row.get("windows")
    if isinstance(windows, list):
        return len(windows)
    return None


def _provenance_fields(row: dict) -> dict:
    """worker_id, data_end, data_hash. Absent fields are unknown provenance."""
    worker = row.get("worker_id")
    if isinstance(worker, str) and worker.strip():
        worker = worker.strip()
    else:
        worker = None
    data_end = row.get("data_end")
    if isinstance(data_end, bool) or not isinstance(data_end, (int, float)):
        data_end = None
    else:
        data_end = int(data_end)
    data_hash = row.get("data_hash")
    if isinstance(data_hash, str) and data_hash.strip():
        data_hash = data_hash.strip().lower()
    else:
        data_hash = None
    marked = row.get("provenance")
    if worker and data_end is not None and data_hash and marked in ("shared", "unverified"):
        provenance = marked
    elif worker and data_end is not None and data_hash:
        provenance = "shared"
    else:
        provenance = _PROVENANCE_UNKNOWN
        worker = worker or _PROVENANCE_UNKNOWN
    return {
        "worker_id": worker,
        "data_end": data_end,
        "data_hash": data_hash,
        "provenance": provenance,
    }


def _retest_fields(row: dict) -> dict:
    """Provenance for one v4 buy-and-hold retest. Omitted when absent."""
    if row.get("retest") is not True:
        return {}
    out: dict = {"retest": True}
    for key in ("retest_reason", "retest_batch", "retest_from_rules", "requeued_at"):
        value = row.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    prior = row.get("prior_fail_reasons")
    if isinstance(prior, list):
        out["prior_fail_reasons"] = [item for item in prior if isinstance(item, str)]
    return out


def _same_retest(left: dict, right: dict) -> bool:
    return all(left.get(key) == right.get(key) for key in _RETEST_FIELDS)


def _seed_item(row: dict) -> dict:
    """Fields densify needs. Window payloads are not copied."""
    item: dict = {}
    for field in ("sharpe", "trades", "test_pnl", "bh_oos_pnl", "qualified"):
        if field in row and row.get(field) is not None:
            item[field] = row[field]
    counted = _regimes_tested(row)
    if counted is not None:
        item["regimes_tested"] = counted
    return item


def _publish_seed(name: str, row: dict) -> None:
    global _seed_view
    if _seed_view is None:
        _seed_view = {}
    _seed_view[name] = _seed_item(row)


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
        "regimes_tested": _regimes_tested(row),
    }
    record.update(_provenance_fields(row))
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
    # Amendment 2026-10-08 fields; absent on rows from the old engine.
    for key in ("daily_sharpe", "bh_daily_sharpe", "avg_hold_hours"):
        val = _finite_number(row.get(key))
        if val is not None:
            record[key] = val
    if isinstance(row.get("gate_rules"), str):
        record["gate_rules"] = row["gate_rules"]
    record.update(_retest_fields(row))
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
        "regimes_tested": None,
        "worker_id": _PROVENANCE_UNKNOWN,
        "data_end": None,
        "data_hash": None,
        "provenance": _PROVENANCE_UNKNOWN,
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


def _scan_jsonl(start: int = 0) -> tuple[dict[str, dict], int]:
    """Latest line per name from ``start``, remembering the byte span.

    Returns the rows and the offset after the last complete line. A
    partial last line (a writer mid-append or a crash) is not consumed.
    Write path only.
    """
    path = discovery_results_path()
    found: dict[str, dict] = {}
    end = start
    if not path.exists():
        return found, end
    try:
        with path.open("rb") as fh:
            fh.seek(start)
            while True:
                offset = fh.tell()
                line = fh.readline()
                if not line or not line.endswith(b"\n"):
                    break
                end = offset + len(line)
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
                found[name] = _retain_row(row)
    except OSError:
        return found, end
    return found, end


def _load_map() -> dict[str, dict]:
    """Caller holds ``_lock`` and the results file lock.

    The full jsonl is scanned once per state dir. Later calls scan only
    bytes another process appended since (web, heartbeat, and cycle share
    the volume). A replaced or shrunk file is rescanned.
    """
    global _map, _seed_view, _map_pos
    _reset_if_moved()
    try:
        st = discovery_results_path().stat()
        ino, size = st.st_ino, st.st_size
    except OSError:
        ino, size = None, 0
    if _map is not None and _map_pos is not None and _map_pos[0] == ino and _map_pos[1] <= size:
        if _map_pos[1] < size:
            tail, end = _scan_jsonl(_map_pos[1])
            _map.update(tail)
            for name, row in tail.items():
                _publish_seed(name, row)
            _map_pos = (ino, end)
        return _map
    _map, end = _scan_jsonl()
    _map_pos = (ino, end)
    _seed_view = {name: _seed_item(row) for name, row in _map.items()}
    return _map


def load_seed_view() -> dict[str, dict]:
    """Slim seed rows already in memory. Does not open the jsonl.

    Ingest and the background backfill publish this view. Claim reads it.
    A process that has not scanned yet returns an empty view rather than
    parsing ``discovery_results.jsonl`` on the request.
    """
    with _lock:
        _reset_if_moved()
        if _seed_view is None:
            return {}
        return dict(_seed_view)


def _sort_key(item: tuple[str, dict]) -> tuple:
    name, row = item
    unix = _tested_unix(row.get("tested_at"))
    return (unix <= 0, -unix, name)


def _index_bytes(rows: dict[str, dict], generation: int) -> bytes:
    ordered = sorted(rows.items(), key=_sort_key)
    passed = [item for item in ordered if item[1].get("qualified")]
    failed = [item for item in ordered if not item[1].get("qualified")]
    parts = [_HEADER.pack(
        _MAGIC, _VERSION, len(ordered), len(passed), len(failed), int(generation),
    )]
    for group in (ordered, passed, failed):
        for _name, row in group:
            unix = _tested_unix(row.get("tested_at"))
            flags = _FLAG_QUALIFIED if row.get("qualified") else 0
            parts.append(_REF.pack(
                int(row.get("_offset") or 0), int(row.get("_length") or 0), unix, flags,
            ))
    return b"".join(parts)


def _write_index(rows: dict[str, dict], *, generation: int = 0) -> None:
    path = discovery_results_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(_index_bytes(rows, generation))
    os.replace(tmp, path)
    _invalidate_counts()


def _remove_journal() -> None:
    try:
        discovery_results_journal_path().unlink()
    except OSError:
        pass


def _install_full_index(rows: dict[str, dict]) -> None:
    """Rewrite the base index from ``rows`` and drop the journal.

    Bumps the generation when the journal has live records, so a crash
    before the unlink cannot fold those rows on top of the new index.
    Caller holds ``_lock`` and the exclusive results file lock.
    """
    # Always a new generation: any journal on disk (live, stale, or one a
    # crash leaves behind before the unlink) then no longer applies.
    generation = (_generation() + 1) & 0xFFFFFFFF
    _write_index(rows, generation=generation)
    _remove_journal()


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
    """Caller holds ``_lock`` and the exclusive results file lock."""
    global _map_pos
    path = discovery_results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    from hedge_fund.trading.handler_timing import io_timer

    with io_timer():
        with path.open("ab") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() and not _ends_with_newline(path, fh.tell()):
                # A crash left a partial line. Close it so this record is
                # its own line on a rescan.
                fh.write(b"\n")
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
                    rows[name] = _retain_row(stored)
                    _publish_seed(name, stored)
            fh.flush()
            if rows is _map:
                _map_pos = (os.fstat(fh.fileno()).st_ino, fh.tell())


def _ends_with_newline(path: Path, size: int) -> bool:
    try:
        with path.open("rb") as fh:
            fh.seek(size - 1)
            return fh.read(1) == b"\n"
    except OSError:
        return True


def _apply_rows(
    rows: dict[str, dict],
    incoming: list,
    *,
    fresh: set[str] | None = None,
    index: dict[str, bool] | None = None,
) -> list[dict]:
    """Merge ``incoming`` into ``rows``. Returns one delta per new jsonl line."""
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
        if prev is not None and _same_core(prev, record) and _same_retest(prev, record):
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
        return []
    described: list[tuple[dict, bool, bool, str]] = []
    for record in pending:
        name = record.get("strategy")
        if not isinstance(name, str) or not name:
            continue
        prev = rows.get(name)
        described.append((
            record,
            prev is None,
            bool(prev.get("qualified")) if isinstance(prev, dict) else False,
            name,
        ))
    _append_records([item[0] for item in described], rows)
    deltas: list[dict] = []
    for record, is_new, prev_qualified, name in described:
        stored = rows.get(name) or record
        deltas.append({
            "offset": int(stored.get("_offset") or 0),
            "length": int(stored.get("_length") or 0),
            "unix": _tested_unix(stored.get("tested_at")),
            "qualified": bool(stored.get("qualified")),
            "new": is_new,
            "prev_qualified": prev_qualified,
            "name_hash": _name_hash(name),
        })
    return deltas


def _journal_bytes(deltas: list[dict]) -> bytes:
    parts = []
    for delta in deltas:
        flags = _FLAG_QUALIFIED if delta["qualified"] else 0
        if delta["new"]:
            flags |= _FLAG_NEW
        elif delta["prev_qualified"]:
            flags |= _FLAG_PREV_QUALIFIED
        parts.append(_JREC.pack(
            int(delta["offset"]),
            int(delta["length"]),
            int(delta["unix"]),
            flags,
            int(delta["name_hash"]),
        ))
    return b"".join(parts)


def _append_journal(deltas: list[dict]) -> None:
    """One batch of fixed-width records. Caller holds the exclusive lock.

    A journal that extends a different generation is replaced. A short
    tail left by a crash is truncated before the new records.
    """
    if not deltas:
        return
    generation = _generation()
    path = discovery_results_journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = _journal_bytes(deltas)
    from hedge_fund.trading.handler_timing import io_timer

    with io_timer():
        try:
            end = path.stat().st_size
        except OSError:
            end = 0
        if end < _JHDR.size:
            path.write_bytes(_JHDR.pack(_JMAGIC, _JVERSION, generation, 0) + blob)
            return
        with path.open("r+b") as fh:
            hdr = fh.read(_JHDR.size)
            magic, version, file_gen, _reserved = _JHDR.unpack(hdr)
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            if magic != _JMAGIC or version != _JVERSION or int(file_gen) != generation:
                fh.seek(0)
                fh.truncate()
                fh.write(_JHDR.pack(_JMAGIC, _JVERSION, generation, 0) + blob)
            else:
                extra = (end - _JHDR.size) % _JREC.size
                if extra:
                    end -= extra
                    fh.truncate(end)
                    fh.seek(end)
                fh.write(blob)
            fh.flush()


def _header_ready() -> bool:
    return _read_header(discovery_results_index_path()) is not None


def sync_discovery_results(rows: list, *, fresh: set[str] | None = None) -> bool:
    """Append ``rows`` that changed. Does not take the discovery lock.

    When a base index already exists, one journal record is appended per
    changed name. The base index is not rewritten. The first publish (no
    index yet) still writes the full table once. Callers that already
    updated the bool index should pass only the new or rewritten evals,
    not the historical log.
    """
    publish = False
    with _lock:
        with _ResultsLock(exclusive=True):
            current = _load_map()
            ready = _header_ready()
            deltas = _apply_rows(current, rows, fresh=fresh)
            if not ready:
                if current:
                    _install_full_index(current)
                    publish = True
            elif deltas:
                _append_journal(deltas)
                _invalidate_counts()
                publish = True
            else:
                publish = False
    if publish and results_compact_due():
        from hedge_fund.trading.discovery import schedule_state_compact

        schedule_state_compact()
    return publish


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
        with _ResultsLock(exclusive=True):
            current = _load_map()
            deltas = _apply_rows(current, log_rows, index=index)
            if deltas or not _index_current(current):
                _install_full_index(current)
    from hedge_fund.trading.mem import release_heap

    release_heap()


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


def _status_total(published: dict, status: str | None) -> int:
    if status == "pass":
        return int(published["tested_pass"])
    if status == "fail":
        return int(published["tested_fail"])
    return int(published["unique"])


def _read_span(fh, file_offset: int, length: int) -> dict | None:
    if length <= 0:
        return None
    fh.seek(int(file_offset))
    try:
        row = json.loads(fh.read(int(length)))
    except (OSError, ValueError):
        return None
    return row if isinstance(row, dict) else None


_NAME_PREFIX = re.compile(rb'^\{\s*"strategy"\s*:\s*("(?:[^"\\]|\\.)*")')


def _span_name(fh, file_offset: int, length: int) -> str | None:
    """Strategy name of one jsonl span, reading only its first bytes.

    Records are written with ``strategy`` as the first key. Falls back to a
    full decode when the prefix does not parse.
    """
    if length <= 0:
        return None
    fh.seek(int(file_offset))
    try:
        head = fh.read(min(int(length), 512))
    except OSError:
        return None
    match = _NAME_PREFIX.match(head)
    if match:
        try:
            name = json.loads(match.group(1))
        except ValueError:
            name = None
        if isinstance(name, str):
            return name or None
    row = _read_span(fh, file_offset, length)
    name = row.get("strategy") if row else None
    return name if isinstance(name, str) and name else None


def _page_base(
    *,
    offset: int,
    limit: int,
    status: str | None,
    total: int,
    published: dict,
) -> tuple[list[dict], int, dict]:
    """Seek one page in the base index. Caller holds the results lock."""
    path = discovery_results_index_path()
    body = discovery_results_path()
    try:
        with path.open("rb") as fh:
            blob = fh.read(_HEADER.size)
            if len(blob) < _HEADER.size:
                return [], total, published
            _magic, _version, count, npass, nfail, _generation = _HEADER.unpack(blob)
            start, section_len = _section_start(status, count, npass, nfail)
            if offset >= section_len:
                return [], total, published
            want = min(limit, section_len - offset)
            fh.seek(start + offset * _REF.size)
            raw_refs = fh.read(want * _REF.size)
    except OSError:
        return [], total, published
    refs = [
        _REF.unpack_from(raw_refs, pos)
        for pos in range(0, len(raw_refs) - _REF.size + 1, _REF.size)
    ]
    records: list[dict] = []
    if not refs or not body.exists():
        return records, total, published
    try:
        with body.open("rb") as fh:
            for file_offset, length, _unix, _flags in refs:
                row = _read_span(fh, file_offset, length)
                if row is not None:
                    records.append(row)
    except OSError:
        return records, total, published
    return records, total, published


def _section_refs(status: str | None) -> list[tuple]:
    path = discovery_results_index_path()
    try:
        with path.open("rb") as fh:
            blob = fh.read(_HEADER.size)
            if len(blob) < _HEADER.size:
                return []
            _magic, _version, count, npass, nfail, _generation = _HEADER.unpack(blob)
            start, section_len = _section_start(status, count, npass, nfail)
            if section_len <= 0:
                return []
            fh.seek(start)
            raw = fh.read(section_len * _REF.size)
    except OSError:
        return []
    return [
        _REF.unpack_from(raw, pos)
        for pos in range(0, len(raw) - _REF.size + 1, _REF.size)
    ]


def _listed_before(unix_a: int, hash_a: int, unix_b: int, hash_b: int) -> bool:
    """True when A is listed ahead of B (newer tested_at, then hash)."""
    a_missing = unix_a <= 0
    b_missing = unix_b <= 0
    if a_missing != b_missing:
        return not a_missing
    if unix_a != unix_b:
        return unix_a > unix_b
    return hash_a <= hash_b


def _journal_in_section(flags: int, status: str | None) -> bool:
    qualified = bool(flags & _FLAG_QUALIFIED)
    if status == "pass":
        return qualified
    if status == "fail":
        return not qualified
    return True


def _page_merged(
    journal: list[tuple],
    *,
    offset: int,
    limit: int,
    status: str | None,
    total: int,
    published: dict,
) -> tuple[list[dict], int, dict]:
    """Newest-first merge of the journal onto the base section.

    The base section stays sorted. Journal rows are the newer writes.
    A name in the journal replaces its base ref. Only the jsonl spans
    that land on the page are decoded, plus base refs that have to be
    identified to skip a replaced name.
    """
    latest: dict[int, tuple] = {}
    for rec in journal:
        latest[int(rec[4])] = rec
    items = [rec for rec in latest.values() if _journal_in_section(int(rec[3]), status)]
    items.sort(key=lambda rec: (int(rec[2]) <= 0, -int(rec[2]), int(rec[4])))
    base_refs = _section_refs(status)
    body = discovery_results_path()
    records: list[dict] = []
    if not body.exists():
        return records, total, published
    try:
        fh = body.open("rb")
    except OSError:
        return records, total, published
    try:
        j_i = 0
        b_i = 0
        resolved: tuple[int, int, dict] | None = None
        emitted = 0
        need = offset + limit

        def resolve_base() -> tuple[int, int, tuple] | None:
            # Reads only the name prefix of each skipped base ref; the full
            # span is decoded only when it lands on the page.
            nonlocal b_i
            while b_i < len(base_refs):
                ref = base_refs[b_i]
                b_i += 1
                name = _span_name(fh, ref[0], ref[1])
                if name is None:
                    continue
                hashed = _name_hash(name)
                if hashed in latest:
                    continue
                return int(ref[2]), hashed, ref
            return None

        while emitted < need:
            jrec = items[j_i] if j_i < len(items) else None
            if resolved is None and b_i < len(base_refs):
                if jrec is not None and _listed_before(
                    int(jrec[2]), int(jrec[4]), int(base_refs[b_i][2]), int(jrec[4]),
                ) and int(jrec[2]) != int(base_refs[b_i][2]):
                    # Strictly newer than the next base ref, so newer than
                    # every later base ref too. The hash tie is unused.
                    resolved = None
                else:
                    resolved = resolve_base()
            if jrec is None and resolved is None and b_i >= len(base_refs):
                break
            take_journal = jrec is not None and (
                resolved is None
                or _listed_before(int(jrec[2]), int(jrec[4]), resolved[0], resolved[1])
            )
            if take_journal and jrec is not None:
                if emitted >= offset:
                    row = _read_span(fh, jrec[0], jrec[1])
                    if row is not None:
                        records.append(row)
                j_i += 1
                emitted += 1
                continue
            if resolved is None:
                break
            if emitted >= offset:
                row = _read_span(fh, resolved[2][0], resolved[2][1])
                if row is not None:
                    records.append(row)
            emitted += 1
            resolved = None
    finally:
        fh.close()
    return records, total, published


def page_result_records(
    *,
    offset: int,
    limit: int,
    status: str | None = None,
) -> tuple[list[dict], int, dict]:
    """Return ``limit`` records starting at ``offset`` without reading the rest.

    The third value is the published counts (zeros when no index exists).
    An empty journal seeks the base index. A journal merges on top of it.
    """
    _reset_if_moved()
    with _ResultsLock(exclusive=False):
        published = _peek_unlocked() or {"unique": 0, "tested_pass": 0, "tested_fail": 0}
        total = _status_total(published, status)
        if limit <= 0 or offset >= total or total <= 0:
            return [], total, published
        journal = _read_journal_records(_generation())
        if not journal:
            return _page_base(
                offset=offset, limit=limit, status=status, total=total, published=published,
            )
        return _page_merged(
            journal,
            offset=offset,
            limit=limit,
            status=status,
            total=total,
            published=published,
        )


def results_compact_due() -> bool:
    """True when the results journal is large enough to fold off the request."""
    try:
        return discovery_results_journal_path().stat().st_size >= RESULTS_JOURNAL_MAX_BYTES
    except OSError:
        return False


def compact_results_index(*, force: bool = False) -> bool:
    """Rewrite ``discovery_results.ix`` from the jsonl and drop the journal.

    Does not take ``paper_state_lock``. Sorting happens outside the file
    lock. If an append lands while the bytes are built, the install is
    skipped and the next append schedules another pass.
    """
    with _lock:
        with _ResultsLock(exclusive=True):
            if not force and not results_compact_due():
                return False
            generation = _generation()
            if not _read_journal_records(generation):
                return False
            rows = _load_map()
            snapshot = {name: dict(row) for name, row in rows.items()}
            ix_stat = _file_stat(discovery_results_index_path())
            j_stat = _file_stat(discovery_results_journal_path())
            next_gen = (generation + 1) & 0xFFFFFFFF
    blob = _index_bytes(snapshot, next_gen)
    with _lock:
        with _ResultsLock(exclusive=True):
            if (
                _file_stat(discovery_results_index_path()) != ix_stat
                or _file_stat(discovery_results_journal_path()) != j_stat
            ):
                return False
            path = discovery_results_index_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, path)
            _remove_journal()
            _invalidate_counts()
            return True


def _retain_row(row: dict) -> dict:
    """Drop window payloads from the in-memory map.

    The jsonl line still has them. Dedup, counts, and the index use the
    slim fields. ``records_by_name`` reads the line back when a caller
    needs the windows. A process that kept every window list grew with
    each ingest and never gave the pages back.
    """
    if "windows" not in row:
        return row
    kept = {key: value for key, value in row.items() if key != "windows"}
    kept["_disk_windows"] = True
    return kept


def _public_row(row: dict, fh) -> dict:
    if row.get("_disk_windows") and fh is not None:
        try:
            fh.seek(int(row.get("_offset") or 0))
            parsed = json.loads(fh.read(int(row.get("_length") or 0)))
        except (OSError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {key: value for key, value in row.items() if not str(key).startswith("_")}


def records_by_name() -> dict[str, dict]:
    """Write-path snapshot. Not for request handlers."""
    with _lock, _ResultsLock(exclusive=False):
        current = _load_map()
        path = discovery_results_path()
        try:
            fh = path.open("rb")
        except OSError:
            fh = None
        try:
            return {name: _public_row(row, fh) for name, row in current.items()}
        finally:
            if fh is not None:
                fh.close()
