"""GET /api/champions/trades — read-only champion trade log."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

from hedge_fund.trading.store import TradeStore
from hedge_fund.web.champion_trades import (
    champion_trades_payload,
    clear_champion_trades_cache,
    parse_limit,
)

NAME_A = "h1_ema_abv_50&mom_18b_gt2pc&sma_abv_30&rsi_14_>45"
NAME_B = "h4_ema_abv_20&sma_abv_30"


def _seed(root: Path) -> None:
    (root / "champions.json").write_text(json.dumps({
        "champions": [
            {"name": NAME_A, "closed": 1, "pnl": 10.0, "wins": 1, "champion_since": "2026-10-01T00:00:00+00:00"},
            {"name": NAME_B, "closed": 0, "pnl": 0.0, "wins": 0, "champion_since": "2026-10-20T00:00:00+00:00"},
        ]
    }))
    st = TradeStore(root / f"trades_{NAME_A}.sqlite")
    tid = st.open_trade("BTC/USDT", "5m", NAME_A, 0.5, 100.0, 2.0, entry_fee=0.2, lot_id=1)
    st.conn.execute("UPDATE trades SET entry_ts=? WHERE id=?", ("2026-10-02T00:00:00+00:00", tid))
    st.close_trade(tid, 110.0, "take_profit", 0.22, 19.58, 0.10, 1)
    st.conn.execute("UPDATE trades SET exit_ts=? WHERE id=?", ("2026-10-02T06:00:00+00:00", tid))
    tid2 = st.open_trade("BTC/USDT", "5m", NAME_A, 0.5, 105.0, 1.0, entry_fee=0.105, lot_id=2)
    st.conn.execute("UPDATE trades SET entry_ts=? WHERE id=?", ("2026-10-03T00:00:00+00:00", tid2))
    st.conn.commit()
    st.snapshot_equity(10019.4, baseline=10000.0)
    st.close()
    TradeStore(root / f"trades_{NAME_B}.sqlite").close()


class ChampionTradesPayloadTests(unittest.TestCase):
    def setUp(self):
        clear_champion_trades_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _seed(self.root)
        self.env = patch.dict(os.environ, {"PAPER_STATE": str(self.root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
        clear_champion_trades_cache()

    def test_events_are_newest_first_with_fees_and_net_pnl(self):
        body = champion_trades_payload()
        self.assertTrue(body["paper_only"])
        self.assertEqual(body["total_events"], 3)
        kinds = [(e["kind"], e["ts"]) for e in body["events"]]
        self.assertEqual(kinds, [
            ("entry", "2026-10-03T00:00:00+00:00"),
            ("exit", "2026-10-02T06:00:00+00:00"),
            ("entry", "2026-10-02T00:00:00+00:00"),
        ])
        exit_ev = body["events"][1]
        self.assertEqual(exit_ev["reason"], "take_profit")
        self.assertAlmostEqual(exit_ev["fee"], 0.22)
        self.assertAlmostEqual(exit_ev["pnl"], 19.58)
        self.assertAlmostEqual(exit_ev["notional"], 220.0)
        self.assertTrue(body["events"][0]["open"])

    def test_champion_summaries_include_empty_accounts(self):
        body = champion_trades_payload()
        by_name = {c["name"]: c for c in body["champions"]}
        a = by_name[NAME_A]
        self.assertEqual((a["closed"], a["open"], a["wins"]), (1, 1, 1))
        self.assertAlmostEqual(a["fees_paid"], 0.525)
        self.assertAlmostEqual(a["realized_pnl"], 19.58)
        self.assertAlmostEqual(a["equity"], 10019.4)
        self.assertEqual(len(a["open_entries"]), 1)
        b = by_name[NAME_B]
        self.assertTrue(b["has_account"])
        self.assertEqual(b["trades_total"], 0)
        self.assertIsNone(b["equity"])

    def test_name_filter_and_limit(self):
        body = champion_trades_payload(NAME_A, limit=1)
        self.assertEqual(body["name"], NAME_A)
        self.assertTrue(body["found"])
        self.assertEqual(len(body["events"]), 1)
        self.assertEqual(body["total_events"], 3)
        missing = champion_trades_payload("not_a_champion")
        self.assertFalse(missing["found"])
        self.assertEqual(missing["events"], [])

    def test_read_does_not_write_the_db(self):
        db = self.root / f"trades_{NAME_A}.sqlite"
        before = db.stat().st_mtime_ns
        champion_trades_payload()
        champion_trades_payload()
        self.assertEqual(db.stat().st_mtime_ns, before)

    def test_cache_picks_up_new_closes(self):
        champion_trades_payload()
        st = TradeStore(self.root / f"trades_{NAME_A}.sqlite")
        open_id = st.open_trade_ids()[0]["id"]
        st.close_trade(open_id, 100.0, "stop_loss", 0.1, -5.205, -0.047, 0)
        st.close()
        body = champion_trades_payload(NAME_A)
        self.assertEqual(body["champions"][0]["closed"], 2)
        self.assertEqual(body["events"][0]["reason"], "stop_loss")

    def test_parse_limit(self):
        self.assertEqual(parse_limit(None), 200)
        self.assertEqual(parse_limit("5"), 5)
        for bad in ("0", "1001", "x"):
            with self.assertRaises(ValueError):
                parse_limit(bad)


class ChampionTradesRouteTests(unittest.TestCase):
    def test_route_serves_json_and_rejects_bad_limit(self):
        from hedge_fund.web.server import Handler

        clear_champion_trades_cache()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                port = httpd.server_address[1]
                with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                    body = json.loads(urlopen(
                        f"http://127.0.0.1:{port}/api/champions/trades?name={quote(NAME_A, safe='')}&limit=2",
                        timeout=10,
                    ).read())
                    self.assertEqual(body["name"], NAME_A)
                    self.assertEqual(len(body["events"]), 2)
                    with self.assertRaises(HTTPError) as ctx:
                        urlopen(f"http://127.0.0.1:{port}/api/champions/trades?limit=0", timeout=10)
                    self.assertEqual(ctx.exception.code, 400)
            finally:
                httpd.shutdown()
                httpd.server_close()
                clear_champion_trades_cache()


if __name__ == "__main__":
    unittest.main()
