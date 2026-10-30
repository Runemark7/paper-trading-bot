"""signal_exit engine, daily resampling and the strategy catalog (gate v6)."""
from __future__ import annotations

import unittest
from dataclasses import replace

import numpy as np

from hedge_fund.brokers.paper import SLIPPAGE, TAKER_FEE, Order, PaperBroker
from hedge_fund.library import catalog
from hedge_fund.library.catalog import (
    LIBRARY,
    all_variant_names,
    claim_names,
    grid_params,
    library_trial_count,
    neighbour_names,
    parse_variant,
    variant_name,
)
from hedge_fund.library.daily import DAY_MS, DailyBars, daily_bars
from hedge_fund.library.engine import (
    REASON_END,
    REASON_SIGNAL,
    REASON_STOP,
    Costs,
    MachineState,
    buy_fill,
    run,
    sell_fill,
    step,
    stop_out,
)

DAY0 = 1_700_006_400_000  # 2023-11-15 00:00 UTC


def make_bars(opens, closes=None, highs=None, lows=None, day0=DAY0) -> DailyBars:
    o = np.asarray(opens, dtype=np.float64)
    c = o.copy() if closes is None else np.asarray(closes, dtype=np.float64)
    h = np.maximum(o, c) if highs is None else np.asarray(highs, dtype=np.float64)
    lo = np.minimum(o, c) if lows is None else np.asarray(lows, dtype=np.float64)
    ts = day0 + np.arange(o.shape[0], dtype=np.int64) * DAY_MS
    return DailyBars(ts=ts, open=o, high=h, low=lo, close=c)


def random_walk_bars(n: int = 900, seed: int = 7) -> DailyBars:
    rng = np.random.default_rng(seed)
    close = 20_000.0 * np.exp(np.cumsum(rng.normal(0.001, 0.035, n)))
    opens = np.concatenate(([close[0]], close[:-1])) * (1 + rng.normal(0, 0.002, n))
    high = np.maximum(opens, close) * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = np.minimum(opens, close) * (1 - np.abs(rng.normal(0, 0.01, n)))
    return make_bars(opens, close, high, low)


class DailyResampleTests(unittest.TestCase):
    def _five_minute(self, days: int, extra: int = 1):
        n = days * 288 + extra
        ts = DAY0 + np.arange(n, dtype=np.int64) * 300_000
        price = 100.0 + np.arange(n, dtype=np.float64)
        return ts, price

    def test_ohlc_per_utc_day(self):
        ts, price = self._five_minute(2)
        bars = daily_bars(ts, price, price + 0.5, price - 0.5, price + 0.25)
        self.assertEqual(len(bars), 2)
        self.assertEqual(int(bars.ts[0]), DAY0)
        self.assertEqual(float(bars.open[0]), 100.0)
        self.assertEqual(float(bars.close[0]), 100.0 + 287 + 0.25)
        self.assertEqual(float(bars.high[1]), 100.0 + 575 + 0.5)
        self.assertEqual(float(bars.low[1]), 100.0 + 288 - 0.5)

    def test_forming_day_is_dropped(self):
        ts, price = self._five_minute(2, extra=0)
        bars = daily_bars(ts, price, price, price, price)
        # Day 2 has no later bar yet, so it has not closed.
        self.assertEqual(len(bars), 1)
        ts2, price2 = self._five_minute(2, extra=150)
        self.assertEqual(len(daily_bars(ts2, price2, price2, price2, price2)), 2)

    def test_forming_bars_never_change_closed_days(self):
        ts, price = self._five_minute(5, extra=1)
        full = daily_bars(ts, price, price, price, price)
        later = np.concatenate([ts, ts[-1] + 300_000 * np.arange(1, 100)])
        lp = np.concatenate([price, np.full(99, 1e9)])
        grown = daily_bars(later, lp, lp, lp, lp)
        np.testing.assert_array_equal(grown.close[: len(full)], full.close)

    def test_unsorted_duplicates_keep_last(self):
        ts = np.asarray([DAY0 + DAY_MS, DAY0, DAY0, DAY0 + 2 * DAY_MS], dtype=np.int64)
        px = np.asarray([2.0, 1.0, 1.5, 3.0])
        bars = daily_bars(ts, px, px, px, px)
        self.assertEqual(len(bars), 2)
        self.assertEqual(float(bars.open[0]), 1.5)


