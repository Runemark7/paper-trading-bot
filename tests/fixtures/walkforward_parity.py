"""Deterministic 5m fixture + helpers for walk-forward OOS parity tests.

CI must not load jensa's full ``crypto_history_5m.json`` (~600k bars).
This module builds a fixed synthetic tape and runs the **same** qualify
path as ``scripts/discovery_worker.py``:

    _window_slices → _benchmark_oos → evaluate_strategy_record
        → evaluate_windows → hedge_fund.backtest.strategies.backtest
        → qualification_decision

Reduced size (documented, topology only — not a live-gate change):

    PARITY_N_WINDOWS = QUAL_N_WINDOWS  # 8, same as live
    PARITY_WINDOW_BARS = 960           # ~3.3 calendar days of 5m
    live QUAL_WINDOW_BARS              # 25920 = 90 calendar days of 5m
    PARITY_STRIDE = QUAL_STRIDE        # 1, native 5m, no skip

Train/test cut stays 70/30 inside each window (``_prepare_sample``).
Admit semantics stay the frozen OOS gates (trades ≥ 30, Sharpe ≥ 0.30,
beat B&H, beat sma_stack, fail-once, 5m, rm_v1).
"""
from __future__ import annotations

import json
import math
import random
from typing import Any

from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.trading.constants import (
    QUAL_N_WINDOWS,
    QUAL_STRIDE,
    QUAL_SYMBOLS,
    QUAL_TIMEFRAME,
    QUAL_WINDOW_BARS,
    RISK_POLICY,
)
from scripts.tournament_engine import (
    _benchmark_oos,
    _window_slices,
    evaluate_strategy_record,
    evaluate_windows,
)

# Same window count as live qualify. Shorter per-window tape for CI.
PARITY_N_WINDOWS = QUAL_N_WINDOWS
PARITY_WINDOW_BARS = 960
PARITY_STRIDE = QUAL_STRIDE
PARITY_SEED = 20260913
PARITY_START_MS = 1_700_000_000_000
PARITY_BAR_MS = 300_000

# HTF×mom 2-atoms (farm recipe) + a structure AND + the sma_stack benchmark.
PARITY_STRATEGY_NAMES: tuple[str, ...] = (
    "h1_ema_abv_24&mom_18b_gt2pc",
    "h1_ema_abv_15&mom_12b_gt2pc",
    "h4_ema_abv_12&mom_12b_gt2pc",
    "mom_12b_gt2pc&don_hi_24",
    "sma_stack",
)

RECORD_COMPARE_KEYS: tuple[str, ...] = (
    "strategy",
    "timeframe",
    "risk_policy",
    "train_pnl",
    "test_pnl",
    "sharpe",
    "win_rate_pct",
    "trades",
    "regimes_tested",
    "qualified",
    "bh_oos_pnl",
    "sma_stack_oos_pnl",
    "fail_reasons",
    "all_windows_nonneg",
    "score",
)

WINDOW_COMPARE_KEYS: tuple[str, ...] = (
    "train_pnl",
    "test_pnl",
    "test_trades",
    "trades",
    "wins",
    "sharpe",
    "skipped",
    "failed",
)


def _symbol_rows(n: int, *, seed: int, start_px: float) -> list[list]:
    """Contiguous 5m OHLC: slow trend + sinusoid + seeded gaussian noise."""
    rng = random.Random(seed)
    rows: list[list] = []
    px = float(start_px)
    for i in range(n):
        drift = 0.00012 + 0.00035 * math.sin(i / 90.0)
        shock = rng.gauss(0.0, 0.0016)
        px = max(10.0, px * (1.0 + drift + shock))
        wick = abs(rng.gauss(0.0, 0.0007))
        high = px * (1.0 + wick)
        low = px * (1.0 - wick)
        if low > px:
            low = px
        if high < px:
            high = px
        open_px = px
        rows.append([PARITY_START_MS + i * PARITY_BAR_MS, open_px, high, low, px])
    return rows


