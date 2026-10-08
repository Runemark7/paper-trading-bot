"""Last-known discovery buckets for GET /api/discovery/summary.

Never claims a sidecar process is alive. in_flight names come from
``discovery_in_flight.json`` when the tournament stamp is started (including
stale), and from active claim leases (``discovery_leases.json``) so several
stateless workers show up together. Otherwise the UI shows idle + newest
eval — or stuck/overdue copy when the stamp is stale or evaluations have
gone quiet with leftover work.

``lists=False`` / ``?compact=1`` keeps counts, farm, and stuck copy but omits
tested / queued / extended_names / in-flight name lists so UI polls (Champions
teaser, farm heartbeat) do not ship the full unique-tested log.
"""
from __future__ import annotations

from datetime import datetime, timezone

from hedge_fund.trading.champions import load_graduated, load_pool
from hedge_fund.trading.constants import (
    DISCOVERY_QUIET_SECONDS,
)
from hedge_fund.trading.discovery import (
    evals_on_utc_date,
    latest_eval_per_strategy,
    load_discovery_log,
    newest_eval,
    parse_tested_at,
    prioritize_leftovers,
    read_in_flight,
)
from hedge_fund.trading.discovery_mode import discovery_on_cycle
from hedge_fund.trading.farm import farm_status_block
from hedge_fund.trading.leases import lease_snapshot, load_refill_status
from hedge_fund.trading.refill import load_extended_names
from hedge_fund.trading.discovery_results import page_result_records, peek_result_counts
from hedge_fund.trading.tested_index import ensure_tested_index, merge_tested_rows
from hedge_fund.trading.universe import generate_universe, untested_candidates
from hedge_fund.web.status import _iso, _pipeline_block
from hedge_fund.web.ttl_cache import StaleCache


def compact_query(value: str | None) -> bool:
    """True for ``?compact=1`` / true / yes — UI polls that must not ship lists."""
    return str(value or "").strip().lower() in ("1", "true", "yes")


def _query_int(value: str | None, *, default: int, name: str, lo: int, hi: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None
    if number < lo or number > hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return number


def discovery_results_response(qs: dict | None = None) -> dict:
    """GET /api/discovery/results — paged uncapped records. Read-only."""
    query = qs or {}
    raw_status = str(query.get("status") or "").strip().lower()
    status = raw_status or None
    if status not in (None, "pass", "fail"):
        raise ValueError("status must be pass or fail")
    offset = _query_int(query.get("offset"), default=0, name="offset", lo=0, hi=1_000_000_000)
    limit = _query_int(query.get("limit"), default=100, name="limit", lo=1, hi=1000)
    rows, total, counts = page_result_records(offset=offset, limit=limit, status=status)
    return {
        "paper_only": True,
        "offset": offset,
        "limit": limit,
        "total": total,
        "status": status,
        "unique": counts["unique"],
        "tested_pass": counts["tested_pass"],
        "tested_fail": counts["tested_fail"],
        "results": rows,
    }


def _eval_row(row: dict) -> dict:
    """Slim latest-eval row for the Discovery table — no windows / train extras."""
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
        "fail_reasons": reasons,
        "all_windows_nonneg": row.get("all_windows_nonneg"),
    }


def _sort_tested_newest_first(rows: list[dict]) -> list[dict]:
    def key(row: dict):
        ts = parse_tested_at(row.get("tested_at") if isinstance(row.get("tested_at"), str) else None)
        return ts or datetime.min.replace(tzinfo=timezone.utc)

    return sorted(rows, key=key, reverse=True)


_DISCOVERY_SUMMARY = StaleCache()


def _summary_cache_key(lists: bool) -> str:
    from hedge_fund.paths import state_root

    return f"{state_root().resolve()}|{1 if lists else 0}"


def cached_discovery_summary(*, lists: bool = True) -> dict:
    """Last summary. A cold key builds once; later polls do not refit lift."""
    key = _summary_cache_key(lists)
    hit = _DISCOVERY_SUMMARY.peek(key)
    if not isinstance(hit, dict):
        from hedge_fund.trading.atom_lift import schedule_lift_refresh

        schedule_lift_refresh()
        hit = _DISCOVERY_SUMMARY.get(key, lambda: build_discovery_summary(lists=lists))
    return _with_live_strategy(hit)


def refresh_discovery_summaries() -> None:
    """Background rebuild of the full and compact discovery summaries."""
    for lists in (True, False):
        key = _summary_cache_key(lists)
        _DISCOVERY_SUMMARY.refresh(
            key,
            lambda lists=lists: build_discovery_summary(lists=lists),
        )


