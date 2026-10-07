"""Live paper tape: full qual history, identical latest-bar signals, all champions."""
from __future__ import annotations

import ast
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from hedge_fund.data.binance import Candle
from hedge_fund.web.candles import CHUNK_SIZE, TF_MS

REPO = Path(__file__).resolve().parents[1]
STRATEGY = "h1_ema_abv_60"
DEEP = "h1_ema_abv_60&mom_18b_gt2pc&sma_abv_30&rsi_14_>50"


def _bars(n: int, *, start_ts: int = 1_700_000_000_000, close0: float = 100.0, step: float = 0.5):
    out = []
    for i in range(n):
        c = close0 + i * step
        out.append(
            Candle(
                ts=start_ts + i * TF_MS,
                open=c - 0.1,
                high=c + 1.0,
                low=c - 1.0,
                close=c,
                volume=1.0,
            )
        )
    return out


class _Pages:
    """In-memory venue. Records requested limits. Respects since + limit."""

    def __init__(self, bars: list[Candle]):
        self.bars = list(bars)
        self.limits: list[int] = []

    def fetch_klines(self, symbol, timeframe="5m", limit=200, since=None):
        self.limits.append(int(limit))
        if since is None:
            return list(self.bars[-int(limit):])
        return [b for b in self.bars if b.ts >= int(since)][: int(limit)]

    def fetch_price(self, symbol):
        return float(self.bars[-1].close)


class MergeTests(unittest.TestCase):
    def test_overlap_keeps_newer_close_and_dedups(self):
        from hedge_fund.trading.live_tape import candles_to_array, merge_arrays

        old = candles_to_array(_bars(4, close0=1.0, step=1.0))
        fresh = _bars(4, close0=1.0, step=1.0)
        # Overlap the last two timestamps; bar index 2 close changes; one new bar.
        fresh = fresh[2:]
        fresh[0] = Candle(
            ts=fresh[0].ts, open=9, high=9, low=9, close=9.5, volume=3,
        )
        newer = _bars(1, start_ts=fresh[-1].ts + TF_MS, close0=20.0, step=0)
        merged = merge_arrays(old, candles_to_array(fresh + newer))
        ts = [int(t) for t in merged["ts"]]
        self.assertEqual(ts, sorted(set(ts)))
        self.assertEqual(len(merged), 5)
        # index 2 was the first overlapped row (original bars[2]).
        self.assertEqual(float(merged[2]["close"]), 9.5)
        self.assertEqual(float(merged[-1]["close"]), 20.0)
        self.assertEqual(float(merged[0]["close"]), 1.0)


