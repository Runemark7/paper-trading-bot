"""One-shot approved admit of a rules-v3 requalify pass (rules-v3-admit-20261009).

The requalify lane never admits. Alexander approved one name at
2026-10-09 07:41 Stockholm: dip_204b_lt8pc&h4_ema_abv_150, which passed
the tiled87 gate in batch rules-v3-tiled-20261008. A startup one-shot
seats it from the stored requalify verdict, records provenance, and
writes a marker so it never runs twice. Nothing else is touched.
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

from hedge_fund.trading.constants import GATE_RULES

NOW = datetime(2026, 10, 9, 6, 0, tzinfo=timezone.utc)
NAME = "dip_204b_lt8pc&h4_ema_abv_150"
OTHER = "dip_222b_lt8pc&h4_ema_abv_150"


def _result(qualified: bool = True, gate_rules: str = GATE_RULES) -> dict:
    return {
        "qualified": qualified,
        "fail_reasons": [] if qualified else ["daily_sharpe"],
        "sharpe": 0.4,
        "trades": 102,
        "test_pnl": 1765.7,
        "win_rate_pct": 47.1,
        "daily_sharpe": 0.504,
        "bh_daily_sharpe": 0.415,
        "avg_hold_hours": 7.29,
        "gate_rules": gate_rules,
        "regimes_tested": 23,
        "tested_at": "2026-10-08T20:40:00+00:00",
        "worker_id": "omarchy-771037",
        "data_end": 1791417600000,
    }


def _seed(root: Path, *, meta: dict | None = None, champions: list | None = None, retired: dict | None = None) -> None:
    (root / "champions.json").write_text(json.dumps({
        "champions": champions if champions is not None else [
            {"name": OTHER, "source": "5m_qualification_filter", "champion_since": "2026-10-09T03:43:40+00:00"}
        ],
        "synced_until": "2026-10-07T17:48:50+00:00",
    }))
    (root / "graduated.json").write_text(json.dumps([]))
    (root / "retired.json").write_text(json.dumps({
        "retired": retired or {}, "applied": [], "batches": [],
    }))
    names = {}
    if meta is not None:
        names[NAME] = meta
    (root / "discovery_requalify.json").write_text(json.dumps({
        "batches": [], "names": names, "seeded": ["rules-v3-tiled-20261008"], "paper_only": True,
    }))


def _done(result: dict | None = None) -> dict:
    return {
        "batch_id": "rules-v3-tiled-20261008",
        "status": "done",
        "attempts": 1,
        "result": result or _result(),
        "recorded_at": "2026-10-08T20:41:00+00:00",
    }


def _env(root: Path):
    env = dict(os.environ)
    env["PAPER_STATE"] = str(root)
    return patch.dict(os.environ, env, clear=True)


def _pool(root: Path) -> list[dict]:
    return json.loads((root / "champions.json").read_text())["champions"]


class ConstantsTests(unittest.TestCase):
    def test_explicit_single_approved_name(self):
        from hedge_fund.trading.approved_admit import APPROVED_ADMITS, APPROVED_ADMIT_BATCH

        self.assertEqual(APPROVED_ADMIT_BATCH, "rules-v3-admit-20261009")
        self.assertEqual([a["name"] for a in APPROVED_ADMITS], [NAME])
        self.assertEqual(APPROVED_ADMITS[0]["requalify_batch"], "rules-v3-tiled-20261008")
        self.assertEqual(APPROVED_ADMITS[0]["approved_by"], "Alexander")
        self.assertEqual(APPROVED_ADMITS[0]["approved_at"], "2026-10-09T07:41:00+02:00")


class ApprovedAdmitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        from hedge_fund.trading.approved_admit import ensure_approved_admits_unlocked

        with _env(self.root):
            return ensure_approved_admits_unlocked(NOW)

    def test_seats_name_with_provenance_and_keeps_existing(self):
        _seed(self.root, meta=_done())
        out = self._run()
        self.assertEqual(out["admitted"], [NAME])
        pool = _pool(self.root)
        self.assertEqual([c["name"] for c in pool], [OTHER, NAME])
        self.assertEqual(pool[0]["source"], "5m_qualification_filter")
        row = pool[1]
        self.assertEqual(row["source"], "requalify_approved_admit")
        self.assertEqual(row["sharpe_qual"], 0.4)
        self.assertEqual(row["winrate_qual"], 47.1)
        self.assertEqual(row["closed"], 0)
        self.assertEqual(row["pnl"], 0.0)
        self.assertEqual(row["admitted_at"], NOW.isoformat())
        self.assertEqual(row["champion_since"], NOW.isoformat())
        prov = row["provenance"]
        self.assertEqual(prov["admit_batch"], "rules-v3-admit-20261009")
        self.assertEqual(prov["requalify_batch"], "rules-v3-tiled-20261008")
        self.assertEqual(prov["approved_by"], "Alexander")
        self.assertEqual(prov["approved_at"], "2026-10-09T07:41:00+02:00")
        self.assertEqual(prov["gate_rules"], GATE_RULES)
        self.assertEqual(prov["trades"], 102)
        self.assertEqual(prov["daily_sharpe"], 0.504)
        self.assertEqual(prov["bh_daily_sharpe"], 0.415)
        self.assertEqual(prov["test_pnl"], 1765.7)
        self.assertEqual(prov["worker_id"], "omarchy-771037")
        self.assertEqual(json.loads((self.root / "champions.json").read_text())["synced_until"],
                         "2026-10-07T17:48:50+00:00")

    def test_idempotent_marker(self):
        _seed(self.root, meta=_done())
        self.assertIsNotNone(self._run())
        self.assertIsNone(self._run())
        self.assertEqual([c["name"] for c in _pool(self.root)], [OTHER, NAME])
        marker = json.loads((self.root / "approved_admits.json").read_text())
        self.assertIn("rules-v3-admit-20261009", marker["applied"])

    def test_previous_verdict_is_used_after_reenqueue(self):
        meta = {"batch_id": "later-batch", "status": "queued", "attempts": 0,
                "previous": {"batch_id": "rules-v3-tiled-20261008", "result": _result(),
                             "recorded_at": "2026-10-08T20:41:00+00:00"}}
        _seed(self.root, meta=meta)
        self.assertEqual(self._run()["admitted"], [NAME])

    def test_refuses_without_a_current_pass(self):
        cases = {
            "missing": None,
            "queued": {"batch_id": "rules-v3-tiled-20261008", "status": "queued", "attempts": 0},
            "failed": _done(_result(qualified=False)),
            "old_tag": _done(_result(gate_rules="sltp_cap100_bhdsr_20261008")),
            "wrong_batch": dict(_done(), batch_id="rules-v2-nearmiss-20261008"),
        }
        for label, meta in cases.items():
            with self.subTest(label):
                _seed(self.root, meta=meta)
                out = self._run()
                self.assertIsNone(out)
                self.assertEqual([c["name"] for c in _pool(self.root)], [OTHER])
                self.assertFalse((self.root / "approved_admits.json").exists())

    def test_retired_name_is_not_admitted(self):
        _seed(self.root, meta=_done(), retired={NAME: {"name": NAME, "role": "champion"}})
        out = self._run()
        self.assertEqual(out["admitted"], [])
        self.assertEqual(out["skipped"], [{"strategy": NAME, "reason": "retired"}])
        self.assertEqual([c["name"] for c in _pool(self.root)], [OTHER])

    def test_already_champion_marks_applied_without_duplicate(self):
        _seed(self.root, meta=_done(), champions=[{"name": NAME, "source": "x"}])
        out = self._run()
        self.assertEqual(out["admitted"], [])
        self.assertEqual(len(_pool(self.root)), 1)
        self.assertIsNone(self._run())

    def test_requalify_state_and_log_untouched(self):
        _seed(self.root, meta=_done())
        before = (self.root / "discovery_requalify.json").read_text()
        self._run()
        self.assertEqual((self.root / "discovery_requalify.json").read_text(), before)
        self.assertFalse((self.root / "discovery_log.json").exists())
        self.assertEqual(json.loads((self.root / "retired.json").read_text())["retired"], {})


class StartupAndApiTests(unittest.TestCase):
    def test_server_startup_runs_the_one_shot(self):
        src = (Path(__file__).resolve().parents[1] / "hedge_fund/web/server.py").read_text()
        self.assertIn("start_approved_admits()", src)

    def test_champions_api_shows_source_and_provenance(self):
        from hedge_fund.trading.approved_admit import run_approved_admits

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root, meta=_done())
            with _env(root):
                run_approved_admits()
                from hedge_fund.web.server import Handler

                httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                t = threading.Thread(target=httpd.serve_forever, daemon=True)
                t.start()
                try:
                    port = httpd.server_address[1]
                    data = json.loads(urlopen(f"http://127.0.0.1:{port}/api/champions", timeout=10).read())
                finally:
                    httpd.shutdown()
                    httpd.server_close()
        rows = {r["name"]: r for r in data["active_champions"]}
        self.assertEqual(set(rows), {OTHER, NAME})
        self.assertEqual(rows[NAME]["source"], "requalify_approved_admit")
        self.assertEqual(rows[NAME]["provenance"]["approved_by"], "Alexander")
        self.assertEqual(rows[OTHER]["source"], "5m_qualification_filter")


if __name__ == "__main__":
    unittest.main()
