"""Prod claim/lease queue: disjoint claims, expiry, ingest, refill, fail-once."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hedge_fund.trading.constants import DISCOVERY_REFILL_BATCH_SIZE
from hedge_fund.trading.universe import near_duplicate_key


def _eval(name, *, qualified=False, tested_at="2026-10-07T12:00:00+00:00"):
    return {
        "strategy": name,
        "tested_at": tested_at,
        "qualified": qualified,
        "sharpe": 0.4 if qualified else 0.1,
        "trades": 40 if qualified else 5,
        "test_pnl": 1.0 if qualified else -1.0,
        "train_pnl": 1.0,
        "win_rate_pct": 50.0,
        "fail_reasons": [] if qualified else ["oos_sharpe 0.10 < 0.30"],
        "timeframe": "5m",
        "risk_policy": "rm_v1",
    }


def _names(n: int = 8) -> list[str]:
    return [f"don_hi_{6 + 6 * i}" for i in range(n)]


class LeaseQueueTests(unittest.TestCase):
    def _env(self, root: Path) -> dict:
        return {
            "PAPER_STATE": str(root),
            "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
        }

    def test_ttl_is_about_two_batch_times(self):
        from hedge_fund.trading.leases import lease_ttl_seconds

        with patch.dict(os.environ, {"DISCOVERY_EVAL_TIMEOUT_SECONDS": "600"}):
            self.assertEqual(lease_ttl_seconds(2, 2), 1200)
            self.assertEqual(lease_ttl_seconds(4, 2), 2400)
            self.assertEqual(lease_ttl_seconds(2, None), 1200)
        with patch.dict(os.environ, {"DISCOVERY_EVAL_TIMEOUT_SECONDS": "0"}):
            self.assertEqual(lease_ttl_seconds(1, 1), 1200)

    def test_two_workers_never_share_a_name(self):
        from hedge_fund.trading.leases import claim_discovery_batch, lease_snapshot

        universe = _names(8)
        barrier = threading.Barrier(2)
        results: dict[str, list[str]] = {}
        errors: list[BaseException] = []

        def grab(worker_id: str, n: int) -> None:
            try:
                barrier.wait(timeout=5)
                body = claim_discovery_batch(worker_id, n, parallel=n)
                results[worker_id] = list(body["names"])
            except BaseException as exc:
                errors.append(exc)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, self._env(Path(tmp))):
                with patch(
                    "hedge_fund.trading.leases.discovery_universe",
                    return_value=universe,
                ):
                    threads = [
                        threading.Thread(target=grab, args=("linux-1", 4)),
                        threading.Thread(target=grab, args=("jensa-2", 4)),
                    ]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join(timeout=20)
                        self.assertFalse(thread.is_alive(), "claim deadlocked")
                    self.assertEqual(errors, [])
                    left = set(results["linux-1"])
                    right = set(results["jensa-2"])
                    self.assertEqual(len(left), 4)
                    self.assertEqual(len(right), 4)
                    self.assertFalse(left & right)
                    self.assertEqual(left | right, set(universe))
                    snap = lease_snapshot()
                    self.assertEqual(set(snap["active_names"]), set(universe))
                    by_worker = {row["worker_id"]: set(row["in_flight"]) for row in snap["workers"]}
                    self.assertEqual(by_worker["linux-1"], left)
                    self.assertEqual(by_worker["jensa-2"], right)

    def test_expired_lease_is_reclaimable_and_release_is_per_worker(self):
        from hedge_fund.trading.leases import (
            claim_discovery_batch,
            lease_snapshot,
            release_discovery_leases,
        )

        universe = _names(2)
        t0 = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, self._env(Path(tmp))):
                with patch(
                    "hedge_fund.trading.leases.discovery_universe",
                    return_value=universe,
                ):
                    first = claim_discovery_batch("linux-1", 2, parallel=2, now=t0)
                    self.assertEqual(set(first["names"]), set(universe))
                    ttl = first["ttl_seconds"]
                    self.assertEqual(ttl, 1200)
                    with patch("hedge_fund.trading.leases.next_refill_batch", return_value=[]):
                        blocked = claim_discovery_batch(
                            "jensa-2",
                            2,
                            parallel=2,
                            now=t0 + timedelta(seconds=ttl - 1),
                        )
                    self.assertEqual(blocked["names"], [])
                    self.assertEqual(blocked["refilled"], [])
                    stolen = release_discovery_leases(
                        "jensa-2",
                        names=list(universe),
                        now=t0 + timedelta(seconds=10),
                    )
                    self.assertEqual(stolen["released"], [])
                    self.assertEqual(set(lease_snapshot(t0 + timedelta(seconds=10))["active_names"]), set(universe))
                    again = claim_discovery_batch(
                        "jensa-2",
                        2,
                        parallel=2,
                        now=t0 + timedelta(seconds=ttl + 1),
                    )
                    self.assertEqual(set(again["names"]), set(universe))
                    release_discovery_leases("jensa-2", now=t0 + timedelta(seconds=ttl + 2))
                    self.assertEqual(
                        lease_snapshot(t0 + timedelta(seconds=ttl + 2))["active_names"],
                        [],
                    )

    def test_ingest_clears_lease_and_fail_once_holds(self):
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch, lease_snapshot

        universe = _names(2)
        t0 = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, self._env(root)):
                with patch(
                    "hedge_fund.trading.leases.discovery_universe",
                    return_value=universe,
                ):
                    first = claim_discovery_batch("linux-1", 1, parallel=1, now=t0)
                    name = first["names"][0]
                    self.assertIn(name, lease_snapshot(t0)["active_names"])
                    ingest_discovery_payload({
                        "evaluations": [_eval(name, qualified=False)],
                        "heartbeat": {"status": "running", "worker_id": "linux-1"},
                        "source": "windows_worker",
                    })
                    self.assertNotIn(name, lease_snapshot(t0)["active_names"])
                    later = claim_discovery_batch(
                        "jensa-2",
                        2,
                        parallel=2,
                        now=t0 + timedelta(days=30),
                    )
                    self.assertNotIn(name, later["names"])
                    self.assertEqual(len(later["names"]), 2)
                    self.assertIn(
                        universe[1] if name == universe[0] else universe[0],
                        later["names"],
                    )

    def test_refill_on_empty_skips_tested_and_near_dups(self):
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.trading.refill import discovery_universe

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, self._env(root)):
                universe = discovery_universe()
                (root / "discovery_log.json").write_text(json.dumps([
                    _eval(name) for name in universe
                ]))
                tested_keys = {near_duplicate_key(name) for name in universe}
                first = claim_discovery_batch("linux-1", 1, parallel=1)
                self.assertEqual(len(first["names"]), 1)
                self.assertGreaterEqual(len(first["refilled"]), 1)
                self.assertEqual(len(first["refilled"]), DISCOVERY_REFILL_BATCH_SIZE)
                minted = first["names"][0]
                self.assertNotIn(minted, set(universe))
                self.assertNotIn(near_duplicate_key(minted), tested_keys)
                for name in first["refilled"]:
                    self.assertNotIn(name, set(universe))
                    self.assertNotIn(near_duplicate_key(name), tested_keys)
                second = claim_discovery_batch("jensa-2", 1, parallel=1)
                self.assertEqual(second["refilled"], [])
                self.assertEqual(len(second["names"]), 1)
                self.assertNotEqual(second["names"][0], minted)
                self.assertIn(second["names"][0], first["refilled"])

    def test_champions_are_not_leased_and_stop_claims_nothing(self):
        from hedge_fund.trading.farm import set_farm_enabled
        from hedge_fund.trading.leases import claim_discovery_batch

        universe = _names(2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [{"name": universe[0], "closed": 0, "pnl": 0, "wins": 0}],
                "synced_until": "",
            }))
            with patch.dict(os.environ, self._env(root)):
                with patch(
                    "hedge_fund.trading.leases.discovery_universe",
                    return_value=universe,
                ):
                    body = claim_discovery_batch("linux-1", 2, parallel=2)
                    self.assertEqual(body["names"][0], universe[1])
                    self.assertNotIn(universe[0], body["names"])
                    self.assertEqual(len(body["names"]), 2)
                    self.assertFalse(body["paused"])
                    set_farm_enabled(False)
                    paused = claim_discovery_batch("jensa-2", 2, parallel=2)
                    self.assertTrue(paused["paused"])
                    self.assertEqual(paused["names"], [])

    def test_summary_keeps_farm_inflight_counts_and_lists_workers(self):
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.web.discovery import build_discovery_summary

        universe = _names(2)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, self._env(Path(tmp))):
                with patch(
                    "hedge_fund.trading.leases.discovery_universe",
                    return_value=universe,
                ):
                    with patch("hedge_fund.web.discovery.generate_universe", return_value=universe):
                        with patch("hedge_fund.trading.universe.generate_universe", return_value=universe):
                            claim_discovery_batch("linux-1", 1, parallel=1)
                            claim_discovery_batch("jensa-2", 1, parallel=1)
                            full = build_discovery_summary()
                            compact = build_discovery_summary(lists=False)
        self.assertIn("farm", full)
        self.assertIn("in_flight", full)
        self.assertIn("counts", full)
        self.assertEqual(full["counts"]["leased"], 2)
        self.assertEqual(full["leases"]["active"], 2)
        self.assertTrue(full["in_flight"]["active"])
        self.assertEqual(full["in_flight"]["source"], "claim_queue")
        self.assertEqual(set(full["in_flight"]["names"]), set(universe))
        self.assertEqual({row["worker_id"] for row in full["workers"]}, {"linux-1", "jensa-2"})
        self.assertTrue(all(row["lease_count"] == 1 and row["in_flight"] for row in full["workers"]))
        self.assertEqual(full["farm"]["status"], "running")
        self.assertEqual(compact["in_flight"]["names"], [])
        self.assertEqual(compact["counts"]["leased"], 2)
        self.assertEqual(len(compact["workers"]), 2)
        self.assertTrue(compact["workers"][0]["in_flight"])
        for name in universe:
            self.assertNotIn(name, full["queued"])


class LeaseHttpTests(unittest.TestCase):
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

    def _post(self, port: int, route: str, payload: dict, token: str | None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Discovery-Token"] = token
        req = Request(
            f"http://127.0.0.1:{port}{route}",
            data=json.dumps(payload).encode(),
            method="POST",
            headers=headers,
        )
        raw = urlopen(req, timeout=10)
        return raw.status, json.loads(raw.read().decode())

    def test_claim_requires_token_and_concurrent_posts_are_disjoint(self):
        httpd, thread, port = self._start()
        try:
            universe = _names(8)
            with tempfile.TemporaryDirectory() as tmp:
                env = {
                    "PAPER_STATE": tmp,
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                    "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
                }
                with patch.dict(os.environ, env):
                    with patch(
                        "hedge_fund.trading.leases.discovery_universe",
                        return_value=universe,
                    ):
                        try:
                            self._post(port, "/api/discovery/claim", {"worker_id": "linux-1", "n": 1}, None)
                            self.fail("expected HTTPError")
                        except HTTPError as err:
                            self.assertEqual(err.code, 401)
                        try:
                            self._post(
                                port,
                                "/api/discovery/claim",
                                {"worker_id": "linux-1", "n": 1},
                                "nope",
                            )
                            self.fail("expected HTTPError")
                        except HTTPError as err:
                            self.assertEqual(err.code, 401)

                        results: list[list[str]] = []
                        errors: list[BaseException] = []
                        barrier = threading.Barrier(2)

                        def grab(worker_id: str) -> None:
                            try:
                                barrier.wait(timeout=5)
                                status, body = self._post(
                                    port,
                                    "/api/discovery/claim",
                                    {"worker_id": worker_id, "n": 4, "parallel": 4},
                                    "paper-secret-token",
                                )
                                self.assertEqual(status, 200)
                                results.append(list(body["names"]))
                            except BaseException as exc:
                                errors.append(exc)

                        threads = [
                            threading.Thread(target=grab, args=("linux-1",)),
                            threading.Thread(target=grab, args=("jensa-2",)),
                        ]
                        for item in threads:
                            item.start()
                        for item in threads:
                            item.join(timeout=20)
                            self.assertFalse(item.is_alive())
                        self.assertEqual(errors, [])
                        self.assertEqual(len(results), 2)
                        self.assertFalse(set(results[0]) & set(results[1]))
                        self.assertEqual(set(results[0]) | set(results[1]), set(universe))

                        status, released = self._post(
                            port,
                            "/api/discovery/release",
                            {"worker_id": "linux-1"},
                            "paper-secret-token",
                        )
                        self.assertEqual(status, 200)
                        self.assertTrue(set(released["released"]).issubset(set(universe)))
                        self.assertEqual(len(released["released"]), 4)
        finally:
            self._stop(httpd, thread)


class WorkerClaimModeTests(unittest.TestCase):
    def test_default_main_claims_and_local_plan_does_not(self):
        from scripts import discovery_worker

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crypto_history_5m.json").write_text("{}")
            env = {
                "PAPER_STATE": str(root),
                "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
            }
            with patch.dict(os.environ, env):
                with patch.object(discovery_worker, "poll_farm_enabled", return_value=True):
                    with patch.object(
                        discovery_worker,
                        "_claim_remote",
                        return_value={"names": ["don_hi_6"], "refilled": [], "paused": False},
                    ) as claim:
                        with patch.object(
                            discovery_worker,
                            "run_batch",
                            return_value={"evaluated": 1, "qualified": 0, "planned": ["don_hi_6"]},
                        ) as batch:
                            with patch.object(discovery_worker, "_release_remote", return_value={}):
                                with patch.object(discovery_worker, "_plan_batch") as plan:
                                    with patch.object(discovery_worker, "bootstrap_from_prod") as boot:
                                        rc = discovery_worker.main([
                                            "--once",
                                            "--workers",
                                            "1",
                                            "--worker-id",
                                            "unit-host",
                                        ])
        self.assertEqual(rc, 0)
        plan.assert_not_called()
        boot.assert_not_called()
        claim.assert_called_once()
        self.assertEqual(claim.call_args.args[2], "unit-host")
        self.assertEqual(claim.call_args.args[3], 1)
        self.assertTrue(batch.call_args.kwargs["claim_mode"])
        self.assertEqual(batch.call_args.kwargs["names"], ["don_hi_6"])
        self.assertEqual(batch.call_args.kwargs["worker_id"], "unit-host")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crypto_history_5m.json").write_text("{}")
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with patch.object(discovery_worker, "_claim_remote") as claim:
                    with patch.object(
                        discovery_worker,
                        "run_batch",
                        return_value={"evaluated": 0, "qualified": 0},
                    ) as batch:
                        rc = discovery_worker.main([
                            "--once",
                            "--local-plan",
                            "--no-ingest",
                            "--workers",
                            "1",
                        ])
        self.assertEqual(rc, 0)
        claim.assert_not_called()
        self.assertFalse(batch.call_args.kwargs["claim_mode"])

    def test_post_ingest_heartbeat_includes_worker_id(self):
        from scripts.discovery_worker import _post_ingest

        with patch("scripts.discovery_worker._http_json", return_value={}) as http:
            _post_ingest(
                "https://trading.example/api/discovery/ingest",
                "paper-secret-token",
                [],
                [],
                None,
                heartbeat="running",
                worker_id="jensa-9",
            )
        data = http.call_args.kwargs["data"]
        self.assertEqual(data["worker_id"], "jensa-9")
        self.assertEqual(data["heartbeat"]["worker_id"], "jensa-9")
        self.assertEqual(data["heartbeat"]["status"], "running")
        self.assertNotIn("paper-secret-token", json.dumps(data))

    def test_default_worker_id_uses_hostname_and_pid(self):
        from scripts.discovery_worker import default_worker_id

        wid = default_worker_id()
        self.assertIn(str(os.getpid()), wid)
        self.assertNotIn(" ", wid)


if __name__ == "__main__":
    unittest.main()
