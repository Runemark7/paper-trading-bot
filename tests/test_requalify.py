"""Requalify lane: re-check champions / old passes without touching fail-once or the pool."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.constants import QUAL_N_WINDOWS


def _eval(name, *, qualified=False, regimes=QUAL_N_WINDOWS, pnl=100.0, bh=500.0):
    return {
        "strategy": name,
        "tested_at": "2026-10-08T10:00:00+00:00",
        "qualified": qualified,
        "sharpe": 0.5,
        "trades": 40,
        "test_pnl": pnl,
        "bh_oos_pnl": bh,
        "sma_stack_oos_pnl": -10.0,
        "regimes_tested": regimes,
        "fail_reasons": [] if qualified else [f"oos_pnl {pnl:.2f} <= bh {bh:.2f}"],
        "timeframe": "5m",
        "risk_policy": "rm_v1",
    }


CHAMPS = ["don_hi_12", "dip_36b_lt5pc"]
GRAD = "sma_abv_200"
OLD_PASS = "don_hi_18"
FAIL = "don_hi_24"
FRESH = ["don_hi_30", "don_hi_36", "don_hi_42"]


class RequalifyTests(unittest.TestCase):
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
        from hedge_fund.trading.champions import save_graduated, save_pool
        from hedge_fund.trading.store import paper_state_lock
        from hedge_fund.trading.tested_index import save_tested_index

        save_pool({"champions": [{"name": n, "champion_since": "2026-09-14"} for n in CHAMPS + [OLD_PASS]]})
        save_graduated([{"name": GRAD, "status": "REJECTED_NEGATIVE_PNL"}])
        with paper_state_lock("discovery"):
            save_tested_index({CHAMPS[0]: True, OLD_PASS: True, FAIL: False})
        self.t0 = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self._uni.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _seed(self):
        from hedge_fund.trading.requalify import ensure_auto_batch_unlocked
        from hedge_fund.trading.store import paper_state_lock

        with paper_state_lock("discovery"):
            return ensure_auto_batch_unlocked(self.t0)

    def test_default_names_are_champions_graduated_and_passes(self):
        from hedge_fund.trading.requalify import default_requalify_names

        names = default_requalify_names()
        self.assertEqual(set(names), set(CHAMPS) | {OLD_PASS, GRAD})
        self.assertNotIn(FAIL, names)
        self.assertEqual(len(names), len(set(names)))

    def test_autoseed_is_idempotent(self):
        from hedge_fund.trading.requalify import load_requalify_state

        self.assertTrue(self._seed())
        self.assertFalse(self._seed())
        state = load_requalify_state()
        self.assertEqual(len(state["names"]), 4)
        self.assertEqual(len(state["batches"]), 1)

    def test_no_requalify_file_leaves_claim_unchanged(self):
        from hedge_fund.trading.leases import claim_discovery_batch

        body = claim_discovery_batch("omarchy-1", 3, parallel=3, now=self.t0)
        self.assertEqual(body["requalify"], [])
        self.assertEqual(set(body["names"]), set(FRESH))
        self.assertFalse((self.root / "discovery_requalify.json").exists())

    def test_claim_hands_requalify_first_and_ingest_records_separately(self):
        from hedge_fund.trading.champions import load_pool
        from hedge_fund.trading.discovery import load_discovery_log
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch, lease_snapshot
        from hedge_fund.trading.requalify import requalify_payload
        from hedge_fund.trading.tested_index import load_tested_index

        self._seed()
        pool_before = json.dumps(load_pool(), sort_keys=True)
        index_before = load_tested_index()
        first = claim_discovery_batch("omarchy-1", 6, parallel=6, now=self.t0)
        self.assertEqual(len(first["requalify"]), 4)
        self.assertEqual(first["names"][:4], first["requalify"])
        self.assertEqual(set(first["names"][4:]) <= set(FRESH), True)
        self.assertEqual(len(first["names"]), 6)
        # Requalify names are not in the discovery lease file.
        self.assertFalse(set(first["requalify"]) & set(lease_snapshot(self.t0)["active_names"]))

        evals = [_eval(CHAMPS[0], qualified=False, pnl=200.0)]
        evals.append(_eval(OLD_PASS, qualified=True, pnl=900.0))
        out = ingest_discovery_payload({"evaluations": evals, "worker_id": "omarchy-1"})
        self.assertEqual(set(out["requalify_recorded"]), {CHAMPS[0], OLD_PASS})
        self.assertEqual(out["ingested"], [])
        self.assertEqual(out["admitted"], [])
        # Pool, fail-once index and display log untouched. Nothing retired.
        self.assertEqual(json.dumps(load_pool(), sort_keys=True), pool_before)
        self.assertEqual(load_tested_index(), index_before)
        self.assertEqual(load_discovery_log(), [])

        payload = requalify_payload()
        self.assertEqual(payload["counts"]["done"], 2)
        self.assertEqual(payload["counts"]["pass"], 1)
        self.assertEqual(payload["counts"]["fail"], 1)
        self.assertEqual(payload["counts"]["leased"], 2)
        top = payload["results"][0]
        self.assertEqual(top["strategy"], OLD_PASS)
        self.assertEqual(top["pnl_minus_bh"], 400.0)
        self.assertTrue(top["current_gate"])
        self.assertEqual(top["role"], "champion")

        # A second ingest of the same name falls through to normal skip.
        again = ingest_discovery_payload({"evaluations": [_eval(OLD_PASS, qualified=True)]})
        self.assertEqual(again["requalify_recorded"], [])
        self.assertEqual(again["skipped"][0]["reason"], "already_pooled_or_graduated")

    def test_release_requeues_then_gives_up(self):
        from hedge_fund.trading.leases import claim_discovery_batch, release_discovery_leases
        from hedge_fund.trading.requalify import MAX_ATTEMPTS, requalify_payload

        self._seed()
        with patch("hedge_fund.trading.leases.next_refill_batch", return_value=[]):
            for i in range(MAX_ATTEMPTS):
                body = claim_discovery_batch("omarchy-1", 4, parallel=4, now=self.t0 + timedelta(minutes=i))
                self.assertEqual(len(body["requalify"]), 4)
                rel = release_discovery_leases("omarchy-1", names=body["requalify"], now=self.t0)
                self.assertEqual(set(rel["released"]), set(body["requalify"]))
            body = claim_discovery_batch("omarchy-1", 4, parallel=4, now=self.t0 + timedelta(hours=1))
        self.assertEqual(body["requalify"], [])
        self.assertEqual(requalify_payload()["counts"]["no_result"], 4)

    def test_expired_lease_requeues(self):
        from hedge_fund.trading.leases import claim_discovery_batch

        self._seed()
        first = claim_discovery_batch("omarchy-1", 4, parallel=4, now=self.t0)
        self.assertEqual(len(first["requalify"]), 4)
        other = claim_discovery_batch("omarchy-2", 4, parallel=4, now=self.t0 + timedelta(seconds=10))
        self.assertEqual(other["requalify"], [])
        later = claim_discovery_batch(
            "omarchy-2", 4, parallel=4, now=self.t0 + timedelta(seconds=first["ttl_seconds"] + 1)
        )
        self.assertEqual(set(later["requalify"]), set(first["requalify"]))

    def test_enqueue_validates_and_dedupes(self):
        from hedge_fund.trading.requalify import enqueue_requalify_batch

        with self.assertRaises(ValueError):
            enqueue_requalify_batch("bad id!")
        with self.assertRaises(ValueError):
            enqueue_requalify_batch("b1", names="x")
        out = enqueue_requalify_batch("b1", names=[CHAMPS[1], CHAMPS[1], " "])
        self.assertEqual(out["queued"], 1)
        again = enqueue_requalify_batch("b1", names=[CHAMPS[1]])
        self.assertEqual(again["queued"], 0)
        full = enqueue_requalify_batch("b2")
        self.assertEqual(full["queued"], 4)


if __name__ == "__main__":
    unittest.main()
