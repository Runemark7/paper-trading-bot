"""Densify #5: dip×h4 box around the ``ema_abv_150`` passes (2026-10-09)."""
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
    DENSE5_LOOKBACKS,
    DENSE5_PERIODS,
    RECIPE_MAX_ATOMS,
    _dense5_explore,
    iter_daily_sharpe_dip_names,
    iter_dense_ema150_names,
    iter_lit_trend_dip_names,
    iter_recipe_names,
    name_has_mom_gt_and_dip,
    name_is_parseable,
    next_refill_batch,
)
from hedge_fund.trading.universe import _round_period, generate_universe, near_duplicate_key

# Current-gate passes (tag sltp_cap100_bhdsr_tiled87_20261008).
PASSES = (
    "dip_204b_lt8pc&h4_ema_abv_150",
    "dip_222b_lt8pc&h4_ema_abv_150",
    "dip_222b_lt8pc&h4_ema_abv_160",
)
LEAD = "dip_222b_lt8pc&h4_ema_abv_140"
STACK_SEED = "near_swing_lo_54&sma_stack_20_50_100"


def _atoms(name: str) -> list[str]:
    return name.split("&")


class DenseEma150Tests(unittest.TestCase):
    def setUp(self):
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
        family = list(iter_dense_ema150_names())
        names = list(iter_recipe_names())
        self.assertEqual(family[0], LEAD)
        self.assertEqual(names[: len(family)], family)
        self.assertGreaterEqual(len(family), 300)
        self.assertLessEqual(len(family), 600)
        keys = {near_duplicate_key(n) for n in family}
        self.assertEqual(len(keys), len(family))
        rest = {near_duplicate_key(n) for n in names[len(family) :]}
        self.assertTrue(keys.isdisjoint(rest))
        for older in (iter_daily_sharpe_dip_names(), iter_lit_trend_dip_names()):
            self.assertTrue(keys.isdisjoint({near_duplicate_key(n) for n in older}))
        # The passes themselves are already tested, never re-minted.
        for name in PASSES:
            self.assertNotIn(name, family)

    def test_names_respect_bans(self):
        for name in iter_dense_ema150_names():
            atoms = _atoms(name)
            self.assertLessEqual(len(atoms), 3, msg=name)
            self.assertLessEqual(len(atoms), RECIPE_MAX_ATOMS)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertEqual(canonical_name(name), name, msg=name)
            self.assertIsNone(mint_block_reason(name), msg=name)
            self.assertFalse(name_has_mom_gt_and_dip(name), msg=name)
            self.assertFalse(any(a.startswith("mom_") for a in atoms), msg=name)
            self.assertNotIn("h4×mom", keys_for(name), msg=name)
            for _kind, n in structure_lookbacks(name):
                self.assertLessEqual(n, 96, msg=name)
            # No 5m sma_abv / ema_abv / rsi_> filler, no same-indicator pair.
            fams = [a.rsplit("_", 1)[0] for a in atoms]
            self.assertEqual(len(fams), len(set(fams)), msg=name)
            self.assertFalse(
                any(a.startswith(("sma_abv_", "ema_abv_", "rsi_")) for a in atoms),
                msg=name,
            )

    def test_spine_box_axes(self):
        explore = {canonical_name(n) for n in _dense5_explore()}
        spine = [n for n in iter_dense_ema150_names() if n not in explore]
        self.assertGreaterEqual(len(spine), 300)
        for name in spine:
            dip, h4 = _atoms(name)
            lookback = int(dip.split("b_")[0].split("_")[1])
            pct = int(dip.split("lt")[1].replace("pc", ""))
            period = int(h4.split("_abv_")[1])
            self.assertIn(lookback, DENSE5_LOOKBACKS, msg=name)
            self.assertEqual(lookback % 6, 0, msg=name)
            self.assertIn(pct, (6, 8, 10), msg=name)
            self.assertIn(period, DENSE5_PERIODS, msg=name)
            self.assertEqual(_round_period(period), period, msg=name)
            self.assertTrue(h4.startswith(("h4_ema_abv_", "h4_sma_abv_")), msg=name)
        self.assertEqual(min(DENSE5_PERIODS), 120)
        self.assertEqual(max(DENSE5_PERIODS), 170)
        self.assertEqual(min(DENSE5_LOOKBACKS), 186)
        self.assertEqual(max(DENSE5_LOOKBACKS), 246)

    def test_exploratory_share(self):
        family = list(iter_dense_ema150_names())
        explore = {canonical_name(n) for n in _dense5_explore()}
        n_explore = sum(1 for n in family if n in explore)
        share = n_explore / len(family)
        self.assertGreaterEqual(share, 0.18)
        self.assertLessEqual(share, 0.30)

    def test_order_is_ema8_then_explore_then_sma(self):
        family = list(iter_dense_ema150_names())
        self.assertEqual(
            family[:4],
            [
                "dip_222b_lt8pc&h4_ema_abv_140",
                "dip_234b_lt8pc&h4_ema_abv_160",
                "dip_234b_lt8pc&h4_ema_abv_150",
                "dip_228b_lt8pc&h4_ema_abv_140",
            ],
        )
        first_ema10 = next(i for i, n in enumerate(family) if "lt10pc&h4_ema" in n)
        last_ema8 = max(i for i, n in enumerate(family) if "lt8pc&h4_ema" in n and n.startswith("dip_2"))
        self.assertLess(last_ema8, first_ema10)
        first_stack = next(i for i, n in enumerate(family) if "stack" in n)
        first_sma = next(i for i, n in enumerate(family) if "h4_sma" in n and "_stack" not in n)
        self.assertLess(first_ema10, first_stack)
        self.assertLess(first_stack, first_sma)

    def test_filler_confirm_on_pass_is_still_blocked(self):
        for confirm in ("rsi_14_>50", "sma_abv_30", "ema_abv_20"):
            name = f"{PASSES[1]}&{confirm}"
            self.assertEqual(mint_block_reason(name), REASON_FILLER, msg=name)
            self.assertNotIn(name, set(iter_dense_ema150_names()))
        # A trend stack is not filler, so the 3-atom support names mint.
        self.assertIn(f"h4_ema_abv_150&{STACK_SEED}", set(iter_dense_ema150_names()))

    def test_refill_batch_nonempty_when_older_families_are_taken(self):
        family = list(iter_dense_ema150_names())
        family_set = set(family)
        taken = {n for n in iter_recipe_names() if n not in family_set}
        taken |= set(generate_universe())
        batch = next_refill_batch(taken_names=taken, n=50)
        self.assertEqual(batch, family[:50])
        drained = next_refill_batch(taken_names=taken, n=100_000)
        self.assertEqual(len(drained), len(family))
        print(f"\ndensify #5: {len(drained)} new names, next batch {batch[:4]}")
        self.assertEqual(next_refill_batch(taken_names=taken | family_set, n=50), [])


if __name__ == "__main__":
    unittest.main()