class BackfillTests(unittest.TestCase):
    def test_chunked_backfill_dedups_and_keeps_newest_target(self):
        from hedge_fund.trading.live_tape import ensure_live_tape, load_array

        src = _Pages(_bars(500))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_live_tape(source=src, state_dir=root, target_bars=450, max_pages=20)
            for sym in ("BTC/USDT", "ETH/USDT"):
                arr = load_array(sym, root)
                ts = [int(t) for t in arr["ts"]]
                self.assertEqual(ts, sorted(set(ts)))
                self.assertEqual(len(arr), 450)
                self.assertEqual(ts[0], src.bars[-450].ts)
                self.assertEqual(ts[-1], src.bars[-1].ts)
        self.assertTrue(src.limits)
        self.assertTrue(all(n <= CHUNK_SIZE for n in src.limits))
        self.assertNotIn(500, src.limits)

    def test_second_pass_appends_only_the_overlap_and_new_bars(self):
        from hedge_fund.trading.live_tape import ensure_symbol, load_array, save_array, candles_to_array

        start = _bars(11)
        grown = list(start) + _bars(5, start_ts=start[-1].ts + TF_MS, close0=200.0, step=1.0)
        # Forming-bar update on the previous last close.
        grown[10] = Candle(
            ts=start[10].ts, open=1, high=8, low=1, close=7.25, volume=2,
        )
        src = _Pages(grown)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_array("BTC/USDT", candles_to_array(start), root)
            calls_before = len(src.limits)
            ensure_symbol(
                "BTC/USDT", source=src, state_dir=root, target_bars=100, max_pages=10,
            )
            arr = load_array("BTC/USDT", root)
            ts = [int(t) for t in arr["ts"]]
            self.assertEqual(len(ts), len(set(ts)))
            self.assertEqual(len(arr), 16)
            self.assertEqual(float(arr[10]["close"]), 7.25)
            self.assertEqual(int(arr[-1]["ts"]), grown[-1].ts)
            # Not a cold 500-bar walk: a handful of chunked pages.
            self.assertLess(len(src.limits) - calls_before, 8)
            self.assertTrue(all(n <= CHUNK_SIZE for n in src.limits))

    def test_partial_backfill_resumes(self):
        from hedge_fund.trading.live_tape import ensure_symbol, load_array

        src = _Pages(_bars(500))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_symbol(
                "BTC/USDT", source=src, state_dir=root, target_bars=450, max_pages=1,
            )
            partial = load_array("BTC/USDT", root)
            self.assertGreater(len(partial), 0)
            self.assertLess(len(partial), 450)
            ensure_symbol(
                "BTC/USDT", source=src, state_dir=root, target_bars=450, max_pages=10,
            )
            done = load_array("BTC/USDT", root)
            self.assertEqual(len(done), 450)
            self.assertEqual(int(done[-1]["ts"]), src.bars[-1].ts)

    def test_json_bootstrap_survives_a_dead_venue(self):
        from hedge_fund.trading.live_tape import ensure_live_tape, load_array

        def row(i, px):
            return [1_700_000_000_000 + i * TF_MS, px, px + 1, px - 1, px]

        payload = {
            "BTC/USDT": [row(i, 100 + i) for i in range(12)],
            "ETH/USDT": [row(i, 10 + i) for i in range(12)],
        }

        class Dead:
            def fetch_klines(self, *args, **kwargs):
                raise RuntimeError("binance down")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crypto_history_5m.json").write_text(json.dumps(payload))
            ensure_live_tape(source=Dead(), state_dir=root, target_bars=12, max_pages=5)
            btc = load_array("BTC/USDT", root)
            eth = load_array("ETH/USDT", root)
            self.assertEqual(len(btc), 12)
            self.assertEqual(len(eth), 12)
            self.assertEqual(int(btc[-1]["ts"]), payload["BTC/USDT"][-1][0])

    def test_only_btc_and_eth(self):
        from hedge_fund.trading.live_tape import SYMBOLS, ensure_symbol

        self.assertEqual(SYMBOLS, ("BTC/USDT", "ETH/USDT"))
        with self.assertRaises(ValueError):
            ensure_symbol("SOL/USDT", target_bars=10, max_pages=1)


