"""One canonical 5m tape for every discovery worker.

Prod pins the qualification window to the last completed UTC day: the
newest bar's timestamp floored to 00:00 UTC. Bars after that boundary
are the incomplete day and are not hashed, so a live append (~5 min)
does not change the sha256 until the next UTC day. The manifest cache
is keyed by that pinned ``data_end``, not by file mtime.

A worker that has installed a snapshot keeps the pin in a sidecar so
re-hashing the truncated file does not walk back another day. The npz
download is built once per pin and reused.

When ``PAPERBOT_DATABASE_URL`` is set and the candle store covers the
canonical tape through a recent closed bar, that store is the source the
hash is built from. Until then, and whenever the URL is unset, the
live-tape npy (then the JSON file) is the source. Workers never open the
database; they download the npz this module serves.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from hedge_fund.paths import state_root
from hedge_fund.trading.constants import QUAL_CANONICAL_START_MS, QUAL_SYMBOLS, qual_keep_bars
from hedge_fund.trading.live_tape import TAPE_DTYPE, load_array, load_tail, save_array, tape_path

HISTORY_JSON = "crypto_history_5m.json"
PIN_NAME = "discovery_pin.json"
_HASH_PREFIX = b"paper-discovery-tape-v1\n"
# 5m bars are unix milliseconds. A UTC day is an exact multiple of 5m.
DAY_MS = 86_400_000
# (root, pinned data_end, source) -> manifest. Not keyed by file stat.
_MANIFEST_CACHE: dict[tuple, dict] = {}
# One compressed snapshot, keyed by sha256. The next pin replaces it.
_BLOB: tuple[str, bytes] | None = None
# JSON tails are not mmap-friendly. Remember the last bar per file stat
# so a rewrite that stays inside the same UTC day does not re-hash.
_JSON_RAW: dict[tuple, int | None] = {}
_LOCK = threading.Lock()


class HistoryMismatch(RuntimeError):
    """Snapshot bytes are not a canonical tape."""


def floor_utc_day(ts: int) -> int:
    """Floor a unix-ms timestamp to 00:00 UTC of that day."""
    value = int(ts)
    return value - (value % DAY_MS)


def _stat_key(path: Path) -> tuple:
    try:
        st = path.stat()
    except OSError:
        return (str(path), None, None)
    return (str(path), int(st.st_mtime_ns), int(st.st_size))


def _json_path(root: Path) -> Path:
    return root / HISTORY_JSON


def _pin_path(root: Path) -> Path:
    return root / "live_tape" / PIN_NAME


def _tape_ready(root: Path) -> bool:
    for symbol in QUAL_SYMBOLS:
        path = tape_path(symbol, root)
        if not path.is_file() or path.stat().st_size <= 0:
            return False
    return True


def _read_pin(root: Path) -> int | None:
    path = _pin_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("data_end")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _write_pin(root: Path, data_end: int) -> None:
    path = _pin_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"data_end": int(data_end)}))


def _rows_to_array(rows: list) -> np.ndarray:
    from hedge_fund.trading.live_tape import rows_to_array

    return rows_to_array(rows)


def _array_from_json(root: Path, symbol: str) -> np.ndarray:
    path = _json_path(root)
    if not path.is_file():
        return np.empty(0, dtype=TAPE_DTYPE)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return np.empty(0, dtype=TAPE_DTYPE)
    if not isinstance(data, dict):
        return np.empty(0, dtype=TAPE_DTYPE)
    slug = symbol.replace("/", "_")
    rows = data.get(symbol) or data.get(slug)
    if not isinstance(rows, list):
        return np.empty(0, dtype=TAPE_DTYPE)
    return _rows_to_array(rows)


def _use_candle_store() -> bool:
    """True when the shared tape should be read from Postgres."""
    if not os.environ.get("PAPERBOT_DATABASE_URL", "").strip():
        return False
    try:
        from hedge_fund.data.candle_store import candle_store_selected

        return bool(candle_store_selected())
    except Exception as exc:
        print(f"[price history] candle store unavailable, using files: {exc}", flush=True)
        return False


def _source_array(root: Path, symbol: str) -> tuple[np.ndarray, str]:
    if _use_candle_store():
        from hedge_fund.data.candle_store import clear_ready, load_tape

        arr = load_tape(symbol)
        if len(arr) == 0:
            clear_ready()
        else:
            return arr, "candles"
    if _tape_ready(root):
        return load_array(symbol, root), "live_tape"
    return _array_from_json(root, symbol), HISTORY_JSON


def _json_raw_end(root: Path) -> int | None:
    path = _json_path(root)
    key = _stat_key(path)
    if key in _JSON_RAW:
        return _JSON_RAW[key]
    ends: list[int] = []
    for symbol in QUAL_SYMBOLS:
        arr = _array_from_json(root, symbol)
        if len(arr):
            ends.append(int(arr["ts"][-1]))
    raw = min(ends) if ends else None
    _JSON_RAW[key] = raw
    return raw


def _raw_end(root: Path) -> tuple[int | None, str]:
    """Newest bar the two symbols share. Npy reads one row, not the file stat."""
    if _use_candle_store():
        from hedge_fund.data.candle_store import latest_open_ms

        ends: list[int] = []
        for symbol in QUAL_SYMBOLS:
            ts = latest_open_ms(symbol)
            if ts is None:
                return None, "candles"
            ends.append(int(ts))
        return min(ends), "candles"
    if _tape_ready(root):
        ends: list[int] = []
        for symbol in QUAL_SYMBOLS:
            tail = load_tail(symbol, 1, root)
            if len(tail) == 0:
                return None, "live_tape"
            ends.append(int(tail["ts"][-1]))
        return min(ends), "live_tape"
    return _json_raw_end(root), HISTORY_JSON


def _active_pin(root: Path, raw_end: int | None) -> int | None:
    """Installed snapshots keep their pin. A growing tape floors the tail."""
    side = _read_pin(root)
    if side is not None:
        return side
    if raw_end is None:
        return None
    return floor_utc_day(raw_end)


def _trim(arr: np.ndarray, keep: int) -> np.ndarray:
    if arr.size == 0:
        return arr
    if keep > 0 and len(arr) > keep:
        arr = arr[-keep:]
    return np.ascontiguousarray(arr, dtype=TAPE_DTYPE)


def _cut_to_pin(arr: np.ndarray, pin: int | None, keep: int = 0) -> np.ndarray:
    """Drop the incomplete UTC day and everything before the canonical start.

    Bars at exactly ``pin`` (00:00 UTC) stay. Later bars do not, so the last
    OOS segment ends on that day boundary. The front is cut by timestamp at
    ``QUAL_CANONICAL_START_MS`` (tiled OOS layout, amendment 2026-10-08
    18:08), so the hash does not depend on how much older tape a store holds.
    ``keep`` > 0 still caps the bar count (legacy callers / tests).
    """
    if arr.size == 0:
        return _trim(arr, keep)
    if pin is not None and len(arr):
        idx = int(np.searchsorted(arr["ts"], np.int64(pin), side="right"))
        arr = arr[:idx]
    if len(arr):
        lo = int(np.searchsorted(arr["ts"], np.int64(QUAL_CANONICAL_START_MS), side="left"))
        arr = arr[lo:]
    return _trim(arr, keep)


def canonical_arrays(
    state_dir: Path | None = None,
) -> tuple[dict[str, np.ndarray], str, int | None]:
    """Pinned window per symbol, in ``QUAL_SYMBOLS`` order, plus the pin."""
    root = state_root() if state_dir is None else Path(state_dir)
    raw, source = _raw_end(root)
    pin = _active_pin(root, raw)
    keep = 0  # cut by timestamp (QUAL_CANONICAL_START_MS), not by count
    out: dict[str, np.ndarray] = {}
    for symbol in QUAL_SYMBOLS:
        arr, source = _source_array(root, symbol)
        out[symbol] = _cut_to_pin(arr, pin, keep)
    return out, source, pin


def _hash_manifest(
    arrays: dict[str, np.ndarray],
    source: str,
    pin: int | None,
) -> dict:
    digest = hashlib.sha256()
    digest.update(_HASH_PREFIX)
    digest.update(np.int64(-1 if pin is None else pin).tobytes())
    symbols: dict[str, dict] = {}
    for symbol in QUAL_SYMBOLS:
        arr = arrays[symbol]
        digest.update(symbol.encode("utf-8"))
        digest.update(b"\0")
        digest.update(np.int64(len(arr)).tobytes())
        digest.update(arr.tobytes())
        end = int(arr["ts"][-1]) if len(arr) else None
        symbols[symbol] = {"bars": int(len(arr)), "data_end": end}
    return {
        "paper_only": True,
        "sha256": digest.hexdigest(),
        "data_end": pin,
        "symbols": symbols,
        "source": source or "missing",
        "keep_bars": int(len(arrays[QUAL_SYMBOLS[0]])) if arrays else 0,
        "canonical_start": QUAL_CANONICAL_START_MS,
    }


def manifest_for(state_dir: Path | None = None) -> dict:
    """sha256 of the pinned day. A new bar inside that day is a cache hit."""
    root = state_root() if state_dir is None else Path(state_dir)
    raw, source = _raw_end(root)
    pin = _active_pin(root, raw)
    key = (str(root.resolve()), pin, source)
    with _LOCK:
        cached = _MANIFEST_CACHE.get(key)
        if cached is not None:
            return dict(cached)
        arrays, source, pin = canonical_arrays(root)
        payload = _hash_manifest(arrays, source, pin)
        _MANIFEST_CACHE[(str(root.resolve()), pin, source)] = payload
        return dict(payload)


def clear_manifest_cache() -> None:
    global _BLOB
    with _LOCK:
        _MANIFEST_CACHE.clear()
        _JSON_RAW.clear()
        _BLOB = None


def _compress(arrays: dict[str, np.ndarray], pin: int | None) -> bytes:
    buffer = io.BytesIO()
    named = {symbol.replace("/", "_"): arrays[symbol] for symbol in QUAL_SYMBOLS}
    if pin is not None:
        named["data_end"] = np.asarray(pin, dtype=np.int64)
    np.savez_compressed(buffer, **named)
    return buffer.getvalue()


def tape_blob(state_dir: Path | None = None) -> bytes:
    """Compressed npz of the pinned window. Built once per sha256."""
    global _BLOB
    manifest = manifest_for(state_dir)
    sha = manifest.get("sha256")
    if not isinstance(sha, str) or not sha:
        raise HistoryMismatch("canonical tape has no sha256")
    with _LOCK:
        if _BLOB is not None and _BLOB[0] == sha:
            return _BLOB[1]
        arrays, _source, pin = canonical_arrays(state_dir)
        blob = _compress(arrays, pin)
        _BLOB = (sha, blob)
        return blob


def install_tape_blob(blob: bytes, state_dir: Path | None = None) -> dict:
    """Replace the local live-tape npy files with a prod snapshot."""
    root = state_root() if state_dir is None else Path(state_dir)
    with np.load(io.BytesIO(blob)) as packed:
        pin = None
        if "data_end" in packed.files:
            pin = int(np.asarray(packed["data_end"]).reshape(-1)[0])
        for symbol in QUAL_SYMBOLS:
            slug = symbol.replace("/", "_")
            if slug not in packed.files:
                raise HistoryMismatch(f"snapshot missing {symbol}")
            arr = np.ascontiguousarray(packed[slug], dtype=TAPE_DTYPE)
            save_array(symbol, arr, root)
    if pin is not None:
        _write_pin(root, pin)
    clear_manifest_cache()
    return manifest_for(root)


def arrays_to_history(arrays: dict[str, np.ndarray]) -> dict[str, list]:
    """Row lists ``[ts, o, h, l, c, volume]`` for the qualification loader."""
    out: dict[str, list] = {}
    for symbol, arr in arrays.items():
        rows = []
        for row in arr:
            rows.append([
                int(row["ts"]),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            ])
        if rows:
            out[symbol] = rows
    return out


# Sources that are the shared tape (not the legacy JSON parse path).
SHARED_TAPE_SOURCES = ("live_tape", "candles")


def load_canonical_history(state_dir: Path | None = None) -> dict | None:
    """Shared tape only: the live-tape npy files or the Postgres candle store.

    Both are the canonical tape (``canonical_arrays`` picks the store once
    it is caught up). ``crypto_history_5m.json`` stays on the original
    qualification parser
    so a worker that has not refreshed still sees the JSON rows it already
    had. After a refresh the npy files are the history that gets evaluated.
    The rows already end on the pinned UTC day.
    """
    arrays, source, _pin = canonical_arrays(state_dir)
    if source not in SHARED_TAPE_SOURCES or not any(len(arr) for arr in arrays.values()):
        return None
    history = arrays_to_history(arrays)
    if history:
        # Mint/densify/lift/refill scopes must not observe sealed bars.
        # Outside those scopes this is a no-op (workers load the full tape).
        from hedge_fund.trading.holdout import note_history_load

        note_history_load(history)
    return history or None


# Disk copy of the pinned manifest and the npz the workers download.
# Keyed by the pin (00:00 UTC). A request never rebuilds this. The
# background thread does, and a missing file is a fast 503.
TAPE_CACHE_DIR = "discovery_tape"
HISTORY_RETRY_AFTER_SECONDS = 30
# Start the next UTC day's tape before midnight so rollover does not
# wait on Postgres. The previous pin keeps being served until the new
# one is installed.
PREBUILD_LEAD_MS = 30 * 60 * 1000
_BUILD_BACKOFF_START = 5.0
_BUILD_BACKOFF_MAX = 300.0

_served: dict[str, dict] = {}
_serve_lock = threading.Lock()
_build_guard = threading.Lock()
_build_threads: dict[str, threading.Thread] = {}


class TapeNotReady(RuntimeError):
    """No persisted tape yet. The request must not build one."""

    def __init__(self, retry_after: int = HISTORY_RETRY_AFTER_SECONDS) -> None:
        self.retry_after = int(retry_after)
        super().__init__("tape not ready")


def _root_key(root: Path) -> str:
    try:
        return str(root.resolve())
    except OSError:
        return str(root)


def _cache_dir(root: Path) -> Path:
    return root / TAPE_CACHE_DIR


def _manifest_file(root: Path, pin: int) -> Path:
    return _cache_dir(root) / f"{int(pin)}.manifest.json"


def _blob_file(root: Path, pin: int) -> Path:
    return _cache_dir(root) / f"{int(pin)}.npz"


# Bookkeeping stored beside the manifest on disk. Stripped before serving.
_CACHE_META = "_tape_cache"
# Days of tape files kept on disk (today, yesterday, a prebuilt tomorrow).
TAPE_CACHE_KEEP = 3


def _atomic_bytes(path: Path, data: bytes) -> None:
    """Temp file in the same directory, fsync, rename, fsync the directory.

    A crash leaves either the old file or the new one, never a torn one.
    The temp name starts with a dot, so the cache scan ignores leftovers.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    try:
        fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def save_tape_cache(
    root: Path,
    pin: int,
    manifest: dict,
    blob: bytes,
    *,
    built_raw_end: int | None = None,
) -> None:
    """Write the npz first, then the manifest. A reader ignores a half pair.

    The manifest file records the npz length and sha256, so an npz that does
    not match its manifest (a crash between the two renames, a stray copy) is
    not served.
    """
    _atomic_bytes(_blob_file(root, pin), blob)
    record = dict(manifest)
    record[_CACHE_META] = {
        "blob_bytes": len(blob),
        "blob_sha256": hashlib.sha256(blob).hexdigest(),
        "built_raw_end": None if built_raw_end is None else int(built_raw_end),
    }
    _atomic_bytes(
        _manifest_file(root, pin),
        json.dumps(record, separators=(",", ":")).encode(),
    )


