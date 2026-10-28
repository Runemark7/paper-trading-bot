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


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_tape_cache(root: Path, pin: int, manifest: dict, blob: bytes) -> None:
    """Write the npz first, then the manifest. A reader ignores a half pair."""
    _atomic_bytes(_blob_file(root, pin), blob)
    _atomic_bytes(
        _manifest_file(root, pin),
        json.dumps(manifest, separators=(",", ":")).encode(),
    )


def read_tape_cache(root: Path, pin: int) -> tuple[dict, bytes] | None:
    man_path = _manifest_file(root, pin)
    blob_path = _blob_file(root, pin)
    if not man_path.is_file() or not blob_path.is_file():
        return None
    try:
        manifest = json.loads(man_path.read_text())
        blob = blob_path.read_bytes()
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    return manifest, blob


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


def _remember(root: Path, pin: int, manifest: dict, blob: bytes) -> None:
    _served[_root_key(root)] = {
        "pin": int(pin),
        "manifest": manifest,
        "blob": blob,
    }


def _install_built(
    root: Path,
    pin: int,
    manifest: dict,
    blob: bytes,
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
        _served[key] = {"pin": int(pin), "manifest": manifest, "blob": blob}


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


def _build_pin(root: Path, pin: int, *, active_pin: int | None) -> dict | None:
    arrays: dict[str, np.ndarray] = {}
    source = "missing"
    for symbol in QUAL_SYMBOLS:
        arr, source = _source_array(root, symbol)
        arrays[symbol] = _cut_to_pin(arr, int(pin), 0)
    if not any(len(arr) for arr in arrays.values()):
        return None
    manifest = _hash_manifest(arrays, source, int(pin))
    blob = _compress(arrays, int(pin))
    save_tape_cache(root, int(pin), manifest, blob)
    _install_built(root, int(pin), manifest, blob, active_pin)
    return manifest


def publish_shared_tape(
    state_dir: Path | None = None,
    *,
    pin: int | None = None,
    now_ms: int | None = None,
) -> dict | None:
    """Build one pin and persist it. Not for a request handler."""
    root = state_root() if state_dir is None else Path(state_dir)
    active = pin if pin is not None else _pin_for_build(
        root, now_ms if now_ms is not None else int(time.time() * 1000)
    )
    if active is None:
        return None
    target = int(pin) if pin is not None else int(active)
    return _build_pin(root, target, active_pin=int(active))


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
    """Newest on-disk tape. A prebuild that does not reach its pin is skipped
    when an older, complete day is also on disk.
    """
    pins = _cached_pin_numbers(root)
    chosen: tuple[int, dict, bytes] | None = None
    fallback: tuple[int, dict, bytes] | None = None
    for pin in pins:
        loaded = read_tape_cache(root, pin)
        if loaded is None:
            continue
        manifest, blob = loaded
        if fallback is None:
            fallback = (pin, manifest, blob)
        if manifest_covers_pin(manifest, pin):
            chosen = (pin, manifest, blob)
            break
    picked = chosen or fallback
    if picked is None:
        return None
    pin, manifest, blob = picked
    return {"pin": int(pin), "manifest": manifest, "blob": blob}


def clear_served_tape(state_dir: Path | None = None) -> None:
    """Drop the in-memory served tape. Disk files stay. Tests use this."""
    if state_dir is None:
        _served.clear()
        return
    _served.pop(_root_key(Path(state_dir)), None)


def served_history(state_dir: Path | None = None) -> tuple[dict, bytes]:
    """Manifest and npz already in memory or on disk.

    Raises :class:`TapeNotReady` when neither exists. Does not read
    Postgres and does not hash the tape.
    """
    root = state_root() if state_dir is None else Path(state_dir)
    key = _root_key(root)
    with _serve_lock:
        hit = _served.get(key)
        if hit is None:
            hit = _load_newest(root)
            if hit is not None:
                _served[key] = hit
        if hit is not None:
            return dict(hit["manifest"]), hit["blob"]
    schedule_tape_build(root)
    raise TapeNotReady()


def served_manifest(state_dir: Path | None = None) -> dict:
    manifest, _blob = served_history(state_dir)
    return manifest


def served_tape_blob(state_dir: Path | None = None) -> bytes:
    _manifest, blob = served_history(state_dir)
    return blob


def _symbol_end(manifest: dict | None) -> int | None:
    if not isinstance(manifest, dict):
        return None
    symbols = manifest.get("symbols")
    if not isinstance(symbols, dict):
        return None
    ends: list[int] = []
    for symbol in QUAL_SYMBOLS:
        row = symbols.get(symbol)
        end = row.get("data_end") if isinstance(row, dict) else None
        if isinstance(end, bool) or not isinstance(end, (int, float)):
            return None
        ends.append(int(end))
    return min(ends) if ends else None


def _build_loop(root_str: str) -> None:
    """One builder. Sleeps between passes. A failed pass backs off.

    A pin whose rebuilt tape did not gain a bar is not rebuilt again until
    the source's newest bar moves, so a short tape cannot spin on Postgres.
    """
    delay = _BUILD_BACKOFF_START
    root = Path(root_str)
    # pin -> (symbol end, raw end observed when we last built it)
    stable: dict[int, tuple[int | None, int | None]] = {}
    while True:
        try:
            now_ms = int(time.time() * 1000)
            active, raw_end = _active_and_raw(root)
            if active is not None:
                loaded = read_tape_cache(root, active)
                covers = loaded is not None and manifest_covers_pin(loaded[0], active)
                served = _served_pin(root)
                if covers and served != active and loaded is not None:
                    _install_built(root, active, loaded[0], loaded[1], active)
                elif not covers:
                    sig = _symbol_end(loaded[0]) if loaded is not None else None
                    seen = stable.get(active)
                    raw_moved = seen is None or seen[1] != raw_end
                    if loaded is None or raw_moved or seen is None or seen[0] != sig:
                        manifest = _build_pin(root, active, active_pin=active)
                        stable[active] = (_symbol_end(manifest), raw_end)
            nxt = floor_utc_day(now_ms) + DAY_MS
            if (
                active is not None
                and nxt != active
                and 0 < nxt - now_ms <= PREBUILD_LEAD_MS
                and read_tape_cache(root, nxt) is None
            ):
                _build_pin(root, nxt, active_pin=active)
            wait = 60.0
            if active is not None:
                until_lead = (nxt - PREBUILD_LEAD_MS - now_ms) / 1000.0
                if until_lead > 0:
                    wait = min(60.0, max(1.0, until_lead))
            time.sleep(wait)
            delay = _BUILD_BACKOFF_START
        except Exception as exc:  # noqa: BLE001 — keep the one builder alive
            print(f"[price history] tape build failed: {exc}", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, _BUILD_BACKOFF_MAX)


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
    """Load the newest on-disk tape, then let the background thread catch up.

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
