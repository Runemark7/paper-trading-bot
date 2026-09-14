"""Per-name discovery eval is cheaper without changing OOS numbers."""
from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.signals.dynamic import (
    _ema_series,
    _sma_series,
    clear_ema_cache,
    clear_sma_cache,
    ema,
    sma,
)
from hedge_fund.backtest.strategies import _atr_series, atr, clear_atr_cache
from hedge_fund.signals.wavetrend import (
    _cached_wavetrend_series,
    clear_wavetrend_cache,
    wavetrend_at,
    wavetrend_series,
    wt_cross_up_os,
)
from hedge_fund.trading.discovery import append_discovery_evaluation, load_discovery_log
from scripts.tournament_engine import _load_qual_history, _window_slices


def _naive_ema(closes: list[float], period: int, i: int) -> float:
    if i < period - 1 or period <= 0:
        return float("nan")
    k = 2.0 / (period + 1)
    val = sum(closes[:period]) / period
    for idx in range(period, i + 1):
        val = closes[idx] * k + val * (1.0 - k)
    return val


def _ohlcv(n: int, start: float = 100.0, drift: float = 0.0002) -> list[list]:
    rows = []
    px = start
    ts = 1_700_000_000_000
    for i in range(n):
        px *= 1.0 + drift
        rows.append([ts + i * 300_000, px, px * 1.01, px * 0.99, px])
    return rows


class EmaSeriesCacheTests(unittest.TestCase):
    def tearDown(self):
        clear_ema_cache()

    def test_cached_ema_matches_naive_recompute(self):
        closes = [100.0 + i * 0.05 for i in range(400)]
        clear_ema_cache()
        series = _ema_series(closes, 21)
        for i in (20, 21, 50, 200, 399):
            naive = _naive_ema(closes, 21, i)
            self.assertEqual(series[i], naive)
            self.assertEqual(ema(closes, 21, i), naive)


class SmaSeriesCacheTests(unittest.TestCase):
    def tearDown(self):
        clear_sma_cache()

    def test_cached_sma_matches_window_sum(self):
        closes = [100.0 + i * 0.05 for i in range(400)]
        clear_sma_cache()
        series = _sma_series(closes, 25)
        for i in (24, 25, 50, 200, 399):
            naive = sum(closes[i - 24 : i + 1]) / 25
            self.assertEqual(series[i], naive)
            self.assertEqual(sma(closes, 25, i), naive)


class AtrSeriesCacheTests(unittest.TestCase):
    def tearDown(self):
        clear_atr_cache()

    def test_cached_atr_matches_per_bar_window(self):
        closes = [100.0 + i * 0.1 for i in range(80)]
        highs = [c + 0.4 for c in closes]
        lows = [c - 0.3 for c in closes]
        period = 14
        clear_atr_cache()
        series = _atr_series(highs, lows, closes, period)
        for i in (13, 14, 20, 40, 79):
            if i < period:
                self.assertTrue(math.isnan(series[i]))
                self.assertTrue(math.isnan(atr(highs, lows, closes, period, i)))
                continue
            trs = []
            for j in range(i - period + 1, i + 1):
                h, l, pc = highs[j], lows[j], closes[j - 1]
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
            naive = sum(trs) / len(trs)
            self.assertEqual(series[i], naive)
            self.assertEqual(atr(highs, lows, closes, period, i), naive)


