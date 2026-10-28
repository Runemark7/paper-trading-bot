"""Stateless discovery farm — same OOS gates as tournament, not on the k8s cycle.

Default (claim mode): prod leases never-tested names
(``POST /api/discovery/claim``). This process only needs the prod URL
and the ingest token. It downloads prod's shared tape; it does not need
a local ``crypto_history_5m.json`` and it does not need database
credentials. ``--local-plan`` keeps the old local cursor / refill path
and still reads that JSON file.

Evaluates leased names (fail-once, rm_v1, 5m windows) and POSTs results to
``/api/discovery/ingest``. Cluster ``live_cycle`` stays live-only.

Leave this process running. Pause/resume from the Discovery page
(``POST /api/discovery/farm``) — the loop polls prod and idles instead
of exiting so Start works without relaunching.

GPU is unused (no CUDA rewrite). Default parallelism is 2 workers.
``--workers`` is capped at ``os.cpu_count()`` (minimum 1), not a hard 4.
That many evals stay in flight: the next name starts when one finishes,
instead of waiting out a wave. Each name keeps its own eval timeout
(default 600s). One process pool and one loaded tape are reused across
claims until prod's tape hash changes.

    python scripts/discovery_worker.py --workers 2
    python scripts\\discovery_worker.py --workers 2
    python scripts/discovery_worker.py --once --workers 1 --no-ingest
    python scripts/discovery_worker.py --local-plan --workers 2

Env (a ``.env`` file in the working directory is loaded if present; values
already set in the environment win; the token is never printed):
  PAPER_STATE                        local state dir (history file)
  PAPER_DISCOVERY_INGEST_URL         default https://trading.runevibe.se/api/discovery/ingest
  PAPER_DISCOVERY_INGEST_TOKEN       shared secret (required unless --no-ingest)
  PAPER_DISCOVERY_BASE_URL           prod origin (claim / summary / bootstrap)
  DISCOVERY_WORKER_ID                default hostname-pid
  DISCOVERY_WORKERS                  default 2
  DISCOVERY_STRUCTURE_LOOKBACK_MAX   default 96; 0 disables. Farm ops, not an OOS gate.
  DISCOVERY_EVAL_TIMEOUT_SECONDS     default 600; 0 disables. Coarse per-name backstop.
"""
from __future__ import annotations

import atexit
import argparse
import json
import multiprocessing
import os
import queue
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hedge_fund.paths import state_root
from hedge_fund.trading.champions import load_graduated, load_pool, retired_names, save_graduated, save_pool
from hedge_fund.trading.constants import (
    CROSS_ASSET_SYMBOL,
    DISCOVER_CYCLE_MAX_NAMES,
    DISCOVERY_N_WINDOWS,
    DISCOVERY_REFILL_BATCH_SIZE,
    GATE_RULES,
    QUAL_STRIDE,
    QUAL_WINDOW_BARS,
    TRADE_SYMBOLS,
    qual_keep_bars,
    qual_store_bars,
)
from hedge_fund.trading.discovery import (
    append_discovery_evaluation,
    clear_in_flight,
    failed_discovery_names,
    load_cursor,
    load_discovery_log,
    save_cursor,
    select_cycle_batch,
    tested_discovery_names,
    write_in_flight,
)
from hedge_fund.trading.discovery_guard import (
    eval_timeout_reason,
    eval_timeout_seconds,
    lookback_too_expensive_reason,
    ops_fail_record,
    structure_lookback_max,
)
from hedge_fund.trading.discovery_mode import ingest_token
from hedge_fund.trading.farm import farm_enabled_from_summary
from hedge_fund.trading.refill import (
    append_extended_batch,
    discovery_universe,
    maybe_refill_discovery,
    save_extended_names,
)
from hedge_fund.trading.universe import untested_candidates
from scripts.tournament_engine import (
    _benchmark_bh_daily_sharpe,
    _benchmark_oos,
    _load_qual_history,
    _window_slices,
    evaluate_strategy_record,
)

DEFAULT_INGEST_URL = "https://trading.runevibe.se/api/discovery/ingest"
DEFAULT_BASE_URL = "https://trading.runevibe.se"
PAUSE_SLEEP_DEFAULT = 15
PAUSE_SLEEP_MIN = 10
PAUSE_SLEEP_MAX = 30

_SLICES = None
_BH = None
_SMA = None
_BH_DSR = None
_N_WINDOWS = DISCOVERY_N_WINDOWS
_ETH_SLICES = None
_HOLDOUT = None
_HOLDOUT_BH = None
_N_TRIALS = None
_N_TRIALS_RETEST = None
_RETEST_NAMES: frozenset[str] = frozenset()


def _init_pool(
    slices,
    bh,
    sma,
    n_windows: int,
    bh_dsr: float | None = None,
    eth_slices=None,
    holdout=None,
    holdout_bh=None,
    n_trials: int | None = None,
    retest_names: frozenset[str] | set[str] | list[str] | None = None,
    n_trials_retest: int | None = None,
) -> None:
    global _SLICES, _BH, _SMA, _N_WINDOWS, _BH_DSR
    global _ETH_SLICES, _HOLDOUT, _HOLDOUT_BH, _N_TRIALS
    global _N_TRIALS_RETEST, _RETEST_NAMES
    _SLICES = slices
    _BH = bh
    _SMA = sma
    _N_WINDOWS = n_windows
    _BH_DSR = bh_dsr
    _ETH_SLICES = eth_slices
    _HOLDOUT = holdout
    _HOLDOUT_BH = holdout_bh
    _N_TRIALS = n_trials
    _N_TRIALS_RETEST = n_trials_retest
    _RETEST_NAMES = frozenset(retest_names or ())


def _trials_for(name: str) -> int | None:
    """Fresh names use N+1. A pending retest is already inside N."""
    if name in _RETEST_NAMES:
        return _N_TRIALS_RETEST
    return _N_TRIALS


def _eval_name(
    name: str,
    n_trials: int | None = None,
    explicit_trials: bool = False,
) -> dict | None:
    """Qualify one name. Explicit trial counts win over the pool globals.

    A reused pool is not re-initialised when the census changes between
    claims, so the parent passes that name's N on the task.
    """
    trials = n_trials if explicit_trials else _trials_for(name)
    record = evaluate_strategy_record(
        name,
        _SLICES,
        n_windows=_N_WINDOWS,
        bh_oos_pnl=_BH,
        sma_stack_oos_pnl=_SMA,
        bh_daily_sharpe=_BH_DSR,
    )
    if record and record.get("qualified") and not record.get("ops_park"):
        from hedge_fund.trading.gate_v4 import finish_gate_v4

        record = finish_gate_v4(
            name,
            record,
            btc_slices=_SLICES,
            eth_slices=_ETH_SLICES,
            holdout_sample=_HOLDOUT,
            holdout_benchmarks=_HOLDOUT_BH,
            n_trials=trials,
        )
    return record


