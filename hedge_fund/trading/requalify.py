"""Re-qualification lane: re-run current champions and old passes on today's gate.

Fail-once still holds for discovery. This is a separate queue. A requalify
result is written only to ``discovery_requalify.json``. It never touches
``discovery_log.json``, ``discovery_results.jsonl``, the tested index, or
``champions.json``. Nothing is admitted, retired, or culled here. A human
reads ``GET /api/discovery/requalify`` and decides.

Flow: a batch is enqueued (token-gated ``POST /api/discovery/requalify``,
or once automatically for ``REQUALIFY_AUTO_BATCH`` when the server starts). ``POST /api/discovery/claim`` hands queued requalify names out
before never-tested names. The worker evaluates them like any claimed name
with the unchanged OOS gate and posts the row to ingest. Ingest routes a
leased requalify name here instead of skipping it as already tested.

A lease that expires or is released goes back to the queue. After
``MAX_ATTEMPTS`` leases without a result the name is marked ``no_result``
so an unparseable legacy name cannot loop forever. Paper only.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from hedge_fund.paths import state_root
from hedge_fund.trading.constants import QUAL_N_WINDOWS
from hedge_fund.trading.store import paper_state_lock

DISCOVERY_REQUALIFY = "discovery_requalify.json"
# One automatic batch per id. Bump to re-run everything once after a deploy.
REQUALIFY_AUTO_BATCH = "gate23-20261008"
MAX_ATTEMPTS = 3
MAX_BATCH_NAMES = 2000
_BATCH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_RESULT_KEYS = (
    "qualified",
    "fail_reasons",
    "sharpe",
    "trades",
    "test_pnl",
    "train_pnl",
    "win_rate_pct",
    "bh_oos_pnl",
    "sma_stack_oos_pnl",
    "regimes_tested",
    "all_windows_nonneg",
    "ops_park",
    "tested_at",
    "worker_id",
    "data_end",
    "data_hash",
    "provenance",
    "gate",
)


def requalify_path() -> Path:
    return state_root() / DISCOVERY_REQUALIFY


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _empty() -> dict:
    return {"batches": [], "names": {}, "seeded": []}


def load_requalify_state() -> dict:
    path = requalify_path()
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    names = data.get("names") if isinstance(data.get("names"), dict) else {}
    batches = data.get("batches") if isinstance(data.get("batches"), list) else []
    seeded = data.get("seeded") if isinstance(data.get("seeded"), list) else []
    return {"batches": batches, "names": names, "seeded": [s for s in seeded if isinstance(s, str)]}


def save_requalify_state(state: dict) -> None:
    """Caller holds ``paper_state_lock('discovery')``."""
    path = requalify_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "batches": state.get("batches") or [],
        "names": state.get("names") or {},
        "seeded": state.get("seeded") or [],
        "paper_only": True,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def default_requalify_names() -> list[str]:
    """Active champions, graduated/rejected rows, then every qualified tested name."""
    from hedge_fund.trading.champions import load_graduated, load_pool
    from hedge_fund.trading.tested_index import load_tested_index

    out: list[str] = []
    seen: set[str] = set()

    def add(name: object) -> None:
        if isinstance(name, str) and name.strip() and name.strip() not in seen:
            seen.add(name.strip())
            out.append(name.strip())

    for row in load_pool().get("champions") or []:
        if isinstance(row, dict):
            add(row.get("name"))
    for row in load_graduated():
        if isinstance(row, dict):
            add(row.get("name"))
    for name, qualified in sorted(load_tested_index().items()):
        if qualified:
            add(name)
    return out


def _enqueue_unlocked(state: dict, batch_id: str, names: Iterable[str], now: datetime) -> dict:
    queued: list[str] = []
    for raw in names:
        if not isinstance(raw, str) or not raw.strip():
            continue
        name = raw.strip()
        prev = state["names"].get(name)
        if isinstance(prev, dict) and prev.get("batch_id") == batch_id:
            continue
        state["names"][name] = {
            "batch_id": batch_id,
            "status": "queued",
            "queued_at": _iso(now),
            "attempts": 0,
        }
        queued.append(name)
    state["batches"].append({
        "batch_id": batch_id,
        "created_at": _iso(now),
        "size": len(queued),
        "gate_n_windows": QUAL_N_WINDOWS,
    })
    return {"batch_id": batch_id, "queued": len(queued), "names": queued}


def require_batch_id(value: object) -> str:
    if not isinstance(value, str) or not _BATCH_ID_RE.match(value.strip()):
        raise ValueError("batch_id must be 1-64 letters, digits, '.', '_' or '-'")
    return value.strip()


def enqueue_requalify_batch(batch_id: object, names: object = None, *, now: datetime | None = None) -> dict:
    """Queue names for re-qualification. ``names`` omitted → default set. Never culls."""
    bid = require_batch_id(batch_id)
    if names is not None:
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValueError("names must be a list of strings")
        if len(names) > MAX_BATCH_NAMES:
            raise ValueError(f"at most {MAX_BATCH_NAMES} names per batch")
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with paper_state_lock("discovery"):
        wanted = list(names) if names is not None else default_requalify_names()
        state = load_requalify_state()
        out = _enqueue_unlocked(state, bid, wanted, clock)
        save_requalify_state(state)
    out.update({"ok": True, "paper_only": True})
    return out


def ensure_auto_batch_unlocked(now: datetime) -> bool:
    """Seed ``REQUALIFY_AUTO_BATCH`` once. Caller holds the discovery lock."""
    state = load_requalify_state()
    if REQUALIFY_AUTO_BATCH in state["seeded"]:
        return False
    names = default_requalify_names()
    _enqueue_unlocked(state, REQUALIFY_AUTO_BATCH, names, now)
    state["seeded"].append(REQUALIFY_AUTO_BATCH)
    save_requalify_state(state)
    return True


def start_requalify_autoseed() -> None:
    """Server startup: seed ``REQUALIFY_AUTO_BATCH`` once on a daemon thread.

    No token needed; the batch id makes it idempotent across restarts.
    """
    import logging
    import threading

    def run() -> None:
        try:
            with paper_state_lock("discovery"):
                if ensure_auto_batch_unlocked(datetime.now(timezone.utc)):
                    logging.getLogger(__name__).info(
                        "requalify: seeded batch %s", REQUALIFY_AUTO_BATCH
                    )
        except Exception:
            logging.getLogger(__name__).exception("requalify autoseed failed")

    threading.Thread(target=run, name="requalify-autoseed", daemon=True).start()


def _expire(state: dict, now: datetime) -> None:
    for meta in state["names"].values():
        if not isinstance(meta, dict) or meta.get("status") != "leased":
            continue
        exp = _parse(meta.get("expires_at"))
        if exp is None or exp <= now:
            meta["status"] = "queued"
            meta.pop("worker_id", None)
            meta.pop("expires_at", None)
            if int(meta.get("attempts") or 0) >= MAX_ATTEMPTS:
                meta["status"] = "no_result"


def lease_requalify_unlocked(worker_id: str, n: int, ttl_seconds: int, now: datetime) -> list[str]:
    """Lease up to ``n`` queued requalify names. Caller holds the discovery lock."""
    now = now.astimezone(timezone.utc)
    if not requalify_path().exists():
        return []
    state = load_requalify_state()
    _expire(state, now)
    chosen: list[str] = []
    if n > 0:
        expires = _iso(now + timedelta(seconds=int(ttl_seconds)))
        for name, meta in state["names"].items():
            if len(chosen) >= n:
                break
            if not isinstance(meta, dict) or meta.get("status") != "queued":
                continue
            meta["status"] = "leased"
            meta["worker_id"] = worker_id
            meta["claimed_at"] = _iso(now)
            meta["expires_at"] = expires
            meta["attempts"] = int(meta.get("attempts") or 0) + 1
            chosen.append(name)
    save_requalify_state(state)
    return chosen


def release_requalify_unlocked(worker_id: str, names: list[str] | None) -> list[str]:
    """Return this worker's requalify leases to the queue. Caller holds the lock."""
    path = requalify_path()
    if not path.exists():
        return []
    state = load_requalify_state()
    released: list[str] = []
    for name, meta in state["names"].items():
        if not isinstance(meta, dict) or meta.get("status") != "leased":
            continue
        if meta.get("worker_id") != worker_id:
            continue
        if names is not None and name not in names:
            continue
        meta.pop("worker_id", None)
        meta.pop("expires_at", None)
        meta["status"] = "no_result" if int(meta.get("attempts") or 0) >= MAX_ATTEMPTS else "queued"
        released.append(name)
    if released:
        save_requalify_state(state)
    return released