class NoLookaheadTests(unittest.TestCase):
    def test_signal_on_day_t_fills_at_open_of_t_plus_1(self):
        opens = [100, 101, 102, 103, 110, 120, 130]
        bars = make_bars(opens, closes=[o + 0.5 for o in opens])
        signal = np.zeros(7, dtype=bool)
        signal[3:] = True  # turns on at the close of day 3
        res = run(bars, signal, start=0, end=7)
        self.assertEqual(len(res.trades), 1)
        trade = res.trades[0]
        self.assertEqual(trade.entry_ts, int(bars.ts[4]))
        self.assertAlmostEqual(trade.entry_price, 110 * (1 + SLIPPAGE))

    def test_off_signal_exits_at_next_open(self):
        opens = [100, 100, 105, 110, 90, 80]
        bars = make_bars(opens)
        signal = np.asarray([True, True, True, False, False, False])
        res = run(bars, signal, start=1, end=6)
        trade = res.trades[0]
        self.assertEqual(trade.entry_ts, int(bars.ts[1]))
        self.assertEqual(trade.exit_ts, int(bars.ts[4]))
        self.assertAlmostEqual(trade.exit_price, 90 * (1 - SLIPPAGE))
        self.assertEqual(trade.reason, REASON_SIGNAL)

    def test_every_library_signal_is_causal(self):
        bars = random_walk_bars()
        cuts = (260, 401, 555, 700)
        for strategy in LIBRARY:
            for params in grid_params(strategy):
                full = strategy.signal(bars, params)
                self.assertEqual(full.shape[0], len(bars))
                for cut in cuts:
                    prefix = DailyBars(
                        ts=bars.ts[:cut], open=bars.open[:cut], high=bars.high[:cut],
                        low=bars.low[:cut], close=bars.close[:cut],
                    )
                    np.testing.assert_array_equal(
                        strategy.signal(prefix, params), full[:cut],
                        err_msg=f"{variant_name(strategy, params)} looked ahead at cut {cut}",
                    )

    def test_future_close_cannot_flip_todays_decision(self):
        bars = random_walk_bars(400)
        strategy = catalog.strategy_by_key("sma200")
        base = strategy.signal(bars, strategy.center)
        shocked = DailyBars(
            ts=bars.ts, open=bars.open.copy(), high=bars.high.copy(),
            low=bars.low.copy(), close=bars.close.copy(),
        )
        shocked.close[300:] *= 0.01
        np.testing.assert_array_equal(strategy.signal(shocked, strategy.center)[:300], base[:300])


class FlipTests(unittest.TestCase):
    def test_enter_and_exit_on_each_flip(self):
        bars = make_bars([100 + i for i in range(9)])
        signal = np.asarray([False, True, True, False, False, True, False, False, False])
        res = run(bars, signal, start=1, end=9)
        self.assertEqual(len(res.trades), 2)
        first, second = res.trades
        self.assertEqual((first.entry_ts, first.exit_ts), (int(bars.ts[2]), int(bars.ts[4])))
        self.assertEqual((second.entry_ts, second.exit_ts), (int(bars.ts[6]), int(bars.ts[7])))
        self.assertTrue(all(t.reason == REASON_SIGNAL for t in res.trades))

    def test_fresh_book_with_signal_on_buys_first_open(self):
        bars = make_bars([100, 101, 102])
        res = run(bars, np.ones(3, dtype=bool), start=1, end=3)
        self.assertEqual(res.trades[0].entry_ts, int(bars.ts[1]))
        self.assertEqual(res.trades[0].reason, REASON_END)

    def test_no_pyramiding_and_no_take_profit(self):
        opens = [100.0 * 1.2 ** i for i in range(12)]  # +790% while the signal stays on
        bars = make_bars(opens)
        res = run(bars, np.ones(12, dtype=bool), start=1, end=12)
        self.assertEqual(len(res.trades), 1)
        self.assertEqual(res.trades[0].reason, REASON_END)
        self.assertEqual(res.days_long, 11)

    def test_state_machine(self):
        st = MachineState()
        self.assertEqual(step(st, True), "enter")
        self.assertIsNone(step(st, True))
        self.assertEqual(step(st, False), "exit")
        self.assertIsNone(step(st, False))


