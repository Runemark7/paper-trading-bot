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
    DIP_FILTERS,
    LEVEL_TRENDS,
    MOM_FILTERS,
    STRUCTURE_NS,
    TREND_FILTERS,
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
    "hammer",
    "doji",
    "morning_star",
    "evening_star",
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


def _snapshot_2026_09_11_recipe(ns: tuple[int, ...] | None = None) -> list[str]:
    """Frozen 2026-09-11 yield shape (dip×support, mom×breakout, trend×don_hi)."""
    out: list[str] = []
    for n in ns or (6, 12, 18, 24, 30, 36, 42, 48, 54, 60, 66, 72):
        for dip in DIP_FILTERS:
            out.append(f"{dip}&don_lo_{n}")
            out.append(f"{dip}&near_swing_lo_{n}")
        for mom in MOM_FILTERS:
            out.append(f"{mom}&don_hi_{n}")
            out.append(f"{mom}&near_swing_hi_{n}")
        out.append(f"dbl_bot_{n}")
        out.append(f"dbl_bot_{n}&sma_abv_50")
        out.append(f"dbl_bot_{n}&don_lo_{n}")
        out.append(f"dbl_bot_{n}&sma_stack_20_50_100")
        out.append(f"dbl_bot_{n}&ema_abv_50")
        out.append(f"don_hi_{n}")
        for trend in TREND_FILTERS:
            out.append(f"{trend}&don_hi_{n}")
        for dip in DIP_FILTERS:
            out.append(f"{dip}&don_lo_{n}&sma_abv_50")
            out.append(f"{dip}&near_swing_lo_{n}&sma_abv_50")
        for mom in MOM_FILTERS:
            out.append(f"{mom}&don_hi_{n}&sma_abv_50")
    return out


class RecipeBoundsTests(unittest.TestCase):
    def test_recipe_is_finite_and_not_thousands(self):
        names = list(iter_recipe_names())
        self.assertGreater(len(names), 400)
        self.assertLessEqual(len(names), 950)
        self.assertEqual(len(STRUCTURE_NS), 14)
        self.assertIn(84, STRUCTURE_NS)
        self.assertIn(96, STRUCTURE_NS)
        self.assertEqual(LEVEL_TRENDS, ("sma_abv_50", "ema_abv_50", "sma_stack_20_50_100"))
        self.assertEqual(DISCOVERY_REFILL_BATCH_SIZE, 16)
        self.assertLessEqual(DISCOVERY_REFILL_BATCH_SIZE, 24)
        self.assertGreaterEqual(DISCOVERY_REFILL_BATCH_SIZE, 8)
        # New near-level pass is on top of the 09-11 families, not a rewrite.
        self.assertGreater(len(names), len(_snapshot_2026_09_11_recipe(STRUCTURE_NS)))

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

    def test_recipe_includes_unused_near_level_ands(self):
        names = list(iter_recipe_names())
        # Longer unused lookbacks (7h / 8h on 5m).
        self.assertIn("dip_6b_lt2pc&don_lo_84", names)
        self.assertIn("mom_12b_gt3pc&don_hi_96", names)
        # Standalone near-level tags (already parsed; recipe used to skip them).
        self.assertIn("don_lo_12", names)
        self.assertIn("near_swing_lo_24", names)
        self.assertIn("near_swing_hi_18", names)
        # Trend at support / near swing (was only trend×don_hi).
        self.assertIn("sma_abv_50&don_lo_12", names)
        self.assertIn("ema_abv_50&near_swing_lo_18", names)
        self.assertIn("sma_stack_20_50_100&near_swing_hi_24", names)
        # 3-atom parity with mom×don_hi×sma.
        self.assertIn("mom_12b_gt3pc&near_swing_hi_12&sma_abv_50", names)
        self.assertIn("mom_12b_gt3pc&near_swing_hi_12&ema_abv_50", names)
        self.assertIn("dip_6b_lt2pc&don_lo_12&ema_abv_50", names)
        self.assertIn("mom_12b_gt3pc&don_hi_12&ema_abv_50", names)
        self.assertIn("dbl_bot_18&rsi_14_>50", names)
        self.assertIn("dbl_bot_24&sma_abv_100", names)
        blob = " ".join(names)
        for needle in ("engulfing", "hammer", "doji", "head_and_shoulders", "morning_star"):
            self.assertNotIn(needle, blob)

    def test_legacy_families_stay_first_in_the_stream(self):
        names = list(iter_recipe_names())
        legacy = _snapshot_2026_09_11_recipe(STRUCTURE_NS)
        self.assertEqual(names[: len(legacy)], legacy)

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

    def test_parking_legacy_recipe_still_feeds_near_level_names(self):
        # Prod / farm may have drained the 09-11 families. New near-level
        # keys must still refill and must not collapse onto those parked fails.
        uni = generate_universe()
        legacy = _snapshot_2026_09_11_recipe(STRUCTURE_NS)
        taken = set(uni) | set(legacy)
        taken_keys = {near_duplicate_key(n) for n in taken}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                added = next_refill_batch(taken_names=taken, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        for name in added:
            self.assertNotIn(name, taken)
            self.assertNotIn(near_duplicate_key(name), taken_keys, msg=name)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertTrue(
                any(any(tok.startswith(m) for m in _STRUCTURE_MARKERS) for tok in name.split("&")),
                msg=name,
            )

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
