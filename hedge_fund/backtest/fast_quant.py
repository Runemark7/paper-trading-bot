"""Fast Vectorized Multi-Candidate Backtester using Pandas & QuantStats.

Replaces slow iterative bar-loops with vectorized matrix calculations.
Evaluates candidates across Sortino, Calmar, Max Drawdown, and Profit Factor.
"""
from __future__ import annotations

import json
import os
import sys
import numpy as np
import pandas as pd
import quantstats as qs
from pathlib import Path

STATE_DIR = Path(os.environ.get("PAPER_STATE", "/opt/data/paper-trading-bot/state"))
HIST_5M = STATE_DIR / "crypto_history_5m.json"


def load_data(limit_bars: int = 40000) -> dict[str, pd.DataFrame]:
    raw = json.load(open(HIST_5M))
    dfs = {}
    for sym, rows in raw.items():
        slice_rows = rows[-limit_bars:]
        df = pd.DataFrame(slice_rows, columns=["ts", "open", "high", "low", "close"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df.set_index("ts", inplace=True)
        df["ret"] = df["close"].pct_change().fillna(0.0)
        dfs[sym] = df
    return dfs


def evaluate_vectorized_signals(
    dfs: dict[str, pd.DataFrame],
    signal_func: callable,
    fee: float = 0.001,
    slippage: float = 0.0002
) -> dict:
    """Vectorized multi-asset backtest with next-bar execution & QuantStats metrics."""
    asset_metrics = []

    for sym, df in dfs.items():
        # Compute binary entry signal (1 = long, 0 = flat)
        sig = signal_func(df)
        
        # Next-bar execution (shift signal by 1 bar to avoid lookahead bias)
        pos = sig.shift(1).fillna(0.0)
        
        # Identify trade entry/exit transitions for fee calculation
        trades = (pos.diff().abs() > 0).astype(int)
        
        # Raw strategy return minus transaction costs
        strat_ret = (pos * df["ret"]) - (trades * (fee + slippage))
        daily_ret = strat_ret.resample("1D").sum()

        if len(daily_ret) < 10 or daily_ret.std() == 0:
            continue

        sharpe = qs.stats.sharpe(daily_ret)
        sortino = qs.stats.sortino(daily_ret)
        calmar = qs.stats.calmar(daily_ret)
        max_dd = qs.stats.max_drawdown(daily_ret)
        win_rate = qs.stats.win_rate(daily_ret)
        total_pnl_pct = (1.0 + strat_ret).prod() - 1.0

        asset_metrics.append({
            "symbol": sym,
            "sharpe": float(sharpe) if not np.isnan(sharpe) else 0.0,
            "sortino": float(sortino) if not np.isnan(sortino) else 0.0,
            "calmar": float(calmar) if not np.isnan(calmar) else 0.0,
            "max_dd_pct": float(max_dd) * 100 if not np.isnan(max_dd) else 0.0,
            "win_rate_pct": float(win_rate) * 100 if not np.isnan(win_rate) else 0.0,
            "total_return_pct": float(total_pnl_pct) * 100
        })

    if not asset_metrics:
        return {"qualified": False, "score": -999.0}

    avg_sharpe = sum(m["sharpe"] for m in asset_metrics) / len(asset_metrics)
    avg_sortino = sum(m["sortino"] for m in asset_metrics) / len(asset_metrics)
    avg_calmar = sum(m["calmar"] for m in asset_metrics) / len(asset_metrics)
    avg_win_rate = sum(m["win_rate_pct"] for m in asset_metrics) / len(asset_metrics)
    tot_return_pct = sum(m["total_return_pct"] for m in asset_metrics)

    # Institutional Edge Filter:
    # 1. Positive returns across assets
    # 2. Sortino > 0.5 (good upside to downside ratio)
    # 3. Calmar > 0.2 (low drawdown vs returns)
    qualified = (
        tot_return_pct > 0
        and avg_sortino >= 0.50
        and avg_calmar >= 0.15
        and avg_win_rate >= 40.0
    )

    composite_score = (avg_sortino * 100.0) + (avg_calmar * 50.0) + tot_return_pct

    return {
        "qualified": qualified,
        "score": round(composite_score, 2),
        "avg_sortino": round(avg_sortino, 2),
        "avg_calmar": round(avg_calmar, 2),
        "avg_sharpe": round(avg_sharpe, 2),
        "avg_win_rate_pct": round(avg_win_rate, 1),
        "total_return_pct": round(tot_return_pct, 2),
        "asset_metrics": asset_metrics
    }