def _read_manifest_file(root: Path, pin: int) -> tuple[dict, dict] | None:
    """``(manifest, cache meta)`` when the pair on disk is whole. No blob read."""
    man_path = _manifest_file(root, pin)
    blob_path = _blob_file(root, pin)
    try:
        record = json.loads(man_path.read_text())
        size = blob_path.stat().st_size
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    meta = record.pop(_CACHE_META, None)
    if not isinstance(meta, dict) or meta.get("blob_bytes") != size:
        return None
    if not isinstance(record.get("sha256"), str):
        return None
    return record, meta


def read_tape_cache(root: Path, pin: int) -> tuple[dict, bytes] | None:
    """Manifest and npz bytes. The npz must match the recorded sha256."""
    head = _read_manifest_file(root, pin)
    if head is None:
        return None
    manifest, meta = head
    try:
        blob = _blob_file(root, pin).read_bytes()
    except OSError:
        return None
    if hashlib.sha256(blob).hexdigest() != meta.get("blob_sha256"):
        return None
    return manifest, blob


def _tape_final(manifest: dict, meta: dict | None, pin: int) -> bool:
    """The cut at ``pin`` cannot change any more.

    True when the tape reaches the pin bar, or when it was built after the
    source already had a bar past the pin (a missing 00:00 bar must not make
    the builder rebuild the same day on every new bar).
    """
    if manifest_covers_pin(manifest, pin):
        return True
    raw = meta.get("built_raw_end") if isinstance(meta, dict) else None
    return isinstance(raw, int) and not isinstance(raw, bool) and raw > int(pin)


