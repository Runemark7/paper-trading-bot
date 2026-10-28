"""Read-only forward candidates. There is no champion cap. Paper only."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.constants import GATE_RULES
from hedge_fund.trading.gate_v4 import v4_pass_attestation


def _champ(name: str) -> dict:
    return {"name": name, "closed": 0, "pnl": 0.0, "wins": 0, "champion_since": "2026-10-01T00:00:00+00:00"}


def _qualified(name: str, *, dsr: float | None, daily: float | None, pbo: float | None) -> dict:
    block = {}
    if dsr is not None:
        block["dsr"] = dsr
    if pbo is not None:
        block["pbo"] = pbo
    return {
        "strategy": name,
        "qualified": True,
        "ops_park": False,
        "sharpe": 0.5,
        "trades": 40,
        "daily_sharpe": daily,
        "bh_daily_sharpe": 0.2,
        "bh_oos_pnl": 10.0,
        "gate_rules": GATE_RULES,
        "gate_v4": block,
        "fail_reasons": [],
    }


def _eval(name: str, *, qualified: bool) -> dict:
    row = {
        "strategy": name,
        "tested_at": "2026-10-27T22:00:00+00:00",
        "qualified": qualified,
        "sharpe": 0.5,
        "trades": 40,
        "test_pnl": 12.0,
        "train_pnl": 1.0,
        "win_rate_pct": 50.0,
        "fail_reasons": [] if qualified else ["oos_sharpe 0.10 < 0.30"],
        "timeframe": "5m",
        "risk_policy": "rm_v1",
        "gate_rules": GATE_RULES,
    }
    if qualified:
        row["gate_v4"] = v4_pass_attestation()
    return row


class NoCapTests(unittest.TestCase):
    def test_forward_champion_cap_is_gone(self):
        import hedge_fund.trading.constants as constants
        import hedge_fund.trading.forward_slots as slots

        self.assertFalse(hasattr(constants, "FORWARD_CHAMPION_CAP"))
        self.assertFalse(hasattr(slots, "forward_cap_block"))


class PromoteAndIngestTests(unittest.TestCase):
    def test_two_active_stay_and_further_names_are_seated(self):
        from hedge_fund.trading.champions import load_pool, promote_candidates, save_pool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({"champions": [_champ("kept_a"), _champ("kept_b")], "synced_until": ""})
                out = promote_candidates([
                    {"strategy": "new_1"},
                    {"strategy": "new_2"},
                    {"strategy": "new_3"},
                    {"strategy": "new_4"},
                ])
                names = [c["name"] for c in load_pool()["champions"]]
        self.assertEqual(out["added"], ["new_1", "new_2", "new_3", "new_4"])
        self.assertEqual(names, ["kept_a", "kept_b", "new_1", "new_2", "new_3", "new_4"])
        self.assertEqual(out["active_count"], 6)
        self.assertEqual(out["refused"], [])

    def test_ingest_logs_a_pass_and_does_not_seat_it(self):
        from hedge_fund.trading.champions import load_pool, save_pool
        from hedge_fund.trading.discovery import load_discovery_log
        from hedge_fund.trading.ingest import ingest_discovery_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({"champions": [_champ(f"n{i}") for i in range(4)], "synced_until": ""})
                first = ingest_discovery_payload({"evaluations": [_eval("seat_me", qualified=True)]})
                second = ingest_discovery_payload({"evaluations": [_eval("wait_me", qualified=True)]})
                names_before = [c["name"] for c in load_pool()["champions"]]
                seated = ingest_discovery_payload({"evaluations": [], "force_admit": ["seat_me"]})
                also = ingest_discovery_payload({"evaluations": [], "force_admit": ["wait_me"]})
                names = [c["name"] for c in load_pool()["champions"]]
                logged = {row["strategy"]: row["qualified"] for row in load_discovery_log()}
        self.assertEqual(first["admitted"], [])
        self.assertEqual(first["proposed"], ["seat_me"])
        self.assertEqual(second["admitted"], [])
        self.assertEqual(second["proposed"], ["wait_me"])
        self.assertEqual(names_before, ["n0", "n1", "n2", "n3"])
        self.assertEqual(seated["force_admitted"], ["seat_me"])
        self.assertEqual(seated["admitted"], ["seat_me"])
        self.assertEqual(also["force_admitted"], ["wait_me"])
        self.assertEqual(also["admitted"], ["wait_me"])
        self.assertEqual(names, ["n0", "n1", "n2", "n3", "seat_me", "wait_me"])
        self.assertTrue(logged["seat_me"])
        self.assertTrue(logged["wait_me"])

    def test_sixth_and_fiftieth_force_admit_and_qualified_ingest_is_not_seated(self):
        from hedge_fund.trading.champions import load_pool, save_pool
        from hedge_fund.trading.discovery import load_discovery_log, save_discovery_log
        from hedge_fund.trading.ingest import ingest_discovery_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({"champions": [_champ(f"n{i}") for i in range(5)], "synced_until": ""})
                save_discovery_log([
                    _eval("seat_sixth", qualified=False),
                    _eval("seat_fiftieth", qualified=False),
                ])
                fresh = ingest_discovery_payload({
                    "evaluations": [_eval("fresh_pass", qualified=True)],
                })
                after_ingest = [c["name"] for c in load_pool()["champions"]]
                sixth = ingest_discovery_payload({
                    "evaluations": [],
                    "force_admit": ["seat_sixth"],
                })
                after_sixth = [c["name"] for c in load_pool()["champions"]]
                st = load_pool()
                while len(st["champions"]) < 49:
                    st["champions"].append(_champ(f"pad{len(st['champions'])}"))
                save_pool(st)
                fiftieth = ingest_discovery_payload({
                    "evaluations": [],
                    "force_admit": ["seat_fiftieth"],
                })
                names = [c["name"] for c in load_pool()["champions"]]
                logged = {row["strategy"]: row for row in load_discovery_log()}
        self.assertEqual(fresh["admitted"], [])
        self.assertEqual(fresh["proposed"], ["fresh_pass"])
        self.assertEqual(fresh["admission"], "manual")
        self.assertNotIn("fresh_pass", after_ingest)
        self.assertEqual(len(after_ingest), 5)
        self.assertEqual(sixth["force_admitted"], ["seat_sixth"])
        self.assertEqual(sixth["admitted"], ["seat_sixth"])
        self.assertEqual(len(after_sixth), 6)
        self.assertEqual(after_sixth[-1], "seat_sixth")
        self.assertEqual(fiftieth["force_admitted"], ["seat_fiftieth"])
        self.assertEqual(fiftieth["admitted"], ["seat_fiftieth"])
        self.assertEqual(len(names), 50)
        self.assertEqual(names[-1], "seat_fiftieth")
        self.assertNotIn("fresh_pass", names)
        self.assertTrue(logged["seat_sixth"]["qualified"])
        self.assertIn("force_admitted_at", logged["seat_sixth"])
        self.assertTrue(logged["seat_fiftieth"]["qualified"])
        self.assertIn("force_admitted_at", logged["seat_fiftieth"])
        self.assertTrue(logged["fresh_pass"]["qualified"])
        self.assertNotIn("force_admitted_at", logged["fresh_pass"])


class CandidateRankingTests(unittest.TestCase):
    def test_every_qualified_name_is_proposed_and_nobody_is_admitted(self):
        from hedge_fund.trading.champions import pool_status, save_graduated, save_pool
        from hedge_fund.trading.discovery import save_discovery_log
        from hedge_fund.trading.forward_slots import forward_candidates

        rows = [
            _qualified("a_tie", dsr=0.99, daily=0.5, pbo=0.10),
            _qualified("b_better_pbo", dsr=0.99, daily=0.5, pbo=0.05),
            _qualified("c_best_daily", dsr=0.99, daily=0.8, pbo=0.20),
            _qualified("d_next_dsr", dsr=0.98, daily=2.0, pbo=0.01),
            _qualified("f_fifth", dsr=0.97, daily=0.1, pbo=0.10),
            _qualified("e_missing_dsr", dsr=None, daily=9.0, pbo=0.0),
            _qualified("kept_a", dsr=0.99, daily=9.0, pbo=0.0),
            _qualified("grad_name", dsr=0.99, daily=9.0, pbo=0.0),
            _qualified("retired_name", dsr=0.99, daily=9.0, pbo=0.0),
            {**_qualified("ops_name", dsr=0.99, daily=9.0, pbo=0.0), "ops_park": True},
            {**_qualified("fail_name", dsr=0.99, daily=9.0, pbo=0.0), "qualified": False},
        ]
        expected = ["c_best_daily", "b_better_pbo", "a_tie", "d_next_dsr", "f_fifth", "e_missing_dsr"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({"champions": [_champ("kept_a"), _champ("kept_b")], "synced_until": ""})
                save_graduated([{"name": "grad_name", "status": "GRADUATED_PAPER"}])
                (root / "retired.json").write_text(json.dumps({
                    "retired": {"retired_name": {"role": "retired"}},
                    "applied": [],
                    "batches": [],
                }))
                save_discovery_log(rows)
                ranked = forward_candidates()
                limited = forward_candidates(limit=2)
                status = pool_status(read_only=True)
        self.assertEqual([row["strategy"] for row in ranked["proposed"]], expected)
        self.assertEqual(ranked["eligible"], 6)
        self.assertEqual(ranked["active"], 2)
        self.assertNotIn("cap", ranked)
        self.assertNotIn("open_slots", ranked)
        self.assertEqual(ranked["admitted"], [])
        self.assertEqual(ranked["retired"], [])
        for row in ranked["proposed"]:
            self.assertTrue(row["proposed"])
            self.assertFalse(row["admitted"])
            self.assertEqual(row["status"], "proposed, awaiting approval")
            self.assertEqual(row["bh_role"], "informational")
        self.assertEqual([row["strategy"] for row in limited["proposed"]], expected[:2])
        self.assertEqual(limited["eligible"], 6)
        self.assertNotIn("forward_champion_cap", status)
        self.assertEqual(status["forward_candidates"]["proposed"], ranked["proposed"])
        self.assertNotIn("cap", status["forward_candidates"])
        self.assertNotIn("open_slots", status["forward_candidates"])
        self.assertEqual(status["active_count"], 2)
