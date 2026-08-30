"""Structural fixes: state root, paper-only, fees, risk, MTF honesty, graduation token."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]

LIVE_CODE_GLOBS = [
    "hedge_fund/trading/**/*.py",
    "hedge_fund/web/**/*.py",
    "hedge_fund/dashboard/**/*.py",
    "hedge_fund/brokers/paper.py",
    "hedge_fund/signals/dynamic.py",
    "scripts/tournament_engine.py",
    "scripts/generate_universe.py",
    "scripts/archived_strategies.py",
    "frontend/src/**/*.ts",
    "frontend/src/**/*.tsx",
]


class PaperBrokerFeeTests(unittest.TestCase):
    def test_taker_fee_and_slippage_on_buy(self):
        from hedge_fund.brokers.paper import PaperBroker, Order, TAKER_FEE, SLIPPAGE

        self.assertEqual(TAKER_FEE, 0.001)
        self.assertEqual(SLIPPAGE, 0.0002)
        broker = PaperBroker(cash=10_000.0)
        fill = broker.place_order(
            Order(ticker="BTC/USDT", side="buy", quantity=1.0, price=100.0, stop_loss=95.0),
            market_price=100.0,
        )
        self.assertIsNotNone(fill)
        self.assertAlmostEqual(fill.price, 100.0 * (1 + SLIPPAGE))
        self.assertAlmostEqual(fill.fee, fill.price * 1.0 * TAKER_FEE)
        self.assertAlmostEqual(broker.cash(), 10_000.0 - fill.price - fill.fee)

    def test_paper_only_off_refuses_construction(self):
        from hedge_fund.brokers.paper import PaperBroker

        with patch.dict(os.environ, {"PAPER_ONLY": "0"}):
            with self.assertRaises(RuntimeError) as ctx:
                PaperBroker(cash=10_000.0)
        self.assertIn("PAPER_ONLY", str(ctx.exception))
        self.assertIn("REFUSING", str(ctx.exception))


class RiskManagerHaltTests(unittest.TestCase):
    def test_fifteen_percent_drawdown_halts(self):
        from hedge_fund.risk.managed import MAX_DRAWDOWN, RiskManager

        self.assertEqual(MAX_DRAWDOWN, 0.15)
        risk = RiskManager(initial_equity=10_000.0)
        risk.update_equity(8_500.0)
        self.assertTrue(risk.is_halted())
        d = risk.size_position(8_500.0, entry=100.0, stop=98.0)
        self.assertFalse(d.approved)

    def test_just_under_fifteen_does_not_halt(self):
        from hedge_fund.risk.managed import RiskManager

        risk = RiskManager(initial_equity=10_000.0)
        risk.update_equity(8_501.0)
        self.assertFalse(risk.is_halted())


class CalibrationWarmupTests(unittest.TestCase):
    def test_warmup_blends_then_cutoff_ignores_proposal(self):
        from hedge_fund.calibration import CalibrationStore, WARMUP_TRIALS

        self.assertEqual(WARMUP_TRIALS, 20)
        with tempfile.TemporaryDirectory() as tmp:
            store = CalibrationStore(Path(tmp) / "c.json")
            key = "BTC/USDT|4h|sma_stack_long"
            cold, _ = store.calibrated_probability(key, proposed=0.9)
            self.assertAlmostEqual(cold, 0.9)
            for _ in range(WARMUP_TRIALS - 1):
                store.record_outcome(key, True)
            still_warm, _ = store.calibrated_probability(key, proposed=0.9)
            self.assertGreater(still_warm, 0.5)
            self.assertNotAlmostEqual(still_warm, store.state(key).mean())
            store.record_outcome(key, True)
            hot, _ = store.calibrated_probability(key, proposed=0.9)
            self.assertEqual(store.state(key).trials(), WARMUP_TRIALS)
            self.assertAlmostEqual(hot, store.state(key).mean())
            self.assertNotAlmostEqual(hot, 0.9)


class ParseStrategyHonestyTests(unittest.TestCase):
    def test_daily_wrapper_is_not_silent_same_series_noop(self):
        from hedge_fund.signals.dynamic import parse_strategy

        with self.assertRaises(ValueError) as ctx:
            parse_strategy("daily(sma_abv_50)")
        self.assertIn("multi-timeframe", str(ctx.exception).lower())
        self.assertIn("silent", str(ctx.exception).lower())

        with self.assertRaises(ValueError):
            parse_strategy("h1(rsi_14_>50)")
        with self.assertRaises(ValueError):
            parse_strategy("m5(dip_6b_lt2pc)")

    def test_mfi_refuses_unit_volume_proxy(self):
        from hedge_fund.signals.dynamic import parse_strategy

        with self.assertRaises(ValueError) as ctx:
            parse_strategy("mfi_14_<25")
        self.assertIn("volume", str(ctx.exception).lower())

    def test_plain_sma_still_parses(self):
        from hedge_fund.signals.dynamic import parse_strategy

        pred = parse_strategy("sma_abv_20")
        closes = [float(i) for i in range(1, 40)]
        self.assertTrue(pred(closes))
        pred_down = parse_strategy("sma_abv_20")
        down = [100.0 - i for i in range(40)]
        self.assertFalse(pred_down(down))

    def test_universe_does_not_emit_mtf_or_mfi(self):
        from hedge_fund.trading.universe import generate_5000_universe

        uni = generate_5000_universe()
        for name in uni:
            self.assertNotIn("daily(", name)
            self.assertNotIn("h1(", name)
            self.assertNotIn("m5(", name)
        self.assertNotIn("mfi_", name)


class StateRootTests(unittest.TestCase):
    def test_champions_and_web_honor_paper_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "paper-state"
            root.mkdir()
            (root / "champions.json").write_text(
                '{"champions": [{"name": "sma_stack", "closed": 0, "pnl": 0, "wins": 0}], "synced_until": ""}'
            )
            (root / "trades_sma_stack.sqlite").write_text("")  # placeholder; glob only
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                from hedge_fund.trading.champions import load_pool
                from hedge_fund.web import server as web

                pool = load_pool()
                self.assertEqual(pool["champions"][0]["name"], "sma_stack")
                self.assertEqual(web._state_dir(), root)
                dbs = web.per_strategy_dbs()
                self.assertTrue(any(p.name == "trades_sma_stack.sqlite" for p in dbs))


class GraduatedPaperTokenTests(unittest.TestCase):
    def test_no_ready_for_live_in_live_code(self):
        hits = []
        for pattern in LIVE_CODE_GLOBS:
            for path in REPO.glob(pattern):
                if not path.is_file():
                    continue
                text = path.read_text()
                if "READY_FOR_LIVE" in text:
                    hits.append(str(path.relative_to(REPO)))
        self.assertEqual(hits, [], msg=f"READY_FOR_LIVE still in {hits}")

    def test_graduation_writes_graduated_paper(self):
        from hedge_fund.trading.champions import collect_live_results, load_graduated
        from hedge_fund.trading.constants import GRADUATED_PAPER, TRADE_EVALUATION_LIMIT
        from hedge_fund.trading.store import TradeStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                store = TradeStore(root / "trades_winner.sqlite")
                for i in range(TRADE_EVALUATION_LIMIT):
                    tid = store.open_trade(
                        "BTC/USDT", "4h", "sma_stack_long", 0.6, 100.0, 0.01, 0.01, lot_id=i,
                    )
                    store.close_trade(tid, 110.0, "take_profit", 0.01, 1.0, 0.1, 1)
                (root / "champions.json").write_text(
                    '{"champions": [{"name": "winner", "closed": 0, "pnl": 0.0, "wins": 0}], "synced_until": ""}'
                )
                out = collect_live_results()
                self.assertTrue(out["graduated"])
                self.assertEqual(out["graduated"][0]["status"], GRADUATED_PAPER)
                self.assertEqual(load_graduated()[0]["status"], GRADUATED_PAPER)

    def test_constants_match_protocol_cited_values(self):
        from hedge_fund.trading.constants import (
            MAX_ACTIVE_CHAMPIONS,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            MIN_BACKTEST_WIN_RATE,
            TRADE_EVALUATION_LIMIT,
        )

        self.assertEqual(MIN_BACKTEST_SHARPE, 0.10)
        self.assertEqual(MIN_BACKTEST_WIN_RATE, 0.38)
        self.assertEqual(MIN_BACKTEST_TRADES, 4)
        self.assertEqual(TRADE_EVALUATION_LIMIT, 25)
        self.assertEqual(MAX_ACTIVE_CHAMPIONS, 1000)

    def test_tournament_imports_champions_not_sys_path_opt(self):
        src = (REPO / "scripts" / "tournament_engine.py").read_text()
        self.assertNotIn("sys.path", src)
        self.assertNotIn("/opt/data", src)
        self.assertIn("from hedge_fund.trading.champions import", src)
        self.assertNotIn("from hedge_fund.backtest.fast_quant", src)
        self.assertNotIn("import hedge_fund.backtest.fast_quant", src)

    def test_nginx_does_not_proxy_run(self):
        tmpl = (REPO / "frontend" / "nginx-server.conf.template").read_text()
        self.assertIn("location = /run", tmpl)
        self.assertIn("return 404", tmpl)
        self.assertNotIn("proxy_pass         ${BACKEND_URL}/run", tmpl)

    def test_kustomization_has_no_cronjob(self):
        kust = (REPO / "k8s" / "kustomization.yaml").read_text()
        self.assertNotIn("cronjob.yaml", kust)
        self.assertFalse((REPO / "k8s" / "cronjob.yaml").exists())
        backend = (REPO / "k8s" / "backend.yaml").read_text()
        self.assertIn("name: cycle", backend)
        self.assertIn("sleep 14400", backend)
        compose = (REPO / "docker-compose.yml").read_text()
        self.assertIn("sleep 14400", compose)
        self.assertNotIn("21600", compose)


if __name__ == "__main__":
    unittest.main()