class SignalParityTests(unittest.TestCase):
    def _rising_rows(self, n: int, *, start: float, drift: float = 0.0008):
        rows = []
        px = start
        ts = 1_700_000_000_000
        for i in range(n):
            px *= 1.0 + drift
            rows.append([ts + i * TF_MS, px * 0.999, px * 1.002, px * 0.998, px])
        return rows

    def _candles_from_rows(self, rows):
        return [
            Candle(ts=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
                   close=float(r[4]), volume=1.0)
            for r in rows
        ]

    def test_h1_ema_abv_60_live_path_matches_backtest_and_is_nonempty(self):
        from hedge_fund.signals.dynamic import clear_ema_cache, ema, eval_predicate, parse_strategy
        from hedge_fund.signals.htf import (
            BARS_PER_H1,
            clear_htf_cache,
            completed_htf_count,
        )
        from hedge_fund.signals.htf import _htf_closes_all
        from hedge_fund.signals.momentum import compute_signal
        from hedge_fund.trading.constants import (
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_N_WINDOWS,
            QUAL_WARMUP_BARS,
            QUAL_WINDOW_BARS,
            live_signal_eval_bars,
        )
        from hedge_fund.trading.live_tape import (
            candles_to_array,
            save_array,
            signal_candles,
            strategy_signal_flags,
        )
        from scripts.tournament_engine import _window_slices

        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)
        self.assertEqual(QUAL_WARMUP_BARS, 4032)
        self.assertEqual(live_signal_eval_bars(), QUAL_WINDOW_BARS + QUAL_WARMUP_BARS)

        extra = 2500
        n = live_signal_eval_bars() + extra
        btc_rows = self._rising_rows(n, start=100.0)
        eth_rows = self._rising_rows(n, start=10.0, drift=0.0005)
        data = {"BTC/USDT": btc_rows, "ETH/USDT": eth_rows}
        slices = _window_slices(
            data, window_size=QUAL_WINDOW_BARS, n_windows=1, stride=1,
        )
        bt_closes, bt_highs, bt_lows, _warm, _cut = slices[-1]["BTC/USDT"]
        tail_closes = [float(r[4]) for r in btc_rows[-live_signal_eval_bars():]]
        self.assertEqual(list(bt_closes), tail_closes)
        self.assertEqual(len(bt_closes), live_signal_eval_bars())

        def h1_ema(closes, period=60):
            clear_ema_cache()
            clear_htf_cache()
            i = len(closes) - 1
            n_complete = completed_htf_count(i, BARS_PER_H1)
            htf = _htf_closes_all(list(closes), BARS_PER_H1)
            return ema(htf, period, n_complete - 1)

        self.assertEqual(h1_ema(bt_closes), h1_ema(tail_closes))
        # Warm-up + h1 lookback reseeds EMA. It is not the qualification window.
        short_n = QUAL_WARMUP_BARS + 60 * BARS_PER_H1
        self.assertLess(short_n, len(tail_closes))
        self.assertNotEqual(h1_ema(tail_closes[-short_n:]), h1_ema(tail_closes))

        candles = self._candles_from_rows(btc_rows)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_array("BTC/USDT", candles_to_array(candles), root)
            loaded = signal_candles("BTC/USDT", state_dir=root)
        self.assertEqual(len(loaded), live_signal_eval_bars())
        self.assertEqual([c.close for c in loaded], tail_closes)

        flags = strategy_signal_flags(loaded, STRATEGY)
        self.assertEqual(len(flags), len(loaded))
        self.assertTrue(any(flags), "h1_ema_abv_60 never true on the live slice")
        self.assertTrue(flags[-1])

        starved = strategy_signal_flags(loaded[-300:], STRATEGY)
        self.assertEqual(len(starved), 300)
        self.assertFalse(any(starved))

        sig = compute_signal(loaded, "BTC/USDT", "5m", strategy=STRATEGY)
        pred = parse_strategy(STRATEGY)
        bt_flag = bool(eval_predicate(
            pred, [c.close for c in loaded], len(loaded) - 1,
            highs=[c.high for c in loaded], lows=[c.low for c in loaded],
        ))
        bt_window = bool(eval_predicate(
            pred, list(bt_closes), len(bt_closes) - 1,
            highs=list(bt_highs), lows=list(bt_lows),
        ))
        self.assertEqual(sig.direction == "long", bt_flag)
        self.assertEqual(bt_flag, flags[-1])
        self.assertEqual(bt_window, flags[-1])

    def test_last_bar_sma_matches_full_series(self):
        from hedge_fund.signals.dynamic import _sma_series, clear_sma_cache, sma

        closes = [float(i) * 1.01 for i in range(1, 800)]
        clear_sma_cache()
        fast = sma(closes, 50)
        clear_sma_cache()
        slow = _sma_series(closes, 50)[-1]
        self.assertEqual(fast, slow)
        clear_sma_cache()
        last = sma(closes, 50)
        mid = sma(closes, 50, 200)
        self.assertEqual(mid, _sma_series(closes, 50)[200])
        self.assertEqual(sma(closes, 50), last)


