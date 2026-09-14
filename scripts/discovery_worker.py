"""Windows discovery farm — same OOS gates as tournament, not on the k8s cycle.

Evaluates never-tested names (fail-once, auto-refill, rm_v1, 5m windows)
against local ``crypto_history_5m.json`` and POSTs results to prod
``/api/discovery/ingest``. Cluster ``live_cycle`` stays live-only.

Leave this process running. Pause/resume from the Discovery page
(``POST /api/discovery/farm``) — the loop polls prod and idles instead
of exiting so Start works without relaunching on jensa.

GPU is unused (no CUDA rewrite). Modest CPU parallelism (2–4) on an i5.

    python scripts/discovery_worker.py --workers 2
    python scripts/discovery_worker.py --once --workers 1 --no-ingest

Env:
  PAPER_STATE                        local state dir (history + bootstrap cache)
  PAPER_DISCOVERY_INGEST_URL         default https://trading.runevibe.se/api/discovery/ingest
  PAPER_DISCOVERY_INGEST_TOKEN       shared secret (required unless --no-ingest)
  PAPER_DISCOVERY_BASE_URL           prod origin for bootstrap GETs
  DISCOVERY_WORKERS                  default 2
  DISCOVERY_STRUCTURE_LOOKBACK_MAX   default 96; 0 disables. Farm ops, not an OOS gate.
  DISCOVERY_EVAL_TIMEOUT_SECONDS     default 600; 0 disables. Coarse per-name backstop.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hedge_fund.paths import state_root
from hedge_fund.trading.champions import load_graduated, load_pool, save_graduated, save_pool
from hedge_fund.trading.constants import (
    DISCOVER_CYCLE_MAX_NAMES,
    DISCOVERY_REFILL_BATCH_SIZE,
    QUAL_N_WINDOWS,
    QUAL_STRIDE,
    QUAL_WINDOW_BARS,
    qual_keep_bars,
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
_N_WINDOWS = QUAL_N_WINDOWS


def _init_pool(slices, bh, sma, n_windows: int) -> None:
    global _SLICES, _BH, _SMA, _N_WINDOWS
    _SLICES = slices
    _BH = bh
    _SMA = sma
    _N_WINDOWS = n_windows


def _eval_name(name: str) -> dict | None:
    return evaluate_strategy_record(
        name,
        _SLICES,
        n_windows=_N_WINDOWS,
        bh_oos_pnl=_BH,
        sma_stack_oos_pnl=_SMA,
    )


def _new_eval_pool(n_workers: int, slices, bh, sma, n_windows: int):
    return multiprocessing.Pool(
        processes=n_workers,
        initializer=_init_pool,
        initargs=(slices, bh, sma, n_windows),
    )


def _collect_wave(
    asyncs: list[tuple[str, object]],
    timeout_s: float,
    *,
    sleeper=time.sleep,
    clock=time.monotonic,
) -> tuple[list[tuple[str, dict | None]], list[str]]:
    """Wait for a wave of apply_async results.

    *timeout_s* <= 0 waits until every name finishes. Otherwise any name
    still running after the wave deadline is returned as stuck (caller
    fail-parks and recycles the pool).
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


def _http_json(url: str, *, token: str | None = None, data: dict | None = None, timeout: int = 60):
    headers = {"Accept": "application/json"}
    body = None
    method = "GET"
    if data is not None:
        method = "POST"
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
        if token:
            headers["X-Discovery-Token"] = token
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
        summary = _http_json(f"{base_url.rstrip('/')}/api/discovery/summary?compact=1", timeout=20)
    except Exception:
        return last_known
    return farm_enabled_from_summary(summary, last_known)


def pause_sleep_seconds(raw: int) -> int:
    return max(PAUSE_SLEEP_MIN, min(int(raw), PAUSE_SLEEP_MAX))


def consider_pause(
    enabled: bool,
    *,
    once: bool,
    ingest_url: str | None,
    token: str | None,
    pause_sleep: int,
    sleeper=time.sleep,
) -> str:
    """Idle when the farm flag is off. Does not exit the worker process.

    Returns ``run``, ``pause``, or ``exit`` (``--once`` while paused).
    """
    if enabled:
        return "run"
    print("discovery_worker: farm paused — idling (near-zero CPU)", flush=True)
    clear_in_flight()
    if ingest_url and token:
        try:
            _post_ingest(ingest_url, token, [], [], None, clear=True, heartbeat="paused")
        except Exception as exc:
            print(f"discovery_worker: pause ingest failed: {exc}", flush=True)
    if once:
        return "exit"
    sleeper(pause_sleep_seconds(pause_sleep))
    return "pause"


def _post_ingest(
    url: str,
    token: str,
    evaluations: list[dict],
    extended: list[str],
    flight: dict | None,
    *,
    clear: bool = False,
    heartbeat: str | None = None,
) -> dict:
    payload = {
        "evaluations": evaluations,
        "extended_names": extended,
        "source": "windows_worker",
        "paper_only": True,
    }
    if heartbeat:
        payload["heartbeat"] = {"status": heartbeat, "source": "windows_worker"}
    if clear:
        payload["clear_in_flight"] = True
    elif flight:
        payload["in_flight"] = flight
    return _http_json(url, token=token, data=payload, timeout=90)


