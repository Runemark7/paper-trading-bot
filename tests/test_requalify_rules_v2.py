"""Fix #1 (Alexander 2026-10-08 16:32): one-time rules-v2 requalify batch.

Seeds the requalify lane once at server start with the 442 B&H-only
near-misses plus the 51 local rules-v2 passes. Idempotent (marker in
``discovery_requalify.json["seeded"]``), no token. The lane never admits,
never retires, never touches fail-once.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.constants import GATE_RULES, QUAL_N_WINDOWS

CHAMP = "don_hi_12"
FRESH = ["don_hi_30", "don_hi_36", "don_hi_42"]


def _eval(name, *, qualified):
    return {
        "strategy": name,
        "tested_at": "2026-10-08T15:00:00+00:00",
        "qualified": qualified,
        "sharpe": 0.6,
        "trades": 60,
        "test_pnl": 2500.0,
        "bh_oos_pnl": 31910.0,
        "sma_stack_oos_pnl": -10.0,
        "daily_sharpe": 1.9 if qualified else 0.5,
        "bh_daily_sharpe": 1.635,
        "avg_hold_hours": 7.3,
        "regimes_tested": QUAL_N_WINDOWS,
        "fail_reasons": [] if qualified else ["daily_sharpe 0.50 <= bh_daily_sharpe 1.63"],
        "timeframe": "5m",
        "risk_policy": "rm_v1",
        "gate_rules": GATE_RULES,
    }


class BundledNamesTests(unittest.TestCase):
    def test_bundle_is_448_unique_names_passes_first(self):
        from hedge_fund.trading.requalify import (
            MAX_BATCH_NAMES,
            RULES_V2_BATCH,
            rules_v2_batch_names,
        )

        names = rules_v2_batch_names()
        self.assertEqual(len(names), 448)
        self.assertEqual(len(set(names)), 448)
        self.assertLessEqual(len(names), MAX_BATCH_NAMES)
        self.assertEqual(names[0], "dip_276b_lt8pc&h4_ema_abv_240")
        self.assertTrue(all(isinstance(n, str) and n.strip() == n and n for n in names))
        self.assertEqual(RULES_V2_BATCH, "rules-v2-nearmiss-20261008")

    def test_bundle_is_stamped_with_current_rules(self):
        path = Path(__file__).resolve().parents[1] / "hedge_fund/trading/requalify_rules_v2_names.json"
        data = json.loads(path.read_text())
        # Built under rules v2; the v3 (tiled) batch re-uses the same names.
        self.assertEqual(data["gate_rules"], "sltp_cap100_bhdsr_20261008")
        self.assertEqual(data["batch_id"], "rules-v2-nearmiss-20261008")

    def test_every_name_parses(self):
        from hedge_fund.trading.requalify import rules_v2_batch_names
        from hedge_fund.signals.dynamic import parse_strategy

        for name in rules_v2_batch_names():
            self.assertTrue(callable(parse_strategy(name)), name)


class RulesV2BatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = patch.dict(os.environ, {
            "PAPER_STATE": str(self.root),
            "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
        })
        self._env.start()
        self._uni = patch("hedge_fund.trading.leases.discovery_universe", return_value=list(FRESH))
        self._uni.start()
        self.names = ["dip_276b_lt8pc&h4_ema_abv_240", "dip_24b_lt5pc", "dip_36b_lt5pc&h4_sma_abv_150"]
        self._names = patch(
            "hedge_fund.trading.requalify.rules_v2_batch_names", return_value=list(self.names)
        )
        self._names.start()
        from hedge_fund.trading.champions import save_pool
        from hedge_fund.trading.store import paper_state_lock
        from hedge_fund.trading.tested_index import save_tested_index

        save_pool({"champions": [{"name": CHAMP, "champion_since": "2026-09-14"}]})
        with paper_state_lock("discovery"):
            # Near-misses already failed once under the old rules.
            save_tested_index({CHAMP: True, **{n: False for n in self.names}})
        self.t0 = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self._names.stop()
        self._uni.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _seed_v2(self):
        from hedge_fund.trading.requalify import ensure_rules_v2_batch_unlocked
        from hedge_fund.trading.store import paper_state_lock

        with paper_state_lock("discovery"):
            return ensure_rules_v2_batch_unlocked(self.t0)

    def test_seeds_once_with_marker(self):
        from hedge_fund.trading.requalify import RULES_V2_BATCH, load_requalify_state

        self.assertTrue(self._seed_v2())
        self.assertFalse(self._seed_v2())
        state = load_requalify_state()
        self.assertIn(RULES_V2_BATCH, state["seeded"])
        self.assertEqual(len(state["batches"]), 1)
        self.assertEqual(state["batches"][0]["size"], 3)
        self.assertEqual(state["batches"][0]["gate_rules"], GATE_RULES)
        for name in self.names:
            self.assertEqual(state["names"][name]["batch_id"], RULES_V2_BATCH)
            self.assertEqual(state["names"][name]["status"], "queued")

    def test_marker_survives_finished_batch(self):
        """Restart after the batch is done must not re-queue it."""
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.trading.requalify import load_requalify_state

        self._seed_v2()
        claim_discovery_batch("omarchy-9", 3, parallel=3, now=self.t0, gate_rules=GATE_RULES)
        ingest_discovery_payload({"evaluations": [_eval(n, qualified=False) for n in self.names]})
        self.assertFalse(self._seed_v2())
        statuses = {m["status"] for m in load_requalify_state()["names"].values()}
        self.assertEqual(statuses, {"done"})

    def test_coexists_with_gate23_batch(self):
        from hedge_fund.trading.requalify import (
            REQUALIFY_AUTO_BATCH,
            RULES_V2_BATCH,
            ensure_auto_batch_unlocked,
            load_requalify_state,
        )
        from hedge_fund.trading.store import paper_state_lock

        with paper_state_lock("discovery"):
            self.assertTrue(ensure_auto_batch_unlocked(self.t0))
        before = load_requalify_state()["names"][CHAMP]
        self.assertTrue(self._seed_v2())
        state = load_requalify_state()
        self.assertEqual(state["seeded"], [REQUALIFY_AUTO_BATCH, RULES_V2_BATCH])
        self.assertEqual(state["names"][CHAMP], before)

    def test_does_not_clobber_a_finished_result_from_another_batch(self):
        from hedge_fund.trading.requalify import load_requalify_state, save_requalify_state
        from hedge_fund.trading.store import paper_state_lock

        with paper_state_lock("discovery"):
            state = load_requalify_state()
            state["names"][self.names[1]] = {
                "batch_id": "gate23-20261008", "status": "done", "result": {"qualified": False},
            }
            save_requalify_state(state)
        self._seed_v2()
        meta = load_requalify_state()["names"][self.names[1]]
        self.assertEqual(meta["status"], "queued")
        self.assertEqual(meta["previous"]["batch_id"], "gate23-20261008")
        self.assertEqual(meta["previous"]["result"], {"qualified": False})

    def test_claim_ingest_never_admits_retires_or_touches_fail_once(self):
        from hedge_fund.trading.champions import load_pool, load_retired
        from hedge_fund.trading.discovery import load_discovery_log
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.trading.requalify import RULES_V2_BATCH, requalify_payload
        from hedge_fund.trading.retire import run_auto_retire
        from hedge_fund.trading.tested_index import load_tested_index

        self._seed_v2()
        pool_before = json.dumps(load_pool(), sort_keys=True)
        index_before = load_tested_index()
        body = claim_discovery_batch("omarchy-9", 5, parallel=5, now=self.t0, gate_rules=GATE_RULES)
        self.assertEqual(body["requalify"], self.names)
        self.assertEqual(body["names"][:3], self.names)

        evals = [_eval(self.names[0], qualified=True)]
        evals += [_eval(n, qualified=False) for n in self.names[1:]]
        out = ingest_discovery_payload({"evaluations": evals, "worker_id": "omarchy-9"})
        self.assertEqual(set(out["requalify_recorded"]), set(self.names))
        self.assertEqual(out["admitted"], [])
        self.assertEqual(out["ingested"], [])
        self.assertEqual(json.dumps(load_pool(), sort_keys=True), pool_before)
        self.assertEqual(load_tested_index(), index_before)
        self.assertEqual(load_discovery_log(), [])

        run_auto_retire()
        self.assertEqual(load_retired().get("retired") or {}, {})
        self.assertEqual(json.dumps(load_pool(), sort_keys=True), pool_before)

        payload = requalify_payload(RULES_V2_BATCH)
        self.assertEqual(payload["counts"]["pass"], 1)
        self.assertEqual(payload["counts"]["fail"], 2)
        top = payload["results"][0]
        self.assertEqual(top["strategy"], self.names[0])
        self.assertEqual(top["role"], "rules_v2_candidate")
        self.assertEqual(top["daily_sharpe"], 1.9)
        self.assertEqual(top["bh_daily_sharpe"], 1.635)
        self.assertEqual(top["avg_hold_hours"], 7.3)
        self.assertIn(RULES_V2_BATCH, payload["startup_batches"])

    def test_stale_worker_rows_are_not_recorded(self):
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.trading.requalify import load_requalify_state

        self._seed_v2()
        claim_discovery_batch("omarchy-9", 3, parallel=3, now=self.t0, gate_rules=GATE_RULES)
        stale = _eval(self.names[0], qualified=True)
        stale.pop("gate_rules")
        out = ingest_discovery_payload({"evaluations": [stale]})
        self.assertEqual(out["requalify_recorded"], [])
        self.assertEqual(load_requalify_state()["names"][self.names[0]]["status"], "leased")

    def test_startup_runs_every_startup_batch_once(self):
        from hedge_fund.trading.requalify import (
            REQUALIFY_AUTO_BATCH,
            RULES_V2_BATCH,
            RULES_V3_BATCH,
            load_requalify_state,
            run_requalify_startup_batches,
        )

        first = run_requalify_startup_batches(self.t0)
        self.assertEqual(first, [REQUALIFY_AUTO_BATCH, RULES_V2_BATCH, RULES_V3_BATCH])
        self.assertEqual(run_requalify_startup_batches(self.t0), [])
        self.assertEqual(len(load_requalify_state()["batches"]), 3)


if __name__ == "__main__":
    unittest.main()
