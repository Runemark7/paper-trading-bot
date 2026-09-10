"""Last-known discovery buckets for GET /api/discovery/summary.

Never claims a sidecar process is alive. in_flight names come from
``discovery_in_flight.json`` when the tournament stamp is started (including
stale). Otherwise the UI shows idle + newest eval — or stuck/overdue copy
when the stamp is stale or evaluations have gone quiet with leftover work.
"""
from __future__ import annotations

from datetime import datetime, timezone

from hedge_fund.trading.champions import load_graduated, load_pool
from hedge_fund.trading.constants import (
    DISCOVERY_QUIET_SECONDS,
)
from hedge_fund.trading.discovery import (
    evals_on_utc_date,
    failed_discovery_names,
    latest_eval_per_strategy,
    load_discovery_log,
    newest_eval,
    parse_tested_at,
    prioritize_leftovers,
    read_in_flight,
)
from hedge_fund.trading.universe import generate_universe, untested_candidates
from hedge_fund.web.status import _iso, _pipeline_block


def _eval_row(row: dict) -> dict:
    reasons = row.get("fail_reasons")
    if not isinstance(reasons, list):
        reasons = []
    return {
        "strategy": row.get("strategy"),
        "tested_at": row.get("tested_at"),
        "qualified": bool(row.get("qualified")),
        "sharpe": row.get("sharpe"),
        "trades": row.get("trades"),
        "test_pnl": row.get("test_pnl"),
        "train_pnl": row.get("train_pnl"),
        "win_rate_pct": row.get("win_rate_pct"),
        "fail_reasons": reasons,
        "timeframe": row.get("timeframe"),
        "risk_policy": row.get("risk_policy"),
        "bh_oos_pnl": row.get("bh_oos_pnl"),
        "sma_stack_oos_pnl": row.get("sma_stack_oos_pnl"),
    }


def _sort_tested_newest_first(rows: list[dict]) -> list[dict]:
    def key(row: dict):
        ts = parse_tested_at(row.get("tested_at") if isinstance(row.get("tested_at"), str) else None)
        return ts or datetime.min.replace(tzinfo=timezone.utc)

    return sorted(rows, key=key, reverse=True)


