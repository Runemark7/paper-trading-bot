"""LazyBear WaveTrend green-dot atoms: closed-bar, HLC3, no lookahead."""
from __future__ import annotations

import math
import unittest

from hedge_fund.data.binance import Candle
from hedge_fund.signals.dynamic import eval_predicate, parse_strategy
from hedge_fund.signals.wavetrend import (
    AVERAGE_LEN,
    CHANNEL_LEN,
    CI_SCALE,
    OB_LEVEL,
    OS_LEVEL,
    SIGNAL_LEN,
    wavetrend_at,
    wavetrend_series,
    wt_below_os,
    wt_cross_down_ob,
    wt_cross_up_os,
)
from hedge_fund.trading.universe import (
    UNIVERSE_TARGET_MAX,
    UNIVERSE_TARGET_MIN,
    generate_universe,
)


def _hlc(closes: list[float], wick: float = 0.002):
    highs = [c * (1.0 + wick) for c in closes]
    lows = [c * (1.0 - wick) for c in closes]
    return closes, highs, lows


def _dump_bounce(
    n_flat: int = 40,
    n_dump: int = 12,
    n_bounce: int = 8,
    start: float = 100.0,
    dump_ret: float = -0.35,
    bounce_ret: float = 0.15,
    wick: float = 0.003,
):
    """Closed 5m-shaped tape: flat warmup, impulse dump, then bounce."""
    closes = [start] * n_flat
    px = start
    step = (1.0 + dump_ret) ** (1.0 / n_dump)
    for _ in range(n_dump):
        px *= step
        closes.append(px)
    step_up = (1.0 + bounce_ret) ** (1.0 / max(n_bounce, 1))
    for _ in range(n_bounce):
        px *= step_up
        closes.append(px)
    return _hlc(closes, wick)


def _cross_up(wt1, wt2, i: int) -> bool:
    if i < 1:
        return False
    a0, b0, a1, b1 = wt1[i - 1], wt2[i - 1], wt1[i], wt2[i]
    if any(math.isnan(x) for x in (a0, b0, a1, b1)):
        return False
    return a0 <= b0 and a1 > b1


class DefaultsDocumentedTests(unittest.TestCase):
    def test_lazybear_classic_defaults_frozen(self):
        self.assertEqual(CHANNEL_LEN, 10)
        self.assertEqual(AVERAGE_LEN, 21)
        self.assertEqual(SIGNAL_LEN, 4)
        self.assertEqual(OS_LEVEL, -60.0)
        self.assertEqual(OB_LEVEL, 60.0)
        self.assertEqual(CI_SCALE, 0.015)


class LookaheadTests(unittest.TestCase):
    def test_wt_at_i_ignores_future_bars(self):
        closes, highs, lows = _dump_bounce()
        i = 50
        wt1_p, wt2_p = wavetrend_at(highs, lows, closes, i)
        # Append a violent future spike that would rewrite any lookahead window.
        future_c = list(closes) + [closes[-1] * 2.0, closes[-1] * 0.4]
        future_h = list(highs) + [future_c[-2] * 1.05, future_c[-1] * 1.05]
        future_l = list(lows) + [future_c[-2] * 0.95, future_c[-1] * 0.95]
        wt1_f, wt2_f = wavetrend_series(future_h, future_l, future_c)
        self.assertFalse(math.isnan(wt1_p))
        self.assertAlmostEqual(wt1_p, wt1_f[i], places=12)
        self.assertAlmostEqual(wt2_p, wt2_f[i], places=12)
        pred = parse_strategy("wt_cross_up_os")
        self.assertEqual(
            eval_predicate(pred, closes, i, highs=highs, lows=lows),
            eval_predicate(pred, future_c, i, highs=future_h, lows=future_l),
        )


