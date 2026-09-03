"""champion_since: admit timestamp, honest backfill, no mtime/now() invention."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
TYPES_TS = (REPO / "frontend" / "src" / "api" / "types.ts").read_text()
FORMAT_TS = (REPO / "frontend" / "src" / "status" / "format.ts").read_text()
CHAMPS_TSX = (REPO / "frontend" / "src" / "pages" / "Champions.tsx").read_text()


def _write_trade(root: Path, name: str, entry_ts: str, *, closed: bool = False) -> None:
    from hedge_fund.trading.store import TradeStore

    store = TradeStore(root / f"trades_{name}.sqlite")
    tid = store.open_trade("BTC/USDT", "5m", "test_long", 0.6, 100.0, 0.01, 0.01, lot_id=1)
    store.conn.execute("UPDATE trades SET entry_ts=? WHERE id=?", (entry_ts, tid))
    store.conn.commit()
    if closed:
        store.close_trade(tid, 101.0, "take_profit", 0.01, 1.0, 0.01, 1)


class PromoteChampionSinceTests(unittest.TestCase):
    def test_new_promote_sets_champion_since_second_save_does_not_change_it(self):
        from hedge_fund.trading.champions import (
            collect_live_results,
            load_pool,
            promote_candidates,
            save_pool,
        )

        t1 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 9, 2, 18, 30, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with patch("hedge_fund.trading.champions.datetime") as mock_dt:
                    mock_dt.now.return_value = t1
                    out = promote_candidates([
                        {"strategy": "sma_abv_20", "source": "sweep_promotion"},
                    ])
                self.assertEqual(out["added"], ["sma_abv_20"])
                first = load_pool()["champions"][0]
                self.assertEqual(first["champion_since"], t1.isoformat())
                self.assertEqual(first["source"], "sweep_promotion")

                with patch("hedge_fund.trading.champions.datetime") as mock_dt:
                    mock_dt.now.return_value = t2
                    collect_live_results()
                    st = load_pool()
                    st["champions"][0]["closed"] = 4
                    st["champions"][0]["champion_since"] = t2.isoformat()
                    save_pool(st)
                again = load_pool()["champions"][0]
                self.assertEqual(again["champion_since"], t1.isoformat())
                self.assertEqual(again["closed"], 4)

    def test_backtest_promotion_source_kept(self):
        from hedge_fund.trading.champions import load_pool, promote_candidates

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                promote_candidates([{"strategy": "mom_12b_gt3pc", "source": "backtest_promotion"}])
                row = load_pool()["champions"][0]
        self.assertEqual(row["source"], "backtest_promotion")
        self.assertTrue(row["champion_since"])


class BackfillChampionSinceTests(unittest.TestCase):
    def test_prefers_first_trade_ts_over_discovery(self):
        from hedge_fund.trading.champions import pool_status, save_pool

        trade_ts = "2026-08-20T12:00:00+00:00"
        later_trade = "2026-08-22T09:00:00+00:00"
        discovery_ts = "2026-08-01T08:00:00+00:00"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_target = {
                "champions": [{"name": "alpha", "closed": 2, "pnl": 1.0, "wins": 1}],
                "synced_until": "",
            }
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool(save_target)
                _write_trade(root, "alpha", later_trade, closed=True)
                _write_trade(root, "alpha", trade_ts, closed=False)
                (root / "discovery_log.json").write_text(json.dumps([{
                    "strategy": "alpha",
                    "tested_at": discovery_ts,
                    "qualified": True,
                }]))
                with patch("hedge_fund.trading.champions.datetime") as mock_dt:
                    mock_dt.now.return_value = datetime(2026, 9, 2, tzinfo=timezone.utc)
                    status = pool_status()
                self.assertEqual(status["active_champions"][0]["champion_since"], trade_ts)
                persisted = json.loads((root / "champions.json").read_text())
                self.assertEqual(persisted["champions"][0]["champion_since"], trade_ts)

    def test_missing_both_is_null_and_does_not_stamp_now(self):
        from hedge_fund.trading.champions import pool_status, save_pool

        fake_now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({
                    "champions": [{"name": "grandpa", "closed": 0, "pnl": 0.0, "wins": 0}],
                    "synced_until": "",
                })
                with patch("hedge_fund.trading.champions.datetime") as mock_dt:
                    mock_dt.now.return_value = fake_now
                    status = pool_status()
                self.assertIsNone(status["active_champions"][0]["champion_since"])
                persisted = json.loads((root / "champions.json").read_text())
                self.assertIsNone(persisted["champions"][0].get("champion_since"))
                self.assertNotEqual(
                    persisted["champions"][0].get("champion_since"),
                    fake_now.isoformat(),
                )

    def test_qualified_discovery_when_no_trades(self):
        from hedge_fund.trading.champions import pool_status, save_pool

        disc = "2026-07-15T11:22:00+00:00"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({
                    "champions": [{"name": "beta", "closed": 0, "pnl": 0.0, "wins": 0}],
                    "synced_until": "",
                })
                (root / "discovery_log.json").write_text(json.dumps([
                    {"strategy": "beta", "tested_at": "2026-07-20T00:00:00+00:00", "qualified": False},
                    {"strategy": "beta", "tested_at": disc, "qualified": True},
                    {"strategy": "other", "tested_at": "2026-01-01T00:00:00+00:00", "qualified": True},
                ]))
                status = pool_status()
        self.assertEqual(status["active_champions"][0]["champion_since"], disc)

    def test_unqualified_discovery_alone_stays_null(self):
        from hedge_fund.trading.champions import pool_status, save_pool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({
                    "champions": [{"name": "gamma", "closed": 0, "pnl": 0.0, "wins": 0}],
                    "synced_until": "",
                })
                (root / "discovery_log.json").write_text(json.dumps([{
                    "strategy": "gamma",
                    "tested_at": "2026-07-15T11:22:00+00:00",
                    "qualified": False,
                }]))
                status = pool_status()
        self.assertIsNone(status["active_champions"][0]["champion_since"])


    def test_read_only_skips_sqlite_min_and_does_not_write(self):
        from hedge_fund.trading.champions import pool_status, save_pool

        trade_ts = "2026-08-20T12:00:00+00:00"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({
                    "champions": [{"name": "alpha", "closed": 0, "pnl": 0.0, "wins": 0}],
                    "synced_until": "",
                })
                _write_trade(root, "alpha", trade_ts, closed=False)
                with patch("hedge_fund.trading.champions.infer_champion_since") as infer:
                    status = pool_status(read_only=True)
                infer.assert_not_called()
                self.assertIsNone(status["active_champions"][0]["champion_since"])
                persisted = json.loads((root / "champions.json").read_text())
                self.assertIsNone(persisted["champions"][0].get("champion_since"))
                filled = pool_status()
                self.assertEqual(filled["active_champions"][0]["champion_since"], trade_ts)


class ChampionSinceUiContractTests(unittest.TestCase):
    def test_frontend_type_and_payload_include_the_field(self):
        self.assertIn("champion_since?: string | null", TYPES_TS)
        self.assertIn("function fmtChampionSince", FORMAT_TS)
        self.assertIn("toLocaleDateString", FORMAT_TS)
        self.assertIn("before dating", FORMAT_TS)
        collapsed = CHAMPS_TSX.split("{isOpen &&")[0]
        expanded = CHAMPS_TSX.split("{isOpen &&", 1)[1]
        self.assertIn("ChampionSinceChip", collapsed)
        self.assertIn('label="Champion since"', collapsed)
        self.assertIn("fmtChampionSince(c.champion_since)", collapsed)
        self.assertIn("flex flex-wrap", collapsed)
        self.assertIn("Started {fmtChampionSince(c.champion_since)}", expanded)
        self.assertIn("flex flex-wrap", expanded)
        self.assertNotIn("target_active: number", TYPES_TS)


if __name__ == "__main__":
    unittest.main()
