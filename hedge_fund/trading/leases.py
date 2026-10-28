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

Lease expiry is per name and follows that worker's heartbeat. A claim
sets ``expires_at`` one heartbeat grace out
(``FARM_HEARTBEAT_STALE_SECONDS``, 900s). A later heartbeat from the
same worker, running or idle, pushes each of its leases out by another
grace. A name the worker reports in ``heartbeat.evaluating`` records
``started_at`` on first sight and is not extended past
``started_at + 2 × DISCOVERY_EVAL_TIMEOUT_SECONDS`` (default 1200s;
timeout 0 still uses the 600s backstop). A paused heartbeat does not
extend. A worker that goes offline therefore returns its names about
15 minutes after its last heartbeat, including names it had not
started. Batch length does not scale the lease: 16 names are not
held for ``16 × 2 × 600`` seconds.

OOS gates are not involved here. Fail-once: a name in the durable
tested-name index (not the capped display log) is never leased again,
except one pending discovery-window buy-and-hold retest. That lane
is leased after requalify and before leftovers or a mint.

When the unleased queue cannot fill this claim, the claim mints only
enough names to fill it, and at most the old handful of 16 when that
is the whole top-up. A thin queue (untested eligible below
``max(cap, 4 × recent leased lanes)``) is topped up on a daemon thread
after the claim releases the paper-state lock, in batches up to 64.
The buffer's ``mix_refill`` runs outside the paper-state lock so a
claim or an ingest is not stuck behind that CPU. The lock is held
again only to drop names that were taken while the mint ran and to
append the rest. Bans and the holdout seal on ``mix_refill`` /
``next_refill_batch`` are unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
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
from hedge_fund.trading.tested_index import ensure_tested_index, load_tested_index
from hedge_fund.trading.universe import untested_candidates

logger = logging.getLogger(__name__)

DISCOVERY_LEASES = "discovery_leases.json"
DISCOVERY_REFILL_STATUS = "discovery_refill.json"
MAX_CLAIM = 64
LEASE_TTL_FACTOR = 2
# Old claim-path watermark (2 × lease capacity, counting names already
# leased). The buffer below replaced it. Kept so the old name still
# resolves; claim no longer mints from it.
LEASE_WATERMARK_FACTOR = 2
# Top up when unleased untested names are below max(cap, this × lanes).
REFILL_LANE_MULTIPLE = 4
# One background mint. The claim itself never asks for more than the
# recipe handful (DISCOVERY_REFILL_BATCH_SIZE) except the names it
# still needs to fill ``n``.
REFILL_BATCH_MAX = 64
WORKER_RETENTION = timedelta(days=7)
_buffer_guard = threading.Lock()
_buffer_running = False
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
        "random": 0,
        "paired": 0,
        "exploit": 0,
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
    if source not in ("recipe", "densify", "random", "paired", "mixed"):
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
        "random": _nonneg(data.get("random")),
        "paired": _nonneg(data.get("paired")),
        "exploit": _nonneg(data.get("exploit")),
    }


