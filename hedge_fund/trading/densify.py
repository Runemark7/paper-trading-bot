"""Ordered densify iterator for when ``iter_recipe_names`` runs short.

Seeds are qualified names in the tested-name index (the admit island).
When OOS metrics are passed in, seeds also include non-passes with a
positive full-history Sharpe and at least one trade, ordered by that
Sharpe. Each yielded name changes one axis of a seed (HTF period, momentum
lookback, momentum percent, MA period, RSI threshold) or ANDs one extra
parser-allowed atom, up to ``RECIPE_MAX_ATOMS``. Rank 0 is the nearest
step; later ranks walk outward. A call takes the first ``n`` names that
still pass the mint rules, so the stream is unbounded across calls and
bounded per call. An informative atom-lift model reorders a window of
those names (most slots by lift, the rest explored) and leaves the mint
rules in front of that ranking.

Rules (same as the recipe):
- parser-allowed atoms only
- no ``mom_*`` AND ``dip_*`` in one stack
- no structure lookback N>96 on ``don_*`` / ``near_swing_*`` / ``dbl_bot_*``
- skip ``near_duplicate_key`` collisions and already-tested names
- skip the h4×mom family (measured all-fail) and any family/spine with
  at least ``BURNED_MIN_TESTED`` evaluations and zero passes
- skip redundant same-indicator thresholds and empty bands
  (``mint_block_reason``)
- cap mom/dip percents at the reachability table, and stop stepping an
  axis once a nearer neighbor on that axis recorded trades=0
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator

from hedge_fund.trading.discovery_guard import (
    DEFAULT_STRUCTURE_LOOKBACK_MAX,
    is_ops_park_record,
    lookback_too_expensive_reason,
)
from hedge_fund.trading.constants import MIN_BACKTEST_TRADES, QUAL_N_WINDOWS
from hedge_fund.trading.mint_quality import (
    canonical_key_set,
    canonical_name,
    max_move_pct,
    mint_block_reason,
)
from hedge_fund.trading.refill import (
    RECIPE_MAX_ATOMS,
    _is_refillable_name,
    name_has_mom_gt_and_dip,
    name_is_parseable,
)
from hedge_fund.trading.universe import _canon_atom, _round_period, near_duplicate_key

BURNED_MIN_TESTED = 30
# Nearest-first steps. 64 ranks of period/lookback is far past the island;
# each claim only consumes ``n`` accepted names.
DENSIFY_MAX_RANK = 64
# Cap measured seeds so a 12k index does not explode the neighbor walk.
SEED_CAP = 64

_HTF_RE = re.compile(r"^(h[14])_(ema|sma)_abv_(\d+)$")
_MOM_RE = re.compile(r"^mom_(\d+)b_gt(\d+)pc$")
_DIP_RE = re.compile(r"^dip_(\d+)b_lt(\d+)pc$")
_MA_RE = re.compile(r"^(sma_abv|ema_abv)_(\d+)$")
_RSI_RE = re.compile(r"^rsi_(\d+)_>(\d+)(?:_<(\d+))?$")
_STRUCT_RE = re.compile(
    r"^(don_hi|don_lo|near_swing_hi|near_swing_lo|dbl_bot)_(\d+)$"
)
_STRUCT_PREFIXES = (
    "don_hi_",
    "don_lo_",
    "near_swing_hi_",
    "near_swing_lo_",
    "dbl_bot_",
)

# Admitted-family atoms first (RSI / short MA / the paying mom), then
# wider continuation, then cheap structure. Dip atoms are not in this
# list: adding one to a mom seed is rejected, and dip seeds already
# vary their own dip axis.
EXTRA_ATOMS: tuple[str, ...] = (
    "rsi_14_>45",
    "rsi_14_>50",
    "rsi_14_>55",
    "rsi_14_>40",
    "rsi_14_>60",
    "rsi_7_>50",
    "rsi_21_>50",
    "sma_abv_20",
    "sma_abv_30",
    "sma_abv_50",
    "ema_abv_20",
    "ema_abv_30",
    "ema_abv_50",
    "sma_abv_15",
    "ema_abv_15",
    "sma_abv_25",
    "ema_abv_25",
    "sma_abv_35",
    "ema_abv_35",
    "sma_abv_40",
    "ema_abv_40",
    "sma_abv_60",
    "ema_abv_60",
    "sma_abv_70",
    "ema_abv_70",
    "sma_abv_100",
    "ema_abv_100",
    "sma_abv_200",
    "mom_18b_gt2pc",
    "mom_18b_gt4pc",
    "mom_24b_gt2pc",
    "mom_24b_gt4pc",
    "mom_30b_gt2pc",
    "mom_12b_gt2pc",
    "mom_36b_gt2pc",
    "mom_6b_gt2pc",
    "mom_48b_gt2pc",
    "mom_24b_gt6pc",
    "mom_18b_gt6pc",
    "near_swing_hi_12",
    "near_swing_hi_24",
    "near_swing_hi_48",
    "don_hi_12",
    "don_hi_24",
    "don_hi_48",
    "sma_stack_20_50_100",
    "ema_stack_20_50_100",
)


def _canon_mom_lb(n: int) -> int:
    return int(round(int(n) / 6.0) * 6) or 6


def _canon_mom_thr(n: int) -> int:
    return int(round(int(n) / 2.0) * 2) or 2


def _pct_hi(lookback: int) -> int:
    """Highest mom/dip percent densify may step to at this lookback."""
    cap = int(max_move_pct(lookback))
    return max(2, min(40, cap))


def _canon_rsi_th(n: int) -> int:
    return int(round(int(n) / 5.0) * 5)


def _canon_struct(n: int) -> int:
    return int(round(int(n) / 6.0) * 6) or 6


def _neighbors(current: int, *, step: int, lo: int, hi: int, canon) -> list[int]:
    """Canon values other than ``current``, nearest distance first."""
    origin = canon(current)
    seen = {origin}
    found: list[int] = []
    for k in range(1, 80):
        for raw in (current - k * step, current + k * step):
            if raw < lo or raw > hi:
                continue
            c = canon(raw)
            if c < lo or c > hi or c in seen:
                continue
            seen.add(c)
            found.append(c)
    found.sort(key=lambda c: (abs(c - origin), c))
    return found


def _atoms(name: str) -> list[str]:
    return [p.strip() for p in name.split("&") if p.strip()]


def keys_for(name: str) -> tuple[str, ...]:
    """Family, spine, and extra buckets used for the zero-pass burn rule.

    Any stack that contains an h4 regime atom and a ``mom_*`` atom is
    ``h4×mom``, including dual h1+h4 names.
    """
    atoms = _atoms(name)
    has_mom = any(a.startswith("mom_") for a in atoms)
    has_dip = any(a.startswith("dip_") for a in atoms)
    has_h4 = any(a.startswith("h4_") for a in atoms)
    has_h1 = any(a.startswith("h1_") for a in atoms)
    entry = "mom" if has_mom else ("dip" if has_dip else "flat")
    struct = next((a for a in atoms if a.startswith(_STRUCT_PREFIXES)), None)
    if has_h4 and has_mom:
        family = "h4×mom"
    elif has_h1 and has_mom:
        family = "h1×mom"
    elif has_h4:
        family = f"h4×{entry}"
    elif has_h1:
        family = f"h1×{entry}"
    elif struct and has_mom:
        family = "struct×mom"
    elif struct and has_dip:
        family = "struct×dip"
    elif has_mom:
        family = "mom"
    elif has_dip:
        family = "dip"
    else:
        family = "other"
    if has_h4 and has_mom:
        h4 = next(a for a in atoms if a.startswith("h4_"))
        spine = f"{_canon_atom(h4)}×mom"
    elif has_h1:
        h1 = next(a for a in atoms if a.startswith("h1_"))
        spine = f"{_canon_atom(h1)}×{entry}"
    elif has_h4:
        h4 = next(a for a in atoms if a.startswith("h4_"))
        spine = f"{_canon_atom(h4)}×{entry}"
    elif struct:
        tag = struct.rsplit("_", 1)[0]
        spine = f"{tag}×{entry}"
    else:
        spine = f"plain×{entry}"
    keys = [family, spine]
    if struct and has_mom:
        keys.append("struct×mom")
    elif struct and has_dip:
        keys.append("struct×dip")
    # Preserve order, drop duplicates (family may already be struct×mom).
    out: list[str] = []
    for key in keys:
        if key not in out:
            out.append(key)
    return tuple(out)


def burned_keys(index: dict[str, bool]) -> set[str]:
    """``h4×mom`` always, plus any key with >=30 tests and zero passes."""
    tested: dict[str, int] = {}
    passed: dict[str, int] = {}
    for name, qual in index.items():
        if not isinstance(name, str) or not name:
            continue
        for key in keys_for(name):
            tested[key] = tested.get(key, 0) + 1
            if qual:
                passed[key] = passed.get(key, 0) + 1
    burned = {"h4×mom"}
    for key, n in tested.items():
        if n >= BURNED_MIN_TESTED and passed.get(key, 0) == 0:
            burned.add(key)
    return burned


def _seed_sort_key(name: str) -> tuple:
    atoms = _atoms(name)
    has_h1 = any(a.startswith("h1_") for a in atoms)
    has_h4 = any(a.startswith("h4_") for a in atoms)
    has_mom = any(a.startswith("mom_") for a in atoms)
    has_ma = any(a.startswith(("sma_abv_", "ema_abv_")) for a in atoms)
    has_rsi = any(a.startswith("rsi_") for a in atoms)
    if has_h1 and has_mom and (has_ma or has_rsi):
        bucket = 0
    elif has_h1 and has_mom:
        bucket = 1
    elif has_h1:
        bucket = 2
    elif has_mom and not has_h4:
        bucket = 3
    else:
        bucket = 4
    return (bucket, name)


def _metric_numbers(metric: dict | None) -> tuple[float | None, int | None]:
    if not isinstance(metric, dict):
        return None, None
    sharpe = metric.get("sharpe")
    trades = metric.get("trades")
    if isinstance(sharpe, bool) or not isinstance(sharpe, (int, float)):
        sharpe_n = None
    else:
        sharpe_n = float(sharpe)
    if isinstance(trades, bool) or not isinstance(trades, (int, float)):
        trades_n = None
    else:
        trades_n = int(trades)
    return sharpe_n, trades_n


def _row_for_seed(name: str, metrics: dict | None, results: dict | None) -> dict:
    row: dict = {}
    if metrics and isinstance(metrics.get(name), dict):
        row.update(metrics[name])
    if results and isinstance(results.get(name), dict):
        for key, value in results[name].items():
            if value is not None:
                row[key] = value
    return row


def _window_count(row: dict) -> int | None:
    regimes = row.get("regimes_tested")
    if isinstance(regimes, bool) or not isinstance(regimes, (int, float)):
        windows = row.get("windows")
        if isinstance(windows, list):
            return len(windows)
        return None
    return int(regimes)


def _excess_pnl(row: dict) -> float | None:
    pnl = row.get("test_pnl")
    bh = row.get("bh_oos_pnl")
    if isinstance(pnl, bool) or not isinstance(pnl, (int, float)):
        return None
    if isinstance(bh, bool) or not isinstance(bh, (int, float)):
        return None
    excess = float(pnl) - float(bh)
    if excess != excess or excess in (float("inf"), float("-inf")):
        return None
    return excess


def order_seeds(
    index: dict[str, bool],
    metrics: dict | None = None,
    results: dict | None = None,
) -> list[str]:
    """Seed names for densify.

    No metrics and no results → qualified names in the historical
    heuristic order (unit tests of the neighbor walk).

    Otherwise seed only names whose result used the current window
    count (``regimes_tested`` or ``len(windows)`` == ``QUAL_N_WINDOWS``)
    and at least ``MIN_BACKTEST_TRADES`` trades, best net OOS P&L minus
    buy-and-hold first. Sharpe breaks ties. Old 8-window passes stay in
    the index and are not seeds.

    When nothing was tested on the current window set, the near-miss
    band is trades ≥ 30 and net P&L > 0. Known other window counts are
    left out of that band too: the fallback is for rows that never
    recorded a window count.
    """
    if results is None and not metrics:
        names = [name for name, qual in index.items() if qual and isinstance(name, str) and name]
        names.sort(key=_seed_sort_key)
        return names
    current: list[tuple[bool, float, float, str]] = []
    any_current = False
    for name in index:
        if not isinstance(name, str) or not name:
            continue
        row = _row_for_seed(name, metrics, results)
        windows = _window_count(row)
        sharpe, trades = _metric_numbers(row)
        if windows == QUAL_N_WINDOWS:
            any_current = True
        if (
            windows == QUAL_N_WINDOWS
            and trades is not None
            and trades >= MIN_BACKTEST_TRADES
        ):
            excess = _excess_pnl(row)
            current.append((excess is None, -(excess or 0.0), -(sharpe or 0.0), name))
    if any_current:
        current.sort()
        return [item[3] for item in current[:SEED_CAP]]
    near: list[tuple[bool, float, float, str]] = []
    for name in index:
        if not isinstance(name, str) or not name:
            continue
        row = _row_for_seed(name, metrics, results)
        if _window_count(row) is not None:
            continue
        _sharpe, trades = _metric_numbers(row)
        pnl = row.get("test_pnl")
        if (
            trades is None
            or trades < MIN_BACKTEST_TRADES
            or isinstance(pnl, bool)
            or not isinstance(pnl, (int, float))
            or float(pnl) <= 0
        ):
            continue
        excess = _excess_pnl(row)
        near.append((excess is None, -(excess or 0.0), -float(pnl), name))
    near.sort()
    return [item[3] for item in near[:SEED_CAP]]


def _atom_replacements(atom: str, rank: int) -> list[str]:
    """One-axis neighbors at ``rank`` (possibly several sub-axes)."""
    m = _HTF_RE.match(atom)
    if m:
        neigh = _neighbors(int(m.group(3)), step=5, lo=5, hi=400, canon=_round_period)
        if rank < len(neigh):
            return [f"{m.group(1)}_{m.group(2)}_abv_{neigh[rank]}"]
        return []
    m = _MOM_RE.match(atom)
    if m:
        lb = int(m.group(1))
        thr = int(m.group(2))
        out: list[str] = []
        lbs = _neighbors(lb, step=6, lo=6, hi=360, canon=_canon_mom_lb)
        ths = _neighbors(
            thr, step=2, lo=2, hi=_pct_hi(lb), canon=_canon_mom_thr,
        )
        if rank < len(lbs):
            out.append(f"mom_{lbs[rank]}b_gt{thr}pc")
        if rank < len(ths):
            out.append(f"mom_{lb}b_gt{ths[rank]}pc")
        return out
    m = _DIP_RE.match(atom)
    if m:
        lb = int(m.group(1))
        thr = int(m.group(2))
        out = []
        lbs = _neighbors(lb, step=6, lo=6, hi=360, canon=_canon_mom_lb)
        ths = _neighbors(
            thr, step=2, lo=2, hi=_pct_hi(lb), canon=_canon_mom_thr,
        )
        if rank < len(lbs):
            out.append(f"dip_{lbs[rank]}b_lt{thr}pc")
        if rank < len(ths):
            out.append(f"dip_{lb}b_lt{ths[rank]}pc")
        return out
    m = _MA_RE.match(atom)
    if m:
        neigh = _neighbors(int(m.group(2)), step=5, lo=5, hi=400, canon=_round_period)
        if rank < len(neigh):
            return [f"{m.group(1)}_{neigh[rank]}"]
        return []
    m = _RSI_RE.match(atom)
    if m:
        period = int(m.group(1))
        th = int(m.group(2))
        upper = f"_<{m.group(3)}" if m.group(3) else ""
        out = []
        periods = _neighbors(period, step=5, lo=5, hi=50, canon=_round_period)
        ths = _neighbors(th, step=5, lo=5, hi=90, canon=_canon_rsi_th)
        if rank < len(periods):
            out.append(f"rsi_{periods[rank]}_>{th}{upper}")
        if rank < len(ths):
            out.append(f"rsi_{period}_>{ths[rank]}{upper}")
        return out
    m = _STRUCT_RE.match(atom)
    if m:
        cap = DEFAULT_STRUCTURE_LOOKBACK_MAX
        neigh = _neighbors(int(m.group(2)), step=6, lo=6, hi=cap, canon=_canon_struct)
        if rank < len(neigh):
            return [f"{m.group(1)}_{neigh[rank]}"]
        return []
    return []


def _extra_name(seed: str, rank: int) -> str | None:
    atoms = _atoms(seed)
    if len(atoms) >= RECIPE_MAX_ATOMS or rank >= len(EXTRA_ATOMS):
        return None
    extra = EXTRA_ATOMS[rank]
    have = {_canon_atom(a) for a in atoms}
    if _canon_atom(extra) in have or extra in atoms:
        return None
    return "&".join(atoms + [extra])


def _atom_axes(atom: str) -> list[tuple[str, tuple, float]]:
    """Numeric axes densify steps. Fixed tuple omits this axis's own value."""
    m = _HTF_RE.match(atom)
    if m:
        return [("htf_period", (m.group(1), m.group(2)), float(m.group(3)))]
    m = _MOM_RE.match(atom)
    if m:
        lb, thr = float(m.group(1)), float(m.group(2))
        return [
            ("mom_lb", ("mom", thr), lb),
            ("mom_thr", ("mom", lb), thr),
        ]
    m = _DIP_RE.match(atom)
    if m:
        lb, thr = float(m.group(1)), float(m.group(2))
        return [
            ("dip_lb", ("dip", thr), lb),
            ("dip_thr", ("dip", lb), thr),
        ]
    m = _MA_RE.match(atom)
    if m:
        return [("ma_period", (m.group(1),), float(m.group(2)))]
    m = _RSI_RE.match(atom)
    if m:
        period, th = float(m.group(1)), float(m.group(2))
        upper = float(m.group(3)) if m.group(3) else None
        axes = [
            ("rsi_period", (th, upper), period),
            ("rsi_gt", (period, upper), th),
        ]
        if upper is not None:
            axes.append(("rsi_lt", (period, th), upper))
        return axes
    m = _STRUCT_RE.match(atom)
    if m:
        return [("struct_n", (m.group(1),), float(m.group(2)))]
    return []


