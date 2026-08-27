"""Fast candidate backtesting & replenishment engine.

Checks active champions count in champions.json.
If active < 10, runs a fast backtest sweep over candidate strategy templates
and immediately promotes the top performers until active champions == 10.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/data/paper-trading-bot")
import hedge_fund.backtest.strategies as bs
from hedge_fund.backtest.stride import downsample
from hedge_fund.trading.champions import (
    load_pool,
    load_graduated,
    promote_candidates,
    MAX_ACTIVE_CHAMPIONS,
)
from scripts.sweep import build_pool

HIST_1H = "/opt/data/paper-trading-bot/state/crypto_history_1h.json"
HIST_5M = "/opt/data/paper-trading-bot/state/crypto_history_5m.json"
HIST = HIST_5M if Path(HIST_5M).exists() else HIST_1H


def replenish_pool(stride: int = 12, recent_bars: int = 15000) -> dict:
    st = load_pool()
    current_count = len(st["champions"])
    needed = MAX_ACTIVE_CHAMPIONS - current_count

    if needed <= 0:
        return {"replenished": False, "reason": f"Pool full ({current_count}/{MAX_ACTIVE_CHAMPIONS})"}

    print(f"[replenish] Active champions: {current_count}/{MAX_ACTIVE_CHAMPIONS}. Need {needed} new champions.")

    data = json.load(open(HIST))
    # Slice the most recent ~15,000 bars and downsample with stride for sub-second sweeps
    sampled_data = {}
    for s, rows in data.items():
        slice_rows = rows[-recent_bars:] if len(rows) > recent_bars else rows
        sampled_data[s] = downsample(slice_rows, stride)

    candidates = build_pool()
    # Deduplicate candidates
    seen = {}
    for n, p in candidates:
        seen.setdefault(n, p)
    candidate_list = list(seen.items())

    grad_list = load_graduated()
    exclude = {c["name"] for c in st["champions"]}.union({g["name"] for g in grad_list})

    scored_candidates = []
    for name, pred in candidate_list:
        if name in exclude:
            continue
        train_stats = []
        hold_stats = []
        for sym, rows in sampled_data.items():
            closes = [r[4] for r in rows]
            highs = [r[2] for r in rows]
            lows = [r[3] for r in rows]
            cut = int(len(rows) * 0.70)
            try:
                tr = bs.backtest(closes[:cut], highs[:cut], lows[:cut], pred)
                ho = bs.backtest(closes[cut:], highs[cut:], lows[cut:], pred)
            except Exception:
                continue
            train_stats.append({"sym": sym, "trades": tr.trades, "pnl": tr.total_pnl, "win": tr.win_rate})
            hold_stats.append({"sym": sym, "trades": ho.trades, "pnl": ho.total_pnl, "win": ho.win_rate})

        if not train_stats or not hold_stats:
            continue

        train_pnl = sum(s["pnl"] for s in train_stats)
        hold_pnl = sum(s["pnl"] for s in hold_stats)
        total_trades = sum(s["trades"] for s in train_stats) + sum(s["trades"] for s in hold_stats)

        # Require profitability and activity
        if train_pnl > 0 and hold_pnl > 0 and total_trades >= 4:
            score = hold_pnl + train_pnl * 0.5
            scored_candidates.append({
                "strategy": name,
                "score": score,
                "train_pnl": train_pnl,
                "hold_pnl": hold_pnl,
                "trades": total_trades
            })

    scored_candidates.sort(key=lambda x: x["score"], reverse=True)
    promo_result = promote_candidates(scored_candidates)

    return {
        "replenished": True,
        "added": promo_result["added"],
        "active_count": promo_result["active_count"],
        "target": MAX_ACTIVE_CHAMPIONS,
        "top_evaluated": scored_candidates[:5]
    }


if __name__ == "__main__":
    res = replenish_pool()
    print(json.dumps(res, indent=2))