def _nonneg(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def save_refill_status(status: dict) -> None:
    """Caller holds ``paper_state_lock('discovery')``."""
    path = refill_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    source = status.get("source")
    if source not in ("recipe", "densify", "random", "paired", "mixed"):
        source = "recipe"
    strategy = status.get("strategy")
    if strategy not in ("lift_ucb", "recipe_order"):
        strategy = "recipe_order"
    payload = {
        "source": source,
        "generated_last": int(status.get("generated_last") or 0),
        "exhausted": bool(status.get("exhausted")),
        "strategy": strategy,
        "random": _nonneg(status.get("random")),
        "paired": _nonneg(status.get("paired")),
        "exploit": _nonneg(status.get("exploit")),
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


def per_name_lease_seconds() -> int:
    """Hard cap from ``started_at``: two eval timeouts, default 1200s.

    Timeout 0 (disabled) still uses the 600s backstop so a started name
    cannot be held forever by a worker that keeps heartbeating.
    """
    per = eval_timeout_seconds()
    if per <= 0:
        per = DEFAULT_EVAL_TIMEOUT_SECONDS
    return LEASE_TTL_FACTOR * per


def lease_heartbeat_grace_seconds() -> int:
    """How long a lease lives after this worker's last heartbeat."""
    return int(FARM_HEARTBEAT_STALE_SECONDS)


def lease_ttl_seconds(n: int, parallel: int | None = None) -> int:
    """Initial lease window. ``n`` and ``parallel`` do not scale it.

    Older callers passed the batch shape because the lease used to be
    ``2 × timeout × ceil(n / parallel)``. A dead worker then held every
    name for the whole batch. The window is now the heartbeat grace.
    """
    del n, parallel
    return lease_heartbeat_grace_seconds()


def bump_lease_expiry(
    record: dict,
    now: datetime,
    *,
    name: str,
    evaluating: set[str] | None,
) -> None:
    """Push one lease out from ``now``. A started name stops at its cap.

    ``evaluating`` is the set of names this heartbeat says are in flight.
    The first such heartbeat records ``started_at``. A later heartbeat
    does not move that timestamp. Names left out of the set stay
    unstarted and keep renewing with the grace, so a live worker's
    queue is not cut off at the cap before those evals begin.
    """
    if evaluating is not None and name in evaluating and not record.get("started_at"):
        record["started_at"] = _iso(now)
    horizon = now + timedelta(seconds=lease_heartbeat_grace_seconds())
    started_raw = record.get("started_at")
    started = parse_tested_at(started_raw) if isinstance(started_raw, str) else None
    if started is not None:
        hard = started + timedelta(seconds=per_name_lease_seconds())
        record["expires_at"] = _iso(min(horizon, hard))
    else:
        record["expires_at"] = _iso(horizon)


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

    A pending buy-and-hold retest stays leased: the name is already
    in the tested index, and fail-once is lifted only until one fresh
    result is stored. Returns how many expired (not tested) leases were
    dropped.
    """
    from hedge_fund.trading.requeue_v4_bh import pending_retest_names

    keep_tested = set(pending_retest_names())
    leases: dict = state["leases"]
    expired = 0
    for name in list(leases):
        if name in tested and name not in keep_tested:
            del leases[name]
            continue
        exp = parse_tested_at(leases[name].get("expires_at") if isinstance(leases[name], dict) else None)
        if exp is None or exp <= now:
            del leases[name]
            expired += 1
    return expired


def _touch(
    state: dict,
    worker_id: str,
    status: str,
    now: datetime,
    parallel: int | None = None,
) -> None:
    workers = state.setdefault("workers", {})
    prev = workers.get(worker_id)
    if not isinstance(prev, dict):
        prev = {}
    prev["worker_id"] = worker_id
    prev["last_seen"] = _iso(now)
    prev["status"] = status
    # A later heartbeat that omits parallel must not wipe the lane width
    # this host claimed with. Missing stays missing (counted as 1).
    if parallel is not None and parallel >= 1:
        prev["parallel"] = int(parallel)
    workers[worker_id] = prev


def active_leased_lanes(state: dict, now: datetime) -> int:
    """Sum of lane widths seen on recent heartbeats.

    ``paused`` is excluded. ``idle`` and ``running`` inside
    ``FARM_HEARTBEAT_STALE_SECONDS`` count, so a host between batches
    does not drop the buffer. A stored ``parallel`` is the width; a
    heartbeat that never sent one counts as one lane. Stale heartbeats
    do not count.
    """
    workers = state.get("workers") or {}
    if not isinstance(workers, dict):
        return 0
    now = now.astimezone(timezone.utc)
    total = 0
    for meta in workers.values():
        if not isinstance(meta, dict) or meta.get("status") == "paused":
            continue
        seen = parse_tested_at(meta.get("last_seen"))
        if seen is None:
            continue
        age = (now - seen).total_seconds()
        if age > FARM_HEARTBEAT_STALE_SECONDS:
            continue
        raw = meta.get("parallel")
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            width = 1
        else:
            width = raw
        total += width
    return total


def refill_trigger(cap: int, lanes: int) -> int:
    """Unleased names we want on the queue before the next claims."""
    return max(int(cap), REFILL_LANE_MULTIPLE * max(0, int(lanes)))


def refill_batch_size(*, eligible: int, cap: int, lanes: int) -> int:
    """How many names to mint when ``eligible`` is under the trigger.

    Zero when the queue is already healthy. A quiet farm (no lanes)
    stays on the recipe handful. Live lanes scale that by the lane
    count, and the batch is never above ``REFILL_BATCH_MAX``.
    """
    trigger = refill_trigger(cap, lanes)
    if int(eligible) >= trigger:
        return 0
    paced = DISCOVERY_REFILL_BATCH_SIZE
    lane_n = max(0, int(lanes))
    if lane_n > 0:
        paced = min(REFILL_BATCH_MAX, max(paced, lane_n * DISCOVERY_REFILL_BATCH_SIZE))
    gap = trigger - int(eligible)
    return min(REFILL_BATCH_MAX, max(paced, gap))


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
    """Random explorer, then paired swaps, then recipe/densify.

    The recipe and densify ranking is unchanged. Uses the published lift
    snapshot. Does not fit and does not schedule a fit; that runs on a
    background thread at most once every five minutes. The OOS gate is
    not involved. ``next_refill_batch`` is the local binding so tests can
    still stub the exploit stream.
    """
    from hedge_fund.trading.atom_lift import lift_model_for_mint
    from hedge_fund.trading.discovery_results import load_seed_view
    from hedge_fund.trading.explorer import mix_refill
    from hedge_fund.trading.tested_index import load_tested_index, load_tested_metrics

    model = lift_model_for_mint()
    metrics = load_tested_metrics()
    # Slim rows published by ingest/backfill. Empty still counts as
    # "results were consulted", so old 8-window passes are not seeds
    # just because they are qualified. Does not scan the jsonl.
    results = load_seed_view()
    # ensure_tested_index already ran in this claim. A second merge is
    # not required; the bool map is the parent pool and the densify index.
    tested = load_tested_index()
    want = max(0, int(n))
    names, meta = mix_refill(
        taken_names=taken,
        n=want,
        lift=model,
        metrics=metrics,
        results=results,
        parents=list(tested),
        index=tested,
        recipe_batch=next_refill_batch,
        densify_batch=next_densify_batch,
    )
    names = [name for name in names if not mint_block_reason(name)]
    if meta.get("exhausted"):
        logger.warning(
            "discovery refill exhausted: wanted %s names, random produced %s, "
            "paired produced %s, exploit produced %s",
            want,
            meta.get("random"),
            meta.get("paired"),
            meta.get("exploit"),
        )
    return names, meta


def _book_names() -> set[str]:
    """Active, graduated, and retired names. Tested names are not in this set."""
    st = load_pool()
    grads = load_graduated()
    blocked = {c["name"] for c in (st.get("champions") or []) if isinstance(c, dict) and c.get("name")}
    blocked |= {g["name"] for g in grads if isinstance(g, dict) and g.get("name")}
    blocked |= retired_names()
    return blocked


def _queue_view(state: dict) -> dict:
    """Unleased never-tested names. Does not mint."""
    log = load_discovery_log()
    blocked = _blocked_names(log)
    active = set(state["leases"])
    universe = discovery_universe()
    record_untested_mint_skips(universe, blocked)
    leftovers = untested_candidates(blocked, universe)
    ordered = prioritize_leftovers(leftovers, log)
    seen_canon = canonical_key_set(blocked)
    claimable: list[str] = []
    for name in ordered:
        if name in active or mint_block_reason(name, seen_canon):
            continue
        claimable.append(name)
        key = canonical_name(name)
        if key:
            seen_canon.add(key)
    return {
        "claimable": claimable,
        "blocked": blocked,
        "universe": set(universe),
        "active": active,
        "seen_canon": seen_canon,
    }


def _take_retests(
    state: dict,
    n: int,
    worker_id: str,
    expires: datetime,
    now: datetime,
) -> list[str]:
    """Lease pending retests ahead of leftovers. Mint bans do not apply.

    Book names (active, graduated, retired) are not leased. A name that
    already has a lease stays with that worker.
    """
    if n <= 0:
        return []
    from hedge_fund.trading.requeue_v4_bh import pending_retest_names

    pending = pending_retest_names()
    if not pending:
        return []
    book = _book_names()
    leases: dict = state["leases"]
    chosen: list[str] = []
    for name in pending:
        if len(chosen) >= n:
            break
        if name in book or name in leases:
            continue
        leases[name] = {
            "worker_id": worker_id,
            "claimed_at": _iso(now),
            "expires_at": _iso(expires),
            "retest": True,
        }
        chosen.append(name)
    return chosen


def _append_minted(view: dict, added: list[str]) -> list[str]:
    """Append minted names that are still leasable. Returns that list."""
    kept: list[str] = []
    active = view["active"]
    blocked = view["blocked"]
    seen_canon = view["seen_canon"]
    claimable: list[str] = view["claimable"]
    for name in added:
        if name in active or name in blocked or mint_block_reason(name, seen_canon):
            continue
        claimable.append(name)
        kept.append(name)
        key = canonical_name(name)
        if key:
            seen_canon.add(key)
    return kept


def _claimable_names(
    state: dict,
    n: int,
    *,
    lanes: int,
) -> tuple[list[str], list[str], dict, bool]:
    """Never-tested, not-leased names, plus whether a buffer mint should follow.

    The claim mints only when this pool cannot cover ``n``. That mint is
    the shortfall, or the recipe handful when the whole top-up is that
    small. A larger buffer (up to 64) is not minted here: the caller
    schedules it after the paper-state lock is released.
    """
    view = _queue_view(state)
    claimable: list[str] = view["claimable"]
    eligible = len(claimable)
    cap = max(int(n), len(view["active"]), 1)
    batch = refill_batch_size(eligible=eligible, cap=cap, lanes=lanes)
    short = max(0, int(n) - eligible)
    sync_want = 0
    if short > 0:
        if 0 < batch <= DISCOVERY_REFILL_BATCH_SIZE:
            sync_want = max(short, batch)
        else:
            sync_want = short
    refilled: list[str] = []
    meta = load_refill_status()
    if sync_want > 0:
        taken = view["universe"] | view["blocked"] | view["active"] | set(claimable)
        added, meta = _mint_unlocked(taken, sync_want)
        if added:
            append_extended_batch(added)
            refilled = list(added)
            _append_minted(view, added)
        save_refill_status(meta)
    schedule = batch > sync_want
    return claimable, refilled, meta, schedule


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
    # The index is the fail-once set. A retest-filled claim does not
    # parse the display log; leftovers still do, inside ``_queue_view``.
    tested = set(load_tested_index())
    _drop_expired_and_tested(state, now, tested)
    ttl = lease_ttl_seconds(n, parallel)
    if not farm_enabled_unlocked():
        _touch(state, worker_id, "paused", now, parallel)
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
            "retest": [],
            "refill": paused_refill,
            "ttl_seconds": ttl,
            "per_name_cap_seconds": per_name_lease_seconds(),
            "expires_at": None,
            "claimed_at": _iso(now),
            "_schedule_refill": False,
        }
    # Count this claim's lanes before the thin check, including a host
    # whose previous heartbeat has not landed yet.
    _touch(state, worker_id, "running", now, parallel)
    lanes = active_leased_lanes(state, now)
    # Requalify lane first: re-checks of champions / old passes on the
    # current gate. Kept in discovery_requalify.json, not in these leases.
    from hedge_fund.trading.requalify import lease_requalify_unlocked

    requal = lease_requalify_unlocked(worker_id, n, ttl, now)
    rest = n - len(requal)
    expires = now + timedelta(seconds=ttl)
    retest: list[str] = []
    schedule = False
    if rest > 0:
        retest = _take_retests(state, rest, worker_id, expires, now)
        rest -= len(retest)
    if rest > 0:
        claimable, refilled, refill_meta, schedule = _claimable_names(
            state, rest, lanes=lanes,
        )
    else:
        claimable, refilled, refill_meta = [], [], load_refill_status()
    fresh = claimable[:max(0, rest)]
    chosen = list(requal) + list(retest) + fresh
    for name in fresh:
        state["leases"][name] = {
            "worker_id": worker_id,
            "claimed_at": _iso(now),
            "expires_at": _iso(expires),
        }
    status = "running" if chosen else "idle"
    _touch(state, worker_id, status, now, parallel)
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
        "retest": list(retest),
        "refill": refill_meta,
        "ttl_seconds": ttl,
        "per_name_cap_seconds": per_name_lease_seconds(),
        "expires_at": _iso(expires) if chosen else None,
        "claimed_at": _iso(now),
        "leases_active": len(state["leases"]),
        "requalify": list(requal),
        "trials": _trial_census_safe(),
        "_schedule_refill": schedule,
    }


def _trial_census_safe() -> dict:
    """Trial count for the deflated Sharpe ratio. Never fails a claim."""
    try:
        from hedge_fund.trading.trials import trial_census

        return trial_census()
    except Exception:
        return {"n_trials": None}


def stale_rules_response(worker_id: str, n: int, gate_rules: object) -> dict:
    """Claim answer for a worker whose checkout predates ``GATE_RULES``."""
    from hedge_fund.trading.constants import GATE_RULES

    return {
        "ok": True,
        "paper_only": True,
        "worker_id": worker_id,
        "names": [],
        "n": n,
        "paused": True,
        "stale_rules": True,
        "worker_gate_rules": gate_rules if isinstance(gate_rules, str) else None,
        "gate_rules": GATE_RULES,
        "message": "worker is on old gate rules; git pull and restart discovery_worker",
        "refilled": [],
        "expires_at": None,
    }


def claim_discovery_batch(
    worker_id: object,
    n: object,
    *,
    parallel: object = None,
    now: datetime | None = None,
    gate_rules: object = None,
    require_gate_rules: bool = False,
) -> dict:
    """Lease up to ``n`` names for ``worker_id``. Holds the paper-state lock.

    With ``require_gate_rules`` (the HTTP route), a worker that does not send
    the current ``GATE_RULES`` gets no names: its results would be computed
    on the old engine and ingest would reject them anyway.
    """
    from hedge_fund.trading.constants import GATE_RULES

    wid = require_worker_id(worker_id)
    count = require_n(n)
    par = require_parallel(parallel)
    if require_gate_rules and gate_rules != GATE_RULES:
        return stale_rules_response(wid, count, gate_rules)
    clock = now or datetime.now(timezone.utc)
    with paper_state_lock("discovery"):
        body = _claim_unlocked(wid, count, parallel=par, now=clock)
    if body.pop("_schedule_refill", False):
        schedule_refill_buffer()
    return body


def _refill_plan(now: datetime) -> dict:
    """How many names the buffer wants. Caller holds the paper-state lock."""
    state = load_lease_state()
    view = _queue_view(state)
    lanes = active_leased_lanes(state, now)
    eligible = len(view["claimable"])
    cap = max(len(view["active"]), 1)
    want = refill_batch_size(eligible=eligible, cap=cap, lanes=lanes)
    taken = view["universe"] | view["blocked"] | view["active"] | set(view["claimable"])
    return {
        "want": want,
        "taken": taken,
        "eligible": eligible,
        "lanes": lanes,
        "cap": cap,
    }


def _refill_commit(added: list[str], meta: dict, plan: dict) -> dict:
    """Append names that are still free. Caller holds the paper-state lock.

    A claim may have leased or minted some of ``added`` while
    ``mix_refill`` ran outside the lock. Those names are dropped.
    """
    state = load_lease_state()
    view = _queue_view(state)
    kept = _append_minted(view, added) if added else []
    if kept:
        append_extended_batch(kept)
    save_refill_status(meta)
    return {
        "ok": True,
        "paper_only": True,
        "refilled": list(kept),
        "wanted": plan["want"],
        "eligible": plan["eligible"],
        "lanes": plan["lanes"],
        "cap": plan["cap"],
    }


def _refill_skipped(reason: str) -> dict:
    return {
        "ok": True,
        "paper_only": True,
        "skipped": reason,
        "refilled": [],
    }


def refill_buffer_unlocked(
    *,
    now: datetime | None = None,
    expect_root: str | None = None,
) -> dict:
    """Top up a thin discovery queue.

    The snapshot and the append take the paper-state lock. ``mix_refill``
    runs between them, so a claim is not blocked for the whole mint.
    ``expect_root`` drops the mint when ``PAPER_STATE`` moved after the
    claim that scheduled this thread (tests delete that directory).
    """
    from hedge_fund.paths import state_root

    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    def moved() -> bool:
        return expect_root is not None and str(state_root()) != expect_root

    with paper_state_lock("discovery"):
        if moved():
            return _refill_skipped("state_moved")
        plan = _refill_plan(clock)
    if plan["want"] <= 0:
        return {
            "ok": True,
            "paper_only": True,
            "refilled": [],
            "wanted": 0,
            "eligible": plan["eligible"],
            "lanes": plan["lanes"],
            "cap": plan["cap"],
        }
    added, meta = _mint_unlocked(plan["taken"], plan["want"])
    with paper_state_lock("discovery"):
        if moved():
            return _refill_skipped("state_moved")
        return _refill_commit(list(added), meta, plan)


def _refill_buffer_thread(root: str) -> None:
    global _buffer_running
    try:
        refill_buffer_unlocked(expect_root=root)
    except Exception:
        logger.exception("discovery refill buffer failed")
    finally:
        with _buffer_guard:
            _buffer_running = False


def schedule_refill_buffer() -> bool:
    """Start one daemon mint. A second call while it runs does nothing.

    The thread takes the paper-state lock around the snapshot and the
    append, and mints outside it, so this must be called after the
    claim has released that lock.
    """
    global _buffer_running
    from hedge_fund.paths import state_root

    root = str(state_root())
    with _buffer_guard:
        if _buffer_running:
            return False
        _buffer_running = True
    threading.Thread(
        target=_refill_buffer_thread,
        args=(root,),
        name="discovery-refill-buffer",
        daemon=True,
    ).start()
    return True


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


def touch_worker_unlocked(
    worker_id: object,
    status: object = None,
    *,
    now: datetime | None = None,
    evaluating: object = None,
) -> None:
    """Record a per-worker heartbeat and renew that worker's leases.

    Invalid ids are ignored. Caller holds the paper-state lock. A
    ``paused`` heartbeat updates ``last_seen`` and does not move
    ``expires_at``. ``evaluating`` is the names this worker currently
    has in flight; the first time a leased name appears there, its
    per-name cap starts.
    """
    if not isinstance(worker_id, str) or not _WORKER_ID_RE.match(worker_id.strip()):
        return
    wid = worker_id.strip()
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cleaned = _status(status, "running")
    state = load_lease_state()
    _touch(state, wid, cleaned, clock)
    ev: set[str] | None = None
    if isinstance(evaluating, (list, tuple, set)):
        ev = {n for n in evaluating if isinstance(n, str) and n}
    if cleaned != "paused":
        for name, lease in state["leases"].items():
            if isinstance(lease, dict) and lease.get("worker_id") == wid:
                bump_lease_expiry(lease, clock, name=name, evaluating=ev)
    _prune_workers(state, clock)
    save_lease_state(state)
    if cleaned != "paused":
        from hedge_fund.trading.requalify import refresh_requalify_leases_unlocked

        refresh_requalify_leases_unlocked(wid, clock, ev)


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
