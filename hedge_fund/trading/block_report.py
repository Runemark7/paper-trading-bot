"""Exploratory marginal effects of atom families and paired swaps.

Read-only. Pre-holdout rows only: ``gate_rules`` is the current gate
(18 walk-forward windows that end before the sealed holdout) and the row
is not an ops park. A row whose window list is a different length, or
whose ``regimes_tested`` is not that window count, is left out. So are
rows that carry a window end at or after the holdout.

The depth control is a matched comparison: within each atom-count, the
mean with the block minus the mean without it, weighted by the smaller
side. It is not a regression coefficient and it is not a gate input.
Paired swaps compare child minus parent on OOS daily Sharpe, with a
two-sided sign test and a t interval.

Nothing here changes qualification, the trial count, or what gets minted.
"""
from __future__ import annotations

import math
from collections import defaultdict

from hedge_fund.trading.constants import DISCOVERY_N_WINDOWS, GATE_RULES, HOLDOUT_START_MS
from hedge_fund.trading.discovery_guard import is_ops_park_record
from hedge_fund.trading.explorer import AXES, load_pair_links
from hedge_fund.trading.mint_quality import inspect_atom

_METRIC = ("daily_sharpe", "sharpe", "trades")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _window_crosses_holdout(row: dict) -> bool:
    windows = row.get("windows")
    if not isinstance(windows, list):
        return False
    for window in windows:
        if not isinstance(window, dict):
            continue
        for key in ("end_ms", "end", "test_end_ms"):
            value = _number(window.get(key))
            if value is not None and int(value) >= HOLDOUT_START_MS:
                return True
    return False


def is_pre_holdout(row: dict) -> bool:
    """True for a current-gate discovery row that did not score the holdout."""
    if not isinstance(row, dict) or row.get("partial"):
        return False
    if is_ops_park_record(row):
        return False
    if row.get("gate_rules") != GATE_RULES:
        return False
    counted = row.get("regimes_tested")
    if isinstance(counted, int) and not isinstance(counted, bool) and counted != DISCOVERY_N_WINDOWS:
        return False
    windows = row.get("windows")
    if isinstance(windows, list) and windows and len(windows) != DISCOVERY_N_WINDOWS:
        return False
    if _window_crosses_holdout(row):
        return False
    return any(_number(row.get(key)) is not None for key in _METRIC)


def _atoms(name: str) -> list[str]:
    return [part.strip() for part in str(name).split("&") if part.strip()]


def _bucket(atom: str, family: str, params: tuple, threshold: object, op: str) -> str:
    if family in ("mom", "dip") and params:
        lookback = int(params[0])
        span = "short" if lookback <= 24 else ("mid" if lookback <= 96 else "long")
        pct = int(threshold) if isinstance(threshold, (int, float)) and not isinstance(threshold, bool) else 0
        pct_bin = "2" if pct <= 2 else ("4" if pct <= 4 else ("6" if pct <= 6 else "8plus"))
        return f"{family}:{span}:{pct_bin}pc"
    if family in ("sma_abv", "ema_abv") or family.endswith("_abv"):
        period = int(params[0]) if params else 0
        span = "fast" if period <= 40 else ("mid" if period <= 100 else "slow")
        return f"{family}:{span}"
    if family in ("don_hi", "don_lo", "near_swing_hi", "near_swing_lo", "dbl_bot"):
        n = int(params[0]) if params else 0
        span = "le24" if n <= 24 else ("le48" if n <= 48 else ("le96" if n <= 96 else "gt96"))
        return f"{family}:{span}"
    if family == "rsi":
        if op == "band":
            return "rsi:band"
        thr = int(threshold) if isinstance(threshold, (int, float)) and not isinstance(threshold, bool) else 0
        band = int(round(thr / 10.0) * 10)
        return f"rsi:{op}:{band}"
    if family in ("sma_stack", "ema_stack"):
        longest = max(params) if params else 0
        span = "fast" if longest <= 100 else "slow"
        return f"{family}:{span}"
    return family


