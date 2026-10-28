"""Continuous in-flight eval, per-name timeout, warm pool and tape."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from hedge_fund.trading.constants import DISCOVERY_N_WINDOWS


def _cheap(name: str) -> dict:
    return {
        "strategy": name,
        "tested_at": "2026-10-27T00:00:00+00:00",
        "qualified": False,
        "sharpe": 0.1,
        "trades": 5,
        "test_pnl": -1.0,
        "train_pnl": 0.0,
        "win_rate_pct": 0.0,
        "fail_reasons": ["oos_sharpe 0.10 < 0.30"],
        "timeframe": "5m",
        "risk_policy": "rm_v1",
    }


class _ScriptedPool:
    """In-process stand-in. ready_at is seconds after this submit."""

    def __init__(self, clock: dict, ready_after, attempts: dict):
        self.clock = clock
        self.ready_after = ready_after
        self.attempts = attempts
        self.terminated = False
        self.starts: dict[str, list[float]] = {}
        self.max_inflight = 0
        self._inflight = 0

    def apply_async(self, fn, args):
        name = args[0]
        self.attempts[name] = self.attempts.get(name, 0) + 1
        attempt = self.attempts[name]
        start = self.clock["t"]
        self.starts.setdefault(name, []).append(start)
        self._inflight += 1
        self.max_inflight = max(self.max_inflight, self._inflight)
        delay = self.ready_after(name, attempt)
        ar = MagicMock()

        def ready(delay=delay, start=start):
            if delay is None:
                return False
            return self.clock["t"] >= start + delay

        def get(timeout=None, fn=fn, args=args):
            self._inflight -= 1
            return fn(*args)

        ar.ready.side_effect = ready
        ar.get.side_effect = get
        return ar

    def terminate(self):
        self.terminated = True

    def join(self):
        return None


def _read_log(root: Path) -> list[dict]:
    from hedge_fund.trading.discovery import load_discovery_log

    with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
        return load_discovery_log()


class _Batch:
    def __init__(self, root: Path, *, timeout: str, workers: int, names: list[str]):
        self.root = root
        self.timeout = timeout
        self.workers = workers
        self.names = names
        self.clock = {"t": 0.0}
        self.attempts: dict[str, int] = {}
        self.pools: list[_ScriptedPool] = []
        self.posts: list[dict] = []
        self.eval_calls: list[str] = []
        self.ready_after = lambda name, attempt: 0

    def sleep(self, _n):
        self.clock["t"] += 1.0

    def monotonic(self):
        return self.clock["t"]

    def _new_pool(self, *args, **kwargs):
        pool = _ScriptedPool(self.clock, self.ready_after, self.attempts)
        self.pools.append(pool)
        return pool

    def _eval(self, name, *args, **kwargs):
        self.eval_calls.append(name)
        return _cheap(name)

    def _post(self, url, token, evals, extended, flight, **kwargs):
        self.posts.append({
            "n": len(evals),
            "names": [e.get("strategy") for e in evals],
            "heartbeat": kwargs.get("heartbeat"),
            "evaluating": list(kwargs.get("evaluating") or []),
        })
        return {}

    def run(self, **kwargs):
        from scripts.discovery_worker import run_batch

        env = {
            "PAPER_STATE": str(self.root),
            "DISCOVERY_EVAL_TIMEOUT_SECONDS": self.timeout,
            "DISCOVERY_STRUCTURE_LOOKBACK_MAX": "96",
        }
        with patch.dict(os.environ, env), patch(
            "scripts.discovery_worker._load_qual_history",
            return_value={"BTC/USDT": [1]},
        ) as self.load, patch(
            "scripts.discovery_worker._window_slices",
            return_value=[{}] * DISCOVERY_N_WINDOWS,
        ) as self.slices, patch(
            "scripts.discovery_worker._benchmark_oos",
            return_value=(1.0, 1.0),
        ) as self.benchmark, patch(
            "scripts.discovery_worker._benchmark_bh_daily_sharpe",
            return_value=0.1,
        ), patch(
            "scripts.discovery_worker.evaluate_strategy_record",
            side_effect=self._eval,
        ), patch(
            "scripts.discovery_worker._new_eval_pool",
            side_effect=self._new_pool,
        ), patch(
            "scripts.discovery_worker.time.monotonic",
            self.monotonic,
        ), patch(
            "scripts.discovery_worker.time.sleep",
            self.sleep,
        ), patch(
            "scripts.discovery_worker._post_ingest",
            side_effect=self._post,
        ):
            return run_batch(
                workers=self.workers,
                max_names=len(self.names),
                ingest_url="https://trading.example/api/discovery/ingest",
                token="t",
                names=self.names,
                worker_id="unit-host",
                **kwargs,
            )


class InFlightSchedulingTests(unittest.TestCase):
    def setUp(self):
        from scripts.discovery_worker import _reset_eval_session

        _reset_eval_session()

    def tearDown(self):
        from scripts.discovery_worker import _reset_eval_session

        _reset_eval_session()

    def test_next_name_starts_when_one_finishes_and_posts_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = _Batch(
                Path(tmp),
                timeout="50",
                workers=2,
                names=["slow", "fast", "third"],
            )

            def ready_after(name, attempt):
                return {"slow": None, "fast": 1, "third": 1}[name]

            batch.ready_after = ready_after
            batch.run()
            log = _read_log(batch.root)

        by_name = {r["strategy"]: r for r in log}
        self.assertEqual(batch.pools[0].max_inflight, 2)
        self.assertEqual(batch.pools[0].starts["third"], [1.0])
        self.assertEqual(batch.pools[0].starts["slow"], [0.0])
        self.assertNotIn("slow", batch.eval_calls)
        self.assertEqual(batch.eval_calls, ["fast", "third"])
        result_posts = [p for p in batch.posts if p["n"]]
        self.assertEqual([p["names"] for p in result_posts], [["fast"], ["third"], ["slow"]])
        self.assertTrue(all(p["heartbeat"] == "running" for p in result_posts))
        self.assertTrue(all(p["n"] == 1 for p in result_posts))
        # The replacement is submitted before the result POST, so the
        # heartbeat that carries fast's row already lists third.
        self.assertIn("third", result_posts[0]["evaluating"])
        self.assertIn("eval_timeout after 50s", by_name["slow"]["fail_reasons"])
        self.assertNotIn("eval_timeout", by_name["fast"]["fail_reasons"][0])
        self.assertLess(batch.clock["t"], 50 + 5)

    def test_timeout_reruns_sibling_that_is_still_inside_its_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = _Batch(
                Path(tmp),
                timeout="5",
                workers=2,
                names=["slow", "holder", "sibling"],
            )

            def ready_after(name, attempt):
                if name == "slow":
                    return None
                if name == "holder":
                    return 1
                if attempt == 1:
                    return 100
                return 0

            batch.ready_after = ready_after
            batch.run()
            log = _read_log(batch.root)

        by_name = {r["strategy"]: r for r in log}
        self.assertEqual(len(batch.pools), 2)
        self.assertTrue(batch.pools[0].terminated)
        self.assertEqual(batch.attempts["sibling"], 2)
        self.assertEqual(batch.attempts.get("slow", 0), 1)
        self.assertEqual(batch.eval_calls.count("sibling"), 1)
        self.assertNotIn("slow", batch.eval_calls)
        self.assertIn("eval_timeout after 5s", by_name["slow"]["fail_reasons"])
        self.assertEqual(by_name["sibling"]["fail_reasons"], ["oos_sharpe 0.10 < 0.30"])
        self.assertEqual(batch.pools[1].starts["sibling"], [5.0])

    def test_shutdown_releases_names_that_were_not_started(self):
        from scripts.discovery_worker import run_batch

        released = {}

        def capture(base, token, worker_id, names=None, status="idle"):
            released["base"] = base
            released["names"] = list(names or [])
            released["status"] = status
            return {}

        with tempfile.TemporaryDirectory() as tmp:
            batch = _Batch(
                Path(tmp),
                timeout="30",
                workers=1,
                names=["fast", "slow", "later"],
            )

            def ready_after(name, attempt):
                return 0 if name == "fast" else None

            def sleep(_n):
                raise KeyboardInterrupt

            batch.ready_after = ready_after
            batch.sleep = sleep
            with patch("scripts.discovery_worker._release_remote", side_effect=capture):
                with self.assertRaises(KeyboardInterrupt):
                    batch.run(claim_mode=True)

        self.assertEqual(batch.pools[0].starts["fast"], [0.0])
        self.assertIn("slow", batch.pools[0].starts)
        self.assertNotIn("later", batch.pools[0].starts)
        self.assertEqual(set(released["names"]), {"slow", "later"})
        self.assertEqual(released["status"], "idle")
        self.assertEqual(released["base"], "https://trading.example")
        self.assertTrue(batch.pools[0].terminated)
        posted = [p["names"] for p in batch.posts if p["n"]]
        self.assertEqual(posted, [["fast"]])

    def test_pool_and_history_reused_until_tape_hash_changes(self):
        stamps = [{"sha256": "a" * 64, "data_end": 1, "provenance": "unverified"}]

        def manifest():
            return dict(stamps[0])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crypto_history_5m.json").write_text("{}")
            batch = _Batch(root, timeout="30", workers=2, names=["one", "two"])
            batch.ready_after = lambda name, attempt: 0
            with patch(
                "hedge_fund.trading.price_history.manifest_for",
                side_effect=manifest,
            ):
                batch.run()
                first_load = batch.load.call_count
                first_slices = batch.slices.call_count
                first_bench = batch.benchmark.call_count
                first_pools = len(batch.pools)
                batch.run()
                second_load = batch.load.call_count
                second_slices = batch.slices.call_count
                second_bench = batch.benchmark.call_count
                second_pools = len(batch.pools)
                warm = not batch.pools[0].terminated
                stamps[0] = {"sha256": "b" * 64, "data_end": 2, "provenance": "unverified"}
                batch.run()
                third_load = batch.load.call_count
                third_pools = len(batch.pools)

        self.assertEqual(first_load, 1)
        self.assertEqual(first_slices, 1)
        self.assertEqual(first_bench, 1)
        self.assertEqual(second_load, 0)
        self.assertEqual(second_slices, 0)
        self.assertEqual(second_bench, 0)
        self.assertEqual(first_pools, 1)
        self.assertEqual(second_pools, 1)
        self.assertTrue(warm)
        self.assertEqual(third_load, 1)
        self.assertEqual(third_pools, 2)
        self.assertTrue(batch.pools[0].terminated)
        self.assertFalse(batch.pools[1].terminated)
        result_posts = [p for p in batch.posts if p["n"]]
        self.assertTrue(result_posts)
        self.assertTrue(all(p["n"] == 1 for p in result_posts))

    def test_reused_pool_passes_each_names_trial_count(self):
        seen = []

        def qualified(name, *args, **kwargs):
            return {
                "strategy": name,
                "qualified": True,
                "fail_reasons": [],
                "sharpe": 0.4,
                "trades": 40,
                "test_pnl": 1.0,
            }

        def fake_finish(name, record, **kwargs):
            seen.append((name, kwargs.get("n_trials")))
            out = dict(record)
            out["qualified"] = False
            out["fail_reasons"] = ["dsr"]
            return out

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crypto_history_5m.json").write_text("{}")
            batch = _Batch(root, timeout="30", workers=2, names=["fresh", "re_name"])
            batch.ready_after = lambda name, attempt: 0
            batch._eval = qualified
            with patch(
                "hedge_fund.trading.price_history.manifest_for",
                return_value={"sha256": "c" * 64, "data_end": 1},
            ), patch(
                "hedge_fund.trading.gate_v4.finish_gate_v4",
                side_effect=fake_finish,
            ):
                batch.run(n_trials=11, retest_names=["re_name"], n_trials_retest=10)
                pools_after_first = len(batch.pools)
                batch.run(n_trials=20, retest_names=["re_name"], n_trials_retest=15)

        self.assertEqual(pools_after_first, 1)
        self.assertEqual(len(batch.pools), 1)
        self.assertEqual(
            seen,
            [("fresh", 11), ("re_name", 10), ("fresh", 20), ("re_name", 15)],
        )


class EvalParityTests(unittest.TestCase):
    def tearDown(self):
        from scripts.discovery_worker import _reset_eval_session

        _reset_eval_session()

    def test_inflight_records_match_the_direct_eval(self):
        from scripts.discovery_worker import (
            _acquire_eval_pool,
            _eval_name,
            _init_pool,
            _reset_eval_session,
            _run_inflight,
        )
        from scripts.tournament_engine import _benchmark_bh_daily_sharpe, _benchmark_oos
        from tests.fixtures.walkforward_parity import make_parity_slices

        _reset_eval_session()
        names = ["sma_stack", "mom_12b_gt2pc"]
        n_windows = 2
        slices = make_parity_slices(window_bars=240, n_windows=n_windows, warmup_bars=80)
        bh, sma = _benchmark_oos(slices)
        bh_dsr = _benchmark_bh_daily_sharpe(slices)
        _init_pool(slices, bh, sma, n_windows, bh_dsr, None, None, None, 4, [], None)
        direct = {name: _eval_name(name) for name in names}
        bundle = {
            "slices": slices,
            "bh": bh,
            "sma": sma,
            "bh_dsr": bh_dsr,
            "eth_slices": None,
            "holdout": None,
            "holdout_bh": None,
            "n_windows": n_windows,
            "sha": "parity-sha",
            "state": "parity-state",
        }

        def factory():
            return _acquire_eval_pool(2, bundle, 4, [], None)

        got = {}

        def finish(name, record):
            got[name] = record

        _run_inflight(
            names,
            n_workers=2,
            timeout_s=120,
            finish=finish,
            pool_factory=factory,
            trial_for=lambda _name: 4,
        )
        self.assertEqual(set(got), set(names))
        for name in names:
            left = dict(direct[name])
            right = dict(got[name])
            self.assertIn("tested_at", left)
            self.assertIn("tested_at", right)
            left.pop("tested_at", None)
            right.pop("tested_at", None)
            self.assertEqual(left, right)


if __name__ == "__main__":
    unittest.main()
