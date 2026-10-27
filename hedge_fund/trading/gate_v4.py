"""Gate v4 checks that gate v5 still applies on top of the aggregate floors.

A name that already passed Sharpe, trades, and sma_stack
(``qualification_decision``) is not admitted until:

* one sealed holdout evaluation passes Sharpe, the holdout trade floor,
  and beat sma_stack. Holdout buy-and-hold daily Sharpe is recorded and
  is not a pass rule (gate v5),
* the deflated Sharpe ratio of the walk-forward daily returns is
  at least ``DSR_MIN_PROBABILITY`` given the true trial count,
* CSCV PBO on the walk-forward windows of this name and its parameter
  neighbours is at most ``PBO_MAX``,
* at least ``NEIGHBOR_PASS_MIN_FRACTION`` of those neighbours still
  meet the base Sharpe and trade floors,
* the same walk-forward at 2× fees and +5 bps slippage still has
  Sharpe >= 0.30. Stressed buy-and-hold daily Sharpe is recorded and
  is not a pass rule (gate v5).

ETH/USDT Sharpe over the same discovery windows is recorded and must
be > 0 to count as a pass of the soft check. It does not block
admission: ETH is not a traded symbol and is not a pass requirement.

Holdout numbers are stored under ``gate_v4`` and are not the ``sharpe``
/ ``test_pnl`` fields densify and lift read.
"""
from __future__ import annotations

import math

import numpy as np

from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.trading.buy_and_hold import (
    buy_and_hold_daily_equity,
    buy_and_hold_window_pnl,
    daily_equity_sharpe,
)
from hedge_fund.trading.constants import (
    COST_STRESS_EXTRA_SLIPPAGE,
    COST_STRESS_FEE_MULTIPLE,
    CROSS_ASSET_MIN_SHARPE,
    BH_METRIC_ROLE,
    DSR_MIN_PROBABILITY,
    GATE_RULES,
    HOLDOUT_MIN_TRADES,
    MIN_BACKTEST_SHARPE,
    NEIGHBOR_PASS_MIN_FRACTION,
    PAPER_START_CASH,
    PBO_MAX,
)
from hedge_fund.trading.multiple_testing import (
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    return_moments,
)
from hedge_fund.trading.perturb import neighbor_names
from hedge_fund.risk.rm_v1 import FEE_SLIPPAGE, FEE_TAKER

_ATTESTATION_KEYS = (
    "holdout_pass",
    "dsr_pass",
    "pbo_pass",
    "perturb_pass",
    "cost_pass",
)


def v4_pass_attestation(**extra) -> dict:
    """Minimal block a qualified ingest row must carry."""
    block = {
        "passed": True,
        "rules": GATE_RULES,
        "holdout_pass": True,
        "dsr_pass": True,
        "pbo_pass": True,
        "perturb_pass": True,
        "cost_pass": True,
    }
    block.update(extra)
    return block


def v4_attestation_ok(row: dict) -> bool:
    """True when a qualified row carries a passing v4 block for this tag."""
    block = row.get("gate_v4") if isinstance(row, dict) else None
    if not isinstance(block, dict):
        return False
    if block.get("passed") is not True or block.get("rules") != GATE_RULES:
        return False
    return all(block.get(key) is True for key in _ATTESTATION_KEYS)


def stress_fees() -> tuple[float, float]:
    """(taker fee, slippage per side) for the cost-stress re-run."""
    return (
        float(FEE_TAKER) * float(COST_STRESS_FEE_MULTIPLE),
        float(FEE_SLIPPAGE) + float(COST_STRESS_EXTRA_SLIPPAGE),
    )


def _avg_sharpe(windows: list[dict]) -> float:
    if not windows:
        return 0.0
    return sum(float(w.get("sharpe") or 0.0) for w in windows) / len(windows)


def _trades(windows: list[dict]) -> int:
    return sum(int(w.get("test_trades") or 0) for w in windows)


