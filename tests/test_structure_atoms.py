"""Donchian / fractal-swing / double-bottom structure atoms: no lookahead, OHLC, close-only still parses."""
from __future__ import annotations

import unittest

from hedge_fund.data.binance import Candle
from hedge_fund.signals.dynamic import eval_predicate, parse_strategy
from hedge_fund.signals.structure import (
    HOLD_ATR_MULT,
    HOLD_PCT,
    NEAR_ATR_MULT,
    NEAR_PCT,
    confirmed_swings,
    last_confirmed_swing,
    prior_donchian_high,
)
from hedge_fund.trading.universe import (
    UNIVERSE_TARGET_MAX,
    UNIVERSE_TARGET_MIN,
    generate_universe,
)


def _flat(n: int, close: float = 100.0, w: float = 1.0):
    closes = [close] * n
    highs = [close + w] * n
    lows = [close - w] * n
    return closes, highs, lows


class DonchianLookaheadTests(unittest.TestCase):
    def test_current_bar_high_cannot_create_the_break(self):
        n = 24
        closes, highs, lows = _flat(n + 1, close=100.0, w=0.5)
        # Prior 24 highs are 100.5. Current bar wicks to 110 but closes below
        # the prior high — that wick is not a close break.
        highs[-1] = 110.0
        closes[-1] = 100.4
        lows[-1] = 99.5
        self.assertEqual(prior_donchian_high(highs, n, n), 100.5)
        pred = parse_strategy("don_hi_24")
        self.assertFalse(eval_predicate(pred, closes, n, highs=highs, lows=lows))

    def test_close_break_excludes_current_high_from_the_level(self):
        n = 24
        closes, highs, lows = _flat(n + 1, close=100.0, w=0.5)
        # Current high is the series max. Close is above the *prior* high
        # (100.5) but below the current high (110). Including the current bar
        # in the Donchian window would make this False (lookahead / same-bar).
        highs[-1] = 110.0
        closes[-1] = 101.0
        lows[-1] = 99.5
        pred = parse_strategy("don_hi_24")
        self.assertTrue(eval_predicate(pred, closes, n, highs=highs, lows=lows))
        # Wrong window (include current) would use 110 as the level.
        self.assertGreater(max(highs[n - n + 1 : n + 1]), 101.0)

    def test_don_hi_refuses_close_as_high_proxy(self):
        pred = parse_strategy("don_hi_24")
        closes = [100.0] * 30
        with self.assertRaises(ValueError) as ctx:
            pred(closes)
        self.assertIn("high/low", str(ctx.exception).lower())
        self.assertIn("silent", str(ctx.exception).lower())


