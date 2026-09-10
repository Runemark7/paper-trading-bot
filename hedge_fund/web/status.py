"""Honest running-now vs in-progress snapshot for the React UI.

Reads PAPER_STATE only. Does not start jobs, does not claim a process is
alive, and does not invent telemetry the files do not contain.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hedge_fund.paths import state_root
from hedge_fund.trading.champions import load_graduated, load_pool
from hedge_fund.trading.constants import (
    CYCLE_INTERVAL_SECONDS,
    GRADUATED_PAPER,
    QUAL_TIMEFRAME,
    TRADE_EVALUATION_LIMIT,
)
from hedge_fund.trading.heartbeat import HEARTBEAT_SECONDS
from hedge_fund.trading.open_lots import open_lots_snapshot, paper_book_dbs
from hedge_fund.trading.stamps import HEARTBEAT_STAMP, PIPELINE_STAMP, read_json_stamp
from hedge_fund.trading.store import TradeStore
from hedge_fund.trading.universe import untested_candidates

# Stamp "started" older than this with no finish is labeled stale, not running.
STALE_PIPELINE_SECONDS = 2 * 3600
HEARTBEAT_RECENT_SECONDS = 90


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    return _iso(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))


def _max_ts(*values: str | None) -> str | None:
    best = None
    for v in values:
        if v and (best is None or v > best):
            best = v
    return best


def _file_mtime_note(path: Path) -> dict:
    return {
        "path": path.name,
        "mtime": _mtime_iso(path),
        "exists": path.exists(),
    }


def _last_cycle_at(dbs: list[str]) -> str | None:
    """Last TradingLoop.run_cycle equity snapshot — not heartbeat saved_at."""
    best = None
    for db in dbs:
        try:
            st = TradeStore(db)
            hist = st.equity_history()
            if hist:
                ts = hist[-1]["ts"]
                if ts and (best is None or ts > best):
                    best = ts
        except Exception:
            continue
    return best


def _account_saved_at(dbs: list[str]) -> str | None:
    best = None
    for db in dbs:
        try:
            st = TradeStore(db)
            saved = st.load_account_state()
            ts = (saved or {}).get("saved_at")
            if ts and (best is None or ts > best):
                best = ts
        except Exception:
            continue
    return best


def _infer_next_cycle(last_iso: str | None, now: datetime) -> dict:
    last = _parse_iso(last_iso)
    note = (
        "Inferred from last cycle + CYCLE_INTERVAL_SECONDS. The sidecar sleeps "
        f"{CYCLE_INTERVAL_SECONDS}s around the clock (no night skip). "
        "This API does not see the sidecar process."
    )
    if last is None:
        return {
            "at": None,
            "inferred": True,
            "in_window_now": True,
            "note": "No cycle snapshot yet. " + note,
        }
    nxt = last + timedelta(seconds=CYCLE_INTERVAL_SECONDS)
    overdue = nxt <= now
    extra = " Interval has elapsed; sidecar may be due or down." if overdue else ""
    return {
        "at": _iso(nxt),
        "inferred": True,
        "overdue": overdue,
        "in_window_now": True,
        "note": note + extra,
    }


def _pipeline_block(now: datetime) -> dict:
    stamp = read_json_stamp(PIPELINE_STAMP)
    if not stamp:
        return {
            "job_runner": False,
            "phase": None,
            "status": None,
            "certainty": "no_signal",
            "running": False,
            "stale": False,
            "started_at": None,
            "finished_at": None,
            "at": None,
            "note": (
                "No pipeline_stamp.json. live_cycle.py writes one when the sidecar "
                "runs; without it this API cannot say discovery/tournament/cycle "
                "is running — only last-known file times below."
            ),
        }

    started = _parse_iso(stamp.get("started_at") or stamp.get("at"))
    status = stamp.get("status")
    age = (now - started).total_seconds() if started else None
    stale = bool(status == "started" and age is not None and age > STALE_PIPELINE_SECONDS)
    # Honest: a fresh "started" stamp is last-known phase, not process liveness.
    claimed_in_progress = status == "started" and not stale
    return {
        "job_runner": False,
        "phase": stamp.get("phase"),
        "status": status,
        "certainty": "stamp",
        "running": False,
        "stamp_says_in_progress": claimed_in_progress,
        "stale": stale,
        "started_at": stamp.get("started_at"),
        "finished_at": stamp.get("finished_at"),
        "at": stamp.get("at"),
        "source": stamp.get("source"),
        "note": (
            "Stamp from live_cycle.py. Process liveness is not verified. "
            + ("Stamp is stale (started with no finish). " if stale else "")
            + ("Showing last-known phase, not a live spinner." if claimed_in_progress else "")
        ).strip(),
    }


def _heartbeat_block(now: datetime) -> dict:
    stamp = read_json_stamp(HEARTBEAT_STAMP)
    configured = {
        "configured_interval_seconds": HEARTBEAT_SECONDS,
        "role": "stop/TP only — does not open trades",
    }
    if not stamp:
        return {
            **configured,
            "last_pass_at": None,
            "recent": False,
            "certainty": "no_signal",
            "on": None,
            "note": (
                "No heartbeat.json. The 30s sidecar is configured in compose/k8s "
                "but this API cannot see that process until it writes a stamp."
            ),
        }
    last = _parse_iso(stamp.get("last_pass_at"))
    age = (now - last).total_seconds() if last else None
    recent = bool(age is not None and age <= HEARTBEAT_RECENT_SECONDS)
    return {
        **configured,
        "last_pass_at": stamp.get("last_pass_at"),
        "last_closed": stamp.get("closed"),
        "accounts_checked": stamp.get("accounts_checked"),
        "recent": recent,
        "certainty": "last_known",
        "on": None,
        "note": (
            "Last heartbeat pass from stamp. Recent means the file is fresh, "
            "not that the process is proven alive."
        ),
    }


def _discovery_block(root: Path) -> dict:
    from hedge_fund.trading.discovery import latest_eval_per_strategy, load_discovery_log, newest_eval

    path = root / "discovery_log.json"
    last_tested_at = None
    last_strategy = None
    last_qualified = None
    count = 0
    unique = 0
    if path.exists():
        try:
            log = load_discovery_log()
            count = len(log)
            unique = len(latest_eval_per_strategy(log))
            row = newest_eval(log)
            if row:
                last_tested_at = row.get("tested_at")
                last_strategy = row.get("strategy")
                last_qualified = row.get("qualified")
        except (OSError, ValueError):
            pass
    return {
        "certainty": "last_known" if path.exists() else "no_signal",
        "running": False,
        "log_count": count,
        "unique_tested": unique,
        "last_tested_at": last_tested_at,
        "last_strategy": last_strategy,
        "last_qualified": last_qualified,
        "file": _file_mtime_note(path),
        "note": (
            "Discovery appends discovery_log.json after each name. "
            "log_count is raw rows; unique_tested is latest-eval-per-name. "
            "GET /api/discovery/summary has tested / in-flight / leftover buckets. "
            "Rejected names are parked forever. "
            "A quiet log is silence, not a running job."
        ),
    }


def _graduation_block() -> dict:
    grads = load_graduated()
    last = None
    for g in grads:
        ts = g.get("graduated_at")
        if ts and (last is None or ts > last.get("graduated_at", "")):
            last = g
    return {
        "certainty": "last_known" if grads else "no_signal",
        "running": False,
        "count": len(grads),
        "last_name": (last or {}).get("name"),
        "last_status": (last or {}).get("status"),
        "last_graduated_at": (last or {}).get("graduated_at"),
        "token": GRADUATED_PAPER,
        "note": (
            f"{GRADUATED_PAPER} means graduated paper (paper P&L greater than "
            f"buy-and-hold after {TRADE_EVALUATION_LIMIT} closed trades), not live money."
        ),
    }


def build_status() -> dict:
    now = datetime.now(timezone.utc)
    root = state_root()
    dbs = paper_book_dbs()
    lots = open_lots_snapshot()
    pool = load_pool()
    champs = list(pool.get("champions") or [])
    names = [c.get("name") for c in champs if c.get("name")]
    fallback = os.environ.get("PAPER_STRATEGY", "sma_stack")
    active = names or [fallback]
    mode = "champion_accounts" if names else "sma_stack_fallback"
    last_cycle = _last_cycle_at(dbs)
    pipeline = _pipeline_block(now)
    discovery = _discovery_block(root)
    graduation = _graduation_block()
    heartbeat = _heartbeat_block(now)
    grad = load_graduated()
    blocked = {n for n in names if n} | {g.get("name") for g in grad if g.get("name")}
    replenish_needed = bool(untested_candidates(blocked))

    # Replenish is implied by leftover universe names, not a slot cap.
    stamp_phase = pipeline.get("phase")
    stamp_in_progress = bool(pipeline.get("stamp_says_in_progress"))
    replenish_phase = stamp_in_progress and stamp_phase in ("tournament", "collect_live_results")

    return {
        "paper_only": True,
        "as_of": _iso(now),
        "running_now": {
            "label": "Live paper book",
            "certainty": "last_known",
            "strategy": {
                "mode": mode,
                "active": active,
                "fallback": fallback,
                "source": "champions.json when non-empty, else PAPER_STRATEGY",
            },
            "accounts": {
                "count": len(dbs),
                "kind": "isolated_10k_paper" if any(Path(d).name.startswith("trades_") for d in dbs) else "legacy_single",
                "names": [Path(d).stem.removeprefix("trades_") if Path(d).name.startswith("trades_") else "legacy" for d in dbs],
            },
            "cycle": {
                "interval_seconds": CYCLE_INTERVAL_SECONDS,
                "bar_timeframe": QUAL_TIMEFRAME,
                "window": "24/7",
                "last_cycle_at": last_cycle,
                "account_saved_at": _account_saved_at(dbs),
                "next": _infer_next_cycle(last_cycle, now),
            },
            "positions_open": lots["open_lots"],
            "open_lots": lots["open_lots"],
            "open_lots_by_account": lots["by_account"],
            "open_lots_unit": "open_lots",
            "heartbeat": heartbeat,
            "regime": {
                "gates_live_book": False,
                "display_only": True,
                "note": "Live cycle uses regime=None. /api/regime is display-only.",
            },
        },
        "in_progress": {
            "job_runner": False,
            "note": (
                "There is no job-runner API. Pipeline rows are last-known state "
                "and timestamps. A stamp may say a live_cycle phase started; "
                "that is not process liveness."
            ),
            "pipeline": pipeline,
            "discovery": discovery,
            "tournament": {
                "certainty": "last_known",
                "running": False,
                "stamp_says_this_phase": stamp_in_progress and stamp_phase == "tournament",
                "active_count": len(champs),
                "evaluation_limit": TRADE_EVALUATION_LIMIT,
                "synced_until": pool.get("synced_until") or None,
                "file": _file_mtime_note(root / "champions.json"),
                "note": (
                    "Active names are on the paper book (see Running now). "
                    "There is no live-slot cap. Qualification/replenish is "
                    "last-known from champions.json and discovery_log.json."
                ),
            },
            "replenish": {
                "certainty": "inferred" if replenish_needed else "last_known",
                "running": False,
                "needed": replenish_needed,
                "stamp_says_this_phase": replenish_phase,
                "note": (
                    "needed means the universe still has names not in the pool "
                    "or graduated.json — not leftover slots under a cap of 20. "
                    "That does not mean replenish is running unless the pipeline "
                    "stamp says so."
                ),
            },
            "graduation": graduation,
            "isolated_cycle": {
                "certainty": "stamp" if pipeline.get("certainty") == "stamp" else "last_known",
                "running": False,
                "stamp_says_this_phase": stamp_in_progress and stamp_phase == "run_isolated",
                "last_cycle_at": last_cycle,
                "note": "Isolated accounts run TradingLoop.run_cycle; last snapshot is last_cycle_at.",
            },
        },
    }
