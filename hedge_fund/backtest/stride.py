"""Shared backtest utilities for evolution/sweep speed."""
from __future__ import annotations


def downsample(rows, stride: int = 6):
    """Reduce 1h OHLC rows to ~every `stride`-th bar, preserving the close path.

    Returns list of rows [ts, o, h, l, c]. Sampling every stride-th bar cuts
    backtest runtime ~stride fold while keeping the trend/log-return shape.
    Used because full 8-year 1h history (70k bars) makes 256-strategy sweeps
    impractically slow (~hours) at 1h cadence; stride 6 ≈ backtesting on the
    4h-equivalent sampled series.
    """
    if stride <= 1:
        return rows
    return rows[::stride]


def split_windows(rows, fit=0.70, valid=0.15):
    """Walk-forward split: fit / validation / test indices into `rows`."""
    n = len(rows)
    cut_f = int(n * fit)
    cut_v = int(n * (fit + valid))
    return rows[:cut_f], rows[cut_f:cut_v], rows[cut_v:]
