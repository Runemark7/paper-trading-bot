"""Marginal OOS-Sharpe lift of mint atoms, and how refill uses it.

The tested index is uncapped and includes failures. Each name is split with
the shared atom parser. When a parent stack and the same stack plus one
atom were both tested, that pair is the atom's marginal effect. A ridge
fit (coefficients shrunk toward 0, intercept unpenalized) fills in atoms
that do not have enough pairs. Nothing here reads or writes the OOS gate.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from hedge_fund.trading.discovery_guard import is_ops_park_record
from hedge_fund.trading.mint_quality import parse_atom

DISCOVERY_LIFT = "discovery_lift.json"

MIN_SUPPORT = 5
PRIOR_STRENGTH = 5.0
RIDGE_LAMBDA = 25.0
MAX_RIDGE_ATOMS = 400
EXPLORE_SHARE = 0.25
STEER_WINDOW = 8
UCB_C = 0.02
ZERO_WEIGHT = 0.35
LIFT_SEED = 7063
# Claim and ingest never fit. The background thread refits at most this often,
# even when every eval changes the tested-metrics fingerprint.
LIFT_REFRESH_SECONDS = 300.0
INFORMATIVE_EPS = 1e-4
_CACHE_V = 1

STRATEGY_LIFT = "lift_ucb"
STRATEGY_PLAIN = "recipe_order"
STRATEGY_PENDING = "pending"


@dataclass
class AtomEstimate:
    key: str
    level: str
    sharpe_lift: float
    trades_lift: float
    support: int
    zero_trade_rate: float
    source: str
    name_count: int = 0


@dataclass
class LiftModel:
    atoms: dict[str, AtomEstimate] = field(default_factory=dict)
    families: dict[str, AtomEstimate] = field(default_factory=dict)
    names_used: int = 0
    informative: bool = False
    support_min: int = MIN_SUPPORT
    explore_share: float = EXPLORE_SHARE
    strategy: str = STRATEGY_PLAIN
    fingerprint: str = ""


def family_key(atom: str) -> str | None:
    """``family|params`` with the threshold left out, or None if unparsed."""
    parsed = parse_atom(atom)
    if parsed is None:
        return None
    family, params, _op, _thr = parsed
    return f"{family}|{params}"


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _split(name: str) -> frozenset[str]:
    """Atom set for parent lookup. Order does not matter."""
    return frozenset(part.strip() for part in str(name).split("&") if part.strip())


def _shrunk(values: list[float], prior: float = PRIOR_STRENGTH) -> float:
    count = len(values)
    if count <= 0:
        return 0.0
    mean = sum(values) / count
    return (count / (count + prior)) * mean


def _ridge_coefs(
    records: list[tuple[frozenset[str], float, int | None]],
    name_count: dict[str, int],
) -> dict[str, float]:
    """Binary atom indicators. Penalty on every coefficient except the intercept.

    The normal equations are filled from per-row co-occurrence. That matches
    a dense design matrix without allocating one row per tested name.
    """
    if len(records) < 2 or not name_count:
        return {}
    ranked = sorted(name_count.items(), key=lambda item: (-item[1], item[0]))
    columns = [atom for atom, count in ranked if count >= 1][:MAX_RIDGE_ATOMS]
    if not columns:
        return {}
    index = {atom: pos + 1 for pos, atom in enumerate(columns)}
    width = len(columns) + 1
    gram = np.zeros((width, width), dtype=np.float64)
    rhs = np.zeros(width, dtype=np.float64)
    gram[0, 0] = float(len(records))
    for atoms, sharpe, _trades in records:
        rhs[0] += sharpe
        cols: list[int] = []
        for atom in atoms:
            column = index.get(atom)
            if column is not None:
                cols.append(column)
        for column in cols:
            gram[0, column] += 1.0
            gram[column, 0] += 1.0
            gram[column, column] += 1.0
            rhs[column] += sharpe
        for left in range(len(cols)):
            for right in range(left + 1, len(cols)):
                a = cols[left]
                b = cols[right]
                gram[a, b] += 1.0
                gram[b, a] += 1.0
    penalty = np.eye(width, dtype=np.float64) * RIDGE_LAMBDA
    penalty[0, 0] = 0.0
    try:
        beta = np.linalg.solve(gram + penalty, rhs)
    except np.linalg.LinAlgError:
        beta = np.linalg.lstsq(gram + penalty, rhs, rcond=None)[0]
    return {atom: float(beta[column]) for atom, column in index.items()}


def estimate_lifts(rows: list) -> LiftModel:
    """Fit atom and family+param lifts. Ops-park rows are ignored."""
    by_atoms: dict[frozenset[str], tuple[float, int | None]] = {}
    for row in rows:
        if not isinstance(row, dict) or is_ops_park_record(row):
            continue
        name = row.get("strategy")
        if not isinstance(name, str) or not name:
            continue
        sharpe = _finite(row.get("sharpe"))
        if sharpe is None:
            continue
        atoms = _split(name)
        if not atoms or atoms in by_atoms:
            continue
        trades = _finite(row.get("trades"))
        by_atoms[atoms] = (sharpe, None if trades is None else int(trades))

    records = [(atoms, sharpe, trades) for atoms, (sharpe, trades) in by_atoms.items()]
    pair_sharpe: dict[str, list[float]] = {}
    pair_trades: dict[str, list[float]] = {}
    family_sharpe: dict[str, list[float]] = {}
    family_trades: dict[str, list[float]] = {}
    name_count: dict[str, int] = {}
    zero_num: dict[str, int] = {}
    zero_den: dict[str, int] = {}
    fam_zero_num: dict[str, int] = {}
    fam_zero_den: dict[str, int] = {}
    members: dict[str, set[str]] = {}

    for atoms, sharpe, trades in records:
        families_here: set[str] = set()
        for atom in atoms:
            name_count[atom] = name_count.get(atom, 0) + 1
            if trades is not None:
                zero_den[atom] = zero_den.get(atom, 0) + 1
                if trades == 0:
                    zero_num[atom] = zero_num.get(atom, 0) + 1
            fkey = family_key(atom)
            if fkey is not None:
                families_here.add(fkey)
                members.setdefault(fkey, set()).add(atom)
            parent = atoms - {atom}
            parent_row = by_atoms.get(parent)
            if parent_row is None:
                continue
            pair_sharpe.setdefault(atom, []).append(sharpe - parent_row[0])
            if trades is not None and parent_row[1] is not None:
                pair_trades.setdefault(atom, []).append(float(trades - parent_row[1]))
            if fkey is not None:
                family_sharpe.setdefault(fkey, []).append(sharpe - parent_row[0])
                if trades is not None and parent_row[1] is not None:
                    family_trades.setdefault(fkey, []).append(float(trades - parent_row[1]))
        for fkey in families_here:
            if trades is None:
                continue
            fam_zero_den[fkey] = fam_zero_den.get(fkey, 0) + 1
            if trades == 0:
                fam_zero_num[fkey] = fam_zero_num.get(fkey, 0) + 1

    ridge = _ridge_coefs(records, name_count)

    def _zero(num: dict[str, int], den: dict[str, int], key: str) -> float:
        total = den.get(key, 0)
        if total <= 0:
            return 0.0
        return num.get(key, 0) / total

    atoms_out: dict[str, AtomEstimate] = {}
    for atom, count in sorted(name_count.items()):
        support = len(pair_sharpe.get(atom, ()))
        if support >= MIN_SUPPORT:
            sharpe_lift = _shrunk(pair_sharpe[atom])
            source = "paired"
        elif atom in ridge:
            sharpe_lift = ridge[atom]
            source = "ridge"
        else:
            sharpe_lift = 0.0
            source = "none"
        atoms_out[atom] = AtomEstimate(
            key=atom,
            level="atom",
            sharpe_lift=sharpe_lift,
            trades_lift=_shrunk(pair_trades.get(atom, ())),
            support=support,
            zero_trade_rate=_zero(zero_num, zero_den, atom),
            source=source,
            name_count=count,
        )

    families_out: dict[str, AtomEstimate] = {}
    for fkey, group in sorted(members.items()):
        support = len(family_sharpe.get(fkey, ()))
        if support >= MIN_SUPPORT:
            sharpe_lift = _shrunk(family_sharpe[fkey])
            source = "paired"
        else:
            coefs = [ridge[atom] for atom in group if atom in ridge]
            sharpe_lift = sum(coefs) / len(coefs) if coefs else 0.0
            source = "ridge" if coefs else "none"
        families_out[fkey] = AtomEstimate(
            key=fkey,
            level="family",
            sharpe_lift=sharpe_lift,
            trades_lift=_shrunk(family_trades.get(fkey, ())),
            support=support,
            zero_trade_rate=_zero(fam_zero_num, fam_zero_den, fkey),
            source=source,
            name_count=sum(name_count.get(atom, 0) for atom in group),
        )

    informative = any(
        est.support >= MIN_SUPPORT and abs(est.sharpe_lift) > INFORMATIVE_EPS
        for est in atoms_out.values()
    )
    return LiftModel(
        atoms=atoms_out,
        families=families_out,
        names_used=len(records),
        informative=informative,
        support_min=MIN_SUPPORT,
        explore_share=EXPLORE_SHARE,
        strategy=STRATEGY_LIFT if informative else STRATEGY_PLAIN,
    )


def _effect(model: LiftModel, atom: str) -> tuple[float, float, int]:
    """Sharpe lift, zero-trade rate, and support used for the UCB term.

    Exact atom when it has enough pairs, else the family+param pool, else
    the ridge coefficient.
    """
    est = model.atoms.get(atom)
    fam = None
    fkey = family_key(atom)
    if fkey is not None:
        fam = model.families.get(fkey)
    if est is not None and est.support >= model.support_min:
        return est.sharpe_lift, est.zero_trade_rate, est.support
    if fam is not None and fam.support >= model.support_min:
        return fam.sharpe_lift, fam.zero_trade_rate, fam.support
    if est is not None and est.source == "ridge":
        return est.sharpe_lift, est.zero_trade_rate, est.support
    zero = est.zero_trade_rate if est is not None else 0.0
    return 0.0, zero, 0


def score_name(model: LiftModel, name: str) -> float:
    """Sum of atom lifts. Shared atoms cancel when two stacks are compared."""
    atoms = [part.strip() for part in str(name).split("&") if part.strip()]
    if not atoms:
        return 0.0
    log_term = math.log(model.names_used + 1.0)
    total = 0.0
    for atom in atoms:
        lift, zero_rate, support = _effect(model, atom)
        bonus = UCB_C * math.sqrt(log_term / (support + 1.0))
        total += lift - ZERO_WEIGHT * zero_rate + bonus
    return total


def explore_count(n: int, share: float = EXPLORE_SHARE) -> int:
    take = max(0, int(n))
    if take <= 0:
        return 0
    return max(0, min(take, int(round(take * share))))


def steer_candidates(
    candidates: list[str],
    model: LiftModel | None,
    n: int,
    *,
    seed: int = LIFT_SEED,
) -> list[str]:
    """Rank by expected lift, then replace ``explore_count`` slots at random.

    A flat or missing model keeps the incoming order. The explore draw is
    ``random.Random(seed)`` so tests can replay it.
    """
    want = max(0, int(n))
    if want <= 0 or not candidates:
        return []
    if model is None or not model.informative:
        return list(candidates[:want])
    take = min(want, len(candidates))
    n_explore = explore_count(take, model.explore_share)
    n_exploit = take - n_explore
    scored = [(score_name(model, name), pos, name) for pos, name in enumerate(candidates)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    exploit = [name for _score, _pos, name in scored[:n_exploit]]
    rest = [name for _score, _pos, name in scored[n_exploit:]]
    random.Random(int(seed)).shuffle(rest)
    return (exploit + rest)[:take]


def _public_estimate(est: AtomEstimate) -> dict:
    return {
        "atom": est.key,
        "level": est.level,
        "sharpe_lift": round(est.sharpe_lift, 6),
        "trades_lift": round(est.trades_lift, 4),
        "support": int(est.support),
        "zero_trade_rate": round(est.zero_trade_rate, 4),
        "source": est.source,
    }


def lift_api_payload(model: LiftModel, *, k: int = 10) -> dict:
    """Top and bottom atoms by lift. The two lists do not overlap."""
    ranked = sorted(
        model.atoms.values(),
        key=lambda est: (-est.sharpe_lift, -est.support, est.key),
    )
    top = ranked[: max(0, int(k))]
    top_keys = {est.key for est in top}
    bottom = [est for est in reversed(ranked) if est.key not in top_keys][: max(0, int(k))]
    return {
        "paper_only": True,
        "strategy": model.strategy,
        "explore_share": model.explore_share,
        "names_used": model.names_used,
        "support_min": model.support_min,
        "top": [_public_estimate(est) for est in top],
        "bottom": [_public_estimate(est) for est in bottom],
    }


def fingerprint_rows(rows: list[dict]) -> str:
    count = 0
    sharpe_sum = 0.0
    trades_sum = 0
    for row in rows:
        sharpe = _finite(row.get("sharpe"))
        if sharpe is None:
            continue
        count += 1
        sharpe_sum += sharpe
        trades = _finite(row.get("trades"))
        if trades is not None:
            trades_sum += int(trades)
    return f"{count}:{sharpe_sum:.5f}:{trades_sum}"


def _estimate_from_mapping(raw: object, level: str) -> AtomEstimate | None:
    if not isinstance(raw, dict):
        return None
    key = raw.get("atom")
    if not isinstance(key, str) or not key:
        return None
    sharpe = _finite(raw.get("sharpe_lift"))
    trades = _finite(raw.get("trades_lift"))
    try:
        support = int(raw.get("support") or 0)
    except (TypeError, ValueError):
        support = 0
    zero = _finite(raw.get("zero_trade_rate"))
    source = raw.get("source") if raw.get("source") in ("paired", "ridge", "none") else "none"
    try:
        name_count = int(raw.get("name_count") or 0)
    except (TypeError, ValueError):
        name_count = 0
    return AtomEstimate(
        key=key,
        level=level,
        sharpe_lift=0.0 if sharpe is None else sharpe,
        trades_lift=0.0 if trades is None else trades,
        support=support,
        zero_trade_rate=0.0 if zero is None else zero,
        source=source,
        name_count=name_count,
    )


def lift_cache_path():
    from hedge_fund.paths import state_root

    return state_root() / DISCOVERY_LIFT


def _model_to_payload(model: LiftModel) -> dict:
    def dump(est: AtomEstimate) -> dict:
        item = _public_estimate(est)
        item["sharpe_lift"] = est.sharpe_lift
        item["trades_lift"] = est.trades_lift
        item["zero_trade_rate"] = est.zero_trade_rate
        item["name_count"] = est.name_count
        return item

    return {
        "v": _CACHE_V,
        "paper_only": True,
        "fingerprint": model.fingerprint,
        "strategy": model.strategy,
        "explore_share": model.explore_share,
        "names_used": model.names_used,
        "support_min": model.support_min,
        "informative": model.informative,
        "atoms": [dump(model.atoms[key]) for key in sorted(model.atoms)],
        "families": [dump(model.families[key]) for key in sorted(model.families)],
    }


def _model_from_payload(data: dict) -> LiftModel | None:
    if not isinstance(data, dict) or data.get("v") != _CACHE_V:
        return None
    atoms: dict[str, AtomEstimate] = {}
    families: dict[str, AtomEstimate] = {}
    for raw in data.get("atoms") or []:
        est = _estimate_from_mapping(raw, "atom")
        if est is not None:
            atoms[est.key] = est
    for raw in data.get("families") or []:
        est = _estimate_from_mapping(raw, "family")
        if est is not None:
            families[est.key] = est
    strategy = data.get("strategy")
    if strategy not in (STRATEGY_LIFT, STRATEGY_PLAIN):
        strategy = STRATEGY_PLAIN
    try:
        explore = float(data.get("explore_share"))
    except (TypeError, ValueError):
        explore = EXPLORE_SHARE
    try:
        support_min = int(data.get("support_min") or MIN_SUPPORT)
    except (TypeError, ValueError):
        support_min = MIN_SUPPORT
    return LiftModel(
        atoms=atoms,
        families=families,
        names_used=int(data.get("names_used") or 0),
        informative=bool(data.get("informative")),
        support_min=support_min,
        explore_share=explore,
        strategy=strategy,
        fingerprint=data.get("fingerprint") if isinstance(data.get("fingerprint"), str) else "",
    )


def load_lift_cache() -> LiftModel | None:
    path = lift_cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return _model_from_payload(data)


def save_lift_cache(model: LiftModel) -> None:
    """Caller holds ``paper_state_lock('discovery')``."""
    path = lift_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_model_to_payload(model), separators=(",", ":")))
    os.replace(tmp, path)


def training_rows() -> list[dict]:
    """Newest metrics per name, ops parks left out.

    ``ensure_tested_index`` merges the display log first (log parse is
    outside the discovery lock). Callers that serve HTTP use the published
    snapshot instead of this.
    """
    from hedge_fund.trading.tested_index import ensure_tested_index, load_tested_metrics

    ensure_tested_index()
    rows: list[dict] = []
    for name, metric in load_tested_metrics().items():
        if not isinstance(name, str) or not isinstance(metric, dict):
            continue
        if metric.get("ops_park"):
            continue
        sharpe = _finite(metric.get("sharpe"))
        if sharpe is None:
            continue
        row = {"strategy": name, "sharpe": sharpe, "ops_park": False}
        trades = _finite(metric.get("trades"))
        if trades is not None:
            row["trades"] = int(trades)
        rows.append(row)
    return rows


_model_guard = threading.Lock()
_models: dict[str, LiftModel] = {}
_payload_cache = None  # StaleCache, created lazily so import stays light
_compute_guard = threading.Lock()
_refresh_guard = threading.Lock()
_refresh_running = False
_last_refresh_at = 0.0


def _state_key() -> str:
    from hedge_fund.paths import state_root

    return str(state_root().resolve())


def _payload_store():
    global _payload_cache
    if _payload_cache is None:
        from hedge_fund.web.ttl_cache import StaleCache

        _payload_cache = StaleCache()
    return _payload_cache


def _publish(model: LiftModel) -> None:
    key = _state_key()
    with _model_guard:
        _models[key] = model
    _payload_store().put(key, lift_api_payload(model))


def peek_lift_model() -> LiftModel | None:
    """In-memory model for this PAPER_STATE. Does not read disk and does not fit."""
    with _model_guard:
        return _models.get(_state_key())


def peek_refill_strategy() -> str:
    """``lift_ucb`` / ``recipe_order`` once a snapshot exists, else ``pending``."""
    model = peek_lift_model()
    if model is None:
        return STRATEGY_PENDING
    return model.strategy or STRATEGY_PENDING


def pending_lift_payload() -> dict:
    return {
        "paper_only": True,
        "strategy": STRATEGY_PENDING,
        "explore_share": EXPLORE_SHARE,
        "names_used": 0,
        "support_min": MIN_SUPPORT,
        "top": [],
        "bottom": [],
        "ready": False,
    }


def lift_payload_for_request() -> dict:
    """Cached top/bottom atoms. A cold cache returns ``pending`` and refreshes later."""
    cached = _payload_store().peek(_state_key())
    if isinstance(cached, dict):
        return cached
    disk = load_lift_cache()
    if disk is not None:
        _publish(disk)
        served = _payload_store().peek(_state_key())
        if isinstance(served, dict):
            return served
    schedule_lift_refresh()
    return pending_lift_payload()


def lift_model_for_mint() -> LiftModel:
    """Published snapshot for refill/densify.

    Claim and ingest call this under the discovery lock. It does not fit,
    does not read the lift cache, and does not schedule a refresh. A missing
    snapshot is a flat model (recipe order) until the background thread
    publishes one.
    """
    model = peek_lift_model()
    if model is None:
        return LiftModel()
    return model


def ensure_lift_model() -> LiftModel:
    """Fit when the metrics fingerprint changed. Does not hold the discovery lock.

    The lock is taken only to write ``discovery_lift.json`` after the fit.
    Request handlers use ``lift_payload_for_request`` / ``lift_model_for_mint``.
    """
    from hedge_fund.trading.store import paper_state_lock

    with _compute_guard:
        rows = training_rows()
        fingerprint = fingerprint_rows(rows)
        cached = peek_lift_model()
        if cached is None:
            cached = load_lift_cache()
        if (
            cached is not None
            and cached.fingerprint == fingerprint
            and cached.support_min == MIN_SUPPORT
            and cached.explore_share == EXPLORE_SHARE
        ):
            _publish(cached)
            return cached
        model = estimate_lifts(rows)
        model.fingerprint = fingerprint
        with paper_state_lock("discovery"):
            save_lift_cache(model)
        _publish(model)
        return model


def _claim_refresh_slot() -> bool:
    """True when this caller may start a fit. At most once per five minutes."""
    global _refresh_running, _last_refresh_at
    with _refresh_guard:
        if _refresh_running:
            return False
        now = time.monotonic()
        if _last_refresh_at and (now - _last_refresh_at) < LIFT_REFRESH_SECONDS:
            return False
        _refresh_running = True
        _last_refresh_at = now
        return True


def _release_refresh_slot() -> None:
    global _refresh_running
    with _refresh_guard:
        _refresh_running = False


def schedule_lift_refresh() -> None:
    """Fit on a daemon thread, at most once per ``LIFT_REFRESH_SECONDS``.

    A second call while one is running, or inside the window, does nothing.
    """
    if not _claim_refresh_slot():
        return

    def _run() -> None:
        try:
            ensure_lift_model()
        except Exception as exc:
            print(f"[discovery-lift] rebuild failed: {exc}", file=sys.stderr, flush=True)
        finally:
            _release_refresh_slot()

    threading.Thread(target=_run, name="discovery-lift", daemon=True).start()


def refresh_lift_cache() -> None:
    """Background-loop entry. Schedules a fit; does not fit on the caller."""
    schedule_lift_refresh()