class GreenDotPredicateTests(unittest.TestCase):
    def _green_dot_tape(self):
        return _dump_bounce(n_flat=40, n_dump=12, n_bounce=8, dump_ret=-0.35, bounce_ret=0.15)

    def test_cross_up_in_oversold_is_true(self):
        closes, highs, lows = self._green_dot_tape()
        wt1, wt2 = wavetrend_series(highs, lows, closes)
        hits = [i for i in range(len(closes)) if _cross_up(wt1, wt2, i) and wt2[i] <= OS_LEVEL]
        self.assertTrue(hits, "fixture must produce a closed-bar green-dot")
        i = hits[0]
        self.assertTrue(_cross_up(wt1, wt2, i))
        self.assertLessEqual(wt2[i], OS_LEVEL)
        pred = parse_strategy("wt_cross_up_os")
        self.assertTrue(eval_predicate(pred, closes, i, highs=highs, lows=lows))
        self.assertTrue(wt_cross_up_os(closes, highs, lows, i))

    def test_cross_without_oversold_is_false(self):
        # Mild sine: WT1/WT2 cross while WT2 stays above −60.
        closes = [100.0] * 50 + [100.0 + 3.0 * math.sin(j / 5.0) for j in range(80)]
        closes, highs, lows = _hlc(closes)
        wt1, wt2 = wavetrend_series(highs, lows, closes)
        mid = [
            i
            for i in range(len(closes))
            if _cross_up(wt1, wt2, i) and wt2[i] > OS_LEVEL
        ]
        self.assertTrue(mid, "fixture must produce a mid-range cross-up")
        i = mid[-1]
        self.assertTrue(_cross_up(wt1, wt2, i))
        self.assertGreater(wt2[i], OS_LEVEL)
        pred = parse_strategy("wt_cross_up_os")
        self.assertFalse(eval_predicate(pred, closes, i, highs=highs, lows=lows))

    def test_oversold_without_cross_is_false(self):
        closes, highs, lows = _dump_bounce(
            n_flat=50, n_dump=30, n_bounce=0, dump_ret=-0.50, bounce_ret=0.0
        )
        wt1, wt2 = wavetrend_series(highs, lows, closes)
        os_no_x = [
            i
            for i in range(len(closes))
            if not math.isnan(wt2[i]) and wt2[i] <= OS_LEVEL and not _cross_up(wt1, wt2, i)
        ]
        self.assertTrue(os_no_x)
        i = os_no_x[0]
        pred = parse_strategy("wt_cross_up_os")
        self.assertFalse(eval_predicate(pred, closes, i, highs=highs, lows=lows))
        self.assertTrue(wt_below_os(closes, highs, lows, i))
        filt = parse_strategy("wt_below_os")
        self.assertTrue(eval_predicate(filt, closes, i, highs=highs, lows=lows))

    def test_refuses_close_as_hlc3_proxy(self):
        pred = parse_strategy("wt_cross_up_os")
        with self.assertRaises(ValueError) as ctx:
            pred([100.0] * 80)
        msg = str(ctx.exception).lower()
        self.assertIn("high/low", msg)
        self.assertIn("hlc3", msg)
        self.assertIn("silent", msg)


class ShortSideParseTests(unittest.TestCase):
    def test_cross_down_ob_parses_but_is_not_a_universe_long(self):
        closes = [100.0] * 50
        px = 100.0
        step = (1.25) ** (1.0 / 12)
        for _ in range(12):
            px *= step
            closes.append(px)
        step_d = (0.96) ** (1.0 / 3)
        for _ in range(3):
            px *= step_d
            closes.append(px)
        closes, highs, lows = _hlc(closes, wick=0.003)
        pred = parse_strategy("wt_cross_down_ob")
        hits = [
            i
            for i in range(len(closes))
            if wt_cross_down_ob(closes, highs, lows, i)
        ]
        self.assertTrue(hits)
        self.assertTrue(eval_predicate(pred, closes, hits[0], highs=highs, lows=lows))
        uni = generate_universe()
        self.assertNotIn("wt_cross_down_ob", uni)
        self.assertFalse(any("wt_cross_down_ob" in n for n in uni))


class CloseOnlyCompatTests(unittest.TestCase):
    def test_old_names_still_parse(self):
        pred = parse_strategy("dip_24b_lt1pc")
        deep = [100.0] * 24 + [98.0]
        self.assertTrue(pred(deep))
        pred_sma = parse_strategy("sma_abv_20")
        self.assertTrue(pred_sma([float(i) for i in range(1, 40)]))
        pred_bot = parse_strategy("dbl_bot_12")
        with self.assertRaises(ValueError):
            pred_bot([100.0] * 80)


