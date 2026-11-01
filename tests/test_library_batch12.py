"""Batch-12 daily signals: on/off, warm-up, no look-ahead, and a trade on the tape.

The 231 names ahead of these keys stay where they are. Batch 12 is what a
library-mode claim leases after those 231 are tested. The shared score
start stays 2018-12-01. Each new center has to trade at least a few times
on the 2017-2026 daily tape. None of these rules reads an open week or an
open month.
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
    bollinger_percent_b,
    close_to_close_variance,
    corwin_schultz,
    durbin_watson,
    hampel_mad,
    hurst_rs,
    open_close_skewness,
    overnight_sum,
    parkinson,
    partial_autocorr_lag2,
    realized_vol,
    roll_spread,
    rolling_median_skip_leading,
    semivariance_share,
    sma,
    starc_upper,
    true_range_imbalance,
    turning_point_count,
    upper_half_count,
    volatility_ratio,
    window_max_drawdown,
)
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC, a Wednesday
BATCH11_END = 231
NEW = (
    "hampel", "svar", "hurst", "volr", "starc",
    "pctb", "cspread", "roll", "pacf", "turns",
    "onight", "ablock", "uhalf", "trimb", "parkcc",
    "wide", "highs3", "dw", "ocskew", "mdd",
)
CENTERS = {
    "hampel": {"n": 20, "trend": 200},
    "svar": {"n": 20, "trend": 200},
    "hurst": {"n": 64, "trend": 200},
    "volr": {"n": 10, "trend": 200},
    "starc": {"n": 15},
    "pctb": {"n": 20},
    "cspread": {"n": 20, "trend": 200},
    "roll": {"n": 20, "trend": 200},
    "pacf": {"n": 30},
    "turns": {"n": 20},
    "onight": {"n": 20},
    "ablock": {"trend": 200},
    "uhalf": {"n": 10},
    "trimb": {"n": 20, "trend": 200},
    "parkcc": {"n": 20, "trend": 200},
    "wide": {"n": 10, "trend": 200},
    "highs3": {"trend": 200},
    "dw": {"n": 20, "trend": 200},
    "ocskew": {"n": 20, "trend": 200},
    "mdd": {"n": 20, "trend": 200},
}


def _ready_index(key: str, params: dict, bars: DailyBars | None = None) -> int:
    del bars
    trend = int(params.get("trend", 0))
    n = int(params.get("n", 0))
    if key in ("hampel", "svar", "cspread", "roll"):
        return max(n + 99, trend - 1)
    if key == "hurst":
        return max(n, trend - 1)
    if key == "volr":
        return max(max(n, 100), trend - 1)
    if key == "starc":
        return n
    if key == "pctb":
        return n - 1
    if key in ("pacf", "onight", "turns"):
        return n
    if key == "ablock":
        return max(2, trend - 1)
    if key == "uhalf":
        return n - 1
    if key in ("trimb", "parkcc", "wide"):
        return max(n, trend - 1)
    if key == "highs3":
        return max(2, trend - 1)
    if key in ("dw", "ocskew"):
        return max(n - 1, trend - 1)
    if key == "mdd":
        return max(n + 98, trend - 1)
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


def gapped_walk(n: int = 520, seed: int = 21) -> DailyBars:
    rng = np.random.default_rng(seed)
    close = 20_000.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
    opens = close * (1.0 + rng.normal(0.0, 0.004, n))
    high = np.maximum(opens, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(opens, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    return make_bars(opens, close, high, low)


def _prefix(bars: DailyBars, cut: int) -> DailyBars:
    return DailyBars(
        ts=bars.ts[:cut], open=bars.open[:cut], high=bars.high[:cut],
        low=bars.low[:cut], close=bars.close[:cut],
    )


def _quiet(value: np.ndarray, close: np.ndarray, trend_n: int) -> np.ndarray:
    med = rolling_median_skip_leading(value, 100)
    trend = sma(close, trend_n)
    with np.errstate(invalid="ignore"):
        calm = np.isfinite(value) & np.isfinite(med) & (value < med)
        return calm & np.isfinite(trend) & (close > trend)


class CatalogOrderTests(unittest.TestCase):
    def test_batch12_is_appended_after_the_first_two_hundred_thirty_one(self):
        self.assertEqual(tuple(s.key for s in LIBRARY[BATCH11_END:]), NEW)
        self.assertEqual(LIBRARY[BATCH11_END - 1].key, "bbw")
        self.assertEqual(len(LIBRARY), BATCH11_END + len(NEW))
        for key, center in CENTERS.items():
            strategy = strategy_by_key(key)
            self.assertEqual(strategy.center, center)
            self.assertEqual(strategy.evaluation, "daily")
            self.assertEqual(strategy.stop_frac, LIBRARY_STOP_FRAC)
            self.assertTrue(strategy.citation.tested_rule.strip())
            self.assertTrue(strategy.citation.implemented_rule.strip())
            for param in strategy.params:
                self.assertGreaterEqual(len(param.neighbours), 1)
                self.assertNotIn(param.center, param.neighbours)

    def test_skipped_near_copies_are_not_keys(self):
        keys = {s.key for s in LIBRARY}
        for skipped in (
            "cusum", "tower", "onneck", "on_neck",
            "coppock", "obv", "cmf", "vwap", "sma200w",
        ):
            self.assertNotIn(skipped, keys)
        self.assertIn("hampel", keys)
        self.assertIn("svar", keys)
        self.assertIn("hurst", keys)
        self.assertIn("ablock", keys)
        self.assertIn("mdd", keys)
        self.assertIn("soldiers", keys)
        self.assertIn("morning", keys)
        self.assertIn("engulf", keys)


class WarmupAndLookaheadTests(unittest.TestCase):
    def test_each_new_signal_does_not_look_ahead(self):
        bars = random_walk(720)
        cuts = (280, 400, 520, 640)
        for key in NEW:
            strategy = strategy_by_key(key)
            for params in grid_params(strategy):
                ready = signal_ready(strategy, bars, params)
                self.assertTrue(bool(ready.any()), f"{key} {params}")
                first = int(np.flatnonzero(ready)[0])
                self.assertEqual(first, _ready_index(key, params, bars), f"{key} {params}")
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
    def test_hampel_scale_is_the_normal_constant(self):
        close = 100.0 * np.exp(np.linspace(0.0, 0.2, 30))
        close[-1] = close[-2] * 0.7
        value = hampel_mad(close, 20)
        rets = np.log(close[1:] / close[:-1])
        window = rets[-20:]
        med = float(np.median(window))
        mad = float(np.median(np.abs(window - med)))
        self.assertAlmostEqual(float(value[-1]), 1.4826 * mad, places=10)
        self.assertTrue(np.isnan(value[19]))
        bars = random_walk(360, seed=6)
        np.testing.assert_array_equal(
            strategy_by_key("hampel").signal(bars, {"n": 20, "trend": 200}),
            _quiet(hampel_mad(bars.close, 20), bars.close, 200),
        )

    def test_semivariance_share_of_a_crash_is_the_downside_fraction(self):
        close = 100.0 * np.exp(np.linspace(0.0, 0.05, 25))
        close[-1] = close[-2] * 0.5
        share = semivariance_share(close, 20)
        rets = np.log(close[1:] / close[:-1])[-20:]
        dev = rets - rets.mean()
        expected = float(np.mean(np.minimum(dev, 0.0) ** 2) / np.mean(dev * dev))
        self.assertAlmostEqual(float(share[-1]), expected, places=10)
        self.assertGreater(float(share[-1]), 0.5)
        bars = random_walk(360, seed=7)
        np.testing.assert_array_equal(
            strategy_by_key("svar").signal(bars, {"n": 20, "trend": 200}),
            _quiet(semivariance_share(bars.close, 20), bars.close, 200),
        )

    def test_a_straight_rise_has_hurst_above_one_half(self):
        # Rising log returns, not a constant drift. A constant drift has no range.
        rets = np.linspace(0.001, 0.03, 70)
        close = 100.0 * np.exp(np.cumsum(rets))
        value = hurst_rs(close, 64)
        self.assertGreater(float(value[-1]), 0.5)
        self.assertTrue(np.isnan(value[63]))
        flat = np.full(80, 100.0)
        self.assertAlmostEqual(float(hurst_rs(flat, 64)[-1]), 0.5, places=8)
        bars = random_walk(360, seed=8)
        hurst = hurst_rs(bars.close, 64)
        trend = sma(bars.close, 200)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(hurst) & (hurst > 0.5) & np.isfinite(trend) & (bars.close > trend)
        np.testing.assert_array_equal(
            strategy_by_key("hurst").signal(bars, {"n": 64, "trend": 200}),
            expected,
        )

    def test_volatility_ratio_uses_the_fixed_long_window(self):
        bars = random_walk(180, seed=2)
        ratio = volatility_ratio(bars.close, 10, 100)
        short = realized_vol(bars.close, 10)
        long = realized_vol(bars.close, 100)
        self.assertTrue(np.isnan(ratio[99]))
        self.assertAlmostEqual(float(ratio[100]), float(short[100] / long[100]), places=10)
        trend = sma(bars.close, 50)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(ratio) & (ratio < 1.0) & np.isfinite(trend) & (bars.close > trend)
        np.testing.assert_array_equal(
            strategy_by_key("volr").signal(bars, {"n": 10, "trend": 50}),
            expected,
        )

    def test_starc_is_the_sma_plus_one_atr(self):
        close = np.arange(40, dtype=np.float64) + 50.0
        high = close + 1.0
        low = close - 1.0
        band = starc_upper(high, low, close, 15)
        self.assertTrue(np.isnan(band[14]))
        self.assertTrue(np.isfinite(band[15]))
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("starc").signal(bars, {"n": 15})
        self.assertFalse(bool(signal[14]))
        self.assertEqual(bool(signal[20]), bool(close[20] > band[20]))

    def test_percent_b_above_0_8_is_not_the_upper_band(self):
        bars = random_walk(80, seed=3)
        value = bollinger_percent_b(bars.close, 20)
        self.assertTrue(np.isnan(value[18]))
        self.assertTrue(np.isfinite(value[19]))
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 0.8)
        np.testing.assert_array_equal(
            strategy_by_key("pctb").signal(bars, {"n": 20}),
            expected,
        )
        self.assertFalse(np.array_equal(
            strategy_by_key("pctb").signal(bars, {"n": 20}),
            strategy_by_key("bb_break").signal(bars, strategy_by_key("bb_break").center),
        ))

    def test_corwin_and_roll_are_quiet_spreads(self):
        bars = random_walk(360, seed=5)
        np.testing.assert_array_equal(
            strategy_by_key("cspread").signal(bars, {"n": 20, "trend": 200}),
            _quiet(corwin_schultz(bars.high, bars.low, 20), bars.close, 200),
        )
        spread = roll_spread(bars.close, 20)
        self.assertTrue(np.isnan(spread[19]))
        self.assertTrue(np.isfinite(spread[20]))
        self.assertGreaterEqual(float(spread[20]), 0.0)
        np.testing.assert_array_equal(
            strategy_by_key("roll").signal(bars, {"n": 20, "trend": 200}),
            _quiet(spread, bars.close, 200),
        )

    def test_partial_autocorr_removes_lag_one(self):
        step = np.linspace(0.001, 0.02, 50)
        close = 100.0 * np.exp(np.cumsum(step))
        phi = partial_autocorr_lag2(close, 30)
        self.assertTrue(np.isnan(phi[29]))
        self.assertTrue(np.isfinite(phi[30]))
        bars = make_bars(close, close)
        signal = strategy_by_key("pacf").signal(bars, {"n": 30})
        self.assertEqual(bool(signal[30]), bool(phi[30] > 0.0))
        self.assertFalse(bool(signal[29]))

    def test_fewer_turning_points_needs_a_smooth_up_window(self):
        close = 100.0 + np.arange(25, dtype=np.float64)
        count = turning_point_count(close, 20)
        self.assertEqual(float(count[19]), 0.0)
        self.assertTrue(np.isnan(count[18]))
        bars = make_bars(close, close)
        self.assertFalse(bool(strategy_by_key("turns").signal(bars, {"n": 20})[19]))
        self.assertTrue(bool(strategy_by_key("turns").signal(bars, {"n": 20})[20]))
        chop = np.array([100.0 + (i % 2) for i in range(25)], dtype=np.float64)
        self.assertGreaterEqual(float(turning_point_count(chop, 20)[20]), 12.0)
        self.assertFalse(bool(strategy_by_key("turns").signal(make_bars(chop, chop), {"n": 20})[20]))

    def test_overnight_sum_uses_the_open_and_not_the_close(self):
        close = np.full(25, 100.0)
        open_ = np.full(25, 100.0)
        open_[1:] = 101.0
        value = overnight_sum(open_, close, 20)
        self.assertAlmostEqual(float(value[20]), 20.0 * np.log(1.01), places=8)
        self.assertTrue(np.isnan(value[19]))
        bars = make_bars(open_, close, highs=np.maximum(open_, close) + 1.0, lows=np.minimum(open_, close) - 1.0)
        self.assertTrue(bool(strategy_by_key("onight").signal(bars, {"n": 20})[20]))
        flat = make_bars(close, close)
        self.assertFalse(bool(strategy_by_key("onight").signal(flat, {"n": 20})[20]))

    def test_advance_block_needs_longer_upper_shadows(self):
        # Three white candles, rising closes, and upper shadows 0.1, 0.3, 0.6.
        open_ = np.array([10.0, 11.0, 12.0, 13.0], dtype=np.float64)
        close = np.array([10.5, 12.0, 13.5, 15.0], dtype=np.float64)
        high = np.array([10.6, 12.1, 13.8, 15.6], dtype=np.float64)
        low = close - 0.2
        bars = make_bars(open_, close, high, low)
        signal = strategy_by_key("ablock").signal(bars, {"trend": 2})
        self.assertFalse(bool(signal[2]))
        self.assertTrue(bool(signal[3]))
        high[3] = 15.2  # upper shadow 0.2, no longer growing
        stalled = make_bars(open_, close, high, low)
        self.assertFalse(bool(strategy_by_key("ablock").signal(stalled, {"trend": 2})[3]))

    def test_upper_half_count_is_not_the_average_location(self):
        high = np.full(12, 110.0)
        low = np.full(12, 90.0)
        close = np.full(12, 101.0)
        close[:4] = 89.0
        count = upper_half_count(high, low, close, 10)
        self.assertEqual(float(count[9]), 6.0)
        self.assertTrue(np.isnan(count[8]))
        bars = make_bars(close, close, high, low)
        self.assertTrue(bool(strategy_by_key("uhalf").signal(bars, {"n": 10})[9]))
        close[4] = 89.0
        bars = make_bars(close, close, high, low)
        self.assertFalse(bool(strategy_by_key("uhalf").signal(bars, {"n": 10})[9]))

    def test_true_range_imbalance_splits_up_and_down_days(self):
        close = np.array([10, 12, 11, 13, 12, 14], dtype=np.float64)
        high = close + 1.0
        low = close - 0.5
        value = true_range_imbalance(high, low, close, 4)
        self.assertTrue(np.isnan(value[3]))
        self.assertTrue(np.isfinite(value[4]))
        bars = make_bars(close, close, high, low)
        trend = sma(close, 3)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 0.0) & np.isfinite(trend) & (close > trend)
        np.testing.assert_array_equal(
            strategy_by_key("trimb").signal(bars, {"n": 4, "trend": 3}),
            expected,
        )

    def test_parkinson_below_close_to_close_is_not_the_median_rule(self):
        bars = random_walk(80, seed=4)
        park = parkinson(bars.high, bars.low, 20)
        var = close_to_close_variance(bars.close, 20)
        trend = sma(bars.close, 30)
        with np.errstate(invalid="ignore"):
            expected = (
                np.isfinite(park) & np.isfinite(var) & (park < var)
                & np.isfinite(trend) & (bars.close > trend)
            )
        np.testing.assert_array_equal(
            strategy_by_key("parkcc").signal(bars, {"n": 20, "trend": 30}),
            expected,
        )
        self.assertFalse(np.array_equal(
            strategy_by_key("parkcc").signal(bars, {"n": 20, "trend": 30}),
            strategy_by_key("park").signal(bars, {"n": 20, "trend": 30}),
        ))

    def test_wide_range_up_day_uses_the_prior_ranges(self):
        close = np.full(16, 100.0)
        open_ = np.full(16, 100.0)
        high = np.full(16, 101.0)
        low = np.full(16, 99.0)
        high[-1] = 110.0
        low[-1] = 100.0
        close[-1] = 108.0
        open_[-1] = 101.0
        bars = make_bars(open_, close, high, low)
        signal = strategy_by_key("wide").signal(bars, {"n": 10, "trend": 5})
        self.assertFalse(bool(signal[14]))
        self.assertTrue(bool(signal[15]))
        open_[-1] = 109.0
        close[-1] = 108.0
        down = make_bars(open_, close, high, low)
        self.assertFalse(bool(strategy_by_key("wide").signal(down, {"n": 10, "trend": 5})[15]))

    def test_three_higher_highs_are_that_third_close(self):
        high = np.array([8.0, 7.0, 6.0, 7.0, 9.0], dtype=np.float64)
        close = np.array([8.0, 7.0, 6.0, 7.0, 9.0], dtype=np.float64)
        low = np.array([7.0, 6.0, 4.0, 5.0, 4.5], dtype=np.float64)
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("highs3").signal(bars, {"trend": 2})
        self.assertFalse(bool(signal[3]))
        self.assertTrue(bool(signal[4]))
        self.assertFalse(bool(strategy_by_key("lows3").signal(bars, {"trend": 2})[4]))

    def test_durbin_watson_of_a_line_is_not_below_two(self):
        close = 100.0 + np.arange(30, dtype=np.float64)
        stat = durbin_watson(close, 20)
        self.assertTrue(np.isnan(stat[18]))
        self.assertAlmostEqual(float(stat[19]), 2.0, places=6)
        curved = close + np.sin(np.linspace(0.0, 8.0, 30))
        sticky = durbin_watson(curved, 20)
        self.assertLess(float(sticky[-1]), 2.0)
        bars = make_bars(curved, curved)
        trend = sma(curved, 10)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(sticky) & (sticky < 2.0) & np.isfinite(trend) & (curved > trend)
        np.testing.assert_array_equal(
            strategy_by_key("dw").signal(bars, {"n": 20, "trend": 10}),
            expected,
        )

    def test_open_to_close_skew_ignores_the_gap(self):
        open_ = np.full(25, 100.0)
        close = np.full(25, 100.0)
        close[-1] = 80.0
        session = open_close_skewness(open_, close, 20)
        self.assertLess(float(session[-1]), 0.0)
        self.assertTrue(np.isnan(session[18]))
        bars = make_bars(open_, close, highs=np.maximum(open_, close) + 1.0, lows=np.minimum(open_, close) - 1.0)
        trend = sma(close, 8)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(session) & (session < 0.0) & np.isfinite(trend) & (close > trend)
        np.testing.assert_array_equal(
            strategy_by_key("ocskew").signal(bars, {"n": 20, "trend": 8}),
            expected,
        )

    def test_window_drawdown_is_the_worst_running_decline(self):
        close = np.array([10, 12, 11, 13, 9, 10], dtype=np.float64)
        # Window of 4 ending at index 4: 12, 11, 13, 9. Peak runs 12, 12, 13, 13.
        # Declines: 0, 1/12, 0, 4/13. Worst is 4/13.
        value = window_max_drawdown(close, 4)
        self.assertTrue(np.isnan(value[2]))
        self.assertAlmostEqual(float(value[4]), 4.0 / 13.0, places=8)
        bars = random_walk(360, seed=9)
        np.testing.assert_array_equal(
            strategy_by_key("mdd").signal(bars, {"n": 20, "trend": 200}),
            _quiet(window_max_drawdown(bars.close, 20), bars.close, 200),
        )

    def test_new_signals_are_not_their_named_cousins(self):
        bars = gapped_walk(520, seed=11)
        pairs = (
            ("hampel", "vol_trend"),
            ("svar", "ddev"),
            ("hurst", "fdim"),
            ("volr", "vol_trend"),
            ("starc", "keltner20"),
            ("pctb", "bb_break"),
            ("cspread", "park"),
            ("roll", "ac1"),
            ("pacf", "ac1"),
            ("turns", "runs"),
            ("onight", "tsmom30d"),
            ("ablock", "soldiers"),
            ("uhalf", "clv"),
            ("trimb", "qdon"),
            ("parkcc", "park"),
            ("wide", "nr7"),
            ("highs3", "lows3"),
            ("dw", "rsquared"),
            ("ocskew", "skew"),
            ("mdd", "ulcer"),
        )
        for left, right in pairs:
            a = strategy_by_key(left).signal(bars, strategy_by_key(left).center)
            b = strategy_by_key(right).signal(bars, strategy_by_key(right).center)
            self.assertFalse(np.array_equal(a, b), f"{left} copied {right}")
        sma_long = strategy_by_key("sma200").signal(bars, strategy_by_key("sma200").center)
        self.assertFalse(np.array_equal(
            strategy_by_key("hurst").signal(bars, strategy_by_key("hurst").center),
            sma_long,
        ))


class TapeTradeTests(unittest.TestCase):
    def _bars(self):
        from hedge_fund.library.daily_cache import parse_daily_npz
        from hedge_fund.library.evaluate import build_library_data

        fixture = Path(__file__).resolve().parent / "fixtures" / "btc_library_daily_2017.npz"
        bars, _meta = parse_daily_npz(fixture.read_bytes())
        assert bars is not None
        return bars, build_library_data(bars)

    def test_each_new_name_trades_at_least_a_few_times(self):
        bars, data = self._bars()
        for key in NEW:
            strategy = strategy_by_key(key)
            result = run(
                bars, strategy.signal(bars, strategy.center),
                start=data.score_index, end=len(bars), stop_frac=strategy.stop_frac,
            )
            self.assertGreaterEqual(len(result.trades), 3, key)