class SizingTests(unittest.TestCase):
    def test_all_in_with_fees_matches_paper_broker(self):
        costs = Costs()
        qty, fill, fee = buy_fill(10_000.0, 50_000.0, costs)
        self.assertAlmostEqual(fill, 50_000.0 * (1 + SLIPPAGE))
        self.assertAlmostEqual(qty * fill + fee, 10_000.0, places=6)
        self.assertAlmostEqual(fee, qty * fill * TAKER_FEE)
        broker = PaperBroker(cash=10_000.0)
        broker.place_order(Order("BTC/USDT", "buy", qty, 50_000.0, stop_loss=0.0), market_price=50_000.0)
        self.assertAlmostEqual(broker.cash(), 0.0, places=6)
        out_fill, out_fee, proceeds = sell_fill(qty, 60_000.0, costs)
        broker.close_lot(broker.lots[0], 60_000.0)
        self.assertAlmostEqual(broker.cash(), proceeds, places=6)
        self.assertAlmostEqual(out_fill, 60_000.0 * (1 - SLIPPAGE))
        self.assertAlmostEqual(out_fee, qty * out_fill * TAKER_FEE)

    def test_backtest_round_trip_pays_both_fills(self):
        bars = make_bars([100.0, 100.0, 120.0, 120.0])
        res = run(bars, np.asarray([True, True, False, False]), start=0, end=4)
        qty = 10_000.0 / (100.0 * (1 + SLIPPAGE) * (1 + TAKER_FEE))
        expected = qty * 120.0 * (1 - SLIPPAGE) * (1 - TAKER_FEE)
        # The signal of day 0 enters at day 1's open; day 1's signal holds;
        # day 2's off signal sells at day 3's open.
        self.assertEqual(len(res.trades), 1)
        self.assertAlmostEqual(res.equity[-1], expected, places=6)
        trade = res.trades[0]
        self.assertAlmostEqual(trade.pnl, expected - 10_000.0, places=6)
        self.assertAlmostEqual(res.fees, trade.entry_fee + trade.exit_fee, places=9)

    def test_equity_is_marked_daily(self):
        bars = make_bars([100.0, 100.0, 100.0], closes=[100.0, 110.0, 90.0])
        res = run(bars, np.ones(3, dtype=bool), start=1, end=3)
        self.assertEqual(res.equity.shape[0], 3)
        qty = res.trades[0].qty
        self.assertAlmostEqual(res.equity[1], qty * 110.0, places=6)


class SafetyStopTests(unittest.TestCase):
    def test_stop_fills_at_stop_then_locks_until_signal_off(self):
        opens = [100, 100, 100, 95, 95, 95, 95, 96]
        lows = [100, 100, 88, 94, 94, 94, 94, 95]
        bars = make_bars(opens, lows=lows, highs=[o + 1 for o in opens])
        signal = np.asarray([True, True, True, True, False, True, True, True])
        res = run(bars, signal, start=1, end=8, stop_frac=0.10)
        stop_trade = res.trades[0]
        self.assertEqual(stop_trade.reason, REASON_STOP)
        # Day 2's low breaches. The fill is day 3's open, not the stop price.
        self.assertAlmostEqual(stop_trade.exit_price, 95 * (1 - SLIPPAGE))
        self.assertEqual(stop_trade.exit_ts, int(bars.ts[3]))
        # Signal still on at days 2-3: no re-entry. Off at day 4 re-arms;
        # on at day 5 enters at day 6's open.
        second = res.trades[1]
        self.assertEqual(second.entry_ts, int(bars.ts[6]))
        self.assertEqual(second.reason, REASON_END)

    def test_gap_below_stop_fills_at_open(self):
        bars = make_bars([100, 100, 70, 70], lows=[100, 100, 65, 65])
        res = run(bars, np.ones(4, dtype=bool), start=1, end=4, stop_frac=0.15)
        self.assertEqual(res.trades[0].reason, REASON_STOP)
        self.assertAlmostEqual(res.trades[0].exit_price, 70 * (1 - SLIPPAGE))

    def test_stop_off_by_default(self):
        from hedge_fund.trading.constants import LIBRARY_STOP_FRAC

        self.assertEqual(LIBRARY_STOP_FRAC, 0.20)
        self.assertTrue(all(s.stop_frac == LIBRARY_STOP_FRAC for s in LIBRARY))
        bars = make_bars([100, 100, 40, 40], lows=[100, 100, 30, 30])
        # The engine default is still off, so a caller that omits the stop
        # does not invent one. The library passes its pre-registered 20%.
        res = run(bars, np.ones(4, dtype=bool), start=1, end=4)
        self.assertEqual([t.reason for t in res.trades], [REASON_END])
        stopped = run(bars, np.ones(4, dtype=bool), start=1, end=4, stop_frac=LIBRARY_STOP_FRAC)
        self.assertEqual(stopped.trades[0].reason, REASON_STOP)
        self.assertEqual(stopped.trades[0].exit_ts, int(bars.ts[3]))
        self.assertAlmostEqual(stopped.trades[0].exit_price, 40 * (1 - SLIPPAGE))

    def test_fresh_book_enters_when_the_signal_is_already_on(self):
        bars = make_bars([100, 110, 120, 130])
        signal = np.ones(4, dtype=bool)
        res = run(bars, signal, start=1, end=4)
        self.assertEqual(len(res.trades), 1)
        self.assertEqual(res.trades[0].entry_ts, int(bars.ts[1]))
        self.assertAlmostEqual(res.trades[0].entry_price, 110 * (1 + SLIPPAGE))

    def test_stop_out_state(self):
        st = MachineState(long=True)
        stop_out(st)
        self.assertEqual((st.long, st.lockout), (False, True))
        self.assertIsNone(step(st, True))
        self.assertIsNone(step(st, False))
        self.assertEqual(step(st, True), "enter")


