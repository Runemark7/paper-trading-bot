"""Forward champion cap and the read-only candidate ranking. Paper only."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.constants import FORWARD_CHAMPION_CAP, GATE_RULES
from hedge_fund.trading.forward_slots import forward_cap_block
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


class CapMessageTests(unittest.TestCase):
    def test_message_names_the_cap_and_the_active_count(self):
        self.assertIsNone(forward_cap_block(4))
        self.assertEqual(
            forward_cap_block(5),
            "forward champion cap is 5; 5 active; admission refused",
        )
        self.assertEqual(
            forward_cap_block(31),
            "forward champion cap is 5; 31 active; admission refused",
        )
        self.assertEqual(FORWARD_CHAMPION_CAP, 5)


class PromoteAndIngestTests(unittest.TestCase):
    def test_two_active_stay_and_the_fifth_seat_is_refused(self):
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
        self.assertEqual(out["added"], ["new_1", "new_2", "new_3"])
        self.assertEqual(names, ["kept_a", "kept_b", "new_1", "new_2", "new_3"])
        self.assertEqual(out["active_count"], 5)
        self.assertEqual(out["refused"], [{
            "strategy": "new_4",
            "reason": "forward champion cap is 5; 5 active; admission refused",
        }])

    def test_ingest_logs_a_pass_past_the_cap_and_does_not_seat_it(self):
        from hedge_fund.trading.champions import load_pool, save_pool
        from hedge_fund.trading.discovery import load_discovery_log
        from hedge_fund.trading.ingest import ingest_discovery_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({"champions": [_champ(f"n{i}") for i in range(4)], "synced_until": ""})
                first = ingest_discovery_payload({"evaluations": [_eval("seat_me", qualified=True)]})
                second = ingest_discovery_payload({"evaluations": [_eval("wait_me", qualified=True)]})
                seated = ingest_discovery_payload({"evaluations": [], "force_admit": ["seat_me"]})
                refused = ingest_discovery_payload({"evaluations": [], "force_admit": ["wait_me"]})
                names = [c["name"] for c in load_pool()["champions"]]
                logged = {row["strategy"]: row["qualified"] for row in load_discovery_log()}
        self.assertEqual(first["admitted"], [])
        self.assertEqual(first["proposed"], ["seat_me"])
        self.assertEqual(second["admitted"], [])
        self.assertEqual(second["proposed"], ["wait_me"])
        self.assertEqual(seated["force_admitted"], ["seat_me"])
        self.assertEqual(seated["admitted"], ["seat_me"])
        self.assertEqual(refused["admitted"], [])
        self.assertIn(
            {"strategy": "wait_me", "reason": "forward champion cap is 5; 5 active; admission refused"},
            refused["skipped"],
        )
        self.assertEqual(names, ["n0", "n1", "n2", "n3", "seat_me"])
        self.assertTrue(logged["seat_me"])
        self.assertTrue(logged["wait_me"])

    def test_force_admit_refuses_before_it_rewrites_the_row(self):
        from hedge_fund.trading.champions import save_pool
        from hedge_fund.trading.discovery import load_discovery_log, save_discovery_log
        from hedge_fund.trading.ingest import ingest_discovery_payload

        parked = _eval("parked_name", qualified=False)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({"champions": [_champ(f"n{i}") for i in range(5)], "synced_until": ""})
                save_discovery_log([parked])
                out = ingest_discovery_payload({"evaluations": [], "force_admit": ["parked_name"]})
                row = load_discovery_log()[0]
                pool = json.loads((root / "champions.json").read_text())
        self.assertEqual(out["force_admitted"], [])
        self.assertEqual(out["admitted"], [])
        self.assertIn(
            {"strategy": "parked_name", "reason": "forward champion cap is 5; 5 active; admission refused"},
            out["skipped"],
        )
        self.assertFalse(row["qualified"])
        self.assertNotIn("force_admitted_at", row)
        self.assertEqual(len(pool["champions"]), 5)


class CandidateRankingTests(unittest.TestCase):
    def test_top_five_are_proposed_and_nobody_is_admitted(self):
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
                status = pool_status(read_only=True)
        self.assertEqual(
            [row["strategy"] for row in ranked["proposed"]],
            ["c_best_daily", "b_better_pbo", "a_tie", "d_next_dsr", "f_fifth"],
        )
        self.assertEqual(ranked["eligible"], 6)
        self.assertEqual(ranked["active"], 2)
        self.assertEqual(ranked["open_slots"], 3)
        self.assertEqual(ranked["cap"], 5)
        self.assertEqual(ranked["admitted"], [])
        self.assertEqual(ranked["retired"], [])
        for row in ranked["proposed"]:
            self.assertTrue(row["proposed"])
            self.assertFalse(row["admitted"])
            self.assertEqual(row["status"], "proposed, awaiting approval")
            self.assertEqual(row["bh_role"], "informational")
        self.assertEqual(status["forward_champion_cap"], 5)
        self.assertEqual(status["forward_candidates"]["proposed"], ranked["proposed"])
        self.assertEqual(status["active_count"], 2)
