"""Running-now vs in-progress: honest stamps, no invented job telemetry."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


class StatusPayloadTests(unittest.TestCase):
    def test_empty_state_never_claims_running_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                from hedge_fund.web.status import build_status

                st = build_status()
        self.assertTrue(st["paper_only"])
        run = st["running_now"]
        self.assertEqual(run["accounts"]["count"], 0)
        self.assertEqual(run["strategy"]["mode"], "sma_stack_fallback")
        self.assertEqual(run["strategy"]["active"], ["sma_stack"])
        self.assertIsNone(run["cycle"]["last_cycle_at"])
        self.assertTrue(run["cycle"]["next"]["inferred"])
        self.assertIsNone(run["heartbeat"]["on"])
        self.assertEqual(run["heartbeat"]["certainty"], "no_signal")
        self.assertFalse(run["regime"]["gates_live_book"])
        self.assertTrue(run["regime"]["display_only"])
        prog = st["in_progress"]
        self.assertFalse(prog["job_runner"])
        self.assertFalse(prog["pipeline"]["running"])
        self.assertFalse(prog["discovery"]["running"])
        self.assertFalse(prog["replenish"]["running"])
        self.assertFalse(prog["graduation"]["running"])
        self.assertEqual(prog["pipeline"]["certainty"], "no_signal")
        self.assertEqual(prog["discovery"]["certainty"], "no_signal")

    def test_last_cycle_uses_equity_snapshot_not_heartbeat_saved_at(self):
        from hedge_fund.trading.store import TradeStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = TradeStore(root / "trades_sma_stack.sqlite")
            store.snapshot_equity(10_000.0, None)
            cycle_ts = store.equity_history()[-1]["ts"]
            later = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
            store.save_account_state({
                "broker": {"cash": 10_000.0, "lots": []},
                "saved_at": later,
            })
            (root / "champions.json").write_text(json.dumps({
                "champions": [{"name": "sma_stack", "closed": 2, "pnl": 1.0, "wins": 1}],
                "synced_until": "",
            }))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                from hedge_fund.web.status import build_status
                st = build_status()
        self.assertEqual(st["running_now"]["cycle"]["last_cycle_at"], cycle_ts)
        self.assertEqual(st["running_now"]["strategy"]["mode"], "champion_accounts")
        self.assertNotEqual(st["running_now"]["cycle"]["account_saved_at"], cycle_ts)

    def test_pipeline_stamp_does_not_set_running_true(self):
        from hedge_fund.trading.stamps import write_pipeline_stamp

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                write_pipeline_stamp("tournament", "started")
                from hedge_fund.web.status import build_status
                st = build_status()
        pipe = st["in_progress"]["pipeline"]
        self.assertFalse(pipe["running"])
        self.assertTrue(pipe["stamp_says_in_progress"])
        self.assertEqual(pipe["phase"], "tournament")
        self.assertEqual(pipe["certainty"], "stamp")
        self.assertTrue(st["in_progress"]["tournament"]["stamp_says_this_phase"])
        self.assertFalse(st["in_progress"]["tournament"]["running"])

    def test_stale_started_stamp_is_labeled_stale(self):
        from hedge_fund.trading.stamps import PIPELINE_STAMP, write_json_stamp

        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(timespec="seconds")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                write_json_stamp(PIPELINE_STAMP, {
                    "phase": "run_isolated",
                    "status": "started",
                    "at": old,
                    "started_at": old,
                    "source": "live_cycle.py",
                })
                from hedge_fund.web.status import build_status
                st = build_status()
        pipe = st["in_progress"]["pipeline"]
        self.assertTrue(pipe["stale"])
        self.assertFalse(pipe["stamp_says_in_progress"])
        self.assertFalse(pipe["running"])

    def test_discovery_and_graduation_last_known(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(json.dumps([{
                "strategy": "rsi_14_>50",
                "tested_at": "2026-08-30T10:00:00+00:00",
                "qualified": True,
                "train_pnl": 1, "test_pnl": 1, "sharpe": 0.2, "win_rate_pct": 40, "trades": 5,
            }]))
            (root / "graduated.json").write_text(json.dumps([{
                "name": "winner",
                "status": "GRADUATED_PAPER",
                "graduated_at": "2026-08-30T12:00:00+00:00",
                "closed_trades": 25, "total_pnl": 10, "wins": 15, "win_rate_pct": 60,
                "trade_history": [],
            }]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                from hedge_fund.web.status import build_status
                st = build_status()
        disc = st["in_progress"]["discovery"]
        self.assertFalse(disc["running"])
        self.assertEqual(disc["last_strategy"], "rsi_14_>50")
        self.assertEqual(st["in_progress"]["graduation"]["last_status"], "GRADUATED_PAPER")
        self.assertFalse(st["in_progress"]["graduation"]["running"])

    def test_cycle_interval_is_hourly_not_4h(self):
        from hedge_fund.trading.constants import CYCLE_INTERVAL_SECONDS
        from hedge_fund.web.status import build_status

        self.assertEqual(CYCLE_INTERVAL_SECONDS, 3600)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                st = build_status()
        self.assertEqual(st["running_now"]["cycle"]["interval_seconds"], 3600)
        self.assertEqual(st["running_now"]["cycle"]["bar_timeframe"], "4h")


class LiveAliasTests(unittest.TestCase):
    def test_live_preview_exposes_react_field_names(self):
        from hedge_fund.trading.store import TradeStore
        from hedge_fund.web.live import live_preview

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "trades_sma_stack.sqlite"
            store = TradeStore(db)
            store.save_account_state({
                "broker": {
                    "cash": 9000.0,
                    "lots": [{
                        "lot_id": 1, "ticker": "BTC/USDT", "quantity": 0.01,
                        "entry_price": 100_000.0, "stop_loss": 97_000.0,
                        "entry_fee": 1.0, "entry_condition": "sma_stack_long",
                    }],
                },
                "saved_at": "2026-08-30T10:00:00+00:00",
            })
            with patch("hedge_fund.web.live.live_prices", return_value={"BTC/USDT": 101_000.0, "ETH/USDT": 1.0}):
                prev = live_preview(str(db))
        pos = prev["positions"][0]
        self.assertEqual(pos["entry"], pos["entry_price"])
        self.assertEqual(pos["stop"], pos["stop_loss"])
        self.assertEqual(pos["pnl"], pos["unrealized_pnl"])
        self.assertEqual(pos["pnl_pct"], pos["unrealized_pct"])
        self.assertAlmostEqual(pos["entry_price"], 100_000.0)

    def test_live_preview_survives_null_exchange_prices(self):
        from hedge_fund.trading.store import TradeStore
        from hedge_fund.web.live import live_preview

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "trades_sma_stack.sqlite"
            store = TradeStore(db)
            store.save_account_state({
                "broker": {
                    "cash": 8500.0,
                    "lots": [{
                        "lot_id": 1, "ticker": "BTC/USDT", "quantity": 0.01,
                        "entry_price": 108_400.0, "stop_loss": 105_200.0,
                        "entry_fee": 1.0, "entry_condition": "sma_stack_long",
                    }],
                },
            })
            with patch("hedge_fund.web.live.live_prices", return_value={"BTC/USDT": None, "ETH/USDT": None}):
                prev = live_preview(str(db))
        self.assertEqual(len(prev["positions"]), 1)
        self.assertEqual(prev["positions"][0]["entry_price"], 108_400.0)
        self.assertIsNone(prev["positions"][0]["current"])
        self.assertGreater(prev["live_equity"], 0)


class HeartbeatStampTests(unittest.TestCase):
    def test_heartbeat_once_writes_stamp_without_claiming_closes(self):
        from hedge_fund.trading.heartbeat import heartbeat_once
        from hedge_fund.trading.stamps import HEARTBEAT_STAMP, read_json_stamp

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with patch("hedge_fund.trading.heartbeat.CcxtSource") as src:
                    src.return_value.fetch_price.return_value = 100.0
                    n = heartbeat_once()
                stamp = read_json_stamp(HEARTBEAT_STAMP)
        self.assertEqual(n, 0)
        self.assertIsNotNone(stamp)
        self.assertIn("last_pass_at", stamp)
        self.assertEqual(stamp["closed"], 0)


class RouteAndCopyTests(unittest.TestCase):
    def test_server_registers_api_status(self):
        src = (REPO / "hedge_fund" / "web" / "server.py").read_text()
        self.assertIn('route == "/api/status"', src)
        self.assertIn("build_status", src)

    def test_frontend_has_running_vs_progress_story(self):
        overview = (REPO / "frontend" / "src" / "pages" / "Overview.tsx").read_text()
        self.assertIn("Running now", overview)
        self.assertIn("In progress", overview)
        self.assertIn("display only", overview.lower())
        self.assertIn("sma_stack", overview)
        champs = (REPO / "frontend" / "src" / "pages" / "Champions.tsx").read_text()
        self.assertNotIn("Production Ready", champs)
        self.assertNotIn("Real-time stream", champs)
        self.assertIn("GRADUATED_PAPER", champs)
        self.assertIn("last-known", champs)
        for pattern in ("frontend/src/**/*.ts", "frontend/src/**/*.tsx"):
            for path in REPO.glob(pattern):
                text = path.read_text()
                self.assertNotIn("READY_FOR_LIVE", text, path)
                self.assertNotIn("READY FOR LIVE", text, path)

    def test_status_never_sets_on_true_for_heartbeat(self):
        src = (REPO / "hedge_fund" / "web" / "status.py").read_text()
        self.assertIn('"on": None', src)


if __name__ == "__main__":
    unittest.main()
