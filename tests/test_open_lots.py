"""One open-lot definition across Overview, Champions, Positions, and APIs."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


def _lot(ticker: str, lot_id: int) -> dict:
    return {
        "lot_id": lot_id,
        "ticker": ticker,
        "quantity": 0.01,
        "entry_price": 100.0 if ticker.startswith("BTC") else 3_000.0,
        "stop_loss": 95.0 if ticker.startswith("BTC") else 2_850.0,
        "entry_fee": 0.1,
        "entry_condition": "test_long",
    }


def _write_account(root: Path, name: str, lots: list[dict]) -> None:
    from hedge_fund.trading.store import TradeStore

    store = TradeStore(root / f"trades_{name}.sqlite")
    store.save_account_state({
        "broker": {"cash": 9_000.0, "lots": lots},
        "saved_at": "2026-08-31T00:00:00+00:00",
    })


def _write_pool(root: Path, names: list[str]) -> None:
    root.joinpath("champions.json").write_text(json.dumps({
        "champions": [
            {"name": name, "closed": 0, "pnl": 0.0, "wins": 0} for name in names
        ],
        "synced_until": "",
    }))


def _three_views():
    from hedge_fund.trading.champions import pool_status
    from hedge_fund.trading.open_lots import attach_open_lots, open_lots_snapshot
    from hedge_fund.web.live import live_preview
    from hedge_fund.web.server import store_dbs
    from hedge_fund.web.status import build_status

    snap = open_lots_snapshot()
    status = build_status()
    champs = attach_open_lots(pool_status())
    live_total = 0
    for db in store_dbs():
        with patch("hedge_fund.web.live.live_prices", return_value={"BTC/USDT": 101.0, "ETH/USDT": 3_010.0}):
            live_total += live_preview(db).get("open_lots") or 0
    return snap, status, champs, live_total


class OpenLotsHelperTests(unittest.TestCase):
    def test_three_champions_two_lots_each_is_six_everywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = ["alpha", "bravo", "charlie"]
            for name in names:
                _write_account(root, name, [_lot("BTC/USDT", 1), _lot("ETH/USDT", 2)])
            _write_pool(root, names)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                snap, status, champs, live_total = _three_views()

        self.assertEqual(snap["open_lots"], 6)
        self.assertEqual(status["running_now"]["positions_open"], 6)
        self.assertEqual(status["running_now"]["open_lots"], 6)
        self.assertEqual(champs["open_lots"], 6)
        self.assertEqual(sum(c["open_lots"] for c in champs["active_champions"]), 6)
        self.assertEqual(live_total, 6)
        self.assertEqual(
            {c["name"]: c["open_lots"] for c in champs["active_champions"]},
            {"alpha": 2, "bravo": 2, "charlie": 2},
        )

    def test_mixed_one_lot_and_two_lot_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_account(root, "solo", [_lot("BTC/USDT", 1)])
            _write_account(root, "pair", [_lot("BTC/USDT", 1), _lot("ETH/USDT", 2)])
            _write_pool(root, ["solo", "pair"])
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                snap, status, champs, live_total = _three_views()

        self.assertEqual(snap["open_lots"], 3)
        self.assertEqual(status["running_now"]["open_lots"], 3)
        self.assertEqual(status["running_now"]["positions_open"], 3)
        self.assertEqual(champs["open_lots"], 3)
        self.assertEqual(sum(c["open_lots"] for c in champs["active_champions"]), 3)
        self.assertEqual(live_total, 3)
        by_name = {c["name"]: c["open_lots"] for c in champs["active_champions"]}
        self.assertEqual(by_name["solo"], 1)
        self.assertEqual(by_name["pair"], 2)

    def test_legacy_sqlite_ignored_when_isolated_dbs_exist(self):
        from hedge_fund.trading.open_lots import open_lots_snapshot, paper_book_dbs
        from hedge_fund.trading.store import TradeStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_account(root, "alpha", [_lot("BTC/USDT", 1), _lot("ETH/USDT", 2)])
            legacy = TradeStore(root / "trades.sqlite")
            legacy.save_account_state({
                "broker": {"cash": 9_000.0, "lots": [_lot("BTC/USDT", 9), _lot("ETH/USDT", 10)]},
            })
            _write_pool(root, ["alpha"])
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                dbs = paper_book_dbs()
                snap = open_lots_snapshot()
                _, status, champs, live_total = _three_views()

        self.assertTrue(all(Path(p).name.startswith("trades_") for p in dbs))
        self.assertEqual(snap["open_lots"], 2)
        self.assertEqual(status["running_now"]["open_lots"], 2)
        self.assertEqual(champs["open_lots"], 2)
        self.assertEqual(champs["active_champions"][0]["open_lots"], 2)
        self.assertEqual(live_total, 2)

    def test_two_lots_same_symbol_still_count_as_two(self):
        """Pyramiding: two BTC lots on one champion is 2, not 1 symbol-row."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_account(root, "pyr", [_lot("BTC/USDT", 1), _lot("BTC/USDT", 2)])
            _write_pool(root, ["pyr"])
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                snap, status, champs, live_total = _three_views()
                from hedge_fund.web.live import live_preview
                db = str(root / "trades_pyr.sqlite")
                with patch("hedge_fund.web.live.live_prices", return_value={"BTC/USDT": 101.0, "ETH/USDT": 3_010.0}):
                    prev = live_preview(db)

        self.assertEqual(snap["open_lots"], 2)
        self.assertEqual(status["running_now"]["open_lots"], 2)
        self.assertEqual(champs["active_champions"][0]["open_lots"], 2)
        self.assertEqual(live_total, 2)
        self.assertEqual(prev["open_lots"], 2)
        self.assertEqual(len(prev["positions"]), 1)
        self.assertEqual(prev["positions"][0]["lot_count"], 2)

    def test_status_and_champions_and_live_import_the_shared_helper(self):
        status_src = (REPO / "hedge_fund" / "web" / "status.py").read_text()
        server_src = (REPO / "hedge_fund" / "web" / "server.py").read_text()
        champs_src = (REPO / "hedge_fund" / "trading" / "champions.py").read_text()
        self.assertIn("from hedge_fund.trading.open_lots import", status_src)
        self.assertIn("open_lots_snapshot", status_src)
        self.assertIn("from hedge_fund.trading.open_lots import", server_src)
        self.assertIn("attach_open_lots", server_src)
        self.assertIn("attach_open_lots", champs_src)
        self.assertNotIn("def _open_positions_count", status_src)


if __name__ == "__main__":
    unittest.main()