class SwingAndDipFixtureTests(unittest.TestCase):
    def _swing_low_tape(self):
        """Confirmed k=12 swing low at j=20 (level 90), then a later dip."""
        k = 12
        n = 60
        closes, highs, lows = _flat(n, close=101.0, w=1.0)
        j = 20
        lows[j] = 90.0
        closes[j] = 91.0
        highs[j] = 92.0
        return closes, highs, lows, k, j

    def test_unconfirmed_swing_is_invisible(self):
        closes, highs, lows, k, j = self._swing_low_tape()
        i_early = j + k - 1  # right side incomplete
        self.assertIsNone(last_confirmed_swing(lows, k, i_early, want_high=False))
        pred = parse_strategy("near_swing_lo_12")
        self.assertFalse(eval_predicate(pred, closes, i_early, highs=highs, lows=lows))

    def test_confirmed_swing_near_true_far_false(self):
        closes, highs, lows, k, j = self._swing_low_tape()
        i = j + k  # first bar the pivot is confirmed
        pred = parse_strategy("near_swing_lo_12")
        # Sitting at 101, far from 90.
        self.assertFalse(eval_predicate(pred, closes, i, highs=highs, lows=lows))
        # Close near the pivot; current low stays above 90 so the unique
        # swing at j=20 is not replaced on this bar.
        closes[i] = 90.15
        lows[i] = 90.05
        highs[i] = 91.0
        self.assertTrue(eval_predicate(pred, closes, i, highs=highs, lows=lows))

    def test_dip_and_near_swing_lo_true_false_fixtures(self):
        closes, highs, lows, k, j = self._swing_low_tape()
        # Decision bar well after confirmation so the 6-bar dip doesn't
        # need the unconfirmed pivot. Pivot at 20 is confirmed from i=32.
        i = 50
        self.assertIsNotNone(last_confirmed_swing(lows, k, i, want_high=False))
        pred = parse_strategy("dip_6b_lt2pc&near_swing_lo_12")

        # TRUE: 6-bar dump onto the swing low.
        for t in range(i - 6, i):
            closes[t] = 101.0
            highs[t] = 102.0
            lows[t] = 100.0
        closes[i] = 90.15
        highs[i] = 91.0
        lows[i] = 90.05
        self.assertLess(closes[i] / closes[i - 6] - 1.0, -0.02)
        self.assertTrue(eval_predicate(pred, closes, i, highs=highs, lows=lows))

        # FALSE: same dip magnitude, but it lands far above the swing low.
        closes[i] = 97.0
        highs[i] = 98.0
        lows[i] = 96.5
        self.assertLess(closes[i] / closes[i - 6] - 1.0, -0.02)
        self.assertFalse(eval_predicate(pred, closes, i, highs=highs, lows=lows))

        # FALSE: sitting on the swing low but no 6-bar dip.
        for t in range(i - 6, i + 1):
            closes[t] = 90.15
            highs[t] = 91.0
            lows[t] = 90.05
        self.assertGreaterEqual(closes[i] / closes[i - 6] - 1.0, -0.02)
        self.assertFalse(eval_predicate(pred, closes, i, highs=highs, lows=lows))

    def test_near_tolerance_is_documented(self):
        self.assertEqual(NEAR_PCT, 0.002)
        self.assertEqual(NEAR_ATR_MULT, 0.25)


class DoubleBottomTests(unittest.TestCase):
    def _w_tape(self, second_low=90.3, bounce_close=100.0):
        """Two k=12 fractal swing lows (first at 90), then a bounce.

        First trough at j1=24; second at j2=49; first confirmation bar
        i = j2 + k = 61. Right-hand k bars of the second swing exist only
        at i >= 61 — earlier bars must not see the second pivot.
        """
        k = 12
        j1 = 2 * k
        j2 = j1 + 2 * k + 1
        n = j2 + k + 10
        closes, highs, lows = _flat(n, close=100.0, w=1.0)
        lows[j1], closes[j1], highs[j1] = 90.0, 91.0, 92.0
        lows[j2] = second_low
        closes[j2] = second_low + 1.0
        highs[j2] = second_low + 2.0
        i = j2 + k
        closes[i] = bounce_close
        highs[i] = bounce_close + 1.0
        lows[i] = min(bounce_close - 1.0, 99.0)
        return closes, highs, lows, k, j1, j2, i

    def test_hold_tolerance_is_documented(self):
        self.assertEqual(HOLD_PCT, 0.01)
        self.assertEqual(HOLD_ATR_MULT, 1.0)

    def test_second_swing_invisible_until_k_right_hand_bars(self):
        closes, highs, lows, k, j1, j2, i = self._w_tape()
        pred = parse_strategy("dbl_bot_12")
        i_early = j2 + k - 1
        # Only the first swing is confirmed; the second is not visible yet.
        self.assertEqual(len(confirmed_swings(lows, k, i_early, want_high=False, limit=2)), 1)
        self.assertFalse(eval_predicate(pred, closes, i_early, highs=highs, lows=lows))
        self.assertEqual(len(confirmed_swings(lows, k, i, want_high=False, limit=2)), 2)
        self.assertTrue(eval_predicate(pred, closes, i, highs=highs, lows=lows))

    def test_two_similar_lows_then_bounce_is_true(self):
        closes, highs, lows, k, j1, j2, i = self._w_tape(second_low=90.3, bounce_close=100.0)
        pred = parse_strategy("dbl_bot_12")
        self.assertTrue(eval_predicate(pred, closes, i, highs=highs, lows=lows))
        # Extra OHLC kwargs path used by eval_predicate / live.
        self.assertTrue(pred(closes, i, highs=highs, lows=lows))

    def test_single_low_is_false(self):
        closes, highs, lows, k, j1, j2, i = self._w_tape()
        # Wipe the second trough — only one swing low remains.
        lows[j2], closes[j2], highs[j2] = 99.0, 100.0, 101.0
        pred = parse_strategy("dbl_bot_12")
        self.assertFalse(eval_predicate(pred, closes, i, highs=highs, lows=lows))

    def test_much_lower_second_low_is_false(self):
        closes, highs, lows, k, j1, j2, i = self._w_tape(second_low=80.0, bounce_close=100.0)
        pred = parse_strategy("dbl_bot_12")
        self.assertFalse(eval_predicate(pred, closes, i, highs=highs, lows=lows))

    def test_close_still_at_second_low_is_not_recovered(self):
        closes, highs, lows, k, j1, j2, i_conf = self._w_tape()
        # After confirmation, give back the bounce: close sits on the second low.
        i = i_conf + 5
        lo2 = lows[j2]
        closes[i] = lo2
        highs[i] = lo2 + 1.0
        lows[i] = lo2
        pred = parse_strategy("dbl_bot_12")
        self.assertFalse(eval_predicate(pred, closes, i, highs=highs, lows=lows))

    def test_dbl_bot_refuses_close_as_high_proxy(self):
        pred = parse_strategy("dbl_bot_12")
        with self.assertRaises(ValueError) as ctx:
            pred([100.0] * 80)
        self.assertIn("high/low", str(ctx.exception).lower())

    def test_dbl_top_parses_but_is_not_a_universe_long(self):
        k = 12
        j1 = 2 * k
        j2 = j1 + 2 * k + 1
        n = j2 + k + 5
        closes, highs, lows = _flat(n, close=100.0, w=1.0)
        highs[j1], closes[j1], lows[j1] = 110.0, 109.0, 108.0
        highs[j2], closes[j2], lows[j2] = 110.3, 109.3, 108.3
        i = j2 + k
        closes[i], highs[i], lows[i] = 100.0, 101.0, 99.0
        pred = parse_strategy("dbl_top_12")
        self.assertTrue(eval_predicate(pred, closes, i, highs=highs, lows=lows))
        uni = generate_universe()
        self.assertNotIn("dbl_top_12", uni)


