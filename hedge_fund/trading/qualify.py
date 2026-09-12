"""Honest OOS qualification gates (paper only).

Aggregate test PnL / Sharpe / trade count decide. A single skipped, empty,
or negative walk-forward window is a diagnostic, not a fail reason.
Beat buy-and-hold and sma_stack stay. Fail-once still parks names that
fail those remaining gates.
"""
from __future__ import annotations

from datetime import datetime, timezone

from hedge_fund.trading.constants import (
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    QUAL_N_WINDOWS,
)

WINDOW_VETO_REASON = "not all windows non-negative"
WINDOW_SLOT_REASON_PREFIX = "window["
REQUALIFY_SOURCE = "aggregate_oos_no_window_veto"


def oos_admission_score(tot_test_pnl: float, avg_sharpe: float) -> float:
    """Admit ranking uses OOS/test only — train PnL never boosts the score."""
    return tot_test_pnl + (avg_sharpe * 10.0)


def is_window_veto_reason(reason: object) -> bool:
    text = str(reason)
    return text == WINDOW_VETO_REASON or text.startswith(WINDOW_SLOT_REASON_PREFIX)


def window_is_nonneg(window: dict) -> bool:
    skipped = bool(window.get("skipped") or window.get("failed"))
    test_pnl = window.get("test_pnl")
    test_trades = int(window.get("test_trades") or 0)
    if skipped or test_pnl is None or test_trades < 1 or float(test_pnl) < 0:
        return False
    return True


def aggregate_fail_reasons(
    *,
    tot_test_pnl: float,
    tot_oos_trades: int,
    avg_sharpe: float,
    bh_oos_pnl: float | None,
    sma_stack_oos_pnl: float | None,
    n_windows: int,
    expected_windows: int,
    min_sharpe: float = MIN_BACKTEST_SHARPE,
    min_trades: int = MIN_BACKTEST_TRADES,
) -> list[str]:
    """Fail reasons from stored or just-computed OOS aggregates. No per-window veto."""
    reasons: list[str] = []
    if n_windows != expected_windows:
        reasons.append(f"windows {n_windows} != expected {expected_windows}")
    if tot_oos_trades < min_trades:
        reasons.append(f"oos_trades {tot_oos_trades} < {min_trades}")
    if avg_sharpe < min_sharpe:
        reasons.append(f"oos_sharpe {avg_sharpe:.2f} < {min_sharpe}")
    if bh_oos_pnl is None:
        reasons.append("buy-and-hold missing")
    elif tot_test_pnl <= bh_oos_pnl:
        reasons.append(f"oos_pnl {tot_test_pnl:.2f} <= bh {bh_oos_pnl:.2f}")
    if sma_stack_oos_pnl is None:
        reasons.append("sma_stack missing")
    elif tot_test_pnl <= sma_stack_oos_pnl:
        reasons.append(f"oos_pnl {tot_test_pnl:.2f} <= sma_stack {sma_stack_oos_pnl:.2f}")
    return reasons


