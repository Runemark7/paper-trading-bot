"""Evaluate the whole strategy library locally and print the gate-v6 table.

Paper only, BTC only. Reads the BTC/USDT 5m tape under ``PAPER_STATE``
(``live_tape/BTC_USDT.npy``, the same store the live cycle and workers
use). ``--fetch`` first downloads or extends it from Binance's public
market-data API (no keys). Nothing is posted to prod and nothing is
admitted: this is the same evaluation a discovery worker runs for a
claimed library name.

    python scripts/run_library.py --fetch
    python scripts/run_library.py --markdown --json /tmp/library.json
    python scripts/run_library.py --sanity        # 200-day SMA vs B&H in 2018 and 2022
    python scripts/run_library.py --before-after  # old stop/TP engine vs signal_exit
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hedge_fund.library.catalog import (  # noqa: E402
    LIBRARY,
    claim_names,
    library_trial_count,
    parse_variant,
    strategy_by_key,
)
from hedge_fund.library.daily import DAY_MS, DailyBars, daily_bars  # noqa: E402
from hedge_fund.library.evaluate import (  # noqa: E402
    dsr_hurdles,
    evaluate_library_record,
    library_data_from_tape,
)
from hedge_fund.paths import state_root  # noqa: E402
from hedge_fund.trading.constants import QUAL_CANONICAL_START_MS  # noqa: E402

PUBLIC_BASES = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api.binance.us",
)
INTERVAL_MS = {"5m": 300_000, "1d": DAY_MS}
SYMBOL = "BTC/USDT"


def _get_json(path: str) -> list:
    last: Exception | None = None
    for base in PUBLIC_BASES:
        for attempt in range(4):
            try:
                req = Request(base + path, headers={"Accept": "application/json", "User-Agent": "paperbot-library"})
                with urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except HTTPError as exc:
                last = exc
                if exc.code in (403, 451):
                    break  # restricted location: try the next base
                time.sleep(2 ** attempt)
            except (URLError, TimeoutError, ValueError) as exc:
                last = exc
                time.sleep(2 ** attempt)
    raise RuntimeError(f"public klines unavailable: {last}")


def fetch_klines(interval: str, start_ms: int, end_ms: int | None = None, *, quiet: bool = False) -> np.ndarray:
    """Closed and forming klines from ``start_ms`` (ascending, deduplicated)."""
    from hedge_fund.trading.live_tape import TAPE_DTYPE, merge_arrays

    step = INTERVAL_MS[interval]
    cursor = int(start_ms)
    out = np.empty(0, dtype=TAPE_DTYPE)
    pages = 0
    while True:
        page = _get_json(f"/api/v3/klines?symbol=BTCUSDT&interval={interval}&startTime={cursor}&limit=1000")
        if not page:
            break
        arr = np.empty(len(page), dtype=TAPE_DTYPE)
        for i, row in enumerate(page):
            arr[i] = (int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]))
        out = merge_arrays(out, arr)
        pages += 1
        last = int(page[-1][0])
        if not quiet and pages % 50 == 0:
            print(f"  fetched {len(out):,} {interval} bars to {_day(last)}", flush=True)
        if len(page) < 1000 or (end_ms is not None and last >= end_ms):
            break
        cursor = last + step
    return out


def ensure_tape(state: Path, fetch: bool) -> np.ndarray:
    from hedge_fund.trading.live_tape import load_array, merge_arrays, save_array

    arr = load_array(SYMBOL, state)
    if fetch:
        since = int(arr["ts"][-1]) if len(arr) else QUAL_CANONICAL_START_MS
        print(f"fetching {SYMBOL} 5m from {_day(since)} (Binance public API)", flush=True)
        arr = merge_arrays(arr, fetch_klines("5m", since))
        save_array(SYMBOL, arr, state)
    if len(arr) == 0:
        raise SystemExit(f"no {SYMBOL} tape under {state}/live_tape; run with --fetch")
    lo = int(np.searchsorted(arr["ts"], np.int64(QUAL_CANONICAL_START_MS), side="left"))
    return arr[lo:]


def _day(ts: int | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _f(value, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _params(record: dict) -> str:
    strategy, params = parse_variant(record["strategy"])
    return ",".join(f"{p.name}={params[p.name]}" for p in strategy.params)


def _row(record: dict) -> dict:
    lib = record["library"]
    gate = record["gate_v4"]
    hold = lib.get("holdout") or {}
    checks = lib["checks"]
    return {
        "name": record["strategy"],
        "citation": lib["citation"],
        "params": _params(record),
        "sharpe": record["daily_sharpe"],
        "trades": record["trades"],
        "pnl": record["test_pnl"],
        "sma": record["sma_stack_oos_pnl"],
        "maxdd": lib["discovery"]["max_drawdown"],
        "expo": lib["discovery"]["exposure"],
        "h_sharpe": hold.get("daily_sharpe"),
        "h_trades": hold.get("trades"),
        "h_pnl": hold.get("pnl"),
        "cost": gate["cost_sharpe"],
        "dsr": gate["dsr"],
        "pbo": gate["pbo"],
        "nb": checks["neighbours"]["value"],
        "bh_sharpe": record["bh_daily_sharpe"],
        "bh_pnl": record["bh_oos_pnl"],
        "bh_maxdd": (lib.get("bh") or {}).get("max_drawdown"),
        "pass": record["qualified"],
        "count_only": lib["count_only"],
        "fails": lib["failing_checks"],
        "dsr_need": lib["dsr"].get("required_annual_sharpe"),
        "dsr_days": lib["dsr"].get("days_needed"),
        "psr_days": lib["dsr"].get("psr_days_needed"),
    }


def print_table(records: list[dict]) -> None:
    rows = [_row(r) for r in records]
    head = (
        f"{'strategy':<18}{'params':<26}{'Sharpe':>7}{'trd':>5}{'OOS P&L':>10}{'sma_stk':>9}"
        f"{'maxDD':>7}{'hSh':>6}{'hTr':>5}{'cost':>6}{'DSR':>7}{'PBO':>6}{'nb':>6}  verdict"
    )
    print(head)
    print("-" * len(head))
    for r in rows:
        verdict = "PASS" if r["pass"] else ("count-only fail" if r["count_only"] else "fail")
        print(
            f"{r['name']:<18}{r['params']:<26}{_f(r['sharpe']):>7}{r['trades']:>5}{_f(r['pnl'], 0):>10}"
            f"{_f(r['sma'], 0):>9}{_f(r['maxdd']):>7}{_f(r['h_sharpe']):>6}{_f(r['h_trades']):>5}"
            f"{_f(r['cost']):>6}{_f(r['dsr'], 3):>7}{_f(r['pbo']):>6}{r['nb']:>6}  {verdict}: {', '.join(r['fails']) or '-'}"
        )
    bh = rows[0] if rows else None
    if bh:
        print(
            f"\nbuy-and-hold (informational, same days and engine): daily Sharpe {_f(bh['bh_sharpe'])}, "
            f"OOS P&L {_f(bh['bh_pnl'], 0)}, max DD {_f(bh['bh_maxdd'])}"
        )


def markdown_table(records: list[dict]) -> str:
    lines = [
        "| Strategy | Citation | Params | OOS daily Sharpe | OOS trades | OOS P&L (sma_stack) | Max DD | "
        "Holdout Sharpe / trades / P&L | Cost-stress Sharpe | DSR | PBO | Neighbours | B&H Sharpe / P&L / max DD (info) | Verdict |",
        "|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|",
    ]
    for record in records:
        r = _row(record)
        if r["pass"]:
            verdict = "**PASS**"
        else:
            tag = "fails only on trade counts: " if r["count_only"] else "fail: "
            verdict = tag + ", ".join(r["fails"])
        lines.append(
            f"| `{r['name']}` | {r['citation']} | {r['params']} | {_f(r['sharpe'])} | {r['trades']} | "
            f"{_f(r['pnl'], 0)} ({_f(r['sma'], 0)}) | {_f(r['maxdd'])} | "
            f"{_f(r['h_sharpe'])} / {_f(r['h_trades'])} / {_f(r['h_pnl'], 0)} | {_f(r['cost'])} | "
            f"{_f(r['dsr'], 3)} | {_f(r['pbo'])} | {r['nb']} | "
            f"{_f(r['bh_sharpe'])} / {_f(r['bh_pnl'], 0)} / {_f(r['bh_maxdd'])} | {verdict} |"
        )
    return "\n".join(lines)


def sanity_drawdowns() -> dict:
    """200-day SMA long-or-flat vs buy-and-hold inside calendar 2018 and 2022.

    Binance daily klines start on 2017-08-17, so the 200-day SMA exists only
    from early March 2018; the strategy is flat before that.
    """
    from hedge_fund.library.engine import run

    raw = fetch_klines("1d", int(datetime(2017, 8, 1, tzinfo=timezone.utc).timestamp() * 1000), quiet=True)
    full = daily_bars(raw["ts"], raw["open"], raw["high"], raw["low"], raw["close"])
    strat = strategy_by_key("sma200")
    signal = strat.signal(full, strat.center)
    out: dict = {"first_day": _day(full.ts[0]), "last_day": _day(full.ts[-1])}
    first_sma = int(np.argmax(np.isfinite(_sma_values(full, 200))))
    out["sma200_defined_from"] = _day(full.ts[first_sma])
    for year in (2018, 2022):
        a = full.index_of(int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))
        b = full.index_of(int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))
        s = run(full, signal, start=a, end=b)
        h = run(full, np.ones(len(full), dtype=bool), start=a, end=b)
        out[str(year)] = {
            "sma200_max_drawdown": round(_mdd(s.equity), 4),
            "bh_max_drawdown": round(_mdd(h.equity), 4),
            "sma200_return": round(float(s.equity[-1] / s.equity[0] - 1.0), 4),
            "bh_return": round(float(h.equity[-1] / h.equity[0] - 1.0), 4),
            "sma200_trades": len(s.trades),
            "sma200_days_long": int(s.days_long),
            "days": int(b - a),
        }
    return out


def _sma_values(bars: DailyBars, n: int) -> np.ndarray:
    from hedge_fund.library.indicators import sma

    return sma(bars.close, n)


def _mdd(points: np.ndarray) -> float:
    peak = np.maximum.accumulate(points)
    return float(np.max((peak - points) / peak)) if points.shape[0] else 0.0


def before_after(data) -> dict:
    """Same 200-day-SMA entry: old rm_v1 stop/TP exits vs signal_exit, discovery span."""
    import hedge_fund.backtest.strategies as bs
    from hedge_fund.library.evaluate import discovery_metrics

    strat = strategy_by_key("sma200")
    bars = data.discovery
    a, b = data.discovery_bounds()
    signal = strat.signal(bars, strat.center)
    closes, highs, lows = list(bars.close), list(bars.high), list(bars.low)

    def entry(cl, i, _sig=signal):
        return bool(_sig[i])

    old = bs.backtest(closes, highs, lows, entry, score_from=a, score_to=b, bars_per_day=1)
    old_eq = np.asarray(old.daily_equity, dtype=np.float64)
    old_rets = old_eq[1:] / old_eq[:-1] - 1.0
    from hedge_fund.trading.buy_and_hold import daily_returns_sharpe

    new = discovery_metrics(data, strat, strat.center)
    return {
        "old_rm_v1_stop_tp": {
            "trades": old.trades,
            "avg_hold_days": round(old.hold_bars / old.trades, 2) if old.trades else None,
            "return": round(old.total_pnl / 10_000.0, 4),
            "max_drawdown": round(old.max_drawdown, 4),
            "daily_sharpe": round(daily_returns_sharpe(list(old_rets)), 3),
            "exits": old.exits,
            "max_notional_frac": round(old.max_notional_frac, 3),
        },
        "signal_exit": {
            "trades": new.trades,
            "avg_hold_days": None if new.avg_hold_days is None else round(new.avg_hold_days, 2),
            "return": round(new.total_return, 4),
            "max_drawdown": round(new.max_dd, 4),
            "daily_sharpe": round(new.daily_sharpe, 3),
            "exits": new.exits,
            "exposure": round(new.exposure, 4),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate the strategy library (gate v6, paper only)")
    ap.add_argument("--state", default=str(state_root()), help="PAPER_STATE directory with live_tape/")
    ap.add_argument("--fetch", action="store_true", help="download / extend the BTC 5m tape first")
    ap.add_argument("--json", default="", help="write every record to this file")
    ap.add_argument("--markdown", action="store_true", help="also print a markdown table")
    ap.add_argument("--sanity", action="store_true", help="2018 / 2022 drawdown check on Binance daily data")
    ap.add_argument("--before-after", action="store_true", help="old stop/TP engine vs signal_exit")
    args = ap.parse_args(argv)

    state = Path(args.state)
    tape = ensure_tape(state, args.fetch)
    data = library_data_from_tape(tape)
    print(
        f"tape {SYMBOL}: {len(tape):,} 5m bars {_day(int(tape['ts'][0]))} -> {_day(int(tape['ts'][-1]))}; "
        f"daily discovery bars {len(data.discovery)}, holdout bars "
        f"{len(data.holdout) if data.holdout is not None else 'missing'}",
        flush=True,
    )
    trials = library_trial_count()
    print(f"library: {len(LIBRARY)} strategies, {trials} configurations = DSR trial count\n", flush=True)
    records = [evaluate_library_record(name, data) for name in claim_names()]
    print_table(records)
    n_obs = int(records[0]["gate_v4"]["oos_return_n"] or 0)
    for n_trials in (1, trials):
        hurdle = dsr_hurdles({"sharpe": 0.0, "n": n_obs, "skew": 0.0, "kurtosis": 3.0}, n_trials)
        print(
            f"{'PSR' if n_trials == 1 else 'DSR'} >= 0.95 with N={n_trials} over {n_obs} daily returns needs an "
            f"annualized daily Sharpe of about {hurdle['required_annual_sharpe']} (normal returns)."
        )
    if args.markdown:
        print("\n" + markdown_table(records))
    if args.json:
        Path(args.json).write_text(json.dumps(records, indent=2, default=str))
        print(f"\nwrote {args.json}")
    if args.before_after:
        print("\nbefore/after, 200-day SMA entry on the discovery span:")
        print(json.dumps(before_after(data), indent=2))
    if args.sanity:
        print("\nsanity: 200-day SMA vs buy-and-hold (Binance daily klines):")
        print(json.dumps(sanity_drawdowns(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