def _new_eval_pool(
    n_workers: int,
    slices,
    bh,
    sma,
    n_windows: int,
    bh_dsr: float | None = None,
    eth_slices=None,
    holdout=None,
    holdout_bh=None,
    n_trials: int | None = None,
    retest_names: frozenset[str] | set[str] | list[str] | None = None,
    n_trials_retest: int | None = None,
):
    # fork() copies thread state. Stop the post thread first so a child
    # does not inherit its queue lock. Queued posts stay in the queue
    # and a later submit starts the thread again.
    poster = globals().get("_poster")
    if poster is not None:
        poster.stop()
    return multiprocessing.Pool(
        processes=n_workers,
        initializer=_init_pool,
        initargs=(
            slices, bh, sma, n_windows, bh_dsr, eth_slices, holdout, holdout_bh,
            n_trials, retest_names, n_trials_retest,
        ),
    )


def _collect_wave(
    asyncs: list[tuple[str, object]],
    timeout_s: float,
    *,
    sleeper=time.sleep,
    clock=time.monotonic,
) -> tuple[list[tuple[str, dict | None]], list[str]]:
    """Wait for one already-submitted wave.

    ``run_batch`` does not call this. It keeps ``--workers`` names in
    flight via ``_run_inflight``. The helper stays so a single shared
    deadline can still be checked on its own.
    """
    deadline = (clock() + timeout_s) if timeout_s > 0 else None
    pending = {name: ar for name, ar in asyncs}
    completed: list[tuple[str, dict | None]] = []
    while pending:
        if deadline is not None and clock() >= deadline:
            return completed, list(pending)
        ready: list[str] = []
        for name, ar in pending.items():
            if ar.ready():
                try:
                    rec = ar.get()
                except Exception as exc:
                    print(f"discovery_worker: {name} failed: {exc}", flush=True)
                    rec = None
                completed.append((name, rec))
                ready.append(name)
        for name in ready:
            del pending[name]
        if pending:
            sleeper(0.05)
    return completed, []


# One loaded tape and one process pool for the life of this process.
# The tape is replaced only when its sha256 changes. The pool is replaced
# with it, or when a per-name timeout forces terminate().
_SESSION: dict = {"hist": None, "pool": None}


def _state_key() -> str:
    try:
        return str(state_root().resolve())
    except OSError:
        return str(state_root())


def _stamp_sha(stamp: dict | None) -> str | None:
    if not isinstance(stamp, dict):
        return None
    sha = stamp.get("sha256")
    if isinstance(sha, str) and sha:
        return sha
    return None


def _pool_alive(pool) -> bool:
    state = getattr(pool, "_state", None)
    if state is None:
        return True
    try:
        from multiprocessing.pool import RUN
    except Exception:
        return state == 0
    return state == RUN


def _drop_eval_pool() -> None:
    """Terminate the cached pool. A missing pool is a no-op."""
    pool = _SESSION.get("pool")
    _SESSION["pool"] = None
    _SESSION["pool_workers"] = None
    _SESSION["pool_sha"] = None
    _SESSION["pool_state"] = None
    _SESSION["pool_windows"] = None
    if pool is None:
        return
    try:
        pool.terminate()
    except Exception:
        pass
    try:
        pool.join()
    except Exception:
        pass


def _reset_eval_session() -> None:
    """Drop the cached pool and tape. Tests use this between cases."""
    _drop_eval_pool()
    _SESSION["hist"] = None


def _acquire_eval_pool(n_workers: int, bundle: dict, n_trials, retest_names, n_trials_retest):
    """Return the live pool for this tape, creating it when the key differs."""
    sha = bundle.get("sha")
    state = bundle.get("state")
    n_windows = bundle["n_windows"]
    pool = _SESSION.get("pool")
    if (
        pool is not None
        and sha
        and _SESSION.get("pool_workers") == n_workers
        and _SESSION.get("pool_sha") == sha
        and _SESSION.get("pool_state") == state
        and _SESSION.get("pool_windows") == n_windows
        and _pool_alive(pool)
    ):
        return pool
    _drop_eval_pool()
    pool = _new_eval_pool(
        n_workers,
        bundle["slices"],
        bundle["bh"],
        bundle["sma"],
        n_windows,
        bundle["bh_dsr"],
        bundle["eth_slices"],
        bundle["holdout"],
        bundle["holdout_bh"],
        n_trials,
        retest_names,
        n_trials_retest,
    )
    _SESSION["pool"] = pool
    _SESSION["pool_workers"] = n_workers
    _SESSION["pool_sha"] = sha
    _SESSION["pool_state"] = state
    _SESSION["pool_windows"] = n_windows
    return pool


def _cached_bundle(history_stamp: dict | None, n_windows: int) -> dict | None:
    sha = _stamp_sha(history_stamp)
    cached = _SESSION.get("hist")
    if not sha or not isinstance(cached, dict):
        return None
    if cached.get("sha") != sha or cached.get("state") != _state_key():
        return None
    if cached.get("n_windows") != n_windows:
        return None
    out = dict(cached)
    out["history_stamp"] = history_stamp
    return out


def _store_bundle(bundle: dict) -> None:
    sha = bundle.get("sha")
    prev = _SESSION.get("hist")
    if isinstance(prev, dict) and (
        prev.get("sha") != sha
        or prev.get("state") != bundle.get("state")
        or prev.get("n_windows") != bundle.get("n_windows")
    ):
        _drop_eval_pool()
    if not sha:
        _SESSION["hist"] = None
        _drop_eval_pool()
        return
    stored = dict(bundle)
    stored.pop("history_stamp", None)
    _SESSION["hist"] = stored


def _load_eval_bundle(history_stamp: dict | None, n_windows: int, *, claim_mode: bool) -> dict:
    """Load slices for this tape. Caller already decided the cache missed."""
    data = _load_qual_history(keep_bars=qual_store_bars())
    if not data:
        if claim_mode:
            raise SystemExit(
                "shared price history is empty. Claim mode loads it from prod "
                "and does not use crypto_history_5m.json."
            )
        raise SystemExit(
            "crypto_history_5m.json missing or empty under PAPER_STATE. "
            "Run: python scripts/fetch_history.py"
        )
    btc_rows = _symbol_rows(data, TRADE_SYMBOLS[0], purpose="walkforward")
    if not btc_rows:
        raise SystemExit("BTC/USDT tape missing; discovery is BTC-only")
    eth_rows = _symbol_rows(data, CROSS_ASSET_SYMBOL, purpose="walkforward")
    holdout = _holdout_sample(data)
    try:
        slices = _window_slices(
            {TRADE_SYMBOLS[0]: btc_rows},
            QUAL_WINDOW_BARS,
            n_windows,
            QUAL_STRIDE,
            tiled=True,
            discovery=True,
        )
    except ValueError as exc:
        raise SystemExit(f"history does not cover the tiled OOS segments: {exc}") from exc
    eth_slices = None
    if eth_rows:
        try:
            eth_slices = _window_slices(
                {CROSS_ASSET_SYMBOL: eth_rows},
                QUAL_WINDOW_BARS,
                n_windows,
                QUAL_STRIDE,
                tiled=True,
                discovery=True,
            )
        except ValueError:
            eth_slices = None
    holdout_bh = None
    if holdout:
        try:
            from hedge_fund.trading.gate_v4 import holdout_benchmarks

            holdout_bh = holdout_benchmarks(holdout)
        except Exception:
            holdout_bh = None
    del data
    if len(slices) != n_windows:
        raise SystemExit("history too short for qualification windows")
    return {
        "slices": slices,
        "bh": None,
        "sma": None,
        "bh_dsr": None,
        "bh_ready": False,
        "eth_slices": eth_slices,
        "holdout": holdout,
        "holdout_bh": holdout_bh,
        "n_windows": n_windows,
        "history_stamp": history_stamp,
        "sha": _stamp_sha(history_stamp),
        "state": _state_key(),
    }