def _single_axis_change(old: str, new: str) -> tuple[str, tuple, float, float] | None:
    """``(axis, fixed, old_value, new_value)`` when exactly one axis value moved."""
    old_axes = {axis: (fixed, value) for axis, fixed, value in _atom_axes(old)}
    new_axes = {axis: (fixed, value) for axis, fixed, value in _atom_axes(new)}
    if set(old_axes) != set(new_axes) or not old_axes:
        return None
    changed = [axis for axis in old_axes if old_axes[axis][1] != new_axes[axis][1]]
    if len(changed) != 1:
        return None
    axis = changed[0]
    fixed, old_v = old_axes[axis]
    _fixed_new, new_v = new_axes[axis]
    if fixed != _fixed_new:
        return None
    return axis, fixed, old_v, new_v


def _zero_axis_index(zero_names: Iterable[str]) -> dict[tuple, set[float]]:
    """Map ``(other atoms, axis, fixed params)`` → values that printed trades=0."""
    index: dict[tuple, set[float]] = {}
    for name in zero_names:
        if not name:
            continue
        atoms = _atoms(name)
        for i, atom in enumerate(atoms):
            others = tuple(sorted(atoms[:i] + atoms[i + 1 :]))
            for axis, fixed, value in _atom_axes(atom):
                key = (others, axis, fixed)
                index.setdefault(key, set()).add(value)
    return index


