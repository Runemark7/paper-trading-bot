"""Per-name discovery eval is cheaper without changing OOS numbers."""
from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.signals.dynamic import _ema_series, clear_ema_cache, ema
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
        full_w = _window_slices(full, window_size=10, n_windows=3, stride=1)
        trim_w = _window_slices(trimmed, window_size=10, n_windows=3, stride=1)
        self.assertEqual(len(full_w), 3)
        for a, b in zip(full_w, trim_w):
            self.assertEqual(set(a), set(b))
            for sym in a:
                c1, h1, l1, cut1 = a[sym]
                c2, h2, l2, cut2 = b[sym]
                self.assertEqual(cut1, cut2)
                self.assertEqual(c1, c2)
                self.assertEqual(h1, h2)
                self.assertEqual(l1, l2)


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
