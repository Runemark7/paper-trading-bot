"""Densify #7: one axis off the current-tape swing × 200-stack near-misses.

Gate v4 (18 discovery windows) qualified nobody. The near-miss band on
that tape is ``near_swing_lo`` × ``sma_stack_20_50_200``. This batch
does not retune the OOS gate.
"""
from __future__ import annotations

import os
import re
import tempfile
import time
import unittest
from unittest.mock import patch

from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.trading.constants import (
    DISCOVERY_N_WINDOWS,
    GATE_RULES,
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    QUAL_N_WINDOWS,
    QUAL_WARMUP_BARS,
    TRADE_SYMBOLS,
)
from hedge_fund.trading.densify import keys_for
from hedge_fund.trading.discovery_guard import structure_lookbacks
from hedge_fund.trading.mint_quality import (
    canonical_name,
    clear_move_cap_cache,
    mint_block_reason,
)
from hedge_fund.trading.refill import (
    DENSE7_FAST_LONG,
    DENSE7_FAST_SHORT,
    DENSE7_MID_LONG,
    DENSE7_MID_SHORT,
    DENSE7_SLOW_LONG,
    DENSE7_SLOW_SHORT,
    DENSE7_STACK,
    DENSE7_SWING_NS,
    DENSE7_SWING_SHORT_NS,
    RECIPE_MAX_ATOMS,
    iter_dense6_names,
    iter_dense7_names,
    iter_recipe_names,
    name_has_mom_gt_and_dip,
    name_is_parseable,
    next_refill_batch,
)
from hedge_fund.trading.universe import _round_period, generate_universe, near_duplicate_key

# Already tested on prod. The depth axis must not re-mint them.
TESTED_SPINE = tuple(
    f"near_swing_lo_{n}&sma_stack_20_50_200" for n in DENSE7_SWING_NS
)
LEAD = "ema_stack_20_50_200&near_swing_lo_96"
# dip_234b is a dip lookback (19.5h), not a structure atom. N<=96 does
# not apply to it; the guard's regex is the classification.
DIP_NOT_STRUCTURE = "dip_234b_lt8pc&h4_ema_abv_140"

_STACK_RE = re.compile(r"^(sma|ema)_stack_(\d+)_(\d+)_(\d+)$")
_STRUCT_RE = re.compile(r"^(near_swing_lo|don_lo)_(\d+)$")


def _expected_count() -> int:
    ns = len(DENSE7_SWING_NS)
    periods = (
        len(DENSE7_SLOW_LONG)
        + len(DENSE7_MID_LONG)
        + len(DENSE7_FAST_LONG)
        + len(DENSE7_SLOW_SHORT)
        + len(DENSE7_MID_SHORT)
        + len(DENSE7_FAST_SHORT)
    )
    # ema twin + one-period sma steps + don_lo + shorter swing Ns.
    return ns + periods * ns + ns + len(DENSE7_SWING_SHORT_NS)