def prune_tape_cache(root: Path, keep: int = TAPE_CACHE_KEEP) -> None:
    """Drop tape files older than the newest ``keep`` days, and stale temps."""
    directory = _cache_dir(root)
    if not directory.is_dir():
        return
    pins: set[int] = set()
    for path in directory.iterdir():
        name = path.name
        if name.startswith("."):
            try:
                if time.time() - path.stat().st_mtime > 3600:
                    path.unlink()
            except OSError:
                pass
            continue
        stem = name.split(".", 1)[0]
        try:
            pins.add(int(stem))
        except ValueError:
            continue
    for pin in sorted(pins, reverse=True)[max(1, int(keep)):]:
        for path in (_manifest_file(root, pin), _blob_file(root, pin)):
            try:
                path.unlink()
            except OSError:
                pass


def manifest_covers_pin(manifest: dict, pin: int) -> bool:
    """True when every symbol's last bar is on or after the pin boundary."""
    symbols = manifest.get("symbols") if isinstance(manifest, dict) else None
    if not isinstance(symbols, dict):
        return False
    ends: list[int] = []
    for symbol in QUAL_SYMBOLS:
        row = symbols.get(symbol)
        end = row.get("data_end") if isinstance(row, dict) else None
        if isinstance(end, bool) or not isinstance(end, (int, float)):
            return False
        ends.append(int(end))
    return bool(ends) and min(ends) >= int(pin)


