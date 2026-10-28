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
# Below this, append still rewrites the JSON array (tests and a small log).
# Above it, append is one jsonl line. Compact folds the delta back in.
LOG_REWRITE_MAX_BYTES = 256 * 1024
DELTA_COMPACT_LINES = 32
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
    return (str(path), st.st_mtime_ns, st.st_size)


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
    """Delta file order is oldest-first. The returned list is newest-first."""
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with path.open() as fh:
            for line in fh:
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


def _read_discovery_log() -> list[dict]:
    base = _read_json_array(discovery_log_path())
    extra = _read_delta_newest_first(discovery_log_delta_path())
    if not extra:
        return base
    return (extra + base)[:DISCOVERY_LOG_CAP]


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


def _append_delta(records: list[dict]) -> int:
    """Append oldest-first so the last line is the newest record. Returns the line count."""
    path = discovery_log_delta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for row in reversed(records):
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        fh.flush()
    try:
        with path.open() as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return len(records)


def _clear_delta() -> None:
    path = discovery_log_delta_path()
    try:
        path.unlink()
    except OSError:
        pass


def _commit_append_locked(
    eval_records: list[dict],
    *,
    cap: int,
    index_blob: str | None = None,
    index_payload: dict | None = None,
    log_blob: str | None = None,
    prepared: dict | None = None,
) -> list[dict]:
    """Write one append. Caller holds ``paper_state_lock``.

    When ``prepared`` is set, the JSON was encoded outside the lock and
    the fingerprints must still match. Otherwise this reads and encodes
    under the lock (the nested-lock path).
    """
    from hedge_fund.trading.tested_index import (
        commit_tested_blob,
        mark_index_covers_log,
        save_tested_index,
    )

    path = discovery_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if prepared is None:
        index, metrics = load_index_and_metrics()
        flags_changed = merge_tested_rows(index, eval_records)
        metrics_changed = merge_tested_metrics(metrics, eval_records)
        if flags_changed or metrics_changed:
            save_tested_index(index, metrics, cleaned=True)
        base_size = path.stat().st_size if path.exists() else 0
        delta = discovery_log_delta_path()
        delta_exists = delta.exists() and delta.stat().st_size > 0
        prior = load_discovery_log()
        log = (list(eval_records) + prior)[:cap]
        if base_size < LOG_REWRITE_MAX_BYTES and not delta_exists:
            _write_log_array(path, log)
            _clear_delta()
        else:
            n_lines = _append_delta(list(eval_records))
            if n_lines >= DELTA_COMPACT_LINES:
                # Drop the delta before the array replace so a crash cannot
                # load those rows twice. A crash in between loses only the
                # rows still in memory; the index already has the names.
                _clear_delta()
                _write_log_array(path, log)
    else:
        if index_blob is not None and index_payload is not None:
            commit_tested_blob(index_blob, index_payload)
        log = prepared["log"]
        if prepared["rewrite"]:
            _write_log_blob(path, log_blob or "")
            _clear_delta()
        else:
            n_lines = _append_delta(list(eval_records))
            if n_lines >= DELTA_COMPACT_LINES:
                _clear_delta()
                if log_blob is None:
                    _write_log_array(path, log)
                else:
                    _write_log_blob(path, log_blob)
    _remember_log(log)
    mark_index_covers_log()
    return log