class CatalogTests(unittest.TestCase):
    def test_trial_count_is_every_grid_point(self):
        expected = sum(1 + sum(len(p.neighbours) for p in s.params) for s in LIBRARY)
        self.assertEqual(len(set(all_variant_names())), len(all_variant_names()))
        self.assertEqual(library_trial_count(), expected)
        self.assertGreater(library_trial_count(), len(LIBRARY))

    def test_cited_strategies_keep_the_original_fifteen_first(self):
        original = (
            "sma200", "sma5x150", "sma100", "golden_cross", "ema20x50",
            "faber10m", "tsmom12m", "tsmom6m", "tsmom3m", "tsmom1w",
            "detzel2w", "bll_trb50", "turtle55", "rsi2", "macd",
        )
        self.assertEqual(tuple(s.key for s in LIBRARY[:15]), original)
        # Batch 2 is appended. The lane cap is still 2 per core; the
        # catalog is allowed to be larger than one claim.
        self.assertGreaterEqual(len(LIBRARY), len(original) + 10)
        self.assertLessEqual(len(LIBRARY), 40)
        for s in LIBRARY:
            c = s.citation
            for text in (c.authors, c.title, c.venue, c.tested_rule, c.implemented_rule, c.differences):
                self.assertTrue(text and text.strip(), f"{s.key} citation incomplete")
            if c.peer_reviewed:
                self.assertTrue(c.link.startswith("https://doi.org/"), s.key)
                self.assertGreater(c.year, 1900)
            else:
                self.assertIn("practitioner", c.short().lower())

    def test_names_round_trip_and_are_safe(self):
        for name in all_variant_names():
            strategy, params = parse_variant(name)
            self.assertEqual(variant_name(strategy, params), name)
            for ch in "/:& ":
                self.assertNotIn(ch, name)
        self.assertEqual(claim_names(), [f"lib.{s.key}" for s in LIBRARY])
        with self.assertRaises(ValueError):
            parse_variant("lib.sma200.1_200")  # canonical spelling is lib.sma200
        with self.assertRaises(ValueError):
            parse_variant("lib.nope")
        with self.assertRaises(ValueError):
            parse_variant("sma_abv_200")

    def test_neighbours_differ_in_one_parameter(self):
        for name in claim_names():
            strategy, center = parse_variant(name)
            nbs = neighbour_names(name)
            self.assertEqual(len(nbs), sum(len(p.neighbours) for p in strategy.params))
            for nb in nbs:
                _s, params = parse_variant(nb)
                self.assertEqual(sum(1 for k in params if params[k] != center[k]), 1)

    def test_stop_is_a_declared_parameter(self):
        strategy = catalog.strategy_by_key("sma200")
        stopped = replace(strategy, stop_frac=0.15)
        bars = make_bars([100, 100, 80, 80], lows=[100, 100, 80, 80])
        res = run(bars, np.ones(4, dtype=bool), start=1, end=4, stop_frac=stopped.stop_frac)
        self.assertEqual(res.trades[0].reason, REASON_STOP)


if __name__ == "__main__":
    unittest.main()