def _daily(windows: list[dict]) -> list[float]:
    out: list[float] = []
    for w in windows:
        rets = w.get("daily_returns")
        if isinstance(rets, list):
            out.extend(float(x) for x in rets)
    return out


def _max_dd(windows: list[dict]) -> float | None:
    vals = []
    for w in windows:
        dd = w.get("max_drawdown")
        if isinstance(dd, (int, float)) and not isinstance(dd, bool) and math.isfinite(float(dd)):
            vals.append(float(dd))
    return max(vals) if vals else None


def _window_pnl(windows: list[dict]) -> list[float]:
    return [float(w.get("test_pnl") or 0.0) for w in windows]


def neighbour_fraction_ok(passed: int, total: int) -> tuple[bool, str | None]:
    if total <= 0:
        return False, "no parameter neighbours (lone spike)"
    frac = passed / total
    if frac + 1e-12 < NEIGHBOR_PASS_MIN_FRACTION:
        return False, f"neighbours {passed}/{total} = {frac:.0%} < {NEIGHBOR_PASS_MIN_FRACTION:.0%}"
    return True, None


def base_sharpe_trades_ok(sharpe: float, trades: int) -> bool:
    return float(sharpe) >= MIN_BACKTEST_SHARPE and int(trades) >= 30


def score_name(name: str, slices: list, *, taker_fee: float | None = None, slippage: float | None = None) -> list[dict]:
    """Walk-forward window scores for one name. Patched in tests."""
    from scripts.tournament_engine import evaluate_windows

    pred = parse_strategy(name)
    return evaluate_windows(pred, slices, taker_fee=taker_fee, slippage=slippage)


def _holdout_metrics(name: str, sample: dict, benchmarks: dict) -> dict:
    from scripts.tournament_engine import _eval_slice

    pred = parse_strategy(name)
    results = _eval_slice(pred, sample, train=False)
    if not results:
        return {"pass": False, "reason": "holdout produced no backtest"}
    pnl = sum(r.total_pnl for r in results)
    trades = sum(r.trades for r in results)
    sharpe = sum(r.sharpe for r in results) / len(results)
    dd = max(float(getattr(r, "max_drawdown", 0.0) or 0.0) for r in results)
    daily = []
    from scripts.tournament_engine import _book_daily_returns

    daily = _book_daily_returns(results)
    from hedge_fund.trading.buy_and_hold import daily_returns_sharpe

    dsr = daily_returns_sharpe(daily)
    bh = benchmarks.get("bh_daily_sharpe")
    sma = benchmarks.get("sma_pnl")
    reasons = []
    if sharpe < MIN_BACKTEST_SHARPE:
        reasons.append(f"holdout_sharpe {sharpe:.2f} < {MIN_BACKTEST_SHARPE}")
    if trades < HOLDOUT_MIN_TRADES:
        reasons.append(f"holdout_trades {trades} < {HOLDOUT_MIN_TRADES}")
    if sma is None:
        reasons.append("holdout sma_stack missing")
    elif pnl <= float(sma):
        reasons.append(f"holdout_pnl {pnl:.2f} <= sma_stack {float(sma):.2f}")
    return {
        "pass": not reasons,
        "reason": "; ".join(reasons),
        "sharpe": round(sharpe, 4),
        "trades": int(trades),
        "pnl": round(pnl, 2),
        "daily_sharpe": None if dsr is None else round(float(dsr), 4),
        "bh_daily_sharpe": None if bh is None else round(float(bh), 4),
        "bh_role": BH_METRIC_ROLE,
        "sma_pnl": None if sma is None else round(float(sma), 2),
        "max_drawdown": round(dd, 4),
    }


