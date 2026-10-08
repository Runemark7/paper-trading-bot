"""Central discovery claim/lease queue on prod paper state.

Workers used to plan batches locally from a bootstrapped copy of the log,
so two hosts evaluated the same names. Prod is the source of truth:
``POST /api/discovery/claim`` leases never-tested names, ingest of a result
clears that lease, and a dead worker's names return when the lease expires.

Topology (do not add a second replica without a new lock):
``paperbot-backend`` is ``replicas: 1`` with ``strategy: Recreate`` and a
ReadWriteOnce PVC. The pod runs web (ThreadingHTTPServer), heartbeat, and
cycle against that volume. ``paper_state_lock('discovery')`` is an in-process
threading lock plus ``fcntl.flock`` so claim / ingest / release read-modify-write
``discovery_leases.json`` atomically across those threads and processes.
A second replica cannot mount the RWO volume, so flock is sufficient.

Lease TTL is about 2× batch time. One wave is bounded by
``DISCOVERY_EVAL_TIMEOUT_SECONDS`` (default 600). ``parallel`` is the worker
pool size; waves = ceil(n / parallel). TTL = 2 × timeout × waves, and never
shorter than 2 × the per-name backstop. Timeout 0 (disabled) still uses the
600s default so a lease cannot live forever.

OOS gates are not involved here. Fail-once: a name in the durable
tested-name index (not the capped display log) is never leased again.
When the recipe cannot fill a claim, densify mints around qualified
passes. If eligible work drops below twice the active lease capacity,
claim tops the queue up before handing names out.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from hedge_fund.paths import state_root
from hedge_fund.trading.champions import load_graduated, load_pool, retired_names
from hedge_fund.trading.constants import DISCOVERY_REFILL_BATCH_SIZE
from hedge_fund.trading.discovery import (
    load_discovery_log,
    parse_tested_at,
    prioritize_leftovers,
    tested_discovery_names,
)
from hedge_fund.trading.discovery_guard import (
    DEFAULT_EVAL_TIMEOUT_SECONDS,
    eval_timeout_seconds,
)
from hedge_fund.trading.farm import (
    FARM_HEARTBEAT_STALE_SECONDS,
    apply_heartbeat_unlocked,
    farm_enabled_unlocked,
)
from hedge_fund.trading.densify import next_densify_batch
from hedge_fund.trading.mint_quality import (
    canonical_key_set,
    canonical_name,
    mint_block_reason,
    record_untested_mint_skips,
)
from hedge_fund.trading.refill import (
    append_extended_batch,
    discovery_universe,
    next_refill_batch,
)
from hedge_fund.trading.store import paper_state_lock
from hedge_fund.trading.tested_index import ensure_tested_index
from hedge_fund.trading.universe import untested_candidates

logger = logging.getLogger(__name__)

DISCOVERY_LEASES = "discovery_leases.json"
DISCOVERY_REFILL_STATUS = "discovery_refill.json"
MAX_CLAIM = 64
LEASE_TTL_FACTOR = 2
# Top up when never-tested work (including names already leased) falls
# below this multiple of active lease capacity.
LEASE_WATERMARK_FACTOR = 2
WORKER_RETENTION = timedelta(days=7)
_WORKER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def leases_path() -> Path:
    return state_root() / DISCOVERY_LEASES


def refill_status_path() -> Path:
    return state_root() / DISCOVERY_REFILL_STATUS


def load_refill_status() -> dict[str, Any]:
    """Last claim refill. Missing file → recipe, nothing generated, not exhausted."""
    default = {
        "source": "recipe",
        "generated_last": 0,
        "exhausted": False,
        "strategy": "recipe_order",
    }
    path = refill_status_path()
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return dict(default)
    if not isinstance(data, dict):
        return dict(default)
    source = data.get("source")
    if source not in ("recipe", "densify"):
        source = "recipe"
    try:
        generated = int(data.get("generated_last") or 0)
    except (TypeError, ValueError):
        generated = 0
    strategy = data.get("strategy")
    if strategy not in ("lift_ucb", "recipe_order"):
        strategy = "recipe_order"
    return {
        "source": source,
        "generated_last": generated,
        "exhausted": bool(data.get("exhausted")),
        "strategy": strategy,
    }


def save_refill_status(status: dict) -> None:
    """Caller holds ``paper_state_lock('discovery')``."""
    path = refill_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    source = status.get("source")
    if source not in ("recipe", "densify"):
        source = "recipe"
    strategy = status.get("strategy")
    if strategy not in ("lift_ucb", "recipe_order"):
        strategy = "recipe_order"
    payload = {
        "source": source,
        "generated_last": int(status.get("generated_last") or 0),
        "exhausted": bool(status.get("exhausted")),
        "strategy": strategy,
        "paper_only": True,
        "updated_at": _iso(datetime.now(timezone.utc)),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def _empty_state() -> dict:
    return {"leases": {}, "workers": {}}


def load_lease_state() -> dict:
    path = leases_path()
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    leases = data.get("leases")
    workers = data.get("workers")
    if not isinstance(leases, dict):
        leases = {}
    if not isinstance(workers, dict):
        workers = {}
    return {"leases": leases, "workers": workers}


def save_lease_state(state: dict) -> None:
    path = leases_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "leases": state.get("leases") if isinstance(state.get("leases"), dict) else {},
        "workers": state.get("workers") if isinstance(state.get("workers"), dict) else {},
        "paper_only": True,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def require_worker_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("worker_id is required")
    wid = value.strip()
    if not _WORKER_ID_RE.match(wid):
        raise ValueError("worker_id must be 1–128 letters, digits, '.', '_' or '-'")
    return wid


def require_n(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"n must be an integer from 1 to {MAX_CLAIM}")
    if value < 1 or value > MAX_CLAIM:
        raise ValueError(f"n must be an integer from 1 to {MAX_CLAIM}")
    return value


def require_parallel(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("parallel must be a positive integer")
    if value < 1 or value > 256:
        raise ValueError("parallel must be a positive integer")
    return value


def _status(value: object, default: str) -> str:
    if not isinstance(value, str):
        return default
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch in "-_")[:32]
    return cleaned or default


def lease_ttl_seconds(n: int, parallel: int | None = None) -> int:
    """~2× batch time. One wave lasts at most one eval timeout."""
    per = eval_timeout_seconds()
    if per <= 0:
        per = DEFAULT_EVAL_TIMEOUT_SECONDS
    count = max(1, int(n))
    if parallel is None or parallel <= 0:
        waves = 1
    else:
        width = max(1, int(parallel))
        waves = max(1, (count + width - 1) // width)
    return LEASE_TTL_FACTOR * per * waves


def _blocked_names(log: list[dict]) -> set[str]:
    st = load_pool()
    grads = load_graduated()
    blocked = {c["name"] for c in (st.get("champions") or []) if isinstance(c, dict) and c.get("name")}
    blocked |= {g["name"] for g in grads if isinstance(g, dict) and g.get("name")}
    blocked |= retired_names()
    blocked |= tested_discovery_names(log)
    return blocked


def _drop_expired_and_tested(state: dict, now: datetime, tested: set[str]) -> int:
    """Remove expired leases and any lease on an already-tested name.

    Returns how many expired (not tested) leases were dropped.
    """
    leases: dict = state["leases"]
    expired = 0
    for name in list(leases):
        if name in tested:
            del leases[name]
            continue
        exp = parse_tested_at(leases[name].get("expires_at") if isinstance(leases[name], dict) else None)
        if exp is None or exp <= now:
            del leases[name]
            expired += 1
    return expired


def _touch(state: dict, worker_id: str, status: str, now: datetime) -> None:
    workers = state.setdefault("workers", {})
    prev = workers.get(worker_id)
    if not isinstance(prev, dict):
        prev = {}
    prev["worker_id"] = worker_id
    prev["last_seen"] = _iso(now)
    prev["status"] = status
    workers[worker_id] = prev


def _prune_workers(state: dict, now: datetime) -> None:
    active_ids = set()
    for lease in state["leases"].values():
        if isinstance(lease, dict) and isinstance(lease.get("worker_id"), str):
            active_ids.add(lease["worker_id"])
    cutoff = now - WORKER_RETENTION
    workers = state["workers"]
    for wid in list(workers):
        if wid in active_ids:
            continue
        meta = workers.get(wid)
        ts = parse_tested_at(meta.get("last_seen") if isinstance(meta, dict) else None)
        if ts is not None and ts < cutoff:
            del workers[wid]


def _mint_unlocked(taken: set[str], n: int) -> tuple[list[str], dict]:
    """Recipe first. Densify only the shortfall. Logs if nothing can be minted.

    Uses the published lift snapshot. Does not fit and does not schedule a
    fit; that runs on a background thread at most once every five minutes.
    The OOS gate is not involved.
    """
    from hedge_fund.trading.atom_lift import lift_model_for_mint
    from hedge_fund.trading.discovery_results import load_seed_view
    from hedge_fund.trading.tested_index import load_tested_metrics

    model = lift_model_for_mint()
    metrics = load_tested_metrics()
    # Slim rows published by ingest/backfill. Empty still counts as
    # "results were consulted", so old 8-window passes are not seeds
    # just because they are qualified. Does not scan the jsonl.
    results = load_seed_view()
    want = max(0, int(n))
    recipe = next_refill_batch(taken_names=taken, n=want, lift=model) if want else []
    recipe = [name for name in recipe if not mint_block_reason(name)]
    names = list(recipe)
    exhausted = False
    source = "recipe"
    if len(names) < want:
        more, ran_out = next_densify_batch(
            taken_names=set(taken) | set(names),
            n=want - len(names),
            lift=model,
            metrics=metrics,
            results=results,
        )
        more = [name for name in more if not mint_block_reason(name)]
        names.extend(more)
        source = "densify"
        exhausted = bool(ran_out) and len(names) < want
        if exhausted:
            logger.warning(
                "discovery refill exhausted: wanted %s names, recipe produced %s, densify produced %s",
                want,
                len(recipe),
                len(more),
            )
    return names, {
        "source": source,
        "generated_last": len(names),
        "exhausted": exhausted,
        "strategy": model.strategy,
    }


def _claimable_names(state: dict, n: int) -> tuple[list[str], list[str], dict]:
    """Never-tested, not-leased names.

    Refill when that pool cannot cover ``n`` or when never-tested work
    (including active leases) is below ``2 ×`` lease capacity. The recipe
    fills first; densify covers a shortfall. Existing claimable names stay
    in front of anything just minted.
    """
    log = load_discovery_log()
    blocked = _blocked_names(log)
    active = set(state["leases"])
    universe = discovery_universe()
    # Queued violators become skipped, not tested and not failed.
    record_untested_mint_skips(universe, blocked)
    leftovers = untested_candidates(blocked, universe)
    eligible = prioritize_leftovers(leftovers, log)
    # Canonical form of an already-tested stack is not leased again.
    # A later permutation in this same queue is skipped the same way.
    seen_canon = canonical_key_set(blocked)
    claimable: list[str] = []
    for name in eligible:
        if name in active or mint_block_reason(name, seen_canon):
            continue
        claimable.append(name)
        key = canonical_name(name)
        if key:
            seen_canon.add(key)
    pool = len(claimable) + len(active)
    capacity = max(len(active), int(n), 1)
    watermark = LEASE_WATERMARK_FACTOR * capacity
    refilled: list[str] = []
    meta = load_refill_status()
    need_fill = int(n) > 0 and (len(claimable) < int(n) or pool < watermark)
    if need_fill:
        target = max(int(n), watermark, len(claimable) + DISCOVERY_REFILL_BATCH_SIZE)
        want = max(0, target - len(claimable))
        taken = set(universe) | blocked | active | set(claimable)
        added, meta = _mint_unlocked(taken, want)
        if added:
            append_extended_batch(added)
            refilled = list(added)
            for name in added:
                if name in active or name in blocked or mint_block_reason(name, seen_canon):
                    continue
                claimable.append(name)
                key = canonical_name(name)
                if key:
                    seen_canon.add(key)
        save_refill_status(meta)
    return claimable, refilled, meta


def _claim_unlocked(
    worker_id: str,
    n: int,
    *,
    parallel: int | None,
    now: datetime,
) -> dict:
    now = now.astimezone(timezone.utc)
    ensure_tested_index()
    state = load_lease_state()
    log = load_discovery_log()
    tested = tested_discovery_names(log)
    _drop_expired_and_tested(state, now, tested)
    ttl = lease_ttl_seconds(n, parallel)
    if not farm_enabled_unlocked():
        _touch(state, worker_id, "paused", now)
        _prune_workers(state, now)
        save_lease_state(state)
        apply_heartbeat_unlocked("paused")
        paused_refill = load_refill_status()
        return {
            "ok": True,
            "paper_only": True,
            "worker_id": worker_id,
            "names": [],
            "n": n,
            "paused": True,
            "refilled": [],
            "refill": paused_refill,
            "ttl_seconds": ttl,
            "expires_at": None,
            "claimed_at": _iso(now),
        }
    # Requalify lane first: re-checks of champions / old passes on the
    # current gate. Kept in discovery_requalify.json, not in these leases.
    from hedge_fund.trading.requalify import lease_requalify_unlocked

    requal = lease_requalify_unlocked(worker_id, n, ttl, now)
    rest = n - len(requal)
    if rest > 0:
        claimable, refilled, refill_meta = _claimable_names(state, rest)
    else:
        claimable, refilled, refill_meta = [], [], load_refill_status()
    expires = now + timedelta(seconds=ttl)
    fresh = claimable[:max(0, rest)]
    chosen = list(requal) + fresh
    for name in fresh:
        state["leases"][name] = {
            "worker_id": worker_id,
            "claimed_at": _iso(now),
            "expires_at": _iso(expires),
        }
    status = "running" if chosen else "idle"
    _touch(state, worker_id, status, now)
    _prune_workers(state, now)
    save_lease_state(state)
    apply_heartbeat_unlocked(status)
    return {
        "ok": True,
        "paper_only": True,
        "worker_id": worker_id,
        "names": chosen,
        "n": n,
        "paused": False,
        "refilled": refilled,
        "refill": refill_meta,
        "ttl_seconds": ttl,
        "expires_at": _iso(expires) if chosen else None,
        "claimed_at": _iso(now),
        "leases_active": len(state["leases"]),
        "requalify": list(requal),
    }


def claim_discovery_batch(
    worker_id: object,
    n: object,
    *,
    parallel: object = None,
    now: datetime | None = None,
) -> dict:
    """Lease up to ``n`` names for ``worker_id``. Holds the paper-state lock."""
    wid = require_worker_id(worker_id)
    count = require_n(n)
    par = require_parallel(parallel)
    clock = now or datetime.now(timezone.utc)
    with paper_state_lock("discovery"):
        return _claim_unlocked(wid, count, parallel=par, now=clock)


def _release_unlocked(
    worker_id: str,
    names: list[str] | None,
    *,
    status: str,
    now: datetime,
) -> dict:
    now = now.astimezone(timezone.utc)
    state = load_lease_state()
    released: list[str] = []
    leases: dict = state["leases"]
    for name in list(leases):
        lease = leases.get(name)
        if not isinstance(lease, dict) or lease.get("worker_id") != worker_id:
            continue
        if names is not None and name not in names:
            continue
        del leases[name]
        released.append(name)
    from hedge_fund.trading.requalify import release_requalify_unlocked

    released.extend(release_requalify_unlocked(worker_id, names))
    _touch(state, worker_id, status, now)
    _prune_workers(state, now)
    save_lease_state(state)
    apply_heartbeat_unlocked(status)
    return {
        "ok": True,
        "paper_only": True,
        "worker_id": worker_id,
        "released": released,
        "status": status,
    }


def release_discovery_leases(
    worker_id: object,
    names: object = None,
    *,
    status: object = None,
    now: datetime | None = None,
) -> dict:
    """Drop this worker's leases. ``names`` omitted releases every lease it holds."""
    wid = require_worker_id(worker_id)
    wanted: list[str] | None
    if names is None:
        wanted = None
    elif isinstance(names, list) and all(isinstance(n, str) for n in names):
        wanted = list(names)
    else:
        raise ValueError("names must be a list of strings")
    clock = now or datetime.now(timezone.utc)
    with paper_state_lock("discovery"):
        return _release_unlocked(wid, wanted, status=_status(status, "idle"), now=clock)


