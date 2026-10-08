"""Tested-name index survives log trim; densify fills a dry recipe."""
from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.constants import GATE_RULES
from hedge_fund.trading.densify import (
    BURNED_MIN_TESTED,
    candidate_allowed,
    keys_for,
    next_densify_batch,
)
from hedge_fund.trading.discovery_guard import structure_lookbacks
from hedge_fund.trading.universe import near_duplicate_key


def _eval(name, *, qualified=False, tested_at="2026-10-07T12:00:00+00:00"):
    return {
        "strategy": name,
        "tested_at": tested_at,
        "qualified": qualified,
        "sharpe": 0.4 if qualified else 0.1,
        "trades": 40 if qualified else 5,
        "test_pnl": 1.0 if qualified else -1.0,
        "train_pnl": 1.0,
        "win_rate_pct": 50.0,
        "fail_reasons": [] if qualified else ["oos_sharpe 0.10 < 0.30"],
        "timeframe": "5m",
        "risk_policy": "rm_v1",
        "gate_rules": GATE_RULES,
    }


PASS = "h1_ema_abv_50&mom_18b_gt2pc"
PASS_2 = "h1_sma_abv_30&mom_18b_gt2pc"


def _empty_taken():
    return set(), set()


class TestedIndexTests(unittest.TestCase):
    def test_index_survives_log_trim_and_blocks_reclaim(self):
        from hedge_fund.trading.discovery import (
            append_discovery_evaluation,
            load_discovery_log,
            prioritize_leftovers,
            save_discovery_log,
            tested_discovery_names,
        )
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.trading.tested_index import load_tested_index

        old = "h1_ema_abv_50&mom_18b_gt2pc"
        kept = "h1_sma_abv_20&mom_18b_gt2pc"
        fresh = "h1_ema_abv_50&mom_24b_gt2pc"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                append_discovery_evaluation(_eval(old, qualified=False), cap=1)
                append_discovery_evaluation(_eval(kept, qualified=True), cap=1)
                log = load_discovery_log()
                self.assertEqual(len(log), 1)
                self.assertEqual(log[0]["strategy"], kept)
                self.assertNotIn(old, {row["strategy"] for row in log})
                self.assertIn(old, tested_discovery_names(log))
                self.assertIn(old, load_tested_index())
                self.assertFalse(load_tested_index()[old])
                self.assertTrue(load_tested_index()[kept])
                self.assertEqual(prioritize_leftovers([old, kept, fresh], log), [fresh])
                with patch(
                    "hedge_fund.trading.leases.discovery_universe",
                    return_value=[old, fresh],
                ):
                    body = claim_discovery_batch("linux-1", 1, parallel=1)
                self.assertEqual(body["names"], [fresh])
                self.assertNotIn(old, body["names"])
                # Requalify flips the flag without dropping the trimmed name.
                kept_row = dict(log[0])
                kept_row["qualified"] = False
                save_discovery_log([kept_row])
                self.assertFalse(load_tested_index()[kept])
                self.assertIn(old, load_tested_index())

    def test_direct_log_write_migrates_on_read(self):
        from hedge_fund.trading.discovery import tested_discovery_names
        from hedge_fund.trading.tested_index import load_tested_index
        import json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("don_hi_12", qualified=True),
                _eval("don_hi_24", qualified=False),
            ]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                names = tested_discovery_names()
                index = load_tested_index()
        self.assertEqual(names, {"don_hi_12", "don_hi_24"})
        self.assertTrue(index["don_hi_12"])
        self.assertFalse(index["don_hi_24"])


