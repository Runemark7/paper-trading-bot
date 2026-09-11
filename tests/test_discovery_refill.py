"""Auto-refill never-tested discovery names when eligible runs dry."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.signals.dynamic import eval_predicate, parse_strategy
from hedge_fund.trading.constants import (
    DISCOVER_CYCLE_MAX_NAMES,
    DISCOVERY_REFILL_BATCH_SIZE,
)
from hedge_fund.trading.discovery import prioritize_leftovers
from hedge_fund.trading.refill import (
    STRUCTURE_NS,
    discovery_universe,
    iter_recipe_names,
    load_extended_names,
    maybe_refill_discovery,
    name_is_parseable,
    name_is_refused,
    next_refill_batch,
)
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

_REFUSED_NEEDLES = (
    "head_and_shoulders",
    "flag",
    "triangle",
    "engulfing",
    "mfi_",
    "wt_",
    "sommi",
    "gold_dot",
    "dbl_top_",
    "daily(",
    "h1(",
    "m5(",
    "chart_pattern",
    "order_block",
    "fvg_",
    "candlestick",
)


def _park_log(names: list[str]) -> list[dict]:
    return [
        {"strategy": n, "qualified": False, "tested_at": "2026-09-11T00:00:00+00:00"}
        for n in names
    ]


class RecipeBoundsTests(unittest.TestCase):
    def test_recipe_is_finite_and_not_thousands(self):
        names = list(iter_recipe_names())
        self.assertGreater(len(names), 80)
        self.assertLessEqual(len(names), 500)
        self.assertEqual(len(STRUCTURE_NS), 12)
        self.assertEqual(DISCOVERY_REFILL_BATCH_SIZE, 16)
        self.assertLessEqual(DISCOVERY_REFILL_BATCH_SIZE, 24)
        self.assertGreaterEqual(DISCOVERY_REFILL_BATCH_SIZE, 8)

    def test_every_recipe_name_parses_and_has_structure(self):
        closes = [100.0] * 80
        highs = [101.0] * 80
        lows = [99.0] * 80
        seen = set()
        for name in iter_recipe_names():
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertFalse(name_is_refused(name), msg=name)
            self.assertTrue(
                any(any(tok.startswith(m) for m in _STRUCTURE_MARKERS) for tok in name.split("&")),
                msg=name,
            )
            self.assertNotIn(name, seen)
            seen.add(name)
            for tok in name.split("&"):
                atom = parse_strategy(tok)
                if any(tok.startswith(m) for m in _STRUCTURE_MARKERS):
                    with self.assertRaises(ValueError) as ctx:
                        atom(closes)
                    self.assertIn("high/low", str(ctx.exception).lower())
                else:
                    atom(closes)
            eval_predicate(parse_strategy(name), closes, None, highs=highs, lows=lows)

    def test_refused_families_stay_out_of_recipe_and_static(self):
        recipe = list(iter_recipe_names())
        recipe_blob = " ".join(recipe)
        for needle in _REFUSED_NEEDLES:
            self.assertNotIn(needle, recipe_blob)
        self.assertFalse(any("wt_" in n for n in recipe))
        uni = generate_universe()
        self.assertGreaterEqual(len(uni), UNIVERSE_TARGET_MIN)
        self.assertLessEqual(len(uni), UNIVERSE_TARGET_MAX)
        uni_blob = " ".join(uni)
        for needle in (
            "head_and_shoulders",
            "engulfing",
            "mfi_",
            "sommi",
            "gold_dot",
            "dbl_top_",
            "daily(",
        ):
            self.assertNotIn(needle, uni_blob)


class RefillBatchTests(unittest.TestCase):
    def test_empty_eligible_refill_adds_new_names(self):
        uni = generate_universe()
        log = _park_log(uni)
        leftovers = untested_candidates(set(), uni)
        eligible = prioritize_leftovers(leftovers, log)
        self.assertEqual(eligible, [])
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                added = maybe_refill_discovery(
                    eligible_count=0,
                    cap=DISCOVER_CYCLE_MAX_NAMES,
                    taken_names=set(uni) | {r["strategy"] for r in log},
                )
                self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
                self.assertTrue(set(added).isdisjoint(uni))
                persisted = load_extended_names()
                self.assertEqual(persisted, added)
                merged = discovery_universe()
                for name in added:
                    self.assertIn(name, merged)
                    self.assertTrue(name_is_parseable(name))
                leftovers2 = untested_candidates(set(), merged)
                eligible2 = prioritize_leftovers(leftovers2, log)
                self.assertEqual(set(eligible2), set(added))

    def test_refill_skips_parked_and_near_dups(self):
        uni = generate_universe()
        parked = list(NEW_STRUCTURE_ANDS) + list(uni[:10])
        champ = "sma_stack"
        taken = set(uni) | set(parked) | {champ, "sma_stack_7_25_50"}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                added = next_refill_batch(taken_names=taken, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        taken_keys = {near_duplicate_key(n) for n in taken}
        seen = set()
        for name in added:
            self.assertNotIn(name, taken)
            key = near_duplicate_key(name)
            self.assertNotIn(key, taken_keys, msg=f"{name} collapses onto {key}")
            self.assertNotIn(key, seen)
            seen.add(key)
        self.assertNotIn(champ, added)
        self.assertNotIn("sma_stack_7_25_50", added)

    def test_refill_skips_when_eligible_already_feeds_the_slice(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                added = maybe_refill_discovery(
                    eligible_count=DISCOVER_CYCLE_MAX_NAMES,
                    cap=DISCOVER_CYCLE_MAX_NAMES,
                    taken_names=generate_universe(),
                )
                self.assertEqual(added, [])
                self.assertEqual(load_extended_names(), [])

    def test_multiple_batches_over_time(self):
        taken = set(generate_universe())
        batches: list[list[str]] = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                for _ in range(4):
                    added = maybe_refill_discovery(
                        eligible_count=0,
                        cap=DISCOVER_CYCLE_MAX_NAMES,
                        taken_names=taken,
                    )
                    self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
                    batches.append(added)
                    taken.update(added)
                persisted = load_extended_names()
        flat = [n for b in batches for n in b]
        self.assertEqual(len(flat), 4 * DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(set(flat)), len(flat))
        self.assertEqual(set(persisted), set(flat))
        keys = [near_duplicate_key(n) for n in flat]
        self.assertEqual(len(keys), len(set(keys)))
        for a, b in zip(batches, batches[1:]):
            self.assertTrue(set(a).isdisjoint(b))

    def test_about_to_be_empty_also_refills(self):
        # eligible < cap is "about to be" empty even when live max is 1.
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                added = maybe_refill_discovery(
                    eligible_count=1,
                    cap=2,
                    taken_names=generate_universe(),
                )
                self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)

    def test_max_names_one_refills_only_when_eligible_empty(self):
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                skip = maybe_refill_discovery(
                    eligible_count=DISCOVER_CYCLE_MAX_NAMES,
                    cap=DISCOVER_CYCLE_MAX_NAMES,
                    taken_names=generate_universe(),
                )
                self.assertEqual(skip, [])
                added = maybe_refill_discovery(
                    eligible_count=0,
                    cap=DISCOVER_CYCLE_MAX_NAMES,
                    taken_names=generate_universe(),
                )
                self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)


class TournamentRefillIntegrationTests(unittest.TestCase):
    def test_empty_leftovers_cycle_refills_and_does_not_retest_parked(self):
        from scripts.tournament_engine import replenish_and_evaluate

        leftovers = ["failed_once", "also_parked"]
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
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(json.dumps(_park_log(leftovers)))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with (
                    patch(
                        "scripts.tournament_engine.generate_candidate_pool",
                        return_value=leftovers,
                    ),
                    patch(
                        "scripts.tournament_engine._load_qual_history",
                        return_value={"BTC/USDT": [[0] * 5] * 10, "ETH/USDT": [[0] * 5] * 10},
                    ),
                    patch("scripts.tournament_engine._window_slices", return_value=[{}, {}, {}]),
                    patch("scripts.tournament_engine._benchmark_oos", return_value=(0.0, 0.0)),
                    patch(
                        "scripts.tournament_engine.parse_strategy",
                        return_value=lambda *a, **k: True,
                    ),
                    patch(
                        "scripts.tournament_engine.evaluate_windows",
                        return_value=dummy_windows,
                    ),
                ):
                    from hedge_fund.trading.discovery import load_discovery_log

                    res = replenish_and_evaluate(max_names=2, cooldown_seconds=0)
                    self.assertEqual(res["total_tested_in_batch"], 2)
                    log = load_discovery_log()
                    new_rows = [r for r in log if r["strategy"] not in leftovers]
                    self.assertEqual(len(new_rows), 2)
                    for row in new_rows:
                        self.assertTrue(name_is_parseable(row["strategy"]))
                    self.assertEqual(
                        sum(1 for r in log if r["strategy"] == "failed_once"), 1
                    )
                    self.assertEqual(len(load_extended_names()), DISCOVERY_REFILL_BATCH_SIZE)


if __name__ == "__main__":
    unittest.main()
