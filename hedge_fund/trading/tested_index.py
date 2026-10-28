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
import threading
import uuid
from pathlib import Path
from typing import TypedDict

from hedge_fund.paths import state_root
from hedge_fund.trading.store import paper_state_lock

DISCOVERY_TESTED_INDEX = "discovery_tested.json"
# One JSON object per line. Ingest appends here instead of rewriting the base.
# A partial last line (crash mid-write) is ignored. Readers apply it onto the
# base document. Compaction folds it back off the request path.
DISCOVERY_TESTED_JOURNAL = "discovery_tested.jsonl"
# A handful of overlay lines stay a journal. A bulk replace rewrites the base.
_JOURNAL_REPLACE_MIN = 64
# Every base write gets a new generation id, serialized as the first key so
# it can be read from the first bytes. The journal's first line names the
# generation it extends; a journal for another generation is ignored. A
# crash between a base install and the journal unlink then cannot re-apply
# stale lines over a bulk edit. mtime+size alone is not unique (coarse
# mtime ticks), so the generation is also part of the cache fingerprint.
_GEN_KEY = "jgen"
_GEN_PREFIX = b'{"jgen":"'


def tested_index_path() -> Path:
    return state_root() / DISCOVERY_TESTED_INDEX


def tested_journal_path() -> Path:
    return state_root() / DISCOVERY_TESTED_JOURNAL


def _file_stat(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path), st.st_mtime_ns, st.st_size, st.st_ino)


_doc_guard = threading.Lock()
_doc_cache: dict = {"key": None, "data": None}
# Flags, cleaned metrics, and the name set for the cached document.
# Rebuilt when the file fingerprint changes, not on every claim.
_derived: dict = {"key": None, "flags": None, "metrics": None, "names": None}
# (log fingerprint, index fingerprint) already merged into the index.
_covers: tuple | None = None


def tested_index_fingerprint() -> tuple:
    """State dir, base file, and journal. Another process's append changes it.

    A missing file is a ``None`` slot. The directory is always included so
    two empty temp dirs do not share one cached view.
    """
    from hedge_fund.paths import state_root

    return (
        str(state_root()),
        _base_key(),
        _journal_key(),
    )


def _peek_base_gen() -> str | None:
    """Generation id from the base file's first bytes. Legacy base → None."""
    try:
        with tested_index_path().open("rb") as fh:
            head = fh.read(64)
    except OSError:
        return None
    if not head.startswith(_GEN_PREFIX):
        return None
    end = head.find(b'"', len(_GEN_PREFIX))
    if end < 0:
        return None
    return head[len(_GEN_PREFIX):end].decode("ascii", "replace")


def _base_key() -> tuple | None:
    st = _file_stat(tested_index_path())
    return None if st is None else st + (_peek_base_gen(),)


def _journal_key() -> tuple | None:
    st = _file_stat(tested_journal_path())
    return None if st is None else st + _journal_gen()


def _base_gen(data: dict | None) -> str | None:
    gen = data.get(_GEN_KEY) if isinstance(data, dict) else None
    return gen if isinstance(gen, str) else None


def _is_header(row: object) -> bool:
    return isinstance(row, dict) and _GEN_KEY in row and "name" not in row


def _journal_gen() -> tuple[bool, str | None]:
    """``(has_header, gen)`` from the journal's first complete line."""
    try:
        with tested_journal_path().open() as fh:
            first = fh.readline()
    except OSError:
        return False, None
    if not first.endswith("\n"):
        return False, None
    try:
        row = json.loads(first)
    except ValueError:
        return False, None
    if _is_header(row):
        gen = row.get(_GEN_KEY)
        return True, gen if isinstance(gen, str) else None
    return False, None


def _read_document() -> dict | None:
    """Base document only. The journal is applied in ``_derived_now``."""
    path = tested_index_path()
    key = _base_key()
    with _doc_guard:
        if _doc_cache["key"] == key and isinstance(_doc_cache["data"], dict):
            return _doc_cache["data"]
    if not path.exists():
        with _doc_guard:
            _doc_cache["key"] = key
            _doc_cache["data"] = {}
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    with _doc_guard:
        if _base_key() == key and _base_gen(data) == (key[-1] if key else None):
            _doc_cache["key"] = key
            _doc_cache["data"] = data
    return data


