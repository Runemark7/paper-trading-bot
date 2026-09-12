"""Windows discovery farm: cycle gate, ingest, fail-once, no champion cull."""
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

from hedge_fund.trading.discovery_mode import discovery_on_cycle, tokens_match
from hedge_fund.trading.ingest import ingest_discovery_payload


def _eval(name, *, qualified, tested_at="2026-09-12T10:00:00+00:00", **extra):
    row = {
        "strategy": name,
        "tested_at": tested_at,
        "qualified": qualified,
        "sharpe": 0.4 if qualified else 0.1,
        "trades": 40 if qualified else 5,
        "test_pnl": 12.0 if qualified else -3.0,
        "train_pnl": 8.0,
        "win_rate_pct": 50.0,
        "fail_reasons": [] if qualified else ["oos_sharpe 0.10 < 0.30"],
        "timeframe": "5m",
        "risk_policy": "rm_v1",
    }
    row.update(extra)
    return row


class DiscoveryModeTests(unittest.TestCase):
    def test_default_off(self):
        self.assertFalse(discovery_on_cycle({}))
        self.assertFalse(discovery_on_cycle({"DISCOVERY_ON_CYCLE": "0"}))
        self.assertFalse(discovery_on_cycle({"PAPER_DISCOVERY_MODE": "off"}))

    def test_explicit_on(self):
        self.assertTrue(discovery_on_cycle({"DISCOVERY_ON_CYCLE": "1"}))
        self.assertTrue(discovery_on_cycle({"PAPER_DISCOVERY_MODE": "on"}))
        self.assertFalse(discovery_on_cycle({
            "PAPER_DISCOVERY_MODE": "on",
            "DISCOVERY_ON_CYCLE": "0",
        }))

    def test_tokens_match(self):
        self.assertTrue(tokens_match("abc", "abc"))
        self.assertFalse(tokens_match("abc", "abd"))
        self.assertFalse(tokens_match("", "abc"))
        self.assertFalse(tokens_match("ab", "abc"))


class LiveCycleGateTests(unittest.TestCase):
    def test_skips_tournament_by_default(self):
        from scripts import live_cycle

        labels = []

        def fake_run(label, argv, *, critical=False):
            labels.append(label)
            return 0

        with patch.dict(os.environ, {"DISCOVERY_ON_CYCLE": "0", "PAPER_DISCOVERY_MODE": "off"}):
            with patch.object(live_cycle, "_run", side_effect=fake_run):
                with patch.object(live_cycle, "write_pipeline_stamp"):
                    rc = live_cycle.main()
        self.assertEqual(rc, 0)
        self.assertNotIn("tournament", labels)
        self.assertIn("run_isolated", labels)
        self.assertIn("collect_live_results", labels)
        self.assertIn("hourly_report", labels)

    def test_runs_tournament_when_enabled(self):
        from scripts import live_cycle

        labels = []

        def fake_run(label, argv, *, critical=False):
            labels.append(label)
            return 0

        with patch.dict(os.environ, {"DISCOVERY_ON_CYCLE": "1"}):
            with patch.object(live_cycle, "_run", side_effect=fake_run):
                with patch.object(live_cycle, "write_pipeline_stamp"):
                    live_cycle.main()
        self.assertEqual(labels[0], "tournament")
        self.assertIn("run_isolated", labels)


