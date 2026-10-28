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


_FIELDS = ("daily_sharpe", "sharpe", "trades")


class _Prepared:
    """Column view of the usable rows. Totals let each block cost one pass over its members."""

    __slots__ = ("values", "qualified", "depth", "n", "totals", "depth_totals", "q_total")

    def __init__(self, rows: list[dict]) -> None:
        self.values = {field: [_number(row.get(field)) for row in rows] for field in _FIELDS}
        self.qualified = [bool(row.get("qualified")) for row in rows]
        self.depth = [int(row["depth"]) for row in rows]
        self.n = len(rows)
        self.q_total = sum(self.qualified)
        self.totals = {}
        for field, column in self.values.items():
            present = [v for v in column if v is not None]
            self.totals[field] = (sum(present), len(present))
        depth_totals: dict[int, list[float]] = {}
        for value, depth in zip(self.values["daily_sharpe"], self.depth):
            slot = depth_totals.setdefault(depth, [0.0, 0])
            if value is not None:
                slot[0] += value
                slot[1] += 1
        self.depth_totals = depth_totals


def _ratio(total: float, count: int) -> float | None:
    return total / count if count else None


def _effect(prep: _Prepared, members: list[int], block: str, level: str) -> dict:
    n_with = len(members)
    out: dict = {"block": block, "level": level, "n_with": n_with, "n_without": prep.n - n_with}
    labels = {"daily_sharpe": "daily_sharpe", "sharpe": "trade_sharpe", "trades": "trades"}
    for field in _FIELDS:
        column = prep.values[field]
        w_sum = 0.0
        w_cnt = 0
        for i in members:
            value = column[i]
            if value is not None:
                w_sum += value
                w_cnt += 1
        t_sum, t_cnt = prep.totals[field]
        out[f"mean_{labels[field]}_with"] = _round(_ratio(w_sum, w_cnt))
        out[f"mean_{labels[field]}_without"] = _round(_ratio(t_sum - w_sum, t_cnt - w_cnt))
    q_with = sum(1 for i in members if prep.qualified[i])
    out["pass_rate_with"] = _round(q_with / n_with) if n_with else None
    n_without = prep.n - n_with
    out["pass_rate_without"] = _round((prep.q_total - q_with) / n_without) if n_without else None
    out["depth_controlled_daily_sharpe"] = _round(_matched(prep, members))
    out["method"] = "within-depth matched mean difference"
    return out


def _matched(prep: _Prepared, members: list[int]) -> float | None:
    """Within-depth mean(with) − mean(without), weighted by the smaller side."""
    with_by_depth: dict[int, list[float]] = {}
    column = prep.values["daily_sharpe"]
    for i in members:
        value = column[i]
        if value is None:
            continue
        slot = with_by_depth.setdefault(prep.depth[i], [0.0, 0])
        slot[0] += value
        slot[1] += 1
    numerator = 0.0
    denominator = 0.0
    for depth, (t_sum, t_cnt) in prep.depth_totals.items():
        w_sum, w_cnt = with_by_depth.get(depth, (0.0, 0))
        o_cnt = t_cnt - w_cnt
        if not w_cnt or not o_cnt:
            continue
        weight = min(w_cnt, o_cnt)
        numerator += weight * (w_sum / w_cnt - (t_sum - w_sum) / o_cnt)
        denominator += weight
    if denominator == 0:
        return None
    return numerator / denominator


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


def _slim_prefilter(row: dict) -> bool:
    """Every is_pre_holdout check that the in-memory row can answer without its windows."""
    if not isinstance(row, dict) or row.get("partial") or is_ops_park_record(row):
        return False
    if row.get("gate_rules") != GATE_RULES:
        return False
    counted = row.get("regimes_tested")
    if isinstance(counted, int) and not isinstance(counted, bool) and counted != DISCOVERY_N_WINDOWS:
        return False
    length = row.get("_windows_len")
    if isinstance(length, int) and length and length != DISCOVERY_N_WINDOWS:
        return False
    return any(_number(row.get(key)) is not None for key in _METRIC)


def _stored_rows():
    from hedge_fund.trading.discovery_results import iter_public_rows

    return iter_public_rows(_slim_prefilter)


def build_block_report(
    rows=None,
    pairs: list[dict] | None = None,
) -> dict:
    """Marginal effects. Pass rows to test; the default streams stored results.

    Only the fields the report reads are kept per row, so a full window
    payload is alive for one row at a time.
    """
    if rows is None:
        rows = _stored_rows()
    if pairs is None:
        pairs = load_pair_links()
    prepared: list[dict] = []
    family_rows: dict[str, list[int]] = defaultdict(list)
    bucket_rows: dict[str, list[int]] = defaultdict(list)
    by_name: dict[str, dict] = {}
    for row in rows:
        if not is_pre_holdout(row):
            continue
        name = row.get("strategy")
        if not isinstance(name, str) or not name:
            continue
        families, buckets = _blocks(name)
        item = {key: row.get(key) for key in _FIELDS}
        item["strategy"] = name
        item["qualified"] = bool(row.get("qualified"))
        item["depth"] = len(_atoms(name))
        index = len(prepared)
        prepared.append(item)
        by_name[name] = item
        for family in families:
            family_rows[family].append(index)
        for bucket in buckets:
            bucket_rows[bucket].append(index)
    prep = _Prepared(prepared)
    families_out = [_effect(prep, members, block, "family") for block, members in family_rows.items()]
    buckets_out = [_effect(prep, members, block, "bucket") for block, members in bucket_rows.items()]
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


