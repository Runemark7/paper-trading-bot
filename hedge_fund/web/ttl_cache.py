"""Short TTL cache with single-flight builds.

Concurrent callers for the same key share one in-flight build. A hit
returns a deep copy so a caller can mutate its payload without poisoning
the next request.
"""
from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# Status and summary polls are 30s. A 12s TTL collapses a burst of tabs
# onto one build without serving a cycle-old book.
READ_CACHE_TTL_SECONDS = 12.0


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
