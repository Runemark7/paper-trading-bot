"""Sealed holdout. Discovery scoring must not observe these bars.

The boundary is fixed (Alexander, 2026-10-27): bars with
``HOLDOUT_START_MS <= ts < HOLDOUT_END_MS`` are the one-shot holdout.
Bars at or after ``HOLDOUT_END_MS`` are forward/live only.

Mint, densify, lift, and refill run inside :func:`scoring_purpose`.
Loading the shared tape or reading a bar timestamp in that scope raises
:class:`HoldoutLeak` when the timestamp is inside or after the holdout.
Walk-forward uses :func:`discovery_rows`, which drops those bars, then
:func:`assert_no_holdout` so a segment that still overlaps fails the
process rather than scoring them.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from hedge_fund.trading.constants import HOLDOUT_END_MS, HOLDOUT_START_MS

DISCOVERY_PURPOSES = frozenset({"walkforward", "mint", "densify", "lift", "refill"})
_PURPOSE: ContextVar[str | None] = ContextVar("holdout_scoring_purpose", default=None)


class HoldoutLeak(RuntimeError):
    """A discovery scoring path observed a sealed bar."""


def current_purpose() -> str | None:
    return _PURPOSE.get()


@contextmanager
def scoring_purpose(purpose: str):
    """Mark the current call as mint / densify / lift / refill / walk-forward."""
    if purpose not in DISCOVERY_PURPOSES:
        raise ValueError(f"unknown scoring purpose {purpose!r}")
    token = _PURPOSE.set(purpose)
    try:
        yield
    finally:
        _PURPOSE.reset(token)


def _ts_of(row: object) -> int | None:
    if isinstance(row, (list, tuple)) and row:
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return None
    return None


def bar_in_or_after_holdout(ts: int) -> bool:
    """True when ``ts`` is in the holdout or in the forward/live tail."""
    return int(ts) >= HOLDOUT_START_MS


def in_holdout_window(ts: int) -> bool:
    return HOLDOUT_START_MS <= int(ts) < HOLDOUT_END_MS


def assert_no_holdout(rows: object, *, purpose: str) -> None:
    """Raise when any bar is in the holdout or after it."""
    if purpose not in DISCOVERY_PURPOSES:
        raise ValueError(f"unknown scoring purpose {purpose!r}")
    if not isinstance(rows, list):
        return
    for row in rows:
        ts = _ts_of(row)
        if ts is not None and bar_in_or_after_holdout(ts):
            raise HoldoutLeak(
                f"{purpose} observed bar ts={ts} "
                f"(holdout is [{HOLDOUT_START_MS}, {HOLDOUT_END_MS}))"
            )


def discovery_rows(rows: list) -> list:
    """Bars strictly before the holdout. The sealed window is not returned."""
    kept = []
    for row in rows:
        ts = _ts_of(row)
        if ts is None or ts < HOLDOUT_START_MS:
            kept.append(row)
    return kept


def holdout_rows(rows: list) -> list:
    """Only the sealed window. Forward bars after the end are excluded."""
    return [row for row in rows if (ts := _ts_of(row)) is not None and in_holdout_window(ts)]


def read_bars(rows: list, *, purpose: str) -> list:
    """Bar read used by scoring.

    Discovery purposes receive only pre-holdout bars and raise if the
    caller handed them a sealed timestamp (they must cut with
    :func:`discovery_rows` first, and a missed cut is a bug).

    ``purpose="holdout"`` returns the sealed window only. It is refused
    while a mint/densify/lift/refill scope is active, so a feedback loop
    cannot pull the holdout in through this function.
    """
    outer = current_purpose()
    if purpose == "holdout":
        if outer in DISCOVERY_PURPOSES:
            raise HoldoutLeak(f"holdout read inside {outer}")
        return holdout_rows(rows)
    if purpose == "live":
        return list(rows)
    if purpose not in DISCOVERY_PURPOSES:
        raise ValueError(f"unknown scoring purpose {purpose!r}")
    assert_no_holdout(rows, purpose=purpose)
    return discovery_rows(rows)


def note_history_load(history: dict | None) -> None:
    """Called by the history loaders. A mint scope must not see sealed bars."""
    purpose = current_purpose()
    if purpose not in DISCOVERY_PURPOSES or not isinstance(history, dict):
        return
    for rows in history.values():
        if isinstance(rows, list):
            assert_no_holdout(rows, purpose=purpose)


def seal(purpose: str):
    """Decorator: run ``fn`` inside :func:`scoring_purpose`."""

    def deco(fn):
        def wrapper(*args, **kwargs):
            with scoring_purpose(purpose):
                return fn(*args, **kwargs)

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        wrapper.__wrapped__ = fn
        return wrapper

    return deco