def _prepare_eval_context(
    *,
    sync_base: str | None,
    sync_token: str | None,
    claim_mode: bool,
    n_windows: int,
) -> dict:
    """Resolve the tape stamp, and reuse slices when that hash is unchanged."""
    history_stamp: dict | None = None
    if sync_base and sync_token:
        history_stamp = ensure_shared_history(sync_base, sync_token)
    else:
        try:
            from hedge_fund.trading.price_history import manifest_for

            history_stamp = manifest_for()
            history_stamp["provenance"] = "unverified"
        except Exception:
            history_stamp = None
    cached = _cached_bundle(history_stamp, n_windows)
    if cached is not None:
        return cached
    bundle = _load_eval_bundle(history_stamp, n_windows, claim_mode=claim_mode)
    _store_bundle(bundle)
    return bundle


def _run_inflight(
    names: list[str],
    *,
    n_workers: int,
    timeout_s: float,
    finish,
    pool_factory,
    trial_for,
    sleeper=None,
    clock=None,
    on_inflight=None,
    refill=None,
    prefetch: int = 0,
) -> None:
    """Keep up to *n_workers* evals in flight.

    The next name is submitted as soon as a slot frees, and that submit
    happens before *finish* posts the result, so the HTTP post does not
    leave the core idle. Retests and fresh names share this queue. Each
    name's deadline is *timeout_s* seconds from its own submit
    (``timeout_s`` <= 0 waits). Only a name that crosses its own deadline
    is fail-parked. ``pool.terminate`` is process-wide, so any other
    in-flight name is submitted again with a fresh deadline and is not
    parked. *finish* runs as each result is ready. ``on_inflight`` sees
    the names actually submitted, including right after the first launch.

    ``refill(n)`` leases more names when the local queue cannot fill the
    slots. ``prefetch`` is how many extra names to hold behind the running
    set so the next start does not wait on that claim.
    """
    if sleeper is None:
        sleeper = time.sleep
    if clock is None:
        clock = time.monotonic
    width = max(1, int(n_workers))
    ahead = max(0, int(prefetch))
    queue = list(names)
    inflight: dict[str, tuple[object, float]] = {}
    pool = None

    def _submit_queued() -> None:
        nonlocal pool
        while queue and len(inflight) < width:
            if pool is None:
                pool = pool_factory()
            name = queue.pop(0)
            trials = trial_for(name)
            started = clock()
            ar = pool.apply_async(_eval_name, (name, trials, True))
            inflight[name] = (ar, started)

    def launch() -> None:
        _submit_queued()
        if refill is not None:
            # One claim per launch. A short reply is asked again after the
            # next completion, not in a tight loop.
            deficit = width + ahead - len(inflight) - len(queue)
            if deficit > 0:
                try:
                    more = refill(deficit) or []
                except Exception as exc:
                    print(f"discovery_worker: claim top-up failed: {exc}", flush=True)
                    more = []
                if more:
                    queue.extend(more)
                    _submit_queued()
        if on_inflight is not None:
            on_inflight(list(inflight))

    launch()
    while inflight:
        now = clock()
        stuck: list[str] = []
        if timeout_s > 0:
            for name, (_ar, started) in inflight.items():
                if now - started >= timeout_s:
                    stuck.append(name)
        ready: list[tuple[str, dict | None]] = []
        for name, (ar, _started) in list(inflight.items()):
            if name in stuck or not ar.ready():
                continue
            try:
                rec = ar.get()
            except Exception as exc:
                print(f"discovery_worker: {name} failed: {exc}", flush=True)
                rec = None
            ready.append((name, rec))
        for name, rec in ready:
            del inflight[name]
            # Fill the free slot before the result post. The child is
            # already running while finish() talks to prod.
            if not stuck:
                launch()
            finish(name, rec)
        if stuck:
            reason = eval_timeout_reason(timeout_s)
            killed = [name for name in inflight if name not in stuck]
            inflight.clear()
            if on_inflight is not None:
                on_inflight([])
            _drop_eval_pool()
            pool = None
            for name in stuck:
                finish(name, ops_fail_record(name, reason))
            # Killed siblings start over. The deadline starts over with them.
            if killed:
                print(
                    f"discovery_worker: resubmitting {len(killed)} name(s) after eval timeout",
                    flush=True,
                )
            queue = killed + queue
            launch()
            continue
        if not ready:
            sleeper(0.05)


atexit.register(_drop_eval_pool)


def _http_json(url: str, *, token: str | None = None, data: dict | None = None, timeout: int = 60):
    headers = {"Accept": "application/json"}
    body = None
    method = "GET"
    if token:
        headers["X-Discovery-Token"] = token
    if data is not None:
        method = "POST"
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"{method} {url} -> {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc


def _http_bytes(url: str, *, token: str | None = None, timeout: int = 120) -> bytes:
    headers = {"Accept": "application/octet-stream"}
    if token:
        headers["X-Discovery-Token"] = token
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"GET {url} -> {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc.reason}") from exc


# Shared-tape problems skip this batch. They must not kill the farm loop.
HISTORY_BACKOFF_SECONDS = 60


class HistoryUnavailable(Exception):
    """Prod tape is missing or still mismatched. Back off and do not eval."""


def ensure_shared_history(base_url: str, token: str) -> dict:
    """Fetch prod's canonical tape when the local hash differs.

    A mismatch after refresh, or a failed fetch (including HTTP 502),
    raises ``HistoryUnavailable``. The farm loop sleeps and retries.
    It does not exit.
    """
    from hedge_fund.trading.price_history import install_tape_blob, manifest_for

    try:
        remote = _http_json(
            f"{base_url.rstrip('/')}/api/discovery/history",
            token=token,
            timeout=60,
        )
        local = manifest_for()
        remote_hash = remote.get("sha256") if isinstance(remote, dict) else None
        if remote_hash and local.get("sha256") == remote_hash:
            local["provenance"] = "shared"
            return local
        blob = _http_bytes(
            f"{base_url.rstrip('/')}/api/discovery/history/tape",
            token=token,
            timeout=180,
        )
        installed = install_tape_blob(blob)
    except HistoryUnavailable:
        raise
    except Exception as exc:
        print(
            f"discovery_worker: shared tape fetch failed ({exc}); skipping eval",
            flush=True,
        )
        raise HistoryUnavailable(str(exc)) from exc
    if not remote_hash or installed.get("sha256") != remote_hash:
        print(
            "discovery_worker: price history hash still differs after refresh "
            f"(local={str(installed.get('sha256'))[:12]} remote={str(remote_hash)[:12]}); "
            "skipping eval",
            flush=True,
        )
        raise HistoryUnavailable("price history hash mismatch after refresh")
    installed["provenance"] = "shared"
    print(
        f"discovery_worker: refreshed shared tape hash={installed['sha256'][:12]} "
        f"data_end={installed.get('data_end')}",
        flush=True,
    )
    return installed


