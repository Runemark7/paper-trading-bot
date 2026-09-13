"""Token-gated champion retain / cull_undated: pool only, no trade-DB delete."""
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
from urllib.request import Request, urlopen

from hedge_fund.trading.store import TradeStore

REPO = Path(__file__).resolve().parents[1]


def _pool(*rows: dict) -> dict:
    return {"champions": list(rows), "synced_until": ""}


def _row(name: str, since: str | None = None) -> dict:
    out = {"name": name, "closed": 0, "pnl": 0.0, "wins": 0}
    if since is not None:
        out["champion_since"] = since
    return out


def _write_trade_db(root: Path, name: str) -> Path:
    path = root / f"trades_{name}.sqlite"
    store = TradeStore(path)
    store.open_trade("BTC/USDT", "5m", "test_long", 0.6, 100.0, 0.01, 0.01, lot_id=1)
    return path


class RetainChampionsHelperTests(unittest.TestCase):
    def test_retain_keeps_named_rows_drops_rest_leaves_dbs(self):
        from hedge_fund.trading.champions import load_pool, retain_champions

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                (root / "champions.json").write_text(json.dumps(_pool(
                    _row("dated_oos", "2026-09-01T00:00:00+00:00"),
                    _row("dated_force", "2026-09-12T08:00:00+00:00"),
                    _row("undated"),
                    _row("also_undated", ""),
                )))
                dated_db = _write_trade_db(root, "dated_oos")
                undated_db = _write_trade_db(root, "undated")
                out = retain_champions({"dated_oos", "dated_force", "ghost"})
                self.assertTrue(out["ok"])
                self.assertTrue(out["paper_only"])
                self.assertEqual(out["action"], "retain")
                self.assertEqual(out["kept"], ["dated_oos", "dated_force"])
                self.assertEqual(out["removed"], ["undated", "also_undated"])
                self.assertEqual(out["active_count"], 2)
                pool = load_pool()
                self.assertEqual(
                    [c["name"] for c in pool["champions"]],
                    ["dated_oos", "dated_force"],
                )
                self.assertTrue(dated_db.exists())
                self.assertTrue(undated_db.exists())

    def test_empty_keep_drops_all_active_names(self):
        from hedge_fund.trading.champions import load_pool, retain_champions

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                (root / "champions.json").write_text(json.dumps(_pool(
                    _row("keep_me", "2026-09-01T00:00:00+00:00"),
                    _row("drop_me"),
                )))
                out = retain_champions(set())
                self.assertEqual(out["kept"], [])
                self.assertEqual(out["removed"], ["keep_me", "drop_me"])
                self.assertEqual(out["active_count"], 0)
                self.assertEqual(load_pool()["champions"], [])


class CullUndatedHelperTests(unittest.TestCase):
    def test_cull_keeps_nonempty_since_including_force_admit(self):
        from hedge_fund.trading.champions import cull_undated_champions, load_pool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                (root / "champions.json").write_text(json.dumps(_pool(
                    _row("dated_oos", "2026-09-01T00:00:00+00:00"),
                    _row("dated_force", "2026-09-12T08:00:00+00:00"),
                    _row("missing"),
                    _row("blank", ""),
                    _row("spaces", "   "),
                    {**_row("null_since"), "champion_since": None},
                )))
                missing_db = _write_trade_db(root, "missing")
                out = cull_undated_champions()
                self.assertEqual(out["action"], "cull_undated")
                self.assertEqual(out["kept"], ["dated_oos", "dated_force"])
                self.assertEqual(out["removed"], ["missing", "blank", "spaces", "null_since"])
                self.assertEqual(out["active_count"], 2)
                self.assertTrue(missing_db.exists())
                names = [c["name"] for c in load_pool()["champions"]]
                self.assertEqual(names, ["dated_oos", "dated_force"])

    def test_cull_does_not_backfill_then_keep(self):
        from hedge_fund.trading.champions import cull_undated_champions, load_pool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                (root / "champions.json").write_text(json.dumps(_pool(
                    _row("grandpa"),
                )))
                _write_trade_db(root, "grandpa")
                (root / "discovery_log.json").write_text(json.dumps([{
                    "strategy": "grandpa",
                    "tested_at": "2026-07-01T00:00:00+00:00",
                    "qualified": True,
                }]))
                out = cull_undated_champions()
                self.assertEqual(out["removed"], ["grandpa"])
                self.assertEqual(out["active_count"], 0)
                self.assertEqual(load_pool()["champions"], [])