def pending_requalify_names() -> set[str]:
    """Names queued or leased for requalify. Read-only."""
    state = load_requalify_state()
    return {
        name
        for name, meta in state["names"].items()
        if isinstance(meta, dict) and meta.get("status") in ("queued", "leased")
    }


def record_requalify_unlocked(state: dict, rec: dict, now: datetime) -> bool:
    """Store one eval if ``rec['strategy']`` is queued/leased. Caller holds the lock."""
    name = rec.get("strategy")
    meta = state["names"].get(name) if isinstance(name, str) else None
    if not isinstance(meta, dict) or meta.get("status") not in ("queued", "leased"):
        return False
    result = {key: rec.get(key) for key in _RESULT_KEYS if key in rec}
    result["qualified"] = bool(rec.get("qualified"))
    result["gate_n_windows"] = QUAL_N_WINDOWS
    result["current_gate"] = rec.get("regimes_tested") == QUAL_N_WINDOWS
    meta["status"] = "done"
    meta["result"] = result
    meta["recorded_at"] = _iso(now)
    meta.pop("expires_at", None)
    return True


def _pnl_minus_bh(result: dict) -> float | None:
    try:
        return round(float(result.get("test_pnl")) - float(result.get("bh_oos_pnl")), 2)
    except (TypeError, ValueError):
        return None