def _blocks(name: str) -> tuple[set[str], set[str]]:
    families: set[str] = set()
    buckets: set[str] = set()
    for atom in _atoms(name):
        parts = inspect_atom(atom)
        if parts is None:
            continue
        families.add(parts.family)
        buckets.add(_bucket(atom, parts.family, parts.params, parts.threshold, parts.op))
    return families, buckets


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _matched(rows: list[dict], holds: set[int], field: str) -> float | None:
    """Within-depth mean(with) − mean(without), weighted by the smaller side."""
    by_depth: dict[int, list[dict]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_depth[int(row["depth"])].append(row)
    numerator = 0.0
    denominator = 0.0
    for group in by_depth.values():
        with_values = [
            value
            for row in group
            if id(row) in holds and (value := _number(row.get(field))) is not None
        ]
        without_values = [
            value
            for row in group
            if id(row) not in holds and (value := _number(row.get(field))) is not None
        ]
        if not with_values or not without_values:
            continue
        weight = min(len(with_values), len(without_values))
        left = _mean(with_values)
        right = _mean(without_values)
        if left is None or right is None:
            continue
        numerator += weight * (left - right)
        denominator += weight
    if denominator == 0:
        return None
    return numerator / denominator


def _side(rows: list[dict], holds: set[int], field: str, *, inside: bool) -> float | None:
    values = [
        value
        for row in rows
        if (id(row) in holds) == inside and (value := _number(row.get(field))) is not None
    ]
    return _mean(values)


def _pass_rate(rows: list[dict], holds: set[int], *, inside: bool) -> float | None:
    group = [row for row in rows if (id(row) in holds) == inside]
    if not group:
        return None
    return sum(1 for row in group if row.get("qualified")) / len(group)


def _effect(rows: list[dict], holds: set[int], block: str, level: str) -> dict:
    n_with = sum(1 for row in rows if id(row) in holds)
    return {
        "block": block,
        "level": level,
        "n_with": n_with,
        "n_without": len(rows) - n_with,
        "mean_daily_sharpe_with": _round(_side(rows, holds, "daily_sharpe", inside=True)),
        "mean_daily_sharpe_without": _round(_side(rows, holds, "daily_sharpe", inside=False)),
        "mean_trade_sharpe_with": _round(_side(rows, holds, "sharpe", inside=True)),
        "mean_trade_sharpe_without": _round(_side(rows, holds, "sharpe", inside=False)),
        "mean_trades_with": _round(_side(rows, holds, "trades", inside=True)),
        "mean_trades_without": _round(_side(rows, holds, "trades", inside=False)),
        "pass_rate_with": _round(_pass_rate(rows, holds, inside=True)),
        "pass_rate_without": _round(_pass_rate(rows, holds, inside=False)),
        "depth_controlled_daily_sharpe": _round(_matched(rows, holds, "daily_sharpe")),
        "method": "within-depth matched mean difference",
    }


def _sort_key(row: dict) -> tuple:
    effect = row.get("depth_controlled_daily_sharpe")
    magnitude = abs(effect) if isinstance(effect, (int, float)) else -1.0
    return (-magnitude, -int(row.get("n_with") or 0), str(row.get("block")))


def sign_test_p(positive: int, negative: int) -> float | None:
    """Two-sided exact sign test. Zeros are ignored. None when nothing signed."""
    pos = int(positive)
    neg = int(negative)
    n = pos + neg
    if n <= 0:
        return None
    try:
        from scipy.stats import binomtest

        return float(binomtest(pos, n, 0.5, alternative="two-sided").pvalue)
    except Exception:
        k = min(pos, neg)
        if n > 1024:
            # Normal approximation of the two-sided binomial sign test.
            z = abs(pos - n / 2.0) / math.sqrt(n / 4.0)
            from math import erfc

            p = erfc(z / math.sqrt(2.0))
            return min(1.0, p)
        total = 1 << n
        cdf = sum(math.comb(n, i) for i in range(k + 1))
        p = cdf / total
        if pos != neg:
            p *= 2.0
        return min(1.0, p)


def _mean_ci(deltas: list[float]) -> tuple[float | None, float | None, float | None]:
    n = len(deltas)
    if n == 0:
        return None, None, None
    mean = sum(deltas) / n
    if n < 2:
        return mean, None, None
    var = sum((item - mean) ** 2 for item in deltas) / (n - 1)
    se = math.sqrt(var / n)
    if se == 0.0:
        return mean, mean, mean
    try:
        from scipy import stats

        half = float(stats.t.ppf(0.975, n - 1)) * se
    except Exception:
        half = 1.96 * se
    return mean, mean - half, mean + half


def _paired(rows_by_name: dict[str, dict], pairs: list[dict]) -> list[dict]:
    grouped: dict[str, list[float]] = {axis: [] for axis in AXES}
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        axis = pair.get("axis")
        parent = pair.get("parent")
        child = pair.get("child")
        if not isinstance(axis, str) or axis not in grouped:
            continue
        if not isinstance(parent, str) or not isinstance(child, str) or parent == child:
            continue
        left = rows_by_name.get(parent)
        right = rows_by_name.get(child)
        if left is None or right is None:
            continue
        parent_sharpe = _number(left.get("daily_sharpe"))
        child_sharpe = _number(right.get("daily_sharpe"))
        if parent_sharpe is None or child_sharpe is None:
            continue
        grouped[axis].append(child_sharpe - parent_sharpe)
    out = []
    for axis in AXES:
        deltas = grouped[axis]
        positive = sum(1 for item in deltas if item > 0)
        negative = sum(1 for item in deltas if item < 0)
        zeros = sum(1 for item in deltas if item == 0)
        mean, low, high = _mean_ci(deltas)
        out.append(
            {
                "axis": axis,
                "n": len(deltas),
                "n_positive": positive,
                "n_negative": negative,
                "n_zero": zeros,
                "mean_daily_sharpe_delta": _round(mean),
                "ci95_low": _round(low),
                "ci95_high": _round(high),
                "sign_test_p": _round(sign_test_p(positive, negative)),
            }
        )
    return out


def build_block_report(
    rows: list[dict] | None = None,
    pairs: list[dict] | None = None,
) -> dict:
    """Marginal effects. Pass rows to test; the default reads stored results."""
    if rows is None:
        from hedge_fund.trading.discovery_results import records_by_name

        rows = list(records_by_name().values())
    if pairs is None:
        pairs = load_pair_links()
    usable = [row for row in rows if is_pre_holdout(row)]
    prepared: list[dict] = []
    family_rows: dict[str, set[int]] = defaultdict(set)
    bucket_rows: dict[str, set[int]] = defaultdict(set)
    by_name: dict[str, dict] = {}
    for row in usable:
        name = row.get("strategy")
        if not isinstance(name, str) or not name:
            continue
        families, buckets = _blocks(name)
        item = dict(row)
        item["depth"] = len(_atoms(name))
        prepared.append(item)
        by_name[name] = item
        marker = id(item)
        for family in families:
            family_rows[family].add(marker)
        for bucket in buckets:
            bucket_rows[bucket].add(marker)
    families_out = [
        _effect(prepared, holds, block, "family") for block, holds in family_rows.items()
    ]
    buckets_out = [
        _effect(prepared, holds, block, "bucket") for block, holds in bucket_rows.items()
    ]
    return {
        "paper_only": True,
        "exploratory": True,
        "changes_gate": False,
        "label": (
            "Exploratory. Pre-holdout results only (current gate, "
            f"{DISCOVERY_N_WINDOWS} windows ending before the sealed holdout). "
            "This report does not change the gate, qualification, trial count, or mint."
        ),
        "gate_rules": GATE_RULES,
        "n_results": len(prepared),
        "method": "within-depth matched mean difference; paired child−parent daily Sharpe",
        "families": sorted(families_out, key=_sort_key),
        "buckets": sorted(buckets_out, key=_sort_key),
        "paired": _paired(by_name, pairs),
    }


_cache: dict = {}


def cached_block_report() -> dict:
    """One report per results-file and pair-file generation. Not the summary poll."""
    from hedge_fund.trading.discovery_results import discovery_results_path

    root = state_root_token()
    token = (root, _stat(discovery_results_path()), _stat(pairs_file()))
    if _cache.get("token") == token and isinstance(_cache.get("report"), dict):
        return _cache["report"]
    report = build_block_report()
    _cache["token"] = token
    _cache["report"] = report
    return report


def state_root_token() -> str:
    from hedge_fund.paths import state_root

    return str(state_root().resolve())


def pairs_file():
    from hedge_fund.trading.explorer import pairs_path

    return pairs_path()


def _stat(path) -> tuple:
    try:
        st = path.stat()
    except OSError:
        return (str(path), 0, 0)
    return (str(path), int(st.st_mtime_ns), int(st.st_size))


def clear_block_report_cache() -> None:
    _cache.clear()