# The report moves slowly and costs a full pass over every stored result,
# so nothing rebuilds it more often than this, however often ingest runs.
MIN_REBUILD_SECONDS = 600.0
# A published file older than this means the cycle stopped publishing;
# web then falls back to its own (rate-limited) background build.
PUBLISHED_MAX_AGE_SECONDS = 3 * 3600.0
PUBLISHED_FILE = "discovery_blocks.json"
# Right after a deploy the cycle publishes within a cycle or two; web does
# not start its own build (and pull scipy into its RSS) before then.
WEB_FALLBACK_GRACE_SECONDS = 900.0
_PUBLISH_LOCK_FILE = "discovery_blocks.lock"


def published_path():
    from hedge_fund.paths import state_root

    return state_root() / PUBLISHED_FILE


def _inputs_token() -> tuple:
    from hedge_fund.trading.discovery_results import discovery_results_path

    return (state_root_token(), _stat(discovery_results_path()), _stat(pairs_file()))


def publish_block_report(*, min_interval: float = MIN_REBUILD_SECONDS, force: bool = False) -> str:
    """Build and atomically write ``discovery_blocks.json`` for web to serve.

    Runs off the web process (the cycle sidecar spawns it). A non-blocking
    file lock keeps a second publisher from piling up behind a slow one.
    Returns ``published``, ``fresh``, ``unchanged`` or ``busy``.
    """
    import json
    import os
    import time

    out = published_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    lock_path = out.with_name(_PUBLISH_LOCK_FILE)
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        fcntl = None
    with open(lock_path, "a+") as lock:
        os.utime(lock_path)
        if fcntl is not None:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return "busy"
        token = [list(part) if isinstance(part, tuple) else part for part in _inputs_token()]
        previous = _read_published(out)
        if previous is not None and not force:
            age = time.time() - float(previous.get("built_at_unix") or 0)
            if age < min_interval:
                return "fresh"
            if previous.get("inputs_token") == token:
                os.utime(out)
                return "unchanged"
        started = time.process_time()
        report = build_block_report()
        report["built_at_unix"] = time.time()
        report["build_cpu_seconds"] = round(time.process_time() - started, 3)
        report["inputs_token"] = token
        report["source"] = "cycle"
        tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(report, separators=(",", ":")))
        os.replace(tmp, out)
        return "published"


def _read_published(path) -> dict | None:
    import json

    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


_published: dict = {}


def _published_report() -> dict | None:
    """Parsed published file, re-read only when its stat changes."""
    import time

    path = published_path()
    token = _stat(path)
    if token[1] == 0:
        return None
    if time.time() - token[1] / 1e9 > PUBLISHED_MAX_AGE_SECONDS:
        return None
    if _published.get("token") != token:
        data = _read_published(path)
        if data is None:
            return None
        _published["token"] = token
        _published["report"] = data
    return _published["report"]


def _web_cache():
    from hedge_fund.web.bg_cache import BackgroundCache

    global _WEB_CACHE
    if _WEB_CACHE is None:
        _WEB_CACHE = BackgroundCache("block-report", min_interval=MIN_REBUILD_SECONDS, max_keys=2)
    return _WEB_CACHE


_WEB_CACHE = None


def block_report_response() -> tuple[dict, int]:
    """Body and HTTP status for GET /api/discovery/blocks. Never builds on the request thread.

    Prefers the file the cycle publishes. Without one, a background build
    in this process runs at most once per ``MIN_REBUILD_SECONDS``; until
    it lands the answer is 202 ``computing``.
    """
    import time

    from hedge_fund.web.bg_cache import COMPUTING, computing_body

    published = _published_report()
    if published is not None:
        body = dict(published)
        body.pop("inputs_token", None)
        built = float(body.get("built_at_unix") or 0) or None
        body["status"] = "ready"
        body["age_seconds"] = round(time.time() - built, 1) if built else None
        return body, 200
    if not published_path().exists() and (_publisher_active() or _in_startup_grace()):
        return computing_body("The block report", retry_after=60, source="cycle"), 202
    read = _web_cache().get(state_root_token(), build_block_report, token=_inputs_token)
    if read.status == COMPUTING:
        return computing_body("The block report", retry_after=30, error=read.error), 202
    body = dict(read.value)
    body["status"] = read.status
    body["source"] = "web"
    body["age_seconds"] = round(read.age_seconds or 0.0, 1)
    return body, 200


_IMPORTED_AT = None


def _in_startup_grace() -> bool:
    import time

    global _IMPORTED_AT
    if _IMPORTED_AT is None:
        _IMPORTED_AT = time.monotonic()
    return time.monotonic() - _IMPORTED_AT < WEB_FALLBACK_GRACE_SECONDS


def _publisher_active() -> bool:
    """The cycle touched the publish lock recently, so its file is on the way."""
    import time

    mtime = _stat(published_path().with_name(_PUBLISH_LOCK_FILE))[1]
    return bool(mtime) and time.time() - mtime / 1e9 < 2 * MIN_REBUILD_SECONDS


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
    _published.clear()
    if _WEB_CACHE is not None:
        _WEB_CACHE.clear()