def _dead_end(
    seed_atoms: list[str],
    index: int,
    new_atom: str,
    zero_index: dict[tuple, set[float]],
) -> bool:
    """True when a trades=0 neighbor sits on this step or between it and the seed.

    Further steps in that direction are dead. The opposite direction stays
    open. Ops-park rows are not in ``zero_index``.
    """
    if not zero_index:
        return False
    change = _single_axis_change(seed_atoms[index], new_atom)
    if change is None:
        return False
    axis, fixed, old_v, new_v = change
    if new_v == old_v:
        return False
    others = tuple(sorted(seed_atoms[:index] + seed_atoms[index + 1 :]))
    values = zero_index.get((others, axis, fixed))
    if not values:
        return False
    direction = 1.0 if new_v > old_v else -1.0
    span = abs(new_v - old_v)
    for value in values:
        delta = (value - old_v) * direction
        if delta > 0 and delta <= span + 1e-9:
            return True
    return False


def zero_trade_names_from_rows(rows: Iterable[dict]) -> set[str]:
    """Newest row per name with an explicit trades=0 that is not an ops park.

    The display log is newest-first. A missing ``trades`` field is not zero.
    """
    newest: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("strategy")
        if not isinstance(name, str) or not name or name in newest:
            continue
        newest[name] = row
    out: set[str] = set()
    for name, row in newest.items():
        if is_ops_park_record(row):
            continue
        trades = row.get("trades")
        if isinstance(trades, bool) or not isinstance(trades, (int, float)):
            continue
        if int(trades) == 0:
            out.add(name)
    return out


