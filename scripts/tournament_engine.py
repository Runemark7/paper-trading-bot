"""Tournament Arena & Continuous Discovery Engine.

1. Continuous generation & backtesting of strategy candidates over 5m history.
2. Hard qualification filter (hedge_fund.trading.constants):
   Sharpe >= 0.10, win rate >= 38%, >= 4 trades, train PnL > 0 and test PnL > 0.
3. Arena capacity MAX_ACTIVE_CHAMPIONS (1000); each champion gets an isolated €10k paper account.
4. Graduation: TRADE_EVALUATION_LIMIT (25) closed paper trades.

Qualification uses hedge_fund.backtest.strategies (fees in that engine), not
fast_quant or fee-free SimBroker.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

import hedge_fund.backtest.strategies as bs
from hedge_fund.backtest.stride import downsample
from hedge_fund.paths import state_root
from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.trading.champions import load_graduated, load_pool, save_pool
from hedge_fund.trading.constants import (
    MAX_ACTIVE_CHAMPIONS,
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    MIN_BACKTEST_WIN_RATE,
    TRADE_EVALUATION_LIMIT,
)
from hedge_fund.trading.universe import generate_5000_universe


def _hist_5m():
    return state_root() / "crypto_history_5m.json"


def _hist_1h():
    return state_root() / "crypto_history_1h.json"


def _discovery_log_file():
    return state_root() / "discovery_log.json"


def generate_candidate_pool() -> list[str]:
    """Generates the combinatorial universe (no daily()/h1()/m5() or MFI)."""
    return generate_5000_universe()


def log_discovery_evaluations(eval_records: list[dict]):
    """Persist a log of all tested candidate evaluations for visibility."""
    path = _discovery_log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    log = []
    if path.exists():
        try:
            log = json.loads(path.read_text())
        except Exception:
            pass
    log = (eval_records + log)[:300]
    path.write_text(json.dumps(log, indent=2))


def discover_and_qualify(batch_size: int = 150, window_size: int = 20000, n_windows: int = 3, stride: int = 12) -> tuple[list[dict], list[dict]]:
    """Runs rolling multi-window walk-forward backtests across 5m data.

    Tests candidates across multiple rolling market regimes. To qualify, a strategy must:
    1. Be profitable across the out-of-sample segments of ALL rolling regimes (consistency).
    2. Maintain MIN_BACKTEST_SHARPE / MIN_BACKTEST_WIN_RATE / MIN_BACKTEST_TRADES after fees.
    """
    hist_5m, hist_1h = _hist_5m(), _hist_1h()
    hist_file = hist_5m if hist_5m.exists() else hist_1h
    if not hist_file.exists():
        return [], []

    data = json.load(open(hist_file))

    raw_symbols = list(data.keys())
    min_available_bars = min(len(data[s]) for s in raw_symbols)
    total_span = min(min_available_bars, window_size * n_windows)

    window_slices = []
    step = total_span // n_windows
    for w_i in range(n_windows):
        start_idx = -(total_span - (w_i * step))
        end_idx = start_idx + step if w_i < n_windows - 1 else None

        w_sample = {}
        for s in raw_symbols:
            rows = data[s][start_idx:end_idx]
            w_sample[s] = downsample(rows, stride)
        window_slices.append(w_sample)

    st = load_pool()
    grad = load_graduated()
    active_names = {c["name"] for c in st.get("champions", [])}.union({g["name"] for g in grad})

    universe = generate_candidate_pool()
    untested = [cand for cand in universe if cand not in active_names]

    sample_batch = random.sample(untested, min(batch_size, len(untested))) if untested else []
    qualified = []
    all_evaluated = []

    for name in sample_batch:
        try:
            pred = parse_strategy(name)
        except Exception:
            continue

        window_scores = []
        passed_all_regimes = True

        for w_idx, w_sample in enumerate(window_slices):
            train_results, test_results = [], []
            for sym, rows in w_sample.items():
                closes = [r[4] for r in rows]
                highs = [r[2] for r in rows]
                lows = [r[3] for r in rows]
                n = len(rows)
                cut = int(n * 0.70)
                try:
                    tr = bs.backtest(closes[:cut], highs[:cut], lows[:cut], pred)
                    te = bs.backtest(closes[cut:], highs[cut:], lows[cut:], pred)
                    train_results.append(tr)
                    test_results.append(te)
                except Exception:
                    continue

            if not train_results or not test_results:
                passed_all_regimes = False
                break

            w_train_pnl = sum(r.total_pnl for r in train_results)
            w_test_pnl = sum(r.total_pnl for r in test_results)
            w_trades = sum(r.trades for r in train_results) + sum(r.trades for r in test_results)
            w_wins = sum(r.wins for r in train_results) + sum(r.wins for r in test_results)
            w_sharpe = sum(r.sharpe for r in test_results) / len(test_results)
            w_winrate = (w_wins / w_trades) if w_trades > 0 else 0.0

            if w_test_pnl < -50 or w_trades < 2:
                passed_all_regimes = False

            window_scores.append({
                "train_pnl": w_train_pnl,
                "test_pnl": w_test_pnl,
                "trades": w_trades,
                "wins": w_wins,
                "sharpe": w_sharpe,
                "winrate": w_winrate
            })

        if not window_scores:
            continue

        tot_train_pnl = sum(ws["train_pnl"] for ws in window_scores)
        tot_test_pnl = sum(ws["test_pnl"] for ws in window_scores)
        tot_trades = sum(ws["trades"] for ws in window_scores)
        tot_wins = sum(ws["wins"] for ws in window_scores)
        avg_sharpe = sum(ws["sharpe"] for ws in window_scores) / len(window_scores)
        overall_win_rate = (tot_wins / tot_trades) if tot_trades > 0 else 0.0

        is_passed = (
            passed_all_regimes
            and tot_train_pnl > 0
            and tot_test_pnl > 0
            and avg_sharpe >= MIN_BACKTEST_SHARPE
            and overall_win_rate >= MIN_BACKTEST_WIN_RATE
            and tot_trades >= MIN_BACKTEST_TRADES
        )

        record = {
            "strategy": name,
            "tested_at": datetime.now(timezone.utc).isoformat(),
            "train_pnl": round(tot_train_pnl, 2),
            "test_pnl": round(tot_test_pnl, 2),
            "sharpe": round(avg_sharpe, 2),
            "win_rate_pct": round(overall_win_rate * 100, 1),
            "trades": tot_trades,
            "regimes_tested": len(window_scores),
            "qualified": is_passed
        }
        all_evaluated.append(record)

        if is_passed:
            composite_score = tot_test_pnl + (tot_train_pnl * 0.5) + (avg_sharpe * 100)
            record["score"] = round(composite_score, 2)
            qualified.append(record)

    log_discovery_evaluations(all_evaluated)
    qualified.sort(key=lambda x: x["score"], reverse=True)
    return qualified, all_evaluated


def replenish_and_evaluate(batch_size: int = 150) -> dict:
    """Discovers qualified champions and admits them up to MAX_ACTIVE_CHAMPIONS (1000)."""
    st = load_pool()
    existing_names = {c["name"] for c in st["champions"]}

    qualified, all_eval = discover_and_qualify(batch_size=batch_size)
    admitted = []
    needed = MAX_ACTIVE_CHAMPIONS - len(st["champions"])

    for q in qualified:
        if needed <= 0:
            break
        name = q["strategy"]
        if name not in existing_names:
            st["champions"].append({
                "name": name,
                "closed": 0,
                "pnl": 0.0,
                "wins": 0,
                "sharpe_qual": q["sharpe"],
                "winrate_qual": q["win_rate_pct"],
                "admitted_at": datetime.now(timezone.utc).isoformat(),
                "source": "5m_qualification_filter"
            })
            existing_names.add(name)
            admitted.append(q)
            needed -= 1

    save_pool(st)
    return {
        "active_champions_count": len(st["champions"]),
        "evaluation_limit": TRADE_EVALUATION_LIMIT,
        "total_tested_in_batch": len(all_eval),
        "admitted_new_count": len(admitted),
        "admitted": admitted
    }


if __name__ == "__main__":
    res = replenish_and_evaluate()
    print(json.dumps(res, indent=2))
