"""One-shot archive of orphan live accounts (batch orphan-live-20261008).

The 16 dip_* accounts dropped from the pool by the 2026-09-13 cull_undated
still sat on /api/live. They move to retired.json (archive, not delete):
trade DBs stay, the accounts leave the live book, and the names are blocked
from re-admit / re-mint. Champions, graduated, already-retired names, the
sma_stack fallback and names off the explicit list are never touched.
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

NOW = datetime(2026, 10, 8, 13, 0, tzinfo=timezone.utc)

ORPHAN_A = "dip_10b_lt5pc"
ORPHAN_B = "dip_12b_lt3pc&ema_abv_50"
ORPHAN_OPEN = "dip_8b_lt5pc"  # on the list but still holds a lot
UNLISTED = "dip_99b_lt9pc"  # orphan by rule but not on the explicit list


def _db(root: Path, name: str, lots: list | None = None, closed_pnl: float | None = None) -> Path:
    path = root / f"trades_{name}.sqlite"
    st = TradeStore(path)
    if closed_pnl is not None:
        tid = st.open_trade("BTC/USDT", "5m", name, 0.5, 100.0, 1.0, 0.1)
        st.close_trade(tid, 101.0, "tp", 0.1, closed_pnl, 0.01, 1 if closed_pnl > 0 else 0)
    st.save_account_state({"broker": {"cash": 10_000.0, "lots": lots or []}})
    st.close()
    return path


def _seed(root: Path) -> None:
    (root / "champions.json").write_text(json.dumps({
        "champions": [{"name": "keeper", "champion_since": "2026-10-08T00:00:00+00:00"}],
        "synced_until": "",
    }))
    (root / "graduated.json").write_text(json.dumps([{"name": "grad_one", "closed_trades": 80}]))
    (root / "retired.json").write_text(json.dumps({
        "retired": {"old_ret": {"name": "old_ret", "role": "champion", "batch_id": "requalify-gate23-20261008"}},
        "applied": ["requalify-gate23-20261008"],
        "batches": [{"batch_id": "requalify-gate23-20261008", "champions": 1, "graduated": 0}],
    }))
    _db(root, ORPHAN_A, closed_pnl=-12.5)
    _db(root, ORPHAN_B)
    _db(root, ORPHAN_OPEN, lots=[{"symbol": "BTC/USDT", "size": 0.1}])
    _db(root, UNLISTED)
    for name in ("sma_stack", "keeper", "grad_one", "old_ret"):
        _db(root, name)


def _env(root: Path):
    env = {k: v for k, v in os.environ.items() if k != "PAPER_STRATEGY"}
    env["PAPER_STATE"] = str(root)
    return patch.dict(os.environ, env, clear=True)


class OrphanListTests(unittest.TestCase):
    def test_explicit_list_is_the_16_prod_dip_accounts(self):
        from hedge_fund.trading.retire import ORPHAN_LIVE_BATCH, ORPHAN_LIVE_NAMES, ORPHAN_LIVE_REASON

        self.assertEqual(len(ORPHAN_LIVE_NAMES), 16)
        self.assertTrue(all(n.startswith("dip_") for n in ORPHAN_LIVE_NAMES))
        self.assertNotIn("sma_stack", ORPHAN_LIVE_NAMES)
        self.assertEqual(ORPHAN_LIVE_BATCH, "orphan-live-20261008")
        self.assertEqual(ORPHAN_LIVE_REASON, "orphan live account after cull_undated 2026-09-13")


class OrphanRetireTests(unittest.TestCase):
    def test_archives_only_listed_orphans_without_open_lots(self):
        from hedge_fund.trading.champions import load_graduated, load_pool, load_retired
        from hedge_fund.trading.retire import ORPHAN_LIVE_BATCH, ensure_orphan_live_retire_unlocked

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            with _env(root):
                out = ensure_orphan_live_retire_unlocked(NOW)
                state = load_retired()
                pool = load_pool()
                grads = load_graduated()

            self.assertEqual(out["retired_orphans"], sorted([ORPHAN_A, ORPHAN_B]))
            self.assertIn(ORPHAN_LIVE_BATCH, state["applied"])
            self.assertIn("requalify-gate23-20261008", state["applied"])
            self.assertIn("old_ret", state["retired"])
            for name in (ORPHAN_OPEN, UNLISTED, "sma_stack", "keeper", "grad_one"):
                self.assertNotIn(name, state["retired"])
            row = state["retired"][ORPHAN_A]
            self.assertEqual(row["role"], "orphan_live")
            self.assertEqual(row["batch_id"], ORPHAN_LIVE_BATCH)
            self.assertEqual(row["reason"], "orphan live account after cull_undated 2026-09-13")
            self.assertEqual(row["record"]["account_db"], f"trades_{ORPHAN_A}.sqlite")
            self.assertEqual(row["record"]["open_lots"], 0)
            self.assertEqual(row["record"]["closed"], 1)
            self.assertEqual(row["record"]["pnl"], -12.5)
            self.assertEqual(state["batches"][-1]["orphan_live"], 2)
            # Pool and graduated untouched.
            self.assertEqual([c["name"] for c in pool["champions"]], ["keeper"])
            self.assertEqual([g["name"] for g in grads], ["grad_one"])
            # Archive, not delete: every DB and its trade rows stay.
            for name in (ORPHAN_A, ORPHAN_B):
                self.assertTrue((root / f"trades_{name}.sqlite").exists())
            with TradeStore.open_readonly(root / f"trades_{ORPHAN_A}.sqlite") as st:
                self.assertEqual(len(st.all_trades()), 1)

    def test_runs_once(self):
        from hedge_fund.trading.champions import load_retired
        from hedge_fund.trading.retire import ensure_orphan_live_retire_unlocked

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            with _env(root):
                self.assertIsNotNone(ensure_orphan_live_retire_unlocked(NOW))
                before = (root / "retired.json").read_text()
                self.assertIsNone(ensure_orphan_live_retire_unlocked(NOW))
                self.assertEqual((root / "retired.json").read_text(), before)
                self.assertEqual(len(load_retired()["batches"]), 2)

    def test_no_orphans_is_noop_and_not_marked(self):
        from hedge_fund.trading.champions import load_retired
        from hedge_fund.trading.retire import ensure_orphan_live_retire_unlocked

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _db(root, "sma_stack")
            _db(root, UNLISTED)
            with _env(root):
                self.assertIsNone(ensure_orphan_live_retire_unlocked(NOW))
                self.assertEqual(load_retired()["applied"], [])
            self.assertFalse((root / "retired.json").exists())

    def test_listed_name_that_is_a_champion_again_is_kept(self):
        from hedge_fund.trading.retire import orphan_live_accounts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            (root / "champions.json").write_text(json.dumps({
                "champions": [{"name": ORPHAN_A}], "synced_until": "",
            }))
            with _env(root):
                found = orphan_live_accounts()
            self.assertNotIn(ORPHAN_A, found)
            self.assertIn(ORPHAN_B, found)

    def test_fallback_never_selected_even_if_listed(self):
        from hedge_fund.trading.retire import orphan_live_accounts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            with _env(root):
                found = orphan_live_accounts(allow={"sma_stack", ORPHAN_A})
            self.assertEqual(sorted(found), [ORPHAN_A])


class OrphanStaysOutTests(unittest.TestCase):
    def _root(self, tmp: str) -> Path:
        from hedge_fund.trading.retire import ensure_orphan_live_retire_unlocked

        root = Path(tmp)
        _seed(root)
        with _env(root):
            ensure_orphan_live_retire_unlocked(NOW)
        return root

    def test_leave_live_book_and_open_lot_counts(self):
        from hedge_fund.trading.open_lots import open_lots_snapshot, paper_book_dbs

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            with _env(root):
                names = {Path(p).name for p in paper_book_dbs()}
                snap = open_lots_snapshot()
            self.assertNotIn(f"trades_{ORPHAN_A}.sqlite", names)
            self.assertNotIn(f"trades_{ORPHAN_B}.sqlite", names)
            self.assertIn("trades_sma_stack.sqlite", names)
            self.assertNotIn(ORPHAN_A, snap["by_account"])
            self.assertIn("sma_stack", snap["by_account"])

    def test_blocked_from_readmit_and_remint(self):
        from hedge_fund.trading.champions import load_pool, promote_candidates
        from hedge_fund.trading.leases import _blocked_names

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            with _env(root):
                out = promote_candidates([{"strategy": ORPHAN_A}, {"strategy": ORPHAN_B}])
                blocked = _blocked_names([])
                names = [c["name"] for c in load_pool()["champions"]]
            self.assertEqual(out["added"], [])
            self.assertNotIn(ORPHAN_A, names)
            self.assertIn(ORPHAN_A, blocked)
            self.assertIn(ORPHAN_B, blocked)

    def test_live_and_retired_http(self):
        from hedge_fund.web.server import Handler

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            port = httpd.server_address[1]
            try:
                with _env(root):
                    with patch("hedge_fund.web.lot_health.fetch_signal_closes", return_value={}):
                        live = json.loads(urlopen(f"http://127.0.0.1:{port}/api/live", timeout=5).read())
                    ret = json.loads(urlopen(f"http://127.0.0.1:{port}/api/champions/retired", timeout=5).read())
            finally:
                httpd.shutdown()
                thread.join(timeout=3)
                httpd.server_close()

        self.assertNotIn(ORPHAN_A, live["open_lots_by_account"])
        self.assertNotIn(ORPHAN_B, live["open_lots_by_account"])
        self.assertIn("sma_stack", live["open_lots_by_account"])
        self.assertEqual(ret["retired_count"], 3)
        rows = {r["name"]: r for r in ret["retired"]}
        self.assertEqual(rows[ORPHAN_A]["role"], "orphan_live")
        self.assertEqual(rows[ORPHAN_A]["pnl"], -12.5)
        self.assertEqual(rows[ORPHAN_A]["closed"], 1)


class ServerWiringTests(unittest.TestCase):
    def test_startup_thread_runs_orphan_batch(self):
        src = (Path(__file__).resolve().parents[1] / "hedge_fund" / "trading" / "retire.py").read_text()
        start = src.index("def start_auto_retire")
        self.assertIn("run_orphan_live_retire()", src[start:])


if __name__ == "__main__":
    unittest.main()