def iter_densify_names(
    passes: Iterable[str],
    burned: set[str],
    zero_names: Iterable[str] | None = None,
    *,
    keep_order: bool = False,
) -> Iterator[str]:
    """Deterministic stream. Same seeds and burn set → same order.

    ``keep_order`` preserves the caller's seed ranking (Sharpe order).
    Otherwise seeds sort by the historical heuristic.
    """
    seeds: list[str] = []
    for name in passes:
        if not isinstance(name, str) or not name:
            continue
        if name_has_mom_gt_and_dip(name) or not name_is_parseable(name):
            continue
        if any(key in burned for key in keys_for(name)):
            continue
        seeds.append(name)
    if not keep_order:
        seeds.sort(key=_seed_sort_key)
    zero_index = _zero_axis_index(zero_names or ())
    limit = max(DENSIFY_MAX_RANK, len(EXTRA_ATOMS))
    for rank in range(limit):
        for seed in seeds:
            atoms = _atoms(seed)
            for i, atom in enumerate(atoms):
                for repl in _atom_replacements(atom, rank):
                    if _dead_end(atoms, i, repl, zero_index):
                        continue
                    nxt = list(atoms)
                    nxt[i] = repl
                    yield "&".join(nxt)
            extra = _extra_name(seed, rank)
            if extra and not mint_block_reason(extra):
                yield extra