class DensifyRuleTests(unittest.TestCase):
    def _batch(self, index: dict[str, bool], n: int = 40) -> list[str]:
        names, _exhausted = next_densify_batch(
            taken_names=set(index),
            n=n,
            index=index,
        )
        return names

    def test_same_state_same_order(self):
        index = {PASS: True, PASS_2: True, "h1_ema_abv_24&dip_12b_lt2pc": True}
        first, exhausted = next_densify_batch(taken_names=set(index), n=12, index=index)
        flipped = dict(reversed(list(index.items())))
        second, _ = next_densify_batch(taken_names=set(flipped), n=12, index=flipped)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 12)
        self.assertFalse(exhausted)
        # h1×mom×MA/RSI seeds lead. The first name is a one-axis neighbor
        # of the lexicographically first such seed, not an h4×mom stack.
        self.assertTrue(first[0].startswith("h1_"))
        self.assertNotIn("h4_", first[0])

    def test_obeys_mom_dip_structure_neardup_and_tested(self):
        index = {
            PASS: True,
            "h1_ema_abv_24&dip_24b_lt5pc&don_hi_90": True,
        }
        names = self._batch(index, n=80)
        self.assertGreaterEqual(len(names), 20)
        taken = set(index)
        taken_keys = {near_duplicate_key(n) for n in taken}
        for name in names:
            self.assertNotIn(name, taken)
            self.assertNotIn(near_duplicate_key(name), taken_keys)
            atoms = [a for a in name.split("&") if a]
            has_mom = any(a.startswith("mom_") for a in atoms)
            has_dip = any(a.startswith("dip_") for a in atoms)
            self.assertFalse(has_mom and has_dip, name)
            for _tag, n in structure_lookbacks(name):
                self.assertLessEqual(n, 96, name)
            self.assertFalse(
                any(a.startswith("h4_") for a in atoms) and has_mom,
                name,
            )
            self.assertLessEqual(len(atoms), 7, name)
        burned = set()
        taken_set, taken_key_set = set(index), {near_duplicate_key(n) for n in index}
        self.assertFalse(candidate_allowed(
            "h1_ema_abv_50&mom_18b_gt2pc&dip_24b_lt5pc",
            taken=taken_set,
            taken_keys=taken_key_set,
            burned=burned,
        ))
        self.assertFalse(candidate_allowed(
            "h1_ema_abv_50&mom_18b_gt2pc&don_hi_108",
            taken=taken_set,
            taken_keys=taken_key_set,
            burned=burned,
        ))
        self.assertFalse(candidate_allowed(
            "h4_ema_abv_24&mom_18b_gt2pc",
            taken=taken_set,
            taken_keys=taken_key_set,
            burned=burned,
        ))
        # 48 rounds onto the tested 50-period mom stack.
        self.assertFalse(candidate_allowed(
            "h1_ema_abv_48&mom_18b_gt2pc&sma_abv_30&rsi_14_>50",
            taken=taken_set,
            taken_keys=taken_key_set,
            burned=burned,
        ))
        self.assertFalse(candidate_allowed(
            PASS,
            taken=taken_set,
            taken_keys=taken_key_set,
            burned=burned,
        ))

    def test_burned_spine_is_skipped_until_thirty_fails(self):
        seed = "h1_sma_abv_60&mom_18b_gt2pc"
        # Distinct strings on the same spine, none equal to the one-axis
        # neighbor ``h1_sma_abv_70&mom_18b_gt2pc``.
        fails = {
            f"h1_sma_abv_70&mom_18b_gt2pc&rsi_{5 + i}_>55": False
            for i in range(BURNED_MIN_TESTED)
        }
        self.assertEqual(len(fails), BURNED_MIN_TESTED)
        burned_index = {seed: True, **fails}
        spine = "h1_sma_abv_70×mom"
        self.assertIn(spine, keys_for("h1_sma_abv_70&mom_18b_gt2pc"))
        blocked, _ = next_densify_batch(
            taken_names=set(burned_index),
            n=40,
            index=burned_index,
        )
        self.assertTrue(blocked)
        self.assertTrue(all(spine not in keys_for(name) for name in blocked))

        under = {seed: True, **{k: v for i, (k, v) in enumerate(fails.items()) if i < BURNED_MIN_TESTED - 1}}
        allowed, _ = next_densify_batch(taken_names=set(under), n=40, index=under)
        self.assertTrue(any(spine in keys_for(name) for name in allowed), allowed)

    def test_h4_mom_seed_is_not_expanded(self):
        index = {
            "h4_ema_abv_24&mom_18b_gt2pc&sma_abv_30": True,
            PASS: True,
        }
        names = self._batch(index, n=30)
        for name in names:
            atoms = name.split("&")
            self.assertFalse(
                any(a.startswith("h4_") for a in atoms) and any(a.startswith("mom_") for a in atoms)
            )
        self.assertTrue(any(n.startswith("h1_") for n in names))


