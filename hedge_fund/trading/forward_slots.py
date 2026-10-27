"""Forward-champion cap and the read-only candidate list. Paper only.

New admissions stop at ``FORWARD_CHAMPION_CAP`` active champions. Names
already on the book stay; nothing here retires a champion to free a slot.
The candidate list is a proposal. It does not admit.
"""
from __future__ import annotations

from hedge_fund.trading.constants import BH_METRIC_ROLE, FORWARD_CHAMPION_CAP

# Shown on GET /api/champions and in the Champions UI. A proposal is not a seat.
AWAITING_APPROVAL = "proposed, awaiting approval"


def forward_cap_block(active_count: int) -> str | None:
    """Refusal text when another admission would pass the cap. None if it fits."""
    active = int(active_count)
    if active >= FORWARD_CHAMPION_CAP:
        return (
            f"forward champion cap is {FORWARD_CHAMPION_CAP}; "
            f"{active} active; admission refused"
        )
    return None


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _gate_block(row: dict) -> dict:
    block = row.get("gate_v4")
    return block if isinstance(block, dict) else {}


def forward_candidates(*, limit: int = FORWARD_CHAMPION_CAP) -> dict:
    """Rank qualified names that are not already forward champions.

    Order: deflated Sharpe (high), daily Sharpe (high), PBO (low). Missing
    numbers sort after numbers that are stored. The top ``limit`` are
    proposed. This function does not write the pool, the log, or retired.
    """
    from hedge_fund.trading.champions import load_graduated, load_pool, retired_names
    from hedge_fund.trading.discovery import latest_eval_per_strategy, load_discovery_log

    active = [
        c.get("name")
        for c in (load_pool().get("champions") or [])
        if isinstance(c, dict) and isinstance(c.get("name"), str)
    ]
    blocked = set(active)
    blocked |= {g.get("name") for g in load_graduated() if isinstance(g, dict) and g.get("name")}
    blocked |= retired_names()

    ranked: list[dict] = []
    for row in latest_eval_per_strategy(load_discovery_log()):
        if not isinstance(row, dict) or not row.get("qualified"):
            continue
        if row.get("ops_park"):
            continue
        name = row.get("strategy")
        if not isinstance(name, str) or not name or name in blocked:
            continue
        block = _gate_block(row)
        dsr = _finite(block.get("dsr"))
        daily = _finite(row.get("daily_sharpe"))
        pbo = _finite(block.get("pbo"))
        ranked.append({
            "strategy": name,
            "dsr": dsr,
            "daily_sharpe": daily,
            "pbo": pbo,
            "sharpe": _finite(row.get("sharpe")),
            "trades": row.get("trades"),
            "bh_daily_sharpe": _finite(row.get("bh_daily_sharpe")),
            "bh_oos_pnl": _finite(row.get("bh_oos_pnl")),
            "bh_role": BH_METRIC_ROLE,
            "gate_rules": row.get("gate_rules"),
            "proposed": True,
            "admitted": False,
            "status": AWAITING_APPROVAL,
        })

    def _key(item: dict) -> tuple:
        dsr = item["dsr"] if item["dsr"] is not None else float("-inf")
        daily = item["daily_sharpe"] if item["daily_sharpe"] is not None else float("-inf")
        pbo = item["pbo"] if item["pbo"] is not None else float("inf")
        return (-dsr, -daily, pbo, item["strategy"])

    ranked.sort(key=_key)
    shown = ranked[: max(0, int(limit))]
    for i, item in enumerate(shown, start=1):
        item["rank"] = i
    active_n = len(active)
    open_slots = max(0, FORWARD_CHAMPION_CAP - active_n)
    return {
        "paper_only": True,
        "cap": FORWARD_CHAMPION_CAP,
        "active": active_n,
        "open_slots": open_slots,
        "eligible": len(ranked),
        "proposed": shown,
        "admitted": [],
        "retired": [],
        "note": (
            "Proposed, awaiting approval. Ranked by deflated Sharpe, then "
            "daily Sharpe, then lower PBO. A newly qualified pass is not "
            "seated. Admission is an explicit owner decision (force_admit or "
            "promote_candidates) and refuses when "
            f"{FORWARD_CHAMPION_CAP} champions are already active. "
            "Nothing in this list is admitted or retired. "
            "Buy-and-hold figures are informational."
        ),
    }
