"""Batch-9 daily signals: on/off, warm-up, no look-ahead, and a trade on the tape.

The 174 names ahead of these keys stay where they are. Batch 9 is what a
library-mode claim leases after those 174 are tested. The shared score
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
    alma,
    awesome_oscillator,
    band_pass,
    efficiency_ratio,
    ema,
    frama,
    relative_vigor,
    safezone_stop,
    schaff_trend,
    signed_efficiency,
    stochastic_k,
    super_smoother,
    tillson_t3,
    zero_lag_ema,
)
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC, a Wednesday
BATCH8_END = 174
NEW = (
    "rvi_z", "ao_up", "stoch50", "bull_pwr", "dm_pivot",
    "t3_up", "alma_up", "zlema_up", "frama_up", "ss_up",
    "mama_up", "itrend_up", "breakaway", "er_sign", "schaff50",
    "bandpass", "safezone",
)
CENTERS = {
    "rvi_z": {"n": 10},
    "ao_up": {"n": 34},
    "stoch50": {"k": 14},
    "bull_pwr": {"n": 13},
    "dm_pivot": {"trend": 200},
    "t3_up": {"n": 5},
    "alma_up": {"n": 9},
    "zlema_up": {"n": 20},
    "frama_up": {"n": 16},
    "ss_up": {"n": 10},
    "mama_up": {"fast": 50},
    "itrend_up": {"alpha": 7},
    "breakaway": {"trend": 200},
    "er_sign": {"n": 10},
    "schaff50": {"cycle": 10},
    "bandpass": {"n": 30},
    "safezone": {"n": 20},
}


def _safezone_first(close: np.ndarray, lookback: int, downs: int = 10) -> int:
    count = 0
    need = int(lookback) - 1
    for t in range(1, len(close)):
        if close[t] < close[t - 1]:
            count += 1
        if count >= downs and t >= need:
            return t
    return len(close)


def _ready_index(key: str, params: dict, bars: DailyBars | None = None) -> int:
    if key == "rvi_z":
        return params["n"] + 2
    if key == "ao_up":
        return params["n"]
    if key == "stoch50":
        return params["k"] - 1
    if key == "bull_pwr":
        return params["n"]
    if key == "dm_pivot":
        return max(1, params["trend"] - 1)
    if key == "t3_up":
        return 6 * (params["n"] - 1) + 1
    if key in ("alma_up", "frama_up"):
        return params["n"]
    if key == "zlema_up":
        lag = (params["n"] - 1) // 2
        return lag + params["n"]
    if key == "ss_up":
        return 3
    if key == "mama_up":
        return 41
    if key == "itrend_up":
        return 7
    if key == "breakaway":
        return max(4, params["trend"] - 1)
    if key == "er_sign":
        return params["n"]
    if key == "schaff50":
        return 49 + 2 * (params["cycle"] - 1)
    if key == "bandpass":
        return params["n"]
    if key == "safezone":
        assert bars is not None
        return _safezone_first(bars.close, params["n"])
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
    def test_batch9_is_appended_after_the_first_hundred_seventy_four(self):
        self.assertEqual(tuple(s.key for s in LIBRARY[BATCH8_END:]), NEW)
        self.assertEqual(LIBRARY[BATCH8_END - 1].key, "rsi_fail")
        self.assertEqual(len(LIBRARY), BATCH8_END + len(NEW))
        for key, center in CENTERS.items():
            strategy = strategy_by_key(key)
            self.assertEqual(strategy.center, center)
            self.assertEqual(strategy.stop_frac, LIBRARY_STOP_FRAC)
            self.assertTrue(strategy.citation.tested_rule.strip())
            self.assertTrue(strategy.citation.implemented_rule.strip())

    def test_skipped_near_copies_are_not_keys(self):
        keys = {s.key for s in LIBRARY}
        for skipped in (
            "smi_sig", "lag_sig", "laguerre_sig", "vidya_up", "kama_up", "mcg_up",
            "projosc", "pigeon", "homing", "coppock", "obv", "cmf", "mfi", "vwap", "fama",
        ):
            self.assertNotIn(skipped, keys)
        self.assertIn("rvi_z", keys)
        self.assertIn("breakaway", keys)
        self.assertIn("safezone", keys)


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


class OnOffTests(unittest.TestCase):
    def test_rvi_zero_is_not_the_signal_line(self):
        bars = gapped_walk(80, seed=3)
        value, _line = relative_vigor(bars.open, bars.high, bars.low, bars.close, 10)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 0.0)
        signal = strategy_by_key("rvi_z").signal(bars, {"n": 10})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("rvi").signal(bars, {"n": 10}),
        ))

    def test_awesome_rises_and_is_not_the_zero_line(self):
        bars = random_walk(80, seed=4)
        value = awesome_oscillator(bars.high, bars.low, 5, 34)
        prev = np.full_like(value, np.nan)
        prev[1:] = value[:-1]
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & np.isfinite(prev) & (value > prev)
        signal = strategy_by_key("ao_up").signal(bars, {"n": 34})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("awesome").signal(bars, {"fast": 5, "slow": 34}),
        ))

    def test_fast_stochastic_midpoint_is_not_the_cross_or_the_pop(self):
        bars = random_walk(80, seed=5)
        value = stochastic_k(bars.high, bars.low, bars.close, 14)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 50.0)
        signal = strategy_by_key("stoch50").signal(bars, {"k": 14})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("stoch").signal(bars, {"k": 14, "d": 3}),
        ))
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("stoch_pop").signal(bars, {"k": 14, "pop": 80}),
        ))

    def test_bull_power_needs_a_rising_average(self):
        bars = random_walk(40, seed=6)
        line = ema(bars.close, 13)
        prev = np.full_like(line, np.nan)
        prev[1:] = line[:-1]
        with np.errstate(invalid="ignore"):
            expected = (
                np.isfinite(bars.high) & np.isfinite(line) & np.isfinite(prev)
                & (bars.high > line) & (line > prev)
            )
        signal = strategy_by_key("bull_pwr").signal(bars, {"n": 13})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("elder_ray").signal(bars, {"n": 13}),
        ))

    def test_demark_pivot_weights_the_open(self):
        # Yesterday closed down, so the low is weighted twice. Today's close
        # sits between that pivot and the equal-weight floor pivot.
        open_ = np.array([10.0, 18.0, 13.0], dtype=np.float64)
        close = np.array([11.0, 12.0, 13.5], dtype=np.float64)
        high = np.array([12.0, 20.0, 14.0], dtype=np.float64)
        low = np.array([9.5, 10.0, 12.5], dtype=np.float64)
        bars = make_bars(open_, close, high, low)
        pivot = (20.0 + 2.0 * 10.0 + 12.0) / 4.0
        floor = (20.0 + 10.0 + 12.0) / 3.0
        self.assertLess(pivot, float(close[2]))
        self.assertGreater(floor, float(close[2]))
        signal = strategy_by_key("dm_pivot").signal(bars, {"trend": 2})
        self.assertTrue(bool(signal[2]))
        self.assertFalse(bool(strategy_by_key("floor_pivot").signal(bars, {"trend": 2})[2]))

    def test_rising_averages_are_not_price_above_the_line(self):
        bars = _rise(80)
        flat_close = np.full(40, 50.0)
        flat = make_bars(flat_close, flat_close)
        checks = (
            ("t3_up", {"n": 5}, tillson_t3(bars.close, 5, 0.70), "t3", {"n": 5, "vf": 70}),
            ("alma_up", {"n": 9}, alma(bars.close, 9, 0.85, 6.0), "alma", {"n": 9, "offset": 85}),
            ("zlema_up", {"n": 20}, zero_lag_ema(bars.close, 20), "zlema", {"n": 20}),
            ("frama_up", {"n": 16}, frama(bars.close, 16), "frama", {"n": 16}),
            ("ss_up", {"n": 10}, super_smoother(bars.close, 10), "ssmooth", {"n": 10}),
        )
        for key, params, value, cousin, cousin_params in checks:
            prev = np.full_like(value, np.nan)
            prev[1:] = value[:-1]
            with np.errstate(invalid="ignore"):
                expected = np.isfinite(value) & np.isfinite(prev) & (value > prev)
            if key == "ss_up":
                expected[:3] = False
            signal = strategy_by_key(key).signal(bars, params)
            np.testing.assert_array_equal(signal, expected, err_msg=key)
            self.assertTrue(bool(signal[40:].any()), key)
            self.assertFalse(bool(strategy_by_key(key).signal(flat, params).any()), key)
            self.assertFalse(np.array_equal(
                signal, strategy_by_key(cousin).signal(bars, cousin_params),
            ), key)

    def test_breakaway_needs_the_white_close_through_the_first_close(self):
        # Black, three smaller bodies, one of them under the black low, then white.
        open_ = np.array([12.0, 10.5, 10.2, 10.0, 9.5], dtype=np.float64)
        close = np.array([10.0, 10.2, 10.0, 9.8, 10.5], dtype=np.float64)
        high = np.array([12.2, 10.8, 10.4, 10.2, 10.8], dtype=np.float64)
        low = np.array([9.8, 10.0, 9.5, 9.6, 9.4], dtype=np.float64)
        bars = make_bars(open_, close, high, low)
        signal = strategy_by_key("breakaway").signal(bars, {"trend": 2})
        self.assertTrue(bool(signal[4]))
        close2 = close.copy()
        close2[4] = 9.9
        short = make_bars(open_, close2, high, low)
        self.assertFalse(bool(strategy_by_key("breakaway").signal(short, {"trend": 2})[4]))

    def test_signed_efficiency_rejects_a_decline(self):
        rise = _rise(20)
        fall_close = 200.0 - np.arange(20, dtype=np.float64)
        fall = make_bars(fall_close + 0.4, fall_close, highs=fall_close + 0.6, lows=fall_close - 0.2)
        self.assertTrue(bool(strategy_by_key("er_sign").signal(rise, {"n": 10})[10:].all()))
        self.assertFalse(bool(strategy_by_key("er_sign").signal(fall, {"n": 10}).any()))
        signed = signed_efficiency(fall.close, 10)
        absolute = efficiency_ratio(fall.close, 10)
        self.assertLess(float(signed[10]), 0.0)
        self.assertGreater(float(absolute[10]), 0.30)

    def test_schaff_midline_is_not_the_25_cross(self):
        bars = random_walk(160, seed=8)
        value = schaff_trend(bars.close, 10)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 50.0)
        signal = strategy_by_key("schaff50").signal(bars, {"cycle": 10})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("schaff").signal(bars, {"cycle": 10, "buy": 25}),
        ))

    def test_bandpass_is_above_zero_after_its_period(self):
        bars = random_walk(80, seed=9)
        value = band_pass(bars.close, 30, 0.3)
        with np.errstate(invalid="ignore"):
            expected = np.isfinite(value) & (value > 0.0)
        expected[:30] = False
        signal = strategy_by_key("bandpass").signal(bars, {"n": 30})
        np.testing.assert_array_equal(signal, expected)
        self.assertFalse(bool(signal[:30].any()))
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("roof").signal(bars, {"n": 48}),
        ))
        self.assertFalse(np.array_equal(
            signal, strategy_by_key("cyber").signal(bars, {"alpha": 7}),
        ))

    def test_safezone_ratchets_while_long_and_resets_when_flat(self):
        # Ten down days, then a climb, a pullback through the trail, and a
        # bounce that clears the fresh raw stop but not the old trail.
        close = np.array(
            [20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 12, 14, 16, 18, 17, 13, 16],
            dtype=np.float64,
        )
        low = close - 1.0
        bars = make_bars(close, close, highs=close + 0.5, lows=low)
        raw = safezone_stop(bars.close, bars.low, 5, downs=10, multiple=2.0)
        signal = strategy_by_key("safezone").signal(bars, {"n": 5})
        self.assertTrue(bool(np.isfinite(raw).any()))
        # The trail stays up through the 18 close. The 13 close is under it.
        self.assertTrue(bool(signal[14]))
        self.assertFalse(bool(signal[16]))
        # After the exit, the next close can enter on a fresh raw stop.
        self.assertGreater(float(close[17]), float(raw[17]))
        self.assertTrue(bool(signal[17]))

    def test_new_signals_are_not_their_named_cousins(self):
        bars = gapped_walk(420, seed=11)
        pairs = (
            ("rvi_z", "rvi"), ("ao_up", "awesome"), ("stoch50", "stoch"),
            ("bull_pwr", "elder_ray"), ("dm_pivot", "floor_pivot"),
            ("t3_up", "t3"), ("alma_up", "alma"),
            ("zlema_up", "zlema"), ("frama_up", "frama"),
            ("ss_up", "ssmooth"), ("mama_up", "mama"),
            ("itrend_up", "itrend"), ("breakaway", "morning"), ("er_sign", "eratio"),
            ("schaff50", "schaff"), ("bandpass", "roof"), ("safezone", "supertrend"),
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
