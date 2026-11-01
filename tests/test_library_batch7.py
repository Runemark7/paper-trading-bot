"""Batch-7 daily signals: on/off, warm-up, no look-ahead, and a trade on the tape.

The 132 names ahead of these keys stay where they are. Batch 7 is what a
library-mode claim leases after those 132 are tested. The shared score
start stays 2018-12-01. The three batch-6 names that previously never
traded are corrected in place and have to fire on this same tape.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from hedge_fund.library.catalog import LIBRARY, grid_params, signal_ready, strategy_by_key
from hedge_fund.library.daily import DAY_MS, DailyBars
from hedge_fund.library.engine import run
from hedge_fund.library.indicators import (
    HILBERT_READY,
    chande_momentum,
    efficiency_ratio,
    linreg_r2,
    linreg_slope,
    linreg_stderr,
    linreg_value,
    sma,
    sma_skip_leading,
    stochastic_momentum_index,
    true_strength_index,
)
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC, a Wednesday
BATCH6_END = 132
NEW = (
    "smi", "tsi_sig", "cmo_sig", "mama", "itrend", "ebsw", "eratio", "rsquared",
    "seband", "kagi", "renko", "morning", "hammer", "rising3", "three_out",
    "twobar", "td_combo", "di_cross", "woodie", "fibhold", "saucer", "gator_wake",
)
FIXED = ("td_cd", "pierce", "soldiers")
CENTERS = {
    "smi": {"n": 10},
    "tsi_sig": {"signal": 7},
    "cmo_sig": {"signal": 9},
    "mama": {"fast": 50},
    "itrend": {"alpha": 7},
    "ebsw": {"lead": 45},
    "eratio": {"n": 10, "trend": 200},
    "rsquared": {"n": 20},
    "seband": {"n": 20},
    "kagi": {"rev": 2},
    "renko": {"brick": 1},
    "morning": {"trend": 200},
    "hammer": {"trend": 200},
    "rising3": {"trend": 200},
    "three_out": {"trend": 200},
    "twobar": {"n": 1},
    "td_combo": {"setup": 9, "count": 13, "trend": 200},
    "di_cross": {"n": 14},
    "woodie": {"trend": 200},
    "fibhold": {"n": 20, "trend": 200},
    "saucer": {"trend": 200},
    "gator_wake": {"lips": 5, "jaw": 13},
}


def _ready_index(key: str, params: dict) -> int:
    if key == "smi":
        return params["n"] + 3
    if key == "tsi_sig":
        return 25 + 13 - 1 + params["signal"] - 1
    if key == "cmo_sig":
        return 14 + params["signal"] - 1
    if key in ("mama", "ebsw"):
        return HILBERT_READY
    if key == "itrend":
        return 6
    if key == "eratio":
        return max(params["n"], params["trend"] - 1)
    if key in ("rsquared", "seband"):
        return params["n"] - 1
    if key in ("kagi", "renko"):
        return 1
    if key in ("morning", "hammer", "three_out"):
        return max(2, params["trend"] - 1)
    if key == "rising3":
        return max(4, params["trend"] - 1)
    if key == "twobar":
        return params["n"]
    if key == "td_combo":
        return max(4, params["trend"] - 1)
    if key == "di_cross":
        return params["n"]
    if key == "woodie":
        return max(1, params["trend"] - 1)
    if key == "fibhold":
        return max(params["n"], params["trend"] - 1)
    if key == "saucer":
        return max(35, params["trend"] - 1)
    if key == "gator_wake":
        lips = params["lips"] - 1 + 3
        teeth = 8 - 1 + 5
        jaw = params["jaw"] - 1 + 8
        return max(lips, teeth, jaw) + 2
    raise AssertionError(key)


def make_bars(opens, closes=None, highs=None, lows=None, day0=DAY0) -> DailyBars:
    o = np.asarray(opens, dtype=np.float64)
    c = o.copy() if closes is None else np.asarray(closes, dtype=np.float64)
    h = np.maximum(o, c) if highs is None else np.asarray(highs, dtype=np.float64)
    lo = np.minimum(o, c) if lows is None else np.asarray(lows, dtype=np.float64)
    h = np.maximum(h, np.maximum(o, c))
    lo = np.minimum(lo, np.minimum(o, c))
    ts = day0 + np.arange(o.shape[0], dtype=np.int64) * DAY_MS
    return DailyBars(ts=ts, open=o, high=h, low=lo, close=c)


def random_walk(n: int = 640, seed: int = 19) -> DailyBars:
    rng = np.random.default_rng(seed)
    close = 20_000.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
    opens = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(opens, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(opens, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    return make_bars(opens, close, high, low)


def _prefix(bars: DailyBars, cut: int) -> DailyBars:
    return DailyBars(
        ts=bars.ts[:cut], open=bars.open[:cut], high=bars.high[:cut],
        low=bars.low[:cut], close=bars.close[:cut],
    )


def _rise(n: int, step: float = 1.0) -> DailyBars:
    close = 100.0 + np.arange(n, dtype=np.float64) * step
    return make_bars(close - 0.4, close, highs=close + 0.2, lows=close - 0.8)


class CatalogOrderTests(unittest.TestCase):
    def test_batch7_is_appended_after_the_first_hundred_thirty_two(self):
        end = BATCH6_END + len(NEW)
        self.assertEqual(tuple(s.key for s in LIBRARY[BATCH6_END:end]), NEW)
        self.assertEqual(LIBRARY[BATCH6_END - 1].key, "expiry")
        self.assertEqual(LIBRARY[end].key, "rsi_sig")
        self.assertGreater(len(LIBRARY), end)
        for key, center in CENTERS.items():
            strategy = strategy_by_key(key)
            self.assertEqual(strategy.center, center)
            self.assertEqual(strategy.stop_frac, LIBRARY_STOP_FRAC)
            self.assertTrue(strategy.citation.tested_rule.strip())
            self.assertTrue(strategy.citation.implemented_rule.strip())

    def test_skipped_near_copies_are_not_keys(self):
        keys = {s.key for s in LIBRARY}
        for skipped in (
            "pgo", "disparity", "aroon_osc", "endpoint", "forecast", "floor_r1",
            "parkinson", "decycler", "coppock", "tema", "ichimoku_chikou", "nr4",
            "ppo", "obv", "cmf", "mfi", "vwap",
        ):
            self.assertNotIn(skipped, keys)
        self.assertIn("smi", keys)
        self.assertIn("td_combo", keys)
        self.assertNotIn("td_cd_v2", keys)
        self.assertNotIn("pierce_v2", keys)


class WarmupAndLookaheadTests(unittest.TestCase):
    def test_each_new_signal_does_not_look_ahead(self):
        bars = random_walk(700)
        cuts = (180, 320, 460, 580)
        for key in NEW:
            strategy = strategy_by_key(key)
            for params in grid_params(strategy):
                ready = signal_ready(strategy, bars, params)
                self.assertTrue(bool(ready.any()), f"{key} {params}")
                first = int(np.flatnonzero(ready)[0])
                self.assertEqual(first, _ready_index(key, params), f"{key} {params}")
                self.assertTrue(bool(ready[first:].all()), f"{key} {params}")
                signal = strategy.signal(bars, params)
                self.assertEqual(signal.dtype, np.bool_)
                self.assertEqual(signal.shape[0], len(bars))
                self.assertGreater(first, 0, key)
                self.assertFalse(
                    bool(signal[:first].any()),
                    f"{key} {params} went long before its lookback existed",
                )
                for cut in cuts:
                    if cut <= first:
                        continue
                    np.testing.assert_array_equal(
                        strategy.signal(_prefix(bars, cut), params),
                        signal[:cut],
                        err_msg=f"{key} {params} looked ahead at {cut}",
                    )

    def test_shared_score_start_stays_2018_12_01(self):
        from hedge_fund.library.daily_cache import parse_daily_npz
        from hedge_fund.library.evaluate import build_library_data

        fixture = Path(__file__).resolve().parent / "fixtures" / "btc_library_daily_2017.npz"
        bars, _meta = parse_daily_npz(fixture.read_bytes())
        assert bars is not None
        data = build_library_data(bars)
        ts = int(data.discovery.ts[data.score_index])
        day = datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d")
        self.assertEqual(day, "2018-12-01")
        self.assertEqual(data.score_index, 471)
        for key in NEW:
            strategy = strategy_by_key(key)
            for params in grid_params(strategy):
                ready = signal_ready(strategy, data.discovery, params)
                first = int(np.flatnonzero(ready)[0])
                self.assertLessEqual(first + 1, data.score_index, f"{key} {params}")


class OnOffTests(unittest.TestCase):
    def test_smi_is_the_midpoint_distance_and_not_stochastic(self):
        bars = random_walk(80, seed=3)
        value = stochastic_momentum_index(bars.high, bars.low, bars.close, 10, 3)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 0.0)
        signal = strategy_by_key("smi").signal(bars, strategy_by_key("smi").center)
        np.testing.assert_array_equal(signal, expected)
        fast = strategy_by_key("stoch").signal(bars, strategy_by_key("stoch").center)
        self.assertFalse(np.array_equal(signal, fast))

    def test_tsi_signal_is_above_its_average_and_not_the_zero_line(self):
        bars = random_walk(120, seed=4)
        value = true_strength_index(bars.close, 25, 13)
        from hedge_fund.library.indicators import ema
        line = ema(value, 7)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & np.isfinite(line) & (value > line)
        signal = strategy_by_key("tsi_sig").signal(bars, {"signal": 7})
        np.testing.assert_array_equal(signal, expected)
        zero = strategy_by_key("tsi").signal(bars, strategy_by_key("tsi").center)
        self.assertFalse(np.array_equal(signal, zero))

    def test_cmo_signal_is_not_the_zero_line(self):
        bars = random_walk(80, seed=5)
        value = chande_momentum(bars.close, 14)
        line = sma_skip_leading(value, 9)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & np.isfinite(line) & (value > line)
        signal = strategy_by_key("cmo_sig").signal(bars, {"signal": 9})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("cmo").signal(bars, {"n": 14}),
        ))

    def test_efficiency_ratio_needs_the_threshold_and_the_average(self):
        bars = _rise(40)
        ratio = efficiency_ratio(bars.close, 10)
        trend = sma(bars.close, 20)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(ratio) & (ratio > 0.30) & np.isfinite(trend) & (bars.close > trend)
        signal = strategy_by_key("eratio").signal(bars, {"n": 10, "trend": 20})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("kama").signal(bars, strategy_by_key("kama").center),
        ))

    def test_r_squared_requires_a_clean_positive_line(self):
        bars = _rise(40)
        r2 = linreg_r2(bars.close, 20)
        slope = linreg_slope(bars.close, 20)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(r2) & np.isfinite(slope) & (r2 > 0.40) & (slope > 0.0)
        signal = strategy_by_key("rsquared").signal(bars, {"n": 20})
        np.testing.assert_array_equal(signal, expected)
        self.assertTrue(bool(signal[19:].all()))
        noisy = random_walk(60, seed=6)
        self.assertFalse(np.array_equal(
            strategy_by_key("rsquared").signal(noisy, {"n": 20}),
            strategy_by_key("linreg").signal(noisy, {"n": 20}),
        ))

    def test_standard_error_band_is_above_the_line(self):
        bars = random_walk(80, seed=7)
        line = linreg_value(bars.close, 20)
        band = line + linreg_stderr(bars.close, 20)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(bars.close) & np.isfinite(band) & (bars.close > band)
        signal = strategy_by_key("seband").signal(bars, {"n": 20})
        np.testing.assert_array_equal(signal, expected)
        bare = np.isfinite(line) & (bars.close > line)
        self.assertFalse(np.array_equal(signal, bare))

    def test_kagi_enters_on_the_reversal_not_the_first_line(self):
        close = np.array([100, 101, 102, 103, 90, 90.5, 91, 104], dtype=np.float64)
        signal = strategy_by_key("kagi").signal(make_bars(close), {"rev": 2})
        self.assertFalse(bool(signal[:7].any()))
        self.assertTrue(bool(signal[7]))

    def test_renko_is_not_the_line_count(self):
        close = np.array([100, 102, 104, 101, 99, 103], dtype=np.float64)
        signal = strategy_by_key("renko").signal(make_bars(close), {"brick": 1})
        self.assertTrue(bool(signal[1]))
        self.assertFalse(bool(signal[4]))
        self.assertTrue(bool(signal[5]))
        lines = strategy_by_key("tlbreak").signal(make_bars(close), {"n": 3})
        self.assertFalse(np.array_equal(signal, lines))

    def test_two_bar_reclaims_the_high_not_only_the_low(self):
        close = np.array([10.0, 11.0, 12.0], dtype=np.float64)
        low = np.array([9.5, 10.5, 9.0], dtype=np.float64)
        high = np.array([10.2, 11.2, 12.4], dtype=np.float64)
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("twobar").signal(bars, {"n": 1})
        self.assertFalse(bool(signal[1]))
        self.assertTrue(bool(signal[2]))
        oops = strategy_by_key("oops").signal(bars, {"n": 1})
        self.assertTrue(bool(oops[2]))
        # A close that only gets back above the low is oops, not this rule.
        close2 = close.copy()
        close2[2] = 10.8
        high2 = high.copy()
        high2[2] = 10.9
        short = make_bars(close2, close2, high2, low)
        self.assertFalse(bool(strategy_by_key("twobar").signal(short, {"n": 1})[2]))
        self.assertTrue(bool(strategy_by_key("oops").signal(short, {"n": 1})[2]))

    def test_directional_cross_has_no_adx_floor(self):
        bars = _rise(40)
        signal = strategy_by_key("di_cross").signal(bars, {"n": 14})
        adx = strategy_by_key("adx14").signal(bars, strategy_by_key("adx14").center)
        self.assertTrue(bool(signal[14:].any()))
        self.assertFalse(np.array_equal(signal, adx))

    def test_new_signals_are_not_their_named_cousins(self):
        bars = random_walk(420, seed=11)
        pairs = (
            ("mama", "kama"), ("itrend", "ssmooth"), ("ebsw", "cyber"),
            ("woodie", "floor_pivot"), ("fibhold", "regline"),
            ("saucer", "awesome"), ("gator_wake", "gator"),
            ("morning", "pierce"), ("three_out", "engulf"),
            ("td_combo", "td_cd"), ("hammer", "oops"), ("rising3", "soldiers"),
        )
        for left, right in pairs:
            a = strategy_by_key(left).signal(bars, strategy_by_key(left).center)
            b = strategy_by_key(right).signal(bars, strategy_by_key(right).center)
            self.assertFalse(np.array_equal(a, b), f"{left} copied {right}")


class TapeTradeTests(unittest.TestCase):
    def _bars(self):
        from hedge_fund.library.daily_cache import parse_daily_npz
        from hedge_fund.library.evaluate import build_library_data

        fixture = Path(__file__).resolve().parent / "fixtures" / "btc_library_daily_2017.npz"
        bars, _meta = parse_daily_npz(fixture.read_bytes())
        assert bars is not None
        return bars, build_library_data(bars)

    def test_corrected_names_trade_on_the_tape(self):
        bars, data = self._bars()
        for key in FIXED:
            strategy = strategy_by_key(key)
            result = run(
                bars, strategy.signal(bars, strategy.center),
                start=data.score_index, end=len(bars), stop_frac=strategy.stop_frac,
            )
            self.assertGreaterEqual(len(result.trades), 3, key)

    def test_each_new_name_trades_at_least_once(self):
        bars, data = self._bars()
        for key in NEW:
            strategy = strategy_by_key(key)
            result = run(
                bars, strategy.signal(bars, strategy.center),
                start=data.score_index, end=len(bars), stop_frac=strategy.stop_frac,
            )
            self.assertGreaterEqual(len(result.trades), 1, key)