class CloseOnlyCompatTests(unittest.TestCase):
    def test_old_dip_24b_lt1pc_still_parses(self):
        pred = parse_strategy("dip_24b_lt1pc")
        # 24-bar return -0.5% is not enough; -2% is.
        base = [100.0] * 25
        shallow = list(base)
        shallow[-1] = 99.5
        self.assertFalse(pred(shallow))
        deep = list(base)
        deep[-1] = 98.0
        self.assertTrue(pred(deep))
        # Extra OHLC kwargs must not break close-only names.
        self.assertTrue(pred(deep, None, highs=deep, lows=deep))

    def test_sma_abv_still_close_only(self):
        pred = parse_strategy("sma_abv_20")
        closes = [float(i) for i in range(1, 40)]
        self.assertTrue(pred(closes))


class UniverseBandTests(unittest.TestCase):
    STRUCTURE_NAMES = (
        "don_hi_24",
        "dip_6b_lt2pc&near_swing_lo_12",
        "dip_12b_lt3pc&don_lo_24",
        "mom_12b_gt3pc&don_hi_24",
        "sma_abv_50&don_hi_24",
        "rsi_14_>50&don_hi_24",
        "dip_6b_lt2pc&don_lo_24",
        "ema_abv_50&don_hi_24",
        "sma_stack_20_50_100&don_hi_24",
        "dbl_bot_12",
        "dbl_bot_12&sma_abv_50",
        "dbl_bot_12&don_lo_24",
        "dbl_bot_12&sma_stack_20_50_100",
        "wt_cross_up_os",
        "wt_cross_up_os&sma_abv_50",
        "wt_cross_up_os&sma_stack_20_50_100",
        "wt_cross_up_os&don_lo_24",
    )
    REFUSED = (
        "head_and_shoulders",
        "h_and_s",
        "flag",
        "triangle",
        "fvg",
        "order_block",
        "engulfing",
        "near_round_100",
        "dbl_top_12",
        "dbl_top_12&sma_abv_50",
        "wt_cross_down_ob",
    )

    def test_universe_stays_in_band_and_adds_structure_ands(self):
        uni = generate_universe()
        self.assertGreaterEqual(len(uni), UNIVERSE_TARGET_MIN)
        self.assertLessEqual(len(uni), UNIVERSE_TARGET_MAX)
        for name in self.STRUCTURE_NAMES:
            self.assertIn(name, uni)
        for name in self.REFUSED:
            self.assertNotIn(name, uni)
        blob = " ".join(uni)
        self.assertNotIn("daily(", blob)
        self.assertNotIn("mfi_", blob)
        # Long-only: no standalone dbl_top longs (combinator has no NOT).
        self.assertFalse(any(p == "dbl_top_12" or p.startswith("dbl_top_") for p in uni))
        # Spaced mom×don_hi lookbacks only — not a cartesian of every combo.
        mom_don = [p for p in uni if "mom_" in p and "don_hi_" in p]
        self.assertEqual(
            mom_don,
            [
                "mom_12b_gt3pc&don_hi_12",
                "mom_12b_gt3pc&don_hi_24",
                "mom_12b_gt3pc&don_hi_36",
                "mom_24b_gt5pc&don_hi_36",
                "mom_6b_gt2pc&don_hi_12",
            ],
        )

    def test_new_universe_names_parse(self):
        closes, highs, lows = _flat(80)
        for name in generate_universe():
            pred = parse_strategy(name)
            eval_predicate(pred, closes, None, highs=highs, lows=lows)


