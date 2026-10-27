"""Densify #6: deeper dip × h4, plus a swing-stack share (2026-10-11)."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from hedge_fund.trading.constants import (
    GATE_RULES,
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
    DENSE6_LOOKBACKS,
    DENSE6_PERIODS,
    DENSE6_STACK,
    DENSE6_SWING_NS,
    RECIPE_MAX_ATOMS,
    _dense6_explore,
    iter_daily_sharpe_dip_names,
    iter_dense6_names,
    iter_dense_ema150_names,
    iter_lit_trend_dip_names,
    iter_recipe_names,
    name_has_mom_gt_and_dip,
    name_is_parseable,
    next_refill_batch,
)
from hedge_fund.trading.universe import _round_period, generate_universe, near_duplicate_key

# Current-gate champions. Already tested; the depth axis does not re-mint them.
CHAMPIONS = (
    "dip_234b_lt8pc&h4_ema_abv_140",
    "dip_240b_lt10pc&h4_sma_abv_140",
)
LEAD = "dip_234b_lt12pc&h4_ema_abv_140"
SWING_SEED = "near_swing_lo_66&sma_stack_20_50_200"


def _atoms(name: str) -> list[str]:
    return name.split("&")


class DipDepthTests(unittest.TestCase):
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
        self.assertEqual(GATE_RULES, "gate_v4_btc_holdout_dsr_pbo_20261027")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)
        self.assertEqual(QUAL_WARMUP_BARS, 4032)

    def test_family_is_the_recipe_prefix(self):
        family = list(iter_dense6_names())
        names = list(iter_recipe_names())
        self.assertEqual(family[0], LEAD)
        self.assertEqual(names[: len(family)], family)
        self.assertEqual(len(family), 406)
        keys = {near_duplicate_key(n) for n in family}
        self.assertEqual(len(keys), len(family))
        rest = {near_duplicate_key(n) for n in names[len(family) :]}
        self.assertTrue(keys.isdisjoint(rest))
        for older in (
            iter_dense_ema150_names(),
            iter_daily_sharpe_dip_names(),
            iter_lit_trend_dip_names(),
        ):
            self.assertTrue(keys.isdisjoint({near_duplicate_key(n) for n in older}))
        for name in CHAMPIONS:
            self.assertNotIn(name, family)

    def test_names_respect_bans(self):
        for name in iter_dense6_names():
            atoms = _atoms(name)
            self.assertLessEqual(len(atoms), 3, msg=name)
            self.assertLessEqual(len(atoms), RECIPE_MAX_ATOMS)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertEqual(canonical_name(name), name, msg=name)
            self.assertEqual(near_duplicate_key(name), name, msg=name)
            self.assertIsNone(mint_block_reason(name), msg=name)
            self.assertFalse(name_has_mom_gt_and_dip(name), msg=name)
            self.assertFalse(any(a.startswith("mom_") for a in atoms), msg=name)
            self.assertNotIn("h4×mom", keys_for(name), msg=name)
            for _kind, n in structure_lookbacks(name):
                self.assertLessEqual(n, 96, msg=name)
            fams = [a.rsplit("_", 1)[0] for a in atoms]
            self.assertEqual(len(fams), len(set(fams)), msg=name)
            self.assertFalse(
                any(a.startswith(("sma_abv_", "ema_abv_", "rsi_")) for a in atoms),
                msg=name,
            )
            self.assertNotIn("lt9pc", name)
            self.assertNotIn("lt11pc", name)
            self.assertNotIn("lt7pc", name)

    def test_family_breakdown(self):
        explore = {canonical_name(n) for n in _dense6_explore()}
        family = list(iter_dense6_names())
        ema12 = [n for n in family if "lt12pc&h4_ema" in n]
        sma12 = [n for n in family if "lt12pc&h4_sma" in n]
        sma10 = [n for n in family if "lt10pc&h4_sma" in n]
        ema10 = [n for n in family if "lt10pc&h4_ema" in n]
        swing = [n for n in family if n in explore]
        self.assertEqual(len(ema12), 108)
        self.assertEqual(len(sma12), 108)
        self.assertEqual(len(sma10), 47)
        self.assertEqual(len(ema10), 47)
        self.assertEqual(len(swing), 96)
        self.assertEqual(len(ema12) + len(sma12) + len(sma10) + len(ema10) + len(swing), len(family))
        share = len(swing) / len(family)
        self.assertGreaterEqual(share, 0.20)
        self.assertLessEqual(share, 0.30)
        for name in ema12 + sma12:
            dip, h4 = _atoms(name)
            lookback = int(dip.split("b_")[0].split("_")[1])
            pct = int(dip.split("lt")[1].replace("pc", ""))
            period = int(h4.split("_abv_")[1])
            self.assertEqual(pct, 12, msg=name)
            self.assertIn(lookback, DENSE6_LOOKBACKS, msg=name)
            self.assertEqual(lookback % 6, 0, msg=name)
            self.assertIn(period, DENSE6_PERIODS, msg=name)
            self.assertEqual(_round_period(period), period, msg=name)
        for name in sma10 + ema10:
            dip, h4 = _atoms(name)
            lookback = int(dip.split("b_")[0].split("_")[1])
            period = int(h4.split("_abv_")[1])
            # A #5 cell (lookback × period 120–170 at 10%) is already minted.
            self.assertFalse(
                lookback in DENSE5_LOOKBACKS and period in DENSE5_PERIODS,
                msg=name,
            )

    def test_order_is_ema12_then_sma12_then_explore_then_lt10(self):
        family = list(iter_dense6_names())
        self.assertEqual(
            family[:4],
            [
                "dip_234b_lt12pc&h4_ema_abv_140",
                "dip_240b_lt12pc&h4_ema_abv_140",
                "dip_234b_lt12pc&h4_ema_abv_150",
                "dip_234b_lt12pc&h4_ema_abv_130",
            ],
        )
        self.assertEqual(family[108], "dip_240b_lt12pc&h4_sma_abv_140")
        self.assertEqual(family[216], f"near_swing_lo_{DENSE6_SWING_NS[0]}&{DENSE6_STACK}")
        self.assertEqual(
            family[222],
            f"h4_sma_abv_140&{SWING_SEED}",
        )
        self.assertEqual(family[312], "dip_252b_lt10pc&h4_sma_abv_140")
        self.assertEqual(family[359], "dip_252b_lt10pc&h4_ema_abv_140")
        first_sma12 = family.index("dip_240b_lt12pc&h4_sma_abv_140")
        first_swing = family.index(f"near_swing_lo_{DENSE6_SWING_NS[0]}&{DENSE6_STACK}")
        first_lt10 = family.index("dip_252b_lt10pc&h4_sma_abv_140")
        self.assertLess(0, first_sma12)
        self.assertLess(first_sma12, first_swing)
        self.assertLess(first_swing, first_lt10)

    def test_filler_confirm_on_champion_is_still_blocked(self):
        for confirm in ("rsi_14_>50", "sma_abv_30", "ema_abv_20"):
            name = f"{CHAMPIONS[0]}&{confirm}"
            self.assertEqual(mint_block_reason(name), REASON_FILLER, msg=name)
            self.assertNotIn(name, set(iter_dense6_names()))
        # A trend stack is not filler, so the gated swing names mint.
        self.assertIn(f"h4_sma_abv_140&{SWING_SEED}", set(iter_dense6_names()))

    def test_refill_batch_nonempty_when_older_families_are_taken(self):
        family = list(iter_dense6_names())
        family_set = set(family)
        taken = {n for n in iter_recipe_names() if n not in family_set}
        taken |= set(generate_universe())
        batch = next_refill_batch(taken_names=taken, n=50)
        self.assertEqual(batch, family[:50])
        drained = next_refill_batch(taken_names=taken, n=100_000)
        self.assertEqual(len(drained), len(family))
        self.assertGreaterEqual(len(drained), 300)
        print(f"\ndensify #6: {len(drained)} new names, next batch {batch[:4]}")
        self.assertEqual(next_refill_batch(taken_names=taken | family_set, n=50), [])


if __name__ == "__main__":
    unittest.main()