class WaveTrendSeriesCacheTests(unittest.TestCase):
    def tearDown(self):
        clear_wavetrend_cache()

    def test_full_series_at_i_matches_prefix_rebuild(self):
        closes = [100.0]
        for j in range(1, 120):
            closes.append(closes[-1] * (1.0 + 0.002 * ((-1) ** j)))
        highs = [c * 1.002 for c in closes]
        lows = [c * 0.998 for c in closes]
        clear_wavetrend_cache()
        cached = _cached_wavetrend_series(highs, lows, closes)
        again = _cached_wavetrend_series(highs, lows, closes)
        self.assertIs(cached[0], again[0])
        for i in (60, 90, 119):
            wt1_p, wt2_p = wavetrend_series(highs[: i + 1], lows[: i + 1], closes[: i + 1])
            if math.isnan(cached[0][i]):
                self.assertTrue(math.isnan(wt1_p[i]))
            else:
                self.assertEqual(cached[0][i], wt1_p[i])
            if math.isnan(cached[1][i]):
                self.assertTrue(math.isnan(wt2_p[i]))
            else:
                self.assertEqual(cached[1][i], wt2_p[i])
            a, b = wavetrend_at(highs, lows, closes, i)
            if math.isnan(a):
                self.assertTrue(math.isnan(wt1_p[i]))
            else:
                self.assertEqual(a, wt1_p[i])
            if math.isnan(b):
                self.assertTrue(math.isnan(wt2_p[i]))
            else:
                self.assertEqual(b, wt2_p[i])
            self.assertEqual(
                wt_cross_up_os(closes, highs, lows, i),
                wt_cross_up_os(closes[: i + 1], highs[: i + 1], lows[: i + 1], i),
            )


