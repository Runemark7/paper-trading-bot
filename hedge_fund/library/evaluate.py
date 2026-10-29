"""Gate v7 evaluation of one library strategy (paper only, BTC only).

Measured on completed UTC daily bars with the ``signal_exit`` engine.
Alexander, 2026-10-29 10:35 CET: gate on profit after fees and costs in
the test years and the final year. DSR and PBO are warnings, not vetoes.
The success criterion is profitable after fees, not beating buy-and-hold.
Alexander, 2026-10-29 11:01 CET: the neighbour rule is a warning too.

* History starts at the Binance BTCUSDT listing (``LIBRARY_TAPE_START_MS``,
  2017-08-17), shared with the live cycle. Indicators are computed on that
  full prefix. Scoring starts only once every grid point, including the
  longest neighbour, is finite on the previous close and stays finite.
  A day that is not warm fails the evaluation; it is never a flat signal.
  A tape that does not start in 2017 is refused, not trimmed into a result.
* Discovery is one continuous book from that shared open (2018-12-01 on
  the warmed tape) through the day before the holdout (2025-09-30). PBO
  slices it into blocks of about ``LIBRARY_BLOCK_TARGET_DAYS`` (390) and
  is a warning. A position is not closed and reopened at a block boundary.
  Nothing after a bar is used, and discovery bars are cut strictly before
  the holdout.
* Sealed holdout ``[2025-10-01, 2026-10-01)``: a separate fresh book. Its
  indicators still see the prefix. A fresh book whose signal is already on
  buys at the first scored open.
* Hard vetoes: out-of-sample net P&L > 0, holdout net P&L > 0, both spans
  still net P&L > 0 at 2× fees + 5 bps, at least 8 out-of-sample
  round-trips, and at least 2 holdout trades. Net P&L is ending equity
  minus starting cash after fees and slippage.
* Warnings, stored and shown, never a veto: DSR with N = every grid
  point, PBO, the Sharpe floors, parameter neighbours (at least 70% must
  clear the three profit checks; the fraction is still computed),
  out-of-sample and holdout versus ``sma_stack``, and the buy-and-hold
  comparison. The 5m path is separate and still vetoes on those, including
  neighbours.
* The 20% stop is on for every library strategy and is not a second trial.

Every check is computed for every strategy. A veto is a fail reason. A
warning is listed beside a pass.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
from scipy.stats import norm

from hedge_fund.library.catalog import (
    LibraryStrategy,
    library_score_index,
    library_trial_count,
    neighbour_names,
    parse_variant,
    signal_ready,
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
    DSR_MIN_PROBABILITY,
    GATE_RULES,
    HOLDOUT_END_MS,
    HOLDOUT_START_MS,
    LIBRARY_BLOCK_TARGET_DAYS,
    LIBRARY_HOLDOUT_MIN_TRADES,
    LIBRARY_MIN_OOS_TRADES,
    LIBRARY_TAPE_START_MS,
    LIBRARY_TAPE_START_SLACK_MS,
    MIN_BACKTEST_SHARPE,
    NEIGHBOR_PASS_MIN_FRACTION,
    PAPER_START_CASH,
    PBO_MAX,
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
# Floors that only count trades. A strategy whose every veto is one of
# these is flagged. The P&L checks are not counts. Neighbours are a warning.
COUNT_CHECKS = ("oos_trades", "holdout_trades")
# Hard vetoes for lib.*. Order is the order fail reasons are listed.
VETO_CHECKS = (
    "windows",
    "oos_trades",
    "oos_pnl",
    "holdout_pnl",
    "holdout_trades",
    "cost_stress",
)
# Computed, stored, and shown. A miss here never rejects the name.
WARNING_CHECKS = (
    "oos_sharpe",
    "beat_sma_stack",
    "holdout_sharpe",
    "holdout_beat_sma_stack",
    "dsr",
    "pbo",
    "neighbours",
    "buy_and_hold",
)
CHECK_ORDER = VETO_CHECKS + WARNING_CHECKS
_LIBRARY_ATTESTATION_KEYS = (
    "oos_pnl_pass",
    "trades_pass",
    "holdout_pass",
    "cost_pass",
)


def assert_library_history(bars: DailyBars) -> None:
    """Refuse a tape that does not reach the Binance BTCUSDT listing.

    A 2021-only tape leaves the 200-day and 12-month rules undefined on the
    first scored year. Those NaNs used to count as flat. That is not a result.
    """
    if len(bars) == 0 or int(bars.ts[0]) > LIBRARY_TAPE_START_MS + LIBRARY_TAPE_START_SLACK_MS:
        raise ValueError(
            "BTC daily tape does not start at 2017-08-17; "
            "a short tape is not trimmed into a result"
        )


def tile_blocks(start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    """Equal ``[start, end)`` blocks covering ``[start_ms, end_ms)``.

    The count is ``round(span / LIBRARY_BLOCK_TARGET_DAYS)``, at least one.
    The last edge is ``end_ms`` exactly (the holdout, for discovery).
    """
    start_ms = int(start_ms)
    end_ms = int(end_ms)
    span = end_ms - start_ms
    if span <= 0:
        raise ValueError("library block span is empty")
    n = max(1, round(span / (LIBRARY_BLOCK_TARGET_DAYS * DAY_MS)))
    edges = [start_ms + (span * k) // n for k in range(n + 1)]
    edges[-1] = end_ms
    return [(edges[k], edges[k + 1]) for k in range(n)]


def _blocks_tile(data: "LibraryData") -> bool:
    segments = data.segments
    if not segments or data.score_index >= len(data.discovery):
        return False
    score_ts = int(data.discovery.ts[data.score_index])
    if segments[0][0] != score_ts or segments[-1][1] != HOLDOUT_START_MS:
        return False
    prev = score_ts
    for start, end in segments:
        if start != prev or end <= start:
            return False
        prev = end
    return prev == HOLDOUT_START_MS


def discovery_segments(data: "LibraryData") -> list[tuple[int, int]]:
    """``[start, end)`` ms of this batch's warmed PBO blocks.

    This is not the 5m 18×87-day grid.
    """
    return list(data.segments)


@dataclass
class LibraryData:
    """Daily bars for one evaluation batch.

    ``discovery`` includes the warm-up prefix and ends strictly before
    ``HOLDOUT_START_MS``. Scoring starts at ``score_index``. ``holdout``
    keeps that same prefix so indicators stay warm, and is ``None`` when
    the tape does not cover the whole holdout year.
    """

    discovery: DailyBars
    holdout: DailyBars | None
    score_index: int
    segments: list[tuple[int, int]]
    holdout_note: str | None = None
    cache: dict = field(default_factory=dict, repr=False, compare=False)

    def discovery_bounds(self) -> tuple[int, int]:
        return self.score_index, len(self.discovery)


def build_library_data(full: DailyBars) -> LibraryData:
    assert_library_history(full)
    discovery = full.before(HOLDOUT_START_MS)
    if len(discovery) and int(discovery.ts[-1]) >= HOLDOUT_START_MS:  # pragma: no cover
        raise HoldoutLeak("discovery daily bars reach the holdout")
    if len(discovery) == 0 or int(discovery.ts[-1]) < HOLDOUT_START_MS - DAY_MS:
        raise ValueError(
            "daily tape does not reach the day before the holdout; "
            "a missing lookback is not scored as flat"
        )
    score_index = library_score_index(discovery)
    segments = tile_blocks(int(discovery.ts[score_index]), HOLDOUT_START_MS)
    holdout = None
    note = None
    last_holdout_day = HOLDOUT_END_MS - DAY_MS
    if len(full) and int(full.ts[-1]) >= last_holdout_day:
        holdout = full.before(HOLDOUT_END_MS)
    else:
        note = "holdout tape incomplete (needs every day through 2026-09-30)"
    return LibraryData(
        discovery=discovery,
        holdout=holdout,
        score_index=score_index,
        segments=segments,
        holdout_note=note,
    )


def library_data_from_rows(rows) -> LibraryData:
    return build_library_data(daily_from_rows(rows))


def library_data_from_tape(arr) -> LibraryData:
    return build_library_data(daily_from_tape(arr))


def _require_ready(strategy: LibraryStrategy, bars: DailyBars, params: dict, start: int, end: int) -> None:
    """Every close the book acts on must be finite. A hole is a failure."""
    ready = signal_ready(strategy, bars, params)
    lo = int(start) - 1
    hi = int(end) - 1
    if lo < 0 or hi > len(ready) or hi <= lo or not bool(ready[lo:hi].all()):
        raise ValueError(
            f"{strategy.key} scored a day whose lookback is not warm; "
            "a missing lookback is not scored as flat"
        )


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
    _require_ready(strategy, data.discovery, params, a, b)
    signal = strategy.signal(data.discovery, params)
    return span_metrics(
        data.discovery, signal, a, b, data.segments,
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
    _require_ready(strategy, bars, params, a, len(bars))
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
        out = span_metrics(bars, signal, a, b, data.segments, costs=costs)
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


def book_pnl(metrics: SpanMetrics) -> float:
    """Net profit of one continuous book: ending equity minus starting cash.

    Fees and slippage are already out of the cash. This is the profit the
    gate uses. The sum of rebased block P&Ls is a different number and stays
    on ``SpanMetrics.pnl`` for the PBO blocks.
    """
    eq = metrics.result.equity
    if eq.size == 0:
        return 0.0
    return float(eq[-1] - eq[0])


def count_only_failure(checks: dict) -> bool:
    """True when a veto fails and every veto is a trade-count floor.

    Warnings are not vetoes, so a Sharpe or DSR miss does not change this.
    """
    failing = [key for key in VETO_CHECKS if not checks.get(key, {}).get("pass")]
    return bool(failing) and all(key in COUNT_CHECKS for key in failing)


def library_pass_attestation(**extra) -> dict:
    """Minimal ``gate_v4`` block a qualified library ingest row must carry.

    DSR, PBO, and the neighbour fraction are absent on purpose. A warning
    may be false on a pass.
    """
    block = {
        "passed": True,
        "rules": GATE_RULES,
        "oos_pnl_pass": True,
        "trades_pass": True,
        "holdout_pass": True,
        "cost_pass": True,
    }
    block.update(extra)
    return block


def library_attestation_ok(row: dict) -> bool:
    """True when a qualified library row carries the v7b veto attestation.

    ``dsr_pass``, ``pbo_pass``, and ``perturb_pass`` are not required. The
    5m path still uses ``v4_attestation_ok``, which does require them.
    """
    block = row.get("gate_v4") if isinstance(row, dict) else None
    if not isinstance(block, dict):
        return False
    if block.get("passed") is not True or block.get("rules") != GATE_RULES:
        return False
    return all(block.get(key) is True for key in _LIBRARY_ATTESTATION_KEYS)


def library_verdict(
    *,
    windows_ok: bool,
    n_windows: int,
    expected_windows: int,
    window_span: str,
    oos_pnl: float,
    oos_trades: int,
    oos_sharpe: float,
    sma_oos_pnl: float | None,
    bh_oos_pnl: float | None,
    holdout_present: bool,
    holdout_note: str | None,
    holdout_pnl: float | None,
    holdout_trades: int | None,
    holdout_sharpe: float | None,
    sma_holdout_pnl: float | None,
    bh_holdout_pnl: float | None,
    stress_oos_pnl: float | None,
    stress_holdout_pnl: float | None,
    dsr_value: float | None,
    n_trials: int,
    pbo: float | None,
    pbo_undefined: str | None,
    neighbour_passes: list[bool],
) -> dict:
    """Split measured numbers into vetoes and warnings.

    ``qualified`` is true only when every veto passes. A warning is stored
    and does not change that.
    """
    reasons: list[str] = []
    warnings: list[str] = []
    checks: dict[str, dict] = {}
    bps = COST_STRESS_EXTRA_SLIPPAGE * 1e4

    def veto(key: str, passed: bool, value, threshold, kind: str, reason: str | None, **extra) -> None:
        checks[key] = _check(passed, value, threshold, kind, role="veto", **extra)
        if reason:
            reasons.append(reason)

    def warn(key: str, ok: bool, value, threshold, kind: str, text: str | None, **extra) -> None:
        checks[key] = _check(ok, value, threshold, kind, role="warning", **extra)
        if text:
            warnings.append(text)

    veto(
        "windows",
        windows_ok,
        n_windows,
        expected_windows,
        "structure",
        None if windows_ok else (
            f"windows {n_windows} do not tile the warmed span ({window_span})"
        ),
    )
    trades_ok = oos_trades >= LIBRARY_MIN_OOS_TRADES
    veto(
        "oos_trades",
        trades_ok,
        oos_trades,
        LIBRARY_MIN_OOS_TRADES,
        "count",
        None if trades_ok else f"oos_trades {oos_trades} < {LIBRARY_MIN_OOS_TRADES}",
    )
    oos_profit = oos_pnl > 0
    veto(
        "oos_pnl",
        oos_profit,
        _r(oos_pnl, 2),
        0,
        "profit",
        None if oos_profit else f"oos_pnl {oos_pnl:.2f} <= 0",
    )

    if not holdout_present:
        note = holdout_note or "holdout tape missing"
        for key, kind in (("holdout_pnl", "profit"), ("holdout_trades", "count")):
            checks[key] = _check(False, None, None, kind, role="veto", note=note)
        reasons.append(note)
        hold_trades_ok = False
        hold_profit = False
    else:
        hold_profit = holdout_pnl is not None and holdout_pnl > 0
        hold_trades_ok = holdout_trades is not None and holdout_trades >= LIBRARY_HOLDOUT_MIN_TRADES
        veto(
            "holdout_pnl",
            hold_profit,
            _r(holdout_pnl, 2),
            0,
            "profit",
            None if hold_profit else f"holdout_pnl {float(holdout_pnl or 0):.2f} <= 0",
        )
        veto(
            "holdout_trades",
            hold_trades_ok,
            holdout_trades,
            LIBRARY_HOLDOUT_MIN_TRADES,
            "count",
            None if hold_trades_ok else (
                f"holdout_trades {holdout_trades} < {LIBRARY_HOLDOUT_MIN_TRADES}"
            ),
        )

    stress_oos_ok = stress_oos_pnl is not None and stress_oos_pnl > 0
    stress_hold_ok = stress_holdout_pnl is not None and stress_holdout_pnl > 0
    cost_bits: list[str] = []
    if not stress_oos_ok:
        shown = "missing" if stress_oos_pnl is None else f"{stress_oos_pnl:.2f} <= 0"
        cost_bits.append(f"cost stress oos pnl {shown} (2x fee + {bps:.0f}bps)")
    if not stress_hold_ok:
        shown = "missing" if stress_holdout_pnl is None else f"{stress_holdout_pnl:.2f} <= 0"
        cost_bits.append(f"cost stress holdout pnl {shown} (2x fee + {bps:.0f}bps)")
    cost_ok = stress_oos_ok and stress_hold_ok
    cost_value = None if stress_oos_pnl is None else _r(stress_oos_pnl, 2)
    veto(
        "cost_stress",
        cost_ok,
        cost_value,
        0,
        "robust",
        None if cost_ok else "; ".join(cost_bits),
        holdout_pnl=None if stress_holdout_pnl is None else _r(stress_holdout_pnl, 2),
    )

    sharpe_ok = oos_sharpe >= MIN_BACKTEST_SHARPE
    warn(
        "oos_sharpe",
        sharpe_ok,
        _r(oos_sharpe),
        MIN_BACKTEST_SHARPE,
        "stat",
        None if sharpe_ok else f"oos_sharpe {oos_sharpe:.2f} < {MIN_BACKTEST_SHARPE} (daily-equity)",
    )
    if sma_oos_pnl is None:
        beat = False
        beat_text = "sma_stack missing"
    else:
        beat = oos_pnl > sma_oos_pnl
        beat_text = None if beat else f"oos_pnl {oos_pnl:.2f} <= sma_stack {sma_oos_pnl:.2f}"
    warn("beat_sma_stack", beat, _r(oos_pnl, 2), _r(sma_oos_pnl, 2), "baseline", beat_text)

    if not holdout_present:
        note = holdout_note or "holdout tape missing"
        checks["holdout_sharpe"] = _check(True, None, MIN_BACKTEST_SHARPE, "holdout", role="warning", note=note)
        checks["holdout_beat_sma_stack"] = _check(True, None, None, "holdout", role="warning", note=note)
    else:
        h_sharpe_ok = holdout_sharpe is not None and holdout_sharpe >= MIN_BACKTEST_SHARPE
        warn(
            "holdout_sharpe",
            h_sharpe_ok,
            _r(holdout_sharpe),
            MIN_BACKTEST_SHARPE,
            "holdout",
            None if h_sharpe_ok else (
                f"holdout_sharpe {float(holdout_sharpe or 0):.2f} < {MIN_BACKTEST_SHARPE} (daily-equity)"
            ),
        )
        if sma_holdout_pnl is None:
            h_beat = False
            h_text = "holdout sma_stack missing"
        else:
            h_beat = holdout_pnl is not None and holdout_pnl > sma_holdout_pnl
            h_text = None if h_beat else (
                f"holdout_pnl {float(holdout_pnl or 0):.2f} <= sma_stack {sma_holdout_pnl:.2f}"
            )
        warn(
            "holdout_beat_sma_stack",
            h_beat,
            _r(holdout_pnl, 2),
            _r(sma_holdout_pnl, 2),
            "holdout",
            h_text,
        )

    if dsr_value is None:
        dsr_ok = False
        dsr_text = "dsr daily returns missing"
    else:
        dsr_ok = dsr_value >= DSR_MIN_PROBABILITY
        dsr_text = None if dsr_ok else (
            f"dsr {dsr_value:.3f} < {DSR_MIN_PROBABILITY} (n_trials={n_trials})"
        )
    warn("dsr", dsr_ok, _r(dsr_value), DSR_MIN_PROBABILITY, "robust", dsr_text, n_trials=n_trials)

    if pbo_undefined:
        pbo_ok = False
        pbo_text = pbo_undefined
    elif pbo is None:
        pbo_ok = False
        pbo_text = "pbo undefined"
    elif pbo > PBO_MAX:
        pbo_ok = False
        pbo_text = f"pbo {pbo:.3f} > {PBO_MAX}"
    else:
        pbo_ok = True
        pbo_text = None
    warn("pbo", pbo_ok, _r(pbo), PBO_MAX, "robust", pbo_text)

    n_nb = len(neighbour_passes)
    n_pass = sum(1 for flag in neighbour_passes if flag)
    frac = n_pass / n_nb if n_nb else 0.0
    nb_ok = n_nb > 0 and frac + 1e-12 >= NEIGHBOR_PASS_MIN_FRACTION
    if n_nb == 0:
        nb_reason = "no parameter neighbours (lone spike)"
    elif not nb_ok:
        nb_reason = f"neighbours {n_pass}/{n_nb} = {frac:.0%} < {NEIGHBOR_PASS_MIN_FRACTION:.0%}"
    else:
        nb_reason = None
    warn(
        "neighbours",
        nb_ok,
        f"{n_pass}/{n_nb}",
        NEIGHBOR_PASS_MIN_FRACTION,
        "robust",
        nb_reason,
        n_pass=n_pass,
        total=n_nb,
    )

    bh_bits: list[str] = []
    if bh_oos_pnl is None:
        bh_bits.append("buy-and-hold oos missing")
    elif oos_pnl <= bh_oos_pnl:
        bh_bits.append(f"oos_pnl {oos_pnl:.2f} <= buy-and-hold {bh_oos_pnl:.2f}")
    if holdout_present:
        if bh_holdout_pnl is None:
            bh_bits.append("buy-and-hold holdout missing")
        elif holdout_pnl is not None and holdout_pnl <= bh_holdout_pnl:
            bh_bits.append(f"holdout_pnl {holdout_pnl:.2f} <= buy-and-hold {bh_holdout_pnl:.2f}")
    warn(
        "buy_and_hold",
        not bh_bits,
        _r(oos_pnl, 2),
        _r(bh_oos_pnl, 2),
        "baseline",
        "; ".join(bh_bits) if bh_bits else None,
        role_note=BH_METRIC_ROLE,
    )

    failing = [key for key in VETO_CHECKS if not checks[key]["pass"]]
    warning_keys = [key for key in WARNING_CHECKS if not checks[key]["pass"]]
    return {
        "checks": checks,
        "fail_reasons": reasons,
        "warnings": warnings,
        "failing_checks": failing,
        "warning_checks": warning_keys,
        "qualified": not failing,
        "count_only": count_only_failure(checks),
        "oos_pnl_pass": oos_profit,
        "trades_pass": trades_ok and hold_trades_ok,
        "holdout_pass": holdout_present and hold_profit and hold_trades_ok,
        "cost_pass": cost_ok,
        "perturb_pass": nb_ok,
        "dsr_pass": dsr_ok,
        "pbo_pass": pbo_ok,
        "neighbours_pass": n_pass,
        "neighbours": n_nb,
    }


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
    """Every gate-v7 check for one library variant, as a discovery record."""
    strategy, params = parse_variant(name)
    trials = library_trial_count() if n_trials is None else int(n_trials)
    disc = discovery_metrics(data, strategy, params)
    bh = _benchmark(data, "bh", "discovery")
    sma = _benchmark(data, "sma_stack", "discovery")
    hold = holdout_metrics(data, strategy, params)
    hold_bh = _benchmark(data, "bh", "holdout")
    hold_sma = _benchmark(data, "sma_stack", "holdout")
    stressed = discovery_metrics(data, strategy, params, costs=STRESS_COSTS)
    stressed_hold = holdout_metrics(data, strategy, params, costs=STRESS_COSTS)
    stressed_bh = _benchmark(data, "bh", "discovery", STRESS_COSTS)
    oos_net = book_pnl(disc)
    hold_net = None if hold is None else book_pnl(hold)
    sma_net = None if sma is None else book_pnl(sma)
    bh_net = None if bh is None else book_pnl(bh)
    hold_sma_net = None if hold_sma is None else book_pnl(hold_sma)
    hold_bh_net = None if hold_bh is None else book_pnl(hold_bh)
    stress_oos_net = book_pnl(stressed)
    stress_hold_net = None if stressed_hold is None else book_pnl(stressed_hold)

    neighbours = []
    for nb in neighbour_names(name):
        _s, nb_params = parse_variant(nb)
        m = discovery_metrics(data, strategy, nb_params)
        h = holdout_metrics(data, strategy, nb_params)
        sm = discovery_metrics(data, strategy, nb_params, costs=STRESS_COSTS)
        sh = holdout_metrics(data, strategy, nb_params, costs=STRESS_COSTS)
        nb_oos = book_pnl(m)
        nb_hold = None if h is None else book_pnl(h)
        nb_stress_oos = book_pnl(sm)
        nb_stress_hold = None if sh is None else book_pnl(sh)
        profit_pass = (
            nb_oos > 0
            and nb_hold is not None
            and nb_hold > 0
            and nb_stress_oos > 0
            and nb_stress_hold is not None
            and nb_stress_hold > 0
        )
        neighbours.append({
            "name": nb,
            "params": nb_params,
            "daily_sharpe": round(m.daily_sharpe, 4),
            "trades": m.trades,
            "pnl": round(nb_oos, 2),
            "holdout_pnl": None if nb_hold is None else round(nb_hold, 2),
            "stress_oos_pnl": round(nb_stress_oos, 2),
            "stress_holdout_pnl": None if nb_stress_hold is None else round(nb_stress_hold, 2),
            "pass": bool(profit_pass),
            "_windows": m.window_pnl,
        })

    moments = return_moments(list(disc.returns))
    dsr_value = None
    if moments is not None:
        dsr_value = deflated_sharpe_ratio(
            sharpe=float(moments["sharpe"]),
            n_obs=int(moments["n"]),
            n_trials=trials,
            skew=float(moments["skew"]),
            kurtosis=float(moments["kurtosis"]),
        )
    hurdles = dsr_hurdles(moments, trials)
    matrix = [disc.window_pnl] + [nb["_windows"] for nb in neighbours]
    pbo = None
    pbo_undefined = None
    if len(matrix) < 2:
        pbo_undefined = "pbo undefined (fewer than 2 configurations)"
    else:
        pbo = probability_of_backtest_overfitting(np.asarray(matrix, dtype=np.float64).T)
        if pbo is None:
            pbo_undefined = "pbo undefined"
    n_windows = len(disc.window_pnl)
    expected_windows = len(data.segments)
    windows_ok = _blocks_tile(data) and n_windows == expected_windows
    sma_pnl = None if sma is None else sma.pnl
    verdict = library_verdict(
        windows_ok=windows_ok,
        n_windows=n_windows,
        expected_windows=expected_windows,
        window_span=(
            f"{_iso_day(int(data.discovery.ts[data.score_index]))} through the day before the holdout"
        ),
        oos_pnl=oos_net,
        oos_trades=disc.trades,
        oos_sharpe=disc.daily_sharpe,
        sma_oos_pnl=sma_net,
        bh_oos_pnl=bh_net,
        holdout_present=hold is not None,
        holdout_note=data.holdout_note,
        holdout_pnl=hold_net,
        holdout_trades=None if hold is None else hold.trades,
        holdout_sharpe=None if hold is None else hold.daily_sharpe,
        sma_holdout_pnl=hold_sma_net,
        bh_holdout_pnl=hold_bh_net,
        stress_oos_pnl=stress_oos_net,
        stress_holdout_pnl=stress_hold_net,
        dsr_value=dsr_value,
        n_trials=trials,
        pbo=pbo,
        pbo_undefined=pbo_undefined,
        neighbour_passes=[bool(nb["pass"]) for nb in neighbours],
    )
    checks = verdict["checks"]
    reasons = verdict["fail_reasons"]
    warnings = verdict["warnings"]
    failing = verdict["failing_checks"]
    count_only = verdict["count_only"]
    passed = verdict["qualified"]
    for nb in neighbours:
        nb.pop("_windows", None)

    segments = data.segments
    windows = [
        {
            "start": _iso_day(seg_start),
            "end": _iso_day(seg_end - DAY_MS),
            "pnl": round(pnl, 2),
            "trades": trades,
        }
        for (seg_start, seg_end), pnl, trades in zip(segments, disc.window_pnl, disc.window_trades)
    ]
    holdout_block = {"pass": verdict["holdout_pass"]}
    if hold is None:
        holdout_block["reason"] = data.holdout_note or "holdout tape missing"
    else:
        holdout_block.update({
            "sharpe": round(hold.daily_sharpe, 4),
            "daily_sharpe": round(hold.daily_sharpe, 4),
            "trades": int(hold.trades),
            "pnl": round(hold_net, 2),
            "max_drawdown": round(hold.max_dd, 4),
            "bh_daily_sharpe": None if hold_bh is None else round(hold_bh.daily_sharpe, 4),
            "bh_pnl": None if hold_bh_net is None else round(hold_bh_net, 2),
            "bh_role": BH_METRIC_ROLE,
            "sma_pnl": None if hold_sma_net is None else round(hold_sma_net, 2),
        })
    gate_block = {
        "passed": passed,
        "rules": GATE_RULES,
        "engine": ENGINE_ID,
        "oos_pnl_pass": verdict["oos_pnl_pass"],
        "trades_pass": verdict["trades_pass"],
        "holdout_pass": verdict["holdout_pass"],
        "dsr_pass": verdict["dsr_pass"],
        "pbo_pass": verdict["pbo_pass"],
        "perturb_pass": verdict["perturb_pass"],
        "cost_pass": verdict["cost_pass"],
        "holdout": {k: v for k, v in holdout_block.items() if k != "pass"},
        "oos_pnl": round(oos_net, 2),
        "dsr": _r(dsr_value, 6),
        "n_trials": trials,
        "pbo": _r(pbo, 6),
        "neighbours": verdict["neighbours"],
        "neighbours_pass": verdict["neighbours_pass"],
        "cost_oos_pnl": round(stress_oos_net, 2),
        "cost_holdout_pnl": None if stress_hold_net is None else round(stress_hold_net, 2),
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
        "warnings": warnings,
        "oos_net_pnl": round(oos_net, 2),
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
            "warning_checks": verdict["warning_checks"],
            "count_only": count_only,
            "discovery": {**disc.summary(), "net_pnl": round(oos_net, 2)},
            "windows": windows,
            "bh": None if bh is None else bh.summary(),
            "sma_stack": None if sma is None else sma.summary(),
            "holdout": None if hold is None else {
                **hold.summary(),
                "net_pnl": round(hold_net, 2),
                "bh": None if hold_bh is None else {**hold_bh.summary(), "net_pnl": round(hold_bh_net, 2)},
                "sma_stack": None if hold_sma is None else {
                    **hold_sma.summary(),
                    "net_pnl": None if hold_sma_net is None else round(hold_sma_net, 2),
                },
            },
            "cost_stress": {
                **stressed.summary(),
                "net_pnl": round(stress_oos_net, 2),
                "holdout_net_pnl": None if stress_hold_net is None else round(stress_hold_net, 2),
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
            "blocks": {
                "count": len(data.segments),
                "target_days": LIBRARY_BLOCK_TARGET_DAYS,
                "score_start": _iso_day(int(data.discovery.ts[data.score_index])),
                "score_end": _iso_day(HOLDOUT_START_MS - DAY_MS),
            },
        },
    }
    if passed:
        record["score"] = round(oos_admission_score(disc.pnl, disc.daily_sharpe), 2)
    return record