class SwingStackDenseTests(unittest.TestCase):
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
        self.assertEqual(GATE_RULES, "gate_v7_library_profit_20261029")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)
        self.assertEqual(QUAL_WARMUP_BARS, 4032)
        self.assertEqual(DISCOVERY_N_WINDOWS, 18)
        self.assertEqual(TRADE_SYMBOLS, ("BTC/USDT",))

    def test_dip_lookback_is_not_structure(self):
        self.assertEqual(structure_lookbacks(DIP_NOT_STRUCTURE), [])
        self.assertGreater(int(DIP_NOT_STRUCTURE.split("b_")[0].split("_")[1]), 96)
        self.assertIsNone(mint_block_reason(DIP_NOT_STRUCTURE))

    def test_family_is_the_recipe_prefix(self):
        family = list(iter_dense7_names())
        names = list(iter_recipe_names())
        self.assertEqual(family[0], LEAD)
        self.assertEqual(names[: len(family)], family)
        self.assertEqual(len(family), _expected_count())
        self.assertGreaterEqual(len(family), 300)
        self.assertLessEqual(len(family), 1200)
        keys = {near_duplicate_key(n) for n in family}
        self.assertEqual(len(keys), len(family))
        rest = {near_duplicate_key(n) for n in names[len(family) :]}
        self.assertTrue(keys.isdisjoint(rest))
        self.assertTrue(keys.isdisjoint({near_duplicate_key(n) for n in iter_dense6_names()}))
        for name in TESTED_SPINE:
            self.assertNotIn(name, family)
            self.assertNotIn(canonical_name(name), family)

    def test_names_are_one_axis_and_respect_bans(self):
        for name in iter_dense7_names():
            atoms = name.split("&")
            self.assertEqual(len(atoms), 2, msg=name)
            self.assertLessEqual(len(atoms), RECIPE_MAX_ATOMS)
            self.assertTrue(name_is_parseable(name), msg=name)
            parse_strategy(name)
            self.assertEqual(canonical_name(name), name, msg=name)
            self.assertEqual(near_duplicate_key(name), name, msg=name)
            self.assertIsNone(mint_block_reason(name), msg=name)
            self.assertFalse(name_has_mom_gt_and_dip(name), msg=name)
            self.assertFalse(any(a.startswith(("mom_", "dip_", "h4_", "h1_")) for a in atoms), msg=name)
            self.assertNotIn("h4×mom", keys_for(name), msg=name)
            self.assertFalse(any(a.startswith(("sma_abv_", "ema_abv_", "rsi_", "mfi_", "wt_")) for a in atoms))
            for _kind, n in structure_lookbacks(name):
                self.assertLessEqual(n, 96, msg=name)
                self.assertGreater(n, 0, msg=name)
            self._assert_one_axis(name)

    def _assert_one_axis(self, name: str) -> None:
        stack = struct = None
        for atom in name.split("&"):
            stack = _STACK_RE.match(atom) or stack
            struct = _STRUCT_RE.match(atom) or struct
        self.assertIsNotNone(stack, msg=name)
        self.assertIsNotNone(struct, msg=name)
        kind = stack.group(1)
        periods = tuple(int(stack.group(i)) for i in (2, 3, 4))
        tag = struct.group(1)
        n = int(struct.group(2))
        self.assertLess(periods[0], periods[1], msg=name)
        self.assertLess(periods[1], periods[2], msg=name)
        for period in periods:
            self.assertEqual(_round_period(period), period, msg=name)
        self.assertEqual(n % 6, 0, msg=name)
        diffs = []
        if kind != "sma":
            diffs.append("kind")
        if periods != DENSE7_STACK:
            changed = [i for i in range(3) if periods[i] != DENSE7_STACK[i]]
            self.assertEqual(len(changed), 1, msg=name)
            diffs.append("period")
        if tag != "near_swing_lo":
            diffs.append("tag")
        if n not in DENSE7_SWING_NS:
            self.assertIn(n, DENSE7_SWING_SHORT_NS, msg=name)
            diffs.append("swing")
        self.assertEqual(len(diffs), 1, msg=(name, diffs))

    def test_order_is_ema_then_slower_periods_then_don_lo(self):
        family = list(iter_dense7_names())
        ns = len(DENSE7_SWING_NS)
        period_steps = (
            len(DENSE7_SLOW_LONG)
            + len(DENSE7_MID_LONG)
            + len(DENSE7_FAST_LONG)
            + len(DENSE7_SLOW_SHORT)
            + len(DENSE7_MID_SHORT)
            + len(DENSE7_FAST_SHORT)
        )
        self.assertEqual(
            family[:4],
            [
                "ema_stack_20_50_200&near_swing_lo_96",
                "ema_stack_20_50_200&near_swing_lo_84",
                "ema_stack_20_50_200&near_swing_lo_90",
                "ema_stack_20_50_200&near_swing_lo_36",
            ],
        )
        self.assertEqual(family[ns], "near_swing_lo_96&sma_stack_20_50_210")
        don_at = ns + period_steps * ns
        self.assertEqual(family[don_at], "don_lo_96&sma_stack_20_50_200")
        self.assertEqual(family[-1], "near_swing_lo_6&sma_stack_20_50_200")
        self.assertLess(
            family.index("near_swing_lo_96&sma_stack_20_50_210"),
            family.index("near_swing_lo_96&sma_stack_20_50_190"),
        )
        self.assertLess(
            family.index("don_lo_96&sma_stack_20_50_200"),
            family.index("near_swing_lo_30&sma_stack_20_50_200"),
        )
        # The h4 gate on this spine already missed the trade floor.
        gated = "h4_sma_abv_140&near_swing_lo_96&sma_stack_20_50_200"
        self.assertNotIn(gated, set(family))

    def test_refill_batch_nonempty_when_older_families_are_taken(self):
        family = list(iter_dense7_names())
        family_set = set(family)
        taken = {n for n in iter_recipe_names() if n not in family_set}
        taken |= set(generate_universe())
        taken |= set(TESTED_SPINE)
        batch = next_refill_batch(taken_names=taken, n=50)
        self.assertEqual(batch, family[:50])
        self.assertTrue(batch)
        drained = next_refill_batch(taken_names=taken, n=100_000)
        self.assertEqual(drained, family)
        self.assertTrue(set(drained).isdisjoint(TESTED_SPINE))
        self.assertEqual(next_refill_batch(taken_names=taken | family_set, n=50), [])

    def test_refill_walk_stays_fast_on_a_prod_shaped_taken_set(self):
        """A full recipe scan must not copy the tested set per candidate.

        Prod claim timed out when ``canonical_tested_reason`` rebuilt a
        ~19k set on every recipe name. The new prefix does not change
        that cost: an exhausted recipe still walks once.
        """
        recipe = list(iter_recipe_names())
        taken = set(recipe)
        taken.update(f"pad_{i}" for i in range(19_000))
        self.assertGreaterEqual(len(taken), 19_000)
        started = time.perf_counter()
        batch = next_refill_batch(taken_names=taken, n=16)
        elapsed = time.perf_counter() - started
        self.assertEqual(batch, [])
        self.assertLess(elapsed, 2.0, f"refill walk {elapsed:.2f}s")

    def test_recipe_structure_lookback_stays_at_or_under_96(self):
        offenders = []
        for name in iter_recipe_names():
            for _kind, n in structure_lookbacks(name):
                if n > 96:
                    offenders.append(name)
                    break
            if len(offenders) >= 5:
                break
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
