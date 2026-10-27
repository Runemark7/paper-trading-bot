"""Re-label stored names whose only fail was the discovery buy-and-hold floor.

Gate v5 drops that floor. A name is re-labelled qualified only when every
other stored v4 input is present and still passes. Missing inputs stay as
they are: this module does not invent a holdout, a trial count, a DSR, a
PBO, a neighbour fraction, or a cost-stress result.

Dry-run is the default. Apply rewrites the discovery log and the tested
index flag. It does not admit or retire champions.

Idempotent: a row already stamped with ``RESCORE_REASON`` under the current
``GATE_RULES`` is left unchanged, including its timestamp.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from hedge_fund.trading.constants import (
    BH_METRIC_ROLE,
    COST_STRESS_EXTRA_SLIPPAGE,
    DISCOVERY_N_WINDOWS,
    DSR_MIN_PROBABILITY,
    GATE_RULES,
    HOLDOUT_MIN_TRADES,
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    NEIGHBOR_PASS_MIN_FRACTION,
    PBO_MAX,
    TRADE_SYMBOLS,
)

RESCORE_REASON = "gate_v5_buy_and_hold_informational"
_BH_MISSING = "buy-and-hold daily sharpe missing"
_BH_BEAT = re.compile(
    r"^daily_sharpe\s+-?[\d.]+\s+<=\s+bh_daily_sharpe\s+-?[\d.]+$"
)
_STATUSES = (
    "relabel",
    "already_rescored",
    "already_qualified",
    "missing_inputs",
    "other_failures",
)


def is_bh_decision_reason(reason: object) -> bool:
    """True for the discovery-window B&H fail strings gate v5 drops.

    Holdout and cost-stress B&H strings are not included. Those checks
    still fail a name.
    """
    text = str(reason).strip()
    return text == _BH_MISSING or _BH_BEAT.match(text) is not None


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _whole(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _metrics(row: dict) -> dict:
    block = row.get("gate_v4") if isinstance(row.get("gate_v4"), dict) else {}
    return {
        "strategy": row.get("strategy"),
        "dsr": _finite(block.get("dsr")),
        "daily_sharpe": _finite(row.get("daily_sharpe")),
        "pbo": _finite(block.get("pbo")),
        "sharpe": _finite(row.get("sharpe")),
        "bh_daily_sharpe": _finite(row.get("bh_daily_sharpe")),
        "bh_role": BH_METRIC_ROLE,
    }


def _rank_key(item: dict) -> tuple:
    dsr = item["dsr"] if item["dsr"] is not None else float("-inf")
    daily = item["daily_sharpe"] if item["daily_sharpe"] is not None else float("-inf")
    return (-dsr, -daily, str(item.get("strategy") or ""))


def _stored_v4_problem(row: dict) -> str | None:
    """``missing_inputs`` or ``other_failures`` when the row cannot be re-labelled.

    None means every non-B&H check is stored and passes.
    """
    if "regimes_tested" not in row or row.get("regimes_tested") is None:
        return "missing_inputs"
    if row.get("sharpe") is None or row.get("trades") is None or row.get("test_pnl") is None:
        return "missing_inputs"
    if "sma_stack_oos_pnl" not in row or row.get("sma_stack_oos_pnl") is None:
        return "missing_inputs"
    symbols = row.get("symbols")
    if symbols is None:
        return "missing_inputs"
    block = row.get("gate_v4")
    if not isinstance(block, dict):
        return "missing_inputs"
    holdout = block.get("holdout")
    if not isinstance(holdout, dict):
        return "missing_inputs"
    needed = (
        _finite(holdout.get("sharpe")),
        _whole(holdout.get("trades")),
        _finite(holdout.get("pnl")),
        _finite(holdout.get("sma_pnl")),
        _finite(holdout.get("daily_sharpe")),
        _finite(holdout.get("bh_daily_sharpe")),
        _finite(block.get("dsr")),
        _whole(block.get("n_trials")),
        _finite(block.get("pbo")),
        _whole(block.get("neighbours")),
        _whole(block.get("neighbours_pass")),
        _finite(block.get("cost_sharpe")),
        _finite(block.get("cost_daily_sharpe")),
        _finite(block.get("cost_bh_daily_sharpe")),
    )
    if any(item is None for item in needed):
        return "missing_inputs"

    windows = _whole(row.get("regimes_tested"))
    trades = _whole(row.get("trades"))
    sharpe = _finite(row.get("sharpe"))
    pnl = _finite(row.get("test_pnl"))
    sma = _finite(row.get("sma_stack_oos_pnl"))
    if windows != DISCOVERY_N_WINDOWS:
        return "other_failures"
    if trades is None or trades < MIN_BACKTEST_TRADES:
        return "other_failures"
    if sharpe is None or sharpe < MIN_BACKTEST_SHARPE:
        return "other_failures"
    if pnl is None or sma is None or pnl <= sma:
        return "other_failures"
    if list(symbols) != list(TRADE_SYMBOLS):
        return "other_failures"

    h_sharpe = _finite(holdout.get("sharpe"))
    h_trades = _whole(holdout.get("trades"))
    h_pnl = _finite(holdout.get("pnl"))
    h_sma = _finite(holdout.get("sma_pnl"))
    h_dsr = _finite(holdout.get("daily_sharpe"))
    h_bh = _finite(holdout.get("bh_daily_sharpe"))
    if (
        h_sharpe is None
        or h_sharpe < MIN_BACKTEST_SHARPE
        or h_trades is None
        or h_trades < HOLDOUT_MIN_TRADES
        or h_dsr is None
        or h_bh is None
        or h_dsr <= h_bh
        or h_sma is None
        or h_pnl is None
        or h_pnl <= h_sma
    ):
        return "other_failures"

    dsr = _finite(block.get("dsr"))
    n_trials = _whole(block.get("n_trials"))
    if dsr is None or n_trials is None or n_trials < 1 or dsr < DSR_MIN_PROBABILITY:
        return "other_failures"
    pbo = _finite(block.get("pbo"))
    if pbo is None or pbo > PBO_MAX:
        return "other_failures"
    neighbours = _whole(block.get("neighbours"))
    passed = _whole(block.get("neighbours_pass"))
    if neighbours is None or passed is None or neighbours <= 0:
        return "other_failures"
    if passed / neighbours + 1e-12 < NEIGHBOR_PASS_MIN_FRACTION:
        return "other_failures"
    cost_sharpe = _finite(block.get("cost_sharpe"))
    cost_daily = _finite(block.get("cost_daily_sharpe"))
    cost_bh = _finite(block.get("cost_bh_daily_sharpe"))
    if (
        cost_sharpe is None
        or cost_sharpe < MIN_BACKTEST_SHARPE
        or cost_daily is None
        or cost_bh is None
        or cost_daily <= cost_bh
    ):
        return "other_failures"
    # Referenced so a threshold edit cannot silently drop the stress size.
    if COST_STRESS_EXTRA_SLIPPAGE <= 0:
        return "other_failures"
    return None


def classify_row(row: dict) -> str:
    """One of ``relabel``, ``already_rescored``, ``already_qualified``,
    ``missing_inputs``, ``other_failures``.
    """
    if not isinstance(row, dict):
        return "other_failures"
    if row.get("qualified") and row.get("rescore_reason") == RESCORE_REASON and row.get("gate_rules") == GATE_RULES:
        return "already_rescored"
    if row.get("qualified"):
        return "already_qualified"
    if row.get("ops_park"):
        return "other_failures"
    reasons = row.get("fail_reasons")
    if not isinstance(reasons, list) or not reasons:
        return "other_failures"
    if not all(is_bh_decision_reason(reason) for reason in reasons):
        return "other_failures"
    problem = _stored_v4_problem(row)
    if problem:
        return problem
    return "relabel"


def _stamp_row(row: dict, stamp: str) -> None:
    old_rules = row.get("gate_rules")
    row["qualified"] = True
    row["fail_reasons"] = []
    row["gate_rules"] = GATE_RULES
    row["rescored_at"] = stamp
    row["rescore_reason"] = RESCORE_REASON
    row["rescore_from_rules"] = old_rules if isinstance(old_rules, str) else None
    row["bh_daily_sharpe_role"] = BH_METRIC_ROLE
    row["bh_oos_pnl_role"] = BH_METRIC_ROLE
    block = row.get("gate_v4")
    if isinstance(block, dict):
        updated = dict(block)
        updated["passed"] = True
        updated["rules"] = GATE_RULES
        for key in ("holdout_pass", "dsr_pass", "pbo_pass", "perturb_pass", "cost_pass"):
            updated[key] = True
        row["gate_v4"] = updated
    from hedge_fund.trading.qualify import qualification_from_record

    decision = qualification_from_record(row, expected_windows=DISCOVERY_N_WINDOWS)
    row["score"] = round(float(decision["score"]), 2)


def _latest(log: list) -> tuple[dict[str, dict], list[dict]]:
    """Newest log row per name, then results-only rows the log does not have."""
    by_name: dict[str, dict] = {}
    order: list[str] = []
    for row in log:
        if not isinstance(row, dict):
            continue
        name = row.get("strategy")
        if not isinstance(name, str) or not name or name in by_name:
            continue
        by_name[name] = row
        order.append(name)
    extras: list[dict] = []
    try:
        from hedge_fund.trading.discovery_results import records_by_name

        for name, row in records_by_name().items():
            if name in by_name or not isinstance(row, dict):
                continue
            extras.append(row)
            by_name[name] = row
            order.append(name)
    except Exception:
        pass
    return by_name, extras


def _report(
    groups: dict[str, list[str]],
    metrics: list[dict],
    *,
    dry_run: bool,
    wrote: bool,
    stamp: str,
    active: int,
) -> dict:
    metrics.sort(key=_rank_key)
    for i, item in enumerate(metrics, start=1):
        item["rank"] = i
    return {
        "ok": True,
        "paper_only": True,
        "dry_run": dry_run,
        "applied": wrote,
        "gate_rules": GATE_RULES,
        "rescore_reason": RESCORE_REASON,
        "rescored_at": stamp if wrote else None,
        "counts": {status: len(groups[status]) for status in _STATUSES},
        "relabel_count": len(groups["relabel"]),
        "relabel_names": [item["strategy"] for item in metrics],
        "top": metrics[:20],
        "admitted": [],
        "retired": [],
        "champions_active": active,
        "note": (
            "Buy-and-hold is informational under gate v5. Names are re-labelled "
            "only when every other stored v4 input passes. Missing inputs are "
            "left unchanged. Champions are not admitted or retired."
        ),
    }


def _active_count() -> int:
    from hedge_fund.trading.champions import load_pool

    return sum(
        1
        for row in (load_pool().get("champions") or [])
        if isinstance(row, dict) and row.get("name")
    )


def rescore_v5(*, apply: bool = False, now: str | None = None) -> dict:
    """Classify stored rows. Write only when ``apply`` is true.

    A second apply does not move ``rescored_at`` or append another verdict.
    """
    stamp = now or datetime.now(timezone.utc).isoformat()
    if not apply:
        from hedge_fund.trading.discovery import load_discovery_log

        by_name, _extras = _latest(load_discovery_log())
        return _build(by_name, dry_run=True, wrote=False, stamp=stamp)

    from hedge_fund.trading.discovery import load_discovery_log, save_discovery_log
    from hedge_fund.trading.store import paper_state_lock

    with paper_state_lock("discovery"):
        log = load_discovery_log()
        by_name, extras = _latest(log)
        groups, metrics = _classify(by_name)
        changed = False
        log_names = {
            row.get("strategy")
            for row in log
            if isinstance(row, dict) and isinstance(row.get("strategy"), str)
        }
        prepend: list[dict] = []
        for name in groups["relabel"]:
            row = by_name[name]
            if row.get("rescore_reason") == RESCORE_REASON and row.get("qualified"):
                continue
            _stamp_row(row, stamp)
            changed = True
            if name not in log_names:
                prepend.append(row)
        if changed:
            save_discovery_log(prepend + log)
        active = _active_count()
    return _report(
        groups, metrics, dry_run=False, wrote=changed, stamp=stamp, active=active,
    )


def _classify(by_name: dict[str, dict]) -> tuple[dict[str, list[str]], list[dict]]:
    groups = {status: [] for status in _STATUSES}
    metrics: list[dict] = []
    for name in sorted(by_name):
        row = by_name[name]
        status = classify_row(row)
        groups[status].append(name)
        if status == "relabel":
            metrics.append(_metrics(row))
    return groups, metrics


def _build(by_name: dict[str, dict], *, dry_run: bool, wrote: bool, stamp: str) -> dict:
    groups, metrics = _classify(by_name)
    return _report(
        groups, metrics, dry_run=dry_run, wrote=wrote, stamp=stamp, active=_active_count(),
    )