def _prepare_append(eval_records: list[dict], cap: int) -> dict:
    """Snapshot the append under the lock. Encode happens after release."""
    from hedge_fund.trading.tested_index import tested_index_document, tested_index_fingerprint

    index, metrics = load_index_and_metrics()
    index = dict(index)
    metrics = dict(metrics)
    flags_changed = merge_tested_rows(index, eval_records)
    metrics_changed = merge_tested_metrics(metrics, eval_records)
    path = discovery_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    base_size = path.stat().st_size if path.exists() else 0
    delta = discovery_log_delta_path()
    delta_exists = delta.exists() and delta.stat().st_size > 0
    prior = load_discovery_log()
    rewrite = base_size < LOG_REWRITE_MAX_BYTES and not delta_exists
    log = (list(eval_records) + prior)[:cap]
    existing_lines = 0
    if not rewrite and delta_exists:
        try:
            with delta.open() as fh:
                existing_lines = sum(1 for line in fh if line.strip())
        except OSError:
            existing_lines = 0
    will_compact = (not rewrite) and (
        existing_lines + len(eval_records) >= DELTA_COMPACT_LINES
    )
    payload = None
    if flags_changed or metrics_changed:
        cleaned = {
            name: metrics[name]
            for name in sorted(metrics)
            if isinstance(name, str) and name
        }
        payload = tested_index_document(index, cleaned)
    return {
        "index_fp": tested_index_fingerprint(),
        "log_fp": discovery_log_fingerprint(),
        "rewrite": rewrite,
        "will_compact": will_compact,
        "log": log,
        "payload": payload,
    }


def append_discovery_evaluations(
    eval_records: list[dict],
    *,
    cap: int = DISCOVERY_LOG_CAP,
    after_commit=None,
    still_valid=None,
) -> list[dict]:
    """Prepend records (newest-first) and cap the display log.

    The tested-name index is updated from these records only. A name that
    falls off the display cap stays in the index. Full records for this
    batch are appended to the results log after the lock is released.

    A small display file is rewritten in place. Once it is past
    ``LOG_REWRITE_MAX_BYTES``, further rows are one jsonl line each and
    folded back into the array every ``DELTA_COMPACT_LINES`` appends.

    When this thread does not already hold the paper-state lock, the
    JSON encode runs outside the lock. The lock is taken again only to
    write the bytes, and only if neither file changed. ``after_commit``
    runs inside that write, before the lock is released, so a retest can
    leave the pending set in the same critical section as the index write.

    ``still_valid`` (optional) is re-checked under the lock before the
    snapshot and before a locked fallback write. When it returns False the
    rows are not appended (a concurrent ingest already wrote this name),
    and ``None`` is returned. Every append changes the display-log
    fingerprint, so a duplicate landing between the snapshot and the
    write forces a retry that re-runs the check.
    """
    if not eval_records:
        return load_discovery_log()
    from hedge_fund.trading.store import paper_lock_held
    from hedge_fund.trading.tested_index import tested_index_fingerprint

    if paper_lock_held():
        with paper_state_lock("discovery"):
            if still_valid is not None and not still_valid():
                return None
            saved = _commit_append_locked(eval_records, cap=cap)
            if after_commit is not None:
                after_commit()
    else:
        saved = None
        for _attempt in range(8):
            with paper_state_lock("discovery"):
                if still_valid is not None and not still_valid():
                    return None
                prepared = _prepare_append(eval_records, cap)
            from hedge_fund.trading.handler_timing import io_timer

            with io_timer():
                index_blob = (
                    json.dumps(prepared["payload"], separators=(",", ":"))
                    if prepared["payload"] is not None
                    else None
                )
                log_blob = (
                    json.dumps(prepared["log"], separators=(",", ":"))
                    if prepared["rewrite"] or prepared["will_compact"]
                    else None
                )
            with paper_state_lock("discovery"):
                if (
                    tested_index_fingerprint() != prepared["index_fp"]
                    or discovery_log_fingerprint() != prepared["log_fp"]
                ):
                    continue
                saved = _commit_append_locked(
                    eval_records,
                    cap=cap,
                    index_blob=index_blob,
                    index_payload=prepared["payload"],
                    log_blob=log_blob,
                    prepared=prepared,
                )
                if after_commit is not None:
                    after_commit()
                break
        if saved is None:
            with paper_state_lock("discovery"):
                if still_valid is not None and not still_valid():
                    return None
                saved = _commit_append_locked(eval_records, cap=cap)
                if after_commit is not None:
                    after_commit()
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


def tested_discovery_names(log: list[dict] | None = None) -> set[str]:
    """Strategy names already evaluated (any outcome).

    Uncapped tested-name index, union any names in ``log``. Trimming
    ``discovery_log.json`` does not make a name eligible again.
    """
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
