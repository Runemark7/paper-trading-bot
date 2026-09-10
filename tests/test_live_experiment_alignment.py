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
        self.assertIn("baseline=None", text)  # historical 2026-08-30 text kept
        self.assertIn("not an LLM", text)
        self.assertIn("Paper only", text)
        # Original sizing/fees language kept for history
        self.assertIn("1 % fixed-fractional risk per trade", text)
        self.assertIn("Taker fee (0.1 %)", text)

    def test_amendment_2026_09_01(self):
        from hedge_fund.trading.constants import (
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
            TRADE_EVALUATION_LIMIT,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-01", text)
        self.assertIn("2026-09-01", text)
        self.assertIn(QUAL_TIMEFRAME, text)
        self.assertIn(RISK_POLICY, text)
        self.assertIn("MAX_ACTIVE_CHAMPIONS = 20", text)
        self.assertIn(str(TRADE_EVALUATION_LIMIT), text)
        self.assertIn(str(MIN_BACKTEST_TRADES), text)
        self.assertIn("0.30", text)
        self.assertIn("same game", text.lower())
        self.assertIn("5m tape was **discovery-only**", text)
        self.assertIn("MIN_BACKTEST_SHARPE = 0.30", text)
        self.assertIn("CYCLE_INTERVAL_SECONDS = 3600", text)

    def test_amendment_2026_09_02(self):
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            QUAL_WINDOW_BARS,
            QUAL_WINDOW_DAYS,
            RISK_POLICY,
            TRADE_EVALUATION_LIMIT,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-02", text)
        self.assertIn("2026-09-02", text)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertIn(QUAL_TIMEFRAME, text)
        self.assertIn("5m", text)
        self.assertIn("CYCLE_INTERVAL_SECONDS = 300", text)
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertIn("07–21 Europe/Stockholm", text)
        self.assertIn("24 × 5m = **2 hours**", text)
        self.assertIn("not 24 × 4h = 4 days", text)
        self.assertIn("dip_24b_lt1pc", text)
        self.assertIn("GRADUATED_PAPER", text)
        self.assertIn("Paper only", text)
        self.assertIn(RISK_POLICY, text)
        self.assertIn("MAX_ACTIVE_CHAMPIONS = 20", text)
        self.assertIn(str(TRADE_EVALUATION_LIMIT), text)
        self.assertIn(str(MIN_BACKTEST_TRADES), text)
        self.assertIn("0.30", text)
        self.assertEqual(QUAL_WINDOW_DAYS, 90)
        self.assertEqual(QUAL_WINDOW_BARS, 25920)
        self.assertIn("25920", text)
        self.assertIn("90 calendar days", text)
        self.assertNotEqual(QUAL_WINDOW_BARS, 2500)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        # 4h remains as historical 09-01 text, but 09-02 names the supersession.
        self.assertIn("5m is the admit tape", text)
        self.assertIn("live and admit are 5m", text)

    def test_amendment_2026_09_03(self):
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
            TRADE_EVALUATION_LIMIT,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-03", text)
        self.assertIn("2026-09-03", text)
        self.assertIn("structure atoms", text.lower())
        self.assertIn("OHLC", text)
        self.assertIn("Paper only", text)
        self.assertIn("don_hi_N", text)
        self.assertIn("near_swing_lo_N", text)
        # Gates unchanged from 2026-09-02.
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        import hedge_fund.trading.constants as constants
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)
        self.assertIn("OOS gates are unchanged", text)
        self.assertIn("arena 20", text)

    def test_amendment_2026_09_03_revokes_live_slot_cap(self):
        import hedge_fund.trading.constants as constants
        from hedge_fund.trading.constants import TRADE_EVALUATION_LIMIT

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("20-slot arena revoked", text)
        self.assertIn("MAX_ACTIVE_CHAMPIONS` is deleted", text)
        self.assertIn("combinatorial bound", text)
        # Historical 2026-09-03 text still names the old sample; the live
        # constant is gone (2026-09-05 drains leftovers instead).
        self.assertIn("DISCOVER_BATCH_SIZE = 30", text)
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        self.assertFalse(hasattr(constants, "DISCOVER_BATCH_SIZE"))
        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)

    def test_amendment_2026_09_05_wavetrend(self):
        from hedge_fund.trading.constants import QUAL_TIMEFRAME, RISK_POLICY, TRADE_EVALUATION_LIMIT
        import hedge_fund.trading.constants as constants

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-05", text)
        self.assertIn("LazyBear", text)
        self.assertIn("wt_cross_up_os", text)
        self.assertIn("HLC3", text)
        self.assertIn("not market cipher", text.lower())
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))

    def test_amendment_2026_09_05_night_window_and_discovery_drain(self):
        import hedge_fund.trading.constants as constants
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("24/7 cycle and leftover-universe discovery", text)
        self.assertIn("Night window revoked", text)
        self.assertIn("Discovery drains leftovers", text)
        self.assertIn("Discovery buckets", text)
        self.assertIn("/api/discovery/summary", text)
        self.assertIn("no 30-name sample", text)
        self.assertIn("Paper only", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("No new strategies", text)
        self.assertIn("CYCLE_INTERVAL_SECONDS = 300", text)
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertFalse(hasattr(constants, "DISCOVER_BATCH_SIZE"))
        self.assertFalse(hasattr(constants, "CYCLE_WINDOW_START_HOUR"))
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        # Historical 09-02 night-window sentence stays; this amendment names the supersession.
        self.assertIn("sidecar still skips outside 07–21 Europe/Stockholm", text)
        self.assertIn("07–21 Europe/Stockholm; that night window is unchanged", text)

    def test_amendment_2026_09_07_budgeted_discovery(self):
        import hedge_fund.trading.constants as constants
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
            DISCOVERY_LOG_CAP,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-07", text)
        self.assertIn("budgeted discovery", text.lower())
        self.assertIn("incremental discovery log", text.lower())
        self.assertIn("DISCOVER_CYCLE_MAX_NAMES", text)
        self.assertIn("DISCOVER_CYCLE_TIME_BUDGET_SECONDS", text)
        self.assertIn("discovery_cursor.json", text)
        self.assertIn("24h", text)
        self.assertIn("DISCOVER_RETEST_COOLDOWN_SECONDS", text)
        self.assertIn("shuffle 30 and ignore the rest", text)
        self.assertIn("Paper only", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("last_tested_at", text)
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 90)
        self.assertEqual(DISCOVERY_LOG_CAP, 1000)
        self.assertLess(DISCOVER_CYCLE_MAX_NAMES, 30)
        self.assertLess(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, CYCLE_INTERVAL_SECONDS)
        self.assertFalse(hasattr(constants, "DISCOVER_BATCH_SIZE"))
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        self.assertIn("lighter per-cycle discovery slice", text)
        self.assertIn("DISCOVER_CYCLE_MAX_NAMES` (1)", text)
        self.assertIn("DISCOVER_CYCLE_TIME_BUDGET_SECONDS` (90s)", text)
        self.assertGreater(
            CYCLE_INTERVAL_SECONDS - DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 150
        )

    def test_amendment_2026_09_10_fail_once_never_retest(self):
        import hedge_fund.trading.constants as constants
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
            TRADE_EVALUATION_LIMIT,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-10", text)
        self.assertIn("fail once, never retest", text.lower())
        self.assertIn("parked forever", text.lower())
        self.assertIn("DISCOVER_RETEST_COOLDOWN_SECONDS` is deleted", text)
        self.assertIn("Never-tested leftovers still drain", text)
        self.assertIn("Existing prod fails are parked", text)
        self.assertIn("Paper only", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("OOS gates are unchanged", text)
        self.assertFalse(hasattr(constants, "DISCOVER_RETEST_COOLDOWN_SECONDS"))
        self.assertFalse(hasattr(constants, "DISCOVER_BATCH_SIZE"))
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 90)
        readme = (REPO / "README.md").read_text()
        self.assertIn("2026-09-10", readme)
        self.assertIn("no 24h", readme.lower())
        web = (REPO / "docs" / "WEB_SERVICE.md").read_text()
        self.assertIn("rejected parked forever", web.lower())


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
        from hedge_fund.trading.constants import GRADUATED_PAPER, QUAL_TIMEFRAME, TRADE_EVALUATION_LIMIT

        html = strategy_rules_section()
        self.assertNotIn("2.5% below entry (hard)", html)
        self.assertNotIn("Longs only in RISK_ON / NEUTRAL", html)
        self.assertNotIn("LIVE (A/B winner)", html)
        self.assertIn("regime=None", html)
        self.assertIn("graduated paper", html.lower())
        self.assertNotIn("baseline=None", html)
        self.assertIn("buy-and-hold", html.lower())
        self.assertIn(f"{ATR_STOP_MULT:.1f}× ATR({ATR_PERIOD})", html)
        self.assertIn(f"{STOP_FLOOR_FRAC:.1%}", html)
        self.assertIn(f"{RISK_FRAC:.0%}", html)
        self.assertIn(f"{MAX_OPEN_RISK_FRAC:.0%}", html)
        self.assertIn(f"{MAX_DRAWDOWN:.0%}", html)
        self.assertIn(str(TRADE_EVALUATION_LIMIT), html)
        self.assertIn("TradingLoop.run_cycle", html)
        self.assertIn(GRADUATED_PAPER, html)
        self.assertNotIn("READY_FOR_LIVE", html)
        self.assertIn(QUAL_TIMEFRAME, html)
        self.assertNotIn("5m history is not the admit bar", html)
        self.assertIn("Every 300s", html)
        self.assertIn("around the clock", html)
        self.assertNotIn("07–21", html)
        self.assertNotIn("Discover batch 30", html)


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

            def fetch_klines(self, symbol, timeframe="5m", limit=300, since=None):
                return candles

        long_sig = Signal(
            symbol="BTC/USDT", timeframe="5m", condition="sma_stack_long",
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
        self.assertIsNotNone(snaps[-1]["baseline"])
        self.assertGreater(snaps[-1]["baseline"], 0)


if __name__ == "__main__":
    unittest.main()