class UniverseBandTests(unittest.TestCase):
    WT_NAMES = (
        "wt_cross_up_os",
        "wt_cross_up_os&sma_abv_50",
        "wt_cross_up_os&sma_stack_20_50_100",
        "wt_cross_up_os&don_lo_24",
    )
    REFUSED = (
        "wt_cross_down_ob",
        "wt_cross_down_ob&sma_abv_50",
        "mfi_14_<25",
        "dbl_top_12",
        "cmf_20",
        "vwap_50",
        "sommi_flag",
        "gold_dot",
        "wt_div",
    )

    def test_universe_stays_in_band_and_adds_wt_ands(self):
        uni = generate_universe()
        self.assertGreaterEqual(len(uni), UNIVERSE_TARGET_MIN)
        self.assertLessEqual(len(uni), UNIVERSE_TARGET_MAX)
        for name in self.WT_NAMES:
            self.assertIn(name, uni)
        for name in self.REFUSED:
            self.assertNotIn(name, uni)
        blob = " ".join(uni)
        self.assertNotIn("mfi_", blob)
        self.assertNotIn("cmf_", blob)
        self.assertNotIn("vwap_", blob)
        self.assertFalse(any(p == "dbl_top_12" or p.startswith("dbl_top_") for p in uni))
        self.assertFalse(any("wt_cross_down_ob" in p for p in uni))

    def test_new_universe_names_parse(self):
        closes, highs, lows = _dump_bounce()
        for name in generate_universe():
            pred = parse_strategy(name)
            eval_predicate(pred, closes, None, highs=highs, lows=lows)


class ComputeSignalAndBacktestTests(unittest.TestCase):
    def test_compute_signal_threads_hlc_for_wavetrend(self):
        from hedge_fund.signals.momentum import compute_signal

        closes, highs, lows = _dump_bounce()
        wt1, wt2 = wavetrend_series(highs, lows, closes)
        i = next(j for j in range(len(closes)) if _cross_up(wt1, wt2, j) and wt2[j] <= OS_LEVEL)
        candles = [
            Candle(
                ts=k * 300_000,
                open=closes[k],
                high=highs[k],
                low=lows[k],
                close=closes[k],
                volume=1.0,
            )
            for k in range(i + 1)
        ]
        sig = compute_signal(candles, "BTC/USDT", "5m", strategy="wt_cross_up_os")
        self.assertEqual(sig.direction, "long")

    def test_backtest_runs_wt_and(self):
        from hedge_fund.backtest.strategies import backtest

        closes, highs, lows = _dump_bounce(n_flat=60, n_dump=20, n_bounce=20)
        r = backtest(closes, highs, lows, "wt_cross_up_os&sma_abv_50")
        self.assertIsNone(r.error)
        self.assertGreaterEqual(r.trades, 0)


class ProtocolAmendmentTests(unittest.TestCase):
    def test_amendment_2026_09_05_in_protocol(self):
        from pathlib import Path

        text = (Path(__file__).resolve().parents[1] / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-05", text)
        self.assertIn("2026-09-05", text)
        self.assertIn("LazyBear", text)
        self.assertIn("WaveTrend", text)
        self.assertIn("wt_cross_up_os", text)
        self.assertIn("HLC3", text)
        self.assertIn("10", text)
        self.assertIn("21", text)
        self.assertIn("−60", text)
        self.assertIn("not market cipher", text.lower())
        self.assertIn("not affiliated", text.lower())
        self.assertIn("VuManChu", text)
        self.assertIn("green-dot", text)
        self.assertIn("closed-bar", text.lower())
        self.assertIn("Paper only", text)
        self.assertIn("rm_v1", text)
        self.assertIn("OOS gates", text)
        self.assertIn("No MFI", text)
        self.assertIn("QUAL_TIMEFRAME", text)
        self.assertIn("Sommi", text)


if __name__ == "__main__":
    unittest.main()
