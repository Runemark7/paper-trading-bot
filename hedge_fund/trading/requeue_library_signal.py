"""Re-lease library names whose stored row predates a signal correction.

``lib.td_cd``, ``lib.pierce``, and ``lib.soldiers`` each finished the
2017-2026 daily tape with no trade. The catalog signals were corrected.
A library claim leases those canonical names again until the newest
discovery-log row carries ``signal_revision``. One accepted row puts the
name back under fail-once. The old row stays. Nothing here deletes a
row or edits a paper account. A corrected pass is seated by ingest, the
same as any other canonical ``lib.*`` gate-v7b pass.

There is no apply step. The queue is the log. A name with no row is
untested, so the ordinary claim already leases it, and the ingest stamps
the revision on that first row. Random mode does not lease this queue.
"""
from __future__ import annotations

REVISION = "library-signal-td-pierce-soldiers-20261101"
CORRECTED_NAMES = ("lib.td_cd", "lib.pierce", "lib.soldiers")
RETEST_REASON = "library_signal_corrected"
BATCH_ID = REVISION

_cache: dict = {"fp": None, "pending": ()}


def is_signal_correction(name: object) -> bool:
    return isinstance(name, str) and name in CORRECTED_NAMES


def _pending_from_log() -> tuple[str, ...]:
    from hedge_fund.paths import state_root
    from hedge_fund.trading.discovery import discovery_log_fingerprint, load_discovery_log

    fp = (str(state_root()), discovery_log_fingerprint())
    if _cache.get("fp") == fp:
        return _cache["pending"]
    latest: dict[str, dict] = {}
    for row in load_discovery_log():
        if not isinstance(row, dict):
            continue
        name = row.get("strategy")
        if name in CORRECTED_NAMES and name not in latest:
            latest[name] = row
    pending = tuple(
        name
        for name in CORRECTED_NAMES
        if name in latest and latest[name].get("signal_revision") != REVISION
    )
    _cache["fp"] = fp
    _cache["pending"] = pending
    return pending


def pending_signal_correction_names() -> list[str]:
    """Corrected names whose newest log row is not this revision.

    Empty when the name has never been stored: an untested name is already
    claimable. Empty once the newest row carries ``signal_revision``.
    """
    return list(_pending_from_log())


def signal_correction_pending(name: str) -> bool:
    return name in _pending_from_log()


def stamp_signal_correction(row: dict) -> None:
    """Mark a fresh row as the corrected-signal retest. Does not qualify it."""
    row["signal_revision"] = REVISION
    row["retest"] = True
    row["retest_reason"] = RETEST_REASON
    row["retest_batch"] = BATCH_ID


def signal_correction_report() -> dict:
    """Who a library claim will re-lease. Does not write."""
    names = pending_signal_correction_names()
    return {
        "ok": True,
        "paper_only": True,
        "dry_run": True,
        "applied": False,
        "batch_id": BATCH_ID,
        "reason": RETEST_REASON,
        "signal_revision": REVISION,
        "count": len(names),
        "names": names,
        "admitted": [],
        "retired": [],
        "rows_deleted": 0,
        "note": (
            "No apply step. A library claim leases these canonical names "
            "ahead of never-tested names while the newest discovery-log row "
            "lacks signal_revision. One stored row puts the name back under "
            "fail-once. The old row is not deleted. A corrected pass is seated "
            "on ingest like any other canonical lib.* gate-v7b pass. Random "
            "mode does not lease this queue."
        ),
    }
