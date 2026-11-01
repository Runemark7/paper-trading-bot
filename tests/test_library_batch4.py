"""Batch-4 daily signals: on/off, warm-up, and no look-ahead.

The original 15, the 17 batch-2 names, and the 24 batch-3 names stay ahead
of these keys, with the same centers. Batch 4 is what a library-mode claim
leases after those 56 are tested. The shared score start stays 2018-12-01.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np

from hedge_fund.library.catalog import LIBRARY, grid_params, signal_ready, strategy_by_key
from hedge_fund.library.daily import DAY_MS, DailyBars
from hedge_fund.library.indicators import (
    chande_momentum,
    dema,
    detrended_price,
    fisher_transform,
    know_sure_thing,
    mass_index,
    qstick,
    relative_vigor,
    relative_volatility,
    schaff_trend,
    sma,
    sma_skip_leading,
    ulcer_index,
    weekday_utc,
)
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC, a Wednesday
BATCH3_END = 56
NEW = (
    "ichimoku_tk", "guppy", "elder_ray", "dema", "cmo", "qstick", "kst",
    "schaff", "fisher", "rvi", "dpo", "wilder_vol", "bb_squeeze", "turtle_soup",
    "rsi2_cum", "rev1m", "tsmom12_1", "bll_trb150", "nr7", "inside_day",
    "floor_pivot", "fractal", "mass_index", "relvol", "ulcer", "weekend",
)
CALENDAR = ("weekend",)
CENTERS = {
    "ichimoku_tk": {"conv": 9, "base": 26},
    "guppy": {"fast": 3, "slow": 60},
    "elder_ray": {"n": 13},
    "dema": {"n": 20},
    "cmo": {"n": 20},
    "qstick": {"n": 8},
    "kst": {"sig": 9},
    "schaff": {"cycle": 10, "buy": 25},
    "fisher": {"n": 10},
    "rvi": {"n": 10},
    "dpo": {"n": 20},
    "wilder_vol": {"atr": 14, "mult": 3},
    "bb_squeeze": {"n": 20, "kc": 15},
    "turtle_soup": {"n": 20, "hold": 2, "trend": 200},
    "rsi2_cum": {"sum_lt": 35, "trend": 200, "exit_ma": 5},
    "rev1m": {"n": 21, "trend": 200},
    "tsmom12_1": {"skip": 21, "slow": 252},
    "bll_trb150": {"n": 150, "hold": 10},
    "nr7": {"n": 7, "trend": 200},
    "inside_day": {"n": 1},
    "floor_pivot": {"trend": 200},
    "fractal": {"wings": 2},
    "mass_index": {"ema": 9, "span": 25, "trend": 200},
    "relvol": {"n": 14, "thr": 50},
    "ulcer": {"n": 14, "trend": 200},
    "weekend": {"span": 2},
}


def _ready_index(key: str, params: dict) -> int:
    if key == "ichimoku_tk":
        return max(params["conv"], params["base"]) - 1
    if key == "guppy":
        return params["slow"] - 1
    if key == "elder_ray":
        return params["n"]
    if key == "dema":
        return 2 * params["n"] - 1
    if key == "cmo":
        return params["n"]
    if key == "qstick":
        return params["n"] - 1
    if key == "kst":
        return 30 + 15 - 1 + params["sig"] - 1
    if key == "schaff":
        return (50 - 1) + 2 * (params["cycle"] - 1) + 1
    if key == "fisher":
        return params["n"]
    if key == "rvi":
        return params["n"] + 5
    if key == "dpo":
        return params["n"] - 1
    if key == "wilder_vol":
        return params["atr"]
    if key == "bb_squeeze":
        return params["n"] + 1
    if key == "turtle_soup":
        return max(params["n"], params["trend"] - 1)
    if key == "rsi2_cum":
        return max(3, params["trend"] - 1, params["exit_ma"] - 1)
    if key == "rev1m":
        return max(params["n"], params["trend"] - 1)
    if key == "tsmom12_1":
        return params["skip"] + params["slow"]
    if key == "bll_trb150":
        return params["n"]
    if key == "nr7":
        return max(params["n"], params["trend"] - 1)
    if key == "inside_day":
        return params["n"]
    if key == "floor_pivot":
        return max(1, params["trend"] - 1)
    if key == "fractal":
        return 2 * params["wings"]
    if key == "mass_index":
        cross = 2 * (params["ema"] - 1) + params["span"]
        return max(cross, params["trend"] - 1)
    if key == "relvol":
        return 2 * params["n"] - 2
    if key == "ulcer":
        return max(params["n"] + 98, params["trend"] - 1)
    if key == "weekend":
        return 0
    raise AssertionError(key)


def make_bars(opens, closes=None, highs=None, lows=None, day0=DAY0) -> DailyBars:
    o = np.asarray(opens, dtype=np.float64)
    c = o.copy() if closes is None else np.asarray(closes, dtype=np.float64)
    h = np.maximum(o, c) if highs is None else np.asarray(highs, dtype=np.float64)
    lo = np.minimum(o, c) if lows is None else np.asarray(lows, dtype=np.float64)
    ts = day0 + np.arange(o.shape[0], dtype=np.int64) * DAY_MS
    return DailyBars(ts=ts, open=o, high=h, low=lo, close=c)


def random_walk(n: int = 560, seed: int = 19) -> DailyBars:
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
    return make_bars(close, highs=close + 0.4, lows=close - 0.6)


def _fall(n: int, step: float = 1.0) -> DailyBars:
    close = 100.0 + n - np.arange(n, dtype=np.float64) * step
    return make_bars(close, highs=close + 0.4, lows=close - 0.6)


class CatalogOrderTests(unittest.TestCase):
    def test_batch4_is_appended_after_the_first_fifty_six(self):
        self.assertEqual(tuple(s.key for s in LIBRARY[BATCH3_END:]), NEW)
        self.assertEqual(LIBRARY[BATCH3_END - 1].key, "halloween")
        self.assertEqual(len(LIBRARY), BATCH3_END + len(NEW))
        for key, center in CENTERS.items():
            strategy = strategy_by_key(key)
            self.assertEqual(strategy.center, center)
            self.assertEqual(strategy.stop_frac, LIBRARY_STOP_FRAC)
            self.assertTrue(strategy.citation.tested_rule.strip())
            self.assertTrue(strategy.citation.implemented_rule.strip())

    def test_skipped_near_copies_are_not_keys(self):
        keys = {s.key for s in LIBRARY}
        # Close versus the close 26 days ago is inside lib.tsmom30d's neighbours.
        self.assertNotIn("ichimoku_chikou", keys)
        self.assertIn("ichimoku_tk", keys)
        self.assertIn("tsmom30d", keys)
        # The sign of a triple EMA is lib.trix.
        self.assertNotIn("tema", keys)
        self.assertIn("trix", keys)
        self.assertIn("dema", keys)
        # Quarter-end turn is a subset of the turn of the month.
        self.assertNotIn("toq", keys)
        self.assertIn("tom", keys)
        self.assertIn("weekend", keys)
        self.assertNotIn("heikin", keys)


class WarmupAndLookaheadTests(unittest.TestCase):
    def test_each_new_signal_does_not_look_ahead(self):
        bars = random_walk(640)
        cuts = (160, 280, 400, 520)
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
    def test_tenkan_kijun_follows_the_slope_and_ignores_the_cloud(self):
        up = _rise(80)
        down = _fall(80)
        params = strategy_by_key("ichimoku_tk").center
        risen = strategy_by_key("ichimoku_tk").signal(up, params)
        fallen = strategy_by_key("ichimoku_tk").signal(down, params)
        self.assertTrue(bool(risen[25:].all()))
        self.assertFalse(bool(fallen[25:].any()))
        # The cloud needs span 52 displaced by the base. Forty bars cannot light it.
        short = _rise(40)
        cloud = strategy_by_key("ichimoku").signal(short, strategy_by_key("ichimoku").center)
        cross = strategy_by_key("ichimoku_tk").signal(short, params)
        self.assertFalse(bool(cloud.any()))
        self.assertTrue(bool(cross[25:].any()))

    def test_guppy_and_dema_turn_on_in_a_rise_and_stay_off_when_flat(self):
        rise = _rise(120)
        flat = make_bars(np.full(120, 50.0), highs=np.full(120, 51.0), lows=np.full(120, 49.0))
        for key in ("guppy", "dema"):
            params = strategy_by_key(key).center
            up = strategy_by_key(key).signal(rise, params)
            still = strategy_by_key(key).signal(flat, params)
            ready = signal_ready(strategy_by_key(key), rise, params)
            first = int(np.flatnonzero(ready)[0])
            self.assertTrue(bool(up[first:].all()), key)
            self.assertFalse(bool(still.any()), key)

    def test_elder_cmo_qstick_and_dpo_match_their_indicators(self):
        bars = random_walk(240, seed=3)
        line = strategy_by_key("elder_ray")
        from hedge_fund.library.indicators import ema, shift_previous

        ema_line = ema(bars.close, 13)
        bear = bars.low - ema_line
        elder = (
            np.isfinite(ema_line) & np.isfinite(shift_previous(ema_line, 1))
            & np.isfinite(bear) & np.isfinite(shift_previous(bear, 1))
            & (ema_line > shift_previous(ema_line, 1))
            & (bear < 0.0)
            & (bear > shift_previous(bear, 1))
        )
        np.testing.assert_array_equal(line.signal(bars, line.center), elder)

        cmo = chande_momentum(bars.close, 20)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(cmo) & (cmo > 0.0)
        np.testing.assert_array_equal(
            strategy_by_key("cmo").signal(bars, strategy_by_key("cmo").center), expected,
        )
        bodies = qstick(bars.open, bars.close, 8)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(bodies) & (bodies > 0.0)
        np.testing.assert_array_equal(
            strategy_by_key("qstick").signal(bars, strategy_by_key("qstick").center), expected,
        )
        # Bodies are the open. A series whose open equals the close is not Qstick-long.
        flat = make_bars(np.full(30, 10.0))
        self.assertFalse(bool(strategy_by_key("qstick").signal(flat, strategy_by_key("qstick").center).any()))
        detached = detrended_price(bars.close, 20)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(detached) & (detached > 0.0)
        np.testing.assert_array_equal(
            strategy_by_key("dpo").signal(bars, strategy_by_key("dpo").center), expected,
        )

    def test_kst_fisher_rvi_and_relvol_match_their_indicators(self):
        bars = random_walk(240, seed=5)
        value = know_sure_thing(bars.close)
        signal = sma_skip_leading(value, 9)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & np.isfinite(signal) & (value > signal)
        np.testing.assert_array_equal(
            strategy_by_key("kst").signal(bars, strategy_by_key("kst").center), expected,
        )
        fish = fisher_transform(bars.high, bars.low, 10)
        from hedge_fund.library.indicators import shift_previous

        prev = shift_previous(fish, 1)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(fish) & np.isfinite(prev) & (fish > prev)
        np.testing.assert_array_equal(
            strategy_by_key("fisher").signal(bars, strategy_by_key("fisher").center), expected,
        )
        rvi, rvi_signal = relative_vigor(bars.open, bars.high, bars.low, bars.close, 10)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(rvi) & np.isfinite(rvi_signal) & (rvi > rvi_signal)
        np.testing.assert_array_equal(
            strategy_by_key("rvi").signal(bars, strategy_by_key("rvi").center), expected,
        )
        rel = relative_volatility(bars.high, bars.low, bars.close, 14)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(rel) & (rel > 50.0)
        np.testing.assert_array_equal(
            strategy_by_key("relvol").signal(bars, strategy_by_key("relvol").center), expected,
        )
        self.assertFalse(np.array_equal(
            strategy_by_key("rvi").signal(bars, strategy_by_key("rvi").center),
            strategy_by_key("relvol").signal(bars, strategy_by_key("relvol").center),
        ))

    def test_schaff_buys_the_cross_above_25_and_flats_under_75(self):
        bars = random_walk(400, seed=8)
        value = schaff_trend(bars.close, 10)
        got = strategy_by_key("schaff").signal(bars, strategy_by_key("schaff").center)
        self.assertTrue(bool(got.any()))
        self.assertFalse(bool(got.all()))
        # A cross above 25 turns the book long. A later cross below 75 turns it off.
        prev = np.roll(value, 1)
        prev[0] = np.nan
        crosses = np.flatnonzero(np.isfinite(prev) & (prev <= 25.0) & (value > 25.0))
        self.assertGreater(crosses.size, 0)
        self.assertTrue(bool(got[int(crosses[0])]))

    def test_wilder_band_buys_the_jump_and_is_not_a_keltner(self):
        n = 40
        close = np.full(n, 100.0)
        high = np.full(n, 101.0)
        low = np.full(n, 99.0)
        close[20] = 110.0
        high[20] = 110.5
        low[20] = 100.0
        for i, price in enumerate((108.0, 106.0, 104.0, 102.0), start=21):
            close[i] = price
            high[i] = price + 0.5
            low[i] = price - 0.5
        jumped = make_bars(close, close, high, low)
        params = strategy_by_key("wilder_vol").center
        signal = strategy_by_key("wilder_vol").signal(jumped, params)
        self.assertFalse(bool(signal[19]))
        self.assertTrue(bool(signal[20]))
        self.assertTrue(bool(signal[24]))
        keltner = strategy_by_key("keltner20").signal(jumped, strategy_by_key("keltner20").center)
        self.assertFalse(bool(keltner[24]))

    def test_squeeze_needs_the_prior_bar_inside_the_channel(self):
        n = 40
        close = np.full(n, 100.0)
        high = np.full(n, 110.0)
        low = np.full(n, 90.0)
        close[30] = 140.0
        high[30] = 145.0
        low[30] = 100.0
        quiet = make_bars(close, close, high, low)
        params = strategy_by_key("bb_squeeze").center
        signal = strategy_by_key("bb_squeeze").signal(quiet, params)
        self.assertFalse(bool(signal[:30].any()))
        self.assertTrue(bool(signal[30]))
        # A close through the upper band with no squeeze behind it is lib.bb_break only.
        m = 45
        wavy = 100.0 + 20.0 * np.sin(np.arange(m) / 2.0)
        wavy[-1] = wavy[-2] + 30.0
        wave_high = wavy + 0.5
        wave_low = wavy - 0.5
        wave_low[-1] = wavy[-2]
        waved = make_bars(wavy, wavy, wave_high, wave_low)
        self.assertFalse(bool(strategy_by_key("bb_squeeze").signal(waved, params)[-1]))
        self.assertTrue(bool(strategy_by_key("bb_break").signal(
            waved, strategy_by_key("bb_break").center,
        )[-1]))

    def test_turtle_soup_buys_the_failed_low_and_turtle20_does_not(self):
        n = 230
        close = 100.0 + np.arange(n, dtype=np.float64) * 0.4
        high = close + 1.0
        low = close - 1.0
        low[185] = 40.0
        # Take out that low and finish back above it.
        low[210] = 30.0
        close[210] = close[209]
        high[210] = close[210] + 1.0
        bars = make_bars(close, close, high, low)
        params = strategy_by_key("turtle_soup").center
        signal = strategy_by_key("turtle_soup").signal(bars, params)
        self.assertTrue(bool(signal[210]))
        self.assertTrue(bool(signal[211]))
        self.assertFalse(bool(signal[212]))
        turtle = strategy_by_key("turtle20").signal(bars, strategy_by_key("turtle20").center)
        self.assertFalse(bool(turtle[210]))
        # A close that stays under the prior low is not the soup.
        close[210] = 20.0
        high[210] = 25.0
        failed = make_bars(close, close, high, low)
        failed_signal = strategy_by_key("turtle_soup").signal(failed, params)
        self.assertFalse(bool(failed_signal[210]))

    def test_cumulative_rsi_is_not_a_single_rsi2(self):
        close = 100.0 + np.arange(250, dtype=np.float64) * 0.5
        close[220] = close[219] * 0.9
        close[221] = close[220] * 0.9
        bars = make_bars(close, highs=close + 1.0, lows=close - 1.0)
        cum = strategy_by_key("rsi2_cum").signal(bars, strategy_by_key("rsi2_cum").center)
        single = strategy_by_key("rsi2").signal(bars, strategy_by_key("rsi2").center)
        self.assertTrue(bool(cum.any()))
        self.assertFalse(np.array_equal(cum, single))

    def test_one_month_reversal_is_the_opposite_of_the_trend_sign(self):
        close = 50.0 + np.arange(260, dtype=np.float64)
        bars = make_bars(close)
        params = strategy_by_key("rev1m").center
        signal = strategy_by_key("rev1m").signal(bars, params)
        # A steady rise is not a one-month loser.
        self.assertFalse(bool(signal[210:230].any()))
        close[240] = close[240 - 21] - 5.0
        dipped = make_bars(close)
        dipped_signal = strategy_by_key("rev1m").signal(dipped, params)
        trend = sma(close, 200)
        self.assertGreater(float(close[240]), float(trend[240]))
        self.assertLess(float(close[240]), float(close[240 - 21]))
        self.assertTrue(bool(dipped_signal[240]))
        self.assertFalse(bool(dipped_signal[239]))

    def test_twelve_minus_one_keeps_the_sign_when_the_latest_month_reverses(self):
        close = np.arange(1, 401, dtype=np.float64)
        close[-1] = 1.0
        bars = make_bars(close)
        params = strategy_by_key("tsmom12_1").center
        signal = strategy_by_key("tsmom12_1").signal(bars, params)
        self.assertFalse(bool(signal[:273].any()))
        self.assertTrue(bool(signal[-1]))
        self.assertGreater(float(close[-1 - 21]), float(close[-1 - 21 - 252]))
        self.assertLess(float(close[-1]), float(close[-1 - 21]))
        plain = strategy_by_key("tsmom30d").signal(bars, {"n": 21})
        self.assertFalse(bool(plain[-1]))

    def test_bll_150_holds_ten_days_and_ignores_a_break_inside_the_hold(self):
        n = 200
        close = np.full(n, 100.0)
        close[160] = 150.0
        close[163] = 180.0
        bars = make_bars(close)
        params = strategy_by_key("bll_trb150").center
        signal = strategy_by_key("bll_trb150").signal(bars, params)
        self.assertFalse(bool(signal[:160].any()))
        self.assertTrue(bool(signal[160:170].all()))
        self.assertFalse(bool(signal[170]))
        short = strategy_by_key("bll_trb50").signal(bars, strategy_by_key("bll_trb50").center)
        # The 50-day high was also 100, so both enter, but this name's window is 150.
        self.assertTrue(bool(short[160]))
        self.assertEqual(params["n"], 150)

    def test_nr7_breaks_the_narrow_day_and_not_a_wide_one(self):
        n = 220
        close = 100.0 + np.arange(n, dtype=np.float64) * 0.2
        high = close + 2.0
        low = close - 2.0
        high[209] = close[209] + 0.05
        low[209] = close[209] - 0.05
        close[210] = high[209] + 1.0
        high[210] = close[210] + 0.2
        low[210] = close[210] - 0.2
        bars = make_bars(close, close, high, low)
        params = strategy_by_key("nr7").center
        signal = strategy_by_key("nr7").signal(bars, params)
        self.assertGreater(float(close[210]), float(sma(close, 200)[210]))
        self.assertTrue(bool(signal[210]))
        self.assertFalse(bool(signal[208]))
        # Widen day 209 so it is not the narrowest, and the next close is not a signal.
        high[209] = close[209] + 5.0
        low[209] = close[209] - 5.0
        wide = make_bars(close, close, high, low)
        wide_signal = strategy_by_key("nr7").signal(wide, params)
        self.assertFalse(bool(wide_signal[210]))

    def test_inside_day_is_long_only_on_the_break_close(self):
        close = np.array([10, 11, 12, 13, 14, 13.5, 16, 15, 14], dtype=np.float64)
        high = np.array([10.5, 11.5, 12.5, 13.5, 15, 14.2, 16.2, 15.5, 14.5])
        low = np.array([9.5, 10.5, 11.5, 12.5, 12, 13.2, 14.5, 14.2, 13.2])
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("inside_day").signal(bars, strategy_by_key("inside_day").center)
        # Day 5 sits inside day 4. Day 6 closes above day 5's high.
        self.assertLess(float(high[5]), float(high[4]))
        self.assertGreater(float(low[5]), float(low[4]))
        self.assertFalse(bool(signal[5]))
        self.assertTrue(bool(signal[6]))
        self.assertFalse(bool(signal[7]))
        donchian = strategy_by_key("donchian20").signal(bars, strategy_by_key("donchian20").center)
        self.assertFalse(bool(donchian[6]))

    def test_floor_pivot_uses_yesterdays_typical_price(self):
        close = 100.0 + np.arange(220, dtype=np.float64)
        high = close + 2.0
        low = close - 2.0
        bars = make_bars(close, close, high, low)
        params = strategy_by_key("floor_pivot").center
        pivot = (high[:-1] + low[:-1] + close[:-1]) / 3.0
        trend = sma(close, 200)
        expected = np.zeros(len(close), dtype=bool)
        ok = np.isfinite(trend[1:])
        expected[1:] = ok & (close[1:] > pivot) & (close[1:] > trend[1:])
        np.testing.assert_array_equal(
            strategy_by_key("floor_pivot").signal(bars, params), expected,
        )

    def test_fractal_breaks_a_five_bar_high_and_not_a_donchian(self):
        n = 30
        close = np.full(n, 10.0)
        high = np.full(n, 11.0)
        low = np.full(n, 9.0)
        # Center the up fractal at index 10. Confirmed at index 12.
        high[10] = 20.0
        close[14] = 21.0
        high[14] = 21.0
        bars = make_bars(close, close, high, low)
        params = strategy_by_key("fractal").center
        signal = strategy_by_key("fractal").signal(bars, params)
        self.assertFalse(bool(signal[:14].any()))
        self.assertTrue(bool(signal[14]))
        # Down fractal centered at 16, confirmed at 18. The exit is a later close.
        low[16] = 1.0
        close[19] = 0.5
        low[19] = 0.4
        high[19] = 0.6
        stopped = make_bars(close, close, high, low)
        stopped_signal = strategy_by_key("fractal").signal(stopped, params)
        self.assertTrue(bool(stopped_signal[14]))
        self.assertTrue(bool(stopped_signal[18]))
        self.assertFalse(bool(stopped_signal[19]))

    def test_mass_bulge_above_the_average_turns_on_and_a_loss_of_the_average_turns_off(self):
        n = 80
        close = 100.0 + np.arange(n, dtype=np.float64)
        high = close + 1.0
        low = close - 1.0
        high[40:50] = close[40:50] + 30.0
        low[40:50] = close[40:50] - 30.0
        bars = make_bars(close, close, high, low)
        params = {"ema": 9, "span": 25, "trend": 20}
        value = mass_index(bars.high, bars.low, 9, 25)
        self.assertGreater(float(np.nanmax(value)), 27.0)
        signal = strategy_by_key("mass_index").signal(bars, params)
        self.assertTrue(bool(signal.any()))
        # Below the trend average, the same bulge is not a long.
        down = close.copy()
        down[:] = 100.0 - np.arange(n)
        down_bars = make_bars(down, highs=down + (high - close), lows=down - (close - low))
        down_signal = strategy_by_key("mass_index").signal(down_bars, params)
        self.assertFalse(bool(down_signal.any()))

    def test_ulcer_wants_a_quiet_uptrend(self):
        close = 100.0 + np.arange(260, dtype=np.float64) * 0.3
        bars = make_bars(close, highs=close + 0.2, lows=close - 0.2)
        params = strategy_by_key("ulcer").center
        signal = strategy_by_key("ulcer").signal(bars, params)
        self.assertTrue(bool(signal[220:].any()))
        close[240:] = close[239] * 0.5
        crashed = make_bars(close, highs=close + 0.2, lows=close - 0.2)
        crashed_signal = strategy_by_key("ulcer").signal(crashed, params)
        self.assertFalse(bool(crashed_signal[240:].any()))
        value = ulcer_index(close, 14)
        self.assertGreater(float(value[245]), float(value[220]))

    def test_weekend_is_flat_saturday_and_sunday(self):
        n = 20
        bars = make_bars(np.full(n, 100.0))
        params = strategy_by_key("weekend").center
        tomorrow = weekday_utc(bars.ts + np.int64(DAY_MS))
        np.testing.assert_array_equal(
            strategy_by_key("weekend").signal(bars, params),
            (tomorrow != 5) & (tomorrow != 6),
        )
        # 2023-11-15 is Wednesday. Index 2's next session is Saturday.
        self.assertEqual(
            datetime.fromtimestamp(int(bars.ts[0]) / 1000, timezone.utc).strftime("%Y-%m-%d"),
            "2023-11-15",
        )
        self.assertFalse(bool(strategy_by_key("weekend").signal(bars, params)[2]))
        self.assertFalse(bool(strategy_by_key("weekend").signal(bars, params)[3]))
        self.assertTrue(bool(strategy_by_key("weekend").signal(bars, params)[4]))
        sunday_only = dict(params)
        sunday_only["span"] = 1
        np.testing.assert_array_equal(
            strategy_by_key("weekend").signal(bars, sunday_only),
            tomorrow != 6,
        )
        monday = strategy_by_key("dow").signal(bars, strategy_by_key("dow").center)
        self.assertFalse(np.array_equal(
            strategy_by_key("weekend").signal(bars, params), monday,
        ))

    def test_guppy_lengths_do_not_collapse_into_one_average(self):
        rise = _rise(100)
        signal = strategy_by_key("guppy").signal(rise, strategy_by_key("guppy").center)
        cross = strategy_by_key("ema12x26").signal(rise, strategy_by_key("ema12x26").center)
        self.assertFalse(np.array_equal(signal, cross))


if __name__ == "__main__":
    unittest.main()