class HistoryTrimTests(unittest.TestCase):
    def test_load_keeps_only_qual_symbols_and_last_bars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "BTC/USDT": _ohlcv(40, start=100.0),
                "ETH/USDT": _ohlcv(40, start=10.0),
                "SOL/USDT": _ohlcv(40, start=50.0),
            }
            (root / "crypto_history_5m.json").write_text(json.dumps(payload))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                data = _load_qual_history(keep_bars=10)
        self.assertEqual(set(data), {"BTC/USDT", "ETH/USDT"})
        self.assertEqual(len(data["BTC/USDT"]), 10)
        self.assertEqual(data["BTC/USDT"][0], payload["BTC/USDT"][-10])
        self.assertEqual(data["BTC/USDT"][-1], payload["BTC/USDT"][-1])

    def test_trimmed_history_same_window_ohlc_as_full(self):
        full = {
            "BTC/USDT": _ohlcv(90, start=100.0),
            "ETH/USDT": _ohlcv(90, start=10.0),
        }
        keep = 30
        trimmed = {s: rows[-keep:] for s, rows in full.items()}
        full_w = _window_slices(full, window_size=10, n_windows=3, stride=1, warmup_bars=0)
        trim_w = _window_slices(trimmed, window_size=10, n_windows=3, stride=1, warmup_bars=0)
        self.assertEqual(len(full_w), 3)
        for a, b in zip(full_w, trim_w):
            self.assertEqual(set(a), set(b))
            for sym in a:
                c1, h1, l1, warm1, cut1 = a[sym]
                c2, h2, l2, warm2, cut2 = b[sym]
                self.assertEqual(warm1, 0)
                self.assertEqual(warm2, 0)
                self.assertEqual(cut1, cut2)
                self.assertEqual(c1, c2)
                self.assertEqual(h1, h2)
                self.assertEqual(l1, l2)

    def test_default_keep_includes_warmup_prefix(self):
        from hedge_fund.trading.constants import QUAL_N_WINDOWS, QUAL_WARMUP_BARS

        window_bars = 10
        warmup = 7
        keep = window_bars * QUAL_N_WINDOWS + warmup
        extra = 25
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "BTC/USDT": _ohlcv(keep + extra, start=100.0),
                "ETH/USDT": _ohlcv(keep + extra, start=10.0),
                "SOL/USDT": _ohlcv(keep + extra, start=50.0),
            }
            (root / "crypto_history_5m.json").write_text(json.dumps(payload))
            with (
                patch.dict(os.environ, {"PAPER_STATE": str(root)}),
                patch("scripts.tournament_engine.QUAL_WINDOW_BARS", window_bars),
                patch("scripts.tournament_engine.QUAL_N_WINDOWS", QUAL_N_WINDOWS),
                patch("scripts.tournament_engine.QUAL_WARMUP_BARS", warmup),
            ):
                data = _load_qual_history()
        self.assertEqual(set(data), {"BTC/USDT", "ETH/USDT"})
        self.assertEqual(len(data["BTC/USDT"]), keep)
        self.assertEqual(data["BTC/USDT"][0], payload["BTC/USDT"][-keep])
        self.assertEqual(data["BTC/USDT"][-1], payload["BTC/USDT"][-1])
        self.assertEqual(QUAL_WARMUP_BARS, 14 * 24 * 12)

    def test_qual_windows_are_chronological_full_size_holdouts(self):
        from hedge_fund.trading.constants import QUAL_N_WINDOWS

        window_size = 10
        n_windows = QUAL_N_WINDOWS
        data = {
            "BTC/USDT": _ohlcv(window_size * n_windows, start=100.0),
            "ETH/USDT": _ohlcv(window_size * n_windows, start=10.0),
        }
        slices = _window_slices(
            data, window_size=window_size, n_windows=n_windows, stride=1, warmup_bars=0,
        )
        self.assertEqual(len(slices), n_windows)
        prev_last = None
        for i, sl in enumerate(slices):
            closes, _h, _l, warmup, cut = sl["BTC/USDT"]
            self.assertEqual(warmup, 0)
            self.assertEqual(len(closes), window_size)
            self.assertEqual(cut, int(window_size * 0.70))
            if prev_last is not None:
                self.assertGreater(closes[0], prev_last)
            prev_last = closes[-1]
        # Last slice is the most recent tape; first slice is the oldest hold-out.
        self.assertEqual(slices[-1]["BTC/USDT"][0][-1], data["BTC/USDT"][-1][4])
        self.assertEqual(slices[0]["BTC/USDT"][0][0], data["BTC/USDT"][0][4])

    def test_warmup_prefix_from_prior_bars_first_window_partial(self):
        window_size = 10
        n_windows = 3
        pad = 8
        extra = 5
        data = {
            "BTC/USDT": _ohlcv(window_size * n_windows + extra, start=100.0),
            "ETH/USDT": _ohlcv(window_size * n_windows + extra, start=10.0),
        }
        slices = _window_slices(
            data, window_size=window_size, n_windows=n_windows, stride=1, warmup_bars=pad,
        )
        self.assertEqual(len(slices), 3)
        w0 = slices[0]["BTC/USDT"]
        closes0, _h, _l, warm0, cut0 = w0
        self.assertEqual(warm0, extra)  # only 5 bars exist before window 0
        self.assertEqual(len(closes0) - warm0, window_size)
        self.assertEqual(cut0, warm0 + int(window_size * 0.70))
        self.assertEqual(closes0[warm0], data["BTC/USDT"][extra][4])
        w1 = slices[1]["BTC/USDT"]
        closes1, _h1, _l1, warm1, cut1 = w1
        self.assertEqual(warm1, pad)
        self.assertEqual(len(closes1) - warm1, window_size)
        # Warm-up is earlier tape, not future bars.
        self.assertLess(closes1[0], closes1[warm1])
        scored_start1 = extra + window_size
        self.assertEqual(closes1[0], data["BTC/USDT"][scored_start1 - pad][4])
        self.assertEqual(closes1[warm1], data["BTC/USDT"][scored_start1][4])
        self.assertEqual(closes1[-1], data["BTC/USDT"][scored_start1 + window_size - 1][4])


class CompactDiscoveryLogTests(unittest.TestCase):
    def test_append_writes_compact_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                append_discovery_evaluation({
                    "strategy": "sma_stack",
                    "qualified": False,
                    "tested_at": "2026-09-10T00:00:00+00:00",
                })
                raw = (root / "discovery_log.json").read_text()
                self.assertNotIn("\n  ", raw)
                log = load_discovery_log()
        self.assertEqual(log[0]["strategy"], "sma_stack")
        self.assertFalse(log[0]["qualified"])


if __name__ == "__main__":
    unittest.main()