def pins_to_build(now_ms: int, active_pin: int | None, have: set[int]) -> list[int]:
    """Pins the background thread should build. The request path does not call this.

    The active pin is always due when it is not on disk. Inside the half
    hour before the next UTC midnight, the next pin is due as well so
    rollover is not the first time the tape is read.
    """
    due: list[int] = []
    if active_pin is not None and int(active_pin) not in have:
        due.append(int(active_pin))
    next_midnight = floor_utc_day(int(now_ms)) + DAY_MS
    if 0 < next_midnight - int(now_ms) <= PREBUILD_LEAD_MS and next_midnight not in have:
        if next_midnight not in due:
            due.append(next_midnight)
    return due


def _entry(root: Path, pin: int, manifest: dict) -> dict:
    """What the web keeps per state dir: the manifest and where its npz is.

    The npz is not held in memory. ``/history/tape`` streams the file.
    """
    blob_path = _blob_file(root, int(pin))
    return {
        "pin": int(pin),
        "manifest": manifest,
        "path": blob_path,
        "size": blob_path.stat().st_size,
    }


def _install_built(
    root: Path,
    pin: int,
    manifest: dict,
    active_pin: int | None,
) -> None:
    """Serve ``pin`` when it is the active day. A future prebuild stays on disk."""
    if active_pin is not None and int(pin) > int(active_pin):
        return
    key = _root_key(root)
    with _serve_lock:
        current = _served.get(key)
        if current is not None and int(current["pin"]) > int(pin):
            return
        if (
            active_pin is not None
            and int(pin) < int(active_pin)
            and current is not None
        ):
            return
        _served[key] = _entry(root, int(pin), manifest)


