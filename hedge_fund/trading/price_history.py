"""One canonical 5m tape for every discovery worker.

Prod hashes the qualification window it already keeps (live tape npy, or
``crypto_history_5m.json`` when that is all that is on disk) and serves
the bytes. A worker refreshes when its local hash differs and refuses to
evaluate until the hash matches. The manifest is cached by file stat so
the request path does not re-hash a warm tape.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np

from hedge_fund.paths import state_root
from hedge_fund.trading.constants import QUAL_SYMBOLS, qual_keep_bars
from hedge_fund.trading.live_tape import TAPE_DTYPE, load_array, save_array, tape_path

HISTORY_JSON = "crypto_history_5m.json"
_HASH_PREFIX = b"paper-discovery-tape-v1\n"
# (stat key, manifest). Process-local; a new mtime rebuilds the hash.
_MANIFEST_CACHE: dict[tuple, dict] = {}


class HistoryMismatch(RuntimeError):
    """Local tape still disagrees with prod after a refresh."""


def _stat_key(path: Path) -> tuple:
    try:
        st = path.stat()
    except OSError:
        return (str(path), None, None)
    return (str(path), int(st.st_mtime_ns), int(st.st_size))


def _json_path(root: Path) -> Path:
    return root / HISTORY_JSON


def _tape_ready(root: Path) -> bool:
    for symbol in QUAL_SYMBOLS:
        path = tape_path(symbol, root)
        if not path.is_file() or path.stat().st_size <= 0:
            return False
    return True


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
        arr = load_array(symbol, root)
        return arr, "live_tape"
    return _array_from_json(root, symbol), HISTORY_JSON


def _trim(arr: np.ndarray, keep: int) -> np.ndarray:
    if arr.size == 0:
        return arr
    if keep > 0 and len(arr) > keep:
        arr = arr[-keep:]
    return np.ascontiguousarray(arr, dtype=TAPE_DTYPE)


def canonical_arrays(state_dir: Path | None = None) -> tuple[dict[str, np.ndarray], str]:
    """Last ``qual_keep_bars`` rows per symbol, in ``QUAL_SYMBOLS`` order."""
    root = state_root() if state_dir is None else Path(state_dir)
    keep = qual_keep_bars()
    source = ""
    out: dict[str, np.ndarray] = {}
    for symbol in QUAL_SYMBOLS:
        arr, source = _source_array(root, symbol)
        out[symbol] = _trim(arr, keep)
    return out, source


def _cache_key(root: Path) -> tuple:
    if _tape_ready(root):
        return tuple(_stat_key(tape_path(symbol, root)) for symbol in QUAL_SYMBOLS)
    return (_stat_key(_json_path(root)),)


def manifest_for(state_dir: Path | None = None) -> dict:
    """sha256 and last-bar timestamps. Cached until the tape files change."""
    root = state_root() if state_dir is None else Path(state_dir)
    key = _cache_key(root)
    cached = _MANIFEST_CACHE.get(key)
    if cached is not None:
        return dict(cached)
    arrays, source = canonical_arrays(root)
    digest = hashlib.sha256()
    digest.update(_HASH_PREFIX)
    symbols: dict[str, dict] = {}
    ends: list[int] = []
    for symbol in QUAL_SYMBOLS:
        arr = arrays[symbol]
        digest.update(symbol.encode("utf-8"))
        digest.update(b"\0")
        digest.update(np.int64(len(arr)).tobytes())
        digest.update(arr.tobytes())
        end = int(arr["ts"][-1]) if len(arr) else None
        if end is not None:
            ends.append(end)
        symbols[symbol] = {"bars": int(len(arr)), "data_end": end}
    data_end = min(ends) if ends else None
    payload = {
        "paper_only": True,
        "sha256": digest.hexdigest(),
        "data_end": data_end,
        "symbols": symbols,
        "source": source or "missing",
        "keep_bars": qual_keep_bars(),
    }
    _MANIFEST_CACHE[key] = payload
    return dict(payload)


def clear_manifest_cache() -> None:
    _MANIFEST_CACHE.clear()


def tape_blob(state_dir: Path | None = None) -> bytes:
    """Compressed npz of the canonical window. Built on download, not on poll."""
    arrays, _source = canonical_arrays(state_dir)
    buffer = io.BytesIO()
    named = {symbol.replace("/", "_"): arrays[symbol] for symbol in QUAL_SYMBOLS}
    np.savez_compressed(buffer, **named)
    return buffer.getvalue()


def install_tape_blob(blob: bytes, state_dir: Path | None = None) -> dict:
    """Replace the local live-tape npy files with a prod snapshot."""
    root = state_root() if state_dir is None else Path(state_dir)
    with np.load(io.BytesIO(blob)) as packed:
        for symbol in QUAL_SYMBOLS:
            slug = symbol.replace("/", "_")
            if slug not in packed:
                raise HistoryMismatch(f"snapshot missing {symbol}")
            arr = np.ascontiguousarray(packed[slug], dtype=TAPE_DTYPE)
            save_array(symbol, arr, root)
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
    """
    arrays, source = canonical_arrays(state_dir)
    if source != "live_tape" or not any(len(arr) for arr in arrays.values()):
        return None
    history = arrays_to_history(arrays)
    return history or None
