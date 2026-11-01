"""Batch-8 daily signals: on/off, warm-up, no look-ahead, and a trade on the tape.

The 154 names ahead of these keys stay where they are. Batch 8 is what a
library-mode claim leases after those 154 are tested. The shared score
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
    aroon,
    balance_of_power,
    center_of_gravity,
    cyber_cycle,
    demarker,
    detrended_price,
    fisher_transform,
    hull_ma,
    intraday_momentum,
    psychological_line,
    qstick,
    roofing_filter,
    rsi_wilder,
    sma_skip_leading,
    spearman_rho,
    theil_sen_slope,
)
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC, a Wednesday
BATCH7_END = 154
NEW = (
    "rsi_sig", "dm_sig", "qstick_sig", "dpo_sig", "bop_sig", "psych_sig",
    "fisher_z", "cyber_z", "cog", "roof", "spearman", "theil", "hma_px",
    "matchlow", "invhammer", "strike", "belt", "aroon_hi", "imi", "rsi_fail",
)
CENTERS = {
    "rsi_sig": {"signal": 9},
    "dm_sig": {"signal": 9},
    "qstick_sig": {"signal": 5},
    "dpo_sig": {"signal": 9},
    "bop_sig": {"signal": 9},
    "psych_sig": {"signal": 5},
    "fisher_z": {"n": 10},
    "cyber_z": {"alpha": 7},
    "cog": {"n": 10},
    "roof": {"n": 48},
    "spearman": {"n": 10},
    "theil": {"n": 10},
    "hma_px": {"n": 16},
    "matchlow": {"trend": 200},
    "invhammer": {"trend": 200},
    "strike": {"trend": 200},
    "belt": {"trend": 200},
    "aroon_hi": {"n": 25},
    "imi": {"n": 14},
    "rsi_fail": {"trend": 200},
}


def _ready_index(key: str, params: dict) -> int:
    if key == "rsi_sig":
        return 14 + params["signal"] - 1
    if key == "dm_sig":
        return 14 + params["signal"] - 1
    if key == "qstick_sig":
        return 7 + params["signal"] - 1
    if key == "dpo_sig":
        return 19 + params["signal"] - 1
    if key == "bop_sig":
        return 13 + params["signal"] - 1
    if key == "psych_sig":
        return 12 + params["signal"] - 1
    if key == "fisher_z":
        return params["n"] - 1
    if key == "cyber_z":
        return 6
    if key == "cog":
        return params["n"]
    if key == "roof":
        return params["n"]
    if key in ("spearman", "theil"):
        return params["n"] - 1
    if key == "hma_px":
        root = max(int(round(params["n"] ** 0.5)), 1)
        return params["n"] + root - 2
    if key == "matchlow":
        return max(5, params["trend"] - 1)
    if key == "invhammer":
        return max(2, params["trend"] - 1)
    if key == "strike":
        return max(3, params["trend"] - 1)
    if key == "belt":
        return max(10, params["trend"] - 1)
    if key == "aroon_hi":
        return params["n"]
    if key == "imi":
        return params["n"] - 1
    if key == "rsi_fail":
        return max(14, params["trend"] - 1)
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
    """Opens that are not the prior close, so an open-to-close rule can differ."""
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


def _rise(n: int, step: float = 1.0) -> DailyBars:
    close = 100.0 + np.arange(n, dtype=np.float64) * step
    return make_bars(close - 0.4, close, highs=close + 0.2, lows=close - 0.8)


class CatalogOrderTests(unittest.TestCase):
    def test_batch8_is_appended_after_the_first_hundred_fifty_four(self):
        end = BATCH7_END + len(NEW)
        self.assertEqual(tuple(s.key for s in LIBRARY[BATCH7_END:end]), NEW)
        self.assertEqual(LIBRARY[BATCH7_END - 1].key, "gator_wake")
        self.assertEqual(LIBRARY[end].key, "rvi_z")
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
            "projosc", "pigeon", "homing", "pgo", "disparity", "decycler",
            "coppock", "obv", "cmf", "mfi", "vwap",
        ):
            self.assertNotIn(skipped, keys)
        self.assertIn("rsi_sig", keys)
        self.assertIn("hma_px", keys)
        self.assertIn("matchlow", keys)


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


class OnOffTests(unittest.TestCase):
    def test_rsi_signal_is_above_its_average_and_not_the_fifty_line(self):
        bars = random_walk(80, seed=3)
        value = rsi_wilder(bars.close, 14)
        line = sma_skip_leading(value, 9)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & np.isfinite(line) & (value > line)
        signal = strategy_by_key("rsi_sig").signal(bars, {"signal": 9})
        np.testing.assert_array_equal(signal, expected)
        level = strategy_by_key("rsi14").signal(bars, {"n": 14, "thr": 50})
        self.assertFalse(np.array_equal(signal, level))

    def test_demarker_signal_is_not_the_half_line(self):
        bars = random_walk(80, seed=4)
        value = demarker(bars.high, bars.low, 14)
        line = sma_skip_leading(value, 9)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & np.isfinite(line) & (value > line)
        signal = strategy_by_key("dm_sig").signal(bars, {"signal": 9})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("demarker").signal(bars, {"n": 14}),
        ))

    def test_qstick_dpo_bop_and_psych_signals_are_not_their_zero_lines(self):
        bars = random_walk(90, seed=5)
        checks = (
            ("qstick_sig", {"signal": 5}, qstick(bars.open, bars.close, 8), "qstick", {"n": 8}),
            ("dpo_sig", {"signal": 9}, detrended_price(bars.close, 20), "dpo", {"n": 20}),
            (
                "bop_sig", {"signal": 9},
                balance_of_power(bars.open, bars.high, bars.low, bars.close, 14),
                "bop", {"n": 14},
            ),
            ("psych_sig", {"signal": 5}, psychological_line(bars.close, 12), "psych", {"n": 12, "thr": 50}),
        )
        for key, params, value, cousin, cousin_params in checks:
            line = sma_skip_leading(value, params["signal"])
            with np.errstate(invalid="ignore"):
                expected = np.isfinite(value) & np.isfinite(line) & (value > line)
            signal = strategy_by_key(key).signal(bars, params)
            np.testing.assert_array_equal(signal, expected)
            self.assertFalse(np.array_equal(
                signal, strategy_by_key(cousin).signal(bars, cousin_params),
            ), key)

    def test_fisher_zero_is_not_the_slope(self):
        bars = random_walk(80, seed=6)
        value = fisher_transform(bars.high, bars.low, 10)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 0.0)
        signal = strategy_by_key("fisher_z").signal(bars, {"n": 10})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("fisher").signal(bars, {"n": 10}),
        ))

    def test_cyber_zero_starts_at_the_book_filter(self):
        bars = random_walk(40, seed=7)
        price = (bars.high + bars.low) / 2.0
        cycle = cyber_cycle(price, 0.07)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(cycle) & (cycle > 0.0)
        expected[:6] = False
        signal = strategy_by_key("cyber_z").signal(bars, {"alpha": 7})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(bool(signal[:6].any()))
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("cyber").signal(bars, {"alpha": 7}),
        ))

    def test_center_of_gravity_rises_when_the_newest_close_jumps(self):
        # A steady line has a constant center of gravity. A heavier newest
        # close pulls it up; that is the turn Ehlers reads.
        close = np.full(15, 10.0)
        close[-1] = 40.0
        bars = make_bars(close, close)
        value = center_of_gravity(bars.close, 10)
        signal = strategy_by_key("cog").signal(bars, {"n": 10})
        self.assertTrue(bool(np.isfinite(value[9])))
        self.assertFalse(bool(signal[:-1].any()))
        self.assertTrue(bool(signal[-1]))
        dropped = close.copy()
        dropped[-1] = 1.0
        self.assertFalse(bool(strategy_by_key("cog").signal(make_bars(dropped, dropped), {"n": 10})[-1]))

    def test_roofing_filter_is_above_zero_and_not_the_smoother(self):
        bars = random_walk(120, seed=8)
        value = roofing_filter(bars.close, 48, 10)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 0.0)
        expected[:48] = False
        signal = strategy_by_key("roof").signal(bars, {"n": 48})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(bool(signal[:48].any()))
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("ssmooth").signal(bars, {"n": 10}),
        ))

    def test_rank_and_median_slopes_are_long_on_a_climb_and_not_least_squares(self):
        bars = _rise(20)
        self.assertTrue(bool(strategy_by_key("spearman").signal(bars, {"n": 10})[9:].all()))
        self.assertTrue(bool(strategy_by_key("theil").signal(bars, {"n": 10})[9:].all()))
        rho = spearman_rho(bars.close, 10)
        slope = theil_sen_slope(bars.close, 10)
        self.assertGreater(float(rho[9]), 0.0)
        self.assertGreater(float(slope[9]), 0.0)
        noisy = random_walk(80, seed=9)
        # One late spike can separate a least-squares slope from the ranks.
        close = noisy.close.copy()
        close[-1] = close[-2] * 1.8
        spiked = make_bars(noisy.open, close, noisy.high, noisy.low)
        rank = strategy_by_key("spearman").signal(spiked, {"n": 10})
        median = strategy_by_key("theil").signal(spiked, {"n": 10})
        least = strategy_by_key("linreg").signal(spiked, {"n": 10})
        self.assertFalse(np.array_equal(rank, least))
        self.assertFalse(np.array_equal(median, least))

    def test_price_above_the_hull_average_is_not_the_slope(self):
        bars = random_walk(80, seed=10)
        signal = strategy_by_key("hma_px").signal(bars, {"n": 16})
        line = hull_ma(bars.close, 16)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(bars.close) & np.isfinite(line) & (bars.close > line)
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("hma").signal(bars, {"n": 16}),
        ))

    def test_inverted_hammer_is_the_upper_shadow(self):
        close = np.array([10.0, 9.0, 9.2], dtype=np.float64)
        open_ = np.array([10.4, 10.0, 9.0], dtype=np.float64)
        high = np.array([10.6, 10.2, 11.0], dtype=np.float64)
        low = np.array([9.6, 8.8, 8.9], dtype=np.float64)
        bars = make_bars(open_, close, high, low)
        signal = strategy_by_key("invhammer").signal(bars, {"trend": 2})
        self.assertFalse(bool(signal[1]))
        self.assertTrue(bool(signal[2]))
        hammer = strategy_by_key("hammer").signal(bars, {"trend": 2})
        self.assertFalse(bool(hammer[2]))

    def test_three_line_strike_needs_the_white_close_through_the_first_open(self):
        # Three black candles, then a white open at the third close.
        open_ = np.array([12.0, 11.0, 10.0, 9.0], dtype=np.float64)
        close = np.array([11.0, 10.0, 9.0, 12.5], dtype=np.float64)
        bars = make_bars(open_, close)
        signal = strategy_by_key("strike").signal(bars, {"trend": 2})
        self.assertTrue(bool(signal[3]))
        close2 = close.copy()
        close2[3] = 11.5
        short = make_bars(open_, close2)
        self.assertFalse(bool(strategy_by_key("strike").signal(short, {"trend": 2})[3]))

    def test_aroon_strong_needs_both_floors(self):
        bars = _rise(40)
        up, down = aroon(bars.high, bars.low, 25)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(up) & np.isfinite(down) & (up > 70.0) & (down < 30.0)
        signal = strategy_by_key("aroon_hi").signal(bars, {"n": 25})
        np.testing.assert_array_equal(signal, expected)
        self.assertTrue(bool(signal[25:].all()))
        self.assertFalse(bool(signal[:25].any()))

    def test_intraday_momentum_uses_the_open_and_not_wilder_rsi(self):
        bars = gapped_walk(80, seed=12)
        value = intraday_momentum(bars.open, bars.close, 14)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 50.0)
        signal = strategy_by_key("imi").signal(bars, {"n": 14})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("rsi14").signal(bars, {"n": 14, "thr": 50}),
        ))
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("cmo").signal(bars, {"n": 14}),
        ))

    def test_new_signals_are_not_their_named_cousins(self):
        bars = gapped_walk(420, seed=11)
        pairs = (
            ("rsi_sig", "rsi14"), ("dm_sig", "demarker"), ("qstick_sig", "qstick"),
            ("dpo_sig", "dpo"), ("bop_sig", "bop"), ("psych_sig", "psych"),
            ("fisher_z", "fisher"), ("cyber_z", "cyber"), ("cog", "linreg"),
            ("roof", "ssmooth"), ("spearman", "linreg"), ("theil", "linreg"),
            ("hma_px", "hma"), ("aroon_hi", "aroon"), ("imi", "bop"),
            ("invhammer", "hammer"), ("strike", "engulf"), ("belt", "soldiers"),
            ("rsi_fail", "rsi14"), ("matchlow", "hammer"),
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
