"""Tiled-OOS rules (2026-10-08 18:08): the 448-name requalify batch re-runs
once under the new tag and waits for a worker on that tag. Rows from older
rules never flip to qualified."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.constants import GATE_RULES, QUAL_N_WINDOWS

OLD_RULES = "sltp_cap100_bhdsr_20261008"


def _eval(name, *, qualified, rules=GATE_RULES):
    return {
        "strategy": name,
        "tested_at": "2026-10-08T17:00:00+00:00",
        "qualified": qualified,
        "sharpe": 0.6,
        "trades": 120,
        "test_pnl": 2500.0,
        "bh_oos_pnl": 9000.0,
        "sma_stack_oos_pnl": -10.0,
        "daily_sharpe": 0.9 if qualified else 0.2,
        "bh_daily_sharpe": 0.5,
        "avg_hold_hours": 7.0,
        "regimes_tested": QUAL_N_WINDOWS,
        "fail_reasons": [] if qualified else ["daily_sharpe 0.20 <= bh_daily_sharpe 0.50"],
        "timeframe": "5m",
        "risk_policy": "rm_v1",
        "gate_rules": rules,
    }


class RulesV3BatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {
            "PAPER_STATE": self._tmp.name, "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
        })
        self._env.start()
        self._uni = patch("hedge_fund.trading.leases.discovery_universe", return_value=["don_hi_30"])
        self._uni.start()
        self.names = ["dip_276b_lt8pc&h4_ema_abv_240", "dip_24b_lt5pc"]
        self._names = patch(
            "hedge_fund.trading.requalify.rules_v2_batch_names", return_value=list(self.names)
        )
        self._names.start()
        self.t0 = datetime(2026, 10, 8, 17, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self._names.stop()
        self._uni.stop()
        self._env.stop()
        self._tmp.cleanup()

    def test_v3_batch_reuses_the_448_names_and_seeds_once_after_v2(self):
        from hedge_fund.trading.requalify import (
            RULES_V2_BATCH,
            RULES_V3_BATCH,
            STARTUP_BATCHES,
            load_requalify_state,
            run_requalify_startup_batches,
        )

        self.assertEqual(RULES_V3_BATCH, "rules-v3-tiled-20261008")
        self.assertEqual([b for b, _ in STARTUP_BATCHES][-1], RULES_V3_BATCH)
        seeded = run_requalify_startup_batches(self.t0)
        self.assertIn(RULES_V2_BATCH, seeded)
        self.assertIn(RULES_V3_BATCH, seeded)
        self.assertEqual(run_requalify_startup_batches(self.t0), [])
        state = load_requalify_state()
        for name in self.names:
            self.assertEqual(state["names"][name]["batch_id"], RULES_V3_BATCH)
            self.assertEqual(state["names"][name]["status"], "queued")

    def test_v2_verdict_is_kept_under_previous(self):
        from hedge_fund.trading.requalify import (
            RULES_V2_BATCH,
            ensure_rules_v2_batch_unlocked,
            ensure_rules_v3_batch_unlocked,
            load_requalify_state,
            record_requalify_unlocked,
            save_requalify_state,
        )
        from hedge_fund.trading.store import paper_state_lock

        with paper_state_lock("discovery"):
            ensure_rules_v2_batch_unlocked(self.t0)
            state = load_requalify_state()
            record_requalify_unlocked(state, _eval(self.names[0], qualified=False, rules=OLD_RULES), self.t0)
            save_requalify_state(state)
            ensure_rules_v3_batch_unlocked(self.t0)
        meta = load_requalify_state()["names"][self.names[0]]
        self.assertEqual(meta["previous"]["batch_id"], RULES_V2_BATCH)
        self.assertEqual(meta["previous"]["result"]["gate_rules"], OLD_RULES)

    def test_batch_waits_for_a_current_tag_worker(self):
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.trading.requalify import RULES_V3_BATCH, load_requalify_state, requalify_payload, run_requalify_startup_batches

        run_requalify_startup_batches(self.t0)
        stale = claim_discovery_batch(
            "omarchy-1", 4, parallel=4, now=self.t0, gate_rules=OLD_RULES, require_gate_rules=True,
        )
        self.assertEqual(stale["names"], [])
        self.assertTrue(stale["paused"])
        # A stale row posted anyway is refused and the name stays queued.
        out = ingest_discovery_payload({"evaluations": [_eval(self.names[0], qualified=True, rules=OLD_RULES)]})
        self.assertEqual(out["requalify_recorded"], [])
        self.assertEqual(load_requalify_state()["names"][self.names[0]]["status"], "queued")

        fresh = claim_discovery_batch(
            "omarchy-2", 4, parallel=4, now=self.t0, gate_rules=GATE_RULES, require_gate_rules=True,
        )
        self.assertEqual(fresh["requalify"], self.names)
        out = ingest_discovery_payload({"evaluations": [_eval(n, qualified=True) for n in self.names]})
        self.assertEqual(set(out["requalify_recorded"]), set(self.names))
        self.assertEqual(out["admitted"], [])
        payload = requalify_payload(RULES_V3_BATCH)
        self.assertEqual(payload["counts"]["pass"], 2)
        self.assertEqual(payload["results"][0]["role"], "rules_v3_candidate")
        self.assertEqual(payload["results"][0]["gate_rules"], GATE_RULES)


class OldRulesNeverFlipTests(unittest.TestCase):
    def test_parked_row_from_older_rules_does_not_flip(self):
        from hedge_fund.trading.qualify import requalify_parked_log

        row = _eval("dip_old_rules", qualified=False, rules=OLD_RULES)
        row["daily_sharpe"] = 0.9  # would pass on stored aggregates
        row["fail_reasons"] = ["daily_sharpe 0.10 <= bh_daily_sharpe 0.50"]
        flipped, names = requalify_parked_log([row], existing_names=set())
        self.assertEqual(names, [])
        self.assertFalse(row["qualified"])

        cur = {**row, "strategy": "dip_new_rules", "gate_rules": GATE_RULES}
        flipped, names = requalify_parked_log([cur], existing_names=set())
        self.assertEqual(names, ["dip_new_rules"])


if __name__ == "__main__":
    unittest.main()
