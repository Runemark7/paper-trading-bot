"""Seeded random explorer: sampler, 70/30 mix, paired swaps, trials, block report."""
from __future__ import annotations

import os
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hedge_fund.trading.constants import (
    DISCOVERY_N_WINDOWS,
    DSR_MIN_PROBABILITY,
    GATE_RULES,
    HOLDOUT_START_MS,
)
from hedge_fund.trading.discovery_guard import (
    lookback_too_expensive_reason,
    ops_fail_record,
    structure_lookbacks,
)
from hedge_fund.trading.explorer import (
    AXES,
    DEPTH_WEIGHTS,
    _Taken,
    _one_atom_change,
    draw_depth,
    load_explorer_cursor,
    load_pair_links,
    mix_refill,
    propose_random,
    reject_reason,
    rng_for,
    sample_paired_names,
    sample_random_names,
    split_counts,
    swap_on_axis,
)
from hedge_fund.trading.mint_quality import mint_block_reason
from hedge_fund.trading.refill import RECIPE_MAX_ATOMS, name_has_mom_gt_and_dip, name_is_parseable
from hedge_fund.trading.universe import near_duplicate_key

_SHARES = {
    "DISCOVERY_RANDOM_SHARE": "0.70",
    "DISCOVERY_PAIRED_SHARE": "0.3333333333333333",
    "DISCOVERY_EXPLORER_SEED": "20261027",
}


def _empty_batches():
    def recipe_batch(*, taken_names, n, lift=None, lift_seed=None):
        return []

    def densify_batch(**kwargs):
        return [], True

    return recipe_batch, densify_batch


class SplitTests(unittest.TestCase):
    def test_documented_quotas(self):
        # 16 * 0.70 → 11 random; exploit 5; a third of that → 2 paired, 3 recipe.
        self.assertEqual(split_counts(100, random_share_value=0.70, paired_share_value=1 / 3), (70, 10, 20))
        self.assertEqual(split_counts(16, random_share_value=0.70, paired_share_value=1 / 3), (11, 2, 3))
        self.assertEqual(split_counts(1, random_share_value=0.70, paired_share_value=1 / 3), (1, 0, 0))
        self.assertEqual(split_counts(10, random_share_value=0.0, paired_share_value=1 / 3), (0, 3, 7))

    def test_mix_fills_each_side_and_never_dries(self):
        recipe_asks: list[int] = []

        def recipe_batch(*, taken_names, n, lift=None, lift_seed=None):
            recipe_asks.append(n)
            out = []
            i = 0
            while len(out) < n:
                name = f"zzrecipe_{i}"
                i += 1
                if name in taken_names:
                    continue
                out.append(name)
            return out

        def densify_batch(**kwargs):
            return [], True

        parents = [
            "sma_abv_50&don_hi_24",
            "ema_abv_40&don_lo_36",
            "h1_ema_abv_50&near_swing_lo_48",
            "h1_sma_abv_60&dbl_bot_30",
            "rsi_15_>50&don_hi_18",
            "mom_18b_gt2pc&sma_abv_30",
            "dip_30b_lt4pc&h4_ema_abv_150",
            "near_swing_lo_90&sma_stack_20_50_200",
        ]
        with patch.dict(os.environ, _SHARES):
            names, meta = mix_refill(
                taken_names=set(),
                n=16,
                lift=SimpleNamespace(strategy="recipe_order", informative=False),
                parents=parents,
                recipe_batch=recipe_batch,
                densify_batch=densify_batch,
                persist=False,
            )
        self.assertEqual(len(names), 16)
        self.assertEqual(len(set(names)), 16)
        self.assertEqual(meta["random"], 11)
        self.assertEqual(meta["paired"], 2)
        self.assertEqual(meta["exploit"], 3)
        self.assertEqual(meta["source"], "mixed")
        self.assertFalse(meta["exhausted"])
        self.assertEqual(meta["strategy"], "recipe_order")
        self.assertEqual(recipe_asks[0], 3)
        self.assertTrue(names[-3].startswith("zzrecipe_"))

        recipe, densify = _empty_batches()
        with patch.dict(os.environ, _SHARES):
            filled, meta = mix_refill(
                taken_names=set(),
                n=8,
                parents=[],
                recipe_batch=recipe,
                densify_batch=densify,
                persist=False,
            )
        self.assertEqual(len(filled), 8)
        self.assertEqual(meta["random"], 8)
        self.assertEqual(meta["source"], "random")
        self.assertFalse(meta["exhausted"])

        with patch.dict(os.environ, {"DISCOVERY_RANDOM_SHARE": "0", "DISCOVERY_PAIRED_SHARE": "0"}):
            dry, meta = mix_refill(
                taken_names=set(),
                n=4,
                parents=[],
                recipe_batch=recipe,
                densify_batch=densify,
                persist=False,
            )
        self.assertEqual(dry, [])
        self.assertTrue(meta["exhausted"])
        self.assertEqual(meta["random"], 0)
        self.assertEqual(meta["paired"], 0)
        self.assertEqual(meta["source"], "densify")