def candidate_allowed(
    name: str,
    *,
    taken: set[str],
    taken_keys: set[str],
    burned: set[str],
    tested_keys: set[str] | None = None,
) -> bool:
    """Mint rules for one generated name."""
    if not name or not name_is_parseable(name):
        return False
    if name_has_mom_gt_and_dip(name):
        return False
    if mint_block_reason(name, tested_keys):
        return False
    if name in taken or near_duplicate_key(name) in taken_keys:
        return False
    # Generator hard-cap. Env can only make this stricter.
    if lookback_too_expensive_reason(name, cap=DEFAULT_STRUCTURE_LOOKBACK_MAX):
        return False
    if lookback_too_expensive_reason(name):
        return False
    keys = keys_for(name)
    # h4×mom is a measured all-fail family, including before 30 samples.
    if "h4×mom" in keys or any(key in burned for key in keys):
        return False
    if not _is_refillable_name(name):
        return False
    return True


def next_densify_batch(
    *,
    taken_names: Iterable[str],
    n: int,
    index: dict[str, bool] | None = None,
    zero_trade_names: Iterable[str] | None = None,
    lift=None,
    metrics: dict | None = None,
    results: dict | None = None,
    lift_seed: int | None = None,
) -> tuple[list[str], bool]:
    """First ``n`` densify names not in ``taken_names``.

    Returns ``(names, exhausted)``. ``exhausted`` is true when the
    iterator ended before ``n`` accepts (including when there are no
    qualified seeds). ``zero_trade_names`` defaults to explicit trades=0
    rows in the discovery log (ops parks excluded).

    ``lift`` is ignored unless it is informative. Then a window of legal
    names is ranked by expected lift and about a quarter of the slots are
    an explore draw. ``results`` (full per-name records) reorder seeds by
    net OOS P&L minus buy-and-hold on the current window count. ``metrics``
    fills sharpe/trades when the result row is thin. Both default to off
    so a caller that only has the bool index keeps the historical stream.
    """
    from hedge_fund.trading.atom_lift import LIFT_SEED, STEER_WINDOW, steer_candidates

    want = max(0, int(n))
    if want == 0:
        return [], False
    if index is None:
        from hedge_fund.trading.tested_index import ensure_tested_index

        index = ensure_tested_index()
    if zero_trade_names is None:
        from hedge_fund.trading.discovery import load_discovery_log

        zero_trade_names = zero_trade_names_from_rows(load_discovery_log())
    burned = burned_keys(index)
    passes = order_seeds(index, metrics, results)
    taken = {name for name in taken_names if name}
    taken_keys = {near_duplicate_key(name) for name in taken}
    taken_canon = canonical_key_set(taken)
    informative = lift is not None and bool(getattr(lift, "informative", False))
    target = want * STEER_WINDOW if informative else want
    out: list[str] = []
    for raw in iter_densify_names(passes, burned, zero_trade_names, keep_order=True):
        cand = canonical_name(raw) or raw
        if not candidate_allowed(
            cand,
            taken=taken,
            taken_keys=taken_keys,
            burned=burned,
            tested_keys=taken_canon,
        ):
            continue
        out.append(cand)
        taken.add(cand)
        taken_keys.add(near_duplicate_key(cand))
        taken_canon.add(cand)
        if len(out) >= target:
            if not informative:
                return out, False
            break
    else:
        if not informative:
            return out, True
        picked = steer_candidates(
            out,
            lift,
            want,
            seed=LIFT_SEED if lift_seed is None else int(lift_seed),
        )
        return picked, len(picked) < want
    picked = steer_candidates(
        out,
        lift,
        want,
        seed=LIFT_SEED if lift_seed is None else int(lift_seed),
    )
    return picked, False
