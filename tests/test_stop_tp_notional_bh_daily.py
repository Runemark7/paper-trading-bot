"""Stop/TP-only exits, 100% notional cap, beat-B&H via daily-equity Sharpe."""
from __future__ import annotations

import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from hedge_fund.backtest.strategies import backtest
from hedge_fund.brokers.paper import PaperBroker
from hedge_fund.calibration import CalibrationStore
from hedge_fund.data.binance import Candle
from hedge_fund.risk.managed import MAX_NOTIONAL_FRAC, RiskManager
from hedge_fund.signals.momentum import Features, Signal
from hedge_fund.trading.buy_and_hold import (
    buy_and_hold_daily_equity,
    buy_and_hold_daily_sharpe,
    daily_equity_sharpe,
)
from hedge_fund.trading.constants import QUAL_N_WINDOWS
from hedge_fund.trading.loop import TradingLoop
from hedge_fund.trading.qualify import (
    aggregate_fail_reasons,
    qualification_decision,
    qualification_from_record,
    requalify_parked_log,
)
from hedge_fund.trading.store import TradeStore


def _always(_closes, _i=None, **_kw):
    return True


def _live_src(price_box, candles):
    class Src:
        def fetch_price(self, symbol):
            return price_box[0]

        def fetch_klines(self, symbol, timeframe="5m", limit=300, since=None):
            return candles

    return Src()


def _sig(direction, price):
    return Signal(
        symbol="BTC/USDT",
        timeframe="5m",
        condition="sma_stack_long" if direction == "long" else "flat",
        features=Features(rsi=55.0, price=price),
        direction=direction,
        raw_score=0.5 if direction == "long" else 0.0,
    )


class StopTpOnlyExitTests(unittest.TestCase):
    def test_backtest_holds_through_signal_flip_until_take_profit(self):
        # Flat tape, one-bar entry signal at i=60, then a jump to TP at i=161.
        closes = [100.0] * 161 + [106.0] * 40
        highs = [c + 0.4 for c in closes]
        lows = [c - 0.4 for c in closes]

        def one_bar(_closes, i=None, **_kw):
            return i == 60

        r = backtest(closes, highs, lows, one_bar)
        # Old engine: closed at i=61 on signal flip (tiny loss after fees).
        self.assertEqual(r.trades, 1)
        self.assertEqual(r.exits, {"tp": 1})
        self.assertGreater(r.total_pnl, 0.0)
        self.assertEqual(r.hold_bars, 101)

    def test_backtest_holds_through_signal_flip_until_stop(self):
        closes = [100.0] * 161 + [90.0] * 40
        highs = [c + 0.4 for c in closes]
        lows = [c - 0.4 for c in closes]

        def one_bar(_closes, i=None, **_kw):
            return i == 60

        r = backtest(closes, highs, lows, one_bar)
        self.assertEqual(r.exits, {"stop": 1})
        self.assertLess(r.total_pnl, 0.0)

    def test_live_loop_does_not_close_on_signal_flip(self):
        entry = 100_000.0
        candles = [
            Candle(ts=i, open=90_000.0 + i * 200, high=90_800.0 + i * 200,
                   low=89_200.0 + i * 200, close=90_000.0 + i * 200, volume=1.0)
            for i in range(40)
        ]
        candles.append(Candle(ts=40, open=entry, high=entry + 800, low=entry - 800,
                              close=entry, volume=1.0))
        price = [entry]
        with TemporaryDirectory() as tmp:
            store = TradeStore(Path(tmp) / "trades.sqlite")
            calib = CalibrationStore(Path(tmp) / "calibration.json")
            broker = PaperBroker(cash=10_000.0)
            loop = TradingLoop(_live_src(price, candles), broker, RiskManager(), calib,
                               store=store, strategy="sma_stack")
            with patch("hedge_fund.trading.loop.compute_signal", return_value=_sig("long", entry)):
                loop.run_cycle(["BTC/USDT"])
            self.assertEqual(len(broker.lots), 1)
            # Above stop, below TP; signal goes flat → lot stays open.
            price[0] = entry * 1.01
            with patch("hedge_fund.trading.loop.compute_signal", return_value=_sig("flat", price[0])):
                results = loop.run_cycle(["BTC/USDT"])
            self.assertEqual(len(broker.lots), 1)
            self.assertFalse(any(r.action == "CLOSE_SIGNAL" for r in results))