class SamplerTests(unittest.TestCase):
    def test_reproducible_and_cursor_does_not_repeat(self):
        first, index = sample_random_names(5, seed=20261027, start_index=0)
        again, index_again = sample_random_names(5, seed=20261027, start_index=0)
        self.assertEqual(first, again)
        self.assertEqual(index, index_again)
        self.assertGreaterEqual(index, 5)
        continued, _ = sample_random_names(5, seed=20261027, start_index=index, taken_names=first)
        self.assertEqual(len(set(first) & set(continued)), 0)
        # Same index, different seed, is a different stream.
        other, _ = sample_random_names(5, seed=20261028, start_index=0)
        self.assertNotEqual(first, other)

        recipe, densify = _empty_batches()
        with tempfile.TemporaryDirectory() as tmp:
            env = {**_SHARES, "PAPER_STATE": tmp, "DISCOVERY_RANDOM_SHARE": "1", "DISCOVERY_PAIRED_SHARE": "0"}
            with patch.dict(os.environ, env):
                batch, _meta = mix_refill(
                    taken_names=set(),
                    n=5,
                    parents=[],
                    recipe_batch=recipe,
                    densify_batch=densify,
                    persist=True,
                )
                cursor = load_explorer_cursor()
                self.assertEqual(cursor["seed"], 20261027)
                self.assertEqual(batch, first)
                self.assertEqual(cursor["draw_index"], index)
                nxt, _ = mix_refill(
                    taken_names=set(batch),
                    n=5,
                    parents=[],
                    recipe_batch=recipe,
                    densify_batch=densify,
                    persist=True,
                )
                self.assertEqual(nxt, continued)
                self.assertEqual(len(set(batch) & set(nxt)), 0)
                # A later env seed does not rewind a cursor that already exists.
                with patch.dict(os.environ, {"DISCOVERY_EXPLORER_SEED": "1"}):
                    self.assertEqual(load_explorer_cursor()["seed"], 20261027)
                self.assertGreater(load_explorer_cursor()["draw_index"], index)

    def test_depth_weights(self):
        draws = 20_000
        counts = Counter(draw_depth(rng_for(20261027, i)) for i in range(draws))
        proposed = Counter(propose_random(20261027, i)[1] for i in range(draws))
        total = sum(weight for _depth, weight in DEPTH_WEIGHTS)
        for depth, weight in DEPTH_WEIGHTS:
            expected = weight / total
            self.assertAlmostEqual(counts[depth] / draws, expected, delta=0.02)
            self.assertAlmostEqual(proposed[depth] / draws, expected, delta=0.02)
        self.assertEqual(sum(counts.values()), draws)

    def test_accepted_sample_has_zero_ban_violations(self):
        names, _index = sample_random_names(400, seed=20261027, start_index=0)
        self.assertEqual(len(names), 400)
        keys = set()
        depths = Counter()
        families = Counter()
        for name in names:
            atoms = [part for part in name.split("&") if part]
            self.assertTrue(name_is_parseable(name), name)
            self.assertFalse(name_has_mom_gt_and_dip(name), name)
            self.assertIsNone(mint_block_reason(name), name)
            self.assertIsNone(reject_reason(name), name)
            self.assertIsNone(lookback_too_expensive_reason(name), name)
            self.assertLessEqual(len(atoms), RECIPE_MAX_ATOMS)
            self.assertGreaterEqual(len(atoms), 2)
            self.assertLessEqual(len(atoms), 4, name)
            self.assertFalse(
                any(atom.startswith("h4_") for atom in atoms) and any(atom.startswith("mom_") for atom in atoms),
                name,
            )
            for _atom, lookback in structure_lookbacks(name):
                self.assertLessEqual(lookback, 96)
            key = near_duplicate_key(name)
            self.assertNotIn(key, keys, name)
            keys.add(key)
            depths[len(atoms)] += 1
            for atom in atoms:
                families[atom.split("_")[0]] += 1
        self.assertEqual(set(depths), {2, 3, 4})
        # Several families actually show up; the grid is not one spine.
        self.assertGreaterEqual(len(families), 6)


