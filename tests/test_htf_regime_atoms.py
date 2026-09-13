"""Causal HTF buyer-regime atoms: no lookahead on 5m resample, parser accepts names."""
from __future__ import annotations

import unittest

import pandas as pd

from hedge_fund.data.binance import Candle
from hedge_fund.data.resample import (
    BARS_5M_PER,
    MultiTimeframeDataset,
    causal_resample_ohlcv,
)
from hedge_fund.signals.dynamic import eval_predicate, parse_strategy
from hedge_fund.signals.htf import (
    BARS_PER_H1,
    BARS_PER_H4,
    completed_htf_closes,
    completed_htf_count,
    htf_close_above_ma,
)
from hedge_fund.trading.constants import (
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    QUAL_N_WINDOWS,
)
from hedge_fund.trading.universe import generate_universe, near_duplicate_key


def _uptrend_5m(n_htf: int, bars_per: int = BARS_PER_H4, start: float = 100.0, step: float = 1.0):
    """Contiguous 5m tape: each completed HTF close is start + (k+1)*step."""
    closes = []
    for k in range(n_htf):
        level = start + (k + 1) * step
        closes.extend([level] * bars_per)
    return closes


def _midnight_5m_df(closes: list[float], start: str = "2024-01-01 00:00:00"):
    idx = pd.date_range(start, periods=len(closes), freq="5min")
    df = pd.DataFrame(
        {
            "open": closes,
            "high": [c + 0.1 for c in closes],
            "low": [c - 0.1 for c in closes],
            "close": closes,
            "volume": [1.0] * len(closes),
        },
        index=idx,
    )
    return df


class FrozenGatesUntouchedTests(unittest.TestCase):
    def test_oos_gates_unchanged(self):
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 8)


class CausalIndexResampleTests(unittest.TestCase):
    def test_bars_per_match_resample_constants(self):
        self.assertEqual(BARS_PER_H4, 48)
        self.assertEqual(BARS_PER_H1, 12)
        self.assertEqual(BARS_PER_H4, BARS_5M_PER["4h"])
        self.assertEqual(BARS_PER_H1, BARS_5M_PER["1h"])

    def test_incomplete_htf_bar_is_omitted(self):
        closes = _uptrend_5m(3)
        # Last close of first 4h is index 47; forming second bar at 48..
        self.assertEqual(completed_htf_count(46, BARS_PER_H4), 0)
        self.assertEqual(completed_htf_count(47, BARS_PER_H4), 1)
        self.assertEqual(completed_htf_count(48, BARS_PER_H4), 1)
        self.assertEqual(completed_htf_count(95, BARS_PER_H4), 2)
        at_first = completed_htf_closes(closes, 47, BARS_PER_H4)
        self.assertEqual(at_first, [closes[47]])
        mid_second = completed_htf_closes(closes, 48, BARS_PER_H4)
        self.assertEqual(mid_second, [closes[47]])

    def test_future_5m_spike_does_not_change_htf_at_i(self):
        closes = _uptrend_5m(30)
        i = BARS_PER_H4 * 25 - 1  # 25 completed 4h bars
        pred = parse_strategy("h4_ema_abv_24")
        before = eval_predicate(pred, closes, i)
        spiked = list(closes)
        spiked[i + 1] = spiked[i] * 10.0
        spiked[-1] = spiked[-1] * 10.0
        after = eval_predicate(pred, spiked, i)
        self.assertEqual(before, after)
        self.assertEqual(
            completed_htf_closes(closes, i, BARS_PER_H4),
            completed_htf_closes(spiked, i, BARS_PER_H4),
        )

    def test_forming_bar_spike_is_not_the_htf_close(self):
        closes = _uptrend_5m(26)
        i = BARS_PER_H4 * 25  # first 5m of the 26th 4h bar
        htf = completed_htf_closes(closes, i, BARS_PER_H4)
        self.assertEqual(len(htf), 25)
        self.assertEqual(htf[-1], closes[BARS_PER_H4 * 25 - 1])
        spiked = list(closes)
        spiked[i] = 1_000_000.0
        self.assertEqual(
            completed_htf_closes(spiked, i, BARS_PER_H4),
            htf,
        )
        self.assertEqual(
            htf_close_above_ma(spiked, i, bars_per=BARS_PER_H4, period=24, kind="ema"),
            htf_close_above_ma(closes, i, bars_per=BARS_PER_H4, period=24, kind="ema"),
        )


