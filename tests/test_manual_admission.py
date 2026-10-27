"""A qualified pass is proposed. Only an explicit admit seats it."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.constants import FORWARD_CHAMPION_CAP, GATE_RULES
from hedge_fund.trading.gate_v4 import v4_pass_attestation


def _eval(name, *, qualified=True):
    row = {
        "strategy": name,
        "tested_at": "2026-10-27T22:00:00+00:00",
        "qualified": qualified,
        "sharpe": 0.4 if qualified else 0.1,
        "trades": 40,
        "test_pnl": 12.0,
        "fail_reasons": [] if qualified else ["oos_sharpe 0.10 < 0.30"],
        "timeframe": "5m",
        "risk_policy": "rm_v1",
        "gate_rules": GATE_RULES,
        "daily_sharpe": 0.5,
        "bh_daily_sharpe": 0.4,
    }
    if qualified:
        row["gate_v4"] = v4_pass_attestation()
    return row


class ManualAdmitTests(unittest.TestCase):
    def test_ingest_does_not_seat_and_explicit_admit_respects_the_cap(self):
        from hedge_fund.trading.champions import load_pool, save_pool
        from hedge_fund.trading.forward_slots import AWAITING_APPROVAL, forward_candidates
        from hedge_fund.trading.ingest import ingest_discovery_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({
                    "champions": [
                        {"name": "kept_a", "closed": 0, "pnl": 0.0, "wins": 0},
                        {"name": "kept_b", "closed": 0, "pnl": 0.0, "wins": 0},
                    ],
                    "synced_until": "",
                })
                out = ingest_discovery_payload({"evaluations": [_eval("fresh_pass")]})
                names = [c["name"] for c in load_pool()["champions"]]
                ranked = forward_candidates()
                seated = ingest_discovery_payload({
                    "evaluations": [],
                    "force_admit": ["fresh_pass"],
                })
                after = [c["name"] for c in load_pool()["champions"]]
        self.assertEqual(out["admitted"], [])
        self.assertEqual(out["proposed"], ["fresh_pass"])
        self.assertEqual(out["admission"], "manual")
        self.assertEqual(names, ["kept_a", "kept_b"])
        self.assertEqual(AWAITING_APPROVAL, "proposed, awaiting approval")
        self.assertEqual(ranked["proposed"][0]["strategy"], "fresh_pass")
        self.assertEqual(ranked["proposed"][0]["status"], AWAITING_APPROVAL)
        self.assertEqual(seated["admitted"], ["fresh_pass"])
        self.assertEqual(after, ["kept_a", "kept_b", "fresh_pass"])
        self.assertEqual(FORWARD_CHAMPION_CAP, 5)

    def test_ui_shows_the_proposal_phrase(self):
        root = Path(__file__).resolve().parents[1]
        page = (root / "frontend/src/pages/Champions.tsx").read_text()
        types = (root / "frontend/src/api/types.ts").read_text()
        self.assertIn("proposed, awaiting approval", page)
        self.assertIn("row.status", page)
        self.assertIn("<PhoneCards>", page)
        self.assertIn("status: string", types)

    def test_collect_loop_densify_and_requalify_do_not_seat(self):
        root = Path(__file__).resolve().parents[1] / "hedge_fund" / "trading"
        for name in ("loop.py", "densify.py", "requalify.py"):
            text = (root / name).read_text()
            self.assertNotIn("_admit_qualified", text, name)
            self.assertNotIn("save_pool", text, name)
        champions = (root / "champions.py").read_text()
        self.assertIn("stray DBs must not stuff", champions)
        # collect updates champions that are already seated. A stray file
        # does not create one.
        from hedge_fund.trading.champions import collect_live_results, load_pool, save_pool
        from hedge_fund.trading.store import TradeStore

        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root_dir)}):
                save_pool({
                    "champions": [
                        {"name": "kept_a", "closed": 0, "pnl": 0.0, "wins": 0},
                        {"name": "kept_b", "closed": 0, "pnl": 0.0, "wins": 0},
                    ],
                    "synced_until": "",
                })
                TradeStore(root_dir / "trades_stray_name.sqlite").save_account_state(
                    {"broker": {"cash": 1.0, "lots": []}}
                )
                collect_live_results()
                names = [c["name"] for c in load_pool()["champions"]]
        self.assertEqual(names, ["kept_a", "kept_b"])
        self.assertNotIn("stray_name", names)


if __name__ == "__main__":
    unittest.main()