class PairedSwapTests(unittest.TestCase):
    def test_each_axis_changes_exactly_one_atom(self):
        rng = random.Random(0)
        h4_mom = swap_on_axis("h1_ema_abv_50&mom_18b_gt2pc", "timeframe", rng)
        self.assertEqual(h4_mom, "h4_ema_abv_50&mom_18b_gt2pc")
        self.assertEqual(reject_reason(h4_mom), "h4_mom")

        kind = swap_on_axis("sma_abv_50&don_hi_24", "ma_kind", random.Random(0))
        self.assertEqual(kind, "ema_abv_50&don_hi_24")
        self.assertIsNone(reject_reason(kind))
        self.assertTrue(_one_atom_change("sma_abv_50&don_hi_24", kind, "ma_kind"))

        stepped = swap_on_axis("near_swing_lo_90", "param_step", random.Random(0))
        self.assertIn(stepped, ("near_swing_lo_84", "near_swing_lo_96"))

        added = swap_on_axis("sma_abv_50&don_hi_24", "add_atom", random.Random(1))
        self.assertIsNotNone(added)
        self.assertEqual(len(added.split("&")), 3)
        self.assertTrue(_one_atom_change("sma_abv_50&don_hi_24", added, "add_atom"))
        self.assertIsNone(reject_reason(added))

        removed = swap_on_axis("sma_abv_50&don_hi_24", "remove_atom", random.Random(0))
        self.assertEqual(len(removed.split("&")), 1)
        self.assertTrue(_one_atom_change("sma_abv_50&don_hi_24", removed, "remove_atom"))
        self.assertIsNone(reject_reason(removed, min_atoms=1))
        self.assertEqual(reject_reason(removed, min_atoms=2), "too_few_atoms")

    def test_sampler_keeps_linkage_and_skips_banned_swaps(self):
        parents = ["h1_ema_abv_50&mom_18b_gt2pc", "sma_abv_50&don_hi_24"]
        rows, index = sample_paired_names(12, parents, seed=7, start_index=0, book=_Taken([]))
        self.assertGreaterEqual(len(rows), 1)
        self.assertGreater(index, 0)
        for child, parent, axis, draw in rows:
            self.assertIn(parent, parents)
            self.assertIn(axis, AXES)
            self.assertIsInstance(draw, int)
            self.assertTrue(_one_atom_change(parent, child, axis))
            self.assertIsNone(reject_reason(child, min_atoms=1), child)
            atoms = child.split("&")
            self.assertFalse(
                any(atom.startswith("h4_") for atom in atoms) and any(atom.startswith("mom_") for atom in atoms)
            )

        recipe, densify = _empty_batches()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {**_SHARES, "PAPER_STATE": tmp, "DISCOVERY_RANDOM_SHARE": "0"}):
                names, meta = mix_refill(
                    taken_names=set(),
                    n=6,
                    parents=parents,
                    recipe_batch=recipe,
                    densify_batch=densify,
                    persist=True,
                )
                links = load_pair_links()
        self.assertEqual(meta["random"], 0)
        self.assertGreater(meta["paired"], 0)
        self.assertEqual(len(links), meta["paired"])
        linked_children = {row["child"] for row in links}
        self.assertTrue(linked_children <= set(names))
        for row in links:
            self.assertIn(row["parent"], parents)
            self.assertIn(row["axis"], AXES)
            self.assertEqual(row["seed"], 20261027)


class TrialCountTests(unittest.TestCase):
    def test_random_and_paired_rows_count_ops_park_does_not(self):
        from hedge_fund.trading.discovery import append_discovery_evaluations
        from hedge_fund.trading.trials import trial_census

        names, _ = sample_random_names(1, seed=20261027, start_index=0)
        rows, _index = sample_paired_names(
            1,
            ["sma_abv_50&don_hi_24"],
            seed=3,
            start_index=0,
            book=_Taken(names),
        )
        child, parent, axis, _draw = rows[0]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                before = trial_census()
                self.assertEqual(before["n_backtested"], 0)
                append_discovery_evaluations(
                    [
                        {
                            "strategy": names[0],
                            "sharpe": 0.2,
                            "trades": 11,
                            "qualified": False,
                            "tested_at": "2026-10-27T00:00:00+00:00",
                            "fail_reasons": [],
                            "gate_rules": GATE_RULES,
                        },
                        {
                            "strategy": child,
                            "sharpe": 0.35,
                            "trades": 14,
                            "qualified": False,
                            "tested_at": "2026-10-27T00:01:00+00:00",
                            "fail_reasons": [],
                            "gate_rules": GATE_RULES,
                        },
                        ops_fail_record("near_swing_lo_192", "lookback_too_expensive 192>96"),
                    ]
                )
                after = trial_census()
        self.assertEqual(after["n_backtested"], before["n_backtested"] + 2)
        self.assertEqual(after["n_trials"], after["n_backtested"] + 1)
        self.assertEqual(after["n_ops_park"], 1)
        self.assertEqual(DSR_MIN_PROBABILITY, 0.95)
        self.assertTrue(parent)
        self.assertIn(axis, AXES)


