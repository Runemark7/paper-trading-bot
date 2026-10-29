"""The live paper cycle uses the same signal_exit state machine as the backtest."""
from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from hedge_fund.brokers.paper import SLIPPAGE, TAKER_FEE, PaperBroker
from hedge_fund.library.catalog import parse_variant
from hedge_fund.library.daily import DAY_MS, DailyBars
from hedge_fund.library.engine import Costs, buy_fill, sell_fill, stop_price
from hedge_fund.library.live import account_path, library_stop_check, run_library_account
from hedge_fund.trading.constants import PAPER_START_CASH
from hedge_fund.trading.store import TradeStore

TS0 = 1_704_067_200_000  # 2024-01-01 00:00 UTC
COSTS = Costs(taker_fee=TAKER_FEE, slippage=SLIPPAGE)


def _bars(n: int, price: float = 100.0) -> DailyBars:
    ts = TS0 + np.arange(n, dtype=np.int64) * DAY_MS
    px = np.full(n, price, dtype=np.float64)
    return DailyBars(ts=ts, open=px.copy(), high=px * 1.01, low=px * 0.99, close=px.copy())


class _Book(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = patch.dict(os.environ, {"PAPER_STATE": str(self.root)})
        self._env.start()
        self._program = {"on": [True], "stop_frac": 0.0}
        base, self.params = parse_variant("lib.sma200")

        def signal(bars, _params):
            on = self._program["on"]
            out = np.zeros(len(bars), dtype=bool)
            out[: min(len(bars), len(on))] = on[: len(bars)]
            return out

        self.strategy = replace(base, signal=signal, stop_frac=0.0)
        self._parse = patch(
            "hedge_fund.library.live.parse_variant",
            side_effect=lambda _name: (replace(self.strategy, stop_frac=self._program["stop_frac"]), self.params),
        )
        self._parse.start()

    def tearDown(self):
        self._parse.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _run(self, n: int, price: float, bars: DailyBars | None = None):
        return run_library_account(
            "lib.sma200", self.root, bars=bars if bars is not None else _bars(n),
            price=price, now="2026-10-28T18:00:00+00:00",
        )

    def _broker(self) -> PaperBroker:
        store = TradeStore(account_path(self.root, "lib.sma200"))
        try:
            saved = store.load_account_state()
            broker = PaperBroker(cash=PAPER_START_CASH, taker_fee=TAKER_FEE, slippage=SLIPPAGE)
            broker.restore_state(saved["broker"])
            return broker
        finally:
            store.close()

    def _saved(self) -> dict:
        store = TradeStore(account_path(self.root, "lib.sma200"))
        try:
            return store.load_account_state()
        finally:
            store.close()


class SignalExitLiveTests(_Book):
    def test_all_in_entry_uses_the_run_price_and_pays_the_paper_fee(self):
        # The signal is day 0's close. The first cycle after that close fills
        # at the cycle price, the live counterpart of the next open.
        out = self._run(1, 105.0)
        self.assertTrue(out["long"])
        self.assertEqual(out["actions"][0]["action"], "ENTER")
        qty, fill, fee = buy_fill(PAPER_START_CASH, 105.0, COSTS)
        broker = self._broker()
        self.assertEqual(len(broker.lots), 1)
        lot = broker.lots[0]
        self.assertAlmostEqual(lot.quantity, qty, places=6)
        self.assertAlmostEqual(lot.entry_price, fill, places=6)
        self.assertAlmostEqual(lot.entry_fee, fee, places=6)
        self.assertAlmostEqual(broker.cash(), 0.0, delta=1e-4)
        self.assertEqual(lot.stop_loss, 0.0)
        # A second cycle with no new day, at double the price, does not
        # take profit and does not add a lot.
        held = self._run(1, 210.0)
        self.assertTrue(held["long"])
        self.assertEqual(held["actions"], [])
        self.assertEqual(len(self._broker().lots), 1)
        self.assertAlmostEqual(self._broker().lots[0].quantity, qty, places=6)

    def test_signal_off_sells_everything_at_the_next_run(self):
        self._run(1, 100.0)
        qty, entry, entry_fee = buy_fill(PAPER_START_CASH, 100.0, COSTS)
        self._program["on"] = [True, False]
        out = self._run(2, 100.0)
        self.assertFalse(out["long"])
        self.assertEqual(out["actions"][0]["action"], "CLOSE_SIGNAL")
        self.assertEqual(out["actions"][0]["reason"], "signal")
        fill, exit_fee, proceeds = sell_fill(qty, 100.0, COSTS)
        broker = self._broker()
        self.assertEqual(broker.lots, [])
        self.assertAlmostEqual(broker.cash(), proceeds, places=4)
        self.assertLess(broker.cash(), PAPER_START_CASH)
        self.assertGreater(entry_fee + exit_fee, 0.0)
        self.assertAlmostEqual(fill, 100.0 * (1.0 - SLIPPAGE), places=6)
        store = TradeStore(account_path(self.root, "lib.sma200"))
        try:
            closed = store.trades()
        finally:
            store.close()
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["exit_reason"], "signal")
        # Nothing new closed: hold flat.
        again = self._run(2, 100.0)
        self.assertEqual(again["actions"], [])
        self.assertFalse(again["long"])

    def test_missed_days_replay_to_one_net_decision(self):
        self._program["on"] = [True, False]
        flat = self._run(2, 100.0)
        self.assertFalse(flat["long"])
        self.assertEqual(flat["actions"], [])
        self.assertEqual(self._broker().lots, [])
        self.assertAlmostEqual(self._broker().cash(), PAPER_START_CASH, places=6)

        self._program["on"] = [True, False, True]
        # A fresh account: the intermediate round-trip is not traded.
        other = tempfile.TemporaryDirectory()
        try:
            root = Path(other.name)
            out = run_library_account("lib.sma200", root, bars=_bars(3), price=100.0, now="2026-10-28T18:00:00+00:00")
            self.assertTrue(out["long"])
            self.assertEqual([a["action"] for a in out["actions"]], ["ENTER"])
            store = TradeStore(account_path(root, "lib.sma200"))
            try:
                self.assertEqual(len(store.all_trades()), 1)
            finally:
                store.close()
        finally:
            other.cleanup()

    def test_safety_stop_locks_reentry_until_the_signal_goes_off(self):
        self._program["stop_frac"] = 0.10
        entered = self._run(1, 100.0)
        self.assertEqual(entered["actions"][0]["action"], "ENTER")
        entry = self._broker().lots[0].entry_price
        stop = stop_price(entry, 0.10)
        self.assertIsNotNone(stop)
        # Above the stop, even at a price that would be a take-profit
        # for the 5m engine, the position stays.
        held = self._run(1, 250.0)
        self.assertTrue(held["long"])
        self.assertEqual(held["actions"], [])
        # An intraday price through the stop is not the next daily open.
        intraday = self._run(1, stop - 1.0)
        self.assertTrue(intraday["long"])
        self.assertEqual(intraday["actions"], [])
        self.assertEqual(len(self._broker().lots), 1)
        # The run that first includes the breach day sells at that run's price.
        self._program["on"] = [True, True]
        breach = _bars(2)
        low = breach.low.copy()
        low[1] = stop - 1.0
        breach = DailyBars(ts=breach.ts, open=breach.open, high=breach.high, low=low, close=breach.close)
        stopped = self._run(2, 100.0, bars=breach)
        self.assertFalse(stopped["long"])
        self.assertEqual(stopped["actions"][0]["reason"], "safety_stop")
        self.assertEqual(self._broker().lots, [])
        saved = self._saved()["library"]["machine"]
        self.assertTrue(saved["lockout"])
        self.assertFalse(saved["long"])
        # The signal is still on. A new day does not buy back in.
        self._program["on"] = [True, True]
        locked = self._run(2, 100.0)
        self.assertFalse(locked["long"])
        self.assertEqual(locked["actions"], [])
        self.assertTrue(self._saved()["library"]["machine"]["lockout"])
        # One close with the signal off clears the lock. The next on-close enters.
        self._program["on"] = [True, True, False]
        cleared = self._run(3, 100.0)
        self.assertFalse(cleared["long"])
        self.assertFalse(self._saved()["library"]["machine"]["lockout"])
        self._program["on"] = [True, True, False, True]
        again = self._run(4, 100.0)
        self.assertTrue(again["long"])
        self.assertEqual(again["actions"][0]["action"], "ENTER")

    def test_heartbeat_checks_the_safety_stop_and_not_the_two_to_one_rule(self):
        self._program["stop_frac"] = 0.10
        self._run(1, 100.0)
        entry = self._broker().lots[0].entry_price
        stop = stop_price(entry, 0.10)
        prices = {"BTC/USDT": entry * 2.0, "ETH/USDT": 3000.0}

        def fetch(symbol):
            return prices[symbol]

        with patch("hedge_fund.trading.heartbeat.CcxtSource") as source, patch(
            "hedge_fund.trading.loop.TradingLoop._manage_open_positions",
            side_effect=AssertionError("library accounts must not use the 2:1 take-profit"),
        ):
            source.return_value.fetch_price.side_effect = fetch
            from hedge_fund.trading.heartbeat import heartbeat_once

            self.assertEqual(heartbeat_once(), 0)
            self.assertEqual(len(self._broker().lots), 1)
            prices["BTC/USDT"] = stop - 0.5
            # The heartbeat does not sell on an intraday print.
            self.assertEqual(heartbeat_once(), 0)
        self.assertEqual(len(self._broker().lots), 1)
        self.assertFalse(self._saved()["library"]["machine"]["lockout"])
        self._program["on"] = [True, True]
        breach = _bars(2)
        low = breach.low.copy()
        low[1] = stop - 1.0
        breach = DailyBars(ts=breach.ts, open=breach.open, high=breach.high, low=low, close=breach.close)
        stopped = self._run(2, 90.0, bars=breach)
        self.assertFalse(stopped["long"])
        self.assertEqual(self._broker().lots, [])
        self.assertTrue(self._saved()["library"]["machine"]["lockout"])
        store = TradeStore(account_path(self.root, "lib.sma200"))
        try:
            closed = store.trades()
        finally:
            store.close()
        self.assertEqual(closed[0]["exit_reason"], "safety_stop")
        # The direct check never closes a lot. The daily cycle owns the stop.
        self.assertEqual(library_stop_check("lib.sma200", self.root, stop - 1.0, "2026-10-28T19:00:00+00:00"), 0)