def _stamp_provenance(record: dict | None, history: dict | None, worker_id: str | None) -> dict | None:
    if not isinstance(record, dict):
        return record
    if not history or not history.get("sha256"):
        record["provenance"] = "unknown"
        record["worker_id"] = worker_id or record.get("worker_id") or "unknown"
        record.setdefault("data_end", None)
        record.setdefault("data_hash", None)
        return record
    record["worker_id"] = worker_id or "unknown"
    record["data_end"] = history.get("data_end")
    record["data_hash"] = history.get("sha256")
    record["provenance"] = history.get("provenance") or "unverified"
    return record


def _base_url(ingest_url: str, override: str | None) -> str:
    if override:
        return override.rstrip("/")
    if "/api/" in ingest_url:
        return ingest_url.split("/api/", 1)[0].rstrip("/")
    return DEFAULT_BASE_URL


def bootstrap_from_prod(base_url: str) -> dict:
    """Copy prod fail-once / pool / extended into local PAPER_STATE."""
    log = _http_json(f"{base_url}/api/discovery")
    if not isinstance(log, list):
        log = []
    champs_payload = _http_json(f"{base_url}/api/champions")
    grads = _http_json(f"{base_url}/api/graduated")
    summary = _http_json(f"{base_url}/api/discovery/summary")
    if not isinstance(grads, list):
        grads = []
    champ_rows = []
    if isinstance(champs_payload, dict):
        champ_rows = champs_payload.get("active_champions") or champs_payload.get("champions") or []
    names = []
    for row in champ_rows:
        if isinstance(row, dict) and row.get("name"):
            names.append({
                "name": row["name"],
                "closed": int(row.get("closed") or 0),
                "pnl": float(row.get("pnl") or 0.0),
                "wins": int(row.get("wins") or 0),
                "champion_since": row.get("champion_since"),
            })
    root = state_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "discovery_log.json").write_text(json.dumps(log, separators=(",", ":")))
    save_pool({"champions": names, "synced_until": ""})
    save_graduated([g for g in grads if isinstance(g, dict)])
    extended = []
    if isinstance(summary, dict):
        extended = [n for n in (summary.get("extended_names") or []) if isinstance(n, str)]
    if extended:
        save_extended_names(extended, batches_emitted=0)
    return {
        "log_rows": len(log),
        "champions": len(names),
        "graduated": len(grads),
        "extended": len(extended),
    }


def _plan_batch(max_names: int) -> tuple[list[str], list[str], list[str]]:
    st = load_pool()
    grads = load_graduated()
    blocked = {c["name"] for c in (st.get("champions") or []) if c.get("name")}
    blocked |= {g["name"] for g in grads if isinstance(g, dict) and g.get("name")}
    blocked |= retired_names()
    universe = discovery_universe()
    leftovers = untested_candidates(blocked, universe)
    log = load_discovery_log()
    cursor = load_cursor()
    planned, rotated = select_cycle_batch(
        leftovers,
        log,
        max_names=max_names,
        cursor_name=cursor.get("next_name"),
    )
    added: list[str] = []
    if len(rotated) < max(DISCOVER_CYCLE_MAX_NAMES, 1):
        taken = (
            set(universe)
            | set(leftovers)
            | set(blocked)
            | tested_discovery_names(log)
            | failed_discovery_names(log)
        )
        added = maybe_refill_discovery(
            eligible_count=len(rotated),
            cap=max(DISCOVER_CYCLE_MAX_NAMES, 1),
            taken_names=taken,
            batch_size=DISCOVERY_REFILL_BATCH_SIZE,
        )
        if added:
            leftovers = list(leftovers) + [n for n in added if n not in leftovers]
            planned, rotated = select_cycle_batch(
                leftovers,
                log,
                max_names=max_names,
                cursor_name=cursor.get("next_name"),
            )
    return planned, rotated, added


def poll_farm_enabled(base_url: str, last_known: bool = True) -> bool:
    """Ask prod whether the farm should run. Network errors keep last_known."""
    try:
        started = time.perf_counter()
        summary = _http_json(f"{base_url.rstrip('/')}/api/discovery/summary?compact=1", timeout=20)
        _log_round_trip("summary", started, summary)
    except Exception:
        return last_known
    return farm_enabled_from_summary(summary, last_known)


def pause_sleep_seconds(raw: int) -> int:
    return max(PAUSE_SLEEP_MIN, min(int(raw), PAUSE_SLEEP_MAX))


def default_worker_id() -> str:
    """Hostname plus pid so two processes on one machine do not share a lease."""
    host = socket.gethostname().split(".")[0].strip() or "worker"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in host).strip("-") or "worker"
    return f"{safe[:48]}-{os.getpid()}"


def _load_local_env() -> None:
    """Fill unset env vars from a local ``.env``. Never logs values."""
    candidates = [Path.cwd() / ".env", ROOT / ".env"]
    seen: set[str] = set()
    for path in candidates:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen or not path.is_file():
            seen.add(key)
            continue
        seen.add(key)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            env_key, val = line.split("=", 1)
            env_key = env_key.strip()
            if not env_key or env_key in os.environ:
                continue
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in {'"', "'"}:
                val = val[1:-1]
            os.environ[env_key] = val


def _claim_remote(base_url: str, token: str, worker_id: str, n: int, parallel: int) -> dict:
    started = time.perf_counter()
    body = _http_json(
        f"{base_url.rstrip('/')}/api/discovery/claim",
        token=token,
        data={
            "worker_id": worker_id,
            "n": int(n),
            "parallel": int(parallel),
            "gate_rules": GATE_RULES,
        },
        timeout=120,
    )
    _log_round_trip(f"claim n={n}", started, body)
    return body


def _release_remote(
    base_url: str,
    token: str,
    worker_id: str,
    *,
    names: list[str] | None = None,
    status: str = "idle",
) -> dict:
    payload: dict = {"worker_id": worker_id, "status": status}
    if names is not None:
        payload["names"] = list(names)
    started = time.perf_counter()
    body = _http_json(
        f"{base_url.rstrip('/')}/api/discovery/release",
        token=token,
        data=payload,
        timeout=60,
    )
    _log_round_trip("release", started, body)
    return body