class NotionalCapTests(unittest.TestCase):
    def test_size_position_shrinks_to_remaining_cash(self):
        self.assertEqual(MAX_NOTIONAL_FRAC, 1.0)
        risk = RiskManager(initial_equity=10_000.0)
        # 1% stop → risk size = 100 qty = $10k notional; only $4k cash left.
        rd = risk.size_position(10_000.0, 100.0, 99.0, [], cash=4_000.0, entry_cost_mult=1.0012)
        self.assertTrue(rd.approved, rd.reason)
        self.assertLess(rd.size, 100.0)
        self.assertLessEqual(rd.size * 100.0 * 1.0012, 4_000.0 + 1e-6)
        self.assertGreater(rd.size * 100.0 * 1.0012, 3_999.0)

    def test_size_position_keeps_risk_size_when_cash_is_ample(self):
        risk = RiskManager(initial_equity=10_000.0)
        rd = risk.size_position(10_000.0, 100.0, 96.0, [], cash=10_000.0, entry_cost_mult=1.0012)
        self.assertTrue(rd.approved)
        self.assertAlmostEqual(rd.size, 25.0)  # 1% of 10k / $4 stop

    def test_size_position_skips_when_no_cash(self):
        risk = RiskManager(initial_equity=10_000.0)
        rd = risk.size_position(10_000.0, 100.0, 98.0, [], cash=5.0, entry_cost_mult=1.0012)
        self.assertFalse(rd.approved)
        self.assertIn("notional", rd.reason)

    def test_backtest_pyramid_never_exceeds_equity_or_goes_negative_cash(self):
        # Tight ATR → 1.5% floor stop → each lot wants ~67% of equity.
        closes = [100.0 + 0.05 * i for i in range(400)]
        highs = [c + 0.1 for c in closes]
        lows = [c - 0.1 for c in closes]
        r = backtest(closes, highs, lows, _always)
        self.assertGreater(r.trades, 0)
        self.assertGreaterEqual(r.min_cash, -1e-6)
        self.assertLessEqual(r.max_notional_frac, MAX_NOTIONAL_FRAC + 1e-9)

    def test_live_loop_pyramid_keeps_cash_non_negative(self):
        base = 100_000.0
        candles = [
            Candle(ts=i, open=base, high=base + 50, low=base - 50, close=base, volume=1.0)
            for i in range(41)
        ]
        price = [base]
        with TemporaryDirectory() as tmp:
            store = TradeStore(Path(tmp) / "trades.sqlite")
            calib = CalibrationStore(Path(tmp) / "calibration.json")
            broker = PaperBroker(cash=10_000.0)
            loop = TradingLoop(_live_src(price, candles), broker, RiskManager(), calib,
                               store=store, strategy="sma_stack")
            for step in range(3):
                price[0] = base * (1 + 0.003 * step)
                with patch("hedge_fund.trading.loop.compute_signal",
                           return_value=_sig("long", price[0])):
                    loop.run_cycle(["BTC/USDT"])
                self.assertGreaterEqual(broker.cash(), -1e-6)
                notional = sum(l.quantity * price[0] for l in broker.lots)
                self.assertLessEqual(notional, broker.equity({"BTC/USDT": price[0]}) + 1e-6)
            self.assertGreaterEqual(len(broker.lots), 1)


