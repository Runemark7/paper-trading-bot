"""Tournament Arena & Continuous Discovery Engine.

1. Walk-forward backtests on **5m** OHLCV (same tape as live TradingLoop).
   4h history must not admit champions.
2. Hard qualification filter (hedge_fund.trading.constants): OOS-only score,
   every window's test PnL >= 0, >= 30 OOS trades, OOS Sharpe >= 0.30,
   OOS beats buy-and-hold and sma_stack after fees. Risk policy: rm_v1.
3. Arena capacity MAX_ACTIVE_CHAMPIONS (20); replenish only into free slots.
4. Graduation: TRADE_EVALUATION_LIMIT (80) closed paper trades vs B&H.

Qualification uses hedge_fund.backtest.strategies with rm_v1 stops/fees,
not fast_quant or fee-free SimBroker.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

import hedge_fund.backtest.strategies as bs
from hedge_fund.backtest.stride import downsample
from hedge_fund.paths import state_root
from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.trading.buy_and_hold import buy_and_hold_window_pnl
from hedge_fund.trading.champions import load_graduated, load_pool, save_pool
from hedge_fund.trading.constants import (
    DISCOVER_BATCH_SIZE,
    MAX_ACTIVE_CHAMPIONS,
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    PAPER_START_CASH,
    QUAL_N_WINDOWS,
    QUAL_STRIDE,
    QUAL_SYMBOLS,
    QUAL_TIMEFRAME,
    QUAL_WINDOW_BARS,
    RISK_POLICY,
    TRADE_EVALUATION_LIMIT,
)
from hedge_fund.trading.universe import generate_universe, near_duplicate_key

FALLBACK_BENCHMARK = "sma_stack"


def _hist_qual():
    return state_root() / f"crypto_history_{QUAL_TIMEFRAME}.json"


def _discovery_log_file():
    return state_root() / "discovery_log.json"


def generate_candidate_pool() -> list[str]:
    """Explicit 5m universe (no daily()/h1()/m5() or MFI)."""
    return generate_universe()


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


def oos_admission_score(tot_test_pnl: float, avg_sharpe: float) -> float:
    """Admit ranking uses OOS/test only — train PnL never boosts the score."""
    return tot_test_pnl + (avg_sharpe * 10.0)


def qualification_decision(
    windows: list[dict],
    *,
    expected_windows: int,
    bh_oos_pnl: float | None,
    sma_stack_oos_pnl: float | None,
    min_sharpe: float = MIN_BACKTEST_SHARPE,
    min_trades: int = MIN_BACKTEST_TRADES,
) -> dict:
    """Pure OOS gate. Failed/skipped windows fail 'all windows non-negative'."""
    tot_test_pnl = sum(float(w.get("test_pnl") or 0.0) for w in windows)
    tot_oos_trades = sum(int(w.get("test_trades") or 0) for w in windows)
    sharpes = [float(w.get("sharpe") or 0.0) for w in windows] or [0.0]
    avg_sharpe = sum(sharpes) / len(sharpes)
    tot_train_pnl = sum(float(w.get("train_pnl") or 0.0) for w in windows)
    reasons: list[str] = []

    if len(windows) != expected_windows:
        reasons.append(f"windows {len(windows)} != expected {expected_windows}")

    all_windows_nonneg = True
    for i, w in enumerate(windows):
        skipped = bool(w.get("skipped") or w.get("failed"))
        test_pnl = w.get("test_pnl")
        test_trades = int(w.get("test_trades") or 0)
        if skipped or test_pnl is None or test_trades < 1 or float(test_pnl) < 0:
            all_windows_nonneg = False
            reasons.append(f"window[{i}] failed/skipped/neg/empty")

    if not all_windows_nonneg:
        reasons.append("not all windows non-negative")
    if tot_oos_trades < min_trades:
        reasons.append(f"oos_trades {tot_oos_trades} < {min_trades}")
    if avg_sharpe < min_sharpe:
        reasons.append(f"oos_sharpe {avg_sharpe:.2f} < {min_sharpe}")
    if bh_oos_pnl is None:
        reasons.append("buy-and-hold missing")
    elif tot_test_pnl <= bh_oos_pnl:
        reasons.append(f"oos_pnl {tot_test_pnl:.2f} <= bh {bh_oos_pnl:.2f}")
    if sma_stack_oos_pnl is None:
        reasons.append("sma_stack missing")
    elif tot_test_pnl <= sma_stack_oos_pnl:
        reasons.append(f"oos_pnl {tot_test_pnl:.2f} <= sma_stack {sma_stack_oos_pnl:.2f}")

    passed = not reasons
    return {
        "passed": passed,
        "reasons": reasons,
        "tot_test_pnl": tot_test_pnl,
        "tot_train_pnl": tot_train_pnl,
        "tot_oos_trades": tot_oos_trades,
        "avg_sharpe": avg_sharpe,
        "all_windows_nonneg": all_windows_nonneg,
        "score": oos_admission_score(tot_test_pnl, avg_sharpe),
        "bh_oos_pnl": bh_oos_pnl,
        "sma_stack_oos_pnl": sma_stack_oos_pnl,
    }


def _load_qual_history() -> dict | None:
    """5m tape only. A 4h-only state dir must not admit anyone."""
    path = _hist_qual()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    out = {}
    for sym in QUAL_SYMBOLS:
        rows = data.get(sym)
        if rows:
            out[sym] = rows
    return out or None


def _window_slices(data: dict, window_size: int, n_windows: int, stride: int) -> list[dict]:
    min_available_bars = min(len(data[s]) for s in data)
    total_span = min(min_available_bars, window_size * n_windows)
    slices = []
    step = total_span // n_windows
    for w_i in range(n_windows):
        start_idx = -(total_span - (w_i * step))
        end_idx = start_idx + step if w_i < n_windows - 1 else None
        w_sample = {}
        for s, rows in data.items():
            chunk = rows[start_idx:end_idx]
            w_sample[s] = downsample(chunk, stride) if stride > 1 else chunk
        slices.append(w_sample)
    return slices


def _ohlc(rows: list) -> tuple[list[float], list[float], list[float]]:
    return [r[4] for r in rows], [r[2] for r in rows], [r[3] for r in rows]


def _eval_slice(pred, sample: dict, train: bool) -> list:
    results = []
    for _sym, rows in sample.items():
        closes, highs, lows = _ohlc(rows)
        n = len(rows)
        cut = int(n * 0.70)
        series = (closes[:cut], highs[:cut], lows[:cut]) if train else (closes[cut:], highs[cut:], lows[cut:])
        if len(series[0]) < 30:
            continue
        try:
            results.append(bs.backtest(series[0], series[1], series[2], pred))
        except Exception:
            continue
    return results


def _test_closes_by_symbol(sample: dict) -> dict[str, list[float]]:
    out = {}
    for sym, rows in sample.items():
        cut = int(len(rows) * 0.70)
        out[sym] = [r[4] for r in rows[cut:]]
    return out


def evaluate_windows(pred, window_slices: list[dict]) -> list[dict]:
    """Run train/test backtests per window. Empty/failed windows are marked skipped."""
    scores = []
    for w_sample in window_slices:
        train_results = _eval_slice(pred, w_sample, train=True)
        test_results = _eval_slice(pred, w_sample, train=False)
        if not train_results or not test_results:
            scores.append({
                "train_pnl": 0.0,
                "test_pnl": None,
                "test_trades": 0,
                "trades": 0,
                "wins": 0,
                "sharpe": 0.0,
                "skipped": True,
                "failed": True,
            })
            continue
        w_train_pnl = sum(r.total_pnl for r in train_results)
        w_test_pnl = sum(r.total_pnl for r in test_results)
        w_test_trades = sum(r.trades for r in test_results)
        w_trades = w_test_trades  # OOS only in the window record
        w_wins = sum(r.wins for r in test_results)
        w_sharpe = sum(r.sharpe for r in test_results) / len(test_results)
        skipped = w_test_trades < 1
        scores.append({
            "train_pnl": w_train_pnl,
            "test_pnl": w_test_pnl,
            "test_trades": w_test_trades,
            "trades": w_trades,
            "wins": w_wins,
            "sharpe": w_sharpe,
            "skipped": skipped,
            "failed": skipped,
        })
    return scores


def _benchmark_oos(window_slices: list[dict]) -> tuple[float | None, float | None]:
    bh_total = 0.0
    bh_ok = False
    sma_total = 0.0
    sma_ok = False
    sma_pred = parse_strategy(FALLBACK_BENCHMARK)
    for w_sample in window_slices:
        closes_test = _test_closes_by_symbol(w_sample)
        bh = buy_and_hold_window_pnl(closes_test, PAPER_START_CASH)
        if bh is not None:
            bh_total += bh
            bh_ok = True
        sma_res = _eval_slice(sma_pred, w_sample, train=False)
        if sma_res:
            sma_total += sum(r.total_pnl for r in sma_res)
            sma_ok = True
    return (bh_total if bh_ok else None), (sma_total if sma_ok else None)


def _pick_batch(universe: list[str], blocked_names: set[str], batch_size: int) -> list[str]:
    taken_keys = {near_duplicate_key(n) for n in blocked_names}
    eligible = []
    for cand in universe:
        if cand in blocked_names:
            continue
        key = near_duplicate_key(cand)
        if key in taken_keys:
            continue
        eligible.append(cand)
        taken_keys.add(key)  # skip intra-universe twins in the same sweep
    if not eligible:
        return []
    k = min(batch_size, len(eligible))
    return random.sample(eligible, k)


def discover_and_qualify(
    batch_size: int | None = None,
    window_size: int = QUAL_WINDOW_BARS,
    n_windows: int = QUAL_N_WINDOWS,
    stride: int = QUAL_STRIDE,
) -> tuple[list[dict], list[dict]]:
    """Walk-forward qualification on 5m BTC/ETH. 4h-only history does not admit."""
    if batch_size is None:
        batch_size = DISCOVER_BATCH_SIZE
    data = _load_qual_history()
    if not data:
        return [], []

    window_slices = _window_slices(data, window_size, n_windows, stride)
    if len(window_slices) != n_windows:
        return [], []

    st = load_pool()
    grad = load_graduated()
    blocked = {c["name"] for c in st.get("champions", [])}.union({g["name"] for g in grad})

    universe = generate_candidate_pool()
    sample_batch = _pick_batch(universe, blocked, batch_size)
    qualified = []
    all_evaluated = []
    bh_oos, sma_oos = _benchmark_oos(window_slices)

    for name in sample_batch:
        try:
            pred = parse_strategy(name)
        except Exception:
            continue

        window_scores = evaluate_windows(pred, window_slices)
        decision = qualification_decision(
            window_scores,
            expected_windows=n_windows,
            bh_oos_pnl=bh_oos,
            sma_stack_oos_pnl=sma_oos,
        )
        tot_wins = sum(int(ws.get("wins") or 0) for ws in window_scores)
        oos_trades = decision["tot_oos_trades"]
        overall_win_rate = (tot_wins / oos_trades) if oos_trades > 0 else 0.0

        record = {
            "strategy": name,
            "tested_at": datetime.now(timezone.utc).isoformat(),
            "timeframe": QUAL_TIMEFRAME,
            "risk_policy": RISK_POLICY,
            "train_pnl": round(decision["tot_train_pnl"], 2),
            "test_pnl": round(decision["tot_test_pnl"], 2),
            "sharpe": round(decision["avg_sharpe"], 2),
            "win_rate_pct": round(overall_win_rate * 100, 1),
            "trades": oos_trades,
            "regimes_tested": len(window_scores),
            "qualified": decision["passed"],
            "bh_oos_pnl": None if bh_oos is None else round(bh_oos, 2),
            "sma_stack_oos_pnl": None if sma_oos is None else round(sma_oos, 2),
            "fail_reasons": decision["reasons"],
        }
        all_evaluated.append(record)

        if decision["passed"]:
            record["score"] = round(decision["score"], 2)
            qualified.append(record)

    log_discovery_evaluations(all_evaluated)
    qualified.sort(key=lambda x: x["score"], reverse=True)
    return qualified, all_evaluated


def replenish_and_evaluate(batch_size: int | None = None) -> dict:
    """Admit 5m-qualified names only into free slots (pool cap 20)."""
    if batch_size is None:
        batch_size = DISCOVER_BATCH_SIZE
    st = load_pool()
    existing_names = {c["name"] for c in st["champions"]}
    needed = MAX_ACTIVE_CHAMPIONS - len(st["champions"])
    if needed <= 0:
        return {
            "active_champions_count": len(st["champions"]),
            "evaluation_limit": TRADE_EVALUATION_LIMIT,
            "total_tested_in_batch": 0,
            "admitted_new_count": 0,
            "admitted": [],
            "reason": f"pool full ({len(st['champions'])}/{MAX_ACTIVE_CHAMPIONS})",
        }

    qualified, all_eval = discover_and_qualify(batch_size=batch_size)
    admitted = []
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
                "source": "5m_qualification_filter",
                "timeframe": QUAL_TIMEFRAME,
                "risk_policy": RISK_POLICY,
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
        "admitted": admitted,
    }


if __name__ == "__main__":
    res = replenish_and_evaluate()
    print(json.dumps(res, indent=2))