def run_batch(
    *,
    workers: int,
    max_names: int,
    ingest_url: str | None,
    token: str | None,
    n_windows: int = QUAL_N_WINDOWS,
) -> dict:
    data = _load_qual_history(keep_bars=qual_keep_bars(n_windows=n_windows))
    if not data:
        raise SystemExit(
            "crypto_history_5m.json missing or empty under PAPER_STATE. "
            "Run: python scripts/fetch_history.py"
        )
    slices = _window_slices(data, QUAL_WINDOW_BARS, n_windows, QUAL_STRIDE)
    del data
    if len(slices) != n_windows:
        raise SystemExit("history too short for qualification windows")

    planned, rotated, added = _plan_batch(max_names)
    if added:
        print(f"discovery_worker: refilled {len(added)} names", flush=True)
    if not planned:
        print("discovery_worker: no never-tested names (recipe dry or all parked)", flush=True)
        if ingest_url and token:
            _post_ingest(ingest_url, token, [], added, None, clear=True, heartbeat="idle")
        clear_in_flight()
        return {"evaluated": 0, "qualified": 0, "planned": [], "refilled": added}

    bh, sma = _benchmark_oos(slices)
    write_in_flight(
        planned,
        current=None,
        remaining=planned,
        completed=[],
        batch_size=len(planned),
        source="windows_worker",
    )
    if ingest_url and token:
        _post_ingest(
            ingest_url,
            token,
            [],
            added,
            {
                "names": planned,
                "remaining": planned,
                "completed": [],
                "batch_size": len(planned),
                "current": None,
            },
            heartbeat="running",
        )

    records: list[dict] = []
    completed: list[str] = []
    lookback_cap = structure_lookback_max()
    timeout_s = eval_timeout_seconds()

    def finish(name: str, record: dict | None) -> None:
        completed.append(name)
        remaining = [n for n in planned if n not in completed]
        if record is not None:
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
            if ingest_url and token:
                _post_ingest(
                    ingest_url,
                    token,
                    [record],
                    [],
                    {
                        "names": remaining,
                        "remaining": remaining,
                        "completed": completed,
                        "batch_size": len(planned),
                    },
                    heartbeat="running",
                )
        write_in_flight(
            remaining,
            current=None,
            remaining=remaining,
            completed=completed,
            batch_size=len(planned),
            source="windows_worker",
        )

    cheap: list[str] = []
    for name in planned:
        expensive = lookback_too_expensive_reason(name, lookback_cap)
        if expensive:
            finish(name, ops_fail_record(name, expensive))
        else:
            cheap.append(name)

    n_workers = max(1, min(int(workers), len(cheap) or 1))
    try:
        if not cheap:
            pass
        elif n_workers == 1 and timeout_s <= 0:
            _init_pool(slices, bh, sma, n_windows)
            for name in cheap:
                finish(name, _eval_name(name))
        else:
            pool = _new_eval_pool(n_workers, slices, bh, sma, n_windows)
            try:
                i = 0
                while i < len(cheap):
                    wave = cheap[i : i + n_workers]
                    asyncs = [(n, pool.apply_async(_eval_name, (n,))) for n in wave]
                    done, stuck = _collect_wave(asyncs, timeout_s)
                    for name, record in done:
                        finish(name, record)
                    if stuck:
                        reason = eval_timeout_reason(timeout_s)
                        for name in stuck:
                            finish(name, ops_fail_record(name, reason))
                        pool.terminate()
                        pool.join()
                        if i + len(wave) < len(cheap):
                            pool = _new_eval_pool(
                                n_workers, slices, bh, sma, n_windows
                            )
                    i += len(wave)
            finally:
                try:
                    pool.terminate()
                    pool.join()
                except Exception:
                    pass
    finally:
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
                _post_ingest(ingest_url, token, [], [], None, clear=True, heartbeat="idle")
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Windows discovery farm (paper only)")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("DISCOVERY_WORKERS") or 2))
    ap.add_argument("--max-names", type=int, default=0, help="Names per batch (default = workers)")
    ap.add_argument("--once", action="store_true", help="One batch then exit")
    ap.add_argument("--no-ingest", action="store_true", help="Local eval only; do not POST prod")
    ap.add_argument("--no-bootstrap", action="store_true", help="Skip pulling prod log/pool")
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

    workers = max(1, min(int(args.workers), 4))
    max_names = int(args.max_names) or workers
    token = None if args.no_ingest else ingest_token()
    ingest_url = None if args.no_ingest else args.ingest_url
    if not args.no_ingest and not token:
        print("discovery_worker: PAPER_DISCOVERY_INGEST_TOKEN is required (or pass --no-ingest)", flush=True)
        return 2

    hist = state_root() / "crypto_history_5m.json"
    if not hist.exists():
        print(f"discovery_worker: missing {hist} — run scripts/fetch_history.py", flush=True)
        return 2

    base = _base_url(args.ingest_url, args.base_url or None)
    print(
        f"discovery_worker: PAPER_STATE={state_root()} workers={workers} "
        f"max_names={max_names} ingest={'off' if args.no_ingest else ingest_url} "
        f"lookback_max={structure_lookback_max()} "
        f"eval_timeout_s={eval_timeout_seconds()}",
        flush=True,
    )

    farm_enabled = True
    while True:
        if not args.no_ingest:
            farm_enabled = poll_farm_enabled(base, farm_enabled)
            decision = consider_pause(
                farm_enabled,
                once=args.once,
                ingest_url=ingest_url,
                token=token,
                pause_sleep=args.pause_sleep,
            )
            if decision == "exit":
                return 0
            if decision == "pause":
                continue
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
            )
        except SystemExit:
            raise
        except Exception as exc:
            print(f"discovery_worker: batch failed: {exc}", flush=True)
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