class ChampionEvalTests(unittest.TestCase):
    def test_every_champion_runs_even_past_graduation_bar(self):
        from hedge_fund.trading import run_isolated as ri
        from hedge_fund.trading.constants import TRADE_EVALUATION_LIMIT, live_signal_eval_bars

        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)
        names = [f"h1_ema_abv_60&n{i}" for i in range(120)]
        self.assertGreater(len(names), TRADE_EVALUATION_LIMIT)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                with patch.object(ri, "load_pool", return_value={
                    "champions": [{"name": n, "closed": 0, "pnl": 0.0, "wins": 0} for n in names],
                }):
                    got = ri.active_strategies()
        self.assertEqual(got, names)

        fake_loop = MagicMock()
        fake_loop.run_cycle.return_value = []
        fake_broker = MagicMock()
        fake_broker.to_state.return_value = {}
        fake_risk = MagicMock()
        fake_risk.peak_equity = 10_000.0
        store = MagicMock()
        store.load_account_state.return_value = None

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ri, "CcxtSource") as src_cls, \
                 patch.object(ri, "load_pool", return_value={
                     "champions": [{"name": n} for n in names],
                 }), \
                 patch.object(ri, "TradeStore", return_value=store), \
                 patch.object(ri, "CalibrationStore"), \
                 patch.object(ri, "PaperBroker", return_value=fake_broker), \
                 patch.object(ri, "RiskManager", return_value=fake_risk), \
                 patch.object(ri, "TradingLoop", return_value=fake_loop) as loop_cls, \
                 patch("sys.argv", ["run_isolated", "--cycles", "1", "--state", tmp]):
                src = src_cls.return_value
                src.fetch_price.return_value = 100.0
                src.fetch_klines.return_value = [object()]
                ri.main()

        self.assertEqual(loop_cls.call_count, 120)
        seen = [c.kwargs["strategy"] for c in loop_cls.call_args_list]
        self.assertEqual(seen, names)
        for call in loop_cls.call_args_list:
            self.assertEqual(call.kwargs["kline_limit"], live_signal_eval_bars())
            self.assertGreater(call.kwargs["kline_limit"], 300)
            self.assertIsNone(call.kwargs.get("regime"))
        self.assertEqual(fake_loop.run_cycle.call_count, 120)

    def test_cached_market_does_not_retruncate_to_300(self):
        from hedge_fund.trading.live_tape import CachedMarket

        bars = _bars(500)
        market = CachedMarket({"BTC/USDT": 1.0}, {"BTC/USDT": bars})
        got = market.fetch_klines("BTC/USDT", "5m", limit=300)
        self.assertEqual(len(got), 500)
        self.assertIs(got[-1], bars[-1])


class HealthTests(unittest.TestCase):
    def test_status_and_summary_report_bars_and_last_time(self):
        from hedge_fund.trading.live_tape import candles_to_array, save_array
        from hedge_fund.web.server import build_summary
        from hedge_fund.web.status import build_status

        bars = _bars(8, start_ts=1_720_000_000_000)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_array("BTC/USDT", candles_to_array(bars), root)
            save_array("ETH/USDT", candles_to_array(bars[:5]), root)
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                status = build_status()
                summary = build_summary()
        for payload in (status["running_now"]["live_history"], summary["live_history"]):
            self.assertTrue(payload["paper_only"])
            self.assertEqual(payload["timeframe"], "5m")
            btc = payload["symbols"]["BTC/USDT"]
            eth = payload["symbols"]["ETH/USDT"]
            self.assertEqual(btc["bars"], 8)
            self.assertEqual(eth["bars"], 5)
            self.assertTrue(btc["last_bar_time"])
            self.assertTrue(eth["last_bar_time"])
            self.assertIn("2024", btc["last_bar_time"])
            self.assertTrue(btc["short"])
        self.assertEqual(
            status["in_progress"]["tournament"]["champions_per_cycle"],
            1,  # empty pool falls back to sma_stack
        )

    def test_reports_use_the_live_loader(self):
        for name in ("live_report.py", "live_cycle_report.py"):
            text = (REPO / "scripts" / name).read_text()
            self.assertNotIn("limit=300", text)
            self.assertIn("load_cycle_klines", text)
        src = (REPO / "hedge_fund" / "trading" / "run_isolated.py").read_text()
        self.assertNotIn("limit=300", src)
        tree = ast.parse(src)
        calls = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
        self.assertIn("run_cycle", calls)


class RuntimeTests(unittest.TestCase):
    def test_signal_pass_for_all_champions_fits_in_a_cycle(self):
        from hedge_fund.signals.momentum import compute_signal
        from hedge_fund.trading.constants import CYCLE_INTERVAL_SECONDS, live_signal_eval_bars

        n = live_signal_eval_bars()
        candles = _bars(n, close0=20_000.0, step=2.0)
        # One composite close to the starved h1 30/50/60 champions, both symbols.
        t0 = time.perf_counter()
        for _ in range(120):
            for sym in ("BTC/USDT", "ETH/USDT"):
                sig = compute_signal(candles, sym, "5m", strategy=DEEP)
                self.assertIn(sig.direction, ("long", "flat"))
        elapsed = time.perf_counter() - t0
        print(
            f"[live signal runtime] 120 champions x 2 symbols x {n} bars "
            f"= {elapsed:.2f}s",
            flush=True,
        )
        self.assertLess(elapsed, min(60.0, CYCLE_INTERVAL_SECONDS))


if __name__ == "__main__":
    unittest.main()