def _active_and_raw(root: Path) -> tuple[int | None, int | None]:
    """``(pin, newest shared bar)``. Background only: may ask Postgres for one timestamp."""
    if _use_candle_store():
        from hedge_fund.data.candle_store import latest_open_ms

        ends: list[int] = []
        for symbol in QUAL_SYMBOLS:
            ts = latest_open_ms(symbol)
            if ts is None:
                return None, None
            ends.append(int(ts))
        raw = min(ends)
        return floor_utc_day(raw), raw
    raw, _source = _raw_end(root)
    if raw is None:
        return None, None
    pin = _active_pin(root, raw)
    return pin, raw


def _pin_for_build(root: Path, now_ms: int) -> int | None:
    del now_ms
    pin, _raw = _active_and_raw(root)
    return pin


def _build_pin(
    root: Path,
    pin: int,
    *,
    active_pin: int | None,
    raw_end: int | None = None,
) -> dict | None:
    """Read the tape, hash it exactly as :func:`manifest_for` does, persist it.

    Same arrays (``_source_array`` cut by ``_cut_to_pin``) and the same
    ``_hash_manifest``, so a worker's cached tape keeps its fingerprint.
    """
    arrays: dict[str, np.ndarray] = {}
    source = "missing"
    for symbol in QUAL_SYMBOLS:
        arr, source = _source_array(root, symbol)
        arrays[symbol] = _cut_to_pin(arr, int(pin), 0)
        del arr
    if not any(len(arr) for arr in arrays.values()):
        return None
    manifest = _hash_manifest(arrays, source, int(pin))
    blob = _compress(arrays, int(pin))
    del arrays
    save_tape_cache(root, int(pin), manifest, blob, built_raw_end=raw_end)
    del blob
    _install_built(root, int(pin), manifest, active_pin)
    try:
        prune_tape_cache(root)
    except OSError:
        pass
    return manifest