class BlockReportTests(unittest.TestCase):
    def _row(self, name, daily, sharpe, trades, qualified, **extra):
        row = {
            "strategy": name,
            "gate_rules": GATE_RULES,
            "regimes_tested": DISCOVERY_N_WINDOWS,
            "daily_sharpe": daily,
            "sharpe": sharpe,
            "trades": trades,
            "qualified": qualified,
        }
        row.update(extra)
        return row

    def test_fixture_effects_ignore_holdout_and_old_gates(self):
        from hedge_fund.trading.block_report import build_block_report, is_pre_holdout, sign_test_p

        dip_a = "dip_30b_lt4pc&sma_abv_20"
        dip_b = "dip_36b_lt4pc&sma_abv_30"
        plain_a = "sma_abv_20&don_hi_24"
        plain_b = "sma_abv_50&don_lo_36"
        child = "h4_ema_abv_150&dip_30b_lt4pc"
        rows = [
            self._row(dip_a, 1.0, 0.5, 40, True),
            self._row(dip_b, 0.8, 0.4, 30, True),
            self._row(plain_a, -0.2, 0.1, 10, False),
            self._row(plain_b, -0.4, 0.0, 8, False),
            self._row(child, 1.4, 0.6, 22, True),
            self._row("dip_30b_lt4pc&ema_abv_20", 9.0, 9.0, 9, True, gate_rules="gate_old"),
            self._row("dip_42b_lt4pc&ema_abv_40", 9.0, 9.0, 9, True, regimes_tested=23),
            self._row(
                "near_swing_lo_90&sma_stack_20_50_200",
                0.1,
                0.1,
                4,
                False,
                ops_park=True,
                fail_reasons=["lookback_too_expensive 108>96"],
            ),
            self._row(
                "sma_abv_80&don_hi_12",
                5.0,
                5.0,
                5,
                True,
                windows=[{"end_ms": HOLDOUT_START_MS}],
            ),
        ]
        self.assertTrue(is_pre_holdout(rows[0]))
        self.assertFalse(is_pre_holdout(rows[5]))
        self.assertFalse(is_pre_holdout(rows[6]))
        self.assertFalse(is_pre_holdout(rows[7]))
        self.assertFalse(is_pre_holdout(rows[8]))
        pairs = [
            {"parent": dip_a, "child": child, "axis": "timeframe"},
            {"parent": plain_a, "child": plain_b, "axis": "timeframe"},
            {"parent": "missing", "child": dip_a, "axis": "timeframe"},
        ]
        report = build_block_report(rows, pairs)
        self.assertTrue(report["exploratory"])
        self.assertFalse(report["changes_gate"])
        self.assertIn("does not change the gate", report["label"])
        self.assertEqual(report["n_results"], 5)
        self.assertEqual(report["gate_rules"], GATE_RULES)
        families = {row["block"]: row for row in report["families"]}
        dip = families["dip"]
        self.assertEqual(dip["n_with"], 3)
        self.assertAlmostEqual(dip["mean_daily_sharpe_with"], 1.066667, places=5)
        self.assertIsNotNone(dip["depth_controlled_daily_sharpe"])
        self.assertGreater(dip["pass_rate_with"], dip["pass_rate_without"])
        paired = {row["axis"]: row for row in report["paired"]}
        self.assertEqual(set(paired), set(AXES))
        tf = paired["timeframe"]
        self.assertEqual(tf["n"], 2)
        self.assertEqual(tf["n_positive"], 1)
        self.assertEqual(tf["n_negative"], 1)
        self.assertAlmostEqual(tf["mean_daily_sharpe_delta"], 0.1, places=6)
        self.assertEqual(tf["sign_test_p"], round(sign_test_p(1, 1), 6))
        self.assertLess(tf["ci95_low"], tf["mean_daily_sharpe_delta"])
        self.assertGreater(tf["ci95_high"], tf["mean_daily_sharpe_delta"])
        self.assertEqual(paired["ma_kind"]["n"], 0)
        self.assertIsNone(paired["ma_kind"]["sign_test_p"])
        text = Path("hedge_fund/web/server.py").read_text()
        self.assertIn('"/api/discovery/blocks"', text)
        script = Path("scripts/block_report.py").read_text()
        self.assertIn("build_block_report", script)

    def test_mix_refill_does_not_read_bars(self):
        recipe, densify = _empty_batches()

        def boom(*_a, **_k):
            raise AssertionError("read_bars")

        with patch("hedge_fund.trading.holdout.read_bars", side_effect=boom):
            names, meta = mix_refill(
                taken_names=set(),
                n=4,
                parents=["sma_abv_50&don_hi_24"],
                recipe_batch=recipe,
                densify_batch=densify,
                persist=False,
            )
        self.assertEqual(len(names), 4)
        self.assertFalse(meta["exhausted"])
        self.assertTrue(hasattr(mix_refill, "__wrapped__"))


if __name__ == "__main__":
    unittest.main()