def _read_journal_lines(base_gen: str | None = None) -> list[dict]:
    """Journal overlays, oldest first. A partial last line is ignored.

    Lines are returned only when the journal extends ``base_gen``. A
    journal without a header line extends a base without a generation.
    """
    path = tested_journal_path()
    if not path.exists():
        return []
    rows: list[dict] = []
    journal_gen: str | None = None
    first = True
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
                    first = False
                    continue
                if first and _is_header(row):
                    gen = row.get(_GEN_KEY)
                    journal_gen = gen if isinstance(gen, str) else None
                    first = False
                    continue
                first = False
                if isinstance(row, dict) and isinstance(row.get("name"), str) and row.get("name"):
                    rows.append(row)
    except OSError:
        return []
    if journal_gen != base_gen:
        return []
    return rows


def _apply_journal_line(flags: dict, metrics: dict, line: dict) -> None:
    name = line.get("name")
    if not isinstance(name, str) or not name:
        return
    if "q" in line:
        flags[name] = bool(line.get("q"))
    item: dict = {}
    if "sharpe" in line and isinstance(line.get("sharpe"), (int, float)) and not isinstance(line.get("sharpe"), bool):
        item["sharpe"] = float(line["sharpe"])
    if "trades" in line and isinstance(line.get("trades"), (int, float)) and not isinstance(line.get("trades"), bool):
        item["trades"] = int(line["trades"])
    if line.get("ops_park"):
        item["ops_park"] = True
    if item:
        metrics[name] = item
    elif line.get("m0"):
        metrics.pop(name, None)


def _journal_line(name: str, qualified: bool, metric: dict | None, *, drop_metric: bool = False) -> dict:
    line: dict = {"name": name, "q": 1 if qualified else 0}
    if drop_metric and not metric:
        # Explicit removal; without it a removed metric would reappear on replay.
        line["m0"] = 1
    if isinstance(metric, dict):
        if "sharpe" in metric:
            line["sharpe"] = metric["sharpe"]
        if "trades" in metric:
            line["trades"] = metric["trades"]
        if metric.get("ops_park"):
            line["ops_park"] = True
    return line


def _flags_from_document(data: dict | None) -> dict[str, bool]:
    raw = data.get("names") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, bool] = {}
    for name, flag in raw.items():
        if isinstance(name, str) and name:
            out[name] = bool(flag)
    return out


def _metrics_from_document(data: dict | None) -> dict[str, TestedMetric]:
    raw = data.get("metrics") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    return _clean_metrics(raw)


def invalidate_tested_derived() -> None:
    """Drop the merged view so the next read rebuilds from base + journal."""
    _derived["key"] = None


def _store_derived(key, flags: dict[str, bool], metrics: dict) -> None:
    _derived["key"] = key
    _derived["flags"] = flags
    _derived["metrics"] = metrics
    _derived["names"] = set(flags)


def _derived_now() -> dict:
    """Base document plus journal. Rebuilt when either file's fingerprint changes."""
    key = tested_index_fingerprint()
    if (
        _derived.get("key") == key
        and isinstance(_derived.get("flags"), dict)
        and isinstance(_derived.get("metrics"), dict)
    ):
        return _derived
    data = _read_document() or {}
    flags = _flags_from_document(data)
    metrics = _metrics_from_document(data)
    for line in _read_journal_lines(_base_gen(data)):
        _apply_journal_line(flags, metrics, line)
    _store_derived(key, flags, metrics)
    return _derived


def append_tested_overlay(name: str, qualified: bool, metric: dict | None) -> None:
    """One journal line. Does not rewrite ``discovery_tested.json``.

    Caller holds ``paper_state_lock('discovery')``. A later line for the
    same name wins. Re-applying an identical line is a no-op.
    """
    if not isinstance(name, str) or not name:
        return
    current = _derived_now()
    # No metric on the row keeps the stored one (same as ``merge_tested_metrics``).
    if current["flags"].get(name) == bool(qualified) and (
        not metric or current["metrics"].get(name) == metric
    ):
        return
    _append_journal_lines([_journal_line(name, qualified, metric)])


