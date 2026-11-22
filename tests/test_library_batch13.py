"""Batch-13 daily signals: on/off, warm-up, no look-ahead, and a trade on the tape.

The 251 names ahead of these keys stay where they are. Batch 13 is what a
library-mode claim leases after those 251 are tested. The shared score
start stays 2018-12-01. Each new center has to complete round-trips on the
2017-2026 daily tape. Prior weeks and months are completed periods only.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from hedge_fund.library.catalog import (
    LIBRARY, grid_params, library_trial_count, signal_ready, strategy_by_key,
)
from hedge_fund.library.daily import DAY_MS, DailyBars
from hedge_fund.library.engine import run
from hedge_fund.library.indicators import (
    abs_return_autocorr,
    bipower_jump_share,
    cox_stuart_share,
    cusum_squares_reset,
    hodges_lehmann,
    lower_shadow_imbalance,
    median_run_count,
    prior_range_log_width,
    return_iqr,
    rolling_median_skip_leading,
    sign_entropy,
    sma,
    yang_zhang_gap,
)
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC, a Wednesday
BATCH12_END = 251
NEW = (
    "tower", "sentropy", "absac", "hlloc", "iqr",
    "dwid", "inside2", "yzgap", "rspk", "volvol",
    "pwmid", "medruns", "cusumsq", "pmhigh", "lshadow",
    "coxstu", "bpv", "wvb", "smash", "r310",
)
CENTERS = {
    "tower": {"trend": 200},
    "sentropy": {"n": 20},
    "absac": {"n": 20, "trend": 200},
    "hlloc": {"n": 20},
    "iqr": {"n": 20, "trend": 200},
    "dwid": {"n": 20},
    "inside2": {"trend": 200},
    "yzgap": {"n": 20, "trend": 200},
    "rspk": {"n": 20, "trend": 200},
    "volvol": {"n": 20, "trend": 200},
    "pwmid": {"trend": 200},
    "medruns": {"n": 20},
    "cusumsq": {"trend": 200},
    "pmhigh": {"trend": 200},
    "lshadow": {"n": 10, "trend": 200},
    "coxstu": {"n": 20},
    "bpv": {"n": 20, "trend": 200},
    "wvb": {"k": 50},
    "smash": {"trend": 200},
    "r310": {"fast": 3, "slow": 10},
}


def _ready_index(key: str, params: dict) -> int:
    trend = int(params.get("trend", 0))
    n = int(params.get("n", 0))
    if key == "tower":
        return max(13, trend - 1)
    if key in ("sentropy", "dwid"):
        return n + 99
    if key in ("absac", "iqr", "yzgap", "bpv"):
        return max(n + 99, trend - 1)
    if key == "hlloc":
        return n
    if key == "inside2":
        return max(2, trend - 1)
    if key == "coxstu":
        return n - 1
    if key == "smash":
        return max(1, trend - 1)
    if key == "rspk":
        return max(n - 1, trend - 1)
    if key == "volvol":
        return max(n + 98, trend - 1)
    if key in ("pwmid", "pmhigh"):
        # The first complete Sunday (index 4) and month end (index 46) come first.
        return trend - 1
    if key == "medruns":
        return n - 1
    if key == "cusumsq":
        return max(101, trend - 1)
    if key == "lshadow":
        return max(n - 1, trend - 1)
    if key == "wvb":
        return 1
    if key == "r310":
        return int(params["slow"]) + 14
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


def _tape():
    from hedge_fund.library.daily_cache import parse_daily_npz
    from hedge_fund.library.evaluate import build_library_data

    fixture = Path(__file__).resolve().parent / "fixtures" / "btc_library_daily_2017.npz"
    bars, _meta = parse_daily_npz(fixture.read_bytes())
    assert bars is not None
    return bars, build_library_data(bars)


class CatalogOrderTests(unittest.TestCase):
    def test_batch13_is_appended_after_the_first_two_hundred_fifty_one(self):
        end = BATCH12_END + len(NEW)
        self.assertEqual(tuple(s.key for s in LIBRARY[BATCH12_END:end]), NEW)
        self.assertEqual(LIBRARY[BATCH12_END - 1].key, "mdd")
        self.assertGreaterEqual(len(LIBRARY), end)
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

    def test_dsr_trial_count_is_every_grid_point(self):
        self.assertEqual(library_trial_count(), 1064)

    def test_dropped_candidates_are_not_keys(self):
        keys = {s.key for s in LIBRARY}
        for skipped in ("gapgo", "idrift", "ssvar", "nr4", "failbrk", "cusum", "onight", "coppock"):
            self.assertNotIn(skipped, keys)


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
        bars, data = _tape()
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
    def test_sign_entropy_is_zero_for_a_one_way_window_and_one_for_a_split(self):
        close = 100.0 + np.arange(30, dtype=np.float64)
        self.assertEqual(float(sign_entropy(close, 20)[20]), 0.0)
        self.assertTrue(np.isnan(sign_entropy(close, 20)[19]))
        zig = np.array([100.0 + (i % 2) for i in range(30)])
        self.assertAlmostEqual(float(sign_entropy(zig, 20)[25]), 1.0, places=10)
        flat = np.full(30, 5.0)  # a tie is a down step
        self.assertEqual(float(sign_entropy(flat, 20)[25]), 0.0)
        # The 100-day median does not exist yet on a 30-bar tape.
        self.assertFalse(bool(strategy_by_key("sentropy").signal(make_bars(close, close), {"n": 20}).any()))

    def test_loud_and_quiet_switches_match_their_definitions(self):
        bars = random_walk(420, seed=5)
        value = abs_return_autocorr(bars.close, 20)
        self.assertTrue(np.isnan(value[19]))
        self.assertTrue(np.isfinite(value[20]))
        med = rolling_median_skip_leading(value, 100)
        trend = sma(bars.close, 200)
        with np.errstate(invalid="ignore"):
            expected = (
                np.isfinite(value) & np.isfinite(med) & (value > med)
                & np.isfinite(trend) & (bars.close > trend)
            )
        np.testing.assert_array_equal(
            strategy_by_key("absac").signal(bars, {"n": 20, "trend": 200}), expected,
        )
        np.testing.assert_array_equal(
            strategy_by_key("iqr").signal(bars, {"n": 20, "trend": 200}),
            _quiet(return_iqr(bars.close, 20), bars.close, 200),
        )
        np.testing.assert_array_equal(
            strategy_by_key("bpv").signal(bars, {"n": 20, "trend": 200}),
            _quiet(bipower_jump_share(bars.close, 20), bars.close, 200),
        )
        steps = np.full(40, 0.001)
        steps[26] = 0.3
        jump = 100.0 * np.exp(np.cumsum(steps))
        self.assertGreater(float(bipower_jump_share(jump, 20)[30]), 0.3)
        self.assertEqual(float(bipower_jump_share(np.full(40, 3.0), 20)[30]), 0.0)

    def test_hodges_lehmann_ignores_one_wild_day(self):
        rets = np.full(30, 0.002)
        rets[-1] = -0.5
        close = 100.0 * np.exp(np.cumsum(rets))
        value = hodges_lehmann(close, 20)
        self.assertGreater(float(value[-1]), 0.0)
        self.assertTrue(np.isnan(value[19]))
        self.assertLess(float(np.log(close[-1] / close[-21])), 0.0)
        bars = make_bars(close, close)
        self.assertTrue(bool(strategy_by_key("hlloc").signal(bars, {"n": 20})[-1]))

    def test_donchian_width_excludes_today(self):
        high = np.full(30, 101.0)
        low = np.full(30, 99.0)
        width = prior_range_log_width(high, low, 20)
        self.assertTrue(np.isnan(width[19]))
        self.assertAlmostEqual(float(width[20]), float(np.log(101.0 / 99.0)), places=12)
        high[25] = 200.0  # today's spike is not in today's window
        spiked = prior_range_log_width(high, low, 20)
        self.assertAlmostEqual(float(spiked[25]), float(width[20]), places=12)
        self.assertGreater(float(spiked[26]), float(width[20]))

    def test_two_inside_days_need_both_bars_inside_and_a_later_break(self):
        o = np.full(6, 100.0)
        high = np.array([110, 108, 106, 104, 107, 100], dtype=float)
        low = np.array([90, 92, 94, 96, 95, 90], dtype=float)
        close = np.array([100, 100, 100, 100, 107, 100], dtype=float)
        bars = make_bars(o, close, high, low)
        signal = strategy_by_key("inside2").signal(bars, {"trend": 2})
        # Bars 1, 2 and 3 are each inside the bar before; 2 and 3 are the pair.
        self.assertFalse(bool(signal[3]))
        self.assertTrue(bool(signal[4]))  # 107 > the second inside bar's high, 104
        close2 = close.copy()
        close2[4] = 95.0  # through the second bar's low (96) drops the setup
        close2[5] = 107.0
        bars2 = make_bars(o, close2, high, low)
        self.assertFalse(bool(strategy_by_key("inside2").signal(bars2, {"trend": 2})[5]))
        single = make_bars(o[:3], close[:3], high[:3], low[:3])
        self.assertFalse(bool(strategy_by_key("inside2").signal(single, {"trend": 2}).any()))

    def test_range_estimator_names_match_their_rules(self):
        bars = gapped_walk(420, seed=3)
        gap = yang_zhang_gap(bars.open, bars.high, bars.low, bars.close, 20)
        self.assertTrue(np.isnan(gap[19]))
        self.assertTrue(np.isfinite(gap[20]))
        np.testing.assert_array_equal(
            strategy_by_key("yzgap").signal(bars, {"n": 20, "trend": 200}),
            _quiet(gap, bars.close, 200),
        )
        sig = strategy_by_key("volvol").signal(bars, strategy_by_key("volvol").center)
        self.assertTrue(bool(sig.any()))
        rs = strategy_by_key("rspk").signal(bars, {"n": 20, "trend": 200})
        self.assertFalse(bool(rs[:199].any()))

    def test_lower_shadow_imbalance_scores_the_shadows(self):
        o = np.array([10.0, 10.0, 10.0])
        c = np.array([10.0, 10.0, 10.0])
        h = np.array([10.0, 10.0, 12.0])
        lo = np.array([8.0, 10.0, 10.0])
        self.assertAlmostEqual(float(lower_shadow_imbalance(o, h, lo, c, 1)[0]), 1.0)
        self.assertAlmostEqual(float(lower_shadow_imbalance(o, h, lo, c, 1)[1]), 0.0)
        self.assertAlmostEqual(float(lower_shadow_imbalance(o, h, lo, c, 1)[2]), -1.0)
        self.assertAlmostEqual(float(lower_shadow_imbalance(o, h, lo, c, 3)[2]), 0.0)
        self.assertTrue(np.isnan(lower_shadow_imbalance(o, h, lo, c, 3)[1]))

    def test_prior_week_midpoint_uses_completed_weeks_only(self):
        # DAY0 is a Wednesday. The first complete week is Monday 2023-11-20
        # (index 5) through Sunday 2023-11-26 (index 11).
        n = 17
        close = np.full(n, 100.0)
        high = np.full(n, 101.0)
        low = np.full(n, 99.0)
        high[8] = 120.0  # inside the first complete week: midpoint (120 + 99) / 2
        close[12:] = 105.0
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("pwmid").signal(bars, {"trend": 2})
        self.assertFalse(bool(signal[9]))
        self.assertFalse(bool(signal[11]))
        self.assertFalse(bool(signal[12]))  # 105 < 109.5
        close[13] = 111.0
        bars = make_bars(close, close, high, low)
        self.assertTrue(bool(strategy_by_key("pwmid").signal(bars, {"trend": 2})[13]))

    def test_prior_month_high_uses_completed_months_only(self):
        # Friday 2023-12-01 is the first day of a month: December ends at index 30.
        day0 = int(datetime(2023, 12, 1, tzinfo=timezone.utc).timestamp() * 1000)
        n = 80
        close = np.full(n, 100.0)
        high = np.full(n, 101.0)
        low = np.full(n, 99.0)
        high[10] = 150.0  # December's high
        close[15] = 145.0  # December is still open
        close[31:] = 140.0
        close[40] = 155.0  # above December's high, in January
        bars = make_bars(close, close, high, low, day0=day0)
        signal = strategy_by_key("pmhigh").signal(bars, {"trend": 2})
        self.assertFalse(bool(signal[15]))
        self.assertFalse(bool(signal[30]))
        self.assertFalse(bool(signal[31]))
        self.assertTrue(bool(signal[40]))
        # Started mid-month: December is partial, so it is not a reference.
        partial = make_bars(close, close, high, low, day0=day0 + 10 * DAY_MS)
        self.assertFalse(bool(strategy_by_key("pmhigh").signal(partial, {"trend": 2})[:21].any()))

    def test_median_runs_count_sides_about_the_median(self):
        close = np.arange(1, 11, dtype=float)
        self.assertEqual(float(median_run_count(close, 10)[9]), 2.0)
        zig = np.array([1, 10] * 5, dtype=float)
        self.assertEqual(float(median_run_count(zig, 10)[9]), 10.0)
        self.assertTrue(np.isnan(median_run_count(close, 10)[8]))
        up = 100.0 + np.arange(30, dtype=float)
        self.assertTrue(bool(strategy_by_key("medruns").signal(make_bars(up, up), {"n": 20})[25]))
        chop = np.array([100.0 + (i % 2) * 5 for i in range(30)])
        self.assertFalse(bool(strategy_by_key("medruns").signal(make_bars(chop, chop), {"n": 20})[25]))

    def test_cusum_squares_resets_and_is_causal(self):
        bars = random_walk(420, seed=8)
        level, mean = cusum_squares_reset(bars.close, 50)
        self.assertTrue(np.isnan(level[100]))
        self.assertTrue(np.isfinite(level[101]))
        # The sum restarts after it passes the mean, so it never runs away.
        over = np.flatnonzero(level[101:-1] > mean[101:-1]) + 101
        self.assertGreater(over.shape[0], 5)
        self.assertLess(float(np.nanmax(level[101:] / mean[101:])), 50.0)
        cut_level, cut_mean = cusum_squares_reset(bars.close[:300], 50)
        np.testing.assert_array_equal(cut_level, level[:300])
        np.testing.assert_array_equal(cut_mean, mean[:300])
        trend = sma(bars.close, 200)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(level) & (level <= mean) & np.isfinite(trend) & (bars.close > trend)
        np.testing.assert_array_equal(
            strategy_by_key("cusumsq").signal(bars, {"trend": 200}), expected,
        )

    def test_cox_stuart_share_counts_rising_pairs(self):
        close = 100.0 + np.arange(30, dtype=float)
        share = cox_stuart_share(close, 20)
        self.assertTrue(np.isnan(share[18]))
        self.assertEqual(float(share[19]), 1.0)
        down = 200.0 - np.arange(30, dtype=float)
        self.assertEqual(float(cox_stuart_share(down, 20)[25]), 0.0)
        flat = np.full(30, 7.0)  # a tie is not rising
        self.assertEqual(float(cox_stuart_share(flat, 20)[25]), 0.0)
        # An odd window drops its middle close.
        odd = np.array([1, 2, 3, 100, 0, 1, 2], dtype=float)
        self.assertEqual(float(cox_stuart_share(odd, 7)[6]), 0.0)
        bars = make_bars(close, close)
        signal = strategy_by_key("coxstu").signal(bars, {"n": 20})
        self.assertFalse(bool(signal[18]))
        self.assertTrue(bool(signal[19]))
        self.assertFalse(bool(strategy_by_key("coxstu").signal(make_bars(down, down), {"n": 20}).any()))

    def test_williams_breakout_trigger_uses_todays_open_and_yesterdays_range(self):
        o = np.full(4, 100.0)
        lo = np.full(4, 90.0)
        h = np.array([110, 110, 112, 110], dtype=float)
        c = np.array([100, 100, 111, 103], dtype=float)
        bars = make_bars(o, c, h, lo)
        wvb = strategy_by_key("wvb")
        # Yesterday's range is 20, so the trigger is the open plus 10 at k = 50.
        np.testing.assert_array_equal(wvb.signal(bars, {"k": 50}), [False, False, True, False])
        self.assertTrue(bool(wvb.signal(bars, {"k": 30})[2]))
        self.assertFalse(bool(wvb.signal(bars, {"k": 70})[2]))

    def test_smash_day_break_waits_for_a_close_above_the_smash_high(self):
        close = np.array([10, 11, 8, 9, 12, 12], dtype=float)
        high = np.array([10.5, 11.5, 9.0, 10.0, 12.5, 12.5])
        low = np.array([9.5, 10.5, 7.5, 8.5, 11.0, 11.0])
        bars = make_bars(close, close, high, low)
        smash = strategy_by_key("smash").signal(bars, {"trend": 2})
        self.assertFalse(bool(smash[2]))  # the smash day itself
        self.assertFalse(bool(smash[3]))  # 9 is not above the smash high, 9.0
        self.assertTrue(bool(smash[4]))

    def test_three_ten_oscillator_needs_its_average(self):
        rng = np.random.default_rng(4)
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.02, 80)))
        bars = make_bars(close, close)
        signal = strategy_by_key("r310").signal(bars, {"fast": 3, "slow": 10})
        gap = sma(close, 3) - sma(close, 10)
        line = np.full(80, np.nan)
        for t in range(9 + 15, 80):
            line[t] = np.mean(gap[t - 15 : t + 1])
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(line) & (gap > line)
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(bool(signal[:24].any()))

    def test_tower_bottom_needs_the_long_black_and_a_long_white_through_the_midpoint(self):
        n = 20
        o = np.full(n, 100.0)
        c = np.full(n, 100.5)
        # Bars 14..17: long black, two small bodies, long white.
        o[14], c[14] = 110.0, 100.0
        o[15], c[15] = 100.0, 100.4
        o[16], c[16] = 100.4, 100.2
        o[17], c[17] = 100.2, 109.0
        bars = make_bars(o, c, np.maximum(o, c) + 0.2, np.minimum(o, c) - 0.2)
        signal = strategy_by_key("tower").signal(bars, {"trend": 5})
        self.assertTrue(bool(signal[17]))
        self.assertFalse(bool(signal[16]))
        c[17] = 104.0  # short of the black midpoint, 105
        weak = make_bars(o, c, np.maximum(o, c) + 0.2, np.minimum(o, c) - 0.2)
        self.assertFalse(bool(strategy_by_key("tower").signal(weak, {"trend": 5})[17]))


class TapeTradeTests(unittest.TestCase):
    def test_each_new_grid_point_completes_signal_round_trips(self):
        bars, data = _tape()
        for key in NEW:
            strategy = strategy_by_key(key)
            for params in grid_params(strategy):
                signal = strategy.signal(bars, params)
                scored = signal[data.score_index:]
                round_trips = int(np.sum(scored[:-1] & ~scored[1:]))
                self.assertGreaterEqual(round_trips, 8, f"{key} {params}")
            result = run(
                bars, strategy.signal(bars, strategy.center),
                start=data.score_index, end=len(bars), stop_frac=strategy.stop_frac,
            )
            self.assertGreaterEqual(len(result.trades), 8, key)

    def test_new_signals_are_not_the_trend_filter(self):
        """A rule that matches close-above-SMA on 95% of scored days is that filter."""
        bars, data = _tape()
        for key in NEW:
            strategy = strategy_by_key(key)
            for params in grid_params(strategy):
                signal = strategy.signal(bars, params)
                trend_n = int(params.get("trend", 200))
                trend = sma(bars.close, trend_n)
                scored = slice(data.score_index, None)
                finite = np.isfinite(trend[scored])
                above = bars.close[scored][finite] > trend[scored][finite]
                agree = float(np.mean(signal[scored][finite] == above))
                self.assertLess(
                    agree, 0.95,
                    f"{key} {params} agrees with close>SMA({trend_n}) on {agree:.1%} of scored days",
                )

    def test_new_centers_are_not_copies_of_a_registered_center(self):
        """No new center repeats a registered one on 90% of its on-days."""
        bars, data = _tape()
        scored = slice(data.score_index, None)
        old = {s.key: s.signal(bars, s.center)[scored] for s in LIBRARY[:BATCH12_END]}
        for key in NEW:
            strategy = strategy_by_key(key)
            sig = strategy.signal(bars, strategy.center)[scored]
            self.assertTrue(bool(sig.any()), key)
            for other, theirs in old.items():
                union = int(np.sum(sig | theirs))
                jaccard = int(np.sum(sig & theirs)) / union if union else 0.0
                self.assertLess(jaccard, 0.9, f"{key} repeats {other} on {jaccard:.1%} of on-days")
                agree = float(np.mean(sig == theirs))
                if jaccard >= 0.5:
                    self.assertLess(agree, 0.95, f"{key} agrees with {other} on {agree:.1%}")