class CausalCalendarResampleTests(unittest.TestCase):
    def test_causal_resample_drops_incomplete_and_ignores_future(self):
        closes = _uptrend_5m(4)
        df = _midnight_5m_df(closes)
        # Mid-second 4h: 01 Jan 04:05 — first 4h (00:00–04:00) is complete.
        as_of = df.index[BARS_PER_H4]  # 04:00, first 5m of second 4h
        causal = causal_resample_ohlcv(df, "4h", as_of=as_of)
        self.assertEqual(len(causal), 1)
        self.assertAlmostEqual(causal["close"].iloc[-1], closes[BARS_PER_H4 - 1])

        spiked = df.copy()
        spiked.iloc[-1, spiked.columns.get_loc("close")] = 9_999.0
        causal_spike = causal_resample_ohlcv(spiked, "4h", as_of=as_of)
        self.assertAlmostEqual(causal_spike["close"].iloc[-1], causal["close"].iloc[-1])

    def test_get_aligned_history_does_not_use_full_series_htf(self):
        closes = _uptrend_5m(4)
        closes[-1] = 50_000.0  # last 5m of the 4th 4h — future vs mid-tape
        rows = []
        start = pd.Timestamp("2024-01-01 00:00:00")
        for i, c in enumerate(closes):
            ts = int((start + pd.Timedelta(minutes=5 * i)).timestamp() * 1000)
            rows.append([ts, c, c + 0.1, c - 0.1, c, 1.0])
        ds = MultiTimeframeDataset(rows)
        as_of = pd.Timestamp("2024-01-01 04:00:00")
        hist = ds.get_aligned_history("4h", as_of)
        self.assertEqual(len(hist), 1)
        self.assertNotAlmostEqual(hist["close"].iloc[-1], 50_000.0)

    def test_index_and_midnight_calendar_closes_agree(self):
        closes = _uptrend_5m(6)
        i = BARS_PER_H4 * 5 - 1
        index_htf = completed_htf_closes(closes, i, BARS_PER_H4)
        df = _midnight_5m_df(closes)
        cal = causal_resample_ohlcv(df, "4h", as_of=df.index[i])
        self.assertEqual(list(cal["close"]), index_htf)


class ParseStrategyAcceptsTests(unittest.TestCase):
    def test_named_atoms_parse_and_fire_on_buyer_tape(self):
        closes = _uptrend_5m(60)
        i = len(closes) - 1
        for name in ("h4_ema_abv_24", "h4_sma_abv_50", "h1_ema_abv_24"):
            pred = parse_strategy(name)
            self.assertTrue(eval_predicate(pred, closes, i), msg=name)

    def test_seller_tape_is_flat_not_short(self):
        closes = _uptrend_5m(60, step=-1.0)
        pred = parse_strategy("h4_ema_abv_24")
        self.assertFalse(eval_predicate(pred, closes, len(closes) - 1))

    def test_and_with_5m_dip_needs_both(self):
        # Rising 4h, then a 5m dip at the end.
        closes = _uptrend_5m(60)
        for j in range(1, 13):
            closes[-j] = closes[-13] * (1.0 - 0.004 * j)
        pred = parse_strategy("h4_ema_abv_24&dip_12b_lt2pc")
        self.assertTrue(eval_predicate(pred, closes, len(closes) - 1))
        # Same dip, but smash the completed HTF closes below the EMA.
        sellers = [c * 0.5 for c in closes]
        self.assertFalse(eval_predicate(pred, sellers, len(sellers) - 1))

    def test_wrappers_still_refused(self):
        with self.assertRaises(ValueError):
            parse_strategy("h1(sma_abv_50)")
        with self.assertRaises(ValueError):
            parse_strategy("4h(ema_abv_24)")
        with self.assertRaises(ValueError):
            parse_strategy("daily(sma_abv_50)")

    def test_close_only_no_highs_required(self):
        closes = _uptrend_5m(30)
        pred = parse_strategy("h4_ema_abv_24")
        pred(closes)  # must not raise

    def test_compute_signal_threads_htf(self):
        from hedge_fund.signals.momentum import compute_signal

        closes = _uptrend_5m(30)
        candles = [
            Candle(ts=i, open=c, high=c + 0.1, low=c - 0.1, close=c, volume=1.0)
            for i, c in enumerate(closes)
        ]
        sig = compute_signal(candles, "BTC/USDT", "5m", strategy="h4_ema_abv_24")
        self.assertIn(sig.direction, ("long", "flat"))

    def test_near_duplicate_key_keeps_htf_prefix(self):
        self.assertNotEqual(
            near_duplicate_key("h4_ema_abv_24"),
            near_duplicate_key("ema_abv_24"),
        )
        self.assertEqual(
            near_duplicate_key("h4_ema_abv_24&dip_12b_lt2pc"),
            near_duplicate_key("dip_12b_lt2pc&h4_ema_abv_24"),
        )

    def test_static_universe_does_not_emit_htf_or_wrappers(self):
        uni = generate_universe()
        blob = " ".join(uni)
        self.assertNotIn("h4_ema_abv_", blob)
        self.assertNotIn("h4_sma_abv_", blob)
        self.assertNotIn("h1_ema_abv_", blob)
        self.assertNotIn("daily(", blob)
        self.assertNotIn("h1(", blob)


if __name__ == "__main__":
    unittest.main()
