"""One champion per near-identical family. Park the twins, never delete.

Alexander, 2026-10-09 08:04: keep only the best champion per family of
near-identical strategies and park the rest.

Family (structural, decided at admit time on prod):

* same template: the name's atoms, sorted, with every number replaced by
  ``N`` (``dip_Nb_ltNpc&h4_ema_abv_N``), and
* every threshold equal (numbers that end in ``pc`` or follow ``>`` /
  ``<``: a 8% dip and a 10% dip trade differently), and
* every lookback / period within ``FAMILY_PARAM_TOL`` (20%) of the other
  name's value, relative to the larger one (204 vs 222, 130 vs 160).

Membership is always measured against a family *winner*, never chained,
so a run of 120→150→180→… never collapses into one family.

OOS daily-return correlation would be the better test, but no daily
series is stored on prod: workers post only the scalar daily Sharpe,
and adding the series is a worker change (laptop pull). Recomputing it
on prod means a 23-window walk-forward per name inside the web server.
The structural rule is checked against that correlation offline in the
PR that introduced it.

Best = highest OOS daily-equity Sharpe under the current ``GATE_RULES``,
ties by gate Sharpe, then OOS P&L, then the earlier admit. A champion
without a current-tag verdict is never parked or used to park.

Parked twins move to ``retired.json`` with ``role="parked_twin"``,
``family_winner`` and the reason. They are blocked from re-admit (like
any retired name) while the winner is active. If the winner is retired
(not parked), the twin becomes reconsiderable: it is no longer blocked
for an explicit admit. A twin that still holds an open paper lot is not
parked until it is flat (it would be stranded off the live book).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Callable

from hedge_fund.trading.champions import load_pool, load_retired, save_pool
from hedge_fund.trading.constants import GATE_RULES
from hedge_fund.trading.store import paper_state_lock

log = logging.getLogger(__name__)

FAMILY_PARAM_TOL = 0.20
PARK_ROLE = "parked_twin"
PARK_BATCH_PREFIX = "family-park"
PARK_REASON = "near-identical family twin of {winner}; best per family kept (Alexander 2026-10-09)"

_NUM = re.compile(r"\d+")
_TF = re.compile(r"^h\d+_")
_METRIC_KEYS = ("gate_rules", "daily_sharpe", "bh_daily_sharpe", "sharpe", "test_pnl", "trades")


def family_signature(name: str) -> tuple[str, tuple[tuple[int, bool], ...]] | None:
    """(template, ((value, exact), ...)) or None for an empty name.

    ``exact`` marks thresholds (``…pc`` or after ``>``/``<``).
    """
    if not isinstance(name, str) or not name.strip():
        return None
    atoms = sorted(a.strip() for a in name.split("&") if a.strip())
    params: list[tuple[int, bool]] = []
    shapes: list[str] = []
    for atom in atoms:
        # The bar timeframe (``h1_`` / ``h4_``) is part of the template.
        tf = _TF.match(atom)
        head = tf.group(0) if tf else ""
        body = atom[len(head):]
        for m in _NUM.finditer(body):
            before = body[m.start() - 1] if m.start() > 0 else ""
            after = body[m.end() : m.end() + 2]
            exact = after == "pc" or before in "<>"
            params.append((int(m.group()), exact))
        shapes.append(head + _NUM.sub("N", body))
    return "&".join(shapes), tuple(params)


def same_family(a: str, b: str, tol: float = FAMILY_PARAM_TOL) -> bool:
    if a == b:
        return True
    sa, sb = family_signature(a), family_signature(b)
    if sa is None or sb is None or sa[0] != sb[0] or len(sa[1]) != len(sb[1]):
        return False
    for (va, exact), (vb, _) in zip(sa[1], sb[1]):
        if exact:
            if va != vb:
                return False
            continue
        hi = max(va, vb)
        if hi and abs(va - vb) > tol * hi:
            return False
    return True


def _current(row: object) -> dict | None:
    if not isinstance(row, dict) or row.get("gate_rules") != GATE_RULES:
        return None
    if row.get("daily_sharpe") is None:
        return None
    return {k: row.get(k) for k in _METRIC_KEYS if k in row}


def qual_metrics(record: dict) -> dict | None:
    """Current-tag ranking fields of an eval record (stored on the champion row)."""
    return _current(record)


def _default_lookup() -> Callable[[str], dict | None]:
    """Results log first; the discovery log only on a miss. Loaded lazily."""
    results: dict[str, dict] | None = None
    log_rows: dict[str, dict] | None = None

    def lookup(name: str) -> dict | None:
        nonlocal results, log_rows
        if results is None:
            try:
                from hedge_fund.trading.discovery_results import records_by_name

                results = records_by_name()
            except Exception:
                results = {}
        got = _current(results.get(name))
        if got:
            return got
        if log_rows is None:
            log_rows = {}
            try:
                from hedge_fund.trading.discovery import load_discovery_log

                for row in load_discovery_log():
                    n = row.get("strategy") if isinstance(row, dict) else None
                    if isinstance(n, str) and n not in log_rows:
                        log_rows[n] = row
            except Exception:
                pass
        return _current(log_rows.get(name))

    return lookup


def champion_metrics(row: dict, lookup: Callable[[str], dict | None]) -> dict | None:
    for source in (row.get("qual"), row.get("provenance")):
        got = _current(source)
        if got:
            return got
    name = row.get("name")
    return lookup(name) if isinstance(name, str) else None


def _num(value: object) -> float:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("-inf")
    return out if out == out else float("-inf")


def rank_key(metrics: dict, row: dict) -> tuple:
    """Higher is better. Ties: gate Sharpe, P&L, then the earlier admit."""
    admitted = str(row.get("admitted_at") or row.get("champion_since") or "")
    return (
        _num(metrics.get("daily_sharpe")),
        _num(metrics.get("sharpe")),
        _num(metrics.get("test_pnl")),
        tuple(-ord(ch) for ch in admitted),
        tuple(-ord(ch) for ch in str(row.get("name") or "")),
    )


def _open_lots(name: str) -> int:
    from hedge_fund.paths import state_root
    from hedge_fund.trading.open_lots import account_slug, open_lot_count_from_saved
    from hedge_fund.trading.store import TradeStore

    db = state_root() / f"trades_{account_slug(name)}.sqlite"
    if not db.exists():
        return 0
    try:
        with TradeStore.open_readonly(db) as st:
            return open_lot_count_from_saved(st.load_account_state())
    except Exception:
        return 1  # unknown: treat as holding, do not strand it


def plan_families(
    champions: list[dict],
    lookup: Callable[[str], dict | None],
) -> tuple[list[str], dict[str, str], list[str], dict[str, dict]]:
    """(winners, {twin: winner}, unranked, metrics) for the active pool."""
    metrics: dict[str, dict] = {}
    unranked: list[str] = []
    ranked: list[dict] = []
    for row in champions:
        name = row.get("name") if isinstance(row, dict) else None
        if not isinstance(name, str):
            continue
        m = champion_metrics(row, lookup)
        if m is None:
            unranked.append(name)
            continue
        metrics[name] = m
        ranked.append(row)
    ranked.sort(key=lambda r: rank_key(metrics[r["name"]], r), reverse=True)
    winners: list[str] = []
    twins: dict[str, str] = {}
    for row in ranked:
        name = row["name"]
        winner = next((w for w in winners if same_family(w, name)), None)
        if winner is None:
            winners.append(name)
        else:
            twins[name] = winner
    return winners, twins, unranked, metrics


def save_retired_state(state: dict) -> None:
    from hedge_fund.trading.retire import save_retired

    save_retired(state)


def enforce_champion_families_unlocked(
    now: datetime,
    *,
    lookup: Callable[[str], dict | None] | None = None,
    open_lots: Callable[[str], int] | None = None,
) -> dict | None:
    """Park every non-best member of a family. Caller holds the discovery lock.

    Idempotent: returns None when the pool has no twins to park.
    """
    pool = load_pool()
    champions = [c for c in (pool.get("champions") or []) if isinstance(c, dict)]
    names = [c.get("name") for c in champions if isinstance(c.get("name"), str)]
    # Cheap exit: no two active names share a template.
    templates = [family_signature(n)[0] for n in names if family_signature(n)]
    if len(templates) == len(set(templates)):
        return None
    winners, twins, unranked, metrics = plan_families(champions, lookup or _default_lookup())
    if not twins:
        return None
    lots = open_lots or _open_lots
    deferred = {t: w for t, w in twins.items() if lots(t) > 0}
    park = {t: w for t, w in twins.items() if t not in deferred}
    if not park:
        return {"parked": {}, "deferred": deferred, "winners": winners, "unranked": unranked}

    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    batch_id = f"{PARK_BATCH_PREFIX}-{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    state = load_retired()
    by_name = {c["name"]: c for c in champions if isinstance(c.get("name"), str)}
    for twin, winner in park.items():
        state["retired"][twin] = {
            "name": twin,
            "role": PARK_ROLE,
            "retired_at": stamp,
            "batch_id": batch_id,
            "reason": PARK_REASON.format(winner=winner),
            "family_winner": winner,
            "family_template": family_signature(twin)[0],
            "metrics": metrics.get(twin) or {},
            "winner_metrics": metrics.get(winner) or {},
            "requalify": {},
            "record": by_name.get(twin) or {},
        }
    # Twins of a winner that just got parked now point at the new winner.
    for row in state["retired"].values():
        if isinstance(row, dict) and row.get("role") == PARK_ROLE:
            old = row.get("family_winner")
            if old in park:
                row["family_winner"] = park[old]
                row["reason"] = PARK_REASON.format(winner=park[old])
    state["batches"].append({
        "batch_id": batch_id,
        "retired_at": stamp,
        "reason": "family park: best per near-identical family kept",
        "champions": len(park),
        "graduated": 0,
        "parked": sorted(park),
        "winners": sorted({w for w in park.values()}),
    })
    # Archive first so a crash between writes never loses a row.
    save_retired_state(state)
    pool["champions"] = [c for c in pool.get("champions") or [] if not (isinstance(c, dict) and c.get("name") in park)]
    save_pool(pool)
    return {
        "batch_id": batch_id,
        "parked": park,
        "deferred": deferred,
        "winners": winners,
        "unranked": unranked,
    }


def enforce_champion_families(now: datetime | None = None) -> dict | None:
    with paper_state_lock("discovery"):
        out = enforce_champion_families_unlocked(now or datetime.now(timezone.utc))
    if out and out.get("parked"):
        log.info("families: parked %s", out["parked"])
    return out


def reconsiderable_twins(state: dict | None = None) -> set[str]:
    """Parked twins whose winner was retired (not parked): no longer blocked."""
    state = state or load_retired()
    retired = state["retired"]
    out: set[str] = set()
    for name, row in retired.items():
        if not isinstance(row, dict) or row.get("role") != PARK_ROLE:
            continue
        winner = retired.get(row.get("family_winner"))
        if isinstance(winner, dict) and winner.get("role") != PARK_ROLE:
            out.add(name)
    return out


def admit_blocked_names() -> set[str]:
    """Retired names that block a re-admit (reconsiderable twins excluded)."""
    state = load_retired()
    names = {n for n in state["retired"] if isinstance(n, str) and n}
    return names - reconsiderable_twins(state)


def unpark_unlocked(names: list[str]) -> list[str]:
    """Drop re-admitted reconsiderable twins from the archive. Lock held."""
    state = load_retired()
    gone = [n for n in names if isinstance(state["retired"].get(n), dict)
            and state["retired"][n].get("role") == PARK_ROLE]
    if gone:
        for n in gone:
            state["retired"].pop(n, None)
        save_retired_state(state)
    return gone