def _append_journal_lines(lines: list[dict]) -> None:
    """Caller holds ``paper_state_lock('discovery')``."""
    if not lines:
        return
    from hedge_fund.trading.handler_timing import io_timer

    path = tested_journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Taken before the write. The merged view is patched in place only when
    # it was built from exactly this state; another process's lines in
    # between change the journal key and force a rebuild instead.
    before = tested_index_fingerprint()
    base_gen = _peek_base_gen()
    body = "".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines)
    header = json.dumps({_GEN_KEY: base_gen}, separators=(",", ":")) + "\n"
    with io_timer():
        journal = before[2]
        size = journal[2] if journal is not None else 0
        has_header, journal_gen = (journal[-2], journal[-1]) if journal is not None else (False, None)
        if size and (journal_gen != base_gen or (base_gen is not None and not has_header)):
            # Journal extends an older base (crash before its unlink). Readers
            # already ignore it and the base holds its lines; start fresh.
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp.write_text(header + body)
            os.replace(tmp, path)
        else:
            with path.open("a") as fh:
                if not size:
                    fh.write(header)
                elif not _ends_with_newline(path, size):
                    # A crash left a partial line. Close it so this line parses.
                    fh.write("\n")
                fh.write(body)
                fh.flush()
    if (
        _derived.get("key") == before
        and isinstance(_derived.get("flags"), dict)
        and isinstance(_derived.get("metrics"), dict)
    ):
        for line in lines:
            _apply_journal_line(_derived["flags"], _derived["metrics"], line)
        _derived["names"] = set(_derived["flags"])
        _derived["key"] = tested_index_fingerprint()
    else:
        _derived["key"] = None
    from hedge_fund.trading.discovery import schedule_state_compact

    schedule_state_compact()


def _ends_with_newline(path: Path, size: int) -> bool:
    try:
        with path.open("rb") as fh:
            fh.seek(size - 1)
            return fh.read(1) == b"\n"
    except OSError:
        return True


def tested_name_set() -> set[str]:
    """Names in the tested-name index. Do not mutate the returned set."""
    names = _derived_now().get("names")
    return names if isinstance(names, set) else set()


def load_tested_index() -> dict[str, bool]:
    """Read the bool ``names`` map. Missing or corrupt → empty. Does not migrate.

    A copy, so a caller can overlay a log without changing the cache.
    """
    return dict(_derived_now()["flags"])


def load_index_and_metrics() -> tuple[dict[str, bool], dict[str, TestedMetric]]:
    """One read of the file: fail-once flags and the slim metrics map.

    Copies. The cleaned metrics are cached with the file fingerprint, so a
    claim does not re-validate every stored sharpe.
    """
    derived = _derived_now()
    return dict(derived["flags"]), dict(derived["metrics"])


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
    return dict(_derived_now()["metrics"])


def save_tested_index(
    index: dict[str, bool],
    metrics: dict | None = None,
    *,
    cleaned: bool = False,
) -> None:
    """Persist ``index``. Caller holds ``paper_state_lock('discovery')``.

    A small overlay is appended to the journal. A different set of names,
    or a large batch, rewrites the base document and drops the journal so
    the on-disk shape stays the original ``{names, metrics}`` object.
    ``metrics is None`` keeps whatever metrics are already stored.
    ``cleaned=True`` skips re-validating every stored metric.
    """
    if metrics is None:
        metrics = load_tested_metrics()
        cleaned = True
    elif not cleaned:
        metrics = _clean_metrics(metrics)
    else:
        metrics = {name: metrics[name] for name in sorted(metrics) if isinstance(name, str) and name}
    current = _derived_now()
    flags = current["flags"]
    cur_metrics = current["metrics"]
    if set(index) != set(flags):
        _replace_tested_base(index, metrics)
        return
    lines = []
    for name, qual in index.items():
        metric = metrics.get(name) if isinstance(metrics, dict) else None
        if flags.get(name) == bool(qual) and cur_metrics.get(name) == metric:
            continue
        lines.append(_journal_line(
            name,
            bool(qual),
            metric if isinstance(metric, dict) else None,
            drop_metric=name in cur_metrics,
        ))
    if not lines:
        return
    if len(lines) > _JOURNAL_REPLACE_MIN:
        _replace_tested_base(index, metrics)
        return
    _append_journal_lines(lines)


