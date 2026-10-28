"""Single-flight, rate-limited caches behind the heavy Discovery / Champions GETs.

Prod web is 0.5 CPU / 1Gi. A request must never run these builds on its
own thread, concurrent requests must share one build, and a build must not
rerun more often than the min interval just because ingest touched a file.
"""
from __future__ import annotations

import json
import math
import os
import random
import tempfile
import threading
import time
import unittest
from collections import defaultdict
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from hedge_fund.trading import block_report
from hedge_fund.trading.constants import DISCOVERY_N_WINDOWS, GATE_RULES
from hedge_fund.web.bg_cache import COMPUTING, READY, STALE, BackgroundCache

REPO = Path(__file__).resolve().parents[1]


class _SlowBuilder:
    def __init__(self, value="v", delay=0.2):
        self.calls = 0
        self.value = value
        self.delay = delay
        self.lock = threading.Lock()
        self.fail = False

    def __call__(self):
        with self.lock:
            self.calls += 1
            n = self.calls
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        return f"{self.value}{n}"


class BackgroundCacheTests(unittest.TestCase):
    def test_concurrent_cold_calls_start_one_build_and_answer_computing(self):
        cache = BackgroundCache("t", min_interval=60)
        build = _SlowBuilder()
        results = []

        def hit():
            results.append(cache.get("k", build).status)

        threads = [threading.Thread(target=hit) for _ in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(set(results), {COMPUTING})
        self.assertTrue(cache.wait_idle("k"))
        self.assertEqual(build.calls, 1)
        self.assertEqual(cache.builds_started, 1)
        read = cache.get("k", build)
        self.assertEqual((read.status, read.value), (READY, "v1"))

    def test_cold_wait_returns_the_build_when_it_lands_in_time(self):
        cache = BackgroundCache("t", min_interval=60)
        build = _SlowBuilder(delay=0.05)
        read = cache.get("k", build, wait_cold=5)
        self.assertEqual((read.status, read.value), (READY, "v1"))

    def test_min_interval_holds_even_when_the_token_changes(self):
        cache = BackgroundCache("t", min_interval=600)
        build = _SlowBuilder(delay=0)
        token = {"n": 0}
        now = [1000.0]
        with patch("hedge_fund.web.bg_cache.time.monotonic", side_effect=lambda: now[0]):
            cache.get("k", build, token=lambda: token["n"])
            cache.wait_idle("k")
            for i in range(1, 30):
                token["n"] = i  # every ingest moves the file stat
                now[0] += 10
                read = cache.get("k", build, token=lambda: token["n"])
                self.assertEqual(read.value, "v1")
            self.assertEqual(build.calls, 1)
            now[0] = 1000.0 + 601
            read = cache.get("k", build, token=lambda: token["n"])
            # Last payload is served at once, flagged stale, while one rebuild runs.
            self.assertEqual(read.value, "v1")
            self.assertEqual(read.status, STALE)
            cache.wait_idle("k")
            self.assertEqual(build.calls, 2)
            self.assertEqual(cache.get("k", build, token=lambda: token["n"]).value, "v2")

    def test_unchanged_token_skips_rebuild_after_interval(self):
        cache = BackgroundCache("t", min_interval=1)
        build = _SlowBuilder(delay=0)
        now = [0.0]
        with patch("hedge_fund.web.bg_cache.time.monotonic", side_effect=lambda: now[0]):
            cache.get("k", build, token=lambda: "same")
            cache.wait_idle("k")
            now[0] = 100.0
            cache.get("k", build, token=lambda: "same")
            cache.wait_idle("k")
        self.assertEqual(build.calls, 1)

    def test_failed_rebuild_keeps_last_payload(self):
        cache = BackgroundCache("t", min_interval=0)
        build = _SlowBuilder(delay=0)
        cache.get("k", build)
        cache.wait_idle("k")
        build.fail = True
        cache.get("k", build)
        cache.wait_idle("k")
        read = cache.get("k", build)
        cache.wait_idle("k")
        self.assertEqual(read.value, "v1")

    def test_keys_are_bounded(self):
        cache = BackgroundCache("t", min_interval=60, max_keys=3)
        build = _SlowBuilder(delay=0)
        for i in range(10):
            cache.get(i, build)
            cache.wait_idle(i)
        self.assertLessEqual(len(cache._slots), 3)


def _row(name, daily, sharpe, trades, qualified, **extra):
    row = {
        "strategy": name,
        "gate_rules": GATE_RULES,
        "regimes_tested": DISCOVERY_N_WINDOWS,
        "daily_sharpe": daily,
        "sharpe": sharpe,
        "trades": trades,
        "qualified": qualified,
    }
    row.update(extra)
    return row


def _reference_effect(rows, holds, block, level):
    """The pre-optimisation implementation, kept here to pin the numbers."""

    def number(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            return None
        return float(v)

    def mean(vs):
        return sum(vs) / len(vs) if vs else None

    def side(field, inside):
        return mean([v for r in rows if (id(r) in holds) == inside and (v := number(r.get(field))) is not None])

    def pass_rate(inside):
        g = [r for r in rows if (id(r) in holds) == inside]
        return sum(1 for r in g if r.get("qualified")) / len(g) if g else None

    def matched():
        by_depth = defaultdict(list)
        for r in rows:
            by_depth[int(r["depth"])].append(r)
        num = den = 0.0
        for g in by_depth.values():
            w = [v for r in g if id(r) in holds and (v := number(r.get("daily_sharpe"))) is not None]
            o = [v for r in g if id(r) not in holds and (v := number(r.get("daily_sharpe"))) is not None]
            if not w or not o:
                continue
            k = min(len(w), len(o))
            num += k * (mean(w) - mean(o))
            den += k
        return num / den if den else None

    r6 = lambda v: None if v is None else round(float(v), 6)  # noqa: E731
    n_with = sum(1 for r in rows if id(r) in holds)
    return {
        "block": block, "level": level, "n_with": n_with, "n_without": len(rows) - n_with,
        "mean_daily_sharpe_with": r6(side("daily_sharpe", True)),
        "mean_daily_sharpe_without": r6(side("daily_sharpe", False)),
        "mean_trade_sharpe_with": r6(side("sharpe", True)),
        "mean_trade_sharpe_without": r6(side("sharpe", False)),
        "mean_trades_with": r6(side("trades", True)),
        "mean_trades_without": r6(side("trades", False)),
        "pass_rate_with": r6(pass_rate(True)),
        "pass_rate_without": r6(pass_rate(False)),
        "depth_controlled_daily_sharpe": r6(matched()),
        "method": "within-depth matched mean difference",
    }


class BlockReportParityTests(unittest.TestCase):
    def test_single_pass_effects_match_the_old_full_scans(self):
        rng = random.Random(7)
        atoms = ["sma_abv_20", "sma_abv_150", "ema_abv_50", "mom_18b_gt2pc", "mom_96b_gt6pc", "dip_30b_lt4pc",
                 "rsi_14_>45", "rsi_14_<30", "don_hi_24", "near_swing_lo_48", "h4_ema_abv_150", "sma_stack_20_50_200"]
        rows = []
        for i in range(600):
            name = "&".join(rng.sample(atoms, rng.randint(2, 6))) + f"&sma_abv_{30 + i}"
            daily = None if rng.random() < 0.1 else rng.gauss(0, 1)
            trades = None if rng.random() < 0.05 else rng.randint(0, 120)
            rows.append(_row(name, daily, rng.gauss(0, 1), trades, rng.random() < 0.1))
        report = block_report.build_block_report(rows, [])
        prepared = []
        fam, buck = defaultdict(set), defaultdict(set)
        for row in rows:
            item = dict(row)
            item["depth"] = len(block_report._atoms(row["strategy"]))
            prepared.append(item)
            f, b = block_report._blocks(row["strategy"])
            for x in f:
                fam[x].add(id(item))
            for x in b:
                buck[x].add(id(item))
        expected = {("family", k): _reference_effect(prepared, v, k, "family") for k, v in fam.items()}
        expected.update({("bucket", k): _reference_effect(prepared, v, k, "bucket") for k, v in buck.items()})
        got = {(r["level"], r["block"]): r for r in report["families"] + report["buckets"]}
        self.assertEqual(set(got), set(expected))
        for key, want in expected.items():
            for field, value in want.items():
                if isinstance(value, float):
                    self.assertAlmostEqual(got[key][field], value, places=5, msg=f"{key} {field}")
                else:
                    self.assertEqual(got[key][field], value, msg=f"{key} {field}")

    def test_streamed_rows_are_slim(self):
        rows = iter([_row("sma_abv_20&don_hi_24", 1.0, 0.5, 10, True, windows=[{"end_ms": 1}] * DISCOVERY_N_WINDOWS)])
        report = block_report.build_block_report(rows, [])
        self.assertEqual(report["n_results"], 1)

    def test_slim_prefilter_agrees_with_is_pre_holdout_on_window_free_checks(self):
        good = _row("a&b", 1.0, 1.0, 5, False)
        self.assertTrue(block_report._slim_prefilter(good))
        self.assertFalse(block_report._slim_prefilter({**good, "gate_rules": "old"}))
        self.assertFalse(block_report._slim_prefilter({**good, "regimes_tested": 3}))
        self.assertFalse(block_report._slim_prefilter({**good, "_windows_len": 3}))
        self.assertFalse(block_report._slim_prefilter({**good, "partial": True}))
        self.assertFalse(block_report._slim_prefilter({**good, "daily_sharpe": None, "sharpe": None, "trades": None}))


class BlockReportEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"PAPER_STATE": str(self.root)})
        self.env.start()
        block_report.clear_block_report_cache()
        self.grace = patch.object(block_report, "WEB_FALLBACK_GRACE_SECONDS", 0.0)
        self.grace.start()

    def tearDown(self):
        self.grace.stop()
        block_report.clear_block_report_cache()
        self.env.stop()
        self.tmp.cleanup()

    def test_fresh_web_process_waits_for_the_cycle_before_building(self):
        calls = []
        with patch.object(block_report, "WEB_FALLBACK_GRACE_SECONDS", 900.0), \
                patch.object(block_report, "build_block_report", self._slow_build(calls)):
            body, code = block_report.block_report_response()
        self.assertEqual(code, 202)
        self.assertEqual(calls, [])

    def _slow_build(self, calls):
        def build(*_a, **_k):
            calls.append(1)
            time.sleep(0.3)
            return {"families": [], "buckets": [], "paired": [], "n_results": 0, "label": "x"}
        return build

    def test_cold_start_is_202_and_concurrent_calls_share_one_build(self):
        calls = []
        with patch.object(block_report, "build_block_report", self._slow_build(calls)):
            codes = []

            def hit():
                codes.append(block_report.block_report_response()[1])

            threads = [threading.Thread(target=hit) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(set(codes), {202})
            block_report._web_cache().wait_idle(block_report.state_root_token())
            body, code = block_report.block_report_response()
        self.assertEqual(code, 200)
        self.assertEqual(len(calls), 1)
        self.assertIn(body["status"], ("ready", "stale"))

    def test_published_file_is_served_without_building(self):
        calls = []
        with patch.object(block_report, "build_block_report", self._slow_build(calls)):
            self.assertEqual(block_report.publish_block_report(), "published")
            self.assertEqual(len(calls), 1)
            for _ in range(5):
                body, code = block_report.block_report_response()
                self.assertEqual(code, 200)
            self.assertEqual(body["source"], "cycle")
            self.assertNotIn("inputs_token", body)
            self.assertEqual(len(calls), 1)

    def test_publish_respects_min_interval_and_skips_unchanged_inputs(self):
        calls = []
        with patch.object(block_report, "build_block_report", self._slow_build(calls)):
            self.assertEqual(block_report.publish_block_report(), "published")
            (self.root / "discovery_results.jsonl").write_text("{}\n")  # ingest moved the file
            self.assertEqual(block_report.publish_block_report(), "fresh")
            self.assertEqual(len(calls), 1)
            data = json.loads(block_report.published_path().read_text())
            data["built_at_unix"] -= block_report.MIN_REBUILD_SECONDS + 1
            block_report.published_path().write_text(json.dumps(data))
            self.assertEqual(block_report.publish_block_report(), "published")
            self.assertEqual(len(calls), 2)
            data = json.loads(block_report.published_path().read_text())
            data["built_at_unix"] -= block_report.MIN_REBUILD_SECONDS + 1
            block_report.published_path().write_text(json.dumps(data))
            self.assertEqual(block_report.publish_block_report(), "unchanged")
            self.assertEqual(len(calls), 2)

    def test_second_publisher_does_not_pile_up(self):
        import fcntl

        lock_path = block_report.published_path().with_name("discovery_blocks.lock")
        with open(lock_path, "a+") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            self.assertEqual(block_report.publish_block_report(force=True), "busy")

    def test_web_defers_to_an_active_cycle_publisher(self):
        calls = []
        block_report.published_path().with_name("discovery_blocks.lock").touch()
        with patch.object(block_report, "build_block_report", self._slow_build(calls)):
            body, code = block_report.block_report_response()
        self.assertEqual(code, 202)
        self.assertEqual(body["status"], "computing")
        self.assertEqual(calls, [])


class CyclePublishHookTests(unittest.TestCase):
    def test_cycle_spawns_a_detached_niced_publish(self):
        from scripts import live_cycle

        with patch.object(live_cycle.subprocess, "Popen") as popen, patch.dict(os.environ, {"BLOCK_REPORT_PUBLISH": "1"}):
            live_cycle._spawn_block_report_publish()
        argv = popen.call_args.args[0]
        self.assertIn("--publish", argv)
        self.assertIn("--nice", argv)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_publish_can_be_switched_off(self):
        from scripts import live_cycle

        with patch.object(live_cycle.subprocess, "Popen") as popen, patch.dict(os.environ, {"BLOCK_REPORT_PUBLISH": "0"}):
            live_cycle._spawn_block_report_publish()
        popen.assert_not_called()

    def test_trading_failure_skips_publish(self):
        from scripts import live_cycle

        def fake_run(label, argv, *, critical=False):
            return 1 if label == "run_isolated" else 0

        with patch.object(live_cycle, "_run", side_effect=fake_run), \
                patch.object(live_cycle, "write_pipeline_stamp"), \
                patch.object(live_cycle, "_spawn_block_report_publish") as spawn:
            self.assertEqual(live_cycle.main(), 1)
        spawn.assert_not_called()


class HttpRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "champions.json").write_text(json.dumps({"champions": []}))
        self.env = patch.dict(os.environ, {"PAPER_STATE": str(self.root)})
        self.env.start()
        block_report.clear_block_report_cache()
        self.grace = patch.object(block_report, "WEB_FALLBACK_GRACE_SECONDS", 0.0)
        self.grace.start()
        self.addCleanup(self.grace.stop)
        from hedge_fund.web import server

        server._CHAMPIONS_CACHE.clear()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        block_report.clear_block_report_cache()
        self.env.stop()
        self.tmp.cleanup()

    def test_blocks_route_answers_202_with_retry_after_on_cold_start(self):
        def slow(*_a, **_k):
            time.sleep(0.5)
            return {"families": [], "buckets": [], "paired": [], "n_results": 0}

        with patch.object(block_report, "build_block_report", slow):
            resp = urlopen(f"{self.base}/api/discovery/blocks", timeout=5)
            self.assertEqual(resp.status, 202)
            self.assertTrue(resp.headers.get("Retry-After"))
            self.assertEqual(json.loads(resp.read())["status"], "computing")
            block_report._web_cache().wait_idle(block_report.state_root_token())
            resp = urlopen(f"{self.base}/api/discovery/blocks", timeout=5)
            self.assertEqual(resp.status, 200)

    def test_champions_route_builds_once_for_concurrent_requests(self):
        from hedge_fund.trading import champions

        calls = []
        real = champions.pool_status

        def counted(**kw):
            calls.append(kw)
            time.sleep(0.2)
            return real(**kw)

        with patch.object(champions, "pool_status", counted):
            codes = []

            def hit():
                codes.append(urlopen(f"{self.base}/api/champions", timeout=15).status)

            threads = [threading.Thread(target=hit) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            for _ in range(5):
                urlopen(f"{self.base}/api/champions", timeout=15).read()
        self.assertEqual(set(codes), {200})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], {"read_only": True})


class FrontendPollingContractTests(unittest.TestCase):
    def _src(self, rel):
        return (REPO / "frontend" / "src" / rel).read_text()

    def test_no_15s_summary_polls_and_no_background_polling(self):
        for rel in ("pages/Discovery.tsx", "status/FarmControl.tsx", "status/DiscoveryBuckets.tsx"):
            src = self._src(rel)
            self.assertNotIn("15_000", src, rel)
            self.assertIn("SUMMARY_POLL_MS", src, rel)
        self.assertIn("SUMMARY_POLL_MS = 60_000", self._src("api/poll.ts"))
        self.assertIn("refetchIntervalInBackground: false", self._src("main.tsx"))

    def test_block_effects_polls_slowly_and_handles_computing(self):
        src = self._src("status/BlockEffects.tsx")
        self.assertIn("BLOCKS_POLL_MS", src)
        self.assertIn("retry: false", src)
        self.assertIn("refetchIntervalInBackground: false", src)
        self.assertIn("isComputing", src)
        self.assertIn("BLOCKS_POLL_MS = 600_000", self._src("api/poll.ts"))
        self.assertIn("res.status === 202", self._src("api/client.ts"))


if __name__ == "__main__":
    unittest.main()