def _release_remote_safe(
    base_url: str | None,
    token: str | None,
    worker_id: str | None,
    *,
    names: list[str] | None = None,
    status: str = "idle",
) -> None:
    if not (base_url and token and worker_id):
        return
    try:
        _release_remote(base_url, token, worker_id, names=names, status=status)
    except Exception as exc:
        print(f"discovery_worker: release failed: {exc}", flush=True)


_PARENT_PID = os.getpid()


def _install_exit_signals() -> dict:
    """SIGINT/SIGTERM become KeyboardInterrupt in the parent only."""

    def handler(signum, frame):
        if os.getpid() != _PARENT_PID:
            raise KeyboardInterrupt
        raise KeyboardInterrupt

    prev: dict = {}
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            prev[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
    return prev


def _restore_exit_signals(prev: dict) -> None:
    for sig, old in prev.items():
        try:
            signal.signal(sig, old)
        except (ValueError, OSError):
            pass


def consider_pause(
    enabled: bool,
    *,
    once: bool,
    ingest_url: str | None,
    token: str | None,
    pause_sleep: int,
    sleeper=time.sleep,
    claim_mode: bool = False,
    worker_id: str | None = None,
    base_url: str | None = None,
) -> str:
    """Idle when the farm flag is off. Does not exit the worker process.

    Returns ``run``, ``pause``, or ``exit`` (``--once`` while paused).
    Claim mode releases this worker's leases and does not clear the shared
    in-flight file (another host may still hold names).
    """
    if enabled:
        return "run"
    print("discovery_worker: farm paused — idling (near-zero CPU)", flush=True)
    clear_in_flight()
    if claim_mode:
        _release_remote_safe(base_url, token, worker_id, status="paused")
    elif ingest_url and token:
        try:
            _post_ingest(
                ingest_url,
                token,
                [],
                [],
                None,
                clear=True,
                heartbeat="paused",
                worker_id=worker_id,
            )
        except Exception as exc:
            print(f"discovery_worker: pause ingest failed: {exc}", flush=True)
    if once:
        return "exit"
    sleeper(pause_sleep_seconds(pause_sleep))
    return "pause"


# Retries and drain must not follow the eval loop's patched clock.
_REAL_SLEEP = time.sleep
_REAL_MONO = time.monotonic


class _ResultPoster:
    """Post ingest and heartbeats off the eval loop.

    Results are retried and acked once the server accepts them. A full
    queue blocks a result (backpressure) and drops a heartbeat-only post.
    ``drain`` waits until the queued posts have finished or given up.
    """

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=64)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closing = False

    def _ensure(self) -> None:
        with self._lock:
            if self._closing:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._loop, name="discovery-post", daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Join the post thread so a later fork does not copy it."""
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._closing = True
            self._thread = None
        self._q.put(None)
        thread.join(timeout=30)
        with self._lock:
            self._closing = False

    def submit(self, item: dict) -> bool:
        """Queue ``item``. Result posts block when 64 are waiting."""
        self._ensure()
        if item.get("ack_name"):
            self._q.put(item)
            return True
        try:
            self._q.put_nowait(item)
        except queue.Full:
            print("discovery_worker: dropping heartbeat, post queue full", flush=True)
            return False
        return False

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            try:
                if item is None:
                    return
                self._send(item)
            finally:
                self._q.task_done()

    def _send(self, item: dict) -> None:
        delays = (0.0, 0.5, 1.5)
        last: Exception | None = None
        for delay in delays:
            if delay:
                _REAL_SLEEP(delay)
            try:
                _post_ingest(
                    item["url"],
                    item["token"],
                    item["evals"],
                    item["extended"],
                    item["flight"],
                    clear=item["clear"],
                    heartbeat=item["heartbeat"],
                    worker_id=item["worker_id"],
                    evaluating=item["evaluating"],
                )
                ack = item.get("on_ack")
                if ack is not None:
                    ack()
                return
            except Exception as exc:
                last = exc
                print(
                    f"discovery_worker: post failed ({exc}); retrying",
                    flush=True,
                )
        print(f"discovery_worker: giving up on post ({last})", flush=True)

    def drain(self, timeout: float = 60.0) -> bool:
        if self._thread is None:
            return True
        deadline = _REAL_MONO() + timeout
        while self._q.unfinished_tasks and _REAL_MONO() < deadline:
            _REAL_SLEEP(0.01)
        return self._q.unfinished_tasks == 0


_poster = _ResultPoster()


def _log_round_trip(label: str, started: float, body: object) -> None:
    ms = (time.perf_counter() - started) * 1000.0
    server = body.get("handler_ms") if isinstance(body, dict) else None
    detail = body.get("handler_ms_breakdown") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        extra = (
            f" server={detail.get('total')}ms"
            f" lock_wait={detail.get('lock_wait')}ms"
            f" io={detail.get('io')}ms"
            f" work={detail.get('work')}ms"
            f" hold={detail.get('lock_hold')}ms"
        )
    elif isinstance(server, (int, float)):
        extra = f" server={server:.0f}ms"
    else:
        extra = ""
    print(f"discovery_worker: {label} {ms:.0f}ms{extra}", flush=True)


def _post_ingest(
    url: str,
    token: str,
    evaluations: list[dict],
    extended: list[str],
    flight: dict | None,
    *,
    clear: bool = False,
    heartbeat: str | None = None,
    worker_id: str | None = None,
    evaluating: list[str] | None = None,
) -> dict:
    payload = {
        "evaluations": evaluations,
        "extended_names": extended,
        "source": "windows_worker",
        "paper_only": True,
    }
    if worker_id:
        payload["worker_id"] = worker_id
    if heartbeat:
        hb = {"status": heartbeat, "source": "windows_worker"}
        if worker_id:
            hb["worker_id"] = worker_id
        if evaluating is not None:
            hb["evaluating"] = [n for n in evaluating if isinstance(n, str)]
        payload["heartbeat"] = hb
    if clear:
        payload["clear_in_flight"] = True
    elif flight:
        payload["in_flight"] = flight
    started = time.perf_counter()
    body = _http_json(url, token=token, data=payload, timeout=90)
    kind = "ingest" if evaluations else "heartbeat"
    _log_round_trip(kind, started, body)
    return body


def _looks_like_bars(rows: object) -> bool:
    return isinstance(rows, list) and bool(rows) and isinstance(rows[0], (list, tuple)) and len(rows[0]) >= 5


def _symbol_rows(data: dict, symbol: str, *, purpose: str) -> list | None:
    """Pre-holdout rows for one symbol. Non-bar fixtures pass through."""
    rows = data.get(symbol)
    if not rows:
        return None
    if not _looks_like_bars(rows):
        return rows
    from hedge_fund.trading.holdout import assert_no_holdout, discovery_rows

    kept = discovery_rows(rows)
    assert_no_holdout(kept, purpose=purpose)
    return kept


def _holdout_sample(data: dict) -> dict | None:
    """One BTC holdout span. Refused while a mint scope is active."""
    from hedge_fund.trading.constants import QUAL_WARMUP_BARS
    from hedge_fund.trading.holdout import read_bars

    rows = data.get(TRADE_SYMBOLS[0])
    if not _looks_like_bars(rows):
        return None
    sealed = read_bars(rows, purpose="holdout")
    pre = [r for r in rows if int(r[0]) < sealed[0][0]] if sealed else []
    if len(sealed) < 30 or len(pre) < 14:
        return None
    warm = pre[-QUAL_WARMUP_BARS:]
    chunk = warm + sealed
    from scripts.tournament_engine import _ohlc

    closes, highs, lows = _ohlc(chunk)
    warm_len = len(warm)
    # cut == warmup so the scored span is the holdout, not a 70/30 split.
    return {TRADE_SYMBOLS[0]: (closes, highs, lows, warm_len, warm_len)}


def run_batch(
    *,
    workers: int,
    max_names: int,
    ingest_url: str | None,
    token: str | None,
    n_windows: int = DISCOVERY_N_WINDOWS,
    names: list[str] | None = None,
    claim_mode: bool = False,
    worker_id: str | None = None,
    sync_base: str | None = None,
    sync_token: str | None = None,
    n_trials: int | None = None,
    retest_names: list[str] | None = None,
    n_trials_retest: int | None = None,
    claim_more=None,
    prefetch: int = 0,
    trials_for=None,
) -> dict:
    bundle = _prepare_eval_context(
        sync_base=sync_base,
        sync_token=sync_token,
        claim_mode=claim_mode,
        n_windows=n_windows,
    )
    slices = bundle["slices"]
    eth_slices = bundle["eth_slices"]
    holdout = bundle["holdout"]
    holdout_bh = bundle["holdout_bh"]
    history_stamp = bundle["history_stamp"]
    bh = bundle["bh"]
    sma = bundle["sma"]
    bh_dsr = bundle["bh_dsr"]
    # Claim mode must use the prod census. A missing count fails DSR closed.
    # A local empty index would otherwise report N=1 and skip the haircut.
    if n_trials is None and not claim_mode:
        try:
            from hedge_fund.trading.trials import trial_census

            n_trials = int(trial_census()["n_trials"])
        except Exception:
            n_trials = None
    if len(slices) != n_windows:
        raise SystemExit("history too short for qualification windows")

    if names is not None:
        planned, rotated, added = list(names), list(names), []
    else:
        planned, rotated, added = _plan_batch(max_names)
    if added:
        print(f"discovery_worker: refilled {len(added)} names", flush=True)

    evaluating_names: list[str] = []
    eval_beat = {"sent": False}

    def _ingest(evals, extended, flight, *, clear=False, heartbeat=None, evaluating=None, ack_name=None):
        """Queue a prod post. Returns True when ``ack_name`` will be acked on success."""
        if not (ingest_url and token):
            return False
        if claim_mode:
            flight = None
            clear = False
        on_ack = None
        if ack_name:
            on_ack = lambda name=ack_name: acked.append(name)
        return _poster.submit({
            "url": ingest_url,
            "token": token,
            "evals": evals,
            "extended": extended,
            "flight": flight,
            "clear": clear,
            "heartbeat": heartbeat,
            "worker_id": worker_id,
            "evaluating": evaluating,
            "ack_name": ack_name,
            "on_ack": on_ack,
        })

    def on_inflight(names_now: list[str]) -> None:
        evaluating_names[:] = list(names_now)
        if eval_beat["sent"] or not names_now:
            return
        eval_beat["sent"] = True
        try:
            _ingest(
                [],
                [],
                {
                    "names": list(names_now),
                    "remaining": list(names_now),
                    "completed": [],
                    "batch_size": len(planned),
                },
                heartbeat="running",
                evaluating=list(names_now),
            )
        except Exception as exc:
            print(f"discovery_worker: in-flight heartbeat failed: {exc}", flush=True)

    if not planned:
        print("discovery_worker: no never-tested names (recipe dry or all parked)", flush=True)
        try:
            _ingest([], [] if claim_mode else added, None, clear=True, heartbeat="idle")
        except Exception as exc:
            print(f"discovery_worker: idle ingest failed: {exc}", flush=True)
        if not claim_mode:
            clear_in_flight()
        return {"evaluated": 0, "qualified": 0, "planned": [], "refilled": added}

    if bundle.get("bh_ready"):
        bh = bundle["bh"]
        sma = bundle["sma"]
        bh_dsr = bundle["bh_dsr"]
    else:
        bh, sma = _benchmark_oos(slices)
        bh_dsr = _benchmark_bh_daily_sharpe(slices)
        bundle["bh"] = bh
        bundle["sma"] = sma
        bundle["bh_dsr"] = bh_dsr
        bundle["bh_ready"] = True
        cached = _SESSION.get("hist")
        if isinstance(cached, dict) and cached.get("sha") == bundle.get("sha") and bundle.get("sha"):
            cached["bh"] = bh
            cached["sma"] = sma
            cached["bh_dsr"] = bh_dsr
            cached["bh_ready"] = True
    if not claim_mode:
        write_in_flight(
            planned,
            current=None,
            remaining=planned,
            completed=[],
            batch_size=len(planned),
            source="windows_worker",
        )
    try:
        _ingest(
            [],
            [] if claim_mode else added,
            {
                "names": planned,
                "remaining": planned,
                "completed": [],
                "batch_size": len(planned),
                "current": None,
            },
            heartbeat="running",
        )
    except Exception as exc:
        print(f"discovery_worker: batch-start ingest failed: {exc}", flush=True)

    records: list[dict] = []
    completed: list[str] = []
    acked: list[str] = []
    lookback_cap = structure_lookback_max()
    timeout_s = eval_timeout_seconds()

    def finish(name: str, record: dict | None) -> None:
        completed.append(name)
        remaining = [n for n in planned if n not in completed]
        if record is not None:
            _stamp_provenance(record, history_stamp, worker_id)
            append_discovery_evaluation(record)
            records.append(record)
            reasons = record.get("fail_reasons") or []
            if record.get("ops_park"):
                print(
                    f"discovery_worker: {name} parked {reasons[0] if reasons else 'ops'}",
                    flush=True,
                )
            else:
                print(
                    f"discovery_worker: {name} qualified={record.get('qualified')} "
                    f"sharpe={record.get('sharpe')} trades={record.get('trades')}",
                    flush=True,
                )
            queued = _ingest(
                [record],
                [],
                {
                    "names": remaining,
                    "remaining": remaining,
                    "completed": completed,
                    "batch_size": len(planned),
                },
                heartbeat="running",
                evaluating=list(evaluating_names),
                ack_name=name,
            )
            if not queued:
                acked.append(name)
        if not claim_mode:
            write_in_flight(
                remaining,
                current=None,
                remaining=remaining,
                completed=completed,
                batch_size=len(planned),
                source="windows_worker",
            )

    seen_names = set(planned)
    cheap: list[str] = []
    for name in planned:
        expensive = lookback_too_expensive_reason(name, lookback_cap)
        if expensive:
            finish(name, ops_fail_record(name, expensive))
        else:
            cheap.append(name)

    # Slot count is the worker width, not the size of this claim. A
    # one-name claim used to take the in-process path and leave the
    # other core idle even when more names were waiting on the server.
    slots = max(1, int(workers))
    pool_size = slots
    retain_pool = bool(bundle.get("sha"))
    try:
        if not cheap and claim_more is None:
            pass
        elif slots == 1 and timeout_s <= 0:
            _init_pool(
                slices, bh, sma, n_windows, bh_dsr,
                eth_slices, holdout, holdout_bh, n_trials,
                retest_names, n_trials_retest,
            )
            for name in cheap:
                evaluating_names[:] = [name]
                finish(name, _eval_name(name))
                evaluating_names[:] = []
        else:
            retest = set(retest_names or ())

            def trial_for(name: str) -> int | None:
                if trials_for is not None:
                    return trials_for(name)
                if name in retest:
                    return n_trials_retest
                return n_trials

            def pool_factory():
                return _acquire_eval_pool(
                    pool_size,
                    bundle,
                    n_trials,
                    retest_names,
                    n_trials_retest,
                )

            def refill(n: int) -> list[str]:
                if claim_more is None or n <= 0:
                    return []
                runnable: list[str] = []
                asks = 0
                while len(runnable) < n and asks < 8:
                    asks += 1
                    try:
                        got = claim_more(n - len(runnable)) or []
                    except Exception as exc:
                        print(f"discovery_worker: claim top-up failed: {exc}", flush=True)
                        break
                    if not got:
                        break
                    fresh = False
                    for name in got:
                        if not isinstance(name, str) or name in seen_names:
                            continue
                        seen_names.add(name)
                        planned.append(name)
                        fresh = True
                        expensive = lookback_too_expensive_reason(name, lookback_cap)
                        if expensive:
                            finish(name, ops_fail_record(name, expensive))
                            continue
                        runnable.append(name)
                    if not fresh:
                        break
                return runnable

            try:
                _run_inflight(
                    cheap,
                    n_workers=slots,
                    timeout_s=timeout_s,
                    finish=finish,
                    pool_factory=pool_factory,
                    trial_for=trial_for,
                    on_inflight=on_inflight,
                    refill=refill if claim_more is not None else None,
                    prefetch=prefetch,
                )
            except BaseException:
                _drop_eval_pool()
                raise
    finally:
        # Posts ran beside the evals. Wait for them before deciding which
        # leases were never accepted, so a slow prod round-trip is not a
        # reason to hand the name to another worker.
        _poster.drain()
        if not retain_pool:
            _drop_eval_pool()
        if claim_mode:
            # Posted rows clear their lease on ingest. Release every name
            # that was not posted: never started, killed on shutdown, or
            # an eval that returned no row.
            pending = [n for n in planned if n not in acked]
            clear_in_flight()
            if pending:
                _release_remote_safe(
                    _base_url(ingest_url or "", None) if ingest_url else None,
                    token,
                    worker_id,
                    names=pending,
                    status="idle",
                )
        else:
            done = set(completed)
            next_name = None
            for n in rotated:
                if n not in done:
                    next_name = n
                    break
            save_cursor(next_name, last_evaluated=completed, last_count=len(completed), last_refill=added)
            clear_in_flight()
            if ingest_url and token:
                try:
                    _post_ingest(
                        ingest_url,
                        token,
                        [],
                        [],
                        None,
                        clear=True,
                        heartbeat="idle",
                        worker_id=worker_id,
                    )
                except Exception as exc:
                    print(f"discovery_worker: clear in_flight ingest failed: {exc}", flush=True)

    qualified = [r for r in records if r.get("qualified")]
    return {
        "evaluated": len(records),
        "qualified": len(qualified),
        "planned": planned,
        "refilled": added,
        "admitted_local": [r["strategy"] for r in qualified],
    }


def clamp_workers(requested: int, *, cpu_count: int | None = None) -> int:
    """Clamp worker processes to ``[1, os.cpu_count()]``.

    The CLI default stays 2 (or ``DISCOVERY_WORKERS``). This only raises
    the old hard ceiling of 4. ``cpu_count`` is a test hook; production
    uses ``os.cpu_count()``.
    """
    cpus = os.cpu_count() if cpu_count is None else cpu_count
    cap = max(1, int(cpus or 1))
    return max(1, min(int(requested), cap))


def main(argv: list[str] | None = None) -> int:
    _load_local_env()
    ap = argparse.ArgumentParser(description="Discovery farm (paper only, claim queue by default)")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("DISCOVERY_WORKERS") or 2))
    ap.add_argument("--max-names", type=int, default=0, help="Names per batch (default = workers)")
    ap.add_argument("--once", action="store_true", help="One batch then exit")
    ap.add_argument("--no-ingest", action="store_true", help="Local eval only; do not POST prod")
    ap.add_argument("--no-bootstrap", action="store_true", help="Skip pulling prod log/pool (--local-plan)")
    ap.add_argument(
        "--local-plan",
        action="store_true",
        help="Plan batches locally (old cursor/refill) instead of POST /api/discovery/claim",
    )
    ap.add_argument(
        "--worker-id",
        default=os.environ.get("DISCOVERY_WORKER_ID") or "",
        help="Lease owner (default hostname-pid)",
    )
    ap.add_argument("--ingest-url", default=os.environ.get("PAPER_DISCOVERY_INGEST_URL") or DEFAULT_INGEST_URL)
    ap.add_argument("--base-url", default=os.environ.get("PAPER_DISCOVERY_BASE_URL") or "")
    ap.add_argument("--idle-sleep", type=int, default=60)
    ap.add_argument(
        "--pause-sleep",
        type=int,
        default=PAUSE_SLEEP_DEFAULT,
        help="Seconds to sleep while the farm flag is off (10–30)",
    )
    args = ap.parse_args(argv)

    workers = clamp_workers(args.workers)
    max_names = int(args.max_names) or workers
    local_plan = bool(args.local_plan or args.no_ingest)
    worker_id = (args.worker_id or "").strip() or default_worker_id()
    token = None if args.no_ingest else ingest_token()
    ingest_url = None if args.no_ingest else args.ingest_url
    if not args.no_ingest and not token:
        print("discovery_worker: PAPER_DISCOVERY_INGEST_TOKEN is required (or pass --no-ingest)", flush=True)
        return 2

    # Claim mode downloads prod's shared tape. The JSON file is only the
    # --local-plan / offline tape (ops used to drop a '{}' placeholder).
    if local_plan:
        hist = state_root() / "crypto_history_5m.json"
        if not hist.exists():
            print(f"discovery_worker: missing {hist} — run scripts/fetch_history.py", flush=True)
            return 2

    base = _base_url(args.ingest_url, args.base_url or None)
    print(
        f"discovery_worker: PAPER_STATE={state_root()} workers={workers} "
        f"max_names={max_names} mode={'local-plan' if local_plan else 'claim'} "
        f"worker_id={worker_id} ingest={'off' if args.no_ingest else ingest_url} "
        f"lookback_max={structure_lookback_max()} "
        f"eval_timeout_s={eval_timeout_seconds()}",
        flush=True,
    )

    prev_signals = _install_exit_signals() if not local_plan else {}
    farm_enabled = True
    try:
        return _run_loop(
            args,
            workers=workers,
            max_names=max_names,
            local_plan=local_plan,
            worker_id=worker_id,
            token=token,
            ingest_url=ingest_url,
            base=base,
            farm_enabled=farm_enabled,
        )
    except KeyboardInterrupt:
        print("discovery_worker: stopped", flush=True)
        return 0
    finally:
        _poster.drain()
        if not local_plan:
            _release_remote_safe(base, token, worker_id, status="idle")
        _drop_eval_pool()
        _restore_exit_signals(prev_signals)


def _run_loop(
    args,
    *,
    workers: int,
    max_names: int,
    local_plan: bool,
    worker_id: str,
    token: str | None,
    ingest_url: str | None,
    base: str,
    farm_enabled: bool,
) -> int:
    while True:
        if not args.no_ingest:
            farm_enabled = poll_farm_enabled(base, farm_enabled)
            decision = consider_pause(
                farm_enabled,
                once=args.once,
                ingest_url=ingest_url,
                token=token,
                pause_sleep=args.pause_sleep,
                claim_mode=not local_plan,
                worker_id=worker_id,
                base_url=base,
            )
            if decision == "exit":
                return 0
            if decision == "pause":
                continue
        if local_plan:
            if not args.no_bootstrap and not args.no_ingest:
                try:
                    info = bootstrap_from_prod(base)
                    print(f"discovery_worker: bootstrapped prod {info}", flush=True)
                except Exception as exc:
                    print(f"discovery_worker: bootstrap failed: {exc}", flush=True)
                    if args.once:
                        return 1
                    time.sleep(max(5, args.idle_sleep))
                    continue
            try:
                result = run_batch(
                    workers=workers,
                    max_names=max_names,
                    ingest_url=ingest_url,
                    token=token,
                    claim_mode=False,
                    worker_id=worker_id,
                    sync_base=None if args.no_ingest else base,
                    sync_token=None if args.no_ingest else token,
                )
            except HistoryUnavailable as exc:
                print(
                    f"discovery_worker: shared tape not ready ({exc}); "
                    f"backing off {HISTORY_BACKOFF_SECONDS}s",
                    flush=True,
                )
                if args.once:
                    return 1
                time.sleep(HISTORY_BACKOFF_SECONDS)
                continue
            except SystemExit:
                raise
            except Exception as exc:
                print(f"discovery_worker: batch failed: {exc}", flush=True)
                if args.once:
                    return 1
                time.sleep(max(5, args.idle_sleep))
                continue
        else:
            try:
                # At least one name per slot. A smaller --max-names used to
                # lease a single retest and leave the other core idle.
                claim_n = max(int(max_names), int(workers))
                claimed = _claim_remote(base, token or "", worker_id, claim_n, workers)
            except Exception as exc:
                print(f"discovery_worker: claim failed: {exc}", flush=True)
                if args.once:
                    return 1
                time.sleep(max(5, args.idle_sleep))
                continue
            if claimed.get("paused"):
                decision = consider_pause(
                    False,
                    once=args.once,
                    ingest_url=ingest_url,
                    token=token,
                    pause_sleep=args.pause_sleep,
                    claim_mode=True,
                    worker_id=worker_id,
                    base_url=base,
                )
                if decision == "exit":
                    return 0
                continue
            names = [n for n in (claimed.get("names") or []) if isinstance(n, str)]
            refilled = claimed.get("refilled") or []
            if refilled:
                print(f"discovery_worker: prod refilled {len(refilled)} names", flush=True)
            if not names:
                print("discovery_worker: claim returned no names", flush=True)
                try:
                    if ingest_url and token:
                        _post_ingest(
                            ingest_url,
                            token,
                            [],
                            [],
                            None,
                            heartbeat="idle",
                            worker_id=worker_id,
                        )
                except Exception as exc:
                    print(f"discovery_worker: idle heartbeat failed: {exc}", flush=True)
                if args.once:
                    return 0
                time.sleep(max(5, args.idle_sleep))
                continue
            try:
                trials = claimed.get("trials") if isinstance(claimed.get("trials"), dict) else {}
                from hedge_fund.trading.trials import n_trials_for_retest

                retest_names = [
                    n for n in (claimed.get("retest") or []) if isinstance(n, str)
                ]
                retest_set = set(retest_names)
                fresh_trials = trials.get("n_trials")
                retest_trials = n_trials_for_retest(trials)
                trial_of = {
                    name: retest_trials if name in retest_set else fresh_trials
                    for name in names
                }

                def bind_claim(body: dict) -> list[str]:
                    got = [n for n in (body.get("names") or []) if isinstance(n, str)]
                    got_retests = {
                        n for n in (body.get("retest") or []) if isinstance(n, str)
                    }
                    census = body.get("trials") if isinstance(body.get("trials"), dict) else {}
                    n_fresh = census.get("n_trials")
                    n_re = n_trials_for_retest(census)
                    for name in got:
                        if name in got_retests:
                            retest_set.add(name)
                            trial_of[name] = n_re
                        else:
                            trial_of[name] = n_fresh
                    return got

                def claim_more(n: int) -> list[str]:
                    body = _claim_remote(base, token or "", worker_id, int(n), workers)
                    if body.get("paused") or body.get("stale_rules"):
                        return []
                    return bind_claim(body)

                def trials_for(name: str):
                    return trial_of.get(name)

                result = run_batch(
                    workers=workers,
                    max_names=max_names,
                    ingest_url=ingest_url,
                    token=token,
                    names=names,
                    claim_mode=True,
                    worker_id=worker_id,
                    sync_base=base,
                    sync_token=token,
                    n_trials=fresh_trials,
                    retest_names=retest_names,
                    n_trials_retest=retest_trials,
                    claim_more=claim_more,
                    # One extra wave leased behind the running slots, so a
                    # finish starts the next name without waiting on claim.
                    prefetch=workers,
                    trials_for=trials_for,
                )
            except HistoryUnavailable as exc:
                print(
                    f"discovery_worker: shared tape not ready ({exc}); "
                    f"backing off {HISTORY_BACKOFF_SECONDS}s",
                    flush=True,
                )
                _release_remote_safe(base, token, worker_id, names=names, status="idle")
                if args.once:
                    return 1
                time.sleep(HISTORY_BACKOFF_SECONDS)
                continue
            except SystemExit:
                raise
            except Exception as exc:
                print(f"discovery_worker: batch failed: {exc}", flush=True)
                _release_remote_safe(base, token, worker_id, names=names, status="idle")
                if args.once:
                    return 1
                time.sleep(max(5, args.idle_sleep))
                continue
        print(f"discovery_worker: batch {result}", flush=True)
        if args.once:
            return 0
        if result.get("evaluated"):
            continue
        time.sleep(max(5, args.idle_sleep))


if __name__ == "__main__":
    raise SystemExit(main())