def publish_shared_tape(
    state_dir: Path | None = None,
    *,
    pin: int | None = None,
    now_ms: int | None = None,
) -> dict | None:
    """Build one pin and persist it. Not for a request handler."""
    root = state_root() if state_dir is None else Path(state_dir)
    del now_ms
    active, raw_end = _active_and_raw(root)
    if pin is None:
        if active is None:
            return None
        pin = active
    return _build_pin(
        root,
        int(pin),
        active_pin=int(active) if active is not None else int(pin),
        raw_end=raw_end,
    )


def _served_pin(root: Path) -> int | None:
    hit = _served.get(_root_key(root))
    if hit is None:
        return None
    return int(hit["pin"])


def _cached_pin_numbers(root: Path) -> list[int]:
    directory = _cache_dir(root)
    if not directory.is_dir():
        return []
    pins: list[int] = []
    for path in directory.glob("*.manifest.json"):
        if path.name.startswith("."):
            continue
        stem = path.name[: -len(".manifest.json")]
        try:
            pin = int(stem)
        except ValueError:
            continue
        if _blob_file(root, pin).is_file():
            pins.append(pin)
    pins.sort(reverse=True)
    return pins


def _load_newest(root: Path) -> dict | None:
    """Newest whole on-disk tape. A prebuild that does not reach its pin is
    skipped when an older, complete day is also on disk. Reads only the
    small manifest file; the npz stays on disk.
    """
    pins = _cached_pin_numbers(root)
    chosen: tuple[int, dict] | None = None
    fallback: tuple[int, dict] | None = None
    for pin in pins:
        head = _read_manifest_file(root, pin)
        if head is None:
            continue
        manifest, meta = head
        if fallback is None:
            fallback = (pin, manifest)
        if _tape_final(manifest, meta, pin):
            chosen = (pin, manifest)
            break
    picked = chosen or fallback
    if picked is None:
        return None
    pin, manifest = picked
    try:
        return _entry(root, pin, manifest)
    except OSError:
        return None