def finish_gate_v4(
    name: str,
    record: dict,
    *,
    btc_slices: list,
    eth_slices: list | None,
    holdout_sample: dict | None,
    holdout_benchmarks: dict | None,
    n_trials: int | None,
) -> dict:
    """Apply v4 to a row that already passed the v3 floors. Mutates a copy."""
    out = dict(record)
    reasons = list(out.get("fail_reasons") or [])
    hard: list[str] = []

    windows = score_name(name, btc_slices)
    moments = return_moments(_daily(windows))
    backtest_dd = _max_dd(windows)
    out["backtest_max_dd"] = None if backtest_dd is None else round(backtest_dd, 4)

    # --- holdout (one shot; not written back onto sharpe / test_pnl) ---
    if not holdout_sample or not holdout_benchmarks:
        holdout = {"pass": False, "reason": "holdout tape missing"}
    else:
        holdout = _holdout_metrics(name, holdout_sample, holdout_benchmarks)
    if not holdout.get("pass"):
        hard.append(holdout.get("reason") or "holdout failed")

    # --- DSR on discovery daily returns only (holdout excluded) ---
    dsr_p = None
    dsr_reason = None
    if moments is None:
        dsr_reason = "dsr daily returns missing"
    elif n_trials is None or int(n_trials) < 1:
        dsr_reason = "dsr n_trials missing"
    else:
        dsr_p = deflated_sharpe_ratio(
            sharpe=float(moments["sharpe"]),
            n_obs=int(moments["n"]),
            n_trials=int(n_trials),
            skew=float(moments["skew"]),
            kurtosis=float(moments["kurtosis"]),
        )
        if dsr_p < DSR_MIN_PROBABILITY:
            dsr_reason = f"dsr {dsr_p:.3f} < {DSR_MIN_PROBABILITY}"
    if dsr_reason:
        hard.append(dsr_reason)

    # --- neighbours: base Sharpe / trades, then PBO on the window matrix ---
    neighbours = neighbor_names(name)
    matrix = [_window_pnl(windows)]
    n_pass = 0
    for neighbour in neighbours:
        scored = score_name(neighbour, btc_slices)
        sharpe = _avg_sharpe(scored)
        trades = _trades(scored)
        if base_sharpe_trades_ok(sharpe, trades):
            n_pass += 1
        matrix.append(_window_pnl(scored))
    perturb_ok, perturb_reason = neighbour_fraction_ok(n_pass, len(neighbours))
    if not perturb_ok:
        hard.append(perturb_reason or "parameter neighbours failed")
    pbo = None
    pbo_reason = None
    if len(matrix) < 2:
        pbo_reason = "pbo undefined (fewer than 2 configurations)"
    else:
        # Rows are windows, columns are configurations.
        width = min(len(col) for col in matrix)
        if width < 2:
            pbo_reason = "pbo undefined (fewer than 2 windows)"
        else:
            perf = np.asarray([col[:width] for col in matrix], dtype=np.float64).T
            pbo = probability_of_backtest_overfitting(perf)
            if pbo is None:
                pbo_reason = "pbo undefined"
            elif pbo > PBO_MAX:
                pbo_reason = f"pbo {pbo:.3f} > {PBO_MAX}"
    if pbo_reason:
        hard.append(pbo_reason)

    # --- cost stress ---
    fee, slip = stress_fees()
    stressed = score_name(name, btc_slices, taker_fee=fee, slippage=slip)
    stress_sharpe = _avg_sharpe(stressed)
    stress_daily_rets = _daily(stressed)
    from hedge_fund.trading.buy_and_hold import daily_returns_sharpe

    stress_daily = daily_returns_sharpe(stress_daily_rets) if stress_daily_rets else 0.0
    stress_bh = _stressed_bh_daily(btc_slices, fee, slip)
    cost_ok = stress_sharpe >= MIN_BACKTEST_SHARPE
    cost_reason = None
    if not cost_ok:
        cost_reason = (
            f"cost stress sharpe {stress_sharpe:.2f} < {MIN_BACKTEST_SHARPE} "
            f"(2x fee + {COST_STRESS_EXTRA_SLIPPAGE * 1e4:.0f}bps)"
        )
        hard.append(cost_reason)

    # --- ETH, soft ---
    cross_sharpe = None
    cross_pass = None
    cross_note = None
    if eth_slices:
        eth_windows = score_name(name, eth_slices)
        cross_sharpe = _avg_sharpe(eth_windows)
        cross_pass = cross_sharpe > CROSS_ASSET_MIN_SHARPE
        if not cross_pass:
            cross_note = f"eth sharpe {cross_sharpe:.2f} <= 0 (soft, not a fail)"
    else:
        cross_note = "eth tape missing (soft, not a fail)"

    passed = not hard
    if hard:
        reasons.extend(hard)
        out["qualified"] = False
        out.pop("score", None)
    block = {
        "passed": passed,
        "rules": GATE_RULES,
        "holdout_pass": bool(holdout.get("pass")),
        "dsr_pass": dsr_reason is None,
        "pbo_pass": pbo_reason is None,
        "perturb_pass": perturb_ok,
        "cost_pass": cost_ok,
        "holdout": {k: v for k, v in holdout.items() if k != "pass"},
        "dsr": None if dsr_p is None else round(float(dsr_p), 6),
        "n_trials": None if n_trials is None else int(n_trials),
        "pbo": None if pbo is None else round(float(pbo), 6),
        "neighbours": len(neighbours),
        "neighbours_pass": n_pass,
        "cost_sharpe": round(stress_sharpe, 4),
        "cost_daily_sharpe": round(float(stress_daily), 4),
        "cost_bh_daily_sharpe": None if stress_bh is None else round(float(stress_bh), 4),
        "cost_bh_role": BH_METRIC_ROLE,
        "cross_asset_sharpe": None if cross_sharpe is None else round(float(cross_sharpe), 4),
        "cross_asset_pass": cross_pass,
        "cross_asset_note": cross_note,
        "backtest_max_dd": out.get("backtest_max_dd"),
        "oos_sharpe_per_obs": None if moments is None else round(float(moments["sharpe"]), 6),
        "oos_return_n": None if moments is None else int(moments["n"]),
    }
    out["gate_v4"] = block
    out["fail_reasons"] = reasons
    out["symbols"] = ["BTC/USDT"]
    return out


