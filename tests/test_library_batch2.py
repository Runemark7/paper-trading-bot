"""Batch-2 daily signals: on/off, warm-up, and no look-ahead.

The original 15 strategies stay first in the catalog, with the same
centers. New names are what a library-mode claim leases after those 15
are tested. The shared score start stays 2018-12-01.
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
from hedge_fund.library.indicators import ema, ichimoku_cloud_top, rsi_wilder

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC
ORIGINAL = (
    "sma200", "sma5x150", "sma100", "golden_cross", "ema20x50",
    "faber10m", "tsmom12m", "tsmom6m", "tsmom3m", "tsmom1w",
    "detzel2w", "bll_trb50", "turtle55", "rsi2", "macd",
)
ORIGINAL_CENTERS = {
    "sma200": {"short": 1, "long": 200},
    "sma5x150": {"short": 5, "long": 150},
    "sma100": {"n": 100},
    "golden_cross": {"short": 50, "long": 200},
    "ema20x50": {"fast": 20, "slow": 50},
    "faber10m": {"months": 10},
    "tsmom12m": {"months": 12},
    "tsmom6m": {"months": 6},
    "tsmom3m": {"months": 3},
    "tsmom1w": {"weeks": 1},
    "detzel2w": {"n": 14},
    "bll_trb50": {"n": 50, "hold": 10},
    "turtle55": {"entry": 55, "exit": 20},
    "rsi2": {"rsi_lt": 5, "trend": 200, "exit_ma": 5},
    "macd": {"fast": 12, "slow": 26, "signal": 9},
}
NEW = (
    "turtle20", "donchian20", "ema12x26", "ema50x200",
    "bb_revert", "bb_break", "keltner20", "rsi14", "rsi2_10",
    "tsmom30d", "tsmom90d", "tsmom180d", "adx14", "ichimoku",
    "supertrend", "willr", "ibs",
)


def make_bars(opens, closes=None, highs=None, lows=None, day0=DAY0) -> DailyBars:
    o = np.asarray(opens, dtype=np.float64)
    c = o.copy() if closes is None else np.asarray(closes, dtype=np.float64)
    h = np.maximum(o, c) if highs is None else np.asarray(highs, dtype=np.float64)
    lo = np.minimum(o, c) if lows is None else np.asarray(lows, dtype=np.float64)
    # Keep high/low outside the bodies when the caller passed them through
    # np.maximum already. A caller that passes explicit highs wins.
    if highs is not None:
        h = np.asarray(highs, dtype=np.float64)
    if lows is not None:
        lo = np.asarray(lows, dtype=np.float64)
    ts = day0 + np.arange(o.shape[0], dtype=np.int64) * DAY_MS
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


class CatalogOrderTests(unittest.TestCase):
    def test_original_fifteen_are_unchanged_and_batch2_is_appended(self):
        self.assertEqual(tuple(s.key for s in LIBRARY[:15]), ORIGINAL)
        self.assertEqual(tuple(s.key for s in LIBRARY[15:]), NEW)
        for key, center in ORIGINAL_CENTERS.items():
            strategy = strategy_by_key(key)
            self.assertEqual(strategy.center, center)
            self.assertEqual(strategy.signal.__name__, {
                "sma200": "_sma_cross",
                "sma5x150": "_sma_cross",
                "sma100": "_price_sma",
                "golden_cross": "_sma_cross",
                "ema20x50": "_ema_cross",
                "faber10m": "_faber_monthly",
                "tsmom12m": "_tsmom_monthly",
                "tsmom6m": "_tsmom_monthly",
                "tsmom3m": "_tsmom_monthly",
                "tsmom1w": "_tsmom_weekly",
                "detzel2w": "_detzel_log_ma",
                "bll_trb50": "_bll_trb",
                "turtle55": "_turtle",
                "rsi2": "_rsi2",
                "macd": "_macd",
            }[key])


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
        # No batch-2 neighbour may be the bar that pushes that date later.
        for key in NEW:
            strategy = strategy_by_key(key)
            for params in grid_params(strategy):
                ready = signal_ready(strategy, data.discovery, params)
                first = int(np.flatnonzero(ready)[0])
                self.assertLessEqual(first + 1, data.score_index, f"{key} {params}")


class OnOffTests(unittest.TestCase):
    def test_turtle20_exits_on_the_10_day_low_and_donchian20_does_not(self):
        n = 42
        close = np.full(n, 95.0)
        high = np.full(n, 100.0)
        low = np.full(n, 90.0)
        close[30] = 110.0
        high[30] = 110.0
        low[30] = 100.0
        close[31:41] = 108.0
        high[31:41] = 112.0
        low[31:41] = 96.0
        close[41] = 94.0
        high[41] = 100.0
        low[41] = 94.0
        bars = make_bars(close, close, high, low)
        turtle = strategy_by_key("turtle20").signal(bars, strategy_by_key("turtle20").center)
        donchian = strategy_by_key("donchian20").signal(bars, strategy_by_key("donchian20").center)
        self.assertFalse(bool(turtle[:30].any()))
        self.assertFalse(bool(donchian[:30].any()))
        self.assertTrue(bool(turtle[30:41].all()))
        self.assertTrue(bool(donchian[30:].all()))
        self.assertFalse(bool(turtle[41]))
        self.assertTrue(bool(donchian[41]))

    def test_ema12x26_is_the_macd_zero_line_and_not_the_signal_cross(self):
        bars = random_walk(180, seed=3)
        fast = ema(bars.close, 12)
        slow = ema(bars.close, 26)
        with np.errstate(invalid="ignore"):
            zero = np.isfinite(fast) & np.isfinite(slow) & (fast > slow)
        got = strategy_by_key("ema12x26").signal(bars, strategy_by_key("ema12x26").center)
        np.testing.assert_array_equal(got, zero)
        macd = strategy_by_key("macd").signal(bars, strategy_by_key("macd").center)
        self.assertFalse(np.array_equal(got, macd))
        flat = make_bars(np.full(80, 100.0))
        self.assertFalse(bool(strategy_by_key("ema12x26").signal(flat, strategy_by_key("ema12x26").center).any()))

    def test_ema50x200_turns_on_in_a_rise_and_off_when_flat_equals(self):
        rise = make_bars(np.arange(1, 401, dtype=np.float64))
        signal = strategy_by_key("ema50x200").signal(rise, strategy_by_key("ema50x200").center)
        self.assertFalse(bool(signal[:199].any()))
        self.assertTrue(bool(signal[199:].all()))
        flat = make_bars(np.full(260, 50.0))
        self.assertFalse(bool(strategy_by_key("ema50x200").signal(flat, strategy_by_key("ema50x200").center).any()))

    def test_bollinger_revert_buys_the_break_of_the_lower_band_and_sells_at_the_mean(self):
        close = np.full(40, 100.0)
        close[30] = 50.0
        close[31] = 100.0
        bars = make_bars(close)
        signal = strategy_by_key("bb_revert").signal(bars, strategy_by_key("bb_revert").center)
        self.assertFalse(bool(signal[:30].any()))
        self.assertTrue(bool(signal[30]))
        self.assertFalse(bool(signal[31]))

    def test_bollinger_breakout_buys_the_upper_band_and_sells_through_the_mean(self):
        close = np.full(40, 100.0)
        close[30] = 200.0
        close[31] = 90.0
        bars = make_bars(close)
        signal = strategy_by_key("bb_break").signal(bars, strategy_by_key("bb_break").center)
        self.assertFalse(bool(signal[:30].any()))
        self.assertTrue(bool(signal[30]))
        self.assertFalse(bool(signal[31]))

    def test_keltner_stays_flat_in_a_range_and_turns_on_through_the_band(self):
        n = 50
        close = np.full(n, 100.0)
        high = np.full(n, 101.0)
        low = np.full(n, 99.0)
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("keltner20").signal(bars, strategy_by_key("keltner20").center)
        self.assertFalse(bool(signal.any()))
        close[40] = 1000.0
        high[40] = 1000.0
        low[40] = 900.0
        close[41] = 100.0
        high[41] = 101.0
        low[41] = 99.0
        spiked = make_bars(close, close, high, low)
        spiked_signal = strategy_by_key("keltner20").signal(spiked, strategy_by_key("keltner20").center)
        self.assertFalse(bool(spiked_signal[:40].any()))
        self.assertTrue(bool(spiked_signal[40]))
        self.assertFalse(bool(spiked_signal[41]))

    def test_rsi14_is_on_in_a_rise_and_off_in_a_fall(self):
        up = make_bars(100.0 + np.arange(40))
        down = make_bars(100.0 - np.arange(40))
        params = strategy_by_key("rsi14").center
        up_signal = strategy_by_key("rsi14").signal(up, params)
        down_signal = strategy_by_key("rsi14").signal(down, params)
        self.assertFalse(bool(up_signal[:14].any()))
        self.assertTrue(bool(up_signal[14:].all()))
        self.assertFalse(bool(down_signal.any()))
        # One manual Wilder step: constant gains keep RSI at 100, which is above 50.
        value = rsi_wilder(up.close, 14)
        self.assertAlmostEqual(float(value[14]), 100.0)

    def test_rsi2_10_fires_on_a_milder_dip_than_rsi2(self):
        close = 50.0 + np.arange(230, dtype=np.float64) * 0.4
        # One down close of 5 after a steady +0.4 trend. Wilder RSI(2) is
        # 100 * 0.4 / (0.4 + 5) ≈ 7.4, between the cutoffs 5 and 10, and the
        # close stays above the 200-day SMA.
        close[221] = close[220] - 5.0
        bars = make_bars(close, highs=close + 0.2, lows=close - 0.2)
        rsi = rsi_wilder(close, 2)
        self.assertGreater(float(rsi[221]), 5.0)
        self.assertLess(float(rsi[221]), 10.0)
        loose = strategy_by_key("rsi2_10").signal(bars, strategy_by_key("rsi2_10").center)
        tight = strategy_by_key("rsi2").signal(bars, strategy_by_key("rsi2").center)
        self.assertTrue(bool(loose[221]))
        self.assertFalse(bool(tight[221]))
        self.assertFalse(bool(loose[:200].any()))

    def test_daily_tsmom_follows_the_sign_of_the_n_day_change(self):
        close = np.arange(1, 401, dtype=np.float64)
        bars = make_bars(close)
        for key, n in (("tsmom30d", 30), ("tsmom90d", 90), ("tsmom180d", 180)):
            signal = strategy_by_key(key).signal(bars, strategy_by_key(key).center)
            self.assertFalse(bool(signal[:n].any()), key)
            self.assertTrue(bool(signal[n:].all()), key)
        close[250] = close[250 - 90] - 1.0
        dipped = make_bars(close)
        signal = strategy_by_key("tsmom90d").signal(dipped, strategy_by_key("tsmom90d").center)
        self.assertFalse(bool(signal[250]))
        self.assertTrue(bool(signal[249]))
        self.assertTrue(bool(signal[251]))

    def test_adx_is_long_only_in_the_uptrend(self):
        def ramp(sign: float) -> DailyBars:
            close = 100.0 + sign * np.arange(80, dtype=np.float64)
            return make_bars(close, highs=close + 0.4, lows=close - 0.4)

        params = strategy_by_key("adx14").center
        up = strategy_by_key("adx14").signal(ramp(1.0), params)
        down = strategy_by_key("adx14").signal(ramp(-1.0), params)
        self.assertFalse(bool(up[:27].any()))
        self.assertTrue(bool(up[-1]))
        self.assertFalse(bool(down.any()))

    def test_ichimoku_cloud_uses_the_lagged_span_and_not_the_future(self):
        n = 160
        close = np.full(n, 100.0)
        close[100:] = 500.0
        high = close.copy()
        low = close.copy()
        bars = make_bars(close, close, high, low)
        params = strategy_by_key("ichimoku").center
        signal = strategy_by_key("ichimoku").signal(bars, params)
        top = ichimoku_cloud_top(high, low, 9, 26, 52)
        # Cloud plotted at t is the flat 100 until the jump has aged 26 days.
        self.assertTrue(np.isnan(top[76]))
        self.assertAlmostEqual(float(top[77]), 100.0)
        self.assertAlmostEqual(float(top[100]), 100.0)
        self.assertFalse(bool(signal[:100].any()))
        self.assertTrue(bool(signal[100]))
        # A later close cannot change the cloud already plotted.
        shocked = close.copy()
        shocked[101:] = 1.0
        shocked_top = ichimoku_cloud_top(shocked, shocked, 9, 26, 52)
        np.testing.assert_array_equal(shocked_top[:101], top[:101])

    def test_supertrend_turns_on_through_the_upper_band_and_off_through_the_lower(self):
        n = 40
        close = np.full(n, 100.0)
        high = np.full(n, 101.0)
        low = np.full(n, 99.0)
        quiet = make_bars(close, close, high, low)
        params = strategy_by_key("supertrend").center
        self.assertFalse(bool(strategy_by_key("supertrend").signal(quiet, params).any()))
        close[25] = 1000.0
        high[25] = 1000.0
        low[25] = 900.0
        close[26] = 1.0
        high[26] = 2.0
        low[26] = 1.0
        spiked = make_bars(close, close, high, low)
        signal = strategy_by_key("supertrend").signal(spiked, params)
        self.assertFalse(bool(signal[:25].any()))
        self.assertTrue(bool(signal[25]))
        self.assertFalse(bool(signal[26]))

    def test_willr_buys_an_oversold_close_above_the_200_day_average(self):
        close = 100.0 + np.arange(230, dtype=np.float64)
        high = close + 1.0
        low = close - 1.0
        close[210] = 280.0
        low[210] = 280.0
        high[210] = 281.0
        close[211] = 400.0
        high[211] = 400.0
        low[211] = 390.0
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("willr").signal(bars, strategy_by_key("willr").center)
        self.assertFalse(bool(signal[:210].any()))
        self.assertTrue(bool(signal[210]))
        self.assertFalse(bool(signal[211]))

    def test_ibs_buys_a_weak_close_above_the_200_day_average_and_sells_a_strong_one(self):
        close = 100.0 + np.arange(230, dtype=np.float64)
        high = close + 2.0
        low = close - 2.0
        # IBS 0.10 at 210, IBS 0.90 at 211. Both closes stay above the 200-day SMA.
        low[210] = 300.0
        high[210] = 310.0
        close[210] = 301.0
        low[211] = 310.0
        high[211] = 320.0
        close[211] = 319.0
        bars = make_bars(close, close, high, low)
        signal = strategy_by_key("ibs").signal(bars, strategy_by_key("ibs").center)
        self.assertFalse(bool(signal[:210].any()))
        self.assertTrue(bool(signal[210]))
        self.assertFalse(bool(signal[211]))


if __name__ == "__main__":
    unittest.main()