class IsolatedCycleTests(unittest.TestCase):
    def test_mixed_pool_routes_library_names_and_keeps_going_when_they_fail(self):
        from hedge_fund.trading.loop import TradingLoop
        from hedge_fund.trading.run_isolated import main

        seen: list[str] = []

        def run_cycle(self, symbols):
            seen.append(self.strategy)
            return []

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}), patch(
                "hedge_fund.trading.run_isolated.active_strategies",
                return_value=["lib.sma200", "sma_stack"],
            ), patch(
                "hedge_fund.trading.run_isolated._fetch_snapshot",
                return_value=type("M", (), {"fetch_price": staticmethod(lambda _s: 100.0)})(),
            ), patch.object(TradingLoop, "run_cycle", run_cycle), patch(
                "hedge_fund.library.live.live_daily_bars",
                side_effect=RuntimeError("tape unavailable"),
            ), patch("sys.argv", ["run_isolated", "--cycles", "1", "--state", str(root)]):
                main()
        self.assertEqual(seen, ["sma_stack"])

    def test_library_champion_is_traded_by_the_cycle(self):
        from hedge_fund.trading.loop import TradingLoop
        from hedge_fund.trading.run_isolated import main

        bars = _bars(1)
        base, params = parse_variant("lib.sma200")

        def signal(got, _params):
            return np.ones(len(got), dtype=bool)

        strategy = replace(base, signal=signal, stop_frac=0.0)
        seen: list[str] = []

        def run_cycle(self, symbols):
            seen.append(self.strategy)
            return []

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}), patch(
                "hedge_fund.trading.run_isolated.active_strategies",
                return_value=["lib.sma200", "sma_stack"],
            ), patch(
                "hedge_fund.trading.run_isolated._fetch_snapshot",
                return_value=type("M", (), {"fetch_price": staticmethod(lambda _s: 100.0)})(),
            ), patch.object(TradingLoop, "run_cycle", run_cycle), patch(
                "hedge_fund.library.live.live_daily_bars",
                return_value=bars,
            ), patch(
                "hedge_fund.library.live.parse_variant",
                return_value=(strategy, params),
            ), patch("sys.argv", ["run_isolated", "--cycles", "1", "--state", str(root)]):
                main()
            store = TradeStore(account_path(root, "lib.sma200"))
            try:
                saved = store.load_account_state()
                open_rows = store.all_trades()
            finally:
                store.close()
        self.assertEqual(seen, ["sma_stack"])
        broker = PaperBroker(cash=PAPER_START_CASH, taker_fee=TAKER_FEE, slippage=SLIPPAGE)
        broker.restore_state(saved["broker"])
        self.assertEqual(len(broker.lots), 1)
        self.assertAlmostEqual(broker.cash(), 0.0, delta=1e-4)
        self.assertEqual(len(open_rows), 1)
        self.assertEqual(saved["library"]["engine"], "signal_exit")
        self.assertTrue(saved["library"]["last_signal"])


class UnwarmedDayTests(unittest.TestCase):
    def test_a_missing_lookback_does_not_flatten(self):
        """A new day that is not warm is skipped, even when its signal is off."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, params = parse_variant("lib.sma200")

            def signal(bars, _params):
                out = np.zeros(len(bars), dtype=bool)
                if len(bars):
                    out[0] = True
                return out

            def ready(_strategy, bars, _params):
                out = np.ones(len(bars), dtype=bool)
                if len(bars) >= 2:
                    out[1:] = False
                return out

            strategy = replace(base, signal=signal, stop_frac=0.0)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}), patch(
                "hedge_fund.library.live.parse_variant",
                return_value=(strategy, params),
            ), patch("hedge_fund.library.live._position_ready", side_effect=ready):
                first = run_library_account(
                    "lib.sma200", root, bars=_bars(1), price=100.0, now="2026-10-28T18:00:00+00:00",
                )
                self.assertTrue(first["long"])
                second = run_library_account(
                    "lib.sma200", root, bars=_bars(2), price=100.0, now="2026-10-28T19:00:00+00:00",
                )
            self.assertTrue(second["long"])
            self.assertEqual(second["actions"], [])


if __name__ == "__main__":
    unittest.main()
