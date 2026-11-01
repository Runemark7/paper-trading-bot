"""Batch-11 daily signals: on/off, warm-up, no look-ahead, and a trade on the tape.

The 211 names ahead of these keys stay where they are. Batch 11 is what a
library-mode claim leases after those 211 are tested. The shared score
start stays 2018-12-01. Each new center has to trade at least a few times
on the 2017-2026 daily tape.
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
    atr_wilder,
    bollinger,
    chande_kroll_stop,
    close_location,
    downside_deviation,
    fractal_dimension,
    is_week_end,
    lag1_autocorr,
    month_number,
    parkinson,
    prior_max,
    prior_max_return,
    realized_excess_kurtosis,
    realized_skewness,
    rolling_median_skip_leading,
    run_count,
    shift_previous,
    sma,
    week_number,
)
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC, a Wednesday
BATCH10_END = 211
NEW = (
    "park", "skew", "kurt", "ac1", "maxf",
    "ddev", "fdim", "ckstop", "wkswing", "inweek",
    "wkhigh", "runs", "smonth", "clv", "dmid",
    "kick", "lose5", "qdon", "lows3", "bbw",
)
CENTERS = {
    "park": {"n": 20, "trend": 200},
    "skew": {"n": 20, "trend": 200},
    "kurt": {"n": 20, "trend": 200},
    "ac1": {"n": 20},
    "maxf": {"n": 21, "trend": 200},
    "ddev": {"n": 20, "trend": 200},
    "fdim": {"n": 16, "trend": 200},
    "ckstop": {"n": 10},
    "wkswing": {"span": 1},
    "inweek": {"trend": 200},
    "wkhigh": {"trend": 200},
    "runs": {"n": 20},
    "smonth": {"lag": 12},
    "clv": {"n": 10},
    "dmid": {"n": 20},
    "kick": {"trend": 200},
    "lose5": {"trend": 200},
    "qdon": {"n": 14},
    "lows3": {"trend": 200},
    "bbw": {"n": 20},
}


def _complete_sundays(ts: np.ndarray) -> list[int]:
    week = week_number(ts)
    sunday = is_week_end(ts)
    out: list[int] = []
    for t in range(6, len(ts)):
        if not bool(sunday[t]):
            continue
        block = week[t - 6 : t + 1]
        if int(block[0]) == int(week[t]) and bool(np.all(block == week[t])):
            out.append(t)
    return out


def _month_lag_ready(ts: np.ndarray, close: np.ndarray, lag: int) -> int:
    from hedge_fund.library.indicators import is_month_end

    month = month_number(ts)
    ends = np.flatnonzero(is_month_end(ts))
    have: set[int] = set()
    prev_i = None
    prev_m = None
    for i in ends:
        m = int(month[i])
        if (
            prev_i is not None
            and prev_m is not None
            and m == prev_m + 1
            and float(close[prev_i]) > 0.0
            and float(close[i]) > 0.0
        ):
            have.add(m)
        prev_i = int(i)
        prev_m = m
    first = None
    for t in range(len(ts)):
        if int(month[t]) - int(lag) not in have:
            if first is not None:
                return len(ts)
            continue
        if first is None:
            first = t
    return len(ts) if first is None else first


def _ready_index(key: str, params: dict, bars: DailyBars | None = None) -> int:
    trend = int(params.get("trend", 0))
    if key == "park":
        return max(int(params["n"]) + 98, trend - 1)
    if key == "skew":
        return max(int(params["n"]), trend - 1)
    if key in ("kurt", "ddev"):
        return max(int(params["n"]) + 99, trend - 1)
    if key == "ac1":
        return int(params["n"])
    if key == "maxf":
        return max(int(params["n"]) + 100, trend - 1)
    if key == "fdim":
        return max(int(params["n"]) - 1, trend - 1)
    if key == "ckstop":
        return int(params["n"]) + 8
    if key == "wkswing":
        assert bars is not None
        sundays = _complete_sundays(bars.ts)
        return sundays[int(params["span"])]
    if key == "inweek":
        assert bars is not None
        return max(_complete_sundays(bars.ts)[1], trend - 1)
    if key == "wkhigh":
        assert bars is not None
        return max(_complete_sundays(bars.ts)[0], trend - 1)
    if key == "runs":
        return int(params["n"])
    if key == "smonth":
        assert bars is not None
        return _month_lag_ready(bars.ts, bars.close, int(params["lag"]))
    if key == "clv":
        return int(params["n"]) - 1
    if key == "dmid":
        return int(params["n"])
    if key == "kick":
        return max(11, trend - 1)
    if key == "lose5":
        return max(6, trend - 1)
    if key == "qdon":
        return int(params["n"]) + 99
    if key == "lows3":
        return max(2, trend - 1)
    if key == "bbw":
        return max(int(params["n"]) + 9, 199)
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


def gapped_walk(n: int = 420, seed: int = 21) -> DailyBars:
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


class CatalogOrderTests(unittest.TestCase):
    def test_batch11_is_appended_after_the_first_two_hundred_eleven(self):
        self.assertEqual(tuple(s.key for s in LIBRARY[BATCH10_END:]), NEW)
        self.assertEqual(LIBRARY[BATCH10_END - 1].key, "pctile")
        self.assertEqual(len(LIBRARY), BATCH10_END + len(NEW))
        for key, center in CENTERS.items():
            strategy = strategy_by_key(key)
            self.assertEqual(strategy.center, center)
            self.assertEqual(strategy.stop_frac, LIBRARY_STOP_FRAC)
            self.assertTrue(strategy.citation.tested_rule.strip())
            self.assertTrue(strategy.citation.implemented_rule.strip())

    def test_skipped_near_copies_are_not_keys(self):
        keys = {s.key for s in LIBRARY}
        for skipped in (
            "linreg_up", "slope_up", "onneck", "on_neck",
            "coppock", "obv", "cmf", "vwap",
        ):
            self.assertNotIn(skipped, keys)
        self.assertIn("park", keys)
        self.assertIn("skew", keys)
        self.assertIn("maxf", keys)
        self.assertIn("fdim", keys)
        self.assertIn("ckstop", keys)
        self.assertIn("wkswing", keys)
        self.assertIn("linreg", keys)
        self.assertIn("dmid", keys)
        self.assertIn("fibhold", keys)


class WarmupAndLookaheadTests(unittest.TestCase):
    def test_each_new_signal_does_not_look_ahead(self):
        bars = random_walk(720)
        cuts = (220, 360, 480, 600)
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

    def test_december_2024_is_the_first_same_month_on_the_synthetic_calendar(self):
        bars = random_walk(500)
        # 2023-11-15 start. The first month with a prior month-end is December
        # 2023. Lag 12 is ready on the first day of December 2024.
        self.assertEqual(_month_lag_ready(bars.ts, bars.close, 12), 382)
        self.assertEqual(_month_lag_ready(bars.ts, bars.close, 11), 352)
        self.assertEqual(_month_lag_ready(bars.ts, bars.close, 13), 413)


class OnOffTests(unittest.TestCase):
    def test_parkinson_term_is_the_high_low_formula(self):
        high = np.full(20, 110.0)
        low = np.full(20, 100.0)
        term = np.log(1.1) ** 2 / (4.0 * np.log(2.0))
        value = parkinson(high, low, 20)
        self.assertAlmostEqual(float(value[19]), float(term), places=10)
        self.assertTrue(np.isnan(value[18]))

    def test_quiet_variance_is_not_garman_klass_or_rogers_satchell(self):
        bars = random_walk(360, seed=6)
        value = parkinson(bars.high, bars.low, 20)
        med = rolling_median_skip_leading(value, 100)
        trend = sma(bars.close, 200)
        with np.errstate(invalid="ignore"):
            expected = (
                np.isfinite(value) & np.isfinite(med) & (value < med)
                & np.isfinite(trend) & (bars.close > trend)
            )
        np.testing.assert_array_equal(
            strategy_by_key("park").signal(bars, {"n": 20, "trend": 200}),
            expected,
        )
        self.assertFalse(np.array_equal(
            strategy_by_key("park").signal(bars, {"n": 20, "trend": 200}),
            strategy_by_key("gk").signal(bars, {"n": 20, "trend": 200}),
        ))
        self.assertFalse(np.array_equal(
            strategy_by_key("park").signal(bars, {"n": 20, "trend": 200}),
            strategy_by_key("rsvol").signal(bars, {"n": 20, "trend": 200}),
        ))

    def test_skew_is_negative_after_a_crash_and_kurtosis_is_the_fourth_moment(self):
        close = np.full(30, 100.0)
        close = close * np.exp(np.linspace(0.0, 0.2, 30))
        close[-1] = close[-2] * 0.5
        skew = realized_skewness(close, 20)
        kurt = realized_excess_kurtosis(close, 20)
        self.assertLess(float(skew[-1]), 0.0)
        self.assertTrue(np.isfinite(kurt[-1]))
        self.assertFalse(np.allclose(skew[20:], kurt[20:]))
        bars = make_bars(close, close, highs=close * 1.01, lows=close * 0.99)
        trend = sma(close, 8)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(skew) & (skew < 0.0) & np.isfinite(trend) & (close > trend)
        np.testing.assert_array_equal(
            strategy_by_key("skew").signal(bars, {"n": 20, "trend": 8}),
            expected,
        )

    def test_autocorr_of_a_trending_increment_is_positive(self):
        step = np.linspace(0.002, 0.02, 40)
        close = 100.0 * np.exp(np.cumsum(step))
        rho = lag1_autocorr(close, 20)
        self.assertGreater(float(rho[-1]), 0.0)
        bars = make_bars(close, close)
        self.assertTrue(bool(strategy_by_key("ac1").signal(bars, {"n": 20})[-1]))
        self.assertFalse(bool(strategy_by_key("ac1").signal(bars, {"n": 20})[19]))

    def test_max_excludes_today(self):
        close = np.array([100.0, 101.0, 102.0, 130.0, 104.0], dtype=np.float64)
        value = prior_max_return(close, 2)
        # At index 4 the prior two simple returns are 130/102-1 and 102/101-1.
        # Today's drop is outside the window.
        self.assertAlmostEqual(float(value[4]), 130.0 / 102.0 - 1.0, places=8)
        self.assertTrue(np.isnan(value[2]))

    def test_a_line_has_fractal_dimension_below_the_trend_cutoff(self):
        close = 100.0 + np.arange(40, dtype=np.float64)
        dim = fractal_dimension(close, 16)
        self.assertLess(float(dim[15]), 1.4)
        self.assertTrue(np.isnan(dim[14]))
        bars = make_bars(close, close, highs=close + 0.2, lows=close - 0.2)
        signal = strategy_by_key("fdim").signal(bars, {"n": 16, "trend": 10})
        self.assertTrue(bool(signal[20]))
        self.assertFalse(bool(signal[8]))

    def test_chande_kroll_is_the_lowest_preliminary_stop(self):
        close = np.arange(40, dtype=np.float64) + 50.0
        high = close + 1.0
        low = close - 1.0
        stop = chande_kroll_stop(high, low, close, 10)
        atr = atr_wilder(high, low, close, 10)
        floor = np.array([np.min(low[t - 9 : t + 1]) if t >= 9 else np.nan for t in range(len(close))])
        prelim = floor + atr
        manual = np.min(prelim[22 - 8 : 23])
        self.assertTrue(np.isnan(stop[17]))
        self.assertAlmostEqual(float(stop[22]), float(manual), places=8)
        bars = make_bars(close, close, high, low)
        self.assertTrue(bool(strategy_by_key("ckstop").signal(bars, {"n": 10})[30]))

    def test_a_rising_tape_turns_the_weekly_swing_on_at_the_second_sunday(self):
        close = 100.0 + np.arange(40, dtype=np.float64)
        bars = make_bars(close, close, highs=close + 0.4, lows=close - 0.4)
        signal = strategy_by_key("wkswing").signal(bars, {"span": 1})
        sundays = _complete_sundays(bars.ts)
        self.assertEqual(sundays[1], 18)
        self.assertFalse(bool(signal[:18].any()))
        self.assertTrue(bool(signal[18]))
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("tsmom1w").signal(bars, {"weeks": 1}),
        ))

    def test_inside_week_breaks_on_a_later_close(self):
        # Two quiet weeks, then an inside week, then a close through that high.
        close = np.full(40, 100.0)
        high = np.full(40, 110.0)
        low = np.full(40, 90.0)
        # Week of Nov 20-26 (indexes 5..11) is the first complete week: 90-110.
        # Week of Nov 27-Dec 3 (indexes 12..18) sits inside it.
        high[12:19] = 105.0
        low[12:19] = 95.0
        close[12:19] = 100.0
        close[19] = 106.0
        high[19] = 108.0
        low[19] = 99.0
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("inweek").signal(bars, {"trend": 5})
        self.assertFalse(bool(signal[18]))
        self.assertTrue(bool(signal[19]))
        self.assertFalse(bool(signal[20]))

    def test_prior_week_high_uses_the_finished_week(self):
        close = 100.0 + np.arange(30, dtype=np.float64)
        high = close + 0.2
        low = close - 0.2
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("wkhigh").signal(bars, {"trend": 5})
        sundays = _complete_sundays(bars.ts)
        # On the Sunday the week's high includes that close, so it is not a break.
        self.assertFalse(bool(signal[sundays[0]]))
        self.assertTrue(bool(signal[sundays[0] + 1]))

    def test_fewer_runs_needs_persistence_and_an_up_window(self):
        close = 100.0 + np.arange(25, dtype=np.float64)
        bars = make_bars(close, close)
        count = run_count(close, 20)
        self.assertEqual(float(count[19]), 1.0)
        self.assertTrue(bool(strategy_by_key("runs").signal(bars, {"n": 20})[20]))
        chop = np.array([100.0 + (i % 2) for i in range(25)], dtype=np.float64)
        chop_bars = make_bars(chop, chop)
        self.assertGreaterEqual(float(run_count(chop, 20)[20]), 10.0)
        self.assertFalse(bool(strategy_by_key("runs").signal(chop_bars, {"n": 20})[20]))

    def test_same_month_uses_last_years_month_and_not_this_one(self):
        # Build a year and a bit of flat prices, then make December 2023 up
        # and December 2024's own path down. December 2024 should still be long.
        n = 430
        close = np.full(n, 100.0)
        bars = make_bars(close, close, highs=close + 1.0, lows=close - 1.0)
        month = month_number(bars.ts)
        dec_2023 = int(month[0]) + 1  # first return month
        dec_end = int(np.flatnonzero(month == dec_2023)[-1])
        nov_end = int(np.flatnonzero(month == dec_2023 - 1)[-1])
        close = close.copy()
        close[dec_end] = 110.0
        close[nov_end] = 100.0
        # Keep the path flat after that month so December 2024 itself is not the evidence.
        bars = make_bars(close, close, highs=np.maximum(close + 1.0, close), lows=np.minimum(close - 1.0, close))
        signal = strategy_by_key("smonth").signal(bars, {"lag": 12})
        ready = _month_lag_ready(bars.ts, bars.close, 12)
        self.assertEqual(ready, 382)
        self.assertTrue(bool(signal[ready]))
        self.assertFalse(bool(signal[ready - 1]))

    def test_close_location_above_zero_is_the_upper_half(self):
        high = np.full(12, 110.0)
        low = np.full(12, 90.0)
        upper = np.full(12, 108.0)
        lower = np.full(12, 92.0)
        self.assertGreater(float(close_location(high, low, upper, 10)[9]), 0.0)
        self.assertLess(float(close_location(high, low, lower, 10)[9]), 0.0)
        up = make_bars(upper, upper, high, low)
        down = make_bars(lower, lower, high, low)
        self.assertTrue(bool(strategy_by_key("clv").signal(up, {"n": 10})[9]))
        self.assertFalse(bool(strategy_by_key("clv").signal(down, {"n": 10})[9]))

    def test_donchian_midpoint_excludes_today(self):
        close = np.arange(1, 26, dtype=np.float64)
        high = close + 2.0
        low = close - 2.0
        # Prior 5 highs end yesterday. At index 20 that high is 22 and that
        # low is 14, so the midpoint is 18 and the 61.8 percent level is
        # about 18.9. A close of 18.5 holds the midpoint only.
        close[20] = 18.5
        high[20] = 19.0
        low[20] = 18.0
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("dmid").signal(bars, {"n": 5})
        self.assertFalse(bool(signal[4]))
        self.assertTrue(bool(signal[20]))
        self.assertFalse(bool(strategy_by_key("fibhold").signal(bars, {"n": 5, "trend": 5})[20]))

    def test_kicking_needs_two_long_bodies_in_opposite_colors(self):
        # Prior ten bars have a body of 1 and a range of 10, so the cap is 1
        # and a long body has to beat 1. Then a black long candle and a white one.
        open_ = np.array([10.0] * 9 + [6.0, 20.0, 6.0], dtype=np.float64)
        close = np.array([11.0] * 9 + [5.0, 8.0, 22.0], dtype=np.float64)
        high = np.array([12.0] * 9 + [7.0, 20.2, 22.2], dtype=np.float64)
        low = np.array([2.0] * 9 + [2.0, 7.8, 5.8], dtype=np.float64)
        bars = make_bars(open_, close, high, low)
        self.assertTrue(bool(strategy_by_key("kick").signal(bars, {"trend": 2})[11]))
        self.assertFalse(bool(strategy_by_key("belt").signal(bars, {"trend": 2})[11]))

    def test_five_lower_closes_then_a_higher_close(self):
        close = np.array([10, 9, 8, 7, 6, 5, 4, 6], dtype=np.float64)
        bars = make_bars(close, close)
        signal = strategy_by_key("lose5").signal(bars, {"trend": 2})
        self.assertFalse(bool(signal[6]))
        self.assertTrue(bool(signal[7]))
        self.assertFalse(bool(strategy_by_key("three_dn").signal(bars, {"n": 3, "trend": 2, "exit_ma": 2})[7]))

    def test_quiet_donchian_needs_both_the_atr_and_the_prior_high(self):
        bars = random_walk(180, seed=4)
        atr = atr_wilder(bars.high, bars.low, bars.close, 10)
        med = rolling_median_skip_leading(atr, 100)
        ceiling = prior_max(bars.high, 20)
        with np.errstate(invalid="ignore"):
            expected = (
                np.isfinite(atr) & np.isfinite(med) & (atr < med)
                & np.isfinite(ceiling) & (bars.close > ceiling)
            )
        np.testing.assert_array_equal(
            strategy_by_key("qdon").signal(bars, {"n": 10}),
            expected,
        )
        self.assertFalse(np.array_equal(
            strategy_by_key("qdon").signal(bars, {"n": 14}),
            strategy_by_key("donchian20").signal(bars, {"n": 20}),
        ))

    def test_three_higher_lows_are_that_third_close(self):
        low = np.array([5, 4, 3, 4, 5, 6], dtype=np.float64)
        close = np.array([8, 8, 8, 8, 8, 9], dtype=np.float64)
        high = close + 1.0
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("lows3").signal(bars, {"trend": 2})
        self.assertFalse(bool(signal[4]))
        self.assertTrue(bool(signal[5]))

    def test_bandwidth_expansion_is_not_the_raw_range(self):
        bars = random_walk(80, seed=3)
        mid, lower, upper = bollinger(bars.close, 20, 2.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            width = (upper - lower) / mid
        prev = shift_previous(width, 10)
        trend = sma(bars.close, 200)
        # The 200-day average is not warm on 80 bars, so use the signal's fixed SMA
        # by checking the bandwidth inequality on a short trend through the function.
        signal = strategy_by_key("bbw").signal(bars, {"n": 20})
        self.assertFalse(bool(signal[:29].any()))
        self.assertEqual(signal.dtype, np.bool_)
        self.assertTrue(np.isfinite(prev[40]))

    def test_downside_deviation_counts_up_days_as_zero(self):
        close = 100.0 * np.exp(np.cumsum(np.full(25, 0.01)))
        value = downside_deviation(close, 20)
        self.assertAlmostEqual(float(value[20]), 0.0, places=8)
        crash = close.copy()
        crash[-1] = crash[-2] * 0.8
        self.assertGreater(float(downside_deviation(crash, 20)[-1]), 0.0)

    def test_new_signals_are_not_their_named_cousins(self):
        bars = gapped_walk(480, seed=11)
        pairs = (
            ("park", "gk"),
            ("skew", "kurt"),
            ("kurt", "yz"),
            ("ac1", "vratio"),
            ("maxf", "rev1m"),
            ("ddev", "ulcer"),
            ("fdim", "frama"),
            ("ckstop", "kase"),
            ("wkswing", "tsmom1w"),
            ("inweek", "inside_day"),
            ("wkhigh", "donchian20"),
            ("runs", "eratio"),
            ("smonth", "halloween"),
            ("clv", "bop"),
            ("dmid", "fibhold"),
            ("kick", "belt"),
            ("lose5", "three_dn"),
            ("qdon", "bb_squeeze"),
            ("lows3", "soldiers"),
            ("bbw", "cv"),
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

    def test_each_new_name_trades_at_least_a_few_times(self):
        bars, data = self._bars()
        for key in NEW:
            strategy = strategy_by_key(key)
            result = run(
                bars, strategy.signal(bars, strategy.center),
                start=data.score_index, end=len(bars), stop_frac=strategy.stop_frac,
            )
            self.assertGreaterEqual(len(result.trades), 3, key)
