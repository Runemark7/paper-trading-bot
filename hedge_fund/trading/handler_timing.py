"""Per-request timings for discovery claim and ingest.

``handler_ms`` stays the total. The breakdown splits that into time
blocked on ``paper_state_lock`` (lock wait), time in JSON and file
reads/writes (io), and the rest (work). Lock hold is reported as well
so a multi-second handler shows whether it was waiting or working.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

_local = threading.local()


class HandlerFrame:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.lock_wait = 0.0
        self.lock_hold = 0.0
        self.io = 0.0
        self.steps: list[tuple[str, float]] = []
        self._step_t = self.t0

    def step(self, label: str) -> None:
        now = time.perf_counter()
        self.steps.append((label, (now - self._step_t) * 1000.0))
        self._step_t = now

    def snapshot(self, total_ms: float | None = None) -> dict:
        total = (
            (time.perf_counter() - self.t0) * 1000.0
            if total_ms is None
            else float(total_ms)
        )
        wait = self.lock_wait * 1000.0
        io = self.io * 1000.0
        hold = self.lock_hold * 1000.0
        work = total - wait - io
        if work < 0:
            work = 0.0
        steps = {label: round(ms, 1) for label, ms in self.steps}
        return {
            "total": round(total, 1),
            "lock_wait": round(wait, 1),
            "io": round(io, 1),
            "work": round(work, 1),
            "lock_hold": round(hold, 1),
            "steps": steps,
        }


def current_frame() -> HandlerFrame | None:
    stack = getattr(_local, "stack", None)
    if not stack:
        return None
    return stack[-1]


def _frames() -> list[HandlerFrame]:
    stack = getattr(_local, "stack", None)
    return list(stack) if stack else []


@contextmanager
def handler_frame():
    stack = getattr(_local, "stack", None)
    if stack is None:
        stack = []
        _local.stack = stack
    frame = HandlerFrame()
    stack.append(frame)
    try:
        yield frame
    finally:
        stack.pop()
        if not stack:
            _local.io_depth = 0


def add_lock_wait(seconds: float) -> None:
    for frame in _frames():
        frame.lock_wait += seconds


def add_lock_hold(seconds: float) -> None:
    for frame in _frames():
        frame.lock_hold += seconds


def add_io(seconds: float) -> None:
    for frame in _frames():
        frame.io += seconds


@contextmanager
def io_timer():
    """Time a JSON or file read/write. Nested timers count once."""
    depth = getattr(_local, "io_depth", 0)
    _local.io_depth = depth + 1
    started = time.perf_counter()
    try:
        yield
    finally:
        _local.io_depth = depth
        if depth == 0:
            add_io(time.perf_counter() - started)
