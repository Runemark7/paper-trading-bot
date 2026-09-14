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

from hedge_fund.trading.constants import QUAL_N_WINDOWS
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

    def test_requalifies_window_veto_only_from_stored_aggregates(self):
        parked_pass = _eval(
            "window_veto_only",
            qualified=False,
            tested_at="2026-09-01T00:00:00+00:00",
            sharpe=0.58,
            trades=296,
            test_pnl=946.0,
            bh_oos_pnl=100.0,
            sma_stack_oos_pnl=50.0,
            regimes_tested=QUAL_N_WINDOWS,
            fail_reasons=[
                "window[1] failed/skipped/neg/empty",
                "not all windows non-negative",
            ],
        )
        parked_bh = _eval(
            "dbl_bot_120",
            qualified=False,
            tested_at="2026-09-01T00:00:00+00:00",
            sharpe=0.58,
            trades=296,
            test_pnl=946.0,
            bh_oos_pnl=2000.0,
            sma_stack_oos_pnl=50.0,
            regimes_tested=QUAL_N_WINDOWS,
            fail_reasons=[
                "window[1] failed/skipped/neg/empty",
                "not all windows non-negative",
                "oos_pnl 946.00 <= bh 2000.00",
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(json.dumps([parked_pass, parked_bh]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                out = ingest_discovery_payload({"evaluations": [], "source": "windows_worker"})
                from hedge_fund.trading.champions import load_pool
                from hedge_fund.trading.discovery import load_discovery_log

                pool = load_pool()
                log = load_discovery_log()
        by_name = {r["strategy"]: r for r in log}
        names = [c["name"] for c in pool["champions"]]
        self.assertIn("window_veto_only", out["admitted"])
        self.assertIn("window_veto_only", out["requalified"])
        self.assertNotIn("dbl_bot_120", out["admitted"])
        self.assertNotIn("dbl_bot_120", out["requalified"])
        self.assertTrue(by_name["window_veto_only"]["qualified"])
        self.assertFalse(by_name["dbl_bot_120"]["qualified"])
        self.assertIn("window_veto_only", names)
        self.assertNotIn("dbl_bot_120", names)
        self.assertEqual(by_name["window_veto_only"]["fail_reasons"], [])

    def test_force_admit_seats_parked_beat_bh_fail(self):
        parked = _eval(
            "dbl_bot_120",
            qualified=False,
            tested_at="2026-09-01T00:00:00+00:00",
            sharpe=0.58,
            trades=296,
            test_pnl=946.0,
            bh_oos_pnl=2000.0,
            sma_stack_oos_pnl=50.0,
            regimes_tested=3,
            fail_reasons=[
                "window[1] failed/skipped/neg/empty",
                "not all windows non-negative",
                "oos_pnl 946.00 <= bh 2000.00",
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [
                    {"name": "keep_me", "closed": 4, "pnl": 1.5, "wins": 2},
                ],
                "synced_until": "",
            }))
            (root / "discovery_log.json").write_text(json.dumps([parked]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                out = ingest_discovery_payload({
                    "paper_only": True,
                    "force_admit": ["dbl_bot_120"],
                    "source": "manual_force_admit",
                })
                from hedge_fund.trading.champions import load_pool
                from hedge_fund.trading.discovery import load_discovery_log

                pool = load_pool()
                log = load_discovery_log()
        names = [c["name"] for c in pool["champions"]]
        row = next(r for r in log if r["strategy"] == "dbl_bot_120")
        self.assertEqual(out["force_admitted"], ["dbl_bot_120"])
        self.assertEqual(out["admitted"], ["dbl_bot_120"])
        self.assertEqual(out["requalified"], [])
        self.assertIn("dbl_bot_120", names)
        self.assertIn("keep_me", names)
        self.assertTrue(row["qualified"])
        self.assertEqual(row["fail_reasons"], parked["fail_reasons"])
        self.assertTrue(row.get("force_admitted_at"))
        self.assertEqual(row.get("force_admit_source"), "manual_force_admit")
        champ = next(c for c in pool["champions"] if c["name"] == "dbl_bot_120")
        self.assertEqual(champ["timeframe"], "5m")
        self.assertEqual(champ["risk_policy"], "rm_v1")

    def test_force_admit_skips_existing_champion(self):
        parked = _eval(
            "dbl_bot_120",
            qualified=False,
            tested_at="2026-09-01T00:00:00+00:00",
            sharpe=0.58,
            trades=296,
            test_pnl=946.0,
            bh_oos_pnl=2000.0,
            fail_reasons=["oos_pnl 946.00 <= bh 2000.00"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [
                    {"name": "dbl_bot_120", "closed": 2, "pnl": 0.4, "wins": 1},
                ],
                "synced_until": "",
            }))
            (root / "discovery_log.json").write_text(json.dumps([parked]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                out = ingest_discovery_payload({
                    "force_admit": ["dbl_bot_120"],
                    "source": "manual_force_admit",
                })
                from hedge_fund.trading.champions import load_pool
                from hedge_fund.trading.discovery import load_discovery_log

                pool = load_pool()
                log = load_discovery_log()
        self.assertEqual(out["force_admitted"], [])
        self.assertEqual(out["admitted"], [])
        reasons = {s["strategy"]: s["reason"] for s in out["skipped"]}
        self.assertEqual(reasons["dbl_bot_120"], "already_pooled_or_graduated")
        self.assertEqual([c["name"] for c in pool["champions"]], ["dbl_bot_120"])
        self.assertFalse(log[0]["qualified"])
        self.assertNotIn("force_admitted_at", log[0])


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

    def test_force_admit_rejects_wrong_token(self):
        httpd, thread, port = self._start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = {
                    "PAPER_STATE": tmp,
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                }
                with patch.dict(os.environ, env):
                    body = json.dumps({
                        "paper_only": True,
                        "force_admit": ["dbl_bot_120"],
                        "source": "manual_force_admit",
                    }).encode()
                    bad = Request(
                        f"http://127.0.0.1:{port}/api/discovery/ingest",
                        data=body,
                        method="POST",
                        headers={"X-Discovery-Token": "nope", "Content-Type": "application/json"},
                    )
                    try:
                        urlopen(bad, timeout=3)
                        self.fail("expected HTTPError")
                    except HTTPError as err:
                        self.assertEqual(err.code, 401)
                    missing = Request(
                        f"http://127.0.0.1:{port}/api/discovery/ingest",
                        data=body,
                        method="POST",
                        headers={"Content-Type": "application/json"},
                    )
                    try:
                        urlopen(missing, timeout=3)
                        self.fail("expected HTTPError")
                    except HTTPError as err:
                        self.assertEqual(err.code, 401)
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


class FarmFlagTests(unittest.TestCase):
    def test_default_enabled_and_unseen_until_heartbeat(self):
        from hedge_fund.trading.farm import farm_status_block, set_farm_enabled
        from hedge_fund.web.discovery import build_discovery_summary

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                block = farm_status_block()
                self.assertTrue(block["enabled"])
                self.assertEqual(block["status"], "worker_unseen")
                self.assertFalse(block["worker_seen"])
                self.assertIn("Worker not seen", block["note"])

                paused = set_farm_enabled(False)
                self.assertFalse(paused["enabled"])
                self.assertEqual(paused["status"], "paused")

                resumed = set_farm_enabled(True)
                self.assertTrue(resumed["enabled"])

                with patch("hedge_fund.web.discovery.generate_universe", return_value=[]):
                    with patch("hedge_fund.trading.universe.generate_universe", return_value=[]):
                        s = build_discovery_summary()
                self.assertIn("farm", s)
                self.assertTrue(s["farm"]["enabled"])
                self.assertEqual(s["farm"]["status"], "worker_unseen")

    def test_heartbeat_marks_idle_or_running(self):
        from hedge_fund.trading.discovery import write_in_flight
        from hedge_fund.trading.farm import record_heartbeat, farm_status_block

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                record_heartbeat("idle")
                idle = farm_status_block(in_flight_active=False)
                self.assertTrue(idle["worker_seen"])
                self.assertEqual(idle["status"], "worker_idle")
                self.assertEqual(idle["heartbeat_status"], "idle")

                write_in_flight(["n1"], source="windows_worker")
                running = farm_status_block(in_flight_active=True)
                self.assertEqual(running["status"], "running")

    def test_ingest_heartbeat_does_not_flip_enabled(self):
        from hedge_fund.trading.farm import load_farm, set_farm_enabled
        from hedge_fund.trading.ingest import ingest_discovery_payload

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                set_farm_enabled(False)
                ingest_discovery_payload({
                    "evaluations": [],
                    "heartbeat": {"status": "paused", "source": "windows_worker"},
                    "clear_in_flight": True,
                })
                farm = load_farm()
        self.assertFalse(farm["enabled"])
        self.assertEqual(farm["heartbeat_status"], "paused")
        self.assertTrue(farm["heartbeat_at"])


class FarmHttpTests(unittest.TestCase):
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

    def test_unauthenticated_or_wrong_token_cannot_toggle_farm(self):
        """Anyone who can hit the public host must not pause the farm."""
        httpd, thread, port = self._start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = {
                    "PAPER_STATE": tmp,
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                }
                with patch.dict(os.environ, env):
                    from hedge_fund.trading.farm import load_farm, set_farm_enabled

                    set_farm_enabled(True)
                    url = f"http://127.0.0.1:{port}/api/discovery/farm"
                    body = b'{"enabled":false}'
                    no_auth = Request(
                        url,
                        data=body,
                        method="POST",
                        headers={"Content-Type": "application/json"},
                    )
                    try:
                        urlopen(no_auth, timeout=3)
                        self.fail("expected HTTPError for missing token")
                    except HTTPError as err:
                        self.assertEqual(err.code, 401)

                    wrong = Request(
                        url,
                        data=body,
                        method="POST",
                        headers={
                            "X-Discovery-Token": "nope",
                            "X-Paper-Discovery-Token": "also-nope",
                            "Authorization": "Bearer nope",
                            "Content-Type": "application/json",
                        },
                    )
                    try:
                        urlopen(wrong, timeout=3)
                        self.fail("expected HTTPError for wrong token")
                    except HTTPError as err:
                        self.assertEqual(err.code, 401)

                    self.assertTrue(load_farm()["enabled"])
                    summary = json.loads(urlopen(
                        f"http://127.0.0.1:{port}/api/discovery/summary",
                        timeout=3,
                    ).read().decode())
                    self.assertTrue(summary["farm"]["enabled"])
        finally:
            self._stop(httpd, thread)

    def test_farm_toggle_requires_token_and_shows_on_summary(self):
        httpd, thread, port = self._start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = {
                    "PAPER_STATE": tmp,
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                }
                with patch.dict(os.environ, env):
                    bad = Request(
                        f"http://127.0.0.1:{port}/api/discovery/farm",
                        data=b'{"enabled":false}',
                        method="POST",
                        headers={"X-Discovery-Token": "nope", "Content-Type": "application/json"},
                    )
                    try:
                        urlopen(bad, timeout=3)
                        self.fail("expected HTTPError")
                    except HTTPError as err:
                        self.assertEqual(err.code, 401)

                    paper_hdr = Request(
                        f"http://127.0.0.1:{port}/api/discovery/farm",
                        data=b'{"enabled":false}',
                        method="POST",
                        headers={
                            "X-Paper-Discovery-Token": "paper-secret-token",
                            "Content-Type": "application/json",
                        },
                    )
                    raw = urlopen(paper_hdr, timeout=3)
                    body = json.loads(raw.read().decode())
                    self.assertTrue(body["ok"])
                    self.assertFalse(body["farm"]["enabled"])
                    self.assertEqual(body["farm"]["status"], "paused")

                    good = Request(
                        f"http://127.0.0.1:{port}/api/discovery/farm",
                        data=b'{"enabled":true}',
                        method="POST",
                        headers={
                            "Authorization": "Bearer paper-secret-token",
                            "Content-Type": "application/json",
                        },
                    )
                    raw = urlopen(good, timeout=3)
                    body = json.loads(raw.read().decode())
                    self.assertTrue(body["ok"])
                    self.assertTrue(body["farm"]["enabled"])

                    summary = json.loads(urlopen(
                        f"http://127.0.0.1:{port}/api/discovery/summary",
                        timeout=3,
                    ).read().decode())
                    self.assertTrue(summary["farm"]["enabled"])
        finally:
            self._stop(httpd, thread)


class WorkerPauseTests(unittest.TestCase):
    def test_farm_enabled_from_summary_and_poll_keeps_last_known(self):
        from hedge_fund.trading.farm import farm_enabled_from_summary
        from scripts.discovery_worker import poll_farm_enabled

        self.assertTrue(farm_enabled_from_summary({"farm": {"enabled": True}}))
        self.assertFalse(farm_enabled_from_summary({"farm": {"enabled": False}}))
        self.assertTrue(farm_enabled_from_summary({}, last_known=True))
        self.assertFalse(farm_enabled_from_summary(None, last_known=False))

        worker_src = (Path(__file__).resolve().parents[1] / "scripts" / "discovery_worker.py").read_text()
        self.assertIn("/api/discovery/summary?compact=1", worker_src)
        self.assertIn("/api/discovery/summary", worker_src.split("bootstrap_from_prod")[1].split("def _plan_batch")[0])

        with patch(
            "scripts.discovery_worker._http_json",
            side_effect=RuntimeError("down"),
        ):
            self.assertTrue(poll_farm_enabled("http://example.invalid", True))
            self.assertFalse(poll_farm_enabled("http://example.invalid", False))

        with patch(
            "scripts.discovery_worker._http_json",
            return_value={"farm": {"enabled": False}},
        ):
            self.assertFalse(poll_farm_enabled("http://example.invalid", True))

    def test_consider_pause_idles_then_run_resumes(self):
        from scripts.discovery_worker import consider_pause

        slept = []
        posts = []

        def sleeper(n):
            slept.append(n)

        with patch("scripts.discovery_worker._post_ingest", side_effect=lambda *a, **k: posts.append(k) or {}):
            with patch("scripts.discovery_worker.clear_in_flight") as clear:
                self.assertEqual(
                    consider_pause(
                        False,
                        once=False,
                        ingest_url="http://prod/api/discovery/ingest",
                        token="t",
                        pause_sleep=15,
                        sleeper=sleeper,
                    ),
                    "pause",
                )
                clear.assert_called_once()
                self.assertEqual(slept, [15])
                self.assertEqual(posts[-1].get("heartbeat"), "paused")
                self.assertTrue(posts[-1].get("clear"))

                self.assertEqual(
                    consider_pause(
                        True,
                        once=False,
                        ingest_url="http://prod/api/discovery/ingest",
                        token="t",
                        pause_sleep=15,
                        sleeper=sleeper,
                    ),
                    "run",
                )
                self.assertEqual(len(slept), 1)

                self.assertEqual(
                    consider_pause(
                        False,
                        once=True,
                        ingest_url=None,
                        token=None,
                        pause_sleep=15,
                        sleeper=sleeper,
                    ),
                    "exit",
                )

    def test_main_once_skips_batch_when_paused(self):
        from scripts import discovery_worker

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crypto_history_5m.json").write_text("{}")
            env = {
                "PAPER_STATE": str(root),
                "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
            }
            with patch.dict(os.environ, env):
                with patch.object(discovery_worker, "poll_farm_enabled", return_value=False):
                    with patch.object(discovery_worker, "run_batch") as batch:
                        with patch.object(discovery_worker, "_post_ingest", return_value={}):
                            rc = discovery_worker.main(["--once", "--workers", "1"])
        self.assertEqual(rc, 0)
        batch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
