"""Run the A/B strategy comparison on train and held-out windows, save results.

Loads saved history (ts,open,high,low,close), splits each symbol into
train (first 65%) and held-out (last 35%), backtests every candidate
strategy on both, prints the tables, AND writes results to
state/abtest.json so the dashboard can display them.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/data/paper-trading-bot")
import hedge_fund.backtest.strategies as bs

HIST = "/tmp/crypto_history.json"
OUT = "/opt/data/paper-trading-bot/state/abtest.json"


def _result_dict(r: bs.BacktestResult) -> dict:
    return {
        "strategy": r.strategy, "trades": r.trades, "wins": r.wins,
        "win_rate": round(r.win_rate, 3), "total_pnl": round(r.total_pnl, 2),
        "final_equity": round(r.final_equity, 2), "sharpe": round(r.sharpe, 3),
        "max_drawdown": round(r.max_drawdown, 3), "fees_paid": round(r.fees_paid, 2),
        "error": r.error,
    }


def main():
    data = json.load(open(HIST))
    report = {"as_of": None, "symbols": {}}
    for sym, rows in data.items():
        closes = [r[4] for r in rows]
        highs = [r[2] for r in rows]
        lows = [r[3] for r in rows]
        cut = int(len(rows) * 0.65)
        train = bs.backtest_all(closes[:cut], highs[:cut], lows[:cut])
        hold = bs.backtest_all(closes[cut:], highs[cut:], lows[cut:])
        report["symbols"][sym] = {
            "bars": len(rows), "train_bars": cut, "hold_bars": len(rows) - cut,
            "train": [_result_dict(r) for r in train],
            "held_out": [_result_dict(r) for r in hold],
        }
        print(f"\n=== {sym} — {len(rows)} bars, train {cut}, held-out {len(rows)-cut} ===")
        print(bs.format_results(train, label=f"{sym} TRAIN"))
        print(bs.format_results(hold, label=f"{sym} HELD-OUT"))

    # persist for the dashboard
    Path(OUT).parent.mkdir(exist_ok=True)
    Path(OUT).write_text(json.dumps(report, indent=2))
    print(f"\nsaved backtest results -> {OUT}")


if __name__ == "__main__":
    main()
