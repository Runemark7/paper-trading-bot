"""Discovery log, in-flight cycle batch, and rotating leftover cursor.

Last-known files, not a job runner. Tournament appends one evaluation to
``discovery_log.json`` as soon as that name finishes (newest-first, capped).
Skip / fail-once does **not** read that cap: each append also records the
name in ``discovery_tested.json`` (uncapped qualified flag, slim metrics)
before old display rows are trimmed. The full row is appended to
``discovery_results.jsonl`` after that lock is released. A background
thread backfills older rows and does not take the discovery lock.
A non-qualified eval parks that name forever —
no cooldown retest.
``discovery_in_flight.json`` lists this cycle's budget names and shrinks as
they complete. ``discovery_cursor.json`` remembers where the leftover drain
left off so the next live_cycle continues fairly.
"""
from __future__ import annotations

import json
import sys
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.paths import state_root
from hedge_fund.trading.constants import (
    DISCOVER_CYCLE_MAX_NAMES,
    DISCOVERY_LOG_CAP,
)
from hedge_fund.trading.store import paper_state_lock
from hedge_fund.trading.tested_index import (
    flags_for_skip,
    load_index_and_metrics,
    merge_tested_metrics,
    merge_tested_rows,
    save_tested_index,
)

DISCOVERY_LOG = "discovery_log.json"
# Newest rows land here once the display file is large, so an ingest does
# not rewrite the whole capped log. ``load_discovery_log`` merges it.
DISCOVERY_LOG_DELTA = "discovery_log_delta.jsonl"
DISCOVERY_IN_FLIGHT = "discovery_in_flight.json"
DISCOVERY_CURSOR = "discovery_cursor.json"
# Ingest always appends a JSONL line. Compaction rewrites the base array
# off the request, once the delta is large enough to be worth it.
LOG_REWRITE_MAX_BYTES = 256 * 1024
DELTA_COMPACT_LINES = 256
DELTA_COMPACT_BYTES = 1024 * 1024
_log_cache_guard = threading.Lock()
_log_cache: dict = {"key": None, "rows": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def discovery_log_path() -> Path:
    return state_root() / DISCOVERY_LOG


def discovery_log_delta_path() -> Path:
    return state_root() / DISCOVERY_LOG_DELTA


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


def _file_stat(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path), st.st_mtime_ns, st.st_size, st.st_ino)


def _log_stat_key() -> tuple:
    return (_file_stat(discovery_log_path()), _file_stat(discovery_log_delta_path()))


def discovery_log_fingerprint() -> tuple:
    """Identity of the display log plus its delta. Used to skip a re-merge."""
    return _log_stat_key()


def _read_json_array(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open() as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _read_delta_newest_first(path: Path) -> list[dict]:
    """Delta file order is oldest-first. The returned list is newest-first.

    A crash mid-append leaves a partial last line with no newline. That
    line is ignored. A bad earlier line is skipped; the rest still load.
    """
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with path.open() as fh:
            for line in fh:
                if not line.endswith("\n"):
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    rows.reverse()
    return rows


def _row_key(row: dict) -> tuple | None:
    name = row.get("strategy")
    tested = row.get("tested_at")
    if isinstance(name, str) and name and isinstance(tested, str) and tested:
        return (name, tested)
    return None


def _read_discovery_log() -> list[dict]:
    """Base array plus delta. Newest first. Same row in both is kept once.

    When a ``(strategy, tested_at)`` row is in both, the base copy wins: a
    base is only ever written from a merged view (compaction, or a bulk
    edit such as requalify), so it is the newer version of that row. A
    crash after the base install and before the delta drop then cannot
    revert a bulk edit. Rows without a ``tested_at`` are never collapsed.
    """
    base = _read_json_array(discovery_log_path())
    extra = _read_delta_newest_first(discovery_log_delta_path())
    if not extra:
        return base
    in_base = {key for key in map(_row_key, base) if key is not None}
    seen: set[tuple] = set()
    fresh: list[dict] = []
    for row in extra:
        key = _row_key(row)
        if key is not None:
            if key in in_base or key in seen:
                continue
            seen.add(key)
        fresh.append(row)
    return (fresh + base)[:DISCOVERY_LOG_CAP]


def _remember_log(rows: list[dict]) -> None:
    with _log_cache_guard:
        _log_cache["key"] = _log_stat_key()
        _log_cache["rows"] = rows


def load_discovery_log() -> list[dict]:
    """Newest-first display log, including rows not yet folded into the array.

    The parsed list is cached for this process. A new ``PAPER_STATE`` or a
    rewritten file misses the cache. Callers get a new list, sharing the
    row dicts.
    """
    key = _log_stat_key()
    with _log_cache_guard:
        if _log_cache["key"] == key and isinstance(_log_cache["rows"], list):
            return list(_log_cache["rows"])
    rows = _read_discovery_log()
    with _log_cache_guard:
        if _log_stat_key() == key:
            _log_cache["key"] = key
            _log_cache["rows"] = rows
    return list(rows)


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


def _write_log_array(path: Path, log: list[dict]) -> None:
    from hedge_fund.trading.handler_timing import io_timer

    path.parent.mkdir(parents=True, exist_ok=True)
    with io_timer():
        blob = json.dumps(log, separators=(",", ":"))
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(blob)
        os.replace(tmp, path)


def _write_log_blob(path: Path, blob: str) -> None:
    from hedge_fund.trading.handler_timing import io_timer

    path.parent.mkdir(parents=True, exist_ok=True)
    with io_timer():
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(blob)
        os.replace(tmp, path)


def _append_delta(records: list[dict]) -> None:
    """Append oldest-first so the last line is the newest record.

    Does not re-read the file. A crash mid-line leaves no trailing newline
    and readers ignore that partial line.
    """
    path = discovery_log_delta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    partial = False
    if size:
        try:
            with path.open("rb") as raw:
                raw.seek(size - 1)
                partial = raw.read(1) != b"\n"
        except OSError:
            partial = False
    with path.open("a") as fh:
        if partial:
            # A crash left a partial line. Close it so the next row parses.
            fh.write("\n")
        for row in reversed(records):
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        fh.flush()


def _clear_delta() -> None:
    path = discovery_log_delta_path()
    try:
        path.unlink()
    except OSError:
        pass


def _append_batch_locked(eval_records: list[dict], cap: int) -> list[dict]:
    """Append log lines and tested-index journal lines. Caller holds the lock.

    Neither base file is rewritten. The in-memory log is the previous
    cached rows with these records in front.
    """
    from hedge_fund.trading.tested_index import (
        _metric_from_row,
        append_tested_overlay,
        mark_index_covers_log,
    )

    for row in eval_records:
        if not isinstance(row, dict):
            continue
        name = row.get("strategy")
        if not isinstance(name, str) or not name:
            continue
        append_tested_overlay(name, bool(row.get("qualified")), _metric_from_row(row))
    # Snapshot before the delta write. A read after the write misses the
    # cache and would prepend these rows onto themselves.
    prior = load_discovery_log()
    _append_delta(list(eval_records))
    log = (list(eval_records) + prior)[:cap]
    _remember_log(log)
    mark_index_covers_log()
    schedule_state_compact()
    return log


def append_discovery_evaluations(
    eval_records: list[dict],
    *,
    cap: int = DISCOVERY_LOG_CAP,
    after_commit=None,
    still_valid=None,
) -> list[dict] | None:
    """Append records (newest-first). Does not rewrite the base log or index.

    The tested-name journal gets one line per name. The display delta gets
    one JSONL line per row. Readers merge base + appends. A partial last
    line is ignored. ``still_valid`` is checked under the lock before the
    write so a retried ingest does not append a second row. ``after_commit``
    runs in that same lock, before it is released.

    Full records for this batch are appended to the results log after the
    lock is released. Compaction of a large delta or journal runs on a
    daemon thread, not on this request.
    """
    if not eval_records:
        return load_discovery_log()

    def _write() -> list[dict] | None:
        if still_valid is not None and not still_valid():
            return None
        snap = _cache_snapshot()
        saved = _append_batch_locked(eval_records, cap)
        if after_commit is not None:
            after_commit()
        _note_written(eval_records, snap)
        return saved

    with paper_state_lock("discovery"):
        saved = _write()
    if saved is None:
        return None
    from hedge_fund.trading.discovery_results import sync_discovery_results

    fresh = {
        row.get("strategy")
        for row in eval_records
        if isinstance(row, dict) and isinstance(row.get("strategy"), str)
    }
    sync_discovery_results(eval_records, fresh=fresh)
    return saved


def append_discovery_evaluation(
    record: dict,
    *,
    cap: int = DISCOVERY_LOG_CAP,
    after_commit=None,
    still_valid=None,
) -> list[dict] | None:
    """Append one finished evaluation now. Crash-safe mid-batch progress."""
    return append_discovery_evaluations(
        [record], cap=cap, after_commit=after_commit, still_valid=still_valid
    )


_compact_guard = threading.Lock()
_compact_scheduled = False


def _log_compact_due() -> bool:
    try:
        return discovery_log_delta_path().stat().st_size >= LOG_REWRITE_MAX_BYTES
    except OSError:
        return False


def _index_compact_due() -> bool:
    from hedge_fund.trading.tested_index import tested_journal_path

    try:
        return tested_journal_path().stat().st_size >= LOG_REWRITE_MAX_BYTES
    except OSError:
        return False


def schedule_state_compact() -> None:
    """Fold a large delta or journal into its base file off the request."""
    global _compact_scheduled
    if not _log_compact_due() and not _index_compact_due():
        return
    with _compact_guard:
        if _compact_scheduled:
            return
        _compact_scheduled = True
    threading.Thread(target=_compact_worker, name="discovery-compact", daemon=True).start()


def _compact_worker() -> None:
    global _compact_scheduled
    try:
        compact_discovery_state()
    except Exception as exc:  # noqa: BLE001 - a failed fold retries on the next append
        print(f"discovery compact failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    finally:
        with _compact_guard:
            _compact_scheduled = False
    # No immediate retry: if an append raced the encode, the next append
    # schedules another pass. Retrying here would spin re-reading the files.


def compact_discovery_state() -> None:
    """Rewrite base files from base+appends, then drop the journals.

    Holds the lock only to snapshot and to install. Encoding happens
    outside it. If an append lands in between, the install is skipped.
    A crash after the base rename and before the journal unlink is safe:
    the log reader drops a delta row already in the base, and the tested
    journal carries the base's ``jgen`` so a stale journal is ignored.
    """
    _compact_log()
    _compact_tested_index()


def _compact_log() -> None:
    if not _log_compact_due():
        return
    with paper_state_lock("discovery"):
        delta_fp = _file_stat(discovery_log_delta_path())
        base_fp = _file_stat(discovery_log_path())
        rows = _read_discovery_log()
    blob = json.dumps(rows, separators=(",", ":"))
    with paper_state_lock("discovery"):
        if (
            _file_stat(discovery_log_delta_path()) != delta_fp
            or _file_stat(discovery_log_path()) != base_fp
        ):
            return
        _write_log_blob(discovery_log_path(), blob)
        _clear_delta()
        _remember_log(rows)


def _compact_tested_index() -> None:
    if not _index_compact_due():
        return
    from hedge_fund.trading.tested_index import (
        _derived_now,
        tested_index_document,
        tested_index_fingerprint,
        tested_journal_path,
        write_tested_document,
    )

    with paper_state_lock("discovery"):
        # Includes the base generation and journal header, not just stat.
        fp = tested_index_fingerprint()
        derived = _derived_now()
        flags = dict(derived["flags"])
        metrics = dict(derived["metrics"])
    payload = tested_index_document(
        flags,
        {name: metrics[name] for name in sorted(metrics)},
    )
    with paper_state_lock("discovery"):
        if tested_index_fingerprint() != fp:
            return
        write_tested_document(payload)
        try:
            tested_journal_path().unlink()
        except OSError:
            pass
        from hedge_fund.trading.tested_index import invalidate_tested_derived

        invalidate_tested_derived()


def save_discovery_log(log: list[dict]) -> None:
    """Rewrite discovery_log.json in place (requalify / force-admit flips).

    Qualified flags follow the saved rows. Full records for those names
    are appended when the eval changed, after the discovery lock is
    released. Names that are only in the index stay for the backfill.
    """
    with paper_state_lock("discovery"):
        path = discovery_log_path()
        _write_log_array(path, log)
        _clear_delta()
        _remember_log(list(log))
        index, metrics = load_index_and_metrics()
        flags_changed = merge_tested_rows(index, log)
        metrics_changed = merge_tested_metrics(metrics, log)
        if flags_changed or metrics_changed:
            save_tested_index(index, metrics, cleaned=True)
        from hedge_fund.trading.tested_index import mark_index_covers_log

        mark_index_covers_log()
    from hedge_fund.trading.discovery_results import sync_discovery_results

    sync_discovery_results(log)


def write_in_flight(
    names: list[str],
    *,
    current: str | None = None,
    remaining: list[str] | None = None,
    completed: list[str] | None = None,
    batch_size: int | None = None,
    started_at: str | None = None,
    source: str | None = None,
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
        "source": source or "tournament_engine.py",
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


def failed_discovery_names(log: list[dict] | None = None) -> set[str]:
    """Names with a non-qualified evaluation.

    Fail once: these names are permanently ineligible for another
    evaluate_windows / discovery run. The durable tested-name index is
    the source of truth, not the capped display log. Any non-qualified
    row still in ``log`` counts too (a later pass does not erase the
    earlier fail for this set). Skip itself uses the tested-name union.
    """
    flags = flags_for_skip(log)
    failed = {name for name, qual in flags.items() if not qual}
    rows = log if log is not None else load_discovery_log()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("strategy")
        if name and not row.get("qualified"):
            failed.add(name)
    return failed


# (log fingerprint, index fingerprint) -> names already evaluated.
_tested_names_cache: dict = {"fp": None, "names": None}


def cached_tested_names() -> set[str]:
    """Index ∪ display log. Do not mutate. Rebuilt when either file changes.

    ``ensure_tested_index`` still folds a log that was written without the
    index, which is what a direct log write migrates on read.
    """
    from hedge_fund.paths import state_root
    from hedge_fund.trading.tested_index import (
        ensure_tested_index,
        tested_index_fingerprint,
        tested_name_set,
    )

    fp = _tested_cache_fp()
    names = _tested_names_cache.get("names")
    if _tested_names_cache.get("fp") == fp and isinstance(names, set):
        return names
    # A log written without the index is folded in here, then the set is cached.
    ensure_tested_index()
    fp = (str(state_root()), discovery_log_fingerprint(), tested_index_fingerprint())
    names = _tested_names_cache.get("names")
    if _tested_names_cache.get("fp") == fp and isinstance(names, set):
        return names
    found = set(tested_name_set())
    for row in load_discovery_log():
        if not isinstance(row, dict):
            continue
        name = row.get("strategy")
        if isinstance(name, str) and name:
            found.add(name)
    _tested_names_cache["fp"] = fp
    _tested_names_cache["names"] = found
    return found


def _tested_cache_fp() -> tuple:
    from hedge_fund.paths import state_root
    from hedge_fund.trading.tested_index import tested_index_fingerprint

    return (str(state_root()), discovery_log_fingerprint(), tested_index_fingerprint())


def _cache_snapshot() -> tuple:
    """Fingerprints just before an append. Caller holds the discovery lock."""
    from hedge_fund.trading.leases import queue_snapshot

    return (_tested_cache_fp(), queue_snapshot())


def _note_written(eval_records: list, snap: tuple) -> None:
    """Update the claim caches in place after an append, under the same lock."""
    names = [
        row.get("strategy")
        for row in eval_records
        if isinstance(row, dict) and isinstance(row.get("strategy"), str) and row.get("strategy")
    ]
    remember_tested_names(names, before=snap[0])
    from hedge_fund.trading.leases import note_queue_tested

    note_queue_tested(names, before=snap[1])


def remember_tested_names(names, before: tuple | None) -> None:
    """Names were just written. Keep an already-built set in sync.

    Caller holds ``paper_state_lock('discovery')`` and passes the
    fingerprint taken under that lock before the write. The set is only
    updated in place when it was built from exactly that state; anything
    else (another process, rescore, requeue) drops it so the next read
    rebuilds from disk. A cold cache is left cold.
    """
    names = [n for n in names if isinstance(n, str) and n]
    cached = _tested_names_cache.get("names")
    old = _tested_names_cache.get("fp")
    if not isinstance(cached, set) or not isinstance(old, tuple):
        return
    if before is None or old != before:
        _tested_names_cache["fp"] = None
        _tested_names_cache["names"] = None
        return
    cached.update(names)
    _tested_names_cache["fp"] = _tested_cache_fp()


def tested_discovery_names(log: list[dict] | None = None) -> set[str]:
    """Strategy names already evaluated (any outcome).

    Uncapped tested-name index, union any names in ``log``. Trimming
    ``discovery_log.json`` does not make a name eligible again.
    A copy, so the caller can add a name it is about to append.
    """
    if log is None:
        return set(cached_tested_names())
    return set(flags_for_skip(log))


def prioritize_leftovers(
    leftovers: list[str],
    log: list[dict] | None = None,
    *,
    now: datetime | None = None,
    cooldown_seconds: int | None = None,
) -> list[str]:
    """Never-tested leftovers only (stable leftover order).

    A name in the tested-name index (or still in the display log) is parked
    forever. Already-tested names are not re-queued. ``cooldown_seconds`` is ignored:
    fails are not re-eligible after a timer. ``now`` is unused (call-site
    compat). This is not a random sample.
    """
    _ = (now, cooldown_seconds)
    if log is None:
        log = load_discovery_log()
    blocked = tested_discovery_names(log) | failed_discovery_names(log)
    return [name for name in leftovers if name not in blocked]


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
    cooldown_seconds: int | None = None,
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
