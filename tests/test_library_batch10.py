"""Batch-10 daily signals: on/off, warm-up, no look-ahead, and a trade on the tape.

The 191 names ahead of these keys stay where they are. Batch 10 is what a
library-mode claim leases after those 191 are tested. The shared score
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
    adx_wilder,
    dominant_period,
    ema,
    kase_devstop,
    mesa_adaptive,
    prior_percentile,
    rogers_satchell,
    rolling_max,
    rolling_median_skip_leading,
    rolling_min,
    rsi_wilder,
    shift_previous,
    sma,
    trend_intensity,
    yang_zhang,
)
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC, a Wednesday
BATCH9_END = 191
NEW = (
    "holy", "bar8020", "keyrev", "three_in", "meet",
    "separate", "pnf", "ha", "kase", "yz",
    "rsvol", "tii", "cv", "dcyc", "hook",
    "outclose", "sandwich", "pinball", "gri", "pctile",
)
CENTERS = {
    "holy": {"n": 20},
    "bar8020": {"trend": 200},
    "keyrev": {"trend": 200},
    "three_in": {"trend": 200},
    "meet": {"trend": 200},
    "separate": {"trend": 200},
    "pnf": {"box": 1},
    "ha": {"n": 1},
    "kase": {"n": 20},
    "yz": {"n": 20, "trend": 200},
    "rsvol": {"n": 20, "trend": 200},
    "tii": {"n": 20},
    "cv": {"n": 10},
    "dcyc": {"n": 1},
    "hook": {"trend": 200},
    "outclose": {"trend": 200},
    "sandwich": {"trend": 200},
    "pinball": {"trend": 200},
    "gri": {"n": 20, "trend": 200},
    "pctile": {"n": 20},
}


def _ready_index(key: str, params: dict) -> int:
    trend = int(params.get("trend", 0))
    if key == "holy":
        return max(28, int(params["n"]) - 1, 199)
    if key == "bar8020":
        return trend - 1
    if key in ("keyrev", "three_in"):
        return max(2, trend - 1)
    if key in ("meet", "separate", "sandwich"):
        return max(5, trend - 1)
    if key == "pnf":
        return 1
    if key == "ha":
        return max(int(params["n"]) - 1, 1)
    if key in ("kase", "tii"):
        return int(params["n"]) - 1
    if key == "yz":
        return max(int(params["n"]) + 99, trend - 1)
    if key == "rsvol":
        return max(int(params["n"]) + 98, trend - 1)
    if key == "cv":
        return max(2 * int(params["n"]) - 1, 199)
    if key == "dcyc":
        return int(params["n"]) * 50
    if key in ("hook", "outclose"):
        return max(1, trend - 1)
    if key == "pinball":
        return max(4, trend - 1)
    if key == "gri":
        return max(int(params["n"]), trend - 1)
    if key == "pctile":
        return int(params["n"])
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
    def test_batch10_is_appended_after_the_first_hundred_ninety_one(self):
        end = BATCH9_END + len(NEW)
        self.assertEqual(tuple(s.key for s in LIBRARY[BATCH9_END:end]), NEW)
        self.assertEqual(LIBRARY[BATCH9_END - 1].key, "safezone")
        self.assertEqual(LIBRARY[end].key, "park")
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
            "thrust", "thrusting", "kumo", "cloud_twist", "ichimoku_twist",
            "coppock", "obv", "cmf", "vwap",
        ):
            self.assertNotIn(skipped, keys)
        self.assertIn("holy", keys)
        self.assertIn("yz", keys)
        self.assertIn("pnf", keys)
        self.assertIn("pctile", keys)
        self.assertIn("ichimoku_cloud", keys)


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

    def test_homodyne_period_ignores_the_mama_limits(self):
        bars = random_walk(120, seed=4)
        left = mesa_adaptive(bars.high, bars.low, 0.5, 0.05)[2]
        right = mesa_adaptive(bars.high, bars.low, 0.4, 0.05)[2]
        np.testing.assert_array_equal(left, right)
        np.testing.assert_array_equal(left, dominant_period(bars.high, bars.low))
        self.assertTrue(bool(np.isfinite(left[40:]).all()))


class OnOffTests(unittest.TestCase):
    def test_holy_grail_needs_a_fading_adx_and_an_ema_tag(self):
        bars = random_walk(260, seed=5)
        _plus, _minus, adx = adx_wilder(bars.high, bars.low, bars.close, 14)
        prev = shift_previous(adx, 1)
        line = ema(bars.close, 20)
        trend = sma(bars.close, 200)
        with np.errstate(invalid="ignore"):
            tagged = np.isfinite(line) & (bars.low <= line) & (bars.close > line)
            fading = np.isfinite(adx) & np.isfinite(prev) & (adx > 30.0) & (adx < prev)
            expected = tagged & fading & np.isfinite(trend) & (bars.close > trend)
        signal = strategy_by_key("holy").signal(bars, {"n": 20})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("adx14").signal(bars, strategy_by_key("adx14").center),
        ))

    def test_key_reversal_does_not_need_yesterdays_high(self):
        close = np.array([10.0, 9.0, 9.2], dtype=np.float64)
        low = np.array([9.5, 8.5, 8.0], dtype=np.float64)
        high = np.array([10.5, 9.4, 9.3], dtype=np.float64)
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("keyrev").signal(bars, {"trend": 2})
        self.assertTrue(bool(signal[2]))
        self.assertFalse(bool(signal[1]))
        # Yesterday's high is 9.4. A close of 9.2 is through the close, not the high.
        self.assertLess(float(close[2]), float(high[1]))
        self.assertFalse(bool(strategy_by_key("twobar").signal(bars, {"n": 1})[2]))

    def test_three_inside_confirms_the_harami_on_the_next_close(self):
        open_ = np.array([12.0, 9.0, 11.0], dtype=np.float64)
        close = np.array([8.0, 10.0, 13.0], dtype=np.float64)
        high = np.array([12.0, 10.5, 13.2], dtype=np.float64)
        low = np.array([7.5, 8.8, 10.5], dtype=np.float64)
        bars = make_bars(open_, close, high, low)
        signal = strategy_by_key("three_in").signal(bars, {"trend": 2})
        self.assertTrue(bool(signal[2]))
        self.assertFalse(bool(strategy_by_key("harami").signal(bars, {"trend": 2})[2]))
        self.assertTrue(bool(strategy_by_key("harami").signal(bars, {"trend": 2})[1]))

    def test_8020_hook_and_outside_close_use_the_bar_itself(self):
        open_ = np.array([10.0, 10.2, 9.0], dtype=np.float64)
        close = np.array([10.4, 12.0, 14.0], dtype=np.float64)
        high = np.array([15.0, 12.2, 14.2], dtype=np.float64)
        low = np.array([8.0, 10.0, 8.5], dtype=np.float64)
        bars = make_bars(open_, close, high, low)
        # Bar 2 opens near the low and closes near the high.
        self.assertTrue(bool(strategy_by_key("bar8020").signal(bars, {"trend": 2})[2]))
        self.assertTrue(bool(strategy_by_key("hook").signal(bars, {"trend": 2})[2]))
        self.assertTrue(bool(strategy_by_key("outclose").signal(bars, {"trend": 2})[2]))
        self.assertFalse(bool(strategy_by_key("outside").signal(bars, {"n": 1})[2]))

    def test_meeting_separating_and_sandwich_use_the_equal_band(self):
        # Ranges of 20 make a 5 percent band of 1.0. Closes and opens that
        # differ by less than that match. The trend SMA is 2 days.
        open_ = np.array([20.0, 20.0, 20.0, 20.0, 30.0, 20.0, 20.2], dtype=np.float64)
        close = np.array([10.0, 10.0, 10.0, 10.0, 20.0, 20.4, 19.0], dtype=np.float64)
        high = open_ + 5.0
        low = close - 5.0
        bars = make_bars(open_, close, high, low)
        self.assertTrue(bool(strategy_by_key("meet").signal(bars, {"trend": 2})[5]))
        # Bar 6 reuses bar 5's open within the band and is a white... it is black.
        # Rebuild a separating bar: black open 20 close 10, next open near 20 and white.
        open2 = np.array([20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.2], dtype=np.float64)
        close2 = np.array([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 28.0], dtype=np.float64)
        sep = make_bars(open2, close2, open2 + 5.0, close2 - 5.0)
        self.assertTrue(bool(strategy_by_key("separate").signal(sep, {"trend": 2})[6]))
        # Sandwich: black, white, black, and the two black closes meet.
        open3 = np.array([20.0, 20.0, 20.0, 20.0, 30.0, 16.0, 22.0], dtype=np.float64)
        close3 = np.array([10.0, 10.0, 10.0, 10.0, 20.0, 18.0, 20.2], dtype=np.float64)
        sand = make_bars(open3, close3, np.maximum(open3, close3) + 5.0, np.minimum(open3, close3) - 5.0)
        self.assertTrue(bool(strategy_by_key("sandwich").signal(sand, {"trend": 2})[6]))

    def test_point_and_figure_breaks_the_prior_rising_column(self):
        # One percent boxes. Rise, a three-box reversal down, then a rise
        # through the first column's high.
        close = np.array(
            [100, 100, 106, 112, 112, 100, 100, 108, 114],
            dtype=np.float64,
        )
        bars = make_bars(close, close)
        signal = strategy_by_key("pnf").signal(bars, {"box": 1})
        self.assertFalse(bool(signal[:6].any()))
        self.assertTrue(bool(signal[-1]))
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("renko").signal(bars, {"brick": 1}),
        ))

    def test_heikin_ashi_white_is_not_a_raw_white_candle(self):
        rise = 100.0 + np.arange(12, dtype=np.float64)
        up = make_bars(rise - 0.4, rise, highs=rise + 0.2, lows=rise - 0.8)
        fall_close = 200.0 - np.arange(12, dtype=np.float64)
        down = make_bars(fall_close + 0.4, fall_close, highs=fall_close + 0.6, lows=fall_close - 0.2)
        self.assertTrue(bool(strategy_by_key("ha").signal(up, {"n": 1})[1:].all()))
        self.assertFalse(bool(strategy_by_key("ha").signal(down, {"n": 1}).any()))
        self.assertFalse(bool(strategy_by_key("ha").signal(up, {"n": 1})[0]))

    def test_kase_ratchets_while_long(self):
        close = np.array(
            [10, 11, 12, 13, 14, 16, 18, 20, 19, 18, 12],
            dtype=np.float64,
        )
        bars = make_bars(close, close, highs=close + 0.4, lows=close - 0.4)
        raw = kase_devstop(bars.high, bars.low, bars.close, 5)
        signal = strategy_by_key("kase").signal(bars, {"n": 5})
        self.assertFalse(bool(signal[:4].any()))
        self.assertTrue(bool(np.isfinite(raw[4:]).all()))
        self.assertTrue(bool(signal[7]))
        self.assertGreater(float(close[7]), float(raw[7]))
        self.assertFalse(bool(signal[-1]))

    def test_rogers_satchell_term_matches_the_open_formula(self):
        open_ = np.full(20, 100.0)
        high = np.full(20, 110.0)
        low = np.full(20, 90.0)
        close = np.full(20, 105.0)
        ho = np.log(110.0 / 100.0)
        lo = np.log(90.0 / 100.0)
        co = np.log(105.0 / 100.0)
        term = ho * (ho - co) + lo * (lo - co)
        value = rogers_satchell(open_, high, low, close, 20)
        self.assertAlmostEqual(float(value[19]), float(term), places=10)
        self.assertTrue(np.isnan(value[18]))

    def test_quiet_variance_is_below_its_median_and_not_garman_klass(self):
        bars = random_walk(360, seed=6)
        yz = yang_zhang(bars.open, bars.high, bars.low, bars.close, 20)
        rs = rogers_satchell(bars.open, bars.high, bars.low, bars.close, 20)
        self.assertFalse(np.allclose(yz[120:], rs[120:], equal_nan=True))
        med = rolling_median_skip_leading(yz, 100)
        trend = sma(bars.close, 200)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(yz) & np.isfinite(med) & (yz < med) & np.isfinite(trend) & (bars.close > trend)
        np.testing.assert_array_equal(
            strategy_by_key("yz").signal(bars, {"n": 20, "trend": 200}),
            expected,
        )
        self.assertFalse(np.array_equal(
            strategy_by_key("yz").signal(bars, {"n": 20, "trend": 200}),
            strategy_by_key("rsvol").signal(bars, {"n": 20, "trend": 200}),
        ))
        self.assertFalse(np.array_equal(
            strategy_by_key("rsvol").signal(bars, {"n": 20, "trend": 200}),
            strategy_by_key("gk").signal(bars, {"n": 20, "trend": 200}),
        ))

    def test_trend_intensity_is_the_share_above_the_mean(self):
        held = np.array([1, 1, 1, 10, 10, 10, 10, 10], dtype=np.float64)
        bars = make_bars(held, held)
        value = trend_intensity(held, 8)
        self.assertAlmostEqual(float(value[7]), 62.5, places=6)
        self.assertTrue(bool(strategy_by_key("tii").signal(bars, {"n": 8})[7]))
        line = np.arange(1, 21, dtype=np.float64)
        straight = make_bars(line, line)
        self.assertFalse(bool(strategy_by_key("tii").signal(straight, {"n": 20})[19]))

    def test_percentile_excludes_today(self):
        close = np.arange(1, 31, dtype=np.float64)
        bars = make_bars(close, close)
        level = prior_percentile(close, 20, 80.0)
        self.assertAlmostEqual(float(level[20]), float(np.percentile(close[:20], 80, method="linear")), places=8)
        self.assertTrue(bool(strategy_by_key("pctile").signal(bars, {"n": 20})[20]))
        self.assertFalse(bool(strategy_by_key("pctile").signal(bars, {"n": 20})[19]))

    def test_dominant_cycle_uses_the_clamped_period(self):
        bars = random_walk(180, seed=7)
        period = dominant_period(bars.high, bars.low)
        close = bars.close
        expected = np.zeros(len(bars), dtype=bool)
        for t in range(50, len(bars)):
            lag = int(np.clip(int(round(float(period[t]))), 6, 50))
            expected[t] = close[t] > close[t - lag]
        signal = strategy_by_key("dcyc").signal(bars, {"n": 1})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(bool(signal[:50].any()))

    def test_pinball_is_the_rsi_of_the_percent_change(self):
        bars = gapped_walk(80, seed=8)
        close = bars.close
        ret = np.full(close.shape[0], np.nan)
        ret[1:] = close[1:] / close[:-1] - 1.0
        rsi = np.full(close.shape[0], np.nan)
        rsi[1:] = rsi_wilder(ret[1:], 3)
        trend = sma(close, 20)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(rsi) & (rsi < 30.0) & np.isfinite(trend) & (close > trend)
        np.testing.assert_array_equal(
            strategy_by_key("pinball").signal(bars, {"trend": 20}),
            expected,
        )

    def test_range_index_falls(self):
        bars = random_walk(80, seed=9)
        n = 10
        span = rolling_max(bars.high, n) - rolling_min(bars.low, n)
        with np.errstate(divide="ignore", invalid="ignore"):
            value = np.where(span > 0.0, np.log(span) / np.log(float(n)), np.nan)
        prev = shift_previous(value, 1)
        trend = sma(bars.close, 20)
        with np.errstate(invalid="ignore"):
            expected = (
                np.isfinite(value) & np.isfinite(prev) & (value < prev)
                & np.isfinite(trend) & (bars.close > trend)
            )
        np.testing.assert_array_equal(
            strategy_by_key("gri").signal(bars, {"n": 10, "trend": 20}),
            expected,
        )

    def test_new_signals_are_not_their_named_cousins(self):
        bars = gapped_walk(420, seed=11)
        pairs = (
            ("holy", "adx14"),
            ("bar8020", "ibs"),
            ("keyrev", "twobar"),
            ("three_in", "harami"),
            ("meet", "pierce"),
            ("separate", "belt"),
            ("pnf", "renko"),
            ("ha", "qstick"),
            ("kase", "safezone"),
            ("yz", "gk"),
            ("rsvol", "gk"),
            ("tii", "psych"),
            ("cv", "mass_index"),
            ("dcyc", "tsmom30d"),
            ("hook", "hammer"),
            ("outclose", "outside"),
            ("sandwich", "matchlow"),
            ("pinball", "rsi2"),
            ("gri", "chop"),
            ("pctile", "donchian20"),
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