class ChampionOpsHttpTests(unittest.TestCase):
    def _start(self):
        from hedge_fund.web.server import Handler

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread, httpd.server_address[1]

    def _stop(self, httpd, thread):
        httpd.shutdown()
        thread.join(timeout=3)
        httpd.server_close()

    def _post(self, port: int, route: str, body: dict, headers: dict | None = None):
        req = Request(
            f"http://127.0.0.1:{port}{route}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        return urlopen(req, timeout=3)

    def test_disabled_without_token(self):
        httpd, thread, port = self._start()
        try:
            with patch.dict(os.environ, {"PAPER_DISCOVERY_INGEST_TOKEN": ""}, clear=False):
                os.environ.pop("PAPER_DISCOVERY_INGEST_TOKEN", None)
                for route in ("/api/champions/retain", "/api/champions/cull_undated"):
                    try:
                        self._post(port, route, {"keep": []})
                        self.fail("expected HTTPError")
                    except HTTPError as err:
                        self.assertEqual(err.code, 503)
        finally:
            self._stop(httpd, thread)

    def test_rejects_bad_token(self):
        httpd, thread, port = self._start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = {
                    "PAPER_STATE": tmp,
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                }
                with patch.dict(os.environ, env):
                    for route in ("/api/champions/retain", "/api/champions/cull_undated"):
                        try:
                            self._post(
                                port,
                                route,
                                {"keep": ["sma_stack"]},
                                {"X-Discovery-Token": "nope"},
                            )
                            self.fail("expected HTTPError")
                        except HTTPError as err:
                            self.assertEqual(err.code, 401)
        finally:
            self._stop(httpd, thread)

    def test_retain_requires_keep_list(self):
        httpd, thread, port = self._start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = {
                    "PAPER_STATE": tmp,
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                }
                with patch.dict(os.environ, env):
                    try:
                        self._post(
                            port,
                            "/api/champions/retain",
                            {},
                            {"Authorization": "Bearer paper-secret-token"},
                        )
                        self.fail("expected HTTPError")
                    except HTTPError as err:
                        self.assertEqual(err.code, 400)
                        body = json.loads(err.read().decode())
                        self.assertIn("keep", body["error"])
        finally:
            self._stop(httpd, thread)

    def test_retain_and_cull_accept_good_token(self):
        httpd, thread, port = self._start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                env = {
                    "PAPER_STATE": str(root),
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                }
                with patch.dict(os.environ, env):
                    (root / "champions.json").write_text(json.dumps(_pool(
                        _row("dated_oos", "2026-09-01T00:00:00+00:00"),
                        _row("undated"),
                    )))
                    _write_trade_db(root, "undated")
                    raw = self._post(
                        port,
                        "/api/champions/retain",
                        {"keep": ["dated_oos"]},
                        {"Authorization": "Bearer paper-secret-token"},
                    )
                    body = json.loads(raw.read().decode())
                    self.assertEqual(raw.status, 200)
                    self.assertEqual(body["kept"], ["dated_oos"])
                    self.assertEqual(body["removed"], ["undated"])
                    self.assertEqual(body["active_count"], 1)
                    self.assertTrue((root / "trades_undated.sqlite").exists())

                    (root / "champions.json").write_text(json.dumps(_pool(
                        _row("dated_force", "2026-09-12T08:00:00+00:00"),
                        _row("before_dating"),
                    )))
                    raw = self._post(
                        port,
                        "/api/champions/cull_undated",
                        {},
                        {"X-Paper-Discovery-Token": "paper-secret-token"},
                    )
                    body = json.loads(raw.read().decode())
                    self.assertEqual(raw.status, 200)
                    self.assertEqual(body["action"], "cull_undated")
                    self.assertEqual(body["kept"], ["dated_force"])
                    self.assertEqual(body["removed"], ["before_dating"])
                    get_raw = urlopen(f"http://127.0.0.1:{port}/api/champions", timeout=3)
                    get_body = json.loads(get_raw.read().decode())
                    self.assertEqual(
                        [c["name"] for c in get_body["active_champions"]],
                        ["dated_force"],
                    )
        finally:
            self._stop(httpd, thread)


class ChampionOpsContractTests(unittest.TestCase):
    def test_server_registers_token_gated_routes(self):
        src = (REPO / "hedge_fund" / "web" / "server.py").read_text()
        self.assertIn('route in ("/api/champions/retain", "/api/champions/cull_undated")', src)
        self.assertIn("_discovery_ingest_authorized", src)
        self.assertIn("retain_champions", src)
        self.assertIn("cull_undated_champions", src)

    def test_get_champions_stays_read_only(self):
        src = (REPO / "hedge_fund" / "web" / "server.py").read_text()
        champs_get = src.split('route == "/api/champions"')[1].split("elif route")[0]
        self.assertIn("read_only=True", champs_get)
        self.assertNotIn("retain_champions", champs_get)
        self.assertNotIn("cull_undated_champions", champs_get)
        self.assertNotIn("collect_live_results()", champs_get)


if __name__ == "__main__":
    unittest.main()
