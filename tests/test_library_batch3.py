"""Batch-3 daily signals: on/off, warm-up, and no look-ahead.

The original 15 and the 17 batch-2 names stay ahead of these keys, with
the same centers. New names are what a library-mode claim leases after
those 32 are tested. The shared score start stays 2018-12-01.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np

from hedge_fund.library.catalog import (
    LIBRARY,
    grid_params,
    signal_ready,
    strategy_by_key,
)
from hedge_fund.library.daily import DAY_MS, DailyBars
from hedge_fund.library.indicators import cci, stochastic_slow

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC
BATCH2 = (
    "turtle20", "donchian20", "ema12x26", "ema50x200",
    "bb_revert", "bb_break", "keltner20", "rsi14", "rsi2_10",
    "tsmom30d", "tsmom90d", "tsmom180d", "adx14", "ichimoku",
    "supertrend", "willr", "ibs",
)
NEW = (
    "psar", "kama", "aroon", "cci100", "cci_revert", "stoch", "crsi",
    "tom", "halloween", "weekend", "high52", "bll_band", "keltner_revert",
    "rsi30", "vortex", "trix", "awesome", "kst", "nr7", "alligator",
)
CALENDAR = {"tom", "halloween", "weekend"}
# First index whose inputs exist, at the canonical parameters, on a long
# daily series. Calendar rules are known on the first close.
READY_AT = {
    "psar": 2,
    "kama": 10,
    "aroon": 25,
    "cci100": 19,
    "cci_revert": 19,
    "stoch": 18,
    "crsi": 101,
    "tom": 0,
    "halloween": 0,
    "weekend": 0,
    "high52": 251,
    "bll_band": 199,
    "keltner_revert": 19,
    "rsi30": 14,
    "vortex": 14,
    "trix": 43,
    "awesome": 33,
    "kst": 52,
    "nr7": 7,
    "alligator": 20,
}


def make_bars(opens, closes=None, highs=None, lows=None, day0=DAY0) -> DailyBars:
    o = np.asarray(opens, dtype=np.float64)
    c = o.copy() if closes is None else np.asarray(closes, dtype=np.float64)
    h = np.maximum(o, c) if highs is None else np.asarray(highs, dtype=np.float64)
    lo = np.minimum(o, c) if lows is None else np.asarray(lows, dtype=np.float64)
    if highs is not None:
        h = np.asarray(highs, dtype=np.float64)
    if lows is not None:
        lo = np.asarray(lows, dtype=np.float64)
    ts = int(day0) + np.arange(o.shape[0], dtype=np.int64) * DAY_MS
    return DailyBars(ts=ts, open=o, high=h, low=lo, close=c)


def random_walk(n: int = 500, seed: int = 11) -> DailyBars:
    rng = np.random.default_rng(seed)
    close = 20_000.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    opens = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(opens, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(opens, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    return make_bars(opens, close, high, low)


def _prefix(bars: DailyBars, cut: int) -> DailyBars:
    return DailyBars(
        ts=bars.ts[:cut], open=bars.open[:cut], high=bars.high[:cut],
        low=bars.low[:cut], close=bars.close[:cut],
    )


def _signal(key: str, bars: DailyBars) -> np.ndarray:
    strategy = strategy_by_key(key)
    return strategy.signal(bars, strategy.center)


class CatalogOrderTests(unittest.TestCase):
    def test_batch3_is_appended_after_the_existing_thirty_two(self):
        self.assertEqual(tuple(s.key for s in LIBRARY[15:32]), BATCH2)
        self.assertEqual(tuple(s.key for s in LIBRARY[32:]), NEW)
        self.assertEqual(len(LIBRARY), 52)
        ibs = strategy_by_key("ibs")
        self.assertEqual(ibs.center, {"ibs_lt": 20, "trend": 200})
        turtle = strategy_by_key("turtle20")
        self.assertEqual(turtle.center, {"entry": 20, "exit": 10})
        names = {
            "psar": "_psar",
            "kama": "_kama_trend",
            "aroon": "_aroon_trend",
            "cci100": "_cci_break",
            "cci_revert": "_cci_revert",
            "stoch": "_stoch",
            "crsi": "_crsi",
            "tom": "_tom",
            "halloween": "_halloween",
            "weekend": "_weekend",
            "high52": "_high52",
            "bll_band": "_bll_band",
            "keltner_revert": "_keltner_revert",
            "rsi30": "_rsi30",
            "vortex": "_vortex_trend",
            "trix": "_trix",
            "awesome": "_awesome",
            "kst": "_kst",
            "nr7": "_nr7",
            "alligator": "_alligator",
        }
        for key, fn in names.items():
            self.assertEqual(strategy_by_key(key).signal.__name__, fn)


class WarmupAndLookaheadTests(unittest.TestCase):
    def test_each_new_signal_is_off_until_warm_and_does_not_look_ahead(self):
        bars = random_walk(520)
        cuts = (90, 180, 260, 400)
        for key in NEW:
            strategy = strategy_by_key(key)
            for params in grid_params(strategy):
                ready = signal_ready(strategy, bars, params)
                self.assertTrue(bool(ready.any()), key)
                first = int(np.flatnonzero(ready)[0])
                if key in CALENDAR:
                    self.assertEqual(first, 0, key)
                    self.assertTrue(bool(ready.all()), key)
                else:
                    self.assertGreater(first, 0, key)
                    self.assertFalse(bool(ready[:first].any()), f"{key} {params}")
                self.assertTrue(bool(ready[first:].all()), f"{key} {params}")
                signal = strategy.signal(bars, params)
                self.assertEqual(signal.dtype, np.bool_)
                self.assertFalse(
                    bool(signal[:first].any()),
                    f"{key} {params} went long before its lookback existed",
                )
                full = signal
                for cut in cuts:
                    if cut <= first:
                        continue
                    np.testing.assert_array_equal(
                        strategy.signal(_prefix(bars, cut), params),
                        full[:cut],
                        err_msg=f"{key} {params} looked ahead at {cut}",
                    )

    def test_canonical_warmup_indexes(self):
        bars = random_walk(450, seed=4)
        for key, at in READY_AT.items():
            strategy = strategy_by_key(key)
            ready = signal_ready(strategy, bars, strategy.center)
            first = int(np.flatnonzero(ready)[0])
            self.assertEqual(first, at, key)

    def test_shared_score_start_stays_2018_12_01(self):
        from hedge_fund.library.daily_cache import parse_daily_npz
        from hedge_fund.library.evaluate import build_library_data
        from pathlib import Path

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
    def test_psar_is_long_in_a_rise_and_flat_in_a_fall(self):
        rise_c = 100.0 + np.arange(40)
        fall_c = 200.0 - np.arange(40)
        rise = make_bars(rise_c, highs=rise_c + 0.5, lows=rise_c - 0.5)
        fall = make_bars(fall_c, highs=fall_c + 0.5, lows=fall_c - 0.5)
        up = _signal("psar", rise)
        down = _signal("psar", fall)
        self.assertFalse(bool(up[:2].any()))
        self.assertTrue(bool(up[-1]))
        self.assertFalse(bool(down[-1]))

    def test_kama_lags_a_straight_rise(self):
        signal = _signal("kama", make_bars(np.arange(1, 80, dtype=float)))
        self.assertFalse(bool(signal[:10].any()))
        self.assertFalse(bool(signal[10]))
        self.assertTrue(bool(signal[-1]))

    def test_aroon_follows_the_side_that_printed_the_recent_extreme(self):
        up_c = 10.0 + np.arange(40)
        down_c = 50.0 - np.arange(40)
        up = _signal("aroon", make_bars(up_c, highs=up_c + 1, lows=up_c - 1))
        down = _signal("aroon", make_bars(down_c, highs=down_c + 1, lows=down_c - 1))
        flat = _signal("aroon", make_bars(np.full(40, 10.0), highs=np.full(40, 11.0), lows=np.full(40, 9.0)))
        self.assertFalse(bool(up[:25].any()))
        self.assertTrue(bool(up[-1]))
        self.assertFalse(bool(down.any()))
        self.assertFalse(bool(flat.any()))

    def test_cci_breakout_and_fade_are_opposite_uses_of_the_same_spike(self):
        close = np.full(40, 100.0)
        high = np.full(40, 101.0)
        low = np.full(40, 99.0)
        close[30] = 200.0
        high[30] = 200.0
        low[30] = 190.0
        up = make_bars(close.copy(), close.copy(), high.copy(), low.copy())
        value = cci(up.high, up.low, up.close, 20)
        self.assertGreater(float(value[30]), 100.0)
        broke = _signal("cci100", up)
        self.assertFalse(bool(broke[:30].any()))
        self.assertTrue(bool(broke[30]))
        self.assertFalse(bool(_signal("cci_revert", up).any()))
        close[30] = 20.0
        high[30] = 30.0
        low[30] = 20.0
        down = make_bars(close, close, high, low)
        faded = _signal("cci_revert", down)
        self.assertTrue(bool(faded[30]))
        self.assertFalse(bool(faded[31]))
        self.assertFalse(bool(_signal("cci100", down).any()))

    def test_stochastic_buys_the_oversold_cross(self):
        close = np.concatenate([np.linspace(100, 40, 40), np.linspace(40, 70, 30)])
        bars = make_bars(close, highs=close + 0.5, lows=close - 0.5)
        slow_k, slow_d = stochastic_slow(bars.high, bars.low, bars.close, 14, 3)
        signal = _signal("stoch", bars)
        self.assertFalse(bool(signal[:18].any()))
        self.assertLess(float(slow_k[39]), 20.0)
        self.assertGreater(float(slow_k[39]), float(slow_d[39]))
        self.assertLessEqual(float(slow_k[38]), float(slow_d[38]))
        self.assertTrue(bool(signal[39]))

    def test_connors_rsi_buys_the_crash_and_not_the_climb(self):
        close = np.concatenate([np.linspace(100, 250, 180), [250.0, 250.0, 180.0]])
        signal = _signal("crsi", make_bars(close))
        self.assertFalse(bool(signal[:101].any()))
        self.assertFalse(bool(signal[-2]))
        self.assertTrue(bool(signal[-1]))

    def test_turn_of_month_weekend_and_halloween_follow_the_calendar(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        day0 = int(start.timestamp() * 1000)
        bars = make_bars(np.full(366, 100.0), day0=day0)
        tom = _signal("tom", bars)
        halloween = _signal("halloween", bars)
        weekend = _signal("weekend", bars)
        other = make_bars(np.arange(366, dtype=float) + 5.0, day0=day0)
        np.testing.assert_array_equal(tom, _signal("tom", other))
        np.testing.assert_array_equal(halloween, _signal("halloween", other))
        np.testing.assert_array_equal(weekend, _signal("weekend", other))
        by_day = {
            datetime.fromtimestamp(int(ts) / 1000, timezone.utc).strftime("%Y-%m-%d"): i
            for i, ts in enumerate(bars.ts)
        }
        # Long on the last day and the first three: the close before each of those days.
        self.assertTrue(bool(tom[by_day["2024-01-30"]]))  # Jan 31
        self.assertTrue(bool(tom[by_day["2024-01-31"]]))  # Feb 1
        self.assertTrue(bool(tom[by_day["2024-02-02"]]))  # Feb 3
        self.assertFalse(bool(tom[by_day["2024-02-03"]]))  # Feb 4
        self.assertFalse(bool(tom[by_day["2024-01-14"]]))
        # November-April is long; May-October is flat. The flip is the close before.
        self.assertTrue(bool(halloween[by_day["2024-04-29"]]))
        self.assertFalse(bool(halloween[by_day["2024-04-30"]]))
        self.assertFalse(bool(halloween[by_day["2024-07-15"]]))
        self.assertFalse(bool(halloween[by_day["2024-10-30"]]))
        self.assertTrue(bool(halloween[by_day["2024-10-31"]]))
        # 2024-01-05 was a Friday. Flat into Saturday and Sunday, long into Monday.
        self.assertFalse(bool(weekend[by_day["2024-01-05"]]))
        self.assertFalse(bool(weekend[by_day["2024-01-06"]]))
        self.assertTrue(bool(weekend[by_day["2024-01-07"]]))
        self.assertTrue(bool(weekend[by_day["2024-01-08"]]))

    def test_high52_stays_on_through_a_rise_and_turns_off_when_the_close_leaves_the_band(self):
        close = np.arange(1, 400, dtype=float)
        signal = _signal("high52", make_bars(close))
        self.assertFalse(bool(signal[:251].any()))
        self.assertTrue(bool(signal[251:].all()))
        crashed = close.copy()
        crashed[-1] = close[-2] * 0.5
        shock = _signal("high52", make_bars(crashed))
        self.assertTrue(bool(shock[-2]))
        self.assertFalse(bool(shock[-1]))

    def test_one_percent_band_does_not_flip_inside_the_band(self):
        close = np.full(220, 100.0)
        close[200] = 100.5
        close[201] = 102.0
        close[202] = 100.0
        close[203] = 98.0
        bars = make_bars(close)
        band = _signal("bll_band", bars)
        naked = _signal("sma200", bars)
        np.testing.assert_array_equal(band[199:204], np.array([False, False, True, True, False]))
        np.testing.assert_array_equal(naked[199:204], np.array([False, True, True, False, False]))

    def test_keltner_fade_buys_the_lower_band_and_sells_the_ema(self):
        n = 50
        close = np.full(n, 100.0)
        high = np.full(n, 101.0)
        low = np.full(n, 99.0)
        quiet = make_bars(close, close, high, low)
        self.assertFalse(bool(_signal("keltner_revert", quiet).any()))
        close[40] = 50.0
        high[40] = 60.0
        low[40] = 50.0
        close[41] = 100.0
        high[41] = 101.0
        low[41] = 99.0
        spiked = make_bars(close, close, high, low)
        signal = _signal("keltner_revert", spiked)
        self.assertFalse(bool(signal[:40].any()))
        self.assertTrue(bool(signal[40]))
        self.assertFalse(bool(signal[41]))
        self.assertFalse(np.array_equal(signal, _signal("keltner20", spiked)))

    def test_rsi30_buys_the_oversold_stretch_and_exits_above_70(self):
        close = np.concatenate([
            np.full(20, 100.0),
            np.linspace(100, 40, 20),
            np.linspace(40, 140, 25),
        ])
        signal = _signal("rsi30", make_bars(close))
        self.assertFalse(bool(signal[:14].any()))
        self.assertFalse(bool(signal[20]))
        self.assertTrue(bool(signal[21]))
        self.assertTrue(bool(signal[50]))
        self.assertFalse(bool(signal[51]))
        self.assertFalse(np.array_equal(signal, _signal("rsi14", make_bars(close))))

    def test_vortex_is_long_only_when_up_moves_dominate(self):
        up_c = 100.0 + np.arange(80)
        down_c = 200.0 - np.arange(80)
        up = _signal("vortex", make_bars(up_c, highs=up_c + 1.0, lows=up_c - 0.2))
        down = _signal("vortex", make_bars(down_c, highs=down_c + 0.2, lows=down_c - 1.0))
        self.assertFalse(bool(up[:14].any()))
        self.assertTrue(bool(up[-1]))
        self.assertFalse(bool(down.any()))

    def test_trix_awesome_and_alligator_turn_on_in_a_rise(self):
        bars = make_bars(np.arange(1, 200, dtype=float))
        trix = _signal("trix", bars)
        awesome = _signal("awesome", bars)
        alligator = _signal("alligator", bars)
        self.assertFalse(bool(trix[:43].any()))
        self.assertTrue(bool(trix[-1]))
        self.assertFalse(bool(awesome[:33].any()))
        self.assertTrue(bool(awesome[-1]))
        self.assertFalse(bool(alligator[:20].any()))
        self.assertTrue(bool(alligator[-1]))

    def test_kst_turns_on_when_the_rise_accelerates(self):
        close = np.concatenate([np.full(80, 100.0), 100.0 * np.exp(np.linspace(0.0, 1.2, 80))])
        bars = make_bars(close, highs=close * 1.01, lows=close * 0.99)
        signal = _signal("kst", bars)
        self.assertFalse(bool(signal[:52].any()))
        self.assertTrue(bool(signal[81:86].all()))

    def test_nr7_holds_only_the_breakout_day(self):
        n = 20
        high = np.full(n, 110.0)
        low = np.full(n, 100.0)
        close = np.full(n, 105.0)
        high[6] = 101.0
        low[6] = 100.5
        close[6] = 100.8
        close[7] = 102.0
        high[7] = 103.0
        low[7] = 100.0
        signal = _signal("nr7", make_bars(close, close, high, low))
        self.assertFalse(bool(signal[:7].any()))
        self.assertTrue(bool(signal[7]))
        self.assertFalse(bool(signal[8:].any()))


if __name__ == "__main__":
    unittest.main()
