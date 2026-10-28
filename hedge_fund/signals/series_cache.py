"""Byte-budget cache for structure series and precomputed entry signals.

Keys are a content hash of the tape plus the parameters. Entries are
``array.array`` buffers. The cache does not store the source lists and
does not key on ``id()``: a freed list whose id is reused cannot serve
another tape's series.

One process-wide budget (default 150MB), evicted least-recently-used
by bytes. ``clear_series_cache`` drops it. Qualification calls that at
the start of a window batch, so names on the same slices still share it.
"""
from __future__ import annotations

import hashlib
import threading
from array import array
from collections import OrderedDict

# Child RSS has to stay near 700MB with the prod tape loaded. The series
# we allocate ourselves stay inside this cap; the tape is not counted.
CACHE_BUDGET_BYTES = 150 * 1024 * 1024

_LOCK = threading.Lock()
_STORE: OrderedDict[tuple, tuple[object, int]] = OrderedDict()
_NBYTES = 0

# Identity hint for the content hash. The entry holds the series object,
# so a recycled id cannot alias a different tape while the hint is live.
# The series cache itself is keyed only by the digest.
_ACCEL_MAX = 64
_accel: list[tuple[object, int, bytes]] = []
_tape_slot: tuple | None = None


def cache_nbytes() -> int:
    """Bytes of arrays this process is keeping, not the source tapes."""
    with _LOCK:
        return _NBYTES


def cache_budget_bytes() -> int:
    return CACHE_BUDGET_BYTES


def clear_series_cache() -> None:
    """Drop series and the hash hint. The next batch rebuilds from the tape."""
    global _NBYTES, _tape_slot
    with _LOCK:
        _STORE.clear()
        _NBYTES = 0
        _accel.clear()
        _tape_slot = None


def _array_nbytes(value: array) -> int:
    return value.itemsize * len(value) + 64


def value_nbytes(value: object) -> int:
    if isinstance(value, array):
        return _array_nbytes(value)
    if isinstance(value, tuple):
        return sum(value_nbytes(part) for part in value)
    return 0


def cache_get(key: tuple):
    with _LOCK:
        hit = _STORE.get(key)
        if hit is None:
            return None
        _STORE.move_to_end(key)
        return hit[0]


def cache_put(key: tuple, value: object, nbytes: int | None = None):
    """Store ``value`` if it fits. Always returns ``value`` so a miss can be used."""
    global _NBYTES
    size = value_nbytes(value) if nbytes is None else int(nbytes)
    if size <= 0 or size > CACHE_BUDGET_BYTES:
        return value
    with _LOCK:
        old = _STORE.pop(key, None)
        if old is not None:
            _NBYTES -= old[1]
        while _STORE and _NBYTES + size > CACHE_BUDGET_BYTES:
            _, (_, evicted) = _STORE.popitem(last=False)
            _NBYTES -= evicted
        _STORE[key] = (value, size)
        _NBYTES += size
    return value


def fingerprint_series(values) -> bytes:
    """16-byte content hash. Repeated calls on the same object do not rehash."""
    n = len(values)
    for obj, length, digest in _accel:
        if obj is values and length == n:
            return digest
    if isinstance(values, array) and values.typecode == "d":
        raw = values.tobytes()
    else:
        raw = array("d", values).tobytes()
    digest = hashlib.blake2b(raw, digest_size=16).digest()
    _accel.append((values, n, digest))
    if len(_accel) > _ACCEL_MAX:
        del _accel[: len(_accel) - _ACCEL_MAX]
    return digest


def tape_digest(closes, highs, lows) -> bytes:
    """Content hash of one OHLC tape. Same lists reuse the digest."""
    global _tape_slot
    slot = _tape_slot
    if (
        slot is not None
        and slot[0] is closes
        and slot[1] is highs
        and slot[2] is lows
        and slot[4] == len(closes)
    ):
        return slot[3]
    hasher = hashlib.blake2b(digest_size=16)
    hasher.update(fingerprint_series(closes))
    hasher.update(fingerprint_series(highs))
    hasher.update(fingerprint_series(lows))
    digest = hasher.digest()
    _tape_slot = (closes, highs, lows, digest, len(closes))
    return digest