class IngestMergeTests(unittest.TestCase):
    def test_appends_fail_and_admits_qualified_without_cull(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [
                    {"name": "keep_me", "closed": 4, "pnl": 1.5, "wins": 2},
                ],
                "synced_until": "",
            }))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                out = ingest_discovery_payload({
                    "evaluations": [
                        _eval("fresh_fail", qualified=False),
                        _eval("fresh_pass", qualified=True, score=12.0),
                    ],
                    "extended_names": ["dip_6b_lt2pc&don_lo_48"],
                    "source": "windows_worker",
                })
                from hedge_fund.trading.champions import load_pool
                from hedge_fund.trading.discovery import load_discovery_log
                from hedge_fund.trading.refill import load_extended_names

                pool = load_pool()
                names = [c["name"] for c in pool["champions"]]
                log = load_discovery_log()
                extended = load_extended_names()
        self.assertEqual(out["ingested"], ["fresh_fail", "fresh_pass"])
        self.assertEqual(out["admitted"], ["fresh_pass"])
        self.assertIn("keep_me", names)
        self.assertIn("fresh_pass", names)
        self.assertNotIn("fresh_fail", names)
        self.assertEqual({r["strategy"] for r in log}, {"fresh_fail", "fresh_pass"})
        self.assertIn("dip_6b_lt2pc&don_lo_48", out["extended_added"])
        self.assertIn("dip_6b_lt2pc&don_lo_48", extended)

    def test_fail_once_skips_retest_and_does_not_cull(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [
                    {"name": "keep_me", "closed": 4, "pnl": 1.5, "wins": 2},
                ],
                "synced_until": "",
            }))
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("already_failed", qualified=False, tested_at="2026-09-01T00:00:00+00:00"),
            ]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                out = ingest_discovery_payload({
                    "evaluations": [
                        _eval("already_failed", qualified=True, score=99.0),
                        _eval("keep_me", qualified=False),
                    ],
                })
                from hedge_fund.trading.champions import load_pool
                from hedge_fund.trading.discovery import load_discovery_log

                pool = load_pool()
                log = load_discovery_log()
        self.assertEqual(out["ingested"], [])
        self.assertEqual(out["admitted"], [])
        reasons = {s["strategy"]: s["reason"] for s in out["skipped"]}
        self.assertEqual(reasons["already_failed"], "already_tested")
        self.assertEqual(reasons["keep_me"], "already_pooled_or_graduated")
        self.assertEqual([c["name"] for c in pool["champions"]], ["keep_me"])
        self.assertEqual(sum(1 for r in log if r["strategy"] == "already_failed"), 1)
        self.assertFalse(log[0]["qualified"])

    def test_rejects_wrong_tape(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                out = ingest_discovery_payload({
                    "evaluations": [
                        _eval("bad", qualified=True, timeframe="4h"),
                    ],
                })
        self.assertEqual(out["ingested"], [])
        self.assertEqual(out["rejected_invalid"], ["bad"])


class IngestHttpTests(unittest.TestCase):
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

    def test_disabled_without_token(self):
        httpd, thread, port = self._start()
        try:
            with patch.dict(os.environ, {"PAPER_DISCOVERY_INGEST_TOKEN": ""}, clear=False):
                os.environ.pop("PAPER_DISCOVERY_INGEST_TOKEN", None)
                try:
                    req = Request(
                        f"http://127.0.0.1:{port}/api/discovery/ingest",
                        data=b"{}",
                        method="POST",
                    )
                    urlopen(req, timeout=3)
                    self.fail("expected HTTPError")
                except HTTPError as err:
                    self.assertEqual(err.code, 503)
        finally:
            self._stop(httpd, thread)

    def test_rejects_bad_token_and_accepts_good(self):
        httpd, thread, port = self._start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = {
                    "PAPER_STATE": tmp,
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                }
                with patch.dict(os.environ, env):
                    bad = Request(
                        f"http://127.0.0.1:{port}/api/discovery/ingest",
                        data=b'{"evaluations":[]}',
                        method="POST",
                        headers={"X-Discovery-Token": "nope", "Content-Type": "application/json"},
                    )
                    try:
                        urlopen(bad, timeout=3)
                        self.fail("expected HTTPError")
                    except HTTPError as err:
                        self.assertEqual(err.code, 401)

                    good = Request(
                        f"http://127.0.0.1:{port}/api/discovery/ingest",
                        data=json.dumps({
                            "evaluations": [_eval("http_pass", qualified=True, score=1.0)],
                        }).encode(),
                        method="POST",
                        headers={
                            "Authorization": "Bearer paper-secret-token",
                            "Content-Type": "application/json",
                        },
                    )
                    raw = urlopen(good, timeout=3)
                    body = json.loads(raw.read().decode())
                    self.assertEqual(raw.status, 200)
                    self.assertEqual(body["admitted"], ["http_pass"])
                    self.assertTrue(body["paper_only"])
        finally:
            self._stop(httpd, thread)


class WorkerHelperTests(unittest.TestCase):
    def test_plan_batch_skips_tested_and_champs(self):
        from scripts.discovery_worker import _plan_batch

        leftovers = ["champ_a", "already", "fresh_one", "fresh_two"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [{"name": "champ_a", "closed": 0, "pnl": 0, "wins": 0}],
                "synced_until": "",
            }))
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("already", qualified=False),
            ]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with patch(
                    "scripts.discovery_worker.discovery_universe",
                    return_value=leftovers,
                ):
                    with patch(
                        "scripts.discovery_worker.untested_candidates",
                        return_value=["already", "fresh_one", "fresh_two"],
                    ):
                        with patch(
                            "scripts.discovery_worker.maybe_refill_discovery",
                            return_value=[],
                        ):
                            planned, rotated, added = _plan_batch(2)
        self.assertEqual(planned, ["fresh_one", "fresh_two"])
        self.assertEqual(rotated, ["fresh_one", "fresh_two"])
        self.assertEqual(added, [])
        self.assertNotIn("already", planned)
        self.assertNotIn("champ_a", planned)


if __name__ == "__main__":
    unittest.main()
