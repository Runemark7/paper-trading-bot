"""Discovery summary buckets: tested / in-flight / leftover-untested."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.constants import (
    DISCOVER_CYCLE_MAX_NAMES,
    DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
)
from hedge_fund.trading.discovery import (
    clear_in_flight,
    failed_discovery_names,
    latest_eval_per_strategy,
    load_cursor,
    load_discovery_log,
    newest_eval,
    prioritize_leftovers,
    read_in_flight,
    select_cycle_batch,
    write_in_flight,
)
from hedge_fund.web.discovery import build_discovery_summary


def _eval(name, *, qualified, tested_at, sharpe=0.4, trades=40, test_pnl=12.0):
    return {
        "strategy": name,
        "tested_at": tested_at,
        "qualified": qualified,
        "sharpe": sharpe,
        "trades": trades,
        "test_pnl": test_pnl,
        "train_pnl": 99.0,
        "win_rate_pct": 50.0,
        "fail_reasons": [] if qualified else ["oos_sharpe 0.10 < 0.30"],
    }


def _dummy_windows():
    return [
        {
            "train_pnl": 0.0,
            "test_pnl": -1.0,
            "test_trades": 1,
            "trades": 1,
            "wins": 0,
            "sharpe": 0.0,
            "skipped": False,
            "failed": False,
        }
        for _ in range(3)
    ]


@contextmanager
def _tournament_patches(leftovers, evaluate_side_effect):
    with (
        patch("scripts.tournament_engine.generate_candidate_pool", return_value=leftovers),
        patch(
            "scripts.tournament_engine._load_qual_history",
            return_value={"BTC/USDT": [[0] * 5] * 10, "ETH/USDT": [[0] * 5] * 10},
        ),
        patch("scripts.tournament_engine._window_slices", return_value=[{}, {}, {}]),
        patch("scripts.tournament_engine._benchmark_oos", return_value=(0.0, 0.0)),
        patch("scripts.tournament_engine.parse_strategy", return_value=lambda *a, **k: True),
        patch("scripts.tournament_engine.evaluate_windows", side_effect=evaluate_side_effect),
    ):
        yield


class LatestEvalTests(unittest.TestCase):
    def test_keeps_newest_row_per_name(self):
        log = [
            _eval("dup", qualified=True, tested_at="2026-09-05T12:00:00+00:00", sharpe=0.8),
            _eval("other", qualified=False, tested_at="2026-09-05T11:00:00+00:00"),
            _eval("dup", qualified=False, tested_at="2026-09-01T12:00:00+00:00", sharpe=0.1),
        ]
        latest = latest_eval_per_strategy(log)
        by_name = {r["strategy"]: r for r in latest}
        self.assertEqual(len(latest), 2)
        self.assertTrue(by_name["dup"]["qualified"])
        self.assertEqual(by_name["dup"]["sharpe"], 0.8)
        self.assertFalse(by_name["other"]["qualified"])

    def test_newest_eval_is_max_tested_at_not_list_order(self):
        log = [
            _eval("bb_lower_20_2", qualified=False, tested_at="2026-09-06T20:04:34+00:00"),
            _eval("wt_cross_up_os&sma_stack_20_50_100", qualified=False, tested_at="2026-09-07T03:58:24+00:00"),
        ]
        row = newest_eval(log)
        self.assertEqual(row["strategy"], "wt_cross_up_os&sma_stack_20_50_100")
        self.assertEqual(row["tested_at"], "2026-09-07T03:58:24+00:00")


class DiscoverySummaryBucketTests(unittest.TestCase):
    def test_buckets_and_counts_exclude_champs_and_grads(self):
        universe = ["keep_me", "tested_pass", "tested_fail", "champ_a", "grad_b"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [{"name": "champ_a", "closed": 0, "pnl": 0, "wins": 0}],
                "synced_until": "",
            }))
            (root / "graduated.json").write_text(json.dumps([
                {"name": "grad_b", "status": "GRADUATED_PAPER", "graduated_at": "2026-09-01T00:00:00+00:00"},
            ]))
            recent = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
            older = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat()
            even_older = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("tested_pass", qualified=True, tested_at=recent),
                _eval("tested_fail", qualified=False, tested_at=older),
                _eval("tested_fail", qualified=False, tested_at=even_older),
            ]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with patch("hedge_fund.web.discovery.generate_universe", return_value=universe):
                    with patch("hedge_fund.trading.universe.generate_universe", return_value=universe):
                        s = build_discovery_summary()
        names_tested = [r["strategy"] for r in s["tested"]]
        self.assertEqual(names_tested, ["tested_pass", "tested_fail"])
        self.assertTrue(s["tested"][0]["qualified"])
        self.assertFalse(s["tested"][1]["qualified"])
        self.assertIn("keep_me", s["queued"])
        self.assertNotIn("tested_pass", s["queued"])
        self.assertNotIn("tested_fail", s["queued"])
        self.assertNotIn("champ_a", s["queued"])
        self.assertNotIn("grad_b", s["queued"])
        self.assertEqual(s["queued"], s["untested"])
        self.assertEqual(s["counts"]["tested_pass"], 1)
        self.assertEqual(s["counts"]["tested_fail"], 1)
        self.assertEqual(s["counts"]["tested"], 2)
        self.assertEqual(s["counts"]["unique_tested"], 2)
        self.assertEqual(s["counts"]["log_rows"], 3)
        self.assertEqual(s["counts"]["universe"], 5)
        self.assertEqual(s["counts"]["untested"], 1)
        self.assertEqual(s["counts"]["rejected_parked"], 1)
        self.assertEqual(s["counts"]["extended"], 0)
        self.assertNotIn("retest_queue", s["counts"])
        self.assertNotIn("retest_cooldown_seconds", s)
        self.assertIn("parked forever", s["note"])
        self.assertIn("discovery_extended.json", s["note"])
        self.assertEqual(s["counts"]["champions"], 1)
        self.assertEqual(s["counts"]["graduated"], 1)
        self.assertFalse(s["running"])
        self.assertFalse(s["stuck"])
        self.assertFalse(s["in_flight"]["active"])
        self.assertEqual(s["in_flight"]["names"], [])
        self.assertIn("idle — last eval", s["in_flight"]["note"])
        self.assertEqual(s["last_strategy"], "tested_pass")
        self.assertTrue(s["paper_only"])
        self.assertIn("unique strategy names", s["note"])
        self.assertIn("farm", s)
        self.assertTrue(s["farm"]["enabled"])
        self.assertEqual(s["farm"]["status"], "worker_unseen")
        self.assertIn("Worker not seen", s["farm"]["note"])

    def test_last_tested_at_is_newest_not_alpha_min(self):
        universe = ["bb_lower_20_2", "wt_cross_up_os&sma_stack_20_50_100"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({"champions": [], "synced_until": ""}))
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("bb_lower_20_2", qualified=False, tested_at="2026-09-06T20:04:34+00:00"),
                _eval("wt_cross_up_os&sma_stack_20_50_100", qualified=False, tested_at="2026-09-07T03:58:24+00:00"),
            ]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with patch("hedge_fund.web.discovery.generate_universe", return_value=universe):
                    with patch("hedge_fund.trading.universe.generate_universe", return_value=universe):
                        s = build_discovery_summary()
        self.assertEqual(s["last_tested_at"], "2026-09-07T03:58:24+00:00")
        self.assertEqual(s["last_strategy"], "wt_cross_up_os&sma_stack_20_50_100")
        self.assertEqual(s["tested"][0]["strategy"], "wt_cross_up_os&sma_stack_20_50_100")
        self.assertEqual(s["counts"]["unique_tested"], 2)

    def test_stale_tournament_stamp_is_stuck_not_idle_last_sweep(self):
        universe = ["keep_me"]
        old = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(timespec="seconds")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({"champions": [], "synced_until": ""}))
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("bb_lower_20_2", qualified=False, tested_at="2026-09-06T20:04:34+00:00"),
                _eval("keep_me", qualified=False, tested_at="2026-09-07T03:58:24+00:00"),
            ]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                from hedge_fund.trading.stamps import PIPELINE_STAMP, write_json_stamp
                write_json_stamp(PIPELINE_STAMP, {
                    "phase": "tournament",
                    "status": "started",
                    "at": old,
                    "started_at": old,
                    "source": "live_cycle.py",
                })
                write_in_flight(["keep_me"], current="keep_me", remaining=[], batch_size=1)
                with patch("hedge_fund.web.discovery.generate_universe", return_value=universe):
                    with patch("hedge_fund.trading.universe.generate_universe", return_value=universe):
                        s = build_discovery_summary()
        self.assertTrue(s["stale"])
        self.assertTrue(s["stuck"])
        self.assertTrue(s["in_flight"]["stale"])
        self.assertTrue(s["in_flight"]["active"])
        self.assertIn("stuck", s["in_flight"]["note"].lower())
        self.assertIn("stale", s["in_flight"]["note"].lower())
        self.assertNotIn("idle — last eval", s["in_flight"]["note"])
        self.assertNotIn("idle — last sweep", s["in_flight"]["note"])
        self.assertEqual(s["last_tested_at"], "2026-09-07T03:58:24+00:00")
        self.assertEqual(s["stuck_reason"], s["in_flight"]["note"])

    def test_quiet_evals_with_leftovers_are_cycle_overdue(self):
        universe = ["keep_me", "fresh_name"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({"champions": [], "synced_until": ""}))
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("keep_me", qualified=False, tested_at="2026-09-06T04:00:00+00:00"),
            ]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with patch("hedge_fund.web.discovery.generate_universe", return_value=universe):
                    with patch("hedge_fund.trading.universe.generate_universe", return_value=universe):
                        s = build_discovery_summary()
        self.assertTrue(s["stuck"])
        self.assertIn("cycle overdue", s["stuck_reason"])
        self.assertIn("cycle overdue", s["in_flight"]["note"])
        self.assertIn("never-tested leftover", s["stuck_reason"])
        self.assertNotIn("idle — last sweep", s["in_flight"]["note"])
        self.assertIn("fresh_name", s["queued"])
        self.assertNotIn("keep_me", s["queued"])
        self.assertEqual(s["counts"]["rejected_parked"], 1)
        self.assertEqual(s["counts"]["eligible"], 1)

    def test_rejected_leftovers_are_not_cycle_overdue(self):
        universe = ["keep_me"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({"champions": [], "synced_until": ""}))
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("keep_me", qualified=False, tested_at="2026-09-06T04:00:00+00:00"),
            ]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with patch("hedge_fund.web.discovery.generate_universe", return_value=universe):
                    with patch("hedge_fund.trading.universe.generate_universe", return_value=universe):
                        s = build_discovery_summary()
        self.assertFalse(s["stuck"])
        self.assertIsNone(s["stuck_reason"])
        self.assertEqual(s["queued"], [])
        self.assertEqual(s["counts"]["eligible"], 0)
        self.assertEqual(s["counts"]["rejected_parked"], 1)
        self.assertIn("idle — last eval", s["in_flight"]["note"])

    def test_in_flight_names_only_when_tournament_stamp_in_progress(self):
        universe = ["keep_me", "flying"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({"champions": [], "synced_until": ""}))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                write_in_flight(["flying"])
                with patch("hedge_fund.web.discovery.generate_universe", return_value=universe):
                    with patch("hedge_fund.trading.universe.generate_universe", return_value=universe):
                        idle = build_discovery_summary()
                self.assertFalse(idle["in_flight"]["active"])
                self.assertEqual(idle["in_flight"]["names"], [])
                self.assertIn("flying", idle["queued"])

                from hedge_fund.trading.stamps import write_pipeline_stamp
                write_pipeline_stamp("tournament", "started")
                with patch("hedge_fund.web.discovery.generate_universe", return_value=universe):
                    with patch("hedge_fund.trading.universe.generate_universe", return_value=universe):
                        live = build_discovery_summary()
                self.assertTrue(live["stamp_says_in_progress"])
                self.assertFalse(live["running"])
                self.assertFalse(live["in_flight"]["running"])
                self.assertTrue(live["in_flight"]["active"])
                self.assertEqual(live["in_flight"]["names"], ["flying"])
                self.assertEqual(live["in_flight"]["batch_size"], 1)
                self.assertNotIn("flying", live["queued"])
                self.assertEqual(live["counts"]["in_flight"], 1)

                clear_in_flight()
                with patch("hedge_fund.web.discovery.generate_universe", return_value=universe):
                    with patch("hedge_fund.trading.universe.generate_universe", return_value=universe):
                        no_list = build_discovery_summary()
                self.assertTrue(no_list["in_flight"]["active"])
                self.assertEqual(no_list["in_flight"]["names"], [])
                self.assertIn("Name list was not persisted", no_list["in_flight"]["note"])

    def test_tournament_writes_cycle_budget_then_clears_in_flight(self):
        from scripts.tournament_engine import replenish_and_evaluate

        leftovers = [f"cand_{i}" for i in range(5)]
        seen = []

        def _on_eval(*_a, **_k):
            seen.append(read_in_flight())
            return _dummy_windows()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with _tournament_patches(leftovers, _on_eval):
                    replenish_and_evaluate(max_names=3, cooldown_seconds=0)
                self.assertIsNone(read_in_flight())
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[0]["names"], leftovers[:3])
        self.assertEqual(seen[0]["batch_size"], 3)
        self.assertEqual(seen[0]["current"], leftovers[0])
        self.assertEqual(seen[1]["completed"], leftovers[:1])
        self.assertEqual(seen[1]["remaining"], leftovers[2:3])

    def test_mid_batch_append_visible_before_full_finish(self):
        from scripts.tournament_engine import replenish_and_evaluate

        leftovers = [f"cand_{i}" for i in range(4)]
        logs_during = []

        def _on_eval(*_a, **_k):
            logs_during.append(list(load_discovery_log()))
            if len(logs_during) >= 3:
                raise RuntimeError("mid-batch")
            return _dummy_windows()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with _tournament_patches(leftovers, _on_eval):
                    with self.assertRaises(RuntimeError):
                        replenish_and_evaluate(max_names=4, cooldown_seconds=0)
                log = load_discovery_log()
                self.assertIsNone(read_in_flight())
        self.assertEqual(logs_during[0], [])
        self.assertEqual(len(logs_during[1]), 1)
        self.assertEqual(logs_during[1][0]["strategy"], leftovers[0])
        self.assertEqual(len(logs_during[2]), 2)
        self.assertEqual(len(log), 2)
        self.assertEqual({r["strategy"] for r in log}, {leftovers[0], leftovers[1]})

    def test_one_cycle_respects_name_budget_and_advances_cursor(self):
        from scripts.tournament_engine import replenish_and_evaluate

        leftovers = [f"cand_{i}" for i in range(8)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with _tournament_patches(leftovers, lambda *_a, **_k: _dummy_windows()):
                    first = replenish_and_evaluate(max_names=2, cooldown_seconds=0)
                self.assertEqual(first["total_tested_in_batch"], 2)
                log1 = load_discovery_log()
                self.assertEqual([r["strategy"] for r in log1], [leftovers[1], leftovers[0]])
                self.assertEqual(load_cursor().get("next_name"), leftovers[2])

                with _tournament_patches(leftovers, lambda *_a, **_k: _dummy_windows()):
                    second = replenish_and_evaluate(max_names=2, cooldown_seconds=0)
                self.assertEqual(second["total_tested_in_batch"], 2)
                log2 = load_discovery_log()
                self.assertEqual(len(log2), 4)
                self.assertEqual({r["strategy"] for r in log2[:2]}, {leftovers[2], leftovers[3]})
                self.assertEqual(load_cursor().get("next_name"), leftovers[4])

                with _tournament_patches(leftovers, lambda *_a, **_k: _dummy_windows()):
                    third = replenish_and_evaluate(cooldown_seconds=0)
                self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
                self.assertEqual(third["total_tested_in_batch"], DISCOVER_CYCLE_MAX_NAMES)
                self.assertEqual(load_discovery_log()[0]["strategy"], leftovers[4])
                self.assertEqual(load_cursor().get("next_name"), leftovers[5])

    def test_time_budget_stops_after_elapsed_names(self):
        from scripts.tournament_engine import replenish_and_evaluate

        leftovers = [f"cand_{i}" for i in range(6)]

        class Clock:
            def __init__(self):
                self.t = 0.0

            def monotonic(self):
                return self.t

        clock = Clock()

        def _on_eval(*_a, **_k):
            clock.t += 60
            return _dummy_windows()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with _tournament_patches(leftovers, _on_eval):
                    with patch("scripts.tournament_engine.time.monotonic", clock.monotonic):
                        res = replenish_and_evaluate(
                            max_names=10,
                            time_budget_seconds=DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
                            cooldown_seconds=0,
                        )
                self.assertEqual(res["total_tested_in_batch"], 2)
                self.assertEqual(len(load_discovery_log()), 2)


class FailOnceDiscoveryTests(unittest.TestCase):
    def test_prioritize_skips_any_prior_fail_and_keeps_never_tested(self):
        leftovers = ["old_fail", "prior_fail_then_pass", "never_tested", "only_pass"]
        log = [
            _eval("prior_fail_then_pass", qualified=True, tested_at="2026-09-10T12:00:00+00:00"),
            _eval("old_fail", qualified=False, tested_at="2026-09-01T00:00:00+00:00"),
            _eval("prior_fail_then_pass", qualified=False, tested_at="2026-09-01T00:00:00+00:00"),
            _eval("only_pass", qualified=True, tested_at="2026-09-02T00:00:00+00:00"),
        ]
        self.assertEqual(failed_discovery_names(log), {"old_fail", "prior_fail_then_pass"})
        self.assertEqual(
            prioritize_leftovers(leftovers, log, cooldown_seconds=0),
            ["never_tested"],
        )
        planned, rotated = select_cycle_batch(
            leftovers, log, max_names=8, cooldown_seconds=0,
        )
        self.assertEqual(planned, ["never_tested"])
        self.assertEqual(rotated, ["never_tested"])

    def test_fail_once_second_cycle_skips_that_name_never_tested_still_runs(self):
        from scripts.tournament_engine import replenish_and_evaluate

        leftovers = ["failed_once", "never_tested", "also_fresh"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with _tournament_patches(leftovers, lambda *_a, **_k: _dummy_windows()):
                    first = replenish_and_evaluate(max_names=1, cooldown_seconds=0)
                self.assertEqual(first["total_tested_in_batch"], 1)
                self.assertEqual(load_discovery_log()[0]["strategy"], "failed_once")
                self.assertFalse(load_discovery_log()[0]["qualified"])

                with _tournament_patches(leftovers, lambda *_a, **_k: _dummy_windows()):
                    second = replenish_and_evaluate(max_names=1, cooldown_seconds=0)
                self.assertEqual(second["total_tested_in_batch"], 1)
                log = load_discovery_log()
                self.assertEqual(log[0]["strategy"], "never_tested")
                self.assertEqual({r["strategy"] for r in log}, {"failed_once", "never_tested"})

                with _tournament_patches(leftovers, lambda *_a, **_k: _dummy_windows()):
                    third = replenish_and_evaluate(max_names=8, cooldown_seconds=0)
                # One leftover left (also_fresh) is "about to be" empty → refill.
                self.assertGreaterEqual(third["total_tested_in_batch"], 1)
                log = load_discovery_log()
                names = {r["strategy"] for r in log}
                self.assertIn("also_fresh", names)
                self.assertEqual(sum(1 for r in log if r["strategy"] == "failed_once"), 1)
                self.assertEqual(sum(1 for r in log if r["strategy"] == "never_tested"), 1)

                with _tournament_patches(leftovers, lambda *_a, **_k: _dummy_windows()):
                    fourth = replenish_and_evaluate(max_names=8, cooldown_seconds=0)
                self.assertGreater(fourth["total_tested_in_batch"], 0)
                log = load_discovery_log()
                original = {"failed_once", "never_tested", "also_fresh"}
                self.assertTrue({r["strategy"] for r in log} - original)
                for name in original:
                    self.assertEqual(sum(1 for r in log if r["strategy"] == name), 1)

    def test_existing_discovery_log_fail_is_never_selected(self):
        from scripts.tournament_engine import replenish_and_evaluate

        leftovers = ["prod_reject", "fresh"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("prod_reject", qualified=False, tested_at="2026-09-08T00:00:00+00:00"),
            ]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with _tournament_patches(leftovers, lambda *_a, **_k: _dummy_windows()):
                    res = replenish_and_evaluate(max_names=4, cooldown_seconds=0)
                self.assertGreaterEqual(res["total_tested_in_batch"], 1)
                log = load_discovery_log()
                self.assertIn("fresh", {r["strategy"] for r in log})
                self.assertEqual(sum(1 for r in log if r["strategy"] == "prod_reject"), 1)
                # Newest rows are this cycle; parked fail is not re-walked.
                this_cycle = {r["strategy"] for r in log[: res["total_tested_in_batch"]]}
                self.assertNotIn("prod_reject", this_cycle)


class DiscoveryRouteTests(unittest.TestCase):
    def test_server_registers_summary_route(self):
        src = (Path(__file__).resolve().parents[1] / "hedge_fund" / "web" / "server.py").read_text()
        self.assertIn('route == "/api/discovery/summary"', src)
        self.assertIn("build_discovery_summary", src)
        self.assertIn('route == "/api/discovery/farm"', src)
        self.assertIn("set_farm_enabled", src)
        self.assertIn("X-Paper-Discovery-Token", src)
        self.assertIn("_discovery_ingest_authorized", src)


if __name__ == "__main__":
    unittest.main()
