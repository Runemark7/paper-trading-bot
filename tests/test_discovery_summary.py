"""Discovery summary buckets: tested / in-flight / leftover-untested."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.discovery import (
    clear_in_flight,
    latest_eval_per_strategy,
    read_in_flight,
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
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("tested_pass", qualified=True, tested_at="2026-09-05T12:00:00+00:00"),
                _eval("tested_fail", qualified=False, tested_at="2026-09-05T11:00:00+00:00"),
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
        self.assertEqual(s["counts"]["untested"], 1)
        self.assertEqual(s["counts"]["champions"], 1)
        self.assertEqual(s["counts"]["graduated"], 1)
        self.assertFalse(s["running"])
        self.assertFalse(s["in_flight"]["active"])
        self.assertEqual(s["in_flight"]["names"], [])
        self.assertIn("idle — last sweep", s["in_flight"]["note"])
        self.assertTrue(s["paper_only"])

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

    def test_tournament_writes_then_clears_in_flight(self):
        from scripts.tournament_engine import replenish_and_evaluate

        leftovers = [f"cand_{i}" for i in range(5)]
        dummy_windows = [
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
        seen = []

        def _eval(*_a, **_k):
            seen.append(read_in_flight())
            return dummy_windows

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with (
                    patch("scripts.tournament_engine.generate_candidate_pool", return_value=leftovers),
                    patch(
                        "scripts.tournament_engine._load_qual_history",
                        return_value={"BTC/USDT": [[0] * 5] * 10, "ETH/USDT": [[0] * 5] * 10},
                    ),
                    patch("scripts.tournament_engine._window_slices", return_value=[{}, {}, {}]),
                    patch("scripts.tournament_engine._benchmark_oos", return_value=(0.0, 0.0)),
                    patch("scripts.tournament_engine.parse_strategy", return_value=lambda *a, **k: True),
                    patch("scripts.tournament_engine.evaluate_windows", side_effect=_eval),
                ):
                    replenish_and_evaluate()
                self.assertIsNone(read_in_flight())
        self.assertEqual(len(seen), 5)
        self.assertEqual(seen[0]["names"], leftovers)
        self.assertEqual(seen[0]["batch_size"], 5)


class DiscoveryRouteTests(unittest.TestCase):
    def test_server_registers_summary_route(self):
        src = (Path(__file__).resolve().parents[1] / "hedge_fund" / "web" / "server.py").read_text()
        self.assertIn('route == "/api/discovery/summary"', src)
        self.assertIn("build_discovery_summary", src)


if __name__ == "__main__":
    unittest.main()
