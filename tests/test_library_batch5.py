"""Batch-5 daily signals: on/off, warm-up, and no look-ahead.

The original 15, the 17 batch-2 names, the 24 batch-3 names, and the 26
batch-4 names stay ahead of these keys, with the same centers. Batch 5 is
what a library-mode claim leases after those 82 are tested. The shared score
start stays 2018-12-01.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np

from hedge_fund.library.catalog import LIBRARY, grid_params, signal_ready, strategy_by_key
from hedge_fund.library.daily import DAY_MS, DailyBars
from hedge_fund.library.indicators import (
    balance_of_power,
    laguerre_rsi,
    linreg_slope,
    macd_histogram,
    mcginley_dynamic,
    psychological_line,
    range_expansion_index,
    relative_momentum_index,
    sma,
    stochastic_k,
    tillson_t3,
    true_strength_index,
    vidya,
)
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC, a Wednesday
BATCH4_END = 82
NEW = (
    "elder_impulse", "macd_hist", "tsi", "awesome", "alligator", "bop", "psych",
    "chop", "rwi", "asi", "darvas", "ichimoku_cloud", "td_setup", "rei", "linreg",
    "mom_mid", "first_half", "laguerre", "vidya", "t3", "rmi", "double7",
    "mcginley", "stoch_pop", "outside", "three_dn",
)
CALENDAR = ("first_half",)
CENTERS = {
    "elder_impulse": {"n": 13},
    "macd_hist": {"signal": 9},
    "tsi": {"long": 25, "short": 13},
    "awesome": {"fast": 5, "slow": 34},
    "alligator": {"lips": 5, "jaw": 13},
    "bop": {"n": 14},
    "psych": {"n": 12, "thr": 50},
    "chop": {"n": 14, "trend": 200},
    "rwi": {"n": 14},
    "asi": {"n": 1},
    "darvas": {"look": 20, "confirm": 3},
    "ichimoku_cloud": {"conv": 9, "base": 26, "span": 52},
    "td_setup": {"count": 9, "trend": 200},
    "rei": {"n": 5},
    "linreg": {"n": 20},
    "mom_mid": {"mid": 126, "slow": 252},
    "first_half": {"through": 15},
    "laguerre": {"gamma": 50, "thr": 50},
    "vidya": {"n": 20},
    "t3": {"n": 5, "vf": 70},
    "rmi": {"mom": 5, "n": 14, "thr": 50},
    "double7": {"n": 7, "trend": 200},
    "mcginley": {"n": 20},
    "stoch_pop": {"k": 14, "pop": 80},
    "outside": {"n": 1},
    "three_dn": {"n": 3, "trend": 200, "exit_ma": 5},
}


def _ready_index(key: str, params: dict) -> int:
    if key == "elder_impulse":
        return max(params["n"], (26 - 1) + 9)
    if key == "macd_hist":
        return (26 - 1) + params["signal"]
    if key == "tsi":
        return params["long"] + params["short"] - 1
    if key == "awesome":
        return max(params["fast"], params["slow"]) - 1
    if key == "alligator":
        return max(params["lips"] - 1 + 3, 8 - 1 + 5, params["jaw"] - 1 + 8)
    if key == "bop":
        return params["n"] - 1
    if key == "psych":
        return params["n"]
    if key == "chop":
        return max(params["n"] - 1, params["trend"] - 1)
    if key == "rwi":
        return params["n"]
    if key == "asi":
        return params["n"]
    if key == "darvas":
        return params["look"] + params["confirm"]
    if key == "ichimoku_cloud":
        return max(params["conv"], params["base"], params["span"]) - 1 + params["base"]
    if key == "td_setup":
        return max(4, params["trend"] - 1)
    if key == "rei":
        return params["n"] + 5
    if key == "linreg":
        return params["n"] - 1
    if key == "mom_mid":
        return max(params["mid"], params["slow"])
    if key == "first_half":
        return 0
    if key == "laguerre":
        return 1
    if key == "vidya":
        return params["n"]
    if key == "t3":
        return 6 * (params["n"] - 1)
    if key == "rmi":
        return params["mom"] + params["n"] - 1
    if key == "double7":
        return max(params["n"] - 1, params["trend"] - 1)
    if key == "mcginley":
        return params["n"] - 1
    if key == "stoch_pop":
        return params["k"]
    if key == "outside":
        return params["n"]
    if key == "three_dn":
        return max(params["n"], params["trend"] - 1, params["exit_ma"] - 1)
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
    def test_batch5_is_appended_after_the_first_eighty_two(self):
        self.assertEqual(tuple(s.key for s in LIBRARY[BATCH4_END:]), NEW)
        self.assertEqual(LIBRARY[BATCH4_END - 1].key, "weekend")
        self.assertEqual(len(LIBRARY), BATCH4_END + len(NEW))
        for key, center in CENTERS.items():
            strategy = strategy_by_key(key)
            self.assertEqual(strategy.center, center)
            self.assertEqual(strategy.stop_frac, LIBRARY_STOP_FRAC)
            self.assertTrue(strategy.citation.tested_rule.strip())
            self.assertTrue(strategy.citation.implemented_rule.strip())

    def test_skipped_near_copies_are_not_keys(self):
        keys = {s.key for s in LIBRARY}
        # An n-day SMA rises exactly when the close is above the close n days ago.
        self.assertNotIn("sma_slope", keys)
        self.assertIn("tsmom30d", keys)
        # PPO above zero is the 12/26 EMA cross.
        self.assertNotIn("ppo", keys)
        self.assertIn("ema12x26", keys)
        # Correlation with time has the same sign as the regression slope.
        self.assertNotIn("price_corr", keys)
        self.assertIn("linreg", keys)
        # Inverse Fisher of RSI crosses zero at RSI 50.
        self.assertNotIn("invfisher", keys)
        self.assertIn("rsi14", keys)
        # NR4 is the neighbour of lib.nr7, not its own key.
        self.assertNotIn("nr4", keys)
        self.assertIn("nr7", keys)
        # The published Coppock curve is monthly and stays off this tape.
        self.assertNotIn("coppock", keys)
        # Batch 4's skips stay skipped.
        self.assertNotIn("ichimoku_chikou", keys)
        self.assertNotIn("tema", keys)
        self.assertNotIn("toq", keys)
        self.assertNotIn("heikin", keys)


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
    def test_impulse_and_histogram_follow_a_compounded_rise(self):
        up = _geo(160, 1.01)
        down = _geo(160, 0.99)
        elder = strategy_by_key("elder_impulse")
        hist = strategy_by_key("macd_hist")
        risen = elder.signal(up, elder.center)
        fallen = elder.signal(down, elder.center)
        ready = signal_ready(elder, up, elder.center)
        first = int(np.flatnonzero(ready)[0])
        self.assertTrue(bool(risen[first:].all()))
        self.assertFalse(bool(fallen[first:].any()))
        self.assertTrue(bool(hist.signal(up, hist.center)[first:].all()))
        # The histogram slope is not the signal-line cross.
        cross = strategy_by_key("macd").signal(up, strategy_by_key("macd").center)
        self.assertFalse(np.array_equal(hist.signal(up, hist.center), cross))
        line = macd_histogram(up.close, 12, 26, 9)
        prev = np.roll(line, 1)
        prev[0] = np.nan
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(line) & np.isfinite(prev) & (line > prev)
        np.testing.assert_array_equal(hist.signal(up, hist.center), expected)

    def test_tsi_awesome_alligator_and_asi_turn_on_in_a_rise(self):
        up = _rise(160)
        down = _fall(160)
        for key in ("tsi", "awesome", "alligator", "asi", "linreg", "laguerre", "rmi"):
            strategy = strategy_by_key(key)
            ready = signal_ready(strategy, up, strategy.center)
            first = int(np.flatnonzero(ready)[0])
            self.assertTrue(bool(strategy.signal(up, strategy.center)[first:].all()), key)
            self.assertFalse(bool(strategy.signal(down, strategy.center)[first:].any()), key)
        value = true_strength_index(up.close, 25, 13)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 0.0)
        np.testing.assert_array_equal(
            strategy_by_key("tsi").signal(up, strategy_by_key("tsi").center), expected,
        )

    def test_balance_of_power_needs_a_body_and_is_not_qstick(self):
        up = _rise(40)
        strategy = strategy_by_key("bop")
        self.assertTrue(bool(strategy.signal(up, strategy.center)[20:].all()))
        flat = make_bars(np.full(40, 10.0), highs=np.full(40, 11.0), lows=np.full(40, 9.0))
        self.assertFalse(bool(strategy.signal(flat, strategy.center).any()))
        value = balance_of_power(up.open, up.high, up.low, up.close, 14)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 0.0)
        np.testing.assert_array_equal(strategy.signal(up, strategy.center), expected)
        self.assertFalse(np.array_equal(
            strategy.signal(up, strategy.center),
            strategy_by_key("qstick").signal(up, strategy_by_key("qstick").center),
        ))

    def test_psychological_line_ignores_ties(self):
        up = _rise(30)
        strategy = strategy_by_key("psych")
        value = psychological_line(up.close, 12)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 50.0)
        np.testing.assert_array_equal(strategy.signal(up, strategy.center), expected)
        self.assertTrue(bool(strategy.signal(up, strategy.center)[12:].all()))
        tied = np.repeat(np.arange(10, 20, dtype=np.float64), 2)
        bars = make_bars(tied, highs=tied + 0.2, lows=tied - 0.2)
        # Six of the twelve comparisons are ties, so the line is 50 and not long.
        self.assertFalse(bool(strategy.signal(bars, strategy.center).any()))

    def test_choppiness_wants_a_quiet_rise_and_is_not_adx(self):
        up = _rise(260, step=0.4)
        strategy = strategy_by_key("chop")
        signal = strategy.signal(up, strategy.center)
        self.assertTrue(bool(signal[220:].all()))
        down = _fall(260, step=0.4)
        self.assertFalse(bool(strategy.signal(down, strategy.center).any()))
        bars = random_walk(240, seed=4)
        self.assertFalse(np.array_equal(
            strategy.signal(bars, strategy.center),
            strategy_by_key("adx14").signal(bars, strategy_by_key("adx14").center),
        ))

    def test_rwi_is_long_on_a_rise_and_not_vortex(self):
        up = _rise(80)
        down = _fall(80)
        strategy = strategy_by_key("rwi")
        self.assertTrue(bool(strategy.signal(up, strategy.center)[20:].all()))
        self.assertFalse(bool(strategy.signal(down, strategy.center)[20:].any()))
        bars = random_walk(120, seed=6)
        self.assertFalse(np.array_equal(
            strategy.signal(bars, strategy.center),
            strategy_by_key("vortex").signal(bars, strategy_by_key("vortex").center),
        ))

    def test_darvas_buys_the_completed_box_and_not_the_high_day(self):
        n = 40
        close = np.full(n, 100.0)
        high = np.full(n, 101.0)
        low = np.full(n, 99.0)
        high[20] = 120.0
        close[20] = 119.0
        low[20] = 100.0
        high[21:24] = 110.0
        low[21:24] = 105.0
        close[21:24] = 108.0
        close[28] = 121.0
        high[28] = 122.0
        low[28] = 110.0
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("darvas").signal(bars, {"look": 10, "confirm": 3})
        self.assertFalse(bool(signal[:28].any()))
        self.assertTrue(bool(signal[28]))
        # A Donchian window would have been long on the high day itself.
        self.assertFalse(bool(signal[20]))

    def test_cloud_color_is_not_price_above_the_cloud(self):
        up = _rise(120)
        cloud = strategy_by_key("ichimoku_cloud")
        price = strategy_by_key("ichimoku")
        self.assertTrue(bool(cloud.signal(up, cloud.center)[80:].all()))
        self.assertFalse(bool(cloud.signal(_fall(120), cloud.center)[80:].any()))
        bars = random_walk(200, seed=7)
        self.assertFalse(np.array_equal(
            cloud.signal(bars, cloud.center),
            price.signal(bars, price.center),
        ))

    def test_td_setup_counts_nine_higher_closes_four_days_back(self):
        close = 50.0 + np.arange(40, dtype=np.float64) * 0.2
        bars = make_bars(close, highs=close + 0.2, lows=close - 0.2)
        signal = strategy_by_key("td_setup").signal(bars, {"count": 9, "trend": 10})
        self.assertFalse(bool(signal[:12].any()))
        self.assertTrue(bool(signal[12]))
        # Break the comparison and the streak ends.
        close[20] = close[16] - 1.0
        broken = make_bars(close, highs=np.maximum(close, close + 0.2), lows=close - 0.2)
        broken_signal = strategy_by_key("td_setup").signal(broken, {"count": 9, "trend": 10})
        self.assertFalse(bool(broken_signal[20]))

    def test_rei_matches_the_indicator_on_an_overlapping_rise(self):
        close = 100.0 + np.arange(40, dtype=np.float64) * 0.15
        bars = make_bars(close, highs=close + 1.0, lows=close - 1.0)
        value = range_expansion_index(bars.high, bars.low, 5)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 0.0)
        np.testing.assert_array_equal(
            strategy_by_key("rei").signal(bars, strategy_by_key("rei").center), expected,
        )
        self.assertTrue(bool(expected[12:].all()))

    def test_regression_slope_can_disagree_with_the_endpoint_return(self):
        # The close finishes above where it started, and the fitted slope falls.
        close = np.concatenate((
            [50.0],
            np.linspace(80.0, 40.0, 18),
            [70.0],
        ))
        self.assertGreater(float(close[-1]), float(close[0]))
        self.assertLess(float(linreg_slope(close, 20)[-1]), 0.0)
        bars = make_bars(close)
        self.assertFalse(bool(strategy_by_key("linreg").signal(bars, {"n": 20})[-1]))
        up = _rise(40)
        self.assertTrue(bool(strategy_by_key("linreg").signal(up, strategy_by_key("linreg").center)[25:].all()))

    def test_intermediate_momentum_ignores_the_latest_half_year(self):
        close = np.arange(1, 401, dtype=np.float64)
        close[-1] = 1.0
        bars = make_bars(close)
        params = strategy_by_key("mom_mid").center
        signal = strategy_by_key("mom_mid").signal(bars, params)
        self.assertFalse(bool(signal[:252].any()))
        self.assertTrue(bool(signal[252]))
        self.assertTrue(bool(signal[-1]))
        self.assertGreater(float(close[-1 - 126]), float(close[-1 - 252]))
        self.assertLess(float(close[-1]), float(close[-1 - 126]))
        # The recent half-year is down. A sign that ends today is flat. This one is not.
        recent = strategy_by_key("tsmom30d").signal(bars, {"n": 126})
        self.assertFalse(bool(recent[-1]))
        self.assertTrue(bool(signal[-1]))

    def test_first_half_is_long_on_calendar_days_1_through_15(self):
        # 2023-11-30's close decides 2023-12-01.
        start = int(datetime(2023, 11, 28, tzinfo=timezone.utc).timestamp() * 1000)
        bars = make_bars(np.full(20, 100.0), day0=start)
        signal = strategy_by_key("first_half").signal(bars, strategy_by_key("first_half").center)
        # Index 0 is Nov 28, so index 2 is Nov 30.
        self.assertEqual(
            datetime.fromtimestamp(int(bars.ts[2]) / 1000, timezone.utc).strftime("%Y-%m-%d"),
            "2023-11-30",
        )
        self.assertFalse(bool(signal[1]))
        self.assertTrue(bool(signal[2]))
        self.assertTrue(bool(signal[16]))  # Dec 14 decides Dec 15
        self.assertFalse(bool(signal[17]))  # Dec 15 decides Dec 16
        short = dict(strategy_by_key("first_half").center)
        short["through"] = 10
        shorter = strategy_by_key("first_half").signal(bars, short)
        self.assertTrue(bool(shorter[2]))
        self.assertFalse(bool(shorter[12]))  # Dec 10 decides Dec 11
        monday = strategy_by_key("tom").signal(bars, strategy_by_key("tom").center)
        self.assertFalse(np.array_equal(signal, monday))

    def test_laguerre_vidya_t3_and_mcginley_match_their_indicators(self):
        bars = random_walk(180, seed=9)
        lag = laguerre_rsi(bars.close, 0.5)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(lag) & (lag > 0.5)
        np.testing.assert_array_equal(
            strategy_by_key("laguerre").signal(bars, strategy_by_key("laguerre").center),
            expected,
        )
        line = vidya(bars.close, 20)
        np.testing.assert_array_equal(
            strategy_by_key("vidya").signal(bars, strategy_by_key("vidya").center),
            np.isfinite(line) & np.isfinite(bars.close) & (bars.close > line),
        )
        t3 = tillson_t3(bars.close, 5, 0.7)
        np.testing.assert_array_equal(
            strategy_by_key("t3").signal(bars, strategy_by_key("t3").center),
            np.isfinite(t3) & (bars.close > t3),
        )
        dyn = mcginley_dynamic(bars.close, 20)
        np.testing.assert_array_equal(
            strategy_by_key("mcginley").signal(bars, strategy_by_key("mcginley").center),
            np.isfinite(dyn) & (bars.close > dyn),
        )
        up = _rise(80)
        for key in ("vidya", "t3", "mcginley"):
            strategy = strategy_by_key(key)
            ready = signal_ready(strategy, up, strategy.center)
            first = int(np.flatnonzero(ready)[0])
            self.assertTrue(bool(strategy.signal(up, strategy.center)[first + 1:].all()), key)
            self.assertFalse(bool(strategy.signal(_fall(80), strategy.center).any()), key)
        self.assertFalse(np.array_equal(
            strategy_by_key("vidya").signal(bars, {"n": 20}),
            strategy_by_key("kama").signal(bars, strategy_by_key("kama").center),
        ))
        self.assertFalse(np.array_equal(
            strategy_by_key("laguerre").signal(bars, strategy_by_key("laguerre").center),
            strategy_by_key("rsi14").signal(bars, strategy_by_key("rsi14").center),
        ))

    def test_rmi_is_not_the_one_day_rsi(self):
        bars = random_walk(160, seed=11)
        value = relative_momentum_index(bars.close, 5, 14)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 50.0)
        np.testing.assert_array_equal(
            strategy_by_key("rmi").signal(bars, strategy_by_key("rmi").center), expected,
        )
        self.assertFalse(np.array_equal(
            strategy_by_key("rmi").signal(bars, strategy_by_key("rmi").center),
            strategy_by_key("rsi14").signal(bars, strategy_by_key("rsi14").center),
        ))

    def test_double_seven_buys_the_closing_low_above_the_average(self):
        n = 250
        close = 100.0 + np.arange(n, dtype=np.float64) * 0.5
        close[220] = float(np.min(close[214:220])) - 1.0
        bars = make_bars(close, highs=close + 1.0, lows=close - 1.0)
        signal = strategy_by_key("double7").signal(bars, strategy_by_key("double7").center)
        self.assertGreater(float(close[220]), float(sma(close, 200)[220]))
        self.assertTrue(bool(signal[220]))
        self.assertFalse(bool(signal[219]))
        # The next close is a 7-day high, so the book flats.
        self.assertFalse(bool(signal[221]))
        rsi = strategy_by_key("rsi2").signal(bars, strategy_by_key("rsi2").center)
        self.assertFalse(np.array_equal(signal, rsi))

    def test_stochastic_pop_buys_the_cross_above_80(self):
        bars = random_walk(180, seed=12)
        value = stochastic_k(bars.high, bars.low, bars.close, 14)
        prev = np.roll(value, 1)
        prev[0] = np.nan
        signal = strategy_by_key("stoch_pop").signal(bars, strategy_by_key("stoch_pop").center)
        crosses = np.flatnonzero(np.isfinite(prev) & (prev <= 80.0) & (value > 80.0))
        self.assertGreater(crosses.size, 0)
        self.assertTrue(bool(signal[int(crosses[0])]))
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("stoch").signal(bars, strategy_by_key("stoch").center),
        ))
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("willr").signal(bars, strategy_by_key("willr").center),
        ))

    def test_outside_day_breaks_the_engulfing_high(self):
        close = np.array([10, 11, 12, 13, 14, 16, 15, 18, 14], dtype=np.float64)
        high = np.array([10.5, 11.5, 12.5, 13.5, 14.5, 17, 16.5, 18.2, 14.5])
        low = np.array([9.5, 10.5, 11.5, 12.5, 13.5, 12, 11, 16, 13.2])
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("outside").signal(bars, strategy_by_key("outside").center)
        # Day 5 engulfs day 4. Day 6 takes out the low, so it is not an inside day,
        # and its close does not break the high or cancel through the low.
        self.assertGreater(float(high[5]), float(high[4]))
        self.assertLess(float(low[5]), float(low[4]))
        self.assertFalse(bool(signal[5]))
        self.assertFalse(bool(signal[6]))
        self.assertTrue(bool(signal[7]))
        self.assertFalse(bool(signal[8]))
        inside = strategy_by_key("inside_day").signal(bars, strategy_by_key("inside_day").center)
        self.assertFalse(bool(inside[7]))

    def test_three_lower_closes_enter_above_the_average_and_exit_on_the_short_sma(self):
        close = 100.0 + np.arange(80, dtype=np.float64) * 0.4
        close[60] = close[59] - 1.0
        close[61] = close[60] - 1.0
        close[62] = close[61] - 1.0
        bars = make_bars(close, highs=close + 0.2, lows=close - 0.2)
        params = {"n": 3, "trend": 20, "exit_ma": 5}
        signal = strategy_by_key("three_dn").signal(bars, params)
        self.assertFalse(bool(signal[61]))
        self.assertTrue(bool(signal[62]))
        self.assertGreater(float(close[62]), float(sma(close, 20)[62]))
        self.assertLess(float(close[62]), float(sma(close, 5)[62]))
        # The next close is back above the 5-day average.
        self.assertFalse(bool(signal[63]))
        rsi = strategy_by_key("rsi2").signal(bars, strategy_by_key("rsi2").center)
        self.assertFalse(np.array_equal(signal, rsi))


if __name__ == "__main__":
    unittest.main()
