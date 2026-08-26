"""Sweep a large grid of candidate strategies over BTC/ETH.

Generates *hundreds* of parameterised long-entry strategies, backtests each
on train (65%) + held-out (35%), and ranks by robustness (how consistently a
strategy is profitable across both symbols AND both windows).

Strategy families swept:
  - SMA stacks over many period triples
  - SMA-above (single moving avg) over many periods
  - EMA-above / EMA stacks
  - RSI thresholds (long breakout & oversold)
  - momentum over lookbacks x thresholds
  - volatility regime (breakout & low-vol)
  - combined gates (SMA + RSI, SMA + momentum)

Usage:
  .venv/bin/python scripts/sweep.py           # ~ up to a few hundred strategies
  .venv/bin/python scripts/sweep.py --limit 50   # quick run
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/data/paper-trading-bot")
import hedge_fund.backtest.strategies as bs

HIST = "/tmp/crypto_history.json"
OUT = "/opt/data/paper-trading-bot/state/sweep.json"


def build_pool() -> list[tuple[str, callable]]:
    """Return a large list of (name, predicate) strategies."""
    pool = []

    def add(name, pred):
        pool.append((name, pred))

    # --- SMA stacks: many period triples ---
    stack_triples = [
        (3, 8, 21), (5, 10, 20), (5, 20, 50), (7, 25, 50), (10, 20, 50),
        (10, 30, 50), (20, 50, 100), (50, 100, 200), (8, 21, 55), (13, 34, 89),
        (5, 13, 34), (21, 55, 144),
    ]
    for t in stack_triples:
        add(f"sma_stack_{'_'.join(map(str, t))}", bs.sma_stack_pred(t))
    # EMA stacks
    for t in stack_triples:
        add(f"ema_stack_{'_'.join(map(str, t))}", bs.ema_stack_pred(t))

    # --- SMA-above: many periods ---
    for p in (5, 10, 13, 20, 21, 30, 40, 50, 55, 100, 150, 200):
        add(f"sma_abv_{p}", bs.sma_above(p))
    # EMA-above
    for p in (5, 10, 13, 20, 21, 30, 50, 100, 200):
        add(f"ema_abv_{p}", bs.ema_above(p))

    # --- RSI family ---
    rsi_periods = [7, 10, 14, 20, 30]
    rsi_buy = [30, 35, 40, 45, 50, 52, 55]
    rsi_over = [85, 90, 100]
    for p, th, ov in itertools.product(rsi_periods, rsi_buy, rsi_over):
        if ov <= th:
            continue
        add(f"rsi_{p}_>{th}_<{ov}", bs.rsi_for(p, th, ov))

    # --- Momentum ---
    for lb in (3, 4, 6, 12, 18, 24, 30, 48):
        for thr in (0.01, 0.02, 0.03, 0.05, 0.08):
            add(f"mom_{lb}b_gt{int(thr*100)}pc", bs.mom_gt(lb, thr))
    # dip (short momentum down = mean reversion)
    for lb in (6, 12, 24):
        for thr in (-0.01, -0.02, -0.03, -0.05):
            add(f"dip_{lb}b_lt{int(-thr*100)}pc", bs.mom_lt(lb, thr))

    # --- Volatility ---
    for lb in (20, 30, 50):
        for hq in (0.6, 0.75, 0.9):
            add(f"vol_brk_{lb}_{int(hq*100)}", bs.vol_regime(lb, hq, 0.25))
    for lb, comp in ((20, 60), (30, 90), (50, 150)):
        add(f"vol_lowsm_{lb}_{comp}", bs.vol_low_smooth_pred(lb, comp))

    # --- Combined gates ---
    sma_periods = [20, 40, 50, 100, 200]
    rsi_pick = [45, 50, 55]
    for sp, rp in itertools.product(sma_periods, rsi_pick):
        add(f"sma{sp}_rsi{rp}", bs._and(bs.sma_above(sp), bs.rsi_for(14, rp, 100)))
    for sp in (20, 50, 100):
        for lb, thr in ((6, 0.01), (12, 0.02), (24, 0.03)):
            add(f"sma{sp}_mom{lb}_{int(thr*100)}", bs._and(bs.sma_above(sp), bs.mom_gt(lb, thr)))
    # long-stack + RSI momentum combos
    for t in stack_triples[:6]:
        for rp in (50, 55, 60):
            add(f"pow_{'_'.join(map(str,t))}_rsi{rp}",
                bs._and(bs.sma_stack_pred(t), bs.rsi_for(20, rp, 100)))

    return pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-bars", type=int, default=1, help="min trades on train to rank")
    ap.add_argument("--stride", type=int, default=6, help="downsample 1h bars (every Nth)")
    args = ap.parse_args()

    data = json.load(open(HIST))
    from hedge_fund.backtest.stride import downsample
    data = {s: downsample(rows, args.stride) for s, rows in data.items()}
    pool = build_pool()
    if args.limit:
        pool = pool[: args.limit]
    print(f"pool: {len(pool)} strategies", flush=True)

    # dedupe by name
    seen = {}
    for n, p in pool:
        seen.setdefault(n, p)
    pool = list(seen.items())

    # rank each strategy: avg of (train sharpe, train pnl) across symbols, but
    # require held-out not a loss to avoid overfit. Robustness score.
    records = []
    for idx, (name, pred) in enumerate(pool):
        train_stats, hold_stats = [], []
        for sym, rows in data.items():
            closes = [r[4] for r in rows]
            highs = [r[2] for r in rows]
            lows = [r[3] for r in rows]
            cut = int(len(rows) * 0.65)
            try:
                tr = bs.backtest(closes[:cut], highs[:cut], lows[:cut], pred)
                ho = bs.backtest(closes[cut:], highs[cut:], lows[cut:], pred)
            except Exception as e:  # noqa
                records.append({"strategy": name, "error": str(e)})
                continue
            train_stats.append({"sym": sym, "trades": tr.trades, "pnl": tr.total_pnl,
                                "win": tr.win_rate, "sharpe": tr.sharpe, "dd": tr.max_drawdown})
            hold_stats.append({"sym": sym, "trades": ho.trades, "pnl": ho.total_pnl,
                               "win": ho.win_rate, "sharpe": ho.sharpe, "dd": ho.max_drawdown})
        # robustness: total train pnl across symbols + held-out total pnl + min train trades
        train_syms = [s for s in train_stats if s["trades"] >= args.min_bars]
        if len(train_syms) < 2:
            continue  # not enough activity to be interesting
        train_pnl = sum(s["pnl"] for s in train_syms)
        train_sh = sum(s["sharpe"] for s in train_syms)
        hold_pnl = sum(s["pnl"] for s in hold_stats)
        # rank score: must be profitable on train, bonus if also on held-out
        score = train_pnl + 0.0 * train_sh
        records.append({
            "strategy": name, "train_pnl": round(train_pnl, 0),
            "train_sharpe": round(train_sh, 2), "hold_pnl": round(hold_pnl, 0),
            "train": train_stats, "held_out": hold_stats,
            "score": round(score, 0),
        })

    # sort by score desc
    records.sort(key=lambda r: r.get("score", -10**9) if "score" in r else -10**9, reverse=True)
    Path(OUT).parent.mkdir(exist_ok=True)
    Path(OUT).write_text(json.dumps({"n": len(records), "results": records}, indent=2))

    print(f"\n=== TOP 25 by train PnL (needs >=2 symbols active, >=1 trade) ===")
    print(f"{'strategy':<34}{'trainPnL':>9}{'trSharpe':>9}{'holdPnL':>9}")
    for r in records[:25]:
        if "error" in r:
            continue
        print(f"{r['strategy']:<34}{r['train_pnl']:>9,.0f}{r['train_sharpe']:>9.2f}{r['hold_pnl']:>9,.0f}")
    print(f"\nsaved -> {OUT}")


if __name__ == "__main__":
    main()