"""Batch-3 daily signals: on/off, warm-up, and no look-ahead.

The original 15 and the 17 batch-2 names stay ahead of these keys, with
the same centers. Batch 3 is what a library-mode claim leases after those
32 are tested. The shared score start stays 2018-12-01.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np

from hedge_fund.library.catalog import LIBRARY, grid_params, signal_ready, strategy_by_key
from hedge_fund.library.daily import DAY_MS, DailyBars
from hedge_fund.library.indicators import (
    connors_rsi,
    day_of_month,
    days_in_month,
    month_of_year,
    rsi_wilder,
    sma,
    weekday_utc,
)
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC, a Wednesday
BATCH2_END = 32
NEW = (
    "donchian55", "keltner_rev", "sma200_band", "psar", "kama", "hma",
    "aroon", "cci", "cci_dip", "stoch", "stochrsi", "uo", "rsi14_dip",
    "crsi", "adx_sma", "vortex", "trix", "chandelier", "hi52", "tsmom_vote",
    "vol_trend", "dow", "tom", "halloween",
)
CALENDAR = ("dow", "tom", "halloween")
CENTERS = {
    "donchian55": {"n": 55},
    "keltner_rev": {"ema": 20, "atr": 10, "width": 2},
    "sma200_band": {"n": 200, "band": 1},
    "psar": {"af": 2, "step": 2, "af_max": 20},
    "kama": {"er": 10, "fast": 2, "slow": 30},
    "hma": {"n": 16},
    "aroon": {"n": 25},
    "cci": {"n": 20},
    "cci_dip": {"n": 20, "trend": 200},
    "stoch": {"k": 14, "d": 3},
    "stochrsi": {"n": 14, "thr": 50},
    "uo": {"thr": 50},
    "rsi14_dip": {"n": 14, "os": 30, "trend": 200},
    "crsi": {"rsi_lt": 10, "exit_gt": 70, "adx_gt": 30},
    "adx_sma": {"n": 14, "adx_gt": 25, "trend": 200},
    "vortex": {"n": 14},
    "trix": {"n": 15},
    "chandelier": {"entry": 22, "atr": 22, "mult": 3},
    "hi52": {"n": 252, "near": 95},
    "tsmom_vote": {"slow": 252, "vote": 2},
    "vol_trend": {"mom": 252, "vol": 21},
    "dow": {"dow": 0},
    "tom": {"last": 1, "first": 3},
    "halloween": {"start": 11},
}


def make_bars(opens, closes=None, highs=None, lows=None, day0=DAY0) -> DailyBars:
    o = np.asarray(opens, dtype=np.float64)
    c = o.copy() if closes is None else np.asarray(closes, dtype=np.float64)
    h = np.maximum(o, c) if highs is None else np.asarray(highs, dtype=np.float64)
    lo = np.minimum(o, c) if lows is None else np.asarray(lows, dtype=np.float64)
    ts = day0 + np.arange(o.shape[0], dtype=np.int64) * DAY_MS
    return DailyBars(ts=ts, open=o, high=h, low=lo, close=c)


def random_walk(n: int = 520, seed: int = 19) -> DailyBars:
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
    return make_bars(close, highs=close, lows=close - 1.0)


def _fall(n: int, step: float = 1.0) -> DailyBars:
    close = 100.0 + n - np.arange(n, dtype=np.float64) * step
    return make_bars(close, highs=close, lows=close - 1.0)


class CatalogOrderTests(unittest.TestCase):
    def test_batch3_is_appended_after_the_first_thirty_two(self):
        self.assertEqual(tuple(s.key for s in LIBRARY[BATCH2_END:BATCH2_END + len(NEW)]), NEW)
        self.assertEqual(LIBRARY[BATCH2_END - 1].key, "ibs")
        for key, center in CENTERS.items():
            strategy = strategy_by_key(key)
            self.assertEqual(strategy.center, center)
            self.assertEqual(strategy.stop_frac, LIBRARY_STOP_FRAC)
            self.assertTrue(strategy.citation.tested_rule.strip())
            self.assertTrue(strategy.citation.implemented_rule.strip())

    def test_skipped_near_duplicates_are_not_keys(self):
        keys = {s.key for s in LIBRARY}
        # A 14-period stochastic oversold dip is the same entry as lib.willr.
        self.assertNotIn("stoch_dip", keys)
        self.assertIn("willr", keys)
        self.assertIn("stoch", keys)
        self.assertIn("donchian55", keys)
        self.assertIn("turtle55", keys)


class WarmupAndLookaheadTests(unittest.TestCase):
    def test_each_new_signal_does_not_look_ahead(self):
        bars = random_walk(560)
        cuts = (120, 240, 360, 480)
        for key in NEW:
            strategy = strategy_by_key(key)
            for params in grid_params(strategy):
                ready = signal_ready(strategy, bars, params)
                self.assertTrue(bool(ready.any()), key)
                first = int(np.flatnonzero(ready)[0])
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
    def test_donchian55_exits_on_the_same_window_turtle55_keeps(self):
        n = 80
        close = np.full(n, 85.0)
        high = np.full(n, 90.0)
        low = np.full(n, 80.0)
        close[41:70] = 105.0
        high[41:70] = 110.0
        low[41:70] = 100.0
        close[60] = 120.0
        high[60] = 120.0
        low[60] = 115.0
        # Under the recent 20-day low (100) and still above the 55-day low (80).
        close[70] = 95.0
        high[70] = 100.0
        low[70] = 95.0
        bars = make_bars(close, close, high, low)
        turtle = strategy_by_key("turtle55").signal(bars, strategy_by_key("turtle55").center)
        channel = strategy_by_key("donchian55").signal(bars, strategy_by_key("donchian55").center)
        self.assertFalse(bool(channel[:60].any()))
        self.assertTrue(bool(channel[60]))
        self.assertTrue(bool(turtle[60]))
        self.assertFalse(bool(turtle[70]))
        self.assertTrue(bool(channel[70]))

    def test_keltner_revert_buys_the_lower_band_and_sells_at_the_mean(self):
        n = 50
        close = np.full(n, 100.0)
        high = np.full(n, 101.0)
        low = np.full(n, 99.0)
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("keltner_rev").signal(bars, strategy_by_key("keltner_rev").center)
        self.assertFalse(bool(signal.any()))
        close[40] = 1.0
        high[40] = 2.0
        low[40] = 1.0
        close[41] = 100.0
        high[41] = 101.0
        low[41] = 99.0
        dipped = make_bars(close, close, high, low)
        dipped_signal = strategy_by_key("keltner_rev").signal(dipped, strategy_by_key("keltner_rev").center)
        breakout = strategy_by_key("keltner20").signal(dipped, strategy_by_key("keltner20").center)
        self.assertFalse(bool(dipped_signal[:40].any()))
        self.assertTrue(bool(dipped_signal[40]))
        self.assertFalse(bool(dipped_signal[41]))
        self.assertFalse(bool(breakout[40]))

    def test_sma_band_holds_inside_the_one_percent_band(self):
        close = np.full(220, 100.0)
        close[200] = 130.0
        # Back inside the band and under the average: the band holds, the naked cross is flat.
        close[201:205] = 99.5
        close[205] = 90.0
        bars = make_bars(close)
        band = strategy_by_key("sma200_band").signal(bars, strategy_by_key("sma200_band").center)
        naked = strategy_by_key("sma200").signal(bars, {"short": 1, "long": 200})
        self.assertFalse(bool(band[:200].any()))
        self.assertTrue(bool(band[200:205].all()))
        self.assertFalse(bool(band[205]))
        self.assertFalse(bool(naked[201]))
        self.assertTrue(bool(band[201]))

    def test_psar_stays_long_in_a_rise_and_flips_off_in_a_crash(self):
        rise = _rise(40)
        params = strategy_by_key("psar").center
        up = strategy_by_key("psar").signal(rise, params)
        self.assertFalse(bool(up[0]))
        self.assertTrue(bool(up[1:].all()))
        close = rise.close.copy()
        high = rise.high.copy()
        low = rise.low.copy()
        close[30:] = 1.0
        high[30:] = 2.0
        low[30:] = 0.5
        crashed = make_bars(close, close, high, low)
        down = strategy_by_key("psar").signal(crashed, params)
        self.assertTrue(bool(down[29]))
        self.assertFalse(bool(down[30:].any()))

    def test_kama_and_hull_turn_on_in_a_rise_and_stay_off_when_flat(self):
        rise = _rise(80)
        flat = make_bars(np.full(80, 50.0), highs=np.full(80, 51.0), lows=np.full(80, 49.0))
        for key in ("kama", "hma"):
            params = strategy_by_key(key).center
            up = strategy_by_key(key).signal(rise, params)
            still = strategy_by_key(key).signal(flat, params)
            ready = signal_ready(strategy_by_key(key), rise, params)
            first = int(np.flatnonzero(ready)[0])
            # The KAMA seed equals the close, so the first defined bar is not above it.
            start = first + (1 if key == "kama" else 0)
            self.assertTrue(bool(up[start:].all()), key)
            self.assertFalse(bool(still.any()), key)

    def test_aroon_cci_vortex_and_trix_follow_the_slope(self):
        up = _rise(80)
        down = _fall(80)
        for key in ("aroon", "cci", "vortex", "trix"):
            params = strategy_by_key(key).center
            risen = strategy_by_key(key).signal(up, params)
            fallen = strategy_by_key(key).signal(down, params)
            ready = signal_ready(strategy_by_key(key), up, params)
            first = int(np.flatnonzero(ready)[0])
            self.assertTrue(bool(risen[first:].all()), key)
            self.assertFalse(bool(fallen[first:].any()), key)

    def test_cci_dip_buys_below_minus_100_and_exits_through_zero(self):
        close = 100.0 + np.arange(250, dtype=np.float64) * 0.5
        close[230] = close[229] - 40.0
        bars = make_bars(close)
        signal = strategy_by_key("cci_dip").signal(bars, strategy_by_key("cci_dip").center)
        trend = strategy_by_key("cci").signal(bars, strategy_by_key("cci").center)
        self.assertFalse(bool(signal[:230].any()))
        self.assertTrue(bool(signal[230]))
        self.assertFalse(bool(signal[231]))
        self.assertFalse(bool(trend[230]))

    def test_stochastic_cross_matches_k_above_its_average(self):
        from hedge_fund.library.indicators import sma_skip_leading, stochastic_k

        bars = random_walk(80, seed=4)
        params = strategy_by_key("stoch").center
        k = stochastic_k(bars.high, bars.low, bars.close, params["k"])
        d = sma_skip_leading(k, params["d"])
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(k) & np.isfinite(d) & (k > d)
        got = strategy_by_key("stoch").signal(bars, params)
        np.testing.assert_array_equal(got, expected)
        self.assertFalse(np.array_equal(
            got, strategy_by_key("willr").signal(bars, strategy_by_key("willr").center),
        ))

    def test_stochrsi_and_ultimate_turn_on_when_the_close_is_the_high(self):
        up = _rise(80)
        for key in ("stochrsi", "uo"):
            params = strategy_by_key(key).center
            signal = strategy_by_key(key).signal(up, params)
            ready = signal_ready(strategy_by_key(key), up, params)
            first = int(np.flatnonzero(ready)[0])
            self.assertTrue(bool(signal[first:].all()), key)
            self.assertFalse(bool(signal[:first].any()), key)
        quiet = make_bars(np.full(80, 100.0), highs=np.full(80, 101.0), lows=np.full(80, 99.0))
        # Symmetric bars pin the Ultimate Oscillator at 50, which is not above 50.
        self.assertFalse(bool(strategy_by_key("uo").signal(quiet, strategy_by_key("uo").center).any()))

    def test_rsi14_dip_is_not_the_50_line(self):
        close = 100.0 + np.arange(260, dtype=np.float64)
        close[230] = close[229] - 80.0
        bars = make_bars(close)
        value = rsi_wilder(close, 14)
        self.assertLess(float(value[230]), 30.0)
        self.assertGreater(float(close[230]), float(sma(close, 200)[230]))
        dip = strategy_by_key("rsi14_dip").signal(bars, strategy_by_key("rsi14_dip").center)
        level = strategy_by_key("rsi14").signal(bars, strategy_by_key("rsi14").center)
        self.assertFalse(bool(dip[:230].any()))
        self.assertTrue(bool(dip[230]))
        self.assertTrue(bool(level[220]))
        self.assertFalse(bool(dip[220]))

    def test_connors_rsi_buys_the_guidebook_dip_and_exits_above_70(self):
        close = 100.0 + np.arange(250, dtype=np.float64) * 0.4
        for i in range(220, 226):
            close[i] = close[i - 1] * 0.97
        bars = make_bars(close, highs=close + 1.0, lows=close - 1.0)
        value = connors_rsi(close)
        signal = strategy_by_key("crsi").signal(bars, strategy_by_key("crsi").center)
        self.assertGreater(float(value[219]), 10.0)
        self.assertLess(float(value[220]), 10.0)
        self.assertGreater(float(value[226]), 70.0)
        self.assertFalse(bool(signal[:220].any()))
        self.assertTrue(bool(signal[220:226].all()))
        self.assertFalse(bool(signal[226]))
        rsi2 = strategy_by_key("rsi2").signal(bars, strategy_by_key("rsi2").center)
        self.assertFalse(np.array_equal(signal, rsi2))

    def test_adx_filter_requires_the_200_day_average(self):
        up = _rise(260)
        down = _fall(260)
        params = strategy_by_key("adx_sma").center
        risen = strategy_by_key("adx_sma").signal(up, params)
        fallen = strategy_by_key("adx_sma").signal(down, params)
        plain = strategy_by_key("adx14").signal(up, strategy_by_key("adx14").center)
        self.assertFalse(bool(risen[:199].any()))
        self.assertTrue(bool(risen[-1]))
        self.assertFalse(bool(fallen.any()))
        self.assertFalse(np.array_equal(risen, plain))

    def test_chandelier_holds_a_breakout_and_exits_the_giveback(self):
        close = np.full(90, 100.0)
        close[40:] = 140.0
        bars = make_bars(close, highs=close + 1.0, lows=close - 1.0)
        params = strategy_by_key("chandelier").center
        held = strategy_by_key("chandelier").signal(bars, params)
        self.assertFalse(bool(held[:40].any()))
        self.assertTrue(bool(held[40:70].all()))
        crashed = close.copy()
        crashed[60:] = 50.0
        broken = make_bars(crashed, highs=crashed + 1.0, lows=crashed - 1.0)
        stopped = strategy_by_key("chandelier").signal(broken, params)
        self.assertTrue(bool(stopped[59]))
        self.assertFalse(bool(stopped[60]))

    def test_hi52_is_on_at_the_high_and_off_after_a_ten_percent_drop(self):
        close = 100.0 + np.arange(280, dtype=np.float64)
        bars = make_bars(close)
        params = strategy_by_key("hi52").center
        signal = strategy_by_key("hi52").signal(bars, params)
        self.assertFalse(bool(signal[:251].any()))
        self.assertTrue(bool(signal[251:].all()))
        close[270] = close[269] * 0.80
        dipped = make_bars(close)
        dipped_signal = strategy_by_key("hi52").signal(dipped, params)
        self.assertTrue(bool(dipped_signal[269]))
        self.assertFalse(bool(dipped_signal[270]))

    def test_tsmom_vote_needs_two_signs(self):
        close = np.arange(1, 401, dtype=np.float64)
        bars = make_bars(close)
        params = strategy_by_key("tsmom_vote").center
        signal = strategy_by_key("tsmom_vote").signal(bars, params)
        self.assertFalse(bool(signal[:252].any()))
        self.assertTrue(bool(signal[252:].all()))
        # Break the 21-day and 63-day signs. The 252-day sign stays up, so the vote is 1.
        close[300] = close[300 - 63] - 1.0
        close[300] = min(close[300], close[300 - 21] - 1.0)
        mixed = make_bars(close)
        mixed_signal = strategy_by_key("tsmom_vote").signal(mixed, params)
        self.assertFalse(bool(mixed_signal[300]))
        self.assertGreater(float(close[300]), float(close[300 - 252]))

    def test_vol_trend_stands_aside_when_recent_vol_jumps(self):
        calm = 100.0 * np.cumprod(np.concatenate(([1.0], np.full(399, 1.002))))
        bars = make_bars(calm, highs=calm * 1.001, lows=calm * 0.999)
        params = strategy_by_key("vol_trend").center
        signal = strategy_by_key("vol_trend").signal(bars, params)
        self.assertFalse(bool(signal[:252].any()))
        self.assertTrue(bool(signal[252:].all()))
        rng = np.random.default_rng(2)
        shock = np.zeros(400)
        shock[300:330] = rng.normal(0.0, 0.08, 30)
        wild = 100.0 * np.exp(np.cumsum(np.full(400, 0.002) + shock))
        wild_bars = make_bars(wild, highs=wild * 1.01, lows=wild * 0.99)
        wild_signal = strategy_by_key("vol_trend").signal(wild_bars, params)
        self.assertTrue(bool(wild_signal[280]))
        self.assertFalse(bool(wild_signal[310:325].any()))
        self.assertGreater(float(wild[310]), float(wild[310 - 252]))

    def test_monday_signal_uses_tomorrows_weekday(self):
        bars = random_walk(40)
        params = strategy_by_key("dow").center
        tomorrow = weekday_utc(bars.ts + np.int64(DAY_MS))
        np.testing.assert_array_equal(
            strategy_by_key("dow").signal(bars, params), tomorrow == 0,
        )
        sunday = dict(params)
        sunday["dow"] = 6
        np.testing.assert_array_equal(
            strategy_by_key("dow").signal(bars, sunday), tomorrow == 6,
        )
        self.assertEqual(int(weekday_utc(np.asarray([DAY0]))[0]), 2)

    def test_turn_of_month_is_the_last_day_and_the_first_three(self):
        # 2023-11-15 through enough days to cross into December.
        n = 40
        ts = DAY0 + np.arange(n, dtype=np.int64) * DAY_MS
        bars = make_bars(np.full(n, 100.0), day0=DAY0)
        tomorrow = ts + np.int64(DAY_MS)
        expected = (day_of_month(tomorrow) > days_in_month(tomorrow) - 1) | (day_of_month(tomorrow) <= 3)
        np.testing.assert_array_equal(
            strategy_by_key("tom").signal(bars, strategy_by_key("tom").center), expected,
        )
        # Index 15 is 2023-11-30. Tomorrow is 1 December, inside the window.
        self.assertEqual(
            datetime.fromtimestamp(int(bars.ts[15]) / 1000, timezone.utc).strftime("%Y-%m-%d"),
            "2023-11-30",
        )
        self.assertTrue(bool(expected[14]))
        self.assertTrue(bool(expected[17]))
        self.assertFalse(bool(expected[18]))

    def test_halloween_is_long_november_through_april(self):
        n = 400
        bars = make_bars(np.full(n, 100.0))
        month = month_of_year(bars.ts + np.int64(DAY_MS))
        expected = ((month - 11) % 12) < 6
        np.testing.assert_array_equal(
            strategy_by_key("halloween").signal(bars, strategy_by_key("halloween").center),
            expected,
        )
        # 2023-11-16 is inside the long half. 2024-05-01 is the first flat day.
        self.assertTrue(bool(expected[0]))
        may = datetime(2024, 5, 1, tzinfo=timezone.utc)
        # The signal on the prior close decides the May 1 session.
        idx = int((int(may.timestamp() * 1000) - DAY_MS - DAY0) // DAY_MS)
        self.assertFalse(bool(expected[idx]))
        self.assertTrue(bool(expected[idx - 1]))


if __name__ == "__main__":
    unittest.main()
