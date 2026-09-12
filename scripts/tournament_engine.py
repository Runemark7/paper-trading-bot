"""Tournament Arena & Continuous Discovery Engine.

1. Walk-forward backtests on **5m** OHLCV (same tape as live TradingLoop).
   4h history must not admit champions.
2. Hard qualification filter (hedge_fund.trading.constants): OOS-only score,
   >= 30 OOS trades, OOS Sharpe >= 0.30, OOS beats buy-and-hold and
   sma_stack after fees. A single skipped/empty/negative window is a
   diagnostic (all_windows_nonneg), not a fail reason. Risk policy: rm_v1.
3. No live-slot cap: every 5m-qualified name not already pooled or
   graduated is admitted. Static universe size (~40–120) is the compiled
   list bound; auto-refill appends a handful of never-tested names to
   discovery_extended.json when leftovers run dry (pending queue sidecar).
4. Graduation: TRADE_EVALUATION_LIMIT (80) closed paper trades vs B&H.
5. When invoked (Windows worker, or live_cycle with DISCOVERY_ON_CYCLE=1)
   evaluates a leftover *slice* (max names + wall-clock budget, rotating
   cursor) and appends discovery_log.json after each name — not after the
   full leftover list. A non-qualified eval parks that name forever (no
   24h retest cooldown). Empty eligible triggers one refill batch.
   Cluster live_cycle defaults to skipping this module.

Qualification uses hedge_fund.backtest.strategies with rm_v1 stops/fees,
not fast_quant or fee-free SimBroker.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import hedge_fund.backtest.strategies as bs
from hedge_fund.backtest.stride import downsample
from hedge_fund.paths import state_root
from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.trading.buy_and_hold import buy_and_hold_window_pnl
from hedge_fund.trading.champions import load_graduated, load_pool, save_pool
from hedge_fund.trading.discovery import (
    append_discovery_evaluation,
    clear_in_flight,
    failed_discovery_names,
    load_cursor,
    load_discovery_log,
    save_cursor,
    select_cycle_batch,
    tested_discovery_names,
    write_in_flight,
)
from hedge_fund.trading.constants import (
    DISCOVER_CYCLE_MAX_NAMES,
    DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
    PAPER_START_CASH,
    QUAL_N_WINDOWS,
    QUAL_STRIDE,
    QUAL_SYMBOLS,
    QUAL_TIMEFRAME,
    QUAL_WINDOW_BARS,
    RISK_POLICY,
    TRADE_EVALUATION_LIMIT,
)
from hedge_fund.trading.qualify import (
    oos_admission_score,
    qualification_decision,
)
from hedge_fund.trading.refill import discovery_universe, maybe_refill_discovery
from hedge_fund.trading.universe import untested_candidates

FALLBACK_BENCHMARK = "sma_stack"


def _hist_qual():
    return state_root() / f"crypto_history_{QUAL_TIMEFRAME}.json"


def generate_candidate_pool() -> list[str]:
    """Static 5m universe plus persisted refill names (no MTF/MFI)."""
    return discovery_universe()


def log_discovery_evaluations(eval_records: list[dict]):
    """Append finished evaluations immediately (newest-first, capped)."""
    from hedge_fund.trading.discovery import append_discovery_evaluations

    append_discovery_evaluations(eval_records)


def _load_qual_history(keep_bars: int | None = None) -> dict | None:
    """5m tape only. A 4h-only state dir must not admit anyone.

    Qualification windows only need the last ``window_size * n_windows`` bars
    (default 3×25920). Extra history and unused symbols (SOL/XRP) are dropped
    immediately after parse so peak RSS is not the full fetch file.
    """
    path = _hist_qual()
    if not path.exists():
        return None
    keep = QUAL_WINDOW_BARS * QUAL_N_WINDOWS if keep_bars is None else keep_bars
    try:
        with path.open() as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    out = {}
    for sym in QUAL_SYMBOLS:
        rows = data.pop(sym, None)
        if not rows:
            continue
        if keep > 0 and len(rows) > keep:
            rows = rows[-keep:]
        out[sym] = rows
    data.clear()
    return out or None


def _prepare_sample(w_sample: dict) -> dict:
    """Extract OHLC once per window; train/test share the arrays + a cut index."""
    prepared = {}
    for s, rows in w_sample.items():
        closes, highs, lows = _ohlc(rows)
        cut = int(len(rows) * 0.70)
        prepared[s] = (closes, highs, lows, cut)
    return prepared


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
        slices.append(_prepare_sample(w_sample))
    return slices


def _ohlc(rows: list) -> tuple[list[float], list[float], list[float]]:
    return [r[4] for r in rows], [r[2] for r in rows], [r[3] for r in rows]


def _split_tape(tape, train: bool) -> tuple[list[float], list[float], list[float]] | None:
    """Train or test OHLC from a prepared tape or raw row list."""
    if isinstance(tape, tuple) and len(tape) == 4:
        closes, highs, lows, cut = tape
    elif isinstance(tape, list):
        if not tape:
            return None
        closes, highs, lows = _ohlc(tape)
        cut = int(len(tape) * 0.70)
    else:
        return None
    if train:
        return closes[:cut], highs[:cut], lows[:cut]
    return closes[cut:], highs[cut:], lows[cut:]


def _eval_slice(pred, sample: dict, train: bool) -> list:
    results = []
    for _sym, tape in sample.items():
        series = _split_tape(tape, train)
        if series is None or len(series[0]) < 30:
            continue
        try:
            results.append(bs.backtest(series[0], series[1], series[2], pred))
        except Exception:
            continue
    return results


def _test_closes_by_symbol(sample: dict) -> dict[str, list[float]]:
    out = {}
    for sym, tape in sample.items():
        series = _split_tape(tape, train=False)
        if series is None:
            continue
        out[sym] = series[0]
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


def evaluate_strategy_record(
    name: str,
    window_slices: list[dict],
    *,
    n_windows: int,
    bh_oos_pnl: float | None,
    sma_stack_oos_pnl: float | None,
) -> dict | None:
    """One name through evaluate_windows + qualification_decision.

    Returns a discovery_log record, or None if the name does not parse.
    Same OOS gates as ``discover_and_qualify``. Picklable for ProcessPool.
    """
    try:
        pred = parse_strategy(name)
    except Exception:
        return None
    window_scores = evaluate_windows(pred, window_slices)
    decision = qualification_decision(
        window_scores,
        expected_windows=n_windows,
        bh_oos_pnl=bh_oos_pnl,
        sma_stack_oos_pnl=sma_stack_oos_pnl,
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
        "bh_oos_pnl": None if bh_oos_pnl is None else round(bh_oos_pnl, 2),
        "sma_stack_oos_pnl": None if sma_stack_oos_pnl is None else round(sma_stack_oos_pnl, 2),
        "fail_reasons": decision["reasons"],
        "all_windows_nonneg": decision["all_windows_nonneg"],
    }
    if decision["passed"]:
        record["score"] = round(decision["score"], 2)
    return record


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


def _leftover_batch(
    universe: list[str],
    blocked_names: set[str],
    batch_size: int | None = None,
) -> list[str]:
    """All remaining untested names, still skipping near-duplicates of blocked.

    Default is no sample: the leftover list is the drain queue. ``batch_size``
    is a test hook only (prefix of leftovers). There is no product-rule random
    sample of 30. Production still budgets how many of these run per cycle.
    """
    leftovers = untested_candidates(blocked_names, universe)
    if batch_size is None:
        return leftovers
    return leftovers[:batch_size]


def discover_and_qualify(
    batch_size: int | None = None,
    window_size: int = QUAL_WINDOW_BARS,
    n_windows: int = QUAL_N_WINDOWS,
    stride: int = QUAL_STRIDE,
    *,
    max_names: int | None = None,
    time_budget_seconds: float | None = None,
    cooldown_seconds: int | None = None,
    now: datetime | None = None,
) -> tuple[list[dict], list[dict]]:
    """Walk-forward qualification on 5m BTC/ETH. 4h-only history does not admit.

    Each invocation evaluates a leftover slice of never-tested names, rotating
    from the persisted cursor. Names with any non-qualified discovery_log row
    are parked forever (``cooldown_seconds`` is ignored). When eligible is
    empty (or fewer than this cycle's cap), auto-refill appends the next
    recipe handful to ``discovery_extended.json`` so the drain keeps moving.
    Stops after ``max_names`` or ``time_budget_seconds`` so live_cycle can
    still run ``run_isolated`` in the same 300s tick. ``batch_size`` is a
    leftover-prefix test hook, not a random sample of 30.
    """
    data = _load_qual_history(keep_bars=window_size * n_windows)
    if not data:
        return [], []

    window_slices = _window_slices(data, window_size, n_windows, stride)
    del data
    if len(window_slices) != n_windows:
        return [], []

    st = load_pool()
    grad = load_graduated()
    blocked = {c["name"] for c in st.get("champions", [])}.union({g["name"] for g in grad})

    universe = generate_candidate_pool()
    leftovers = _leftover_batch(universe, blocked, batch_size)
    cap = DISCOVER_CYCLE_MAX_NAMES if max_names is None else max_names
    if batch_size is not None:
        cap = min(cap, batch_size)
    budget = (
        DISCOVER_CYCLE_TIME_BUDGET_SECONDS
        if time_budget_seconds is None
        else time_budget_seconds
    )
    cursor = load_cursor()
    log = load_discovery_log()
    planned, rotated = select_cycle_batch(
        leftovers,
        log,
        max_names=cap,
        cooldown_seconds=cooldown_seconds,
        now=now,
        cursor_name=cursor.get("next_name"),
    )
    added: list[str] = []
    # Refill vs the live slice (DISCOVER_CYCLE_MAX_NAMES), not this
    # invocation's test-hook cap. eligible < cap is empty / about-to-be
    # empty: persist the next handful now so following 1/90s cycles stay
    # fed. This cycle still evals ``cap``.
    if len(rotated) < DISCOVER_CYCLE_MAX_NAMES:
        taken = (
            set(universe)
            | set(leftovers)
            | set(blocked)
            | tested_discovery_names(log)
            | failed_discovery_names(log)
        )
        added = maybe_refill_discovery(
            eligible_count=len(rotated),
            cap=DISCOVER_CYCLE_MAX_NAMES,
            taken_names=taken,
        )
        if added:
            leftovers = list(leftovers) + [n for n in added if n not in leftovers]
            planned, rotated = select_cycle_batch(
                leftovers,
                log,
                max_names=cap,
                cooldown_seconds=cooldown_seconds,
                now=now,
                cursor_name=cursor.get("next_name"),
            )

    qualified = []
    all_evaluated = []
    completed: list[str] = []
    remaining = list(planned)
    started_mono = time.monotonic()
    if planned:
        write_in_flight(
            remaining,
            current=None,
            remaining=remaining,
            completed=completed,
            batch_size=len(planned),
        )
    try:
        bh_oos, sma_oos = _benchmark_oos(window_slices) if planned else (None, None)

        for i, name in enumerate(planned):
            if i > 0 and (time.monotonic() - started_mono) >= budget:
                break
            remaining = planned[i + 1 :]
            write_in_flight(
                [name] + remaining,
                current=name,
                remaining=remaining,
                completed=completed,
                batch_size=len(planned),
            )
            record = evaluate_strategy_record(
                name,
                window_slices,
                n_windows=n_windows,
                bh_oos_pnl=bh_oos,
                sma_stack_oos_pnl=sma_oos,
            )
            if record is None:
                completed.append(name)
                continue
            if record.get("qualified"):
                qualified.append(record)
            append_discovery_evaluation(record)
            all_evaluated.append(record)
            completed.append(name)
            write_in_flight(
                remaining,
                current=None,
                remaining=remaining,
                completed=completed,
                batch_size=len(planned),
            )

        qualified.sort(key=lambda x: x["score"], reverse=True)
        return qualified, all_evaluated
    finally:
        done = set(completed)
        next_name = None
        for n in rotated:
            if n not in done:
                next_name = n
                break
        if planned or added:
            save_cursor(
                next_name,
                last_evaluated=completed,
                last_count=len(completed),
                last_refill=added,
            )
        clear_in_flight()


def replenish_and_evaluate(
    batch_size: int | None = None,
    **discover_kwargs,
) -> dict:
    """Qualify leftover untested names; admit every new name not already pooled or graduated.

    Default takes one cycle budget of leftovers (not a 30-name sample and not
    the entire leftover list). ``batch_size`` is a leftover-prefix test hook.
    """
    st = load_pool()
    grad_list = load_graduated()
    existing_names = {c["name"] for c in st["champions"]}.union(
        {g["name"] for g in grad_list}
    )

    qualified, all_eval = discover_and_qualify(batch_size=batch_size, **discover_kwargs)
    admitted = []
    for q in qualified:
        name = q["strategy"]
        if name not in existing_names:
            admitted_at = datetime.now(timezone.utc).isoformat()
            st["champions"].append({
                "name": name,
                "closed": 0,
                "pnl": 0.0,
                "wins": 0,
                "sharpe_qual": q["sharpe"],
                "winrate_qual": q["win_rate_pct"],
                "admitted_at": admitted_at,
                "champion_since": admitted_at,
                "source": "5m_qualification_filter",
                "timeframe": QUAL_TIMEFRAME,
                "risk_policy": RISK_POLICY,
            })
            existing_names.add(name)
            admitted.append(q)

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
