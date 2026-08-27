import sys, json
sys.path.insert(0, "/opt/data/paper-trading-bot")
from scripts.tournament_engine import generate_candidate_pool, discover_and_qualify, HIST_5M
import hedge_fund.backtest.strategies as bs
from hedge_fund.backtest.stride import downsample
from hedge_fund.signals.dynamic import parse_strategy

candidates = generate_candidate_pool()
print(f"Candidate templates in universe: {len(candidates)}")

data = json.load(open(HIST_5M))
sampled = {s: downsample(rows[-35000:], 6) for s, rows in data.items()}

scores = []
for name in candidates[:40]:
    pred = parse_strategy(name)
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
    
    tot_train_pnl = sum(r.total_pnl for r in train_results)
    tot_test_pnl = sum(r.total_pnl for r in test_results)
    tot_trades = sum(r.trades for r in train_results) + sum(r.trades for r in test_results)
    tot_wins = sum(r.wins for r in train_results) + sum(r.wins for r in test_results)
    avg_sharpe = sum(r.sharpe for r in test_results) / len(test_results)
    win_rate = (tot_wins / tot_trades) if tot_trades > 0 else 0.0

    scores.append({
        "name": name,
        "train_pnl": tot_train_pnl,
        "test_pnl": tot_test_pnl,
        "trades": tot_trades,
        "sharpe": avg_sharpe,
        "win_rate": win_rate
    })

print("Sample benchmark of 40 strategies:")
for s in scores[:15]:
    print(f"  {s['name']:<25} Trades:{s['trades']:<4} TrainPnl:{s['train_pnl']:>8.1f} TestPnl:{s['test_pnl']:>8.1f} Sharpe:{s['sharpe']:>5.2f} WinRate:{s['win_rate']*100:>5.1f}%")
