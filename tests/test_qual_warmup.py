"""Walk-forward indicator warm-up: score OOS only, seed from prior bars."""
from __future__ import annotations

import math
import unittest

from hedge_fund.backtest.strategies import backtest
from hedge_fund.signals.dynamic import ema, parse_strategy
from hedge_fund.signals.htf import BARS_PER_H4, htf_close_above_ma
from hedge_fund.trading.constants import (
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    QUAL_N_WINDOWS,
    QUAL_TAPE_BARS_MEASURED,
    QUAL_WARMUP_BARS,
    QUAL_WARMUP_DAYS,
    QUAL_WINDOW_BARS,
    QUAL_WINDOW_DAYS,
    qual_keep_bars,
)
from scripts.tournament_engine import _benchmark_oos, _window_slices, evaluate_windows


def _trend(n: int, start: float = 100.0, drift: float = 0.001) -> tuple[list[float], list[float], list[float]]:
    closes = []
    px = start
    for _ in range(n):
        px *= 1.0 + drift
        closes.append(px)
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    return closes, highs, lows


def _ohlcv(n: int, start: float = 100.0, drift: float = 0.0002) -> list[list]:
    rows = []
    px = start
    ts = 1_700_000_000_000
    for i in range(n):
        px *= 1.0 + drift
        rows.append([ts + i * 300_000, px, px * 1.01, px * 0.99, px])
    return rows


class WarmupConstantTests(unittest.TestCase):
    def test_pad_covers_h4_ema_70_and_does_not_eat_90d_windows(self):
        h4_ema_70_bars = 70 * BARS_PER_H4
        self.assertEqual(QUAL_WARMUP_DAYS, 14)
        self.assertEqual(QUAL_WARMUP_BARS, 14 * 24 * 12)
        self.assertGreaterEqual(QUAL_WARMUP_BARS, h4_ema_70_bars)
        self.assertGreaterEqual(QUAL_WARMUP_BARS, h4_ema_70_bars + 47)
        self.assertEqual(QUAL_WINDOW_DAYS, 90)
        self.assertEqual(QUAL_WINDOW_BARS, 25920)
        self.assertEqual(QUAL_N_WINDOWS, 23)
        self.assertLess(QUAL_WARMUP_BARS, QUAL_WINDOW_BARS)
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(
            qual_keep_bars(),
            QUAL_WINDOW_BARS * QUAL_N_WINDOWS + QUAL_WARMUP_BARS,
        )
        self.assertLessEqual(qual_keep_bars(), QUAL_TAPE_BARS_MEASURED)
        self.assertEqual(qual_keep_bars(n_windows=8, warmup_bars=0), QUAL_WINDOW_BARS * 8)


class ScoreFromTests(unittest.TestCase):
    def test_warmup_trades_are_not_counted(self):
        n = 400
        closes, highs, lows = _trend(n)
        # True only on the prefix — those bars must not count when score_from=200.
        pred = lambda c, i=None, **_k: (0 if i is None else i) < 80
        cold = backtest(closes, highs, lows, pred)
        self.assertGreater(cold.trades, 0)
        warm = backtest(closes, highs, lows, pred, score_from=200)
        self.assertEqual(warm.trades, 0)
        self.assertEqual(warm.total_pnl, 0.0)
        self.assertEqual(MIN_BACKTEST_TRADES, 30)

    def test_oos_sees_warm_ema_not_nan(self):
        period = 50
        score_from = 200
        closes, highs, lows = _trend(400)
        sliced = ema(closes[score_from:], period, 0)
        seeded = ema(closes, period, score_from)
        self.assertTrue(math.isnan(sliced))  # NaN on cold OOS start
        self.assertFalse(math.isnan(seeded))
        self.assertGreater(seeded, 0.0)

    def test_htf_ready_at_oos_start_with_pad(self):
        period = 24
        need = period * BARS_PER_H4
        score_from = need
        closes, highs, lows = _trend(need + 200, drift=0.0004)
        self.assertTrue(
            htf_close_above_ma(
                closes, score_from, bars_per=BARS_PER_H4, period=period, kind="ema"
            )
        )
        # Cold slice has no completed HTF EMA.
        cold = htf_close_above_ma(
            closes[score_from:], 0, bars_per=BARS_PER_H4, period=period, kind="ema"
        )
        self.assertFalse(cold)


class WindowWarmupEvalTests(unittest.TestCase):
    def test_oos_metrics_use_cut_not_warmup_bars(self):
        window_size = 120
        n_windows = 2
        pad = 80
        data = {
            "BTC/USDT": _ohlcv(window_size * n_windows + pad, start=100.0),
            "ETH/USDT": _ohlcv(window_size * n_windows + pad, start=10.0, drift=0.00015),
        }
        slices = _window_slices(
            data, window_size=window_size, n_windows=n_windows, stride=1, warmup_bars=pad,
        )
        closes, _h, _l, warmup, cut = slices[0]["BTC/USDT"]
        self.assertEqual(warmup, pad)
        self.assertEqual(len(closes) - warmup, window_size)
        oos_len = len(closes) - cut
        self.assertEqual(oos_len, window_size - int(window_size * 0.70))
        bh, sma = _benchmark_oos(slices)
        self.assertIsNotNone(bh)
        # B&H is marked on OOS closes only (not the pad).
        self.assertEqual(len(closes[cut:]), oos_len)
        pred = parse_strategy("sma_stack")
        scores = evaluate_windows(pred, slices)
        self.assertEqual(len(scores), 2)
        for row in scores:
            self.assertIn("test_trades", row)
            self.assertIn("test_pnl", row)
            # Train is diagnostic; OOS trades are the gate input.
            self.assertEqual(row["trades"], row["test_trades"])
        self.assertIsNotNone(sma)
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)

    def test_warmup_only_signals_are_not_oos_trades(self):
        window_size = 120
        pad = 80
        data = {
            "BTC/USDT": _ohlcv(window_size + pad, start=100.0),
            "ETH/USDT": _ohlcv(window_size + pad, start=10.0, drift=0.00015),
        }
        slices = _window_slices(
            data, window_size=window_size, n_windows=1, stride=1, warmup_bars=pad,
        )
        _c, _h, _l, warmup, cut = slices[0]["BTC/USDT"]
        self.assertEqual(warmup, pad)
        pred = lambda c, i=None, **_k: (0 if i is None else i) < warmup
        scores = evaluate_windows(pred, slices)
        self.assertEqual(len(scores), 1)
        self.assertEqual(scores[0]["test_trades"], 0)
        self.assertEqual(scores[0]["trades"], 0)
        self.assertEqual(scores[0]["test_pnl"], 0.0)


if __name__ == "__main__":
    unittest.main()