def make_parity_history(
    *,
    window_bars: int = PARITY_WINDOW_BARS,
    n_windows: int = PARITY_N_WINDOWS,
) -> dict[str, list[list]]:
    """Two-symbol 5m history covering ``window_bars * n_windows`` bars each."""
    n = window_bars * n_windows
    starts = {"BTC/USDT": 42_000.0, "ETH/USDT": 2_400.0}
    out: dict[str, list[list]] = {}
    for offset, sym in enumerate(QUAL_SYMBOLS):
        out[sym] = _symbol_rows(n, seed=PARITY_SEED + offset * 17, start_px=starts[sym])
    return out


def make_parity_slices(
    *,
    window_bars: int = PARITY_WINDOW_BARS,
    n_windows: int = PARITY_N_WINDOWS,
    stride: int = PARITY_STRIDE,
) -> list[dict]:
    data = make_parity_history(window_bars=window_bars, n_windows=n_windows)
    slices = _window_slices(data, window_bars, n_windows, stride)
    if len(slices) != n_windows:
        raise RuntimeError(f"expected {n_windows} slices, got {len(slices)}")
    return slices


def snapshot_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    out = {k: record.get(k) for k in RECORD_COMPARE_KEYS if k in record or k == "score"}
    if "score" not in record:
        out.pop("score", None)
    return out


def snapshot_windows(windows: list[dict]) -> list[dict]:
    return [{k: w.get(k) for k in WINDOW_COMPARE_KEYS} for w in windows]


def run_parity_eval(
    name: str,
    slices: list[dict],
    *,
    n_windows: int,
    bh_oos_pnl: float | None,
    sma_stack_oos_pnl: float | None,
) -> dict[str, Any]:
    """One name through the live qualify path. Returns record + window rows."""
    pred = parse_strategy(name)
    windows = evaluate_windows(pred, slices)
    record = evaluate_strategy_record(
        name,
        slices,
        n_windows=n_windows,
        bh_oos_pnl=bh_oos_pnl,
        sma_stack_oos_pnl=sma_stack_oos_pnl,
    )
    return {
        "name": name,
        "record": snapshot_record(record),
        "windows": snapshot_windows(windows),
    }


def build_golden_payload(
    *,
    names: tuple[str, ...] = PARITY_STRATEGY_NAMES,
    window_bars: int = PARITY_WINDOW_BARS,
    n_windows: int = PARITY_N_WINDOWS,
    stride: int = PARITY_STRIDE,
) -> dict[str, Any]:
    slices = make_parity_slices(window_bars=window_bars, n_windows=n_windows, stride=stride)
    bh, sma = _benchmark_oos(slices)
    strategies = [
        run_parity_eval(name, slices, n_windows=n_windows, bh_oos_pnl=bh, sma_stack_oos_pnl=sma)
        for name in names
    ]
    return {
        "meta": {
            "n_windows": n_windows,
            "window_bars": window_bars,
            "stride": stride,
            "live_window_bars": QUAL_WINDOW_BARS,
            "timeframe": QUAL_TIMEFRAME,
            "risk_policy": RISK_POLICY,
            "symbols": list(QUAL_SYMBOLS),
            "seed": PARITY_SEED,
            "note": (
                "Reduced per-window bar count for CI. Same evaluate_strategy_record "
                "/ evaluate_windows / rm_v1 / 70-30 split / QUAL_N_WINDOWS=8 path "
                "as discovery_worker. Live windows stay 8 × 25920."
            ),
        },
        "bh_oos_pnl": None if bh is None else round(bh, 2),
        "sma_stack_oos_pnl": None if sma is None else round(sma, 2),
        "bh_oos_pnl_raw": bh,
        "sma_stack_oos_pnl_raw": sma,
        "strategies": strategies,
    }


def write_golden(path=None) -> None:
    """Rewrite the committed snapshot from current evaluate_* behavior."""
    from pathlib import Path

    dest = Path(path) if path else Path(__file__).resolve().parent / "walkforward_parity_golden.json"
    payload = build_golden_payload()
    dest.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    print(f"wrote {dest} ({len(payload['strategies'])} strategies)")


if __name__ == "__main__":
    write_golden()
