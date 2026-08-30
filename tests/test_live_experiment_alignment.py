"""Alignment tests: PROTOCOL amendment, dashboard rules, isolated runner.

Locks the 2026-08-30 contract: one paper experiment (TradingLoop cycle),
not a second 0.60 / 2.5% book in run_isolated, and dashboard copy that
matches live constants.
"""
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[1]


class ProtocolAmendmentTests(unittest.TestCase):
    def test_amendment_2026_08_30(self):
        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("can an LLM agent (Hermes) state probabilities", text)
        self.assertNotIn("| _(none yet)_ | |", text)
        self.assertIn("2026-08-30", text)
        self.assertIn("Amendment 2026-08-30", text)
        self.assertIn("graduated paper", text.lower())
        self.assertIn("TradingLoop.run_cycle", text)
        self.assertIn("baseline=None", text)
        self.assertIn("not an LLM", text)
        self.assertIn("Paper only", text)
        # Original sizing/fees language kept for history
        self.assertIn("1 % fixed-fractional risk per trade", text)
        self.assertIn("Taker fee (0.1 %)", text)


class IsolatedRunnerTests(unittest.TestCase):
    def test_no_second_hardcoded_book(self):
        src = (REPO / "hedge_fund" / "trading" / "run_isolated.py").read_text()
        self.assertIn("run_cycle", src)
        self.assertNotIn("prob = 0.60", src)
        self.assertNotIn("entry * (1 - 0.025)", src)
        tree = ast.parse(src)
        calls = [
            n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute)
        ]
        self.assertIn("run_cycle", calls)

    def test_main_delegates_to_trading_loop(self):
        from hedge_fund.trading import run_isolated as ri

        fake_loop = MagicMock()
        fake_loop.run_cycle.return_value = []
        fake_broker = MagicMock()
        fake_broker.to_state.return_value = {}
        fake_loop.broker = fake_broker
        fake_risk = MagicMock()
        fake_risk.peak_equity = 10_000.0

        store = MagicMock()
        store.load_account_state.return_value = None

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ri, "CcxtSource") as src_cls, \
                 patch.object(ri, "load_pool", return_value={"champions": [{"name": "sma_stack"}]}), \
                 patch.object(ri, "TradeStore", return_value=store), \
                 patch.object(ri, "CalibrationStore"), \
                 patch.object(ri, "PaperBroker", return_value=fake_broker), \
                 patch.object(ri, "RiskManager", return_value=fake_risk), \
                 patch.object(ri, "TradingLoop", return_value=fake_loop) as loop_cls, \
                 patch("sys.argv", ["run_isolated", "--cycles", "1", "--state", tmp]):
                src = src_cls.return_value
                src.fetch_price.return_value = 100.0
                src.fetch_klines.return_value = [object()]
                ri.main()

            loop_cls.assert_called()
            kwargs = loop_cls.call_args.kwargs
            self.assertEqual(kwargs.get("strategy"), "sma_stack")
            self.assertIsNone(kwargs.get("regime"))
            fake_loop.run_cycle.assert_called_with(ri.SYMBOLS)
            store.save_account_state.assert_called()


class DashboardRulesTests(unittest.TestCase):
    def test_rules_match_live_constants_not_old_blurb(self):
        from hedge_fund.dashboard.report import strategy_rules_section
        from hedge_fund.risk.managed import RISK_FRAC, MAX_OPEN_RISK_FRAC, MAX_DRAWDOWN
        from hedge_fund.trading.loop import ATR_STOP_MULT, ATR_PERIOD, STOP_FLOOR_FRAC
        from hedge_fund.trading.constants import GRADUATED_PAPER, TRADE_EVALUATION_LIMIT

        html = strategy_rules_section()
        self.assertNotIn("2.5% below entry (hard)", html)
        self.assertNotIn("Longs only in RISK_ON / NEUTRAL", html)
        self.assertNotIn("LIVE (A/B winner)", html)
        self.assertIn("regime=None", html)
        self.assertIn("graduated paper", html.lower())
        self.assertIn("baseline=None", html)
        self.assertIn(f"{ATR_STOP_MULT:.1f}× ATR({ATR_PERIOD})", html)
        self.assertIn(f"{STOP_FLOOR_FRAC:.1%}", html)
        self.assertIn(f"{RISK_FRAC:.0%}", html)
        self.assertIn(f"{MAX_OPEN_RISK_FRAC:.0%}", html)
        self.assertIn(f"{MAX_DRAWDOWN:.0%}", html)
        self.assertIn(str(TRADE_EVALUATION_LIMIT), html)
        self.assertIn("TradingLoop.run_cycle", html)
        self.assertIn(GRADUATED_PAPER, html)
        self.assertNotIn("READY_FOR_LIVE", html)


