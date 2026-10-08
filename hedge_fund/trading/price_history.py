"""One canonical 5m tape for every discovery worker.

Prod pins the qualification window to the last completed UTC day: the
newest bar's timestamp floored to 00:00 UTC. Bars after that boundary
are the incomplete day and are not hashed, so a live append (~5 min)
does not change the sha256 until the next UTC day. The manifest cache
is keyed by that pinned ``data_end``, not by file mtime.

A worker that has installed a snapshot keeps the pin in a sidecar so
re-hashing the truncated file does not walk back another day. The npz
download is built once per pin and reused.
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
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


def _source_array(root: Path, symbol: str) -> tuple[np.ndarray, str]:
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


def load_canonical_history(state_dir: Path | None = None) -> dict | None:
    """Shared npy tape only.

    ``crypto_history_5m.json`` stays on the original qualification parser
    so a worker that has not refreshed still sees the JSON rows it already
    had. After a refresh the npy files are the history that gets evaluated.
    The rows already end on the pinned UTC day.
    """
    arrays, source, _pin = canonical_arrays(state_dir)
    if source != "live_tape" or not any(len(arr) for arr in arrays.values()):
        return None
    history = arrays_to_history(arrays)
    return history or None
