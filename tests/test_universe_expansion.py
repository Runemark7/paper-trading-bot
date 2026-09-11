"""2026-09-11 leftover universe batch: parse, inclusion, not parked twins."""
from __future__ import annotations

import unittest

from hedge_fund.signals.dynamic import eval_predicate, parse_strategy
from hedge_fund.trading.discovery import prioritize_leftovers
from hedge_fund.trading.universe import (
    NEW_STRUCTURE_ANDS,
    UNIVERSE_TARGET_MAX,
    UNIVERSE_TARGET_MIN,
    generate_universe,
    near_duplicate_key,
    untested_candidates,
)


_STRUCTURE_MARKERS = (
    "don_hi_",
    "don_lo_",
    "near_swing_",
    "dbl_bot_",
)


class NewStructureAndsTests(unittest.TestCase):
    def test_batch_is_in_universe_and_parses(self):
        uni = generate_universe()
        self.assertGreaterEqual(len(uni), UNIVERSE_TARGET_MIN)
        self.assertLessEqual(len(uni), UNIVERSE_TARGET_MAX)
        self.assertGreaterEqual(len(NEW_STRUCTURE_ANDS), 15)
        self.assertLessEqual(len(NEW_STRUCTURE_ANDS), 30)
        closes = [100.0] * 80
        highs = [101.0] * 80
        lows = [99.0] * 80
        for name in NEW_STRUCTURE_ANDS:
            self.assertIn(name, uni)
            tokens = name.split("&")
            self.assertTrue(
                any(any(tok.startswith(m) for m in _STRUCTURE_MARKERS) for tok in tokens)
            )
            # AND short-circuits on a false close-only atom; check each
            # structure token itself so a silent False fallback cannot hide.
            for tok in tokens:
                atom = parse_strategy(tok)
                if any(tok.startswith(m) for m in _STRUCTURE_MARKERS):
                    with self.assertRaises(ValueError) as ctx:
                        atom(closes)
                    self.assertIn("high/low", str(ctx.exception).lower())
                else:
                    atom(closes)
            eval_predicate(parse_strategy(name), closes, None, highs=highs, lows=lows)

    def test_new_keys_are_not_near_duplicates_of_prior_universe(self):
        uni = generate_universe()
        prior = [n for n in uni if n not in NEW_STRUCTURE_ANDS]
        self.assertGreaterEqual(len(prior), 60)
        prior_keys = {near_duplicate_key(n) for n in prior}
        seen_new: set[str] = set()
        for name in NEW_STRUCTURE_ANDS:
            key = near_duplicate_key(name)
            self.assertNotIn(
                key,
                prior_keys,
                msg=f"{name} collapses onto a parked/prior key {key}",
            )
            self.assertNotIn(key, seen_new, msg=f"internal near-dup {name} -> {key}")
            seen_new.add(key)

    def test_parked_prior_list_leaves_new_names_eligible(self):
        uni = generate_universe()
        prior = [n for n in uni if n not in NEW_STRUCTURE_ANDS]
        log = [
            {"strategy": n, "qualified": False, "tested_at": "2026-09-10T00:00:00+00:00"}
            for n in prior
        ]
        leftovers = untested_candidates(set(), uni)
        eligible = prioritize_leftovers(leftovers, log)
        self.assertEqual(set(eligible), set(NEW_STRUCTURE_ANDS))
        self.assertFalse(set(prior) & set(eligible))

    def test_champions_and_parked_fails_are_still_skipped(self):
        uni = generate_universe()
        champ = "sma_stack"
        leftovers = untested_candidates({champ}, uni)
        self.assertNotIn(champ, leftovers)
        self.assertNotIn("sma_stack_7_25_50", leftovers)  # near-dup of fallback
        for name in NEW_STRUCTURE_ANDS:
            self.assertIn(name, leftovers)
        log = [
            {"strategy": NEW_STRUCTURE_ANDS[0], "qualified": False, "tested_at": "2026-09-11T00:00:00+00:00"},
        ]
        eligible = prioritize_leftovers(leftovers, log)
        self.assertNotIn(NEW_STRUCTURE_ANDS[0], eligible)
        self.assertIn(NEW_STRUCTURE_ANDS[1], eligible)

    def test_refused_families_stay_out(self):
        uni = generate_universe()
        blob = " ".join(uni)
        for needle in (
            "head_and_shoulders",
            "flag",
            "triangle",
            "engulfing",
            "mfi_",
            "wt_cross_down_ob",
            "sommi",
            "gold_dot",
            "dbl_top_",
            "daily(",
        ):
            self.assertNotIn(needle, blob)
        self.assertFalse(any(n.startswith("wt_") and n not in {
            "wt_cross_up_os",
            "wt_cross_up_os&sma_abv_50",
            "wt_cross_up_os&sma_stack_20_50_100",
            "wt_cross_up_os&don_lo_24",
        } for n in uni))


if __name__ == "__main__":
    unittest.main()