def _replace_tested_base(index: dict[str, bool], metrics: dict) -> None:
    """Full base rewrite. Drops the journal so a crash cannot apply it twice."""
    payload = tested_index_document(index, metrics)
    write_tested_document(payload)
    path = tested_journal_path()
    try:
        path.unlink()
    except OSError:
        pass
    _derived["key"] = tested_index_fingerprint()


def tested_index_document(index: dict[str, bool], metrics: dict) -> dict:
    """JSON-ready index. ``metrics`` is already cleaned and key-sorted."""
    return {
        "v": 1,
        "paper_only": True,
        "names": {name: 1 if qual else 0 for name, qual in sorted(index.items())},
        "metrics": metrics,
    }


def write_tested_document(payload: dict) -> None:
    """Atomic replace of ``discovery_tested.json``. Caller holds the lock.

    The encode is timed as IO. Callers that can drop the lock should
    encode with ``encode_tested_document`` and ``commit_tested_blob``.
    """
    from hedge_fund.trading.handler_timing import io_timer

    path = tested_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # New generation, first key. Any journal on disk now extends an older
    # base and is ignored until it is dropped or restarted.
    payload = {_GEN_KEY: uuid.uuid4().hex, **{k: v for k, v in payload.items() if k != _GEN_KEY}}
    with io_timer():
        blob = json.dumps(payload, separators=(",", ":"))
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(blob)
        os.replace(tmp, path)
    _remember_payload(payload)


def _remember_payload(payload: dict) -> None:
    """Cache the document we just wrote, including cleaned flags and metrics."""
    flags = _flags_from_document(payload)
    raw = payload.get("metrics") if isinstance(payload, dict) else None
    metrics = raw if isinstance(raw, dict) else {}
    with _doc_guard:
        _doc_cache["key"] = _base_key()
        _doc_cache["data"] = payload
    _store_derived(tested_index_fingerprint(), flags, metrics)


def encode_tested_document(payload: dict) -> str:
    """JSON text for a tested-index payload. Safe to run outside the lock."""
    from hedge_fund.trading.handler_timing import io_timer

    with io_timer():
        return json.dumps(payload, separators=(",", ":"))


def commit_tested_blob(blob: str, payload: dict) -> None:
    """Write an index. Caller holds the lock.

    Re-encodes ``payload`` so the base carries a fresh generation id; the
    pre-encoded ``blob`` is ignored.
    """
    del blob
    write_tested_document(payload)


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


def mark_index_covers_log() -> None:
    """The on-disk index already includes the current display log."""
    global _covers
    from hedge_fund.trading.discovery import discovery_log_fingerprint

    _covers = (discovery_log_fingerprint(), tested_index_fingerprint())


def ensure_tested_index() -> dict[str, bool]:
    """Merge the on-disk display log into the index.

    The log is read before the lock. The lock covers only the index
    read-modify-write, so a slow log parse does not block claim, ingest,
    or champions. No-ops without creating the state dir when neither file
    exists. A second call with the same files returns the cached flags
    and does not walk the log again.
    """
    global _covers
    from hedge_fund.trading.discovery import discovery_log_fingerprint, discovery_log_path, load_discovery_log

    if not tested_index_path().exists() and not discovery_log_path().exists():
        return {}
    fp = (discovery_log_fingerprint(), tested_index_fingerprint())
    if _covers == fp and fp[1] is not None:
        return load_tested_index()
    rows = load_discovery_log()
    with paper_state_lock("discovery"):
        # Another writer may have covered the log while we parsed it.
        fp = (discovery_log_fingerprint(), tested_index_fingerprint())
        if _covers == fp and fp[1] is not None:
            return load_tested_index()
        index, metrics = load_index_and_metrics()
        flags_changed = merge_tested_rows(index, rows)
        metrics_changed = merge_tested_metrics(metrics, rows)
        if flags_changed or metrics_changed:
            save_tested_index(index, metrics, cleaned=True)
        mark_index_covers_log()
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
