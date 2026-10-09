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

Amendment 2026-10-08: lots exit on stop/TP only, open notional is capped at
equity, and beat-B&H compares daily-equity Sharpe (``GATE_RULES``).
"""
from __future__ import annotations

import bisect
import json
import time
from datetime import datetime, timezone

import hedge_fund.backtest.strategies as bs
from hedge_fund.backtest.stride import downsample
from hedge_fund.paths import state_root
from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.trading.buy_and_hold import (
    buy_and_hold_daily_equity,
    buy_and_hold_window_pnl,
    daily_returns_sharpe,
    equity_returns,
)
from hedge_fund.trading.champions import load_graduated, load_pool, retired_names, save_pool
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
    GATE_RULES,
    PAPER_START_CASH,
    QUAL_N_WINDOWS,
    QUAL_OOS_START_MS,
    QUAL_SEGMENT_MS,
    QUAL_STRIDE,
    QUAL_SYMBOLS,
    QUAL_TIMEFRAME,
    QUAL_TRAIN_BARS,
    QUAL_WARMUP_BARS,
    QUAL_WINDOW_BARS,
    RISK_POLICY,
    TF_MS_5M,
    qual_store_bars,
    TRADE_EVALUATION_LIMIT,
)
from hedge_fund.trading.discovery_guard import (
    lookback_too_expensive_reason,
    ops_fail_record,
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

    The shared live-tape npy wins over ``crypto_history_5m.json`` so a
    worker that just refreshed from prod does not keep evaluating the
    stale JSON copy.

    Qualification windows need the last ``window_size * n_windows`` scored
    bars plus ``QUAL_WARMUP_BARS`` of prior tape (default 23×25920 + 4032
    ≈ 2070 calendar days of hold-outs plus ~14d of indicator seed). Extra
    history and unused symbols (SOL/XRP) are dropped immediately after parse
    so peak RSS is not the full fetch file.
    """
    from hedge_fund.trading.price_history import load_canonical_history

    shared = load_canonical_history()
    if shared:
        keep = (
            QUAL_WINDOW_BARS * QUAL_N_WINDOWS + QUAL_WARMUP_BARS
            if keep_bars is None
            else keep_bars
        )
        if keep > 0:
            for sym in list(shared):
                rows = shared[sym]
                if len(rows) > keep:
                    shared[sym] = rows[-keep:]
        return shared
    path = _hist_qual()
    if not path.exists():
        return None
    keep = (
        QUAL_WINDOW_BARS * QUAL_N_WINDOWS + QUAL_WARMUP_BARS
        if keep_bars is None
        else keep_bars
    )
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


_SPLIT_MEMO: dict[tuple, tuple[list[float], list[float], list[float]]] = {}


def _prepare_sample(w_sample: dict, warmup_len: int) -> dict:
    """Extract OHLC once per window; train/test share arrays + cut indices.

    ``warmup_len`` is the prefix of prior bars (not part of the 90d window).
    ``cut`` is where OOS begins: warmup + 70% of the scored window.
    """
    prepared = {}
    warm = max(0, int(warmup_len))
    for s, rows in w_sample.items():
        closes, highs, lows = _ohlc(rows)
        window_len = max(0, len(rows) - warm)
        cut = warm + int(window_len * 0.70)
        prepared[s] = (closes, highs, lows, warm, cut)
    return prepared


def _tiled_slices(
    data: dict,
    n_windows: int = QUAL_N_WINDOWS,
    *,
    oos_start_ms: int = QUAL_OOS_START_MS,
    segment_ms: int = QUAL_SEGMENT_MS,
    train_bars: int = QUAL_TRAIN_BARS,
    warmup_bars: int = QUAL_WARMUP_BARS,
    tf_ms: int = TF_MS_5M,
) -> list[dict]:
    """Tiled OOS segments anchored to ``oos_start_ms`` (amendment 2026-10-08 18:08).

    Segment ``k`` scores bars with ts in ``[oos_start + k*segment, +segment)``;
    the last segment runs to the tape end. Segments touch end to end and never
    overlap. Each slice is ``warmup_bars`` of indicator seed, then
    ``train_bars`` of train (logged, never gated), then the OOS segment, all
    located by timestamp, so extra history at the front or a new day at the
    back never moves an earlier segment. Raises ``ValueError`` if the tape
    does not cover segment 0's lead-in or does not reach the last segment.
    """
    bs.clear_qual_caches()
    _SPLIT_MEMO.clear()
    n = int(n_windows)
    lead_ms = (int(train_bars) + int(warmup_bars)) * int(tf_ms)
    train_ms = int(train_bars) * int(tf_ms)
    last_start = int(oos_start_ms) + (n - 1) * int(segment_ms)
    stamps: dict[str, list[int]] = {}
    for sym, rows in data.items():
        if not rows:
            raise ValueError(f"{sym}: empty tape")
        ts = [int(r[0]) for r in rows]
        if ts[0] > int(oos_start_ms) - lead_ms:
            raise ValueError(f"{sym}: tape starts after segment 0 lead-in")
        if ts[-1] < last_start:
            raise ValueError(f"{sym}: tape ends before the last OOS segment")
        stamps[sym] = ts
    slices = []
    for k in range(n):
        start = int(oos_start_ms) + k * int(segment_ms)
        end = None if k == n - 1 else start + int(segment_ms)
        w_sample = {}
        for sym, rows in data.items():
            ts = stamps[sym]
            i_lead = bisect.bisect_left(ts, start - lead_ms)
            i_train = bisect.bisect_left(ts, start - train_ms)
            i_oos = bisect.bisect_left(ts, start)
            i_end = len(ts) if end is None else bisect.bisect_left(ts, end)
            closes, highs, lows = _ohlc(rows[i_lead:i_end])
            w_sample[sym] = (closes, highs, lows, i_train - i_lead, i_oos - i_lead)
        slices.append(w_sample)
    return slices