def clear_served_tape(state_dir: Path | None = None) -> None:
    """Drop the in-memory served tape. Disk files stay. Tests use this."""
    if state_dir is None:
        _served.clear()
        return
    _served.pop(_root_key(Path(state_dir)), None)


def _served_entry(state_dir: Path | None) -> dict:
    root = state_root() if state_dir is None else Path(state_dir)
    key = _root_key(root)
    with _serve_lock:
        hit = _served.get(key)
        if hit is None:
            hit = _load_newest(root)
            if hit is not None:
                _served[key] = hit
        if hit is not None:
            return hit
    schedule_tape_build(root)
    raise TapeNotReady()


def served_manifest(state_dir: Path | None = None) -> dict:
    """Manifest already in memory or on disk. Raises :class:`TapeNotReady`.

    Does not read Postgres and does not hash the tape.
    """
    return dict(_served_entry(state_dir)["manifest"])


def open_served_tape(state_dir: Path | None = None):
    """``(manifest, open binary file, size)`` for streaming the npz.

    The file is opened before returning, so a later rename of a new day over
    it does not change what this response sends.
    """
    for _attempt in range(2):
        hit = _served_entry(state_dir)
        try:
            handle = open(hit["path"], "rb")
        except OSError:
            clear_served_tape(state_dir)
            continue
        size = os.fstat(handle.fileno()).st_size
        return dict(hit["manifest"]), handle, size
    raise TapeNotReady()


def served_history(state_dir: Path | None = None) -> tuple[dict, bytes]:
    """Manifest and npz bytes. Tests and tools; the web streams instead."""
    manifest, handle, _size = open_served_tape(state_dir)
    with handle:
        return manifest, handle.read()


def served_tape_blob(state_dir: Path | None = None) -> bytes:
    _manifest, blob = served_history(state_dir)
    return blob