class SharedCycleTests(unittest.TestCase):
    def test_run_cycle_uses_atr_stop_and_calibration_not_fixed_book(self):
        from hedge_fund.backtest.strategies import atr as atr_fn
        from hedge_fund.brokers.paper import PaperBroker, TAKER_FEE
        from hedge_fund.calibration import CalibrationStore
        from hedge_fund.data.binance import Candle
        from hedge_fund.risk.managed import RiskManager
        from hedge_fund.signals.momentum import Features, Signal
        from hedge_fund.trading.loop import (
            ATR_PERIOD,
            ATR_STOP_MULT,
            STOP_CAP_FRAC,
            STOP_FLOOR_FRAC,
            TradingLoop,
        )
        from hedge_fund.trading.store import TradeStore

        entry = 100_000.0
        candles = []
        for i in range(40):
            c = 90_000.0 + i * 200
            candles.append(Candle(ts=i, open=c, high=c + 800, low=c - 800, close=c, volume=1.0))
        candles.append(Candle(ts=40, open=entry, high=entry + 800, low=entry - 800,
                              close=entry, volume=1.0))

        class Src:
            def fetch_price(self, symbol):
                return entry

            def fetch_klines(self, symbol, timeframe="4h", limit=300, since=None):
                return candles

        long_sig = Signal(
            symbol="BTC/USDT", timeframe="4h", condition="sma_stack_long",
            features=Features(rsi=55.0, price=entry), direction="long", raw_score=0.5,
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = TradeStore(Path(tmp) / "trades.sqlite")
            calib = CalibrationStore(Path(tmp) / "calibration.json")
            broker = PaperBroker(cash=10_000.0)
            self.assertEqual(broker.taker_fee, TAKER_FEE)
            loop = TradingLoop(Src(), broker, RiskManager(), calib, store=store,
                               strategy="sma_stack", regime=None)
            with patch("hedge_fund.trading.loop.compute_signal", return_value=long_sig):
                results = loop.run_cycle(["BTC/USDT"])

        enters = [r for r in results if r.action == "ENTER"]
        self.assertTrue(enters, f"expected ENTER, got {[(r.action, r.reason) for r in results]}")
        entered = enters[0]
        # Not the isolated-runner constant 0.60; heuristic proposal is blended in.
        self.assertNotAlmostEqual(entered.probability, 0.60, places=4)
        self.assertGreater(entered.probability, 0.5)
        self.assertLessEqual(entered.probability, 0.8)

        stop = broker.lots[0].stop_loss
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        closes = [c.close for c in candles]
        a = atr_fn(highs, lows, closes, ATR_PERIOD)
        expected_dist = max(entry * STOP_FLOOR_FRAC, min(entry * STOP_CAP_FRAC, ATR_STOP_MULT * a))
        self.assertAlmostEqual(stop, entry - expected_dist, places=4)
        # Distinct from the old isolated 2.5% book.
        self.assertNotAlmostEqual(stop, entry * (1 - 0.025), places=2)

        snaps = store.equity_history()
        self.assertTrue(snaps)
        self.assertIsNone(snaps[-1]["baseline"])


if __name__ == "__main__":
    unittest.main()