class ClaimUndryTests(unittest.TestCase):
    def _env(self, root: Path) -> dict:
        return {
            "PAPER_STATE": str(root),
            "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
        }

    def test_claim_returns_n_when_recipe_is_exhausted(self):
        from hedge_fund.trading.discovery import append_discovery_evaluation
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.web.discovery import build_discovery_summary

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, self._env(root)):
                append_discovery_evaluation(_eval(PASS, qualified=True))
                append_discovery_evaluation(_eval(PASS_2, qualified=True))
                with patch("hedge_fund.trading.leases.discovery_universe", return_value=[]):
                    with patch("hedge_fund.trading.leases.next_refill_batch", return_value=[]):
                        body = claim_discovery_batch("linux-1", 4, parallel=2)
                self.assertEqual(len(body["names"]), 4)
                self.assertEqual(body["refill"]["source"], "densify")
                self.assertFalse(body["refill"]["exhausted"])
                self.assertGreaterEqual(len(body["refilled"]), 4)
                self.assertEqual(body["names"], body["refilled"][:4])
                for name in body["names"]:
                    self.assertNotEqual(name, PASS)
                    self.assertNotIn("dip_", name)
                summary = build_discovery_summary(lists=False)
                self.assertEqual(summary["refill"]["source"], "densify")
                self.assertFalse(summary["refill"]["exhausted"])
                self.assertGreater(summary["refill"]["generated_last"], 0)
                self.assertEqual(summary["refill"]["eligible"], summary["counts"]["eligible"])
                self.assertGreaterEqual(summary["counts"]["tested_index"], 2)
                self.assertGreater(summary["counts"]["eligible"], 0)

    def test_low_watermark_tops_up_before_the_pool_is_empty(self):
        from hedge_fund.trading.leases import claim_discovery_batch

        universe = ["don_hi_6", "don_hi_12", "don_hi_18"]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, self._env(Path(tmp))):
                with patch(
                    "hedge_fund.trading.leases.discovery_universe",
                    return_value=universe,
                ):
                    body = claim_discovery_batch("linux-1", 2, parallel=2)
        self.assertEqual(len(body["names"]), 2)
        self.assertEqual(body["names"], universe[:2])
        self.assertGreaterEqual(len(body["refilled"]), 1)
        self.assertEqual(body["refill"]["source"], "recipe")
        self.assertFalse(body["refill"]["exhausted"])

    def test_true_exhaustion_logs_a_warning(self):
        from hedge_fund.trading.leases import claim_discovery_batch

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, self._env(Path(tmp))):
                with patch("hedge_fund.trading.leases.discovery_universe", return_value=[]):
                    with patch("hedge_fund.trading.leases.next_refill_batch", return_value=[]):
                        with self.assertLogs("hedge_fund.trading.leases", level=logging.WARNING) as logs:
                            body = claim_discovery_batch("linux-1", 2, parallel=1)
        self.assertEqual(body["names"], [])
        self.assertEqual(body["refilled"], [])
        self.assertTrue(body["refill"]["exhausted"])
        self.assertEqual(body["refill"]["source"], "densify")
        self.assertTrue(any("exhausted" in line.lower() for line in logs.output))


if __name__ == "__main__":
    unittest.main()
