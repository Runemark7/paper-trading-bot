"""Discovery refill buffer: thin queues mint off the claim hot path."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.constants import DISCOVERY_REFILL_BATCH_SIZE
from hedge_fund.trading.farm import FARM_HEARTBEAT_STALE_SECONDS
from hedge_fund.trading.leases import (
    REFILL_BATCH_MAX,
    REFILL_LANE_MULTIPLE,
    active_leased_lanes,
    load_lease_state,
    refill_batch_size,
    refill_buffer_unlocked,
    refill_trigger,
    save_lease_state,
    _touch,
)


def _meta(n: int) -> dict:
    return {
        "source": "random",
        "generated_last": n,
        "exhausted": False,
        "strategy": "recipe_order",
        "random": n,
        "paired": 0,
        "exploit": 0,
    }


class RefillMathTests(unittest.TestCase):
    def test_trigger_and_batch(self):
        self.assertEqual(REFILL_LANE_MULTIPLE, 4)
        self.assertEqual(REFILL_BATCH_MAX, 64)
        self.assertEqual(DISCOVERY_REFILL_BATCH_SIZE, 16)
        # 5 lanes (2+3), 13 untested, a small claim cap.
        self.assertEqual(refill_trigger(2, 5), 20)
        self.assertEqual(refill_batch_size(eligible=13, cap=2, lanes=5), 64)
        # Quiet farm: no heartbeats, empty queue, cap 4 → the old handful.
        self.assertEqual(refill_trigger(4, 0), 4)
        self.assertEqual(refill_batch_size(eligible=0, cap=4, lanes=0), 16)
        # Healthy queue does not mint.
        self.assertEqual(refill_batch_size(eligible=20, cap=2, lanes=5), 0)
        self.assertEqual(refill_batch_size(eligible=3, cap=2, lanes=0), 0)
        # Never asks for more than 64, even with a deep gap.
        self.assertLessEqual(refill_batch_size(eligible=0, cap=2, lanes=40), 64)
        self.assertEqual(refill_batch_size(eligible=0, cap=2, lanes=40), 64)


class LaneCountTests(unittest.TestCase):
    def test_recent_idle_counts_paused_and_stale_do_not(self):
        now = datetime(2026, 10, 27, 22, 0, tzinfo=timezone.utc)
        state = {"leases": {}, "workers": {}}
        _touch(state, "zealous-lion", "running", now, parallel=2)
        _touch(state, "jensa", "idle", now, parallel=3)
        _touch(state, "paused-host", "paused", now, parallel=4)
        _touch(
            state,
            "stale-host",
            "running",
            now - timedelta(seconds=FARM_HEARTBEAT_STALE_SECONDS + 1),
            parallel=9,
        )
        _touch(state, "plain", "running", now)
        self.assertEqual(active_leased_lanes(state, now), 2 + 3 + 1)

    def test_later_heartbeat_keeps_parallel(self):
        from hedge_fund.trading.leases import claim_discovery_batch, touch_worker_unlocked

        now = datetime(2026, 10, 27, 22, 0, tzinfo=timezone.utc)
        universe = [f"don_hi_{6 + 6 * i}" for i in range(8)]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp, "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600"}):
                with patch("hedge_fund.trading.leases.discovery_universe", return_value=universe):
                    with patch("hedge_fund.trading.leases.schedule_refill_buffer"):
                        claim_discovery_batch("zealous-lion", 1, parallel=2, now=now)
                touch_worker_unlocked("zealous-lion", "idle", now=now + timedelta(seconds=5))
                stored = load_lease_state()["workers"]["zealous-lion"]
        self.assertEqual(stored["parallel"], 2)
        self.assertEqual(stored["status"], "idle")


class BufferMintTests(unittest.TestCase):
    def _env(self, root: Path) -> dict:
        return {
            "PAPER_STATE": str(root),
            "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
            "DISCOVERY_RANDOM_SHARE": "0",
            "DISCOVERY_PAIRED_SHARE": "0",
        }

    def test_claim_does_not_mint_and_the_buffer_asks_for_64(self):
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.trading.refill import load_extended_names

        universe = [f"don_hi_{6 + 6 * i}" for i in range(13)]
        now = datetime(2026, 10, 27, 22, 0, tzinfo=timezone.utc)
        asked: list[int] = []

        def mint(taken, n):
            asked.append(n)
            self.assertLessEqual(n, REFILL_BATCH_MAX)
            return [f"buf_{i}" for i in range(n)], _meta(n)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, self._env(Path(tmp))):
                state = load_lease_state()
                _touch(state, "zealous-lion", "running", now, parallel=2)
                _touch(state, "jensa", "idle", now, parallel=3)
                save_lease_state(state)
                with patch("hedge_fund.trading.leases.discovery_universe", return_value=universe):
                    with patch("hedge_fund.trading.leases._mint_unlocked", side_effect=AssertionError("hot")):
                        with patch("hedge_fund.trading.leases.schedule_refill_buffer") as scheduled:
                            body = claim_discovery_batch("zealous-lion", 2, parallel=2, now=now)
                    self.assertEqual(body["names"], universe[:2])
                    self.assertEqual(body["refilled"], [])
                    scheduled.assert_called_once()
                    with patch("hedge_fund.trading.leases._mint_unlocked", side_effect=mint):
                        report = refill_buffer_unlocked(now=now)
                extended = load_extended_names()
        self.assertEqual(asked, [64])
        self.assertEqual(len(report["refilled"]), 64)
        self.assertEqual(report["lanes"], 5)
        self.assertTrue(all(name in extended for name in report["refilled"]))

    def test_healthy_buffer_does_not_refill(self):
        universe = [f"don_hi_{6 + 6 * i}" for i in range(16)]
        universe += [f"don_lo_{6 + 6 * i}" for i in range(16)]
        now = datetime(2026, 10, 27, 22, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, self._env(Path(tmp))):
                state = load_lease_state()
                _touch(state, "zealous-lion", "running", now, parallel=2)
                _touch(state, "jensa", "running", now, parallel=3)
                save_lease_state(state)
                with patch("hedge_fund.trading.leases.discovery_universe", return_value=universe):
                    with patch("hedge_fund.trading.leases._mint_unlocked", side_effect=AssertionError("mint")):
                        report = refill_buffer_unlocked(now=now)
        self.assertEqual(report["refilled"], [])
        self.assertGreaterEqual(report["eligible"], report["cap"])
        self.assertGreaterEqual(report["eligible"], REFILL_LANE_MULTIPLE * report["lanes"])

    def test_mint_bans_still_drop_and_the_seal_stays(self):
        from hedge_fund.trading.explorer import mix_refill
        from hedge_fund.trading.holdout import HoldoutLeak, read_bars, scoring_purpose
        from hedge_fund.trading.constants import HOLDOUT_START_MS
        from hedge_fund.trading.refill import load_extended_names, next_refill_batch

        banned = "mom_18b_gt2pc&dip_24b_lt5pc"
        kept = "don_hi_12"
        seen = {}

        def fake_mix(**kwargs):
            seen["n"] = kwargs["n"]
            self.assertLessEqual(kwargs["n"], REFILL_BATCH_MAX)
            return [banned, kept], _meta(2)

        now = datetime(2026, 10, 27, 22, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, self._env(Path(tmp))):
                with patch("hedge_fund.trading.leases.discovery_universe", return_value=[]):
                    with patch("hedge_fund.trading.explorer.mix_refill", side_effect=fake_mix):
                        report = refill_buffer_unlocked(now=now)
                extended = load_extended_names()
        self.assertNotIn(banned, report["refilled"])
        self.assertNotIn(banned, extended)
        self.assertIn(kept, report["refilled"])
        self.assertLessEqual(seen["n"], 64)
        self.assertTrue(hasattr(mix_refill, "__wrapped__"))
        self.assertTrue(hasattr(next_refill_batch, "__wrapped__"))
        with scoring_purpose("mint"):
            with self.assertRaises(HoldoutLeak):
                read_bars([[HOLDOUT_START_MS, 1, 1, 1, 1, 1]], purpose="holdout")

    def test_buffer_mint_does_not_hold_the_discovery_lock(self):
        from hedge_fund.trading.store import paper_state_lock

        entered = threading.Event()

        def mint(taken, n):
            def other():
                with paper_state_lock("discovery"):
                    entered.set()

            thread = threading.Thread(target=other)
            thread.start()
            thread.join(timeout=2)
            self.assertTrue(entered.is_set(), "mix_refill held the discovery lock")
            return ["buf_0"], _meta(1)

        now = datetime(2026, 10, 27, 22, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, self._env(Path(tmp))):
                with patch("hedge_fund.trading.leases.discovery_universe", return_value=[]):
                    with patch("hedge_fund.trading.leases._mint_unlocked", side_effect=mint):
                        report = refill_buffer_unlocked(now=now)
        self.assertEqual(report["refilled"], ["buf_0"])


if __name__ == "__main__":
    unittest.main()
