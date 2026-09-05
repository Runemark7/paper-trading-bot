"""Last-known discovery buckets for GET /api/discovery/summary.

Never claims a sidecar process is alive. in_flight names come from
``discovery_in_flight.json`` only when the pipeline stamp says tournament
is in progress. Otherwise the UI shows idle + last sweep.
"""
from __future__ import annotations

from datetime import datetime, timezone

from hedge_fund.trading.champions import load_graduated, load_pool
from hedge_fund.trading.discovery import latest_eval_per_strategy, load_discovery_log, read_in_flight
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


def build_discovery_summary() -> dict:
    now = datetime.now(timezone.utc)
    pipeline = _pipeline_block(now)
    stamp_in_progress = bool(pipeline.get("stamp_says_in_progress"))
    tournament_now = stamp_in_progress and pipeline.get("phase") == "tournament"

    pool = load_pool()
    champs = [c.get("name") for c in (pool.get("champions") or []) if c.get("name")]
    grads = [g.get("name") for g in load_graduated() if g.get("name")]
    blocked = set(champs) | set(grads)

    log = load_discovery_log()
    tested = [_eval_row(r) for r in latest_eval_per_strategy(log)]
    tested_names = {r["strategy"] for r in tested if r.get("strategy")}
    tested_pass = sum(1 for r in tested if r.get("qualified"))
    tested_fail = len(tested) - tested_pass

    leftovers = untested_candidates(blocked, generate_universe())
    queued = [n for n in leftovers if n not in tested_names]

    flight = read_in_flight() if tournament_now else None
    flight_names = []
    batch_size = None
    flight_started = None
    if tournament_now and flight:
        raw_names = flight.get("names") or []
        if isinstance(raw_names, list):
            flight_names = [n for n in raw_names if isinstance(n, str)]
        batch_size = flight.get("batch_size")
        if batch_size is None:
            batch_size = len(flight_names)
        flight_started = flight.get("started_at")
    elif tournament_now:
        batch_size = None
        flight_started = pipeline.get("started_at")

    if tournament_now and flight_names:
        inflight_set = set(flight_names)
        queued = [n for n in queued if n not in inflight_set]

    last_tested_at = tested[0]["tested_at"] if tested else None
    last_strategy = tested[0]["strategy"] if tested else None

    if tournament_now and flight_names:
        flight_note = (
            f"Stamp says tournament since {pipeline.get('started_at')}. "
            f"{len(flight_names)} leftover names written at batch start. "
            "Process liveness is not verified."
        )
    elif tournament_now:
        flight_note = (
            f"Stamp says tournament since {pipeline.get('started_at')}. "
            "Name list was not persisted — not inventing names. "
            "Process liveness is not verified."
        )
    elif last_tested_at:
        flight_note = f"idle — last sweep {last_tested_at}"
    else:
        flight_note = "idle — no sweep recorded yet"

    return {
        "paper_only": True,
        "as_of": _iso(now),
        "certainty": "stamp" if tournament_now else ("last_known" if log else "no_signal"),
        "running": False,
        "stamp_says_in_progress": tournament_now,
        "tested": tested,
        "in_flight": {
            "active": tournament_now,
            "running": False,
            "names": flight_names,
            "batch_size": batch_size,
            "started_at": flight_started,
            "stamp_started_at": pipeline.get("started_at") if tournament_now else None,
            "note": flight_note,
        },
        "queued": queued,
        "untested": queued,
        "counts": {
            "tested_pass": tested_pass,
            "tested_fail": tested_fail,
            "tested": len(tested),
            "untested": len(queued),
            "champions": len(champs),
            "graduated": len(grads),
            "in_flight": len(flight_names) if tournament_now else 0,
        },
        "last_tested_at": last_tested_at,
        "last_strategy": last_strategy,
        "note": (
            "Last-known buckets from discovery_log.json, champions.json, "
            "graduated.json, and (when the tournament stamp is in progress) "
            "discovery_in_flight.json. A stamp is not process liveness."
        ),
    }