class ComputeSignalOhlsTests(unittest.TestCase):
    def test_compute_signal_threads_highs_lows(self):
        from hedge_fund.signals.momentum import compute_signal

        n = 30
        candles = []
        for i in range(n):
            close = 100.0 if i < n - 1 else 101.0
            high = 100.5 if i < n - 1 else 110.0
            candles.append(
                Candle(ts=i * 300_000, open=100.0, high=high, low=99.5, close=close, volume=1.0)
            )
        sig = compute_signal(candles, "BTC/USDT", "5m", strategy="don_hi_24")
        self.assertEqual(sig.direction, "long")
        # Wick-only: close still below prior high.
        wick = list(candles)
        wick[-1] = Candle(ts=wick[-1].ts, open=100.0, high=110.0, low=99.5, close=100.4, volume=1.0)
        sig2 = compute_signal(wick, "BTC/USDT", "5m", strategy="don_hi_24")
        self.assertEqual(sig2.direction, "flat")

    def test_backtest_runs_structure_and(self):
        from hedge_fund.backtest.strategies import backtest

        closes, highs, lows = [], [], []
        px = 100.0
        for _ in range(90):
            px += 0.25
            closes.append(px)
            highs.append(px + 0.2)
            lows.append(px - 0.2)
        r = backtest(closes, highs, lows, "sma_abv_50&don_hi_24")
        self.assertIsNone(r.error)
        self.assertGreaterEqual(r.trades, 0)


class ProtocolStructureAmendmentTests(unittest.TestCase):
    def test_amendment_2026_09_03_in_protocol(self):
        from pathlib import Path

        text = (Path(__file__).resolve().parents[1] / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-03", text)
        self.assertIn("structure atoms", text.lower())
        self.assertIn("OHLC", text)
        self.assertIn("close-only", text)
        self.assertIn("Paper only", text)
        self.assertIn("don_hi_N", text)
        self.assertIn("highs[i-N:i]", text)

    def test_amendment_2026_09_03_doubles_in_protocol(self):
        from pathlib import Path

        text = (Path(__file__).resolve().parents[1] / "PROTOCOL.md").read_text()
        self.assertIn("double bottom", text.lower())
        self.assertIn("dbl_bot_k", text)
        self.assertIn("HOLD_PCT", text)
        self.assertIn("1.0%", text)
        self.assertIn("1 × ATR", text)
        self.assertIn("mom_*", text)
        self.assertIn("don_hi_*", text)
        self.assertIn("sma_stack", text)
        self.assertIn("not duplicated", text.lower())
        self.assertIn("standalone long", text)


if __name__ == "__main__":
    unittest.main()
