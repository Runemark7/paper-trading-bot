"""Densify #4: dip×h4 neighborhood of the daily-Sharpe near-misses (2026-10-09)."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from hedge_fund.trading.constants import (
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    QUAL_N_WINDOWS,
    QUAL_WARMUP_BARS,
)
from hedge_fund.trading.densify import keys_for
from hedge_fund.trading.discovery_guard import structure_lookbacks
from hedge_fund.trading.mint_quality import (
    REASON_FILLER,
    canonical_name,
    clear_move_cap_cache,
    mint_block_reason,
)
from hedge_fund.trading.refill import (
    DSHARP_CLUSTER_LOOKBACKS,
    DSHARP_GAP_LOOKBACKS,
    DSHARP_NEW_PERIODS,
    DSHARP_SEEDS,
    LITDIP_CELLS,
    LITDIP_GATE_PERIODS,
    RECIPE_MAX_ATOMS,
    iter_daily_sharpe_dip_names,
    iter_lit_trend_dip_names,
    iter_recipe_names,
    name_has_mom_gt_and_dip,
    name_is_parseable,
    next_refill_batch,
)
from hedge_fund.trading.universe import _round_period, generate_universe, near_duplicate_key

LEAD = "dip_300b_lt8pc&h4_ema_abv_190"
# Best measured spine. A 5m confirm on it is filler_atom, so it is not minted.
BEST_SEED = "dip_300b_lt8pc&h4_ema_abv_180"
FILLER_CONFIRMS = (
    "rsi_14_>50",
    "sma_abv_30",
    "ema_abv_30",
    "sma_abv_20",
    "ema_abv_20",
)


class DailySharpeDipTests(unittest.TestCase):
    def setUp(self):
        # Static reachability caps: no tape on disk in CI.
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"PAPER_STATE": self._tmp.name})
        self._env.start()
        clear_move_cap_cache()

    def tearDown(self):
        self._env.stop()
        clear_move_cap_cache()
        self._tmp.cleanup()

    def test_gate_is_frozen(self):
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)
        self.assertEqual(QUAL_WARMUP_BARS, 4032)

    def test_family_is_the_recipe_prefix(self):
        family = list(iter_daily_sharpe_dip_names())
        names = list(iter_recipe_names())
        self.assertEqual(family[0], LEAD)
        self.assertEqual(names[: len(family)], family)
        self.assertGreaterEqual(len(family), 50)
        self.assertLessEqual(len(family), 400)
        keys = {near_duplicate_key(n) for n in family}
        self.assertEqual(len(keys), len(family))
        rest_keys = {near_duplicate_key(n) for n in names[len(family) :]}
        self.assertTrue(keys.isdisjoint(rest_keys))
        lit_keys = {near_duplicate_key(n) for n in iter_lit_trend_dip_names()}
        self.assertTrue(keys.isdisjoint(lit_keys))

    def test_names_respect_bans(self):
        lit_cells = set(LITDIP_CELLS)
        lit_periods = set(LITDIP_GATE_PERIODS)
        for name in iter_daily_sharpe_dip_names():
            atoms = name.split("&")
            self.assertEqual(len(atoms), 2, msg=name)
            self.assertLessEqual(len(atoms), RECIPE_MAX_ATOMS)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertEqual(canonical_name(name), name, msg=name)
            self.assertEqual(near_duplicate_key(name), name, msg=name)
            self.assertIsNone(mint_block_reason(name), msg=name)
            self.assertFalse(name_has_mom_gt_and_dip(name), msg=name)
            self.assertFalse(any(a.startswith("mom_") for a in atoms), msg=name)
            self.assertNotIn("h4×mom", keys_for(name), msg=name)
            self.assertEqual(keys_for(name)[0], "h4×dip", msg=name)
            self.assertFalse(structure_lookbacks(name), msg=name)
            self.assertTrue(atoms[0].startswith("dip_"), msg=name)
            self.assertTrue(atoms[1].startswith("h4_"), msg=name)
            lookback = int(atoms[0].split("b_")[0].split("_")[1])
            pct = int(atoms[0].split("lt")[1].replace("pc", ""))
            kind, period_s = atoms[1].split("_abv_")
            period = int(period_s)
            self.assertEqual(pct, 8, msg=name)
            self.assertEqual(lookback % 6, 0, msg=name)
            self.assertEqual(_round_period(period), period, msg=name)
            # A step-12 cell at a LITDIP gate was already minted. A step-6
            # lookback was not, so it may sit on a seed gate (150/180).
            if lookback % 12 == 0:
                self.assertIn((lookback, pct), lit_cells, msg=name)
                self.assertNotIn(period, lit_periods, msg=name)
            else:
                self.assertNotIn((lookback, pct), lit_cells, msg=name)

    def test_filler_confirms_are_not_minted(self):
        family = set(iter_daily_sharpe_dip_names())
        self.assertNotIn(BEST_SEED, family)
        for confirm in FILLER_CONFIRMS:
            name = f"{BEST_SEED}&{confirm}"
            self.assertEqual(mint_block_reason(name), REASON_FILLER, msg=name)
            self.assertNotIn(name, family)
            self.assertTrue(name_is_parseable(name), msg=name)

    def test_order_is_seed_then_one_axis(self):
        family = list(iter_daily_sharpe_dip_names())
        self.assertEqual(DSHARP_SEEDS[0], (300, 8, 180))
        self.assertEqual(
            family[:6],
            [
                "dip_300b_lt8pc&h4_ema_abv_190",
                "dip_300b_lt8pc&h4_ema_abv_170",
                "dip_300b_lt8pc&h4_ema_abv_200",
                "dip_300b_lt8pc&h4_ema_abv_160",
                "dip_306b_lt8pc&h4_ema_abv_180",
                "dip_294b_lt8pc&h4_ema_abv_180",
            ],
        )
        # Best seed's close ema neighborhood before the next seed and before sma.
        second = "dip_264b_lt8pc&h4_ema_abv_160"
        sma = "dip_300b_lt8pc&h4_sma_abv_190"
        cluster = "dip_288b_lt8pc&h4_ema_abv_190"
        self.assertLess(family.index(LEAD), family.index(second))
        self.assertLess(family.index(LEAD), family.index(cluster))
        self.assertLess(family.index(cluster), family.index(sma))
        self.assertEqual(DSHARP_NEW_PERIODS[0], 190)
        self.assertIn(288, DSHARP_CLUSTER_LOOKBACKS)
        self.assertIn(306, DSHARP_GAP_LOOKBACKS)
        # 7% and 9% collapse onto 8%; they are not a distinct axis.
        self.assertTrue(all("lt7pc" not in n and "lt9pc" not in n for n in family))

    def test_refill_batch_nonempty_when_prior_families_are_taken(self):
        family = list(iter_daily_sharpe_dip_names())
        family_set = set(family)
        taken = {n for n in iter_recipe_names() if n not in family_set}
        taken |= set(generate_universe())
        batch = next_refill_batch(taken_names=taken, n=50)
        self.assertEqual(len(batch), 50)
        self.assertEqual(batch, family[:50])
        self.assertEqual(batch[0], LEAD)
        for name in batch:
            self.assertNotIn(name, taken)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertIsNone(mint_block_reason(name), msg=name)
            self.assertNotIn(near_duplicate_key(name), {near_duplicate_key(n) for n in taken})
        drained = next_refill_batch(taken_names=taken, n=100_000)
        self.assertEqual(len(drained), len(family))
        print(f"\ndensify #4: {len(drained)} new names, next batch {batch[:4]}")
        after = next_refill_batch(
            taken_names=taken | family_set, n=50
        )
        self.assertEqual(after, [])


if __name__ == "__main__":
    unittest.main()