def _window_slices(
    data: dict,
    window_size: int,
    n_windows: int,
    stride: int,
    warmup_bars: int | None = None,
    *,
    tiled: bool = False,
) -> list[dict]:
    """Walk-forward slices. ``tiled=True`` is the production layout.

    ``tiled=True``: ``_tiled_slices`` (contiguous OOS segments anchored to
    ``QUAL_OOS_START_MS``; ``window_size``/``stride``/``warmup_bars`` unused).

    Legacy (tests/fixtures only): end-aligned scored windows, each prefixed
    with prior ``warmup_bars``. Scored span is the last
    ``min(len, window_size * n_windows)`` bars. Warm-up is clipped at tape
    start so the first window may be partial. ``warmup_bars=0`` restores
    isolated-window slices (no pad).
    """
    if tiled:
        return _tiled_slices(data, n_windows)
    # New list identities; drop caches so id() reuse cannot serve stale series.
    bs.clear_qual_caches()
    _SPLIT_MEMO.clear()
    pad = QUAL_WARMUP_BARS if warmup_bars is None else max(0, int(warmup_bars))
    min_available_bars = min(len(data[s]) for s in data)
    scored_span = min(min_available_bars, window_size * n_windows)
    slices = []
    step = scored_span // n_windows
    for w_i in range(n_windows):
        w_sample = {}
        warmup_len: int | None = None
        for s, rows in data.items():
            n = len(rows)
            scored_start = n - scored_span + (w_i * step)
            scored_end = n - scored_span + ((w_i + 1) * step) if w_i < n_windows - 1 else n
            warm_start = max(0, scored_start - pad)
            chunk = rows[warm_start:scored_end]
            w_sample[s] = downsample(chunk, stride) if stride > 1 else chunk
            this_warm = scored_start - warm_start
            if stride > 1:
                this_warm = this_warm // stride
            warmup_len = this_warm if warmup_len is None else min(warmup_len, this_warm)
        slices.append(_prepare_sample(w_sample, warmup_len or 0))
    return slices


def _ohlc(rows: list) -> tuple[list[float], list[float], list[float]]:
    return [r[4] for r in rows], [r[2] for r in rows], [r[3] for r in rows]


def _tape_bounds(tape) -> tuple[list[float], list[float], list[float], int, int] | None:
    """closes, highs, lows, warmup_len, oos_cut from a prepared tape or raw rows."""
    if isinstance(tape, tuple) and len(tape) == 5:
        closes, highs, lows, warmup, cut = tape
        return closes, highs, lows, int(warmup), int(cut)
    if isinstance(tape, tuple) and len(tape) == 4:
        closes, highs, lows, cut = tape
        return closes, highs, lows, 0, int(cut)
    if isinstance(tape, list):
        if not tape:
            return None
        closes, highs, lows = _ohlc(tape)
        cut = int(len(tape) * 0.70)
        return closes, highs, lows, 0, cut
    return None


def _split_tape(tape, train: bool) -> tuple[list[float], list[float], list[float]] | None:
    """Train or test OHLC from a prepared tape or raw row list.

    Prepared tuples are stable for a window-slice batch. Memoize the 70/30
    copies so ATR/SMA/HTF caches keyed by ``id(closes)`` stay valid across
    names (and are not poisoned when CPython reuses a freed list id).
    Qual evals pass the full series into ``backtest`` with ``score_from``;
    this helper still returns the scored-segment copies for B&H and the
    raw-list fallback.
    """
    parts = _tape_bounds(tape)
    if parts is None:
        return None
    closes, highs, lows, warmup, cut = parts
    memo_key = (id(tape) if not isinstance(tape, list) else id(closes), train, warmup, cut)
    hit = _SPLIT_MEMO.get(memo_key)
    if hit is not None:
        return hit
    split = (closes[warmup:cut], highs[warmup:cut], lows[warmup:cut]) if train else (
        closes[cut:], highs[cut:], lows[cut:]
    )
    _SPLIT_MEMO[memo_key] = split
    return split


