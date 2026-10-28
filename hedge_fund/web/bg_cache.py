"""Single-flight, rate-limited background cache for expensive read endpoints.

A request never runs the builder on its own thread. It gets the last
payload at once (flagged stale when a rebuild is due or running) and, at
most once per ``min_interval`` seconds per key, a daemon thread rebuilds
it. A key with no payload yet answers ``computing`` so the handler can
reply 202 instead of holding a worker thread past the proxy timeout.

Stored payloads are shared, not copied. Callers serialize them and must
not mutate them.
"""
from __future__ import annotations

import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

READY = "ready"
STALE = "stale"
COMPUTING = "computing"


@dataclass(frozen=True)
class CacheRead:
    value: Any
    status: str
    built_at: float | None
    building: bool
    error: str | None = None

    @property
    def age_seconds(self) -> float | None:
        if self.built_at is None:
            return None
        return max(0.0, time.time() - self.built_at)


class _Slot:
    __slots__ = ("value", "has", "built_at", "attempted_at", "token", "building", "done", "error")

    def __init__(self) -> None:
        self.value: Any = None
        self.has = False
        self.built_at: float | None = None
        self.attempted_at: float | None = None
        self.token: object = None
        self.building = False
        self.done = threading.Event()
        self.error: str | None = None


class BackgroundCache:
    def __init__(self, name: str, *, min_interval: float, max_keys: int = 16) -> None:
        if min_interval < 0:
            raise ValueError("min_interval must be >= 0")
        self.name = name
        self.min_interval = float(min_interval)
        self.max_keys = max(1, int(max_keys))
        self._guard = threading.Lock()
        self._slots: OrderedDict[object, _Slot] = OrderedDict()
        self.builds_started = 0

    def clear(self) -> None:
        with self._guard:
            self._slots.clear()

    def _slot(self, key: object) -> _Slot:
        slot = self._slots.get(key)
        if slot is None:
            slot = _Slot()
            self._slots[key] = slot
            self._evict()
        else:
            self._slots.move_to_end(key)
        return slot

    def _evict(self) -> None:
        while len(self._slots) > self.max_keys:
            for old_key, old in self._slots.items():
                if not old.building:
                    del self._slots[old_key]
                    break
            else:
                return

    def _due(self, slot: _Slot, token: object, now: float) -> bool:
        if slot.building:
            return False
        if slot.attempted_at is not None and now - slot.attempted_at < self.min_interval:
            return False
        if not slot.has:
            return True
        return token is None or token != slot.token

    def get(
        self,
        key: object,
        builder: Callable[[], Any],
        *,
        token: Callable[[], object] | None = None,
        wait_cold: float = 0.0,
    ) -> CacheRead:
        """Last payload for ``key``; schedules at most one rebuild per interval.

        ``token`` (cheap, e.g. file stats) skips a rebuild when inputs did not
        change. ``wait_cold`` lets a key with no payload yet wait that long
        for the in-flight build before answering ``computing``.
        """
        now = time.monotonic()
        current = token() if token is not None else None
        with self._guard:
            slot = self._slot(key)
            if self._due(slot, current, now):
                slot.building = True
                slot.attempted_at = now
                slot.done = threading.Event()
                self.builds_started += 1
                done = slot.done
                threading.Thread(
                    target=self._run,
                    args=(key, slot, builder, current, done),
                    name=f"bgcache-{self.name}",
                    daemon=True,
                ).start()
            done = slot.done
            cold = not slot.has
        if cold and wait_cold > 0:
            done.wait(wait_cold)
        with self._guard:
            return self._read(slot)

    def _read(self, slot: _Slot) -> CacheRead:
        if not slot.has:
            return CacheRead(None, COMPUTING, None, slot.building, slot.error)
        status = STALE if slot.building or slot.error else READY
        return CacheRead(slot.value, status, slot.built_at, slot.building, slot.error)

    def _run(self, key: object, slot: _Slot, builder: Callable[[], Any], token: object, done: threading.Event) -> None:
        try:
            value = builder()
        except BaseException as exc:  # noqa: BLE001 — keep the last payload on any failure
            with self._guard:
                slot.building = False
                slot.error = str(exc) or type(exc).__name__
            print(f"[bg-cache:{self.name}] rebuild failed; keeping previous: {exc}", file=sys.stderr, flush=True)
        else:
            with self._guard:
                slot.value = value
                slot.has = True
                slot.built_at = time.time()
                slot.token = token
                slot.building = False
                slot.error = None
        finally:
            done.set()

    def wait_idle(self, key: object, timeout: float = 10.0) -> bool:
        """Test helper: block until the key's in-flight build (if any) finishes."""
        with self._guard:
            slot = self._slots.get(key)
            done = slot.done if slot is not None else None
        return True if done is None else done.wait(timeout)


def computing_body(what: str, **extra: Any) -> dict:
    body = {
        "status": COMPUTING,
        "paper_only": True,
        "message": f"{what} is being computed in the background. Try again shortly.",
    }
    body.update(extra)
    return body