class BhDailySharpeGateTests(unittest.TestCase):
    def test_daily_equity_sharpe_helper(self):
        # Mostly-up book (+2% / 0% alternating) → high positive Sharpe.
        eq = [1000.0]
        for i in range(30):
            eq.append(eq[-1] * (1.02 if i % 2 == 0 else 1.0))
        self.assertGreater(daily_equity_sharpe(eq), 1.0)
        # Known literal: returns [0.01, -0.005, 0.02, 0.0] → 10.770 annualized.
        pts = [100.0, 101.0, 100.495, 102.5049, 102.5049]
        self.assertAlmostEqual(daily_equity_sharpe(pts), 10.770, places=2)
        # Flat book (never traded) → 0, not NaN.
        self.assertEqual(daily_equity_sharpe([10_000.0] * 10), 0.0)

    def test_gate_beats_bh_on_daily_sharpe_not_raw_pnl(self):
        windows = [
            {
                "test_pnl": 10.0,
                "test_trades": 15,
                "sharpe": 0.5,
                "train_pnl": 0.0,
                "skipped": False,
                "failed": False,
            }
            for _ in range(3)
        ]
        # Strategy PnL loses to B&H PnL, but daily Sharpe wins → pass B&H leg.
        d = qualification_decision(
            windows,
            expected_windows=3,
            bh_oos_pnl=10_000.0,
            sma_stack_oos_pnl=1.0,
            strategy_daily_sharpe=0.80,
            bh_daily_sharpe=0.30,
        )
        self.assertTrue(d["passed"], d["reasons"])
        self.assertFalse(any("oos_pnl" in r and "bh" in r for r in d["reasons"]))

        # Daily Sharpe loses → fail even if PnL beats B&H.
        d2 = qualification_decision(
            windows,
            expected_windows=3,
            bh_oos_pnl=1.0,
            sma_stack_oos_pnl=1.0,
            strategy_daily_sharpe=0.10,
            bh_daily_sharpe=0.30,
        )
        self.assertFalse(d2["passed"])
        self.assertTrue(any("daily_sharpe" in r for r in d2["reasons"]))

    def test_aggregate_fail_reasons_require_daily_sharpe_fields(self):
        reasons = aggregate_fail_reasons(
            tot_test_pnl=100.0,
            tot_oos_trades=40,
            avg_sharpe=0.5,
            bh_oos_pnl=1.0,
            sma_stack_oos_pnl=1.0,
            n_windows=3,
            expected_windows=3,
            strategy_daily_sharpe=None,
            bh_daily_sharpe=None,
        )
        self.assertTrue(any("buy-and-hold" in r for r in reasons))

    def test_buy_and_hold_daily_sharpe_uses_per_symbol_cash(self):
        # Two symbols, same uptrend — Sharpe scale-free; capital base matches strategy.
        closes = {
            "BTC/USDT": [100.0 + i for i in range(600)],
            "ETH/USDT": [50.0 + 0.5 * i for i in range(600)],
        }
        sr = buy_and_hold_daily_sharpe(closes, start_cash_per_symbol=10_000.0, bars_per_day=288)
        self.assertTrue(math.isfinite(sr))
        self.assertGreater(sr, 0.0)
        pts = buy_and_hold_daily_equity(closes, start_cash_per_symbol=10_000.0, bars_per_day=288)
        # Same 20k book as the strategy (10k per symbol), not 10k split in two.
        self.assertEqual(pts[0], 20_000.0)
        # Points: start, day-1 mark (bar 287), final sell (bar 599).
        self.assertEqual(len(pts), 4)

    def test_old_engine_row_cannot_flip_to_qualified(self):
        # Near-miss from the old engine: passes Sharpe/trades/sma, lost on raw P&L.
        row = {
            "strategy": "dip_24b_lt5pc",
            "qualified": False,
            "sharpe": 0.52,
            "trades": 243,
            "test_pnl": -2704.0,
            "bh_oos_pnl": -9000.0,  # even if B&H P&L were worse
            "sma_stack_oos_pnl": -66936.0,
            "regimes_tested": QUAL_N_WINDOWS,
            "fail_reasons": ["oos_pnl -2704.00 <= bh 1692.00"],
        }
        d = qualification_from_record(row)
        self.assertFalse(d["passed"])
        self.assertIn("buy-and-hold daily sharpe missing", d["reasons"])
        flipped, names = requalify_parked_log([row], existing_names=set())
        self.assertEqual(names, [])
        self.assertFalse(row["qualified"])

    def test_new_row_requalifies_from_stored_daily_sharpes(self):
        row = {
            "strategy": "new_engine_pass",
            "qualified": False,
            "sharpe": 0.52,
            "trades": 243,
            "test_pnl": -100.0,
            "bh_oos_pnl": 20_000.0,
            "sma_stack_oos_pnl": -66936.0,
            "daily_sharpe": 0.9,
            "bh_daily_sharpe": 0.4,
            "regimes_tested": QUAL_N_WINDOWS,
        }
        self.assertTrue(qualification_from_record(row)["passed"])


class StaleWorkerIngestTests(unittest.TestCase):
    def _row(self, name, **extra):
        row = {
            "strategy": name,
            "tested_at": "2026-10-08T14:00:00+00:00",
            "qualified": True,
            "sharpe": 0.5,
            "trades": 40,
            "test_pnl": 50.0,
            "timeframe": "5m",
            "risk_policy": "rm_v1",
            "fail_reasons": [],
        }
        row.update(extra)
        return row

    def test_rows_without_current_gate_rules_are_rejected(self):
        import json
        import os

        from hedge_fund.trading.constants import GATE_RULES
        from hedge_fund.trading.ingest import ingest_discovery_payload

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({"champions": [], "synced_until": ""}))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                out = ingest_discovery_payload({
                    "evaluations": [
                        self._row("stale_pass"),
                        self._row("stale_old_stamp", gate_rules="pre-20261008"),
                        self._row("fresh_fail", qualified=False, gate_rules=GATE_RULES,
                                  fail_reasons=["oos_sharpe 0.10 < 0.3"]),
                    ],
                    "source": "windows_worker",
                })
                from hedge_fund.trading.discovery import load_discovery_log

                logged = {r["strategy"] for r in load_discovery_log()}
        self.assertEqual(sorted(out["rejected_invalid"]), ["stale_old_stamp", "stale_pass"])
        self.assertEqual(out["admitted"], [])
        self.assertEqual(logged, {"fresh_fail"})

    def test_claim_route_gives_stale_worker_no_names(self):
        import os

        from hedge_fund.trading.constants import GATE_RULES
        from hedge_fund.trading.leases import claim_discovery_batch

        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                with patch(
                    "hedge_fund.trading.leases.discovery_universe",
                    return_value=["sma_abv_50", "ema_abv_20"],
                ):
                    stale = claim_discovery_batch("omarchy-1", 2, require_gate_rules=True)
                    fresh = claim_discovery_batch(
                        "omarchy-1", 2, gate_rules=GATE_RULES, require_gate_rules=True
                    )
        self.assertEqual(stale["names"], [])
        self.assertTrue(stale["stale_rules"])
        self.assertIn("git pull", stale["message"])
        self.assertEqual(len(fresh["names"]), 2)


if __name__ == "__main__":
    unittest.main()