def requalify_payload(batch_id: str | None = None) -> dict[str, Any]:
    """``GET /api/discovery/requalify``. Read-only; no lock."""
    from hedge_fund.trading.champions import load_graduated, load_pool, retired_names

    state = load_requalify_state()
    champs = {
        row.get("name")
        for row in (load_pool().get("champions") or [])
        if isinstance(row, dict)
    }
    grads = {row.get("name") for row in load_graduated() if isinstance(row, dict)}
    retired = retired_names()
    rows: list[dict] = []
    counts = {"queued": 0, "leased": 0, "done": 0, "no_result": 0, "pass": 0, "fail": 0}
    for name, meta in state["names"].items():
        if not isinstance(meta, dict):
            continue
        if batch_id and meta.get("batch_id") != batch_id:
            continue
        status = str(meta.get("status") or "queued")
        counts[status] = counts.get(status, 0) + 1
        result = meta.get("result") if isinstance(meta.get("result"), dict) else None
        row = {
            "strategy": name,
            "batch_id": meta.get("batch_id"),
            "status": status,
            "attempts": meta.get("attempts"),
            "role": (
                "champion" if name in champs
                else "graduated" if name in grads
                else "retired" if name in retired
                else "tested_pass"
            ),
        }
        if result is not None:
            row.update(result)
            row["pnl_minus_bh"] = _pnl_minus_bh(result)
            counts["pass" if result.get("qualified") else "fail"] += 1
        rows.append(row)
    rows.sort(key=lambda r: (r.get("pnl_minus_bh") is None, -(r.get("pnl_minus_bh") or 0.0)))
    return {
        "paper_only": True,
        "gate_n_windows": QUAL_N_WINDOWS,
        "auto_batch": REQUALIFY_AUTO_BATCH,
        "batches": state["batches"],
        "counts": counts,
        "total": len(rows),
        "results": rows,
        "note": (
            "Re-check only. Results never change discovery_log, fail-once, or the "
            "champion pool. Retiring is a separate, named one-shot batch "
            "(hedge_fund.trading.retire)."
        ),
    }
