"""Caches for /api/status and /api/summary.

Requests serve the last payload immediately. A background thread rebuilds
both about every ``READ_REBUILD_SECONDS``. Only a process with no payload
yet builds on the request. A failed rebuild keeps the previous payload.
"""
from __future__ import annotations

import copy
import sys
import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# Background rebuild cadence. Requests do not wait for this.
READ_REBUILD_SECONDS = 10.0
# Kept so older imports still resolve. Requests no longer block on a TTL.
READ_CACHE_TTL_SECONDS = READ_REBUILD_SECONDS


class _Flight:
    __slots__ = ("event", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.error: BaseException | None = None


class TtlSingleFlight:
    def __init__(self, ttl_seconds: float = READ_CACHE_TTL_SECONDS) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be >= 0")
        self.ttl = float(ttl_seconds)
        self._guard = threading.Lock()
        self._values: dict[str, tuple[float, object]] = {}
        self._flights: dict[str, _Flight] = {}

    def clear(self) -> None:
        with self._guard:
            self._values.clear()

    def get(self, key: str, builder: Callable[[], T]) -> T:
        while True:
            with self._guard:
                now = time.monotonic()
                cached = self._values.get(key)
                if cached is not None and (now - cached[0]) < self.ttl:
                    return copy.deepcopy(cached[1])  # type: ignore[return-value]
                flight = self._flights.get(key)
                if flight is None:
                    flight = _Flight()
                    self._flights[key] = flight
                    leader = True
                else:
                    leader = False
            if not leader:
                flight.event.wait()
                if flight.error is not None:
                    raise flight.error
                continue
            try:
                value = builder()
            except BaseException as exc:
                flight.error = exc
                with self._guard:
                    self._flights.pop(key, None)
                flight.event.set()
                raise
            with self._guard:
                self._values[key] = (time.monotonic(), value)
                self._flights.pop(key, None)
            flight.event.set()
            return copy.deepcopy(value)


class _Slot:
    __slots__ = ("value", "has", "built_at", "flight")

    def __init__(self) -> None:
        self.value: object | None = None
        self.has = False
        self.built_at: float | None = None
        self.flight: _Flight | None = None


class StaleCache:
    """Last-built payload per key, served even while a rebuild is in flight.

    ``get`` builds synchronously only when that key has nothing stored yet.
    ``refresh`` is single-flight and, on failure, keeps the previous value.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._slots: dict[str, _Slot] = {}

    def clear(self) -> None:
        with self._guard:
            self._slots.clear()

    def age_seconds(self, key: str) -> float | None:
        with self._guard:
            slot = self._slots.get(key)
            if slot is None or slot.built_at is None:
                return None
            return max(0.0, time.time() - slot.built_at)

    def get(self, key: str, builder: Callable[[], T]) -> T:
        with self._guard:
            slot = self._slots.get(key)
            if slot is not None and slot.has:
                return copy.deepcopy(slot.value)  # type: ignore[return-value]
            if slot is None:
                slot = _Slot()
                self._slots[key] = slot
            flight = slot.flight
            if flight is None:
                flight = _Flight()
                slot.flight = flight
                leader = True
            else:
                leader = False
        if not leader:
            flight.event.wait()
            with self._guard:
                slot = self._slots.get(key)
                if slot is not None and slot.has:
                    return copy.deepcopy(slot.value)  # type: ignore[return-value]
            if flight.error is not None:
                raise flight.error
            raise RuntimeError("read cache rebuild produced no payload")
        try:
            value = builder()
        except BaseException as exc:
            self._fail(key, flight, exc)
            raise
        self._store(key, flight, value)
        return copy.deepcopy(value)

    def refresh(self, key: str, builder: Callable[[], T]) -> None:
        """Rebuild off the request path. Keep the previous payload on failure."""
        with self._guard:
            slot = self._slots.get(key)
            if slot is not None and slot.flight is not None:
                return
            if slot is None:
                slot = _Slot()
                self._slots[key] = slot
            flight = _Flight()
            slot.flight = flight
        try:
            value = builder()
        except BaseException as exc:
            self._fail(key, flight, exc)
            if not self._has(key):
                raise
            return
        self._store(key, flight, value)

    def _has(self, key: str) -> bool:
        with self._guard:
            slot = self._slots.get(key)
            return bool(slot and slot.has)

    def _store(self, key: str, flight: _Flight, value: object) -> None:
        with self._guard:
            slot = self._slots.get(key)
            if slot is None:
                slot = _Slot()
                self._slots[key] = slot
            slot.value = value
            slot.has = True
            slot.built_at = time.time()
            if slot.flight is flight:
                slot.flight = None
        flight.event.set()

    def _fail(self, key: str, flight: _Flight, exc: BaseException) -> None:
        with self._guard:
            slot = self._slots.get(key)
            has = bool(slot and slot.has)
            payload = slot.value if slot is not None else None
            age = None
            if slot is not None and slot.built_at is not None:
                age = max(0.0, time.time() - slot.built_at)
            if slot is not None and slot.flight is flight:
                slot.flight = None
        flight.error = exc
        flight.event.set()
        if not has:
            return
        as_of = payload.get("as_of") if isinstance(payload, dict) else None
        age_s = "n/a" if age is None else f"{age:.1f}s"
        print(
            f"[read-cache] rebuild failed; serving previous as_of={as_of} age={age_s}: {exc}",
            file=sys.stderr,
            flush=True,
        )