def qualification_decision(
    windows: list[dict],
    *,
    expected_windows: int,
    bh_oos_pnl: float | None,
    sma_stack_oos_pnl: float | None,
    min_sharpe: float = MIN_BACKTEST_SHARPE,
    min_trades: int = MIN_BACKTEST_TRADES,
) -> dict:
    """Pure OOS gate. Per-window skipped/neg/empty is diagnostic only."""
    tot_test_pnl = sum(float(w.get("test_pnl") or 0.0) for w in windows)
    tot_oos_trades = sum(int(w.get("test_trades") or 0) for w in windows)
    sharpes = [float(w.get("sharpe") or 0.0) for w in windows] or [0.0]
    avg_sharpe = sum(sharpes) / len(sharpes)
    tot_train_pnl = sum(float(w.get("train_pnl") or 0.0) for w in windows)
    all_windows_nonneg = all(window_is_nonneg(w) for w in windows) if windows else False
    reasons = aggregate_fail_reasons(
        tot_test_pnl=tot_test_pnl,
        tot_oos_trades=tot_oos_trades,
        avg_sharpe=avg_sharpe,
        bh_oos_pnl=bh_oos_pnl,
        sma_stack_oos_pnl=sma_stack_oos_pnl,
        n_windows=len(windows),
        expected_windows=expected_windows,
        min_sharpe=min_sharpe,
        min_trades=min_trades,
    )
    return {
        "passed": not reasons,
        "reasons": reasons,
        "tot_test_pnl": tot_test_pnl,
        "tot_train_pnl": tot_train_pnl,
        "tot_oos_trades": tot_oos_trades,
        "avg_sharpe": avg_sharpe,
        "all_windows_nonneg": all_windows_nonneg,
        "score": oos_admission_score(tot_test_pnl, avg_sharpe),
        "bh_oos_pnl": bh_oos_pnl,
        "sma_stack_oos_pnl": sma_stack_oos_pnl,
    }


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def qualification_from_record(
    row: dict,
    *,
    expected_windows: int = QUAL_N_WINDOWS,
    min_sharpe: float = MIN_BACKTEST_SHARPE,
    min_trades: int = MIN_BACKTEST_TRADES,
) -> dict:
    """Re-decide from a stored discovery_log row. No walk-forward.

    Uses ``test_pnl``, ``trades``, ``sharpe``, ``bh_oos_pnl``,
    ``sma_stack_oos_pnl``, and ``regimes_tested`` (slice-count equivalent).
    """
    tot_test_pnl = float(row.get("test_pnl") or 0.0)
    tot_oos_trades = int(row.get("trades") or 0)
    avg_sharpe = float(row.get("sharpe") or 0.0)
    if "regimes_tested" in row and row.get("regimes_tested") is not None:
        n_windows = int(row.get("regimes_tested") or 0)
    else:
        n_windows = expected_windows
    bh = _optional_float(row.get("bh_oos_pnl")) if "bh_oos_pnl" in row else None
    sma = _optional_float(row.get("sma_stack_oos_pnl")) if "sma_stack_oos_pnl" in row else None
    reasons = aggregate_fail_reasons(
        tot_test_pnl=tot_test_pnl,
        tot_oos_trades=tot_oos_trades,
        avg_sharpe=avg_sharpe,
        bh_oos_pnl=bh,
        sma_stack_oos_pnl=sma,
        n_windows=n_windows,
        expected_windows=expected_windows,
        min_sharpe=min_sharpe,
        min_trades=min_trades,
    )
    old_reasons = row.get("fail_reasons") if isinstance(row.get("fail_reasons"), list) else []
    all_windows_nonneg = not any(is_window_veto_reason(r) for r in old_reasons)
    if "all_windows_nonneg" in row:
        all_windows_nonneg = bool(row.get("all_windows_nonneg"))
    return {
        "passed": not reasons,
        "reasons": reasons,
        "tot_test_pnl": tot_test_pnl,
        "tot_oos_trades": tot_oos_trades,
        "avg_sharpe": avg_sharpe,
        "all_windows_nonneg": all_windows_nonneg,
        "score": oos_admission_score(tot_test_pnl, avg_sharpe),
        "bh_oos_pnl": bh,
        "sma_stack_oos_pnl": sma,
    }


def requalify_parked_log(
    log: list[dict],
    *,
    existing_names: set[str],
    expected_windows: int = QUAL_N_WINDOWS,
    now: str | None = None,
) -> tuple[list[dict], list[str]]:
    """Flip newest parked rows that now pass on stored aggregates.

    Mutates matching log rows in place. Does not re-run walk-forwards.
    Names that still fail Sharpe / trades / beat-B&H / sma stay parked.
    Newest-first log: only the latest eval per name is considered.
    """
    stamp = now or datetime.now(timezone.utc).isoformat()
    admitted_rows: list[dict] = []
    names: list[str] = []
    seen: set[str] = set()
    for row in log:
        if not isinstance(row, dict):
            continue
        name = row.get("strategy")
        if not name or not isinstance(name, str) or name in seen:
            continue
        seen.add(name)
        if row.get("qualified"):
            continue
        if name in existing_names:
            continue
        decision = qualification_from_record(row, expected_windows=expected_windows)
        if not decision["passed"]:
            continue
        old_reasons = row.get("fail_reasons") if isinstance(row.get("fail_reasons"), list) else []
        if any(is_window_veto_reason(r) for r in old_reasons):
            row["all_windows_nonneg"] = False
        row["qualified"] = True
        row["fail_reasons"] = []
        row["requalified_at"] = stamp
        row["requalify_source"] = REQUALIFY_SOURCE
        row["score"] = round(decision["score"], 2)
        admitted_rows.append(row)
        names.append(name)
    return admitted_rows, names
