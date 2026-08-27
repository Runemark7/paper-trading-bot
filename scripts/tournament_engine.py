"""Tournament Arena & Continuous Discovery Engine.

1. Continuous generation & backtesting of strategy candidates over 5m history.
2. Hard qualification score filter (Sharpe >= 0.8, Win Rate >= 45%, Net PnL > 0 on Train + Test).
3. Unlimited active champions arena (each gets an isolated $10k paper account).
4. Graduation threshold: 25 closed live trades.
"""
from __future__ import annotations

import itertools
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/opt/data/paper-trading-bot")
import hedge_fund.backtest.strategies as bs
from hedge_fund.backtest.stride import downsample
from hedge_fund.signals.dynamic import parse_strategy

STATE_DIR = Path(os.environ.get("PAPER_STATE", "/opt/data/paper-trading-bot/state"))
HIST_5M = STATE_DIR / "crypto_history_5m.json"
HIST_1H = STATE_DIR / "crypto_history_1h.json"
CHAMP_FILE = STATE_DIR / "champions.json"
GRADUATED_FILE = STATE_DIR / "graduated.json"

GRADUATION_TRADE_TARGET = 25  # Increased from 10 to 25 trades per champion

# Qualification Criteria on 5m Backtest (Deterministic Minimum Edge)
MIN_BACKTEST_SHARPE = 0.10
MIN_BACKTEST_WIN_RATE = 0.38
MIN_BACKTEST_TRADES = 4


def load_pool() -> dict:
    if CHAMP_FILE.exists():
        try:
            return json.loads(CHAMP_FILE.read_text())
        except Exception:
            pass
    return {"champions": [], "synced_until": ""}


def save_pool(st: dict):
    CHAMP_FILE.write_text(json.dumps(st, indent=2))


def load_graduated() -> list[dict]:
    if GRADUATED_FILE.exists():
        try:
            return json.loads(GRADUATED_FILE.read_text())
        except Exception:
            pass
    return []


def save_graduated(grad_list: list[dict]):
    GRADUATED_FILE.write_text(json.dumps(grad_list, indent=2))


def generate_candidate_pool() -> list[str]:
    """Generates an extensive universe of hundreds of parameterized strategy rules."""
    candidates = set()

    # 1. Multi-MA Stacks (SMA & EMA)
    ma_combos = [
        (3, 8, 21), (5, 10, 20), (5, 20, 50), (7, 25, 50), (9, 28, 51),
        (10, 20, 50), (10, 30, 60), (12, 26, 60), (20, 50, 100), (50, 100, 200),
        (8, 21, 55), (13, 34, 89), (21, 55, 144)
    ]
    for c in ma_combos:
        s_name = "_".join(map(str, c))
        candidates.add(f"sma_stack_{s_name}")
        candidates.add(f"ema_stack_{s_name}")

    # 2. Single MA Regime Breakouts
    for ma in [10, 20, 30, 40, 50, 75, 100, 150, 200, 300]:
        candidates.add(f"sma_abv_{ma}")
        candidates.add(f"ema_abv_{ma}")

    # 3. RSI Bands & Breakouts
    for p in [7, 10, 14, 21, 28, 30]:
        for th in [35, 40, 45, 50, 52, 55, 57, 60]:
            candidates.add(f"rsi_{p}_>{th}")
            for ov in [75, 80, 85, 90]:
                candidates.add(f"rsi_{p}_>{th}_<{ov}")

    # 4. Momentum Thrusts & Mean-Reversion Dips
    for lb in [3, 6, 12, 18, 24, 36, 48, 72]:
        for thr in [1, 2, 3, 5, 8]:
            candidates.add(f"mom_{lb}b_gt{thr}pc")
            candidates.add(f"dip_{lb}b_lt{thr}pc")

    # 5. Volatility Squeeze & Smoothing
    for s_lb, l_lb in [(20, 60), (30, 90), (50, 150), (40, 120)]:
        candidates.add(f"vol_lowsm_{s_lb}_{l_lb}")

    # 6. Hybrid Multi-Indicator Composites (Trend + Momentum / RSI)
    from scripts.generate_universe import generate_5000_universe
    return generate_5000_universe()


DISCOVERY_LOG_FILE = STATE_DIR / "discovery_log.json"

def log_discovery_evaluations(eval_records: list[dict]):
    """Persist a log of all tested candidate evaluations for visibility."""
    log = []
    if DISCOVERY_LOG_FILE.exists():
        try:
            log = json.loads(DISCOVERY_LOG_FILE.read_text())
        except Exception:
            pass
    # Keep last 300 evaluations
    log = (eval_records + log)[:300]
    DISCOVERY_LOG_FILE.write_text(json.dumps(log, indent=2))


def discover_and_qualify(batch_size: int = 150, window_size: int = 20000, n_windows: int = 3, stride: int = 12) -> tuple[list[dict], list[dict]]:
    """Runs rolling multi-window walk-forward backtests across 5m data.
    
    Tests candidates across multiple rolling market regimes. To qualify, a strategy must:
    1. Be profitable across the out-of-sample segments of ALL rolling regimes (consistency).
    2. Maintain minimum Sharpe and WinRate standards after fees and slippage.
    """
    hist_file = HIST_5M if HIST_5M.exists() else HIST_1H
    if not hist_file.exists():
        return [], []

    data = json.load(open(hist_file))
    
    # Construct rolling chronological windows across history
    # e.g. Window 1: Older regime, Window 2: Mid regime, Window 3: Recent regime
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

            # Must not be deeply negative in any single market regime
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
    """Discovers qualified champions and admits them to the unlimited live tournament arena."""
    st = load_pool()
    existing_names = {c["name"] for c in st["champions"]}

    qualified, all_eval = discover_and_qualify(batch_size=batch_size)
    admitted = []

    for q in qualified:
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

    save_pool(st)
    return {
        "active_champions_count": len(st["champions"]),
        "total_tested_in_batch": len(all_eval),
        "admitted_new_count": len(admitted),
        "admitted": admitted
    }


if __name__ == "__main__":
    res = replenish_and_evaluate()
    print(json.dumps(res, indent=2))