def _with_live_strategy(summary: dict) -> dict:
    from hedge_fund.trading.atom_lift import peek_refill_strategy

    out = dict(summary)
    refill = dict(out.get("refill") or {})
    refill["strategy"] = peek_refill_strategy()
    out["refill"] = refill
    return out


def build_discovery_summary(*, lists: bool = True) -> dict:
    now = datetime.now(timezone.utc)
    index = ensure_tested_index()
    pipeline = _pipeline_block(now)
    stamp_in_progress = bool(pipeline.get("stamp_says_in_progress"))
    stamp_stale = bool(pipeline.get("stale"))
    tournament_stamp = pipeline.get("phase") == "tournament" and pipeline.get("status") == "started"
    tournament_now = stamp_in_progress and pipeline.get("phase") == "tournament"

    pool = load_pool()
    champs = [c.get("name") for c in (pool.get("champions") or []) if c.get("name")]
    grads = [g.get("name") for g in load_graduated() if g.get("name")]
    blocked = set(champs) | set(grads)

    universe_static = generate_universe()
    extended = load_extended_names()
    universe = sorted(set(universe_static) | set(extended))
    log = load_discovery_log()
    latest = latest_eval_per_strategy(log)
    flags = dict(index)
    merge_tested_rows(flags, log)
    published = peek_result_counts()
    if published is not None:
        tested_pass = published["tested_pass"]
        tested_fail = published["tested_fail"]
        unique_tested = published["unique"]
    else:
        tested_pass = sum(1 for row in latest if row.get("qualified"))
        tested_fail = len(latest) - tested_pass
        unique_tested = len(latest)
    tested_names = set(flags)
    tested = (
        _sort_tested_newest_first([_eval_row(r) for r in latest]) if lists else []
    )

    leftovers = untested_candidates(blocked, universe)
    failed_names = {name for name, qual in flags.items() if not qual}
    for row in log:
        if not isinstance(row, dict):
            continue
        name = row.get("strategy")
        if name and not row.get("qualified"):
            failed_names.add(name)
    queued = [n for n in leftovers if n not in tested_names and n not in failed_names]
    rejected_parked = [n for n in leftovers if n in failed_names]
    eligible = prioritize_leftovers(leftovers, log, now=now)
    tested_index = flags
    refill_state = load_refill_status()
    from hedge_fund.trading.atom_lift import peek_refill_strategy

    newest = newest_eval(log) or (newest_eval(tested) if tested else None)
    last_tested_at = newest.get("tested_at") if newest else None
    last_strategy = newest.get("strategy") if newest else None
    last_ts = parse_tested_at(last_tested_at if isinstance(last_tested_at, str) else None)
    last_eval_age_seconds = (now - last_ts).total_seconds() if last_ts else None
    evals_today = evals_on_utc_date(log, now)

    on_cycle = discovery_on_cycle()
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
        if not on_cycle:
            stuck_reason += (
                " Walk-forwards run on the Windows discovery worker, "
                "not this cycle sidecar."
            )
    else:
        stuck_reason = None

    raw_flight = read_in_flight()
    snap = lease_snapshot(now)
    lease_names = list(snap["active_names"])
    worker_flight = bool(
        raw_flight and raw_flight.get("source") == "windows_worker"
    )
    flight = raw_flight if (
        tournament_now or (stamp_stale and tournament_stamp) or worker_flight
    ) else None
    flight_names = []
    batch_size = None
    flight_started = None
    current = None
    remaining: list[str] = []
    completed: list[str] = []
    show_flight = bool(
        tournament_now
        or (stamp_stale and tournament_stamp and flight)
        or worker_flight
    )
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

    if lease_names:
        for name in lease_names:
            if name not in flight_names:
                flight_names.append(name)
        show_flight = True
        if flight is None:
            flight = {
                "source": "claim_queue",
                "started_at": snap.get("oldest_claimed_at"),
            }
        if not flight_started:
            flight_started = snap.get("oldest_claimed_at") or (flight or {}).get("started_at")
        if batch_size is None:
            batch_size = len(flight_names)
        if not remaining:
            remaining = list(lease_names)

    if show_flight and flight_names:
        inflight_set = set(flight_names)
        queued = [n for n in queued if n not in inflight_set]

    lease_only = bool(lease_names) and not tournament_now and not (stamp_stale and tournament_stamp)
    n_lease_workers = sum(1 for row in snap["workers"] if row.get("lease_count"))
    if stuck and stuck_reason:
        flight_note = stuck_reason
    elif lease_only:
        hosts = n_lease_workers or 1
        flight_note = (
            f"{len(lease_names)} names leased to {hosts} discovery worker"
            f"{'s' if hosts != 1 else ''}. "
            "Process liveness is not verified."
        )
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
    elif worker_flight and flight_names:
        cur = f" current {current}." if current else ""
        flight_note = (
            f"Windows discovery worker in_flight since {flight_started}. "
            f"{len(flight_names)} outstanding / batch {batch_size}.{cur} "
            "Process liveness is not verified."
        )
    elif last_tested_at:
        flight_note = (
            f"idle — last eval {last_tested_at}"
            + (f" · {last_strategy}" if last_strategy else "")
        )
    else:
        flight_note = "idle — no sweep recorded yet"

    farm = farm_status_block(
        in_flight_active=bool((worker_flight and flight_names) or lease_names),
        now=now,
    )

    ship_names = flight_names if lists else []
    ship_remaining = remaining if lists else []
    ship_completed = completed if lists else []
    ship_queued = queued if lists else []
    ship_extended = list(extended) if lists else []

    return {
        "paper_only": True,
        "as_of": _iso(now),
        "compact": not lists,
        "certainty": "stamp" if tournament_now else ("last_known" if log else "no_signal"),
        "running": False,
        "stamp_says_in_progress": tournament_now,
        "stale": stamp_stale,
        "stuck": stuck,
        "stuck_reason": stuck_reason,
        "tested": tested,
        "discovery_on_cycle": on_cycle,
        "discovery_farm": "cycle_sidecar" if on_cycle else "windows_worker",
        "farm": farm,
        "workers": snap["workers"],
        "leases": {
            "active": len(lease_names),
            "expired": snap["expired"],
        },
        "extended_names": ship_extended,
        "in_flight": {
            "active": bool(
                tournament_now or (stamp_stale and tournament_stamp) or worker_flight or lease_names
            ),
            "running": False,
            "stale": bool(stamp_stale and tournament_stamp),
            "source": (
                "claim_queue" if lease_only
                else (flight or {}).get("source") if flight else None
            ),
            "names": ship_names,
            "current": current,
            "remaining": ship_remaining,
            "completed": ship_completed,
            "batch_size": batch_size,
            "started_at": flight_started,
            "stamp_started_at": pipeline.get("started_at") if (tournament_now or tournament_stamp) else None,
            "note": flight_note,
        },
        "queued": ship_queued,
        "untested": ship_queued,
        "counts": {
            "universe": len(universe),
            "tested_pass": tested_pass,
            "tested_fail": tested_fail,
            "tested": unique_tested,
            "unique_tested": unique_tested,
            "log_rows": len(log),
            "untested": len(queued),
            "leftovers": len(leftovers),
            "rejected_parked": len(rejected_parked),
            "eligible": len(eligible),
            "extended": len(extended),
            "tested_index": len(tested_index),
            "champions": len(champs),
            "graduated": len(grads),
            "in_flight": len(flight_names) if show_flight else 0,
            "leased": len(lease_names),
            "workers": len(snap["workers"]),
            "evals_today": evals_today,
        },
        "refill": {
            "source": refill_state["source"],
            "eligible": len(eligible),
            "generated_last": refill_state["generated_last"],
            "exhausted": refill_state["exhausted"],
            "strategy": peek_refill_strategy(),
        },
        "last_tested_at": last_tested_at,
        "last_strategy": last_strategy,
        "last_eval_age_seconds": last_eval_age_seconds,
        "note": (
            "Last-known buckets from discovery_log.json, champions.json, "
            "graduated.json, and (when the tournament stamp is started) "
            "discovery_in_flight.json. Active claim leases are discovery_leases.json. "
            "counts.tested is unique strategy names "
            "from the discovery_results.ix header when that file exists "
            "(a 24-byte read; the results log is not re-parsed). "
            "Until the header exists, totals follow the display log. "
            "log_rows is that display tail. "
            "GET /api/discovery/results seeks one page and does not load the file. "
            "Already tested · rejected is parked forever — not a cooldown "
            "retest queue. Skip/dedupe reads discovery_tested.json (uncapped); "
            "discovery_log.json stays a capped display. "
            "Claim refills discovery_extended.json from the structure-AND recipe "
            "and, when that runs short, densifies around qualified passes. "
            "refill.strategy is pending until a background lift snapshot exists, "
            "then lift_ucb or recipe_order. Summary and GET /api/discovery/lift "
            "do not fit that model on the request. "
            "refill.eligible matches counts.eligible. "
            "A stamp is not process liveness. "
            + (
                "Walk-forwards run on the Windows discovery worker "
                "(POST /api/discovery/ingest); this cycle sidecar does not "
                "run tournament."
                if not on_cycle
                else "This host still runs tournament inside live_cycle."
            )
        ),
    }