def clear_leases_unlocked(names: Iterable[str]) -> list[str]:
    """Drop leases for these names, any worker. Caller holds the paper-state lock."""
    wanted = {n for n in names if isinstance(n, str) and n}
    if not wanted:
        return []
    state = load_lease_state()
    released: list[str] = []
    leases: dict = state["leases"]
    for name in wanted:
        if name in leases:
            del leases[name]
            released.append(name)
    if released:
        save_lease_state(state)
    return released


def touch_worker_unlocked(worker_id: object, status: object = None, *, now: datetime | None = None) -> None:
    """Record a per-worker heartbeat. Invalid ids are ignored. Caller holds the lock."""
    if not isinstance(worker_id, str) or not _WORKER_ID_RE.match(worker_id.strip()):
        return
    wid = worker_id.strip()
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = load_lease_state()
    _touch(state, wid, _status(status, "running"), clock)
    _prune_workers(state, clock)
    save_lease_state(state)


def _active_map(state: dict, now: datetime) -> tuple[dict[str, dict], int]:
    active: dict[str, dict] = {}
    expired = 0
    for name, lease in state["leases"].items():
        if not isinstance(name, str) or not isinstance(lease, dict):
            continue
        exp = parse_tested_at(lease.get("expires_at") if isinstance(lease.get("expires_at"), str) else None)
        if exp is None or exp <= now:
            expired += 1
            continue
        active[name] = lease
    return active, expired


