"""One-shot retire of champions that failed a requalify batch.

Archive, never delete: rows move to retired.json, trade DBs and discovery
history stay, retired names are blocked from re-admit / re-mint, and the
web reads handle an empty pool.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from hedge_fund.trading.store import TradeStore

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def _row(name: str, **extra) -> dict:
    out = {
        "name": name,
        "closed": 12,
        "pnl": -42.5,
        "wins": 5,
        "champion_since": "2026-09-01T00:00:00+00:00",
    }
    out.update(extra)
    return out


def _grad(name: str) -> dict:
    return {
        "name": name,
        "closed_trades": 80,
        "total_pnl": -600.0,
        "status": "REJECTED_NEGATIVE_PNL",
        "graduated_at": "2026-09-20T00:00:00+00:00",
        "trade_history": [{"symbol": "BTC/USDT", "pnl": -1.0}],
    }


def _rq(batch_id: str, status: str = "done", qualified: bool = False) -> dict:
    meta = {"batch_id": batch_id, "status": status, "attempts": 1}
    if status == "done":
        meta["result"] = {
            "qualified": qualified,
            "trades": 171,
            "sharpe": 0.8,
            "test_pnl": 1242.0,
            "bh_oos_pnl": 15955.0,
            "fail_reasons": [] if qualified else ["beat_bh"],
            "regimes_tested": 23,
        }
    return meta


def _db(root: Path, name: str) -> Path:
    path = root / f"trades_{name}.sqlite"
    TradeStore(path).save_account_state({"broker": {"cash": 10_000.0, "lots": []}})
    return path


def _seed(root: Path) -> None:
    (root / "champions.json").write_text(json.dumps({
        "champions": [_row("dip_24b_lt5pc"), _row("mom_36b_gt5pc"), _row("newcomer")],
        "synced_until": "2026-10-08T00:00:00",
    }))
    (root / "graduated.json").write_text(json.dumps([_grad("h1_old"), _grad("grad_pass")]))
    (root / "discovery_requalify.json").write_text(json.dumps({
        "batches": [{"batch_id": "gate23-20261008", "size": 4}],
        "seeded": ["gate23-20261008"],
        "names": {
            "dip_24b_lt5pc": _rq("gate23-20261008"),
            "mom_36b_gt5pc": _rq("gate23-20261008"),
            "h1_old": _rq("gate23-20261008"),
            "grad_pass": _rq("gate23-20261008", qualified=True),
            "other_batch": _rq("someday"),
        },
    }))
    (root / "discovery_tested.json").write_text(json.dumps({"dip_24b_lt5pc": True}))
    (root / "discovery_log.json").write_text(json.dumps([
        {"strategy": "dip_24b_lt5pc", "qualified": True, "tested_at": "2026-09-01T00:00:00+00:00"},
    ]))
    for name in ("dip_24b_lt5pc", "mom_36b_gt5pc", "newcomer", "h1_old"):
        _db(root, name)


class AutoRetireTests(unittest.TestCase):
    def test_retires_failed_batch_names_and_archives(self):
        from hedge_fund.trading.champions import load_graduated, load_pool, load_retired
        from hedge_fund.trading.retire import RETIRE_AUTO_BATCH, RETIRE_REASON, ensure_auto_retire_unlocked

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            tested_before = (root / "discovery_tested.json").read_text()
            log_before = (root / "discovery_log.json").read_text()
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                out = ensure_auto_retire_unlocked(NOW)
                pool = load_pool()
                grads = load_graduated()
                state = load_retired()

            self.assertEqual(sorted(out["retired_champions"]), ["dip_24b_lt5pc", "mom_36b_gt5pc"])
            self.assertEqual(out["retired_graduated"], ["h1_old"])
            # Not in the batch -> stays. Passed requalify -> stays.
            self.assertEqual([c["name"] for c in pool["champions"]], ["newcomer"])
            self.assertEqual([g["name"] for g in grads], ["grad_pass"])
            self.assertEqual(pool["synced_until"], "2026-10-08T00:00:00")

            self.assertIn(RETIRE_AUTO_BATCH, state["applied"])
            row = state["retired"]["dip_24b_lt5pc"]
            self.assertEqual(row["role"], "champion")
            self.assertEqual(row["reason"], RETIRE_REASON)
            self.assertEqual(row["reason"], "failed gate23 requalify 2026-10-08")
            self.assertEqual(row["record"]["pnl"], -42.5)
            self.assertEqual(row["requalify"]["bh_oos_pnl"], 15955.0)
            grad_row = state["retired"]["h1_old"]
            self.assertEqual(grad_row["role"], "graduated")
            self.assertEqual(grad_row["record"]["trade_history"], [{"symbol": "BTC/USDT", "pnl": -1.0}])

            # Archive, not delete: DBs and discovery history untouched.
            for name in ("dip_24b_lt5pc", "mom_36b_gt5pc", "h1_old"):
                self.assertTrue((root / f"trades_{name}.sqlite").exists())
            self.assertEqual((root / "discovery_tested.json").read_text(), tested_before)
            self.assertEqual((root / "discovery_log.json").read_text(), log_before)

    def test_runs_once(self):
        from hedge_fund.trading.champions import load_pool
        from hedge_fund.trading.retire import ensure_auto_retire_unlocked

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                self.assertIsNotNone(ensure_auto_retire_unlocked(NOW))
                # Even if a name somehow came back, the batch never re-runs.
                pool = load_pool()
                pool["champions"].append(_row("mom_36b_gt5pc"))
                (root / "champions.json").write_text(json.dumps(pool))
                self.assertIsNone(ensure_auto_retire_unlocked(NOW))
                names = [c["name"] for c in load_pool()["champions"]]
            self.assertIn("mom_36b_gt5pc", names)

    def test_no_finished_failures_is_noop_and_not_marked(self):
        from hedge_fund.trading.champions import load_pool, load_retired
        from hedge_fund.trading.retire import ensure_auto_retire_unlocked

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [_row("queued_one")], "synced_until": "",
            }))
            (root / "discovery_requalify.json").write_text(json.dumps({
                "batches": [], "seeded": [],
                "names": {"queued_one": _rq("gate23-20261008", status="queued")},
            }))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                self.assertIsNone(ensure_auto_retire_unlocked(NOW))
                self.assertEqual([c["name"] for c in load_pool()["champions"]], ["queued_one"])
                self.assertEqual(load_retired()["applied"], [])
                self.assertFalse((root / "retired.json").exists())


class RetiredStaysOutTests(unittest.TestCase):
    def _retired_root(self, tmp: str) -> Path:
        from hedge_fund.trading.retire import ensure_auto_retire_unlocked

        root = Path(tmp)
        _seed(root)
        with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
            ensure_auto_retire_unlocked(NOW)
        return root

    def test_promote_and_ingest_do_not_readmit(self):
        from hedge_fund.trading.champions import load_pool, promote_candidates
        from hedge_fund.trading.ingest import ingest_discovery_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = self._retired_root(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                out = promote_candidates([{"strategy": "mom_36b_gt5pc"}, {"strategy": "h1_old"}])
                self.assertEqual(out["added"], [])
                res = ingest_discovery_payload({
                    "evaluations": [{
                        "strategy": "mom_36b_gt5pc",
                        "qualified": True,
                        "tested_at": "2026-10-08T12:00:00+00:00",
                        "sharpe": 0.5,
                        "trades": 40,
                        "test_pnl": 20.0,
                        "fail_reasons": [],
                        "timeframe": "5m",
                        "risk_policy": "rm_v1",
                    }],
                    "source": "windows_worker",
                })
                names = [c["name"] for c in load_pool()["champions"]]
            self.assertEqual(res["admitted"], [])
            self.assertNotIn("mom_36b_gt5pc", names)

    def test_claim_blocklist_includes_retired(self):
        from hedge_fund.trading.leases import _blocked_names

        with tempfile.TemporaryDirectory() as tmp:
            root = self._retired_root(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                blocked = _blocked_names([])
            # mom_36b_gt5pc was never in the tested index: without the retired
            # block it would look never-tested and be minted again.
            self.assertIn("mom_36b_gt5pc", blocked)
            self.assertIn("h1_old", blocked)

    def test_retired_accounts_leave_the_live_book(self):
        from hedge_fund.trading.open_lots import paper_book_dbs

        with tempfile.TemporaryDirectory() as tmp:
            root = self._retired_root(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                names = [Path(p).name for p in paper_book_dbs()]
            self.assertEqual(names, ["trades_newcomer.sqlite"])

    def test_all_retired_book_is_empty_not_legacy(self):
        from hedge_fund.trading.open_lots import paper_book_dbs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _db(root, "gone")
            TradeStore(root / "trades.sqlite")
            (root / "retired.json").write_text(json.dumps({"retired": {"gone": {"name": "gone"}}}))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                self.assertEqual(paper_book_dbs(), [])


class EmptyPoolHttpTests(unittest.TestCase):
    def test_champions_live_and_retired_respond_with_empty_pool(self):
        from hedge_fund.trading.retire import ensure_auto_retire_unlocked
        from hedge_fund.web.server import Handler

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [_row("only_one")], "synced_until": "",
            }))
            (root / "graduated.json").write_text(json.dumps([_grad("grad_one")]))
            (root / "discovery_requalify.json").write_text(json.dumps({
                "batches": [], "seeded": [],
                "names": {
                    "only_one": _rq("gate23-20261008"),
                    "grad_one": _rq("gate23-20261008"),
                },
            }))
            _db(root, "only_one")
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            port = httpd.server_address[1]
            try:
                with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                    ensure_auto_retire_unlocked(NOW)
                    with patch("hedge_fund.web.lot_health.fetch_signal_closes", return_value={}):
                        live = urlopen(f"http://127.0.0.1:{port}/api/live", timeout=5)
                        live_body = json.loads(live.read().decode())
                    champs = urlopen(f"http://127.0.0.1:{port}/api/champions", timeout=5)
                    champs_body = json.loads(champs.read().decode())
                    grads = urlopen(f"http://127.0.0.1:{port}/api/graduated", timeout=5)
                    grads_body = json.loads(grads.read().decode())
                    ret = urlopen(f"http://127.0.0.1:{port}/api/champions/retired", timeout=5)
                    ret_body = json.loads(ret.read().decode())
            finally:
                httpd.shutdown()
                thread.join(timeout=3)
                httpd.server_close()

        self.assertEqual(live.status, 200)
        self.assertEqual(live_body["lots"], [])
        self.assertEqual(live_body["positions"], [])
        self.assertEqual(live_body["open_lots"], 0)
        self.assertEqual(champs.status, 200)
        self.assertEqual(champs_body["active_champions"], [])
        self.assertEqual(champs_body["active_count"], 0)
        self.assertEqual(champs_body["graduated_count"], 0)
        self.assertEqual(champs_body["retired_count"], 2)
        self.assertEqual(grads.status, 200)
        self.assertEqual(grads_body, [])
        self.assertEqual(ret.status, 200)
        self.assertEqual(ret_body["retired_count"], 2)
        self.assertEqual({r["name"] for r in ret_body["retired"]}, {"only_one", "grad_one"})

    def test_live_runner_handles_empty_pool(self):
        from hedge_fund.trading.run_isolated import LIVE_STRATEGY, active_strategies

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({"champions": [], "synced_until": ""}))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                self.assertEqual(active_strategies(), [LIVE_STRATEGY])


class ServerWiringTests(unittest.TestCase):
    def test_server_starts_auto_retire(self):
        src = (Path(__file__).resolve().parents[1] / "hedge_fund" / "web" / "server.py").read_text()
        self.assertIn("start_auto_retire()", src)
        self.assertIn('route == "/api/champions/retired"', src)


if __name__ == "__main__":
    unittest.main()