def _stressed_bh_daily(slices: list, taker_fee: float, slippage: float) -> float | None:
    """B&H daily Sharpe on the same slices, charged the stressed costs."""
    from scripts.tournament_engine import _test_closes_by_symbol
    from hedge_fund.trading.buy_and_hold import equity_returns

    rets: list[float] = []
    seen = False
    for sample in slices:
        closes = _test_closes_by_symbol(sample)
        points = buy_and_hold_daily_equity(
            closes,
            PAPER_START_CASH,
            taker_fee=taker_fee,
            slippage=slippage,
        )
        if len(points) >= 2:
            seen = True
            rets.extend(equity_returns(points))
    if not seen:
        return None
    from hedge_fund.trading.buy_and_hold import daily_returns_sharpe

    return daily_returns_sharpe(rets)


def holdout_benchmarks(sample: dict) -> dict:
    """B&H and sma_stack on the holdout span. Not used by mint."""
    from scripts.tournament_engine import _eval_slice, _test_closes_by_symbol

    closes = _test_closes_by_symbol(sample)
    bh_pnl = buy_and_hold_window_pnl(closes, PAPER_START_CASH * max(1, len(closes)))
    bh_daily = daily_equity_sharpe(buy_and_hold_daily_equity(closes, PAPER_START_CASH))
    sma = parse_strategy("sma_stack")
    sma_res = _eval_slice(sma, sample, train=False)
    sma_pnl = sum(r.total_pnl for r in sma_res) if sma_res else None
    return {
        "bh_pnl": bh_pnl,
        "bh_daily_sharpe": bh_daily,
        "sma_pnl": sma_pnl,
    }
