"""Batch-6 daily signals: on/off, warm-up, and no look-ahead.

The original 15, the 17 batch-2 names, the 24 batch-3 names, the 26
batch-4 names, and the 26 batch-5 names stay ahead of these keys, with the
same centers. Batch 6 is what a library-mode claim leases after those 108
are tested. The shared score start stays 2018-12-01.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np

from hedge_fund.library.catalog import LIBRARY, grid_params, signal_ready, strategy_by_key
from hedge_fund.library.daily import DAY_MS, DailyBars
from hedge_fund.library.indicators import (
    alma,
    awesome_oscillator,
    cyber_cycle,
    demarker,
    frama,
    garman_klass,
    laguerre_filter,
    linreg_slope,
    linreg_value,
    rolling_median_skip_leading,
    sma,
    sma_skip_leading,
    stochastic_k,
    super_smoother,
    third_friday_dom,
    trix,
    variance_ratio,
    vertical_horizontal_filter,
    zero_lag_ema,
)
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC, a Wednesday
BATCH5_END = 108
NEW = (
    "demarker", "td_cd", "trix_sig", "stoch_slow", "zlema", "frama", "ssmooth",
    "cyber", "lfilter", "alma", "tlbreak", "engulf", "pierce", "soldiers",
    "oops", "vratio", "vhf", "accel", "gator", "camarilla", "regline", "gk",
    "harami", "expiry",
)
CALENDAR = ("expiry",)
CENTERS = {
    "demarker": {"n": 14},
    "td_cd": {"setup": 9, "count": 13, "trend": 200},
    "trix_sig": {"signal": 9},
    "stoch_slow": {"k": 14, "d": 3},
    "zlema": {"n": 20},
    "frama": {"n": 16},
    "ssmooth": {"n": 10},
    "cyber": {"alpha": 7},
    "lfilter": {"gamma": 50},
    "alma": {"n": 9, "offset": 85},
    "tlbreak": {"n": 3},
    "engulf": {"trend": 200},
    "pierce": {"trend": 200},
    "soldiers": {"trend": 200},
    "oops": {"n": 1},
    "vratio": {"q": 16, "win": 128, "trend": 200},
    "vhf": {"n": 28, "trend": 200},
    "accel": {"smooth": 5},
    "gator": {"lips": 5, "jaw": 13},
    "camarilla": {"trend": 200},
    "regline": {"n": 20},
    "gk": {"n": 14, "trend": 200},
    "harami": {"trend": 200},
    "expiry": {"n": 3},
}


def _ready_index(key: str, params: dict) -> int:
    if key == "demarker":
        return params["n"]
    if key == "td_cd":
        return max(4, params["trend"] - 1)
    if key == "trix_sig":
        return 3 * (15 - 1) + params["signal"]
    if key == "stoch_slow":
        return params["k"] + 2 * params["d"] - 1
    if key == "zlema":
        return (params["n"] - 1) // 2 + params["n"] - 1
    if key == "frama":
        return params["n"] - 1
    if key == "ssmooth":
        return 2
    if key == "cyber":
        return 6
    if key == "lfilter":
        return 1
    if key == "alma":
        return params["n"] - 1
    if key == "tlbreak":
        return params["n"] + 1
    if key in ("engulf", "pierce", "harami"):
        return max(1, params["trend"] - 1)
    if key == "soldiers":
        return max(3, params["trend"] - 1)
    if key == "oops":
        return params["n"]
    if key == "vratio":
        return max(params["win"], params["trend"] - 1)
    if key == "vhf":
        return max(params["n"] + 99, params["trend"] - 1)
    if key == "accel":
        return 33 + params["smooth"] - 1
    if key == "gator":
        lips = params["lips"] - 1 + 3
        teeth = 8 - 1 + 5
        jaw = params["jaw"] - 1 + 8
        return max(lips, teeth, jaw) + 1
    if key == "camarilla":
        return max(1, params["trend"] - 1)
    if key == "regline":
        return params["n"] - 1
    if key == "gk":
        return max(params["n"] + 98, params["trend"] - 1)
    if key == "expiry":
        return 0
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
    return make_bars(close - 0.4, close, highs=close + 0.4, lows=close - 0.8)


def _fall(n: int, step: float = 1.0) -> DailyBars:
    close = 100.0 + n - np.arange(n, dtype=np.float64) * step
    return make_bars(close + 0.4, close, highs=close + 0.8, lows=close - 0.4)


def _geo(n: int, growth: float) -> DailyBars:
    close = 100.0 * np.power(growth, np.arange(n, dtype=np.float64))
    return make_bars(close / growth, close, highs=close * 1.002, lows=close * 0.998)


class CatalogOrderTests(unittest.TestCase):
    def test_batch6_is_appended_after_the_first_hundred_eight(self):
        self.assertEqual(tuple(s.key for s in LIBRARY[BATCH5_END:]), NEW)
        self.assertEqual(LIBRARY[BATCH5_END - 1].key, "three_dn")
        self.assertEqual(len(LIBRARY), BATCH5_END + len(NEW))
        for key, center in CENTERS.items():
            strategy = strategy_by_key(key)
            self.assertEqual(strategy.center, center)
            self.assertEqual(strategy.stop_frac, LIBRARY_STOP_FRAC)
            self.assertTrue(strategy.citation.tested_rule.strip())
            self.assertTrue(strategy.citation.implemented_rule.strip())

    def test_skipped_near_copies_are_not_keys(self):
        keys = {s.key for s in LIBRARY}
        # Close versus an SMA is already a seated rule. PGO and the disparity
        # index have that same sign.
        self.assertNotIn("pgo", keys)
        self.assertNotIn("disparity", keys)
        self.assertIn("sma200", keys)
        # The Aroon oscillator's sign is Aroon Up versus Aroon Down.
        self.assertNotIn("aroon_osc", keys)
        self.assertIn("aroon", keys)
        # The regression endpoint, and a one-step forecast of it, sit on
        # lib.regline and lib.linreg.
        self.assertNotIn("endpoint", keys)
        self.assertNotIn("forecast", keys)
        self.assertIn("regline", keys)
        # Floor R1 is a higher level of the same pivot. A second Camarilla
        # side, and a second OHLC variance, are not extra keys.
        self.assertNotIn("floor_r1", keys)
        self.assertNotIn("parkinson", keys)
        self.assertNotIn("decycler", keys)
        self.assertIn("floor_pivot", keys)
        self.assertIn("gk", keys)
        # Still blocked or skipped from earlier batches.
        self.assertNotIn("coppock", keys)
        self.assertNotIn("tema", keys)
        self.assertNotIn("ichimoku_chikou", keys)
        self.assertNotIn("nr4", keys)
        self.assertNotIn("ppo", keys)


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
                if key in CALENDAR:
                    self.assertEqual(first, 0, key)
                else:
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
        from pathlib import Path

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
    def test_demarker_is_the_high_low_ratio_and_not_rsi(self):
        up = _rise(40)
        down = _fall(40)
        strategy = strategy_by_key("demarker")
        value = demarker(up.high, up.low, 14)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 0.5)
        np.testing.assert_array_equal(strategy.signal(up, strategy.center), expected)
        self.assertTrue(bool(strategy.signal(up, strategy.center)[14:].all()))
        self.assertFalse(bool(strategy.signal(down, strategy.center)[14:].any()))
        bars = random_walk(80, seed=3)
        self.assertFalse(np.array_equal(
            strategy.signal(bars, strategy.center),
            strategy_by_key("rsi14").signal(bars, strategy_by_key("rsi14").center),
        ))

    def test_countdown_completes_above_the_average_and_is_not_the_setup(self):
        rise = np.linspace(50.0, 200.0, 220)
        pull = rise[-1] - np.arange(1, 45) * 0.25
        close = np.concatenate([rise, pull])
        bars = make_bars(close, highs=close + 0.05, lows=close - 0.05)
        signal = strategy_by_key("td_cd").signal(bars, strategy_by_key("td_cd").center)
        hits = np.flatnonzero(signal)
        self.assertEqual(hits.tolist(), [242])
        self.assertGreater(float(close[242]), float(sma(close, 200)[242]))
        # The completion bar is a close below the close four days earlier.
        # lib.td_setup needs the opposite comparison.
        setup = strategy_by_key("td_setup").signal(bars, {"count": 9, "trend": 200})
        self.assertFalse(bool(setup[242]))

    def test_trix_signal_is_above_its_average_and_not_the_zero_line(self):
        bars = random_walk(160, seed=4)
        value = trix(bars.close, 15)
        line = sma_skip_leading(value, 9)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & np.isfinite(line) & (value > line)
        signal = strategy_by_key("trix_sig").signal(bars, strategy_by_key("trix_sig").center)
        np.testing.assert_array_equal(signal, expected)
        zero = np.isfinite(value) & (value > 0.0)
        self.assertFalse(np.array_equal(signal, zero))

    def test_slow_stochastic_is_not_fast_k_versus_d(self):
        bars = random_walk(120, seed=5)
        fast_k = stochastic_k(bars.high, bars.low, bars.close, 14)
        slow_d = sma_skip_leading(sma_skip_leading(fast_k, 3), 3)
        line = sma_skip_leading(slow_d, 3)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(slow_d) & np.isfinite(line) & (slow_d > line)
        signal = strategy_by_key("stoch_slow").signal(bars, strategy_by_key("stoch_slow").center)
        np.testing.assert_array_equal(signal, expected)
        fast = strategy_by_key("stoch").signal(bars, strategy_by_key("stoch").center)
        self.assertFalse(np.array_equal(signal, fast))

    def test_zero_lag_tracks_a_rise_and_is_not_dema(self):
        up = _rise(80)
        down = _fall(80)
        strategy = strategy_by_key("zlema")
        line = zero_lag_ema(up.close, 20)
        np.testing.assert_array_equal(
            strategy.signal(up, strategy.center),
            np.isfinite(line) & (up.close > line),
        )
        ready = signal_ready(strategy, up, strategy.center)
        first = int(np.flatnonzero(ready)[0])
        self.assertTrue(bool(strategy.signal(up, strategy.center)[first:].all()))
        self.assertFalse(bool(strategy.signal(down, strategy.center)[first:].any()))
        bars = random_walk(100, seed=8)
        self.assertFalse(np.array_equal(
            strategy.signal(bars, strategy.center),
            strategy_by_key("dema").signal(bars, strategy_by_key("dema").center),
        ))

    def test_frama_hugs_a_straight_line_and_lets_a_jump_through(self):
        up = _rise(40)
        strategy = strategy_by_key("frama")
        # Dimension 1 makes alpha 1, so the average sits on the close.
        self.assertFalse(bool(strategy.signal(up, strategy.center).any()))
        close = np.array([100.0 + ((-1) ** i) * 1.5 for i in range(36)], dtype=np.float64)
        close[-1] = 112.0
        bars = make_bars(close, highs=close + 0.2, lows=close - 0.2)
        line = frama(bars.close, 16)
        self.assertGreater(float(bars.close[-1]), float(line[-1]))
        self.assertTrue(bool(strategy.signal(bars, strategy.center)[-1]))
        walked = random_walk(80, seed=9)
        self.assertFalse(np.array_equal(
            strategy.signal(walked, strategy.center),
            strategy_by_key("kama").signal(walked, strategy_by_key("kama").center),
        ))

    def test_super_smoother_lags_a_rise(self):
        up = _rise(40)
        down = _fall(40)
        strategy = strategy_by_key("ssmooth")
        line = super_smoother(up.close, 10)
        np.testing.assert_array_equal(
            strategy.signal(up, strategy.center),
            np.isfinite(line) & (up.close > line),
        )
        self.assertTrue(bool(strategy.signal(up, strategy.center)[2:].all()))
        self.assertFalse(bool(strategy.signal(down, strategy.center)[2:].any()))

    def test_cyber_cycle_rises_with_a_compounded_trend(self):
        up = _geo(80, 1.01)
        strategy = strategy_by_key("cyber")
        price = (up.high + up.low) / 2.0
        cycle = cyber_cycle(price, 0.07)
        prev = np.roll(cycle, 1)
        prev[:6] = np.nan
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(cycle) & np.isfinite(prev) & (cycle > prev)
        expected[:6] = False
        np.testing.assert_array_equal(strategy.signal(up, strategy.center), expected)
        self.assertTrue(bool(strategy.signal(up, strategy.center)[6:].all()))
        bars = random_walk(80, seed=10)
        self.assertFalse(np.array_equal(
            strategy.signal(bars, strategy.center),
            strategy_by_key("fisher").signal(bars, strategy_by_key("fisher").center),
        ))

    def test_laguerre_filter_is_not_the_laguerre_rsi(self):
        up = _rise(40)
        strategy = strategy_by_key("lfilter")
        line = laguerre_filter(up.close, 0.5)
        np.testing.assert_array_equal(
            strategy.signal(up, strategy.center),
            np.isfinite(line) & (up.close > line),
        )
        self.assertTrue(bool(strategy.signal(up, strategy.center)[1:].all()))
        self.assertFalse(bool(strategy.signal(_fall(40), strategy.center)[1:].any()))
        bars = random_walk(80, seed=11)
        self.assertFalse(np.array_equal(
            strategy.signal(bars, strategy.center),
            strategy_by_key("laguerre").signal(bars, strategy_by_key("laguerre").center),
        ))

    def test_alma_is_a_gaussian_average_and_not_the_hull(self):
        up = _rise(30)
        strategy = strategy_by_key("alma")
        line = alma(up.close, 9, 0.85, 6.0)
        np.testing.assert_array_equal(
            strategy.signal(up, strategy.center),
            np.isfinite(line) & (up.close > line),
        )
        self.assertTrue(bool(strategy.signal(up, strategy.center)[8:].all()))
        self.assertFalse(bool(strategy.signal(_fall(30), strategy.center)[8:].any()))
        bars = random_walk(60, seed=12)
        self.assertFalse(np.array_equal(
            strategy.signal(bars, {"n": 9, "offset": 85}),
            strategy_by_key("hma").signal(bars, {"n": 9}),
        ))

    def test_three_line_break_enters_on_the_white_reversal(self):
        close = np.array([10, 11, 12, 13, 9, 8, 7, 14, 15, 6, 5, 4, 16], dtype=np.float64)
        signal = strategy_by_key("tlbreak").signal(make_bars(close), {"n": 3})
        # Three white lines, three black lines, then the white break at index 7.
        self.assertFalse(bool(signal[:7].any()))
        self.assertTrue(bool(signal[7]))
        self.assertTrue(bool(signal[8]))
        self.assertFalse(bool(signal[9]))
        self.assertTrue(bool(signal[12]))
        outside = strategy_by_key("outside").signal(make_bars(close), {"n": 1})
        self.assertFalse(bool(outside[7]))

    def test_engulfing_is_the_body_and_not_the_outside_bar(self):
        n = 16
        close = np.full(n, 90.0)
        open_ = close - 0.4
        open_[-2] = 110.0
        close[-2] = 100.0
        open_[-1] = 99.0
        close[-1] = 112.0
        bars = make_bars(open_, close)
        signal = strategy_by_key("engulf").signal(bars, {"trend": 5})
        self.assertTrue(bool(signal[-1]))
        self.assertFalse(bool(signal[-2]))
        self.assertGreater(float(close[-1]), float(sma(close, 5)[-1]))
        # The outside-day rule waits for a later break of the high.
        self.assertFalse(bool(strategy_by_key("outside").signal(bars, {"n": 1})[-1]))

    def test_piercing_line_stops_short_of_the_prior_open(self):
        n = 16
        close = np.full(n, 90.0)
        open_ = close - 0.4
        open_[-2] = 110.0
        close[-2] = 100.0
        open_[-1] = 98.0
        close[-1] = 106.0
        bars = make_bars(open_, close)
        signal = strategy_by_key("pierce").signal(bars, {"trend": 5})
        self.assertTrue(bool(signal[-1]))
        # The same bar does not clear the prior open, so it is not an engulfing.
        self.assertFalse(bool(strategy_by_key("engulf").signal(bars, {"trend": 5})[-1]))
        close[-1] = 112.0
        engulfed = make_bars(open_, close)
        self.assertFalse(bool(strategy_by_key("pierce").signal(engulfed, {"trend": 5})[-1]))
        self.assertTrue(bool(strategy_by_key("engulf").signal(engulfed, {"trend": 5})[-1]))

    def test_three_white_soldiers_need_three_bodies(self):
        n = 30
        open_ = np.linspace(80.0, 100.0, n)
        close = open_ + 0.5
        high = close + 0.1
        low = open_ - 0.1
        open_[26], close[26], high[26], low[26] = 100.0, 104.0, 105.0, 99.0
        open_[27], close[27], high[27], low[27] = 101.0, 106.0, 106.4, 100.0
        open_[28], close[28], high[28], low[28] = 103.0, 108.0, 108.4, 102.0
        open_[29], close[29], high[29], low[29] = 105.0, 110.0, 110.3, 104.0
        bars = make_bars(open_, close, high, low)
        signal = strategy_by_key("soldiers").signal(bars, {"trend": 10})
        self.assertFalse(bool(signal[28]))
        self.assertTrue(bool(signal[29]))
        self.assertGreater(float(close[29]), float(sma(close, 10)[29]))
        lower = strategy_by_key("three_dn").signal(bars, {"n": 3, "trend": 10, "exit_ma": 5})
        self.assertFalse(bool(lower[29]))

    def test_oops_reclaims_yesterdays_low(self):
        close = np.array([10.0, 10.5, 10.2, 9.1], dtype=np.float64)
        low = np.array([9.6, 10.0, 9.2, 8.5], dtype=np.float64)
        high = np.array([10.4, 10.8, 10.6, 9.3], dtype=np.float64)
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("oops").signal(bars, {"n": 1})
        # Day 2 takes out day 1's low at 10 and closes back above it.
        self.assertFalse(bool(signal[1]))
        self.assertTrue(bool(signal[2]))
        self.assertFalse(bool(signal[3]))
        # A 20-day failure is a different rule, and this tape has no 20-day low.
        soup = strategy_by_key("turtle_soup").signal(bars, strategy_by_key("turtle_soup").center)
        self.assertFalse(bool(soup[2]))

    def test_variance_ratio_matches_the_indicator(self):
        rets = np.tile(np.concatenate([np.full(20, 0.01), np.full(20, -0.004)]), 6)
        close = 100.0 * np.exp(np.cumsum(np.concatenate([[0.0], rets])))
        bars = make_bars(close, highs=close * 1.001, lows=close * 0.999)
        ratio = variance_ratio(close, 16, 128)
        trend = sma(close, 50)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(ratio) & (ratio > 1.0) & np.isfinite(trend) & (close > trend)
        signal = strategy_by_key("vratio").signal(bars, {"q": 16, "win": 128, "trend": 50})
        np.testing.assert_array_equal(signal, expected)
        self.assertTrue(bool(signal.any()))
        recent = strategy_by_key("tsmom30d").signal(bars, {"n": 16})
        self.assertFalse(np.array_equal(signal, recent))

    def test_vhf_is_high_after_a_chop_and_is_not_choppiness(self):
        rng = np.random.default_rng(3)
        noise = 100.0 + np.cumsum(rng.normal(0, 2.0, 180))
        trend = noise[-1] + np.arange(1, 80)
        close = np.concatenate([noise, trend])
        bars = make_bars(close, highs=close + 0.01, lows=close - 0.01)
        value = vertical_horizontal_filter(close, 28)
        med = rolling_median_skip_leading(value, 100)
        average = sma(close, 50)
        with np.errstate(invalid="ignore"):
            expected = (
                np.isfinite(value) & np.isfinite(med) & (value > med)
                & np.isfinite(average) & (close > average)
            )
        signal = strategy_by_key("vhf").signal(bars, {"n": 28, "trend": 50})
        np.testing.assert_array_equal(signal, expected)
        self.assertGreater(float(value[220]), float(med[220]))
        self.assertTrue(bool(signal[220]))
        chop = strategy_by_key("chop").signal(bars, strategy_by_key("chop").center)
        self.assertFalse(np.array_equal(signal, chop))

    def test_accelerator_is_the_awesome_oscillator_above_its_average(self):
        up = _geo(80, 1.01)
        strategy = strategy_by_key("accel")
        ao = awesome_oscillator(up.high, up.low, 5, 34)
        line = sma_skip_leading(ao, 5)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(ao) & np.isfinite(line) & (ao > line)
        np.testing.assert_array_equal(strategy.signal(up, strategy.center), expected)
        ready = signal_ready(strategy, up, strategy.center)
        first = int(np.flatnonzero(ready)[0])
        self.assertTrue(bool(strategy.signal(up, strategy.center)[first:].all()))
        bars = random_walk(120, seed=13)
        self.assertFalse(np.array_equal(
            strategy.signal(bars, strategy.center),
            strategy_by_key("awesome").signal(bars, strategy_by_key("awesome").center),
        ))

    def test_gator_expands_on_a_rise_and_is_not_the_alignment(self):
        up = _rise(80)
        strategy = strategy_by_key("gator")
        ready = signal_ready(strategy, up, strategy.center)
        first = int(np.flatnonzero(ready)[0])
        self.assertTrue(bool(strategy.signal(up, strategy.center)[first:].all()))
        self.assertFalse(bool(strategy.signal(_fall(80), strategy.center)[first:].any()))
        bars = random_walk(100, seed=14)
        self.assertFalse(np.array_equal(
            strategy.signal(bars, strategy.center),
            strategy_by_key("alligator").signal(bars, strategy_by_key("alligator").center),
        ))

    def test_camarilla_h3_is_not_the_floor_pivot(self):
        # Yesterday's range is 10. H3 = 105 + 10 * 1.1 / 4 = 107.75.
        # The floor pivot is (110 + 100 + 105) / 3 = 105.
        n = 12
        close = np.full(n, 90.0)
        high = np.full(n, 91.0)
        low = np.full(n, 89.0)
        high[-2], low[-2], close[-2] = 110.0, 100.0, 105.0
        close[-1] = 106.0
        bars = make_bars(close, close, high, low)
        self.assertFalse(bool(strategy_by_key("camarilla").signal(bars, {"trend": 5})[-1]))
        self.assertTrue(bool(strategy_by_key("floor_pivot").signal(bars, {"trend": 5})[-1]))
        close[-1] = 108.0
        above = make_bars(close, close, high, low)
        self.assertTrue(bool(strategy_by_key("camarilla").signal(above, {"trend": 5})[-1]))

    def test_regression_value_can_disagree_with_the_slope(self):
        line = 50.0 + np.arange(40, dtype=np.float64)
        bars = make_bars(line)
        # A perfect line has a positive slope and a zero residual.
        self.assertGreater(float(linreg_slope(line, 20)[-1]), 0.0)
        self.assertAlmostEqual(float(line[-1]), float(linreg_value(line, 20)[-1]), places=6)
        self.assertTrue(bool(strategy_by_key("linreg").signal(bars, {"n": 20})[-1]))
        self.assertFalse(bool(strategy_by_key("regline").signal(bars, {"n": 20})[-1]))
        bent = np.concatenate(([50.0], np.linspace(80.0, 40.0, 18), [70.0]))
        self.assertLess(float(linreg_slope(bent, 20)[-1]), 0.0)
        self.assertGreater(float(bent[-1]), float(linreg_value(bent, 20)[-1]))
        bent_bars = make_bars(bent)
        self.assertFalse(bool(strategy_by_key("linreg").signal(bent_bars, {"n": 20})[-1]))
        self.assertTrue(bool(strategy_by_key("regline").signal(bent_bars, {"n": 20})[-1]))

    def test_garman_klass_is_quiet_after_a_wide_range(self):
        early = np.linspace(80.0, 100.0, 120)
        late = np.linspace(100.0, 130.0, 80)
        close = np.concatenate([early, late])
        high = close.copy()
        low = close.copy()
        high[:120] = close[:120] * 1.04
        low[:120] = close[:120] * 0.96
        high[120:] = close[120:] * 1.001
        low[120:] = close[120:] * 0.999
        opens = np.concatenate(([close[0]], close[:-1]))
        bars = make_bars(opens, close, high, low)
        value = garman_klass(bars.open, bars.high, bars.low, bars.close, 14)
        med = rolling_median_skip_leading(value, 100)
        trend = sma(close, 50)
        with np.errstate(invalid="ignore"):
            expected = (
                np.isfinite(value) & np.isfinite(med) & (value < med)
                & np.isfinite(trend) & (close > trend)
            )
        signal = strategy_by_key("gk").signal(bars, {"n": 14, "trend": 50})
        np.testing.assert_array_equal(signal, expected)
        self.assertTrue(bool(signal[-10:].all()))
        ulcer = strategy_by_key("ulcer").signal(bars, strategy_by_key("ulcer").center)
        self.assertFalse(np.array_equal(signal, ulcer))

    def test_harami_uses_the_body_and_not_the_inside_day(self):
        n = 16
        close = np.full(n, 90.0)
        open_ = close - 0.4
        high = np.maximum(open_, close) + 0.2
        low = np.minimum(open_, close) - 0.2
        open_[-2], close[-2], high[-2], low[-2] = 110.0, 100.0, 112.0, 98.0
        open_[-1], close[-1], high[-1], low[-1] = 104.0, 106.0, 113.0, 97.0
        bars = make_bars(open_, close, high, low)
        signal = strategy_by_key("harami").signal(bars, {"trend": 5})
        self.assertTrue(bool(signal[-1]))
        # The wicks are outside the prior bar, so this is not an inside day,
        # and the inside-day rule would wait for a later break anyway.
        self.assertFalse(bool(strategy_by_key("inside_day").signal(bars, {"n": 1})[-1]))

    def test_expiration_week_is_the_three_sessions_into_the_third_friday(self):
        start = int(datetime(2023, 11, 14, tzinfo=timezone.utc).timestamp() * 1000)
        bars = make_bars(np.full(8, 100.0), day0=start)
        signal = strategy_by_key("expiry").signal(bars, {"n": 3})
        # Nov 14 decides Nov 15. The third Friday of November 2023 is the 17th.
        self.assertEqual(int(third_friday_dom(np.array([bars.ts[0] + DAY_MS]))[0]), 17)
        self.assertTrue(bool(signal[0]))
        self.assertTrue(bool(signal[1]))
        self.assertTrue(bool(signal[2]))
        self.assertFalse(bool(signal[3]))
        short = strategy_by_key("expiry").signal(bars, {"n": 2})
        self.assertFalse(bool(short[0]))
        self.assertTrue(bool(short[1]))
        self.assertTrue(bool(short[2]))
        tom = strategy_by_key("tom").signal(bars, strategy_by_key("tom").center)
        self.assertFalse(np.array_equal(signal, tom))


if __name__ == "__main__":
    unittest.main()
