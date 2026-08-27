import sys, json
sys.path.insert(0, "/opt/data/paper-trading-bot")
from scripts.tournament_engine import generate_candidate_pool, HIST_5M
import hedge_fund.backtest.strategies as bs
from hedge_fund.backtest.stride import downsample
from hedge_fund.signals.dynamic import parse_strategy

candidates = generate_candidate_pool()
data = json.load(open(HIST_5M))
sampled = {s: downsample(rows[-20000:], 12) for s, rows in data.items()}

passed = []
for name in candidates:
    try:
        pred = parse_strategy(name)
    except Exception:
        continue
    train_results, test_results = [], []
    for sym, rows in sampled.items():
        closes = [r[4] for r in rows]
        highs = [r[2] for r in rows]
        lows = [r[3] for r in rows]
        n = len(rows)
        cut = int(n * 0.70)
        tr = bs.backtest(closes[:cut], highs[:cut], lows[:cut], pred)
        te = bs.backtest(closes[cut:], highs[cut:], lows[cut:], pred)
        train_results.append(tr)
        test_results.append(te)

    if not train_results or not test_results:
        continue

    tot_train_pnl = sum(r.total_pnl for r in train_results)
    tot_test_pnl = sum(r.total_pnl for r in test_results)
    tot_trades = sum(r.trades for r in train_results) + sum(r.trades for r in test_results)
    tot_wins = sum(r.wins for r in train_results) + sum(r.wins for r in test_results)
    avg_sharpe = sum(r.sharpe for r in test_results) / len(test_results)
    win_rate = (tot_wins / tot_trades) if tot_trades > 0 else 0.0

    if tot_train_pnl > 0 and tot_test_pnl > 0 and win_rate >= 0.40 and tot_trades >= 5:
        passed.append({
            "name": name,
            "train_pnl": tot_train_pnl,
            "test_pnl": tot_test_pnl,
            "trades": tot_trades,
            "win_rate": round(win_rate * 100, 1),
            "sharpe": round(avg_sharpe, 2)
        })

print(f"Total candidates meeting positive train+test edge: {len(passed)} / {len(candidates)}")
passed.sort(key=lambda x: x["test_pnl"] + x["train_pnl"]*0.5, reverse=True)
for p in passed[:20]:
    print(f"  {p['name']:<28} Trades:{p['trades']:<4} Win:{p['win_rate']:>5.1f}% TrainPnl:{p['train_pnl']:>7.0f} TestPnl:{p['test_pnl']:>7.0f} Sharpe:{p['sharpe']:>5.2f}")
