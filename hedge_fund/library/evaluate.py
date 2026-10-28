"""Gate v6 evaluation of one library strategy (paper only, BTC only).

Same honesty as gate v5, measured on completed UTC daily bars with the
``signal_exit`` engine:

* Discovery: the 18 sealed 87-day OOS segments that end before the holdout
  (``DISCOVERY_N_WINDOWS``), scored as one continuous book from the first
  segment's first open to the last segment's last close. A position is not
  closed and reopened at a segment boundary; segments slice the daily
  equity curve. Indicators use every completed day before each bar (from
  the canonical tape start, 2021-01-26), which the 200-day and 12-month
  rules need; nothing after a bar is used.
* Sealed holdout ``[2025-10-01, 2026-10-01)``: a separate fresh book over
  that year only. Discovery numbers are computed from daily bars cut
  strictly before the holdout, so they cannot read it.
* Floors are the existing constants: trades >= ``MIN_BACKTEST_TRADES``,
  daily-equity Sharpe >= ``MIN_BACKTEST_SHARPE``, OOS P&L above
  ``sma_stack`` (the same rule on daily bars, same engine), holdout Sharpe,
  holdout trades >= ``HOLDOUT_MIN_TRADES`` and holdout P&L above
  ``sma_stack``, DSR >= ``DSR_MIN_PROBABILITY`` with N = every grid point
  in the library, CSCV PBO <= ``PBO_MAX`` on the strategy and its grid
  neighbours, >= ``NEIGHBOR_PASS_MIN_FRACTION`` of neighbours at the Sharpe
  and trade floors, and the same Sharpe floor at 2x fees + 5 bps.
* Buy-and-hold (the same book, always long) is informational.

Every check is computed for every strategy and every failing check is
listed, so a strategy that fails only on a trade-count floor is visible.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
from scipy.stats import norm

from hedge_fund.library.catalog import (
    LibraryStrategy,
    library_trial_count,
    neighbour_names,
    parse_variant,
    sma_stack_signal,
)
from hedge_fund.library.daily import DAY_MS, DailyBars, daily_from_rows, daily_from_tape
from hedge_fund.library.engine import (
    ENGINE_ID,
    REASON_END,
    REASON_SIGNAL,
    REASON_STOP,
    RISK_POLICY,
    TIMEFRAME,
    Costs,
    RunResult,
    run,
)
from hedge_fund.trading.buy_and_hold import daily_returns_sharpe
from hedge_fund.trading.constants import (
    BH_METRIC_ROLE,
    COST_STRESS_EXTRA_SLIPPAGE,
    COST_STRESS_FEE_MULTIPLE,
    DISCOVERY_N_WINDOWS,
    DSR_MIN_PROBABILITY,
    GATE_RULES,
    HOLDOUT_END_MS,
    HOLDOUT_MIN_TRADES,
    HOLDOUT_START_MS,
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    NEIGHBOR_PASS_MIN_FRACTION,
    PAPER_START_CASH,
    PBO_MAX,
    QUAL_OOS_START_MS,
    QUAL_SEGMENT_MS,
)
from hedge_fund.trading.holdout import HoldoutLeak
from hedge_fund.trading.multiple_testing import (
    EULER_MASCHERONI,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    return_moments,
)
from hedge_fund.trading.qualify import oos_admission_score

BASE_COSTS = Costs()
STRESS_COSTS = Costs(
    taker_fee=BASE_COSTS.taker_fee * COST_STRESS_FEE_MULTIPLE,
    slippage=BASE_COSTS.slippage + COST_STRESS_EXTRA_SLIPPAGE,
)
SHARPE_KIND = "daily_equity_annualized_sqrt365"
# Floors that only count trades. A strategy whose every failing check is one
# of these (or a neighbour check that passes on Sharpe alone) is flagged.
COUNT_CHECKS = ("oos_trades", "holdout_trades")
CHECK_ORDER = (
    "windows",
    "oos_trades",
    "oos_sharpe",
    "beat_sma_stack",
    "holdout_sharpe",
    "holdout_trades",
    "holdout_beat_sma_stack",
    "dsr",
    "pbo",
    "neighbours",
    "cost_stress",
)


def discovery_segments() -> list[tuple[int, int]]:
    """``[start, end)`` ms of the 18 sealed discovery segments."""
    return [
        (QUAL_OOS_START_MS + k * QUAL_SEGMENT_MS, QUAL_OOS_START_MS + (k + 1) * QUAL_SEGMENT_MS)
        for k in range(DISCOVERY_N_WINDOWS)
    ]


@dataclass
class LibraryData:
    """Daily bars for one evaluation batch.

    ``discovery`` ends strictly before ``HOLDOUT_START_MS``. ``holdout`` runs
    through the last holdout day and is ``None`` when the tape does not
    cover the whole holdout year.
    """

    discovery: DailyBars
    holdout: DailyBars | None
    holdout_note: str | None = None
    cache: dict = field(default_factory=dict, repr=False, compare=False)

    def discovery_bounds(self) -> tuple[int, int]:
        hit = self.cache.get("bounds")
        if hit is None:
            hit = _discovery_bounds(self.discovery)
            self.cache["bounds"] = hit
        return hit


def build_library_data(full: DailyBars) -> LibraryData:
    discovery = full.before(HOLDOUT_START_MS)
    if len(discovery) and int(discovery.ts[-1]) >= HOLDOUT_START_MS:  # pragma: no cover
        raise HoldoutLeak("discovery daily bars reach the holdout")
    holdout = None
    note = None
    last_holdout_day = HOLDOUT_END_MS - DAY_MS
    if len(full) and int(full.ts[-1]) >= last_holdout_day:
        holdout = full.before(HOLDOUT_END_MS)
    else:
        note = "holdout tape incomplete (needs every day through 2026-09-30)"
    return LibraryData(discovery=discovery, holdout=holdout, holdout_note=note)


def library_data_from_rows(rows) -> LibraryData:
    return build_library_data(daily_from_rows(rows))


def library_data_from_tape(arr) -> LibraryData:
    return build_library_data(daily_from_tape(arr))


def _discovery_bounds(bars: DailyBars) -> tuple[int, int]:
    segments = discovery_segments()
    first, last_end = segments[0][0], segments[-1][1]
    if len(bars) == 0 or int(bars.ts[0]) > first or int(bars.ts[-1]) < last_end - DAY_MS:
        raise ValueError("daily tape does not cover the 18 discovery segments")
    if int(bars.ts[-1]) >= HOLDOUT_START_MS:
        raise HoldoutLeak("discovery daily bars reach the holdout")
    return bars.index_of(first), bars.index_of(last_end)


@dataclass
class SpanMetrics:
    result: RunResult
    returns: np.ndarray
    daily_sharpe: float
    trades: int
    pnl: float
    window_pnl: list[float]
    window_trades: list[int]
    window_bounds: list[tuple[int, int]]
    max_dd: float
    total_return: float
    exposure: float
    avg_hold_days: float | None
    win_rate: float
    exits: dict

    def summary(self) -> dict:
        return {
            "daily_sharpe": round(self.daily_sharpe, 4),
            "trades": int(self.trades),
            "pnl": round(self.pnl, 2),
            "max_drawdown": round(self.max_dd, 4),
            "total_return": round(self.total_return, 4),
            "exposure": round(self.exposure, 4),
            "avg_hold_days": None if self.avg_hold_days is None else round(self.avg_hold_days, 2),
            "win_rate": round(self.win_rate, 4),
            "days": int(self.returns.shape[0]),
            "fees": round(float(self.result.fees), 2),
            "exits": dict(self.exits),
        }


def _max_drawdown(points: np.ndarray) -> float:
    if points.shape[0] < 2:
        return 0.0
    peak = np.maximum.accumulate(points)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd = np.where(peak > 0, (peak - points) / peak, 0.0)
    return float(np.max(dd))


def span_metrics(
    bars: DailyBars,
    signal: np.ndarray,
    start: int,
    end: int,
    segments: list[tuple[int, int]],
    *,
    costs: Costs = BASE_COSTS,
    stop_frac: float = 0.0,
) -> SpanMetrics:
    """One continuous book over ``[start, end)``; per-segment P&L on a 10k base."""
    res = run(bars, signal, start=start, end=end, costs=costs, stop_frac=stop_frac)
    rets = res.returns()
    eq = res.equity
    window_pnl: list[float] = []
    window_trades: list[int] = []
    bounds: list[tuple[int, int]] = []
    for seg_start, seg_end in segments:
        a = max(start, bars.index_of(seg_start)) - start
        b = min(end, bars.index_of(seg_end)) - start
        bounds.append((a, b))
        base = float(eq[a]) if eq.shape[0] > a else 0.0
        top = float(eq[b]) if eq.shape[0] > b else base
        window_pnl.append(PAPER_START_CASH * (top / base - 1.0) if base > 0 else 0.0)
        window_trades.append(
            sum(1 for t in res.trades if seg_start <= int(t.exit_ts) < seg_end)
        )
    trades = len(res.trades)
    wins = sum(1 for t in res.trades if t.pnl > 0)
    exits: dict[str, int] = {}
    for t in res.trades:
        exits[t.reason] = exits.get(t.reason, 0) + 1
    holds = [t.days for t in res.trades]
    return SpanMetrics(
        result=res,
        returns=rets,
        daily_sharpe=float(daily_returns_sharpe(list(rets))),
        trades=trades,
        pnl=float(sum(window_pnl)),
        window_pnl=window_pnl,
        window_trades=window_trades,
        window_bounds=bounds,
        max_dd=_max_drawdown(eq),
        total_return=float(eq[-1] / eq[0] - 1.0) if eq.shape[0] and eq[0] > 0 else 0.0,
        exposure=float(res.days_long / res.n_days) if res.n_days else 0.0,
        avg_hold_days=float(np.mean(holds)) if holds else None,
        win_rate=float(wins / trades) if trades else 0.0,
        exits=exits,
    )


def discovery_metrics(
    data: LibraryData,
    strategy: LibraryStrategy,
    params: dict,
    *,
    costs: Costs = BASE_COSTS,
) -> SpanMetrics:
    a, b = data.discovery_bounds()
    signal = strategy.signal(data.discovery, params)
    return span_metrics(
        data.discovery, signal, a, b, discovery_segments(),
        costs=costs, stop_frac=strategy.stop_frac,
    )


def holdout_metrics(
    data: LibraryData,
    strategy: LibraryStrategy,
    params: dict,
    *,
    costs: Costs = BASE_COSTS,
) -> SpanMetrics | None:
    bars = data.holdout
    if bars is None:
        return None
    a = bars.index_of(HOLDOUT_START_MS)
    signal = strategy.signal(bars, params)
    return span_metrics(
        bars, signal, a, len(bars), [(HOLDOUT_START_MS, HOLDOUT_END_MS)],
        costs=costs, stop_frac=strategy.stop_frac,
    )


def _benchmark(data: LibraryData, which: str, span: str, costs: Costs = BASE_COSTS) -> SpanMetrics | None:
    key = ("bench", which, span, costs)
    if key in data.cache:
        return data.cache[key]
    bars = data.discovery if span == "discovery" else data.holdout
    if bars is None:
        data.cache[key] = None
        return None
    signal = np.ones(len(bars), dtype=bool) if which == "bh" else sma_stack_signal(bars)
    if span == "discovery":
        a, b = data.discovery_bounds()
        out = span_metrics(bars, signal, a, b, discovery_segments(), costs=costs)
    else:
        out = span_metrics(
            bars, signal, bars.index_of(HOLDOUT_START_MS), len(bars),
            [(HOLDOUT_START_MS, HOLDOUT_END_MS)], costs=costs,
        )
    data.cache[key] = out
    return out


def expected_max_multiplier(n_trials: int) -> float:
    """``E[max]`` of ``n_trials`` standard normals (Bailey & Lopez de Prado 2014)."""
    if n_trials <= 1:
        return 0.0
    z1 = float(norm.ppf(1.0 - 1.0 / n_trials))
    z2 = float(norm.ppf(1.0 - 1.0 / (n_trials * math.e)))
    return (1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2


def dsr_hurdles(moments: dict | None, n_trials: int, confidence: float = DSR_MIN_PROBABILITY) -> dict:
    """What DSR asks of this return series.

    With the estimator variance V = 1 - skew*SR + (kurt - 1)/4 * SR^2, DSR >=
    ``confidence`` holds exactly when SR * sqrt(n - 1) / sqrt(V) >= E[max] + z.
    That gives the per-day Sharpe needed at this sample length and the
    number of days needed at the observed Sharpe.
    """
    if not moments:
        return {"required_annual_sharpe": None, "days_needed": None, "psr_days_needed": None}
    z = float(norm.ppf(confidence))
    e_max = expected_max_multiplier(int(n_trials))
    sr = float(moments["sharpe"])
    n = int(moments["n"])
    skew = float(moments["skew"])
    kurt = float(moments["kurtosis"])

    def variance(s: float) -> float:
        return max(1e-12, 1.0 - skew * s + (kurt - 1.0) / 4.0 * s * s)

    req = (e_max + z) * math.sqrt(1.0 / max(1, n - 1))
    for _ in range(20):
        req = (e_max + z) * math.sqrt(variance(req) / max(1, n - 1))
    days = None
    psr_days = None
    if sr > 0:
        days = int(math.ceil(1.0 + variance(sr) * ((e_max + z) / sr) ** 2))
        psr_days = int(math.ceil(1.0 + variance(sr) * (z / sr) ** 2))
    return {
        "required_annual_sharpe": round(req * math.sqrt(365.0), 3),
        "days_needed": days,
        "psr_days_needed": psr_days,
        "expected_max_multiplier": round(e_max, 4),
    }


def count_only_failure(checks: dict) -> bool:
    """True when something fails and every failure is a trade-count floor.

    The neighbour check counts as a trade-count failure only when the
    neighbours would pass on the Sharpe floor alone.
    """
    failing = [key for key in CHECK_ORDER if not checks.get(key, {}).get("pass")]
    return bool(failing) and all(
        key in COUNT_CHECKS
        or (key == "neighbours" and bool(checks.get("neighbours", {}).get("sharpe_only_pass")))
        for key in failing
    )


def _iso_day(ts: int) -> str:
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _check(passed: bool, value, threshold, kind: str, **extra) -> dict:
    out = {"pass": bool(passed), "value": value, "threshold": threshold, "kind": kind}
    out.update(extra)
    return out


def _r(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def evaluate_library_record(
    name: str,
    data: LibraryData,
    *,
    n_trials: int | None = None,
    tested_at: str | None = None,
) -> dict:
    """Every gate-v6 check for one library variant, as a discovery record."""
    strategy, params = parse_variant(name)
    trials = library_trial_count() if n_trials is None else int(n_trials)
    disc = discovery_metrics(data, strategy, params)
    bh = _benchmark(data, "bh", "discovery")
    sma = _benchmark(data, "sma_stack", "discovery")
    hold = holdout_metrics(data, strategy, params)
    hold_bh = _benchmark(data, "bh", "holdout")
    hold_sma = _benchmark(data, "sma_stack", "holdout")
    stressed = discovery_metrics(data, strategy, params, costs=STRESS_COSTS)
    stressed_bh = _benchmark(data, "bh", "discovery", STRESS_COSTS)

    neighbours = []
    for nb in neighbour_names(name):
        _s, nb_params = parse_variant(nb)
        m = discovery_metrics(data, strategy, nb_params)
        sharpe_ok = m.daily_sharpe >= MIN_BACKTEST_SHARPE
        neighbours.append({
            "name": nb,
            "params": nb_params,
            "daily_sharpe": round(m.daily_sharpe, 4),
            "trades": m.trades,
            "pnl": round(m.pnl, 2),
            "pass": bool(sharpe_ok and m.trades >= MIN_BACKTEST_TRADES),
            "sharpe_only_pass": bool(sharpe_ok),
            "_windows": m.window_pnl,
        })

    reasons: list[str] = []
    checks: dict[str, dict] = {}
    n_windows = len(disc.window_pnl)
    checks["windows"] = _check(n_windows == DISCOVERY_N_WINDOWS, n_windows, DISCOVERY_N_WINDOWS, "structure")
    if n_windows != DISCOVERY_N_WINDOWS:
        reasons.append(f"windows {n_windows} != expected {DISCOVERY_N_WINDOWS}")
    checks["oos_trades"] = _check(disc.trades >= MIN_BACKTEST_TRADES, disc.trades, MIN_BACKTEST_TRADES, "count")
    if disc.trades < MIN_BACKTEST_TRADES:
        reasons.append(f"oos_trades {disc.trades} < {MIN_BACKTEST_TRADES}")
    checks["oos_sharpe"] = _check(
        disc.daily_sharpe >= MIN_BACKTEST_SHARPE, _r(disc.daily_sharpe), MIN_BACKTEST_SHARPE, "stat",
    )
    if disc.daily_sharpe < MIN_BACKTEST_SHARPE:
        reasons.append(f"oos_sharpe {disc.daily_sharpe:.2f} < {MIN_BACKTEST_SHARPE} (daily-equity)")
    sma_pnl = None if sma is None else sma.pnl
    beat = sma_pnl is not None and disc.pnl > sma_pnl
    checks["beat_sma_stack"] = _check(beat, _r(disc.pnl, 2), _r(sma_pnl, 2), "baseline")
    if sma_pnl is None:
        reasons.append("sma_stack missing")
    elif not beat:
        reasons.append(f"oos_pnl {disc.pnl:.2f} <= sma_stack {sma_pnl:.2f}")

    if hold is None:
        note = data.holdout_note or "holdout tape missing"
        for key in ("holdout_sharpe", "holdout_trades", "holdout_beat_sma_stack"):
            checks[key] = _check(False, None, None, "holdout", note=note)
        reasons.append(note)
    else:
        h_sma = None if hold_sma is None else hold_sma.pnl
        checks["holdout_sharpe"] = _check(
            hold.daily_sharpe >= MIN_BACKTEST_SHARPE, _r(hold.daily_sharpe), MIN_BACKTEST_SHARPE, "holdout",
        )
        checks["holdout_trades"] = _check(hold.trades >= HOLDOUT_MIN_TRADES, hold.trades, HOLDOUT_MIN_TRADES, "count")
        h_beat = h_sma is not None and hold.pnl > h_sma
        checks["holdout_beat_sma_stack"] = _check(h_beat, _r(hold.pnl, 2), _r(h_sma, 2), "holdout")
        if hold.daily_sharpe < MIN_BACKTEST_SHARPE:
            reasons.append(f"holdout_sharpe {hold.daily_sharpe:.2f} < {MIN_BACKTEST_SHARPE} (daily-equity)")
        if hold.trades < HOLDOUT_MIN_TRADES:
            reasons.append(f"holdout_trades {hold.trades} < {HOLDOUT_MIN_TRADES}")
        if h_sma is None:
            reasons.append("holdout sma_stack missing")
        elif not h_beat:
            reasons.append(f"holdout_pnl {hold.pnl:.2f} <= sma_stack {h_sma:.2f}")

    moments = return_moments(list(disc.returns))
    dsr_value = None
    if moments is None:
        dsr_ok = False
        reasons.append("dsr daily returns missing")
    else:
        dsr_value = deflated_sharpe_ratio(
            sharpe=float(moments["sharpe"]),
            n_obs=int(moments["n"]),
            n_trials=trials,
            skew=float(moments["skew"]),
            kurtosis=float(moments["kurtosis"]),
        )
        dsr_ok = dsr_value >= DSR_MIN_PROBABILITY
        if not dsr_ok:
            reasons.append(f"dsr {dsr_value:.3f} < {DSR_MIN_PROBABILITY} (n_trials={trials})")
    hurdles = dsr_hurdles(moments, trials)
    checks["dsr"] = _check(dsr_ok, _r(dsr_value), DSR_MIN_PROBABILITY, "robust", n_trials=trials)

    matrix = [disc.window_pnl] + [nb["_windows"] for nb in neighbours]
    pbo = None
    if len(matrix) < 2:
        pbo_reason = "pbo undefined (fewer than 2 configurations)"
    else:
        pbo = probability_of_backtest_overfitting(np.asarray(matrix, dtype=np.float64).T)
        if pbo is None:
            pbo_reason = "pbo undefined"
        elif pbo > PBO_MAX:
            pbo_reason = f"pbo {pbo:.3f} > {PBO_MAX}"
        else:
            pbo_reason = None
    if pbo_reason:
        reasons.append(pbo_reason)
    checks["pbo"] = _check(pbo_reason is None, _r(pbo), PBO_MAX, "robust", configs=len(matrix))

    n_nb = len(neighbours)
    n_pass = sum(1 for nb in neighbours if nb["pass"])
    n_sharpe = sum(1 for nb in neighbours if nb["sharpe_only_pass"])
    frac = n_pass / n_nb if n_nb else 0.0
    nb_ok = n_nb > 0 and frac + 1e-12 >= NEIGHBOR_PASS_MIN_FRACTION
    sharpe_only_ok = n_nb > 0 and (n_sharpe / n_nb) + 1e-12 >= NEIGHBOR_PASS_MIN_FRACTION
    if n_nb == 0:
        reasons.append("no parameter neighbours (lone spike)")
    elif not nb_ok:
        reasons.append(f"neighbours {n_pass}/{n_nb} = {frac:.0%} < {NEIGHBOR_PASS_MIN_FRACTION:.0%}")
    checks["neighbours"] = _check(
        nb_ok, f"{n_pass}/{n_nb}", NEIGHBOR_PASS_MIN_FRACTION, "robust",
        n_pass=n_pass, total=n_nb, sharpe_only_pass=sharpe_only_ok, sharpe_only=f"{n_sharpe}/{n_nb}",
    )

    cost_ok = stressed.daily_sharpe >= MIN_BACKTEST_SHARPE
    if not cost_ok:
        reasons.append(
            f"cost stress sharpe {stressed.daily_sharpe:.2f} < {MIN_BACKTEST_SHARPE} "
            f"(2x fee + {COST_STRESS_EXTRA_SLIPPAGE * 1e4:.0f}bps)"
        )
    checks["cost_stress"] = _check(cost_ok, _r(stressed.daily_sharpe), MIN_BACKTEST_SHARPE, "robust")

    failing = [key for key in CHECK_ORDER if not checks[key]["pass"]]
    count_only = count_only_failure(checks)
    passed = not failing
    for nb in neighbours:
        nb.pop("_windows", None)

    segments = discovery_segments()
    windows = [
        {
            "start": _iso_day(seg_start),
            "end": _iso_day(seg_end - DAY_MS),
            "pnl": round(pnl, 2),
            "trades": trades,
        }
        for (seg_start, seg_end), pnl, trades in zip(segments, disc.window_pnl, disc.window_trades)
    ]
    holdout_block = {"pass": all(checks[k]["pass"] for k in ("holdout_sharpe", "holdout_trades", "holdout_beat_sma_stack"))}
    if hold is None:
        holdout_block["reason"] = data.holdout_note or "holdout tape missing"
    else:
        holdout_block.update({
            "sharpe": round(hold.daily_sharpe, 4),
            "daily_sharpe": round(hold.daily_sharpe, 4),
            "trades": int(hold.trades),
            "pnl": round(hold.pnl, 2),
            "max_drawdown": round(hold.max_dd, 4),
            "bh_daily_sharpe": None if hold_bh is None else round(hold_bh.daily_sharpe, 4),
            "bh_pnl": None if hold_bh is None else round(hold_bh.pnl, 2),
            "bh_role": BH_METRIC_ROLE,
            "sma_pnl": None if hold_sma is None else round(hold_sma.pnl, 2),
        })
    gate_block = {
        "passed": passed,
        "rules": GATE_RULES,
        "engine": ENGINE_ID,
        "holdout_pass": holdout_block["pass"],
        "dsr_pass": checks["dsr"]["pass"],
        "pbo_pass": checks["pbo"]["pass"],
        "perturb_pass": checks["neighbours"]["pass"],
        "cost_pass": checks["cost_stress"]["pass"],
        "holdout": {k: v for k, v in holdout_block.items() if k != "pass"},
        "dsr": _r(dsr_value, 6),
        "n_trials": trials,
        "pbo": _r(pbo, 6),
        "neighbours": n_nb,
        "neighbours_pass": n_pass,
        "cost_sharpe": round(stressed.daily_sharpe, 4),
        "cost_daily_sharpe": round(stressed.daily_sharpe, 4),
        "cost_bh_daily_sharpe": None if stressed_bh is None else round(stressed_bh.daily_sharpe, 4),
        "cost_bh_role": BH_METRIC_ROLE,
        "backtest_max_dd": round(disc.max_dd, 4),
        "oos_sharpe_per_obs": None if moments is None else round(float(moments["sharpe"]), 6),
        "oos_return_n": None if moments is None else int(moments["n"]),
    }
    record = {
        "strategy": name,
        "tested_at": tested_at or datetime.now(timezone.utc).isoformat(),
        "timeframe": TIMEFRAME,
        "risk_policy": RISK_POLICY,
        "engine": ENGINE_ID,
        "train_pnl": None,
        "test_pnl": round(disc.pnl, 2),
        "sharpe": round(disc.daily_sharpe, 3),
        "daily_sharpe": round(disc.daily_sharpe, 3),
        "sharpe_kind": SHARPE_KIND,
        "win_rate_pct": round(disc.win_rate * 100.0, 1),
        "trades": int(disc.trades),
        "regimes_tested": n_windows,
        "qualified": passed,
        "bh_oos_pnl": None if bh is None else round(bh.pnl, 2),
        "sma_stack_oos_pnl": None if sma_pnl is None else round(sma_pnl, 2),
        "bh_daily_sharpe": None if bh is None else round(bh.daily_sharpe, 3),
        "bh_daily_sharpe_role": BH_METRIC_ROLE,
        "bh_oos_pnl_role": BH_METRIC_ROLE,
        "avg_hold_hours": None if disc.avg_hold_days is None else round(disc.avg_hold_days * 24.0, 2),
        "gate_rules": GATE_RULES,
        "fail_reasons": reasons,
        "all_windows_nonneg": all(t >= 1 and p >= 0 for p, t in zip(disc.window_pnl, disc.window_trades)),
        "backtest_max_dd": round(disc.max_dd, 4),
        "symbols": ["BTC/USDT"],
        "gate_v4": gate_block,
        "library": {
            "key": strategy.key,
            "title": strategy.title,
            "family": strategy.family,
            "evaluation": strategy.evaluation,
            "params": params,
            "canonical": params == strategy.center,
            "safety_stop_pct": round(strategy.stop_frac * 100.0, 2) if strategy.stop_frac else None,
            "citation": strategy.citation.short(),
            "peer_reviewed": strategy.citation.peer_reviewed,
            "n_trials": trials,
            "sharpe_kind": SHARPE_KIND,
            "checks": checks,
            "failing_checks": failing,
            "count_only": count_only,
            "discovery": disc.summary(),
            "windows": windows,
            "bh": None if bh is None else bh.summary(),
            "sma_stack": None if sma is None else sma.summary(),
            "holdout": None if hold is None else {
                **hold.summary(),
                "bh": None if hold_bh is None else hold_bh.summary(),
                "sma_stack": None if hold_sma is None else hold_sma.summary(),
            },
            "cost_stress": {
                **stressed.summary(),
                "taker_fee": STRESS_COSTS.taker_fee,
                "slippage": STRESS_COSTS.slippage,
                "bh_daily_sharpe": None if stressed_bh is None else round(stressed_bh.daily_sharpe, 4),
            },
            "neighbours": neighbours,
            "dsr": {
                "value": _r(dsr_value, 6),
                "n_trials": trials,
                "n_obs": None if moments is None else int(moments["n"]),
                "sharpe_per_obs": None if moments is None else round(float(moments["sharpe"]), 6),
                "skew": None if moments is None else round(float(moments["skew"]), 4),
                "kurtosis": None if moments is None else round(float(moments["kurtosis"]), 4),
                **hurdles,
            },
            "pbo": {"value": _r(pbo, 6), "configs": len(matrix)},
            "exit_reasons": {
                "signal": disc.exits.get(REASON_SIGNAL, 0),
                "safety_stop": disc.exits.get(REASON_STOP, 0),
                "window_end": disc.exits.get(REASON_END, 0),
            },
        },
    }
    if passed:
        record["score"] = round(oos_admission_score(disc.pnl, disc.daily_sharpe), 2)
    return record