def lease_snapshot(now: datetime | None = None) -> dict[str, Any]:
    """Read-only view for ``GET /api/discovery/summary``. Does not purge the file."""
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = load_lease_state()
    active, expired = _active_map(state, clock)
    by_worker: dict[str, list[str]] = {}
    oldest: datetime | None = None
    oldest_iso: str | None = None
    for name, lease in active.items():
        wid = lease.get("worker_id") if isinstance(lease.get("worker_id"), str) else ""
        by_worker.setdefault(wid or "?", []).append(name)
        claimed = parse_tested_at(lease.get("claimed_at") if isinstance(lease.get("claimed_at"), str) else None)
        if claimed is not None and (oldest is None or claimed < oldest):
            oldest = claimed
            oldest_iso = lease.get("claimed_at")
    ids = set(state["workers"]) | set(by_worker)
    rows: list[dict] = []
    for wid in sorted(i for i in ids if i):
        meta = state["workers"].get(wid)
        if not isinstance(meta, dict):
            meta = {}
        names = sorted(by_worker.get(wid, []))
        last = meta.get("last_seen") if isinstance(meta.get("last_seen"), str) else None
        ts = parse_tested_at(last)
        age = (clock - ts).total_seconds() if ts else None
        rows.append({
            "worker_id": wid,
            "last_seen": last,
            "last_seen_age_seconds": age,
            "status": meta.get("status"),
            "seen": bool(age is not None and age <= FARM_HEARTBEAT_STALE_SECONDS),
            "in_flight": names,
            "lease_count": len(names),
        })
    return {
        "active_names": sorted(active),
        "expired": expired,
        "workers": rows,
        "oldest_claimed_at": oldest_iso,
    }