def _launch_pin_build(
    root: Path,
    pin: int,
    *,
    active_pin: int | None,
    raw_end: int | None,
) -> dict | None:
    """Build ``pin`` in a short-lived process. This process reads the manifest.

    ``PAPERBOT_TAPE_INPROCESS=1`` keeps the old in-process path for a unit
    test that must patch ``_build_pin``. Production leaves it unset.
    """
    if os.environ.get("PAPERBOT_TAPE_INPROCESS") == "1":
        return _build_pin(root, int(pin), active_pin=active_pin, raw_end=raw_end)
    cmd = [
        sys.executable,
        "-m",
        "hedge_fund.trading.tape_build",
        "--state",
        str(root),
        "--pin",
        str(int(pin)),
        "--active-pin",
        str(int(active_pin) if active_pin is not None else int(pin)),
        "--raw-end",
        "-1" if raw_end is None else str(int(raw_end)),
    ]
    env = os.environ.copy()
    env["PAPER_STATE"] = str(root)
    pkg_root = str(Path(__file__).resolve().parents[2])
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = pkg_root if not prev else pkg_root + os.pathsep + prev
    proc = subprocess.run(cmd, env=env, check=False, timeout=20 * 60)
    if proc.returncode != 0:
        raise RuntimeError(f"tape build exited {proc.returncode}")
    head = _read_manifest_file(root, int(pin))
    if head is None:
        return None
    manifest, _meta = head
    _install_built(root, int(pin), manifest, active_pin)
    return manifest


def _build_pass(root: Path, stable: dict) -> float:
    """One builder pass. Returns seconds to sleep before the next pass."""
    now_ms = int(time.time() * 1000)
    active, raw_end = _active_and_raw(root)
    if active is not None:
        head = _read_manifest_file(root, active)
        final = head is not None and _tape_final(head[0], head[1], active)
        if final and head is not None and _served_pin(root) != active:
            _install_built(root, active, head[0], active)
        elif not final:
            # Rebuild when the source moved since the last try. A tape whose
            # newest bar did not move is not read again. The arrays live in
            # a child process; this process only reads the manifest back.
            if head is None or stable.get(active) != raw_end:
                _launch_pin_build(root, active, active_pin=active, raw_end=raw_end)
                stable[active] = raw_end
    nxt = floor_utc_day(now_ms) + DAY_MS
    if (
        active is not None
        and nxt != active
        and 0 < nxt - now_ms <= PREBUILD_LEAD_MS
        and _read_manifest_file(root, nxt) is None
    ):
        # Warm the next day. It is not served until it is final; after
        # midnight the pass above rebuilds it once the 00:00 bar lands.
        _launch_pin_build(root, nxt, active_pin=active, raw_end=raw_end)
    wait = 60.0
    if active is not None:
        until_lead = (nxt - PREBUILD_LEAD_MS - now_ms) / 1000.0
        if until_lead > 0:
            wait = min(60.0, max(1.0, until_lead))
    return wait


def _build_loop(root_str: str) -> None:
    """One builder per process. A failed pass backs off 5s doubling to 300s."""
    delay = _BUILD_BACKOFF_START
    root = Path(root_str)
    stable: dict[int, int | None] = {}
    while True:
        try:
            wait = _build_pass(root, stable)
            delay = _BUILD_BACKOFF_START
        except Exception as exc:  # noqa: BLE001 — keep the one builder alive
            print(f"[price history] tape build failed: {exc}; retry in {delay:.0f}s", flush=True)
            wait = delay
            delay = min(delay * 2, _BUILD_BACKOFF_MAX)
        time.sleep(wait)


def schedule_tape_build(state_dir: Path | None = None) -> None:
    """One background builder per state dir. A second call does not start another."""
    root = state_root() if state_dir is None else Path(state_dir)
    key = _root_key(root)
    with _build_guard:
        thread = _build_threads.get(key)
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(
            target=_build_loop,
            args=(str(root),),
            name="discovery-tape",
            daemon=True,
        )
        _build_threads[key] = thread
        thread.start()


def prime_tape_cache(state_dir: Path | None = None) -> None:
    """Load the newest on-disk manifest, then let the background thread catch up.

    Called once from the web process start. A request does not call this.
    """
    root = state_root() if state_dir is None else Path(state_dir)
    key = _root_key(root)
    with _serve_lock:
        if key not in _served:
            loaded = _load_newest(root)
            if loaded is not None:
                _served[key] = loaded
    schedule_tape_build(root)