def _eval_slice(pred, sample: dict, train: bool) -> list:
    results = []
    for _sym, tape in sample.items():
        parts = _tape_bounds(tape)
        if parts is None:
            continue
        closes, highs, lows, warmup, cut = parts
        if train:
            score_from, score_to = warmup, cut
        else:
            score_from, score_to = cut, len(closes)
        if score_to - score_from < 30:
            continue
        try:
            results.append(
                bs.backtest(
                    closes, highs, lows, pred,
                    score_from=score_from, score_to=score_to,
                )
            )
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


def _book_daily_returns(results: list) -> list[float]:
    """Daily returns of the summed per-symbol books (each symbol on its own 10k)."""
    curves = [r.daily_equity for r in results if getattr(r, "daily_equity", None)]
    if not curves:
        return []
    n = min(len(c) for c in curves)
    book = [sum(c[k] for c in curves) for k in range(n)]
    return equity_returns(book)


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
                "daily_returns": [],
                "hold_bars": 0,
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
            "daily_returns": _book_daily_returns(test_results),
            "hold_bars": sum(int(getattr(r, "hold_bars", 0) or 0) for r in test_results),
        })
    return scores


def evaluate_strategy_record(
    name: str,
    window_slices: list[dict],
    *,
    n_windows: int,
    bh_oos_pnl: float | None,
    sma_stack_oos_pnl: float | None,
    bh_daily_sharpe: float | None = None,
) -> dict | None:
    """One name through evaluate_windows + qualification_decision.

    Returns a discovery_log record, or None if the name does not parse.
    Same OOS gates as ``discover_and_qualify``. Picklable for ProcessPool.
    """
    expensive = lookback_too_expensive_reason(name)
    if expensive:
        return ops_fail_record(name, expensive)
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
        bh_daily_sharpe=bh_daily_sharpe,
    )
    tot_wins = sum(int(ws.get("wins") or 0) for ws in window_scores)
    oos_trades = decision["tot_oos_trades"]
    overall_win_rate = (tot_wins / oos_trades) if oos_trades > 0 else 0.0
    tot_hold_bars = sum(int(ws.get("hold_bars") or 0) for ws in window_scores)
    avg_hold_hours = (tot_hold_bars / oos_trades * 5.0 / 60.0) if oos_trades > 0 else None
    dsr = decision.get("daily_sharpe")
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
        "daily_sharpe": None if dsr is None else round(dsr, 3),
        "bh_daily_sharpe": None if bh_daily_sharpe is None else round(bh_daily_sharpe, 3),
        "avg_hold_hours": None if avg_hold_hours is None else round(avg_hold_hours, 2),
        "gate_rules": GATE_RULES,
        "fail_reasons": decision["reasons"],
        "all_windows_nonneg": decision["all_windows_nonneg"],
    }
    if decision["passed"]:
        record["score"] = round(decision["score"], 2)
    return record


def _benchmark_bh_daily_sharpe(window_slices: list[dict]) -> float | None:
    """B&H daily-equity Sharpe over every window's OOS span (same capital base).

    Each symbol starts with ``PAPER_START_CASH`` (like the strategy backtest),
    sampled on the same daily points; returns are concatenated across windows.
    """
    rets: list[float] = []
    seen = False
    for w_sample in window_slices:
        closes_test = _test_closes_by_symbol(w_sample)
        points = buy_and_hold_daily_equity(closes_test, PAPER_START_CASH)
        if len(points) >= 2:
            seen = True
            rets.extend(equity_returns(points))
    return daily_returns_sharpe(rets) if seen else None


def _benchmark_oos(window_slices: list[dict]) -> tuple[float | None, float | None]:
    """OOS B&H P&L and sma_stack P&L. B&H uses 10k per symbol (strategy base)."""
    bh_total = 0.0
    bh_ok = False
    sma_total = 0.0
    sma_ok = False
    sma_pred = parse_strategy(FALLBACK_BENCHMARK)
    for w_sample in window_slices:
        closes_test = _test_closes_by_symbol(w_sample)
        bh = buy_and_hold_window_pnl(
            closes_test, PAPER_START_CASH * max(1, len(closes_test))
        )
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
    data = _load_qual_history(keep_bars=qual_store_bars())
    if not data:
        return [], []

    # Production defaults use the tiled OOS layout. A custom window size or
    # count is a test hook and keeps the legacy end-aligned slices.
    tiled = window_size == QUAL_WINDOW_BARS and n_windows == QUAL_N_WINDOWS
    try:
        window_slices = _window_slices(data, window_size, n_windows, stride, tiled=tiled)
    except ValueError as exc:
        print(f"discover_and_qualify: {exc}", flush=True)
        return [], []
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
        bh_dsr = _benchmark_bh_daily_sharpe(window_slices) if planned else None

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
                bh_daily_sharpe=bh_dsr,
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
    existing_names |= retired_names()

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
    if admitted:
        from hedge_fund.trading.families import enforce_champion_families

        enforce_champion_families()
        st = load_pool()
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
