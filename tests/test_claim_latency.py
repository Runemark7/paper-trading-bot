"""Claim latency guard (2026-10-11 HTTP 504 on /api/discovery/claim).

Prod-shaped state: ~18.9k tested names, a 600k-bar live tape per symbol,
and a refill that has to walk past most of the recipe. Root cause was an
O(recipe x tested) set copy in ``canonical_tested_reason`` plus a tape
``stat`` per mom/dip atom; the claim must stay well under ingress timeout.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from hedge_fund.trading import mint_quality
from hedge_fund.trading.discovery import save_discovery_log
from hedge_fund.trading.live_tape import TAPE_DTYPE
from hedge_fund.trading.mint_quality import (
    REASON_CANONICAL,
    canonical_name,
    canonical_tested_reason,
    clear_move_cap_cache,
    max_move_pct,
)
from hedge_fund.trading.refill import iter_recipe_names, save_extended_names, warm_refill_caches
from hedge_fund.trading.tested_index import save_tested_index
from hedge_fund.trading.universe import generate_universe

# Pre-fix main measured ~3.1s per claim on the box and timed out on prod.
CLAIM_BUDGET_SECONDS = 2.0  # main took ~3.1s here (≥60s on prod); this branch ~0.5s
TAPE_BARS = 600_000
UNTESTED_TAIL = 200  # < 32 x STEER_WINDOW: steered refill walks the whole recipe, as on prod
PROD_TESTED = 18_900


def _write_tape(root: str, slug: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    arr = np.zeros(TAPE_BARS, dtype=TAPE_DTYPE)
    arr["ts"] = np.arange(TAPE_BARS, dtype=np.int64) * 300_000
    arr["close"] = 30_000.0 * np.exp(np.cumsum(rng.normal(0, 0.002, TAPE_BARS)))
    os.makedirs(os.path.join(root, "live_tape"), exist_ok=True)
    np.save(os.path.join(root, "live_tape", f"{slug}.npy"), arr)


class ClaimLatencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._env = patch.dict(os.environ, {"PAPER_STATE": cls._tmp.name})
        cls._env.start()
        clear_move_cap_cache()
        root = cls._tmp.name
        _write_tape(root, "BTC_USDT", 1)
        _write_tape(root, "ETH_USDT", 2)
        recipe = list(iter_recipe_names())
        tested = list(dict.fromkeys(generate_universe() + recipe[:-UNTESTED_TAIL]))
        # Pad to prod's tested count (the canonical set size is what hurt).
        pad = PROD_TESTED - len(tested)
        tested += [
            f"dip_{150 + i % 600}b_lt{4 + i % 9}pc&ema_abv_{20 + i // 600}"
            for i in range(max(0, pad))
        ]
        rows = [
            {
                "strategy": name,
                "qualified": False,
                "tested_at": "2026-10-09T00:00:00+00:00",
                "sharpe": 0.0,
                "trades": 0,
                "test_pnl": 0.0,
            }
            for name in tested
        ]
        save_discovery_log(rows[-10_000:])
        save_extended_names(tested, batches_emitted=1)
        save_tested_index({name: False for name in tested})
        cls.tested = tested

    @classmethod
    def tearDownClass(cls):
        cls._env.stop()
        cls._tmp.cleanup()
        clear_move_cap_cache()

    def test_claim_is_fast_on_prod_shaped_state(self):
        from hedge_fund.trading.leases import claim_discovery_batch

        self.assertGreaterEqual(len(self.tested), 18_000)
        t0 = time.monotonic()
        first = claim_discovery_batch("latency-a", 16)
        cold = time.monotonic() - t0
        t0 = time.monotonic()
        second = claim_discovery_batch("latency-b", 16)
        warm = time.monotonic() - t0
        self.assertEqual(len(first["names"]), 16)
        self.assertEqual(len(second["names"]), 16)
        self.assertFalse(set(first["names"]) & set(second["names"]))
        self.assertFalse(set(first["names"]) & set(self.tested))
        self.assertLess(cold, CLAIM_BUDGET_SECONDS, f"cold claim {cold:.2f}s")
        self.assertLess(warm, CLAIM_BUDGET_SECONDS / 2, f"warm claim {warm:.2f}s")

    def test_steered_claim_walking_whole_recipe_is_fast(self):
        """Prod path: informative lift → target x STEER_WINDOW → full recipe walk."""
        from hedge_fund.trading.leases import claim_discovery_batch

        model = SimpleNamespace(informative=True, strategy="lift_ucb", explore_share=0.2)
        with patch("hedge_fund.trading.atom_lift.lift_model_for_mint", return_value=model), patch(
            "hedge_fund.trading.atom_lift.steer_candidates",
            side_effect=lambda cands, _m, n, seed=0: list(cands[:n]),
        ):
            t0 = time.monotonic()
            out = claim_discovery_batch("latency-steer", 16)
            took = time.monotonic() - t0
        self.assertEqual(len(out["names"]), 16)
        self.assertLess(took, CLAIM_BUDGET_SECONDS, f"steered claim {took:.2f}s")

    def test_tape_rewrite_does_not_recompute_caps(self):
        cap = max_move_pct(204)
        os.utime(os.path.join(self._tmp.name, "live_tape", "BTC_USDT.npy"))
        mint_quality._token_memo[0] = None
        with patch("hedge_fund.trading.mint_quality._percentile_99") as p99:
            self.assertEqual(max_move_pct(204), cap)
        p99.assert_not_called()

    def test_warm_up_covers_recipe(self):
        self.assertEqual(warm_refill_caches(), len(list(iter_recipe_names())))


class CanonicalTestedReasonTests(unittest.TestCase):
    def test_accepts_set_list_and_empty(self):
        name = "dip_204b_lt8pc&h4_ema_abv_140"
        swapped = "h4_ema_abv_140&dip_204b_lt8pc"
        keys = {canonical_name(name)}
        self.assertEqual(canonical_tested_reason(swapped, keys), REASON_CANONICAL)
        self.assertEqual(canonical_tested_reason(swapped, list(keys)), REASON_CANONICAL)
        self.assertEqual(canonical_tested_reason(swapped, frozenset(keys)), REASON_CANONICAL)
        self.assertIsNone(canonical_tested_reason(swapped, set()))
        self.assertIsNone(canonical_tested_reason(swapped, {"x"}))


if __name__ == "__main__":
    unittest.main()
