"""Literature undry #3: slow h4 trend gate × capitulation dip (2026-10-08)."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.trading.constants import (
    DISCOVERY_REFILL_BATCH_SIZE,
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    QUAL_N_WINDOWS,
    QUAL_WARMUP_BARS,
)
from hedge_fund.trading.densify import keys_for
from hedge_fund.trading.discovery_guard import structure_lookbacks
from hedge_fund.trading.mint_quality import (
    canonical_name,
    clear_move_cap_cache,
    mint_block_reason,
)
from hedge_fund.trading.refill import (
    LITDIP_CELLS,
    LITDIP_GATES,
    RECIPE_MAX_ATOMS,
    iter_lit_trend_dip_names,
    iter_recipe_names,
    name_has_mom_gt_and_dip,
    name_is_parseable,
    next_refill_batch,
)
from hedge_fund.trading.universe import generate_universe, near_duplicate_key

LEAD = "dip_144b_lt8pc&h4_sma_abv_300"


class LitTrendDipTests(unittest.TestCase):
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

    def test_family_follows_the_daily_sharpe_prefix(self):
        from hedge_fund.trading.refill import iter_daily_sharpe_dip_names

        sharp = list(iter_daily_sharpe_dip_names())
        lit = list(iter_lit_trend_dip_names())
        names = list(iter_recipe_names())
        self.assertEqual(lit[0], LEAD)
        self.assertEqual(names[len(sharp) : len(sharp) + len(lit)], lit)
        self.assertEqual(len(lit), len(LITDIP_GATES) * len(LITDIP_CELLS))
        self.assertGreaterEqual(len(lit), 2500)
        lit_keys = {near_duplicate_key(n) for n in lit}
        self.assertEqual(len(lit_keys), len(lit))
        rest_keys = {near_duplicate_key(n) for n in names[: len(sharp)]}
        rest_keys.update(
            near_duplicate_key(n) for n in names[len(sharp) + len(lit) :]
        )
        self.assertTrue(lit_keys.isdisjoint(rest_keys))

    def test_names_respect_bans(self):
        for name in iter_lit_trend_dip_names():
            atoms = name.split("&")
            self.assertEqual(len(atoms), 2, msg=name)
            self.assertLessEqual(len(atoms), RECIPE_MAX_ATOMS)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertEqual(canonical_name(name), name, msg=name)
            self.assertIsNone(mint_block_reason(name), msg=name)
            self.assertFalse(name_has_mom_gt_and_dip(name), msg=name)
            self.assertFalse(any(a.startswith("mom_") for a in atoms), msg=name)
            self.assertNotIn("h4×mom", keys_for(name), msg=name)
            self.assertEqual(keys_for(name)[0], "h4×dip", msg=name)
            self.assertFalse(structure_lookbacks(name), msg=name)
            self.assertFalse(
                any(a.startswith(("sma_abv_", "ema_abv_", "rsi_")) for a in atoms),
                msg=name,
            )

    def test_cells_and_gates(self):
        periods = set()
        for gate in LITDIP_GATES:
            kind, period = gate.split("_abv_")
            self.assertIn(kind, ("h4_sma", "h4_ema"))
            self.assertGreaterEqual(int(period), 150)
            # 77 days of prefix before the OOS cut seeds <=462 h4 bars.
            self.assertLessEqual(int(period), 450)
            periods.add(int(period))
        self.assertEqual(len(LITDIP_GATES), 2 * len(periods))
        self.assertEqual(LITDIP_CELLS[0], (144, 8))
        self.assertEqual(len(set(LITDIP_CELLS)), len(LITDIP_CELLS))
        for lookback, pct in LITDIP_CELLS:
            self.assertEqual(lookback % 12, 0)
            self.assertTrue(96 <= lookback <= 720)
            self.assertEqual(pct % 2, 0)
            self.assertTrue(6 <= pct <= 18)

    def test_refill_batch_nonempty_when_everything_else_is_tested(self):
        lit = list(iter_lit_trend_dip_names())
        lit_set = set(lit)
        taken = {n for n in iter_recipe_names() if n not in lit_set}
        taken |= set(generate_universe())
        batch = next_refill_batch(taken_names=taken, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(batch), DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(batch[0], LEAD)
        self.assertEqual(batch, lit[:DISCOVERY_REFILL_BATCH_SIZE])
        drained = next_refill_batch(taken_names=taken, n=100_000)
        self.assertEqual(len(drained), len(lit))
        print(f"\nliterature undry #3: {len(drained)} new names, next batch {batch[:4]}")
        after = next_refill_batch(taken_names=taken | lit_set, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(after, [])

    def test_names_parse_and_trade_on_a_crash_in_uptrend(self):
        pred = parse_strategy(LEAD)
        # 60 days of grind-up then a 12h −10% drop: gate true, dip true.
        closes = [100.0 * (1.0005 ** i) for i in range(17_280)]
        top = closes[-1]
        closes += [top * (1 - 0.10 * (k + 1) / 144) for k in range(144)]
        self.assertTrue(pred(closes, len(closes) - 1))
        self.assertFalse(pred(closes, 17_279))


if __name__ == "__main__":
    unittest.main()