def build_discovery_summary() -> dict:
    now = datetime.now(timezone.utc)
    pipeline = _pipeline_block(now)
    stamp_in_progress = bool(pipeline.get("stamp_says_in_progress"))
    stamp_stale = bool(pipeline.get("stale"))
    tournament_stamp = pipeline.get("phase") == "tournament" and pipeline.get("status") == "started"
    tournament_now = stamp_in_progress and pipeline.get("phase") == "tournament"

    pool = load_pool()
    champs = [c.get("name") for c in (pool.get("champions") or []) if c.get("name")]
    grads = [g.get("name") for g in load_graduated() if g.get("name")]
    blocked = set(champs) | set(grads)

    universe = generate_universe()
    log = load_discovery_log()
    tested = _sort_tested_newest_first([_eval_row(r) for r in latest_eval_per_strategy(log)])
    tested_names = {r["strategy"] for r in tested if r.get("strategy")}
    tested_pass = sum(1 for r in tested if r.get("qualified"))
    tested_fail = len(tested) - tested_pass

    leftovers = untested_candidates(blocked, universe)
    failed_names = failed_discovery_names(log)
    queued = [n for n in leftovers if n not in tested_names and n not in failed_names]
    rejected_parked = [n for n in leftovers if n in failed_names]
    eligible = prioritize_leftovers(leftovers, log, now=now)

    newest = newest_eval(log) or newest_eval(tested)
    last_tested_at = newest.get("tested_at") if newest else None
    last_strategy = newest.get("strategy") if newest else None
    last_ts = parse_tested_at(last_tested_at if isinstance(last_tested_at, str) else None)
    last_eval_age_seconds = (now - last_ts).total_seconds() if last_ts else None
    evals_today = evals_on_utc_date(log, now)

    work_now = bool(eligible)
    quiet = bool(
        work_now
        and last_eval_age_seconds is not None
        and last_eval_age_seconds > DISCOVERY_QUIET_SECONDS
    )
    stuck = bool((stamp_stale and tournament_stamp) or (quiet and not tournament_now))
    if stamp_stale and tournament_stamp:
        stuck_reason = (
            "discovery stuck — tournament stamp started with no finish (stale). "
            "Not idle."
        )
    elif quiet and not tournament_now:
        age_h = None if last_eval_age_seconds is None else int(last_eval_age_seconds // 3600)
        stuck_reason = (
            "discovery stuck / cycle overdue — "
            f"no new evaluations for {age_h}h while never-tested leftover names remain."
        )
    else:
        stuck_reason = None

    flight = read_in_flight() if (tournament_now or (stamp_stale and tournament_stamp)) else None
    flight_names = []
    batch_size = None
    flight_started = None
    current = None
    remaining: list[str] = []
    completed: list[str] = []
    show_flight = bool(tournament_now or (stamp_stale and tournament_stamp and flight))
    if show_flight and flight:
        raw_names = flight.get("names") or []
        if isinstance(raw_names, list):
            flight_names = [n for n in raw_names if isinstance(n, str)]
        batch_size = flight.get("batch_size")
        if batch_size is None:
            batch_size = len(flight_names)
        flight_started = flight.get("started_at")
        current = flight.get("current")
        raw_rem = flight.get("remaining") or []
        if isinstance(raw_rem, list):
            remaining = [n for n in raw_rem if isinstance(n, str)]
        raw_done = flight.get("completed") or []
        if isinstance(raw_done, list):
            completed = [n for n in raw_done if isinstance(n, str)]
    elif tournament_now:
        batch_size = None
        flight_started = pipeline.get("started_at")

    if show_flight and flight_names:
        inflight_set = set(flight_names)
        queued = [n for n in queued if n not in inflight_set]

    if stuck and stuck_reason:
        flight_note = stuck_reason
    elif tournament_now and flight_names:
        cur = f" current {current}." if current else ""
        flight_note = (
            f"Stamp says tournament since {pipeline.get('started_at')}. "
            f"Cycle budget {len(flight_names)} outstanding / batch {batch_size}."
            f"{cur} Process liveness is not verified."
        )
    elif tournament_now:
        flight_note = (
            f"Stamp says tournament since {pipeline.get('started_at')}. "
            "Name list was not persisted — not inventing names. "
            "Process liveness is not verified."
        )
    elif last_tested_at:
        flight_note = (
            f"idle — last eval {last_tested_at}"
            + (f" · {last_strategy}" if last_strategy else "")
        )
    else:
        flight_note = "idle — no sweep recorded yet"

    return {
        "paper_only": True,
        "as_of": _iso(now),
        "certainty": "stamp" if tournament_now else ("last_known" if log else "no_signal"),
        "running": False,
        "stamp_says_in_progress": tournament_now,
        "stale": stamp_stale,
        "stuck": stuck,
        "stuck_reason": stuck_reason,
        "tested": tested,
        "in_flight": {
            "active": bool(tournament_now or (stamp_stale and tournament_stamp)),
            "running": False,
            "stale": bool(stamp_stale and tournament_stamp),
            "names": flight_names,
            "current": current,
            "remaining": remaining,
            "completed": completed,
            "batch_size": batch_size,
            "started_at": flight_started,
            "stamp_started_at": pipeline.get("started_at") if (tournament_now or tournament_stamp) else None,
            "note": flight_note,
        },
        "queued": queued,
        "untested": queued,
        "counts": {
            "universe": len(universe),
            "tested_pass": tested_pass,
            "tested_fail": tested_fail,
            "tested": len(tested),
            "unique_tested": len(tested),
            "log_rows": len(log),
            "untested": len(queued),
            "leftovers": len(leftovers),
            "rejected_parked": len(rejected_parked),
            "eligible": len(eligible),
            "champions": len(champs),
            "graduated": len(grads),
            "in_flight": len(flight_names) if show_flight else 0,
            "evals_today": evals_today,
        },
        "last_tested_at": last_tested_at,
        "last_strategy": last_strategy,
        "last_eval_age_seconds": last_eval_age_seconds,
        "note": (
            "Last-known buckets from discovery_log.json, champions.json, "
            "graduated.json, and (when the tournament stamp is started) "
            "discovery_in_flight.json. counts.tested is unique strategy names "
            "(latest eval per name), not the number of log rows. "
            "Already tested · rejected is parked forever — not a cooldown "
            "retest queue. A stamp is not process liveness."
        ),
    }
