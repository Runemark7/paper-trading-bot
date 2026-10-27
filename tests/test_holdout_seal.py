"""The sealed holdout is a code guard, not a convention."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from hedge_fund.trading.constants import (
    DISCOVERY_N_WINDOWS,
    HOLDOUT_END_MS,
    HOLDOUT_START_MS,
    TF_MS_5M,
)
from hedge_fund.trading.holdout import (
    HoldoutLeak,
    assert_no_holdout,
    discovery_rows,
    read_bars,
    scoring_purpose,
)


def _bar(ts: int) -> list:
    return [ts, 1.0, 1.1, 0.9, 1.0, 1.0]


class ReadBarsTests(unittest.TestCase):
    def test_discovery_purposes_refuse_a_sealed_timestamp(self):
        rows = [_bar(HOLDOUT_START_MS - TF_MS_5M), _bar(HOLDOUT_START_MS), _bar(HOLDOUT_END_MS)]
        for purpose in ("walkforward", "mint", "densify", "lift", "refill"):
            with self.subTest(purpose=purpose):
                with self.assertRaises(HoldoutLeak):
                    read_bars(rows, purpose=purpose)
                with self.assertRaises(HoldoutLeak):
                    assert_no_holdout(rows, purpose=purpose)
        kept = discovery_rows(rows)
        self.assertEqual([r[0] for r in kept], [HOLDOUT_START_MS - TF_MS_5M])
        self.assertEqual(read_bars(kept, purpose="mint"), kept)

    def test_holdout_read_inside_lift_raises(self):
        rows = [_bar(HOLDOUT_START_MS), _bar(HOLDOUT_START_MS + TF_MS_5M)]
        with scoring_purpose("lift"):
            with self.assertRaises(HoldoutLeak):
                read_bars(rows, purpose="holdout")
        sealed = read_bars(rows, purpose="holdout")
        self.assertEqual(len(sealed), 2)

    def test_forward_tail_is_not_part_of_the_holdout_read(self):
        rows = [_bar(HOLDOUT_END_MS), _bar(HOLDOUT_START_MS)]
        sealed = read_bars(rows, purpose="holdout")
        self.assertEqual([r[0] for r in sealed], [HOLDOUT_START_MS])


class TiledSealTests(unittest.TestCase):
    def _tape(self, start: int, end_inclusive: int) -> dict:
        lead = (2 + 2) * TF_MS_5M
        rows = [_bar(start - lead), _bar(start - TF_MS_5M), _bar(start)]
        ts = start + TF_MS_5M
        while ts <= end_inclusive:
            rows.append(_bar(ts))
            ts += TF_MS_5M
        return {"BTC/USDT": rows}

    def test_discovery_segment_that_crosses_the_holdout_raises(self):
        from scripts.tournament_engine import _tiled_slices

        start = HOLDOUT_START_MS - 5 * TF_MS_5M
        data = self._tape(start, HOLDOUT_START_MS + TF_MS_5M)
        with self.assertRaises(HoldoutLeak):
            _tiled_slices(
                data,
                1,
                oos_start_ms=start,
                segment_ms=10 * TF_MS_5M,
                train_bars=2,
                warmup_bars=2,
                close_last=True,
                seal_holdout=True,
            )

    def test_segment_that_ends_at_the_holdout_is_scored(self):
        from scripts.tournament_engine import _tiled_slices

        segment = 4 * TF_MS_5M
        start = HOLDOUT_START_MS - segment
        data = self._tape(start, HOLDOUT_START_MS)
        slices = _tiled_slices(
            data,
            1,
            oos_start_ms=start,
            segment_ms=segment,
            train_bars=2,
            warmup_bars=2,
            close_last=True,
            seal_holdout=True,
        )
        self.assertEqual(len(slices), 1)


class MintPathsDoNotReadHoldoutTests(unittest.TestCase):
    def test_seed_lift_and_refill_do_not_call_read_bars(self):
        from hedge_fund.trading.atom_lift import estimate_lifts
        from hedge_fund.trading.densify import order_seeds
        from hedge_fund.trading.refill import next_refill_batch

        def boom(*_a, **_k):
            raise AssertionError("read_bars")

        with patch("hedge_fund.trading.holdout.read_bars", side_effect=boom):
            self.assertEqual(order_seeds({"sma_abv_20": True}, None, None), ["sma_abv_20"])
            self.assertEqual(len(next_refill_batch(taken_names=set(), n=1)), 1)
            estimate_lifts([{"strategy": "sma_abv_20", "sharpe": 0.1, "trades": 4}])

    def test_seed_and_lift_do_not_read_holdout_keys(self):
        from hedge_fund.trading.atom_lift import estimate_lifts
        from hedge_fund.trading.densify import order_seeds

        class SealDict(dict):
            def get(self, key, default=None):
                if str(key).startswith("holdout"):
                    raise AssertionError(key)
                return super().get(key, default)

        row = SealDict(
            strategy="sma_abv_20",
            sharpe=0.4,
            trades=40,
            test_pnl=10.0,
            bh_oos_pnl=1.0,
            regimes_tested=DISCOVERY_N_WINDOWS,
        )
        order_seeds({"sma_abv_20": False}, None, {"sma_abv_20": row})
        estimate_lifts([row])

    def test_twenty_three_window_row_is_not_a_seed(self):
        from hedge_fund.trading.densify import order_seeds

        name18 = "sma_abv_20"
        name23 = "sma_abv_30"
        results = {
            name18: {
                "sharpe": 0.4,
                "trades": 40,
                "test_pnl": 20.0,
                "bh_oos_pnl": 1.0,
                "regimes_tested": DISCOVERY_N_WINDOWS,
            },
            name23: {
                "sharpe": 3.0,
                "trades": 80,
                "test_pnl": 500.0,
                "bh_oos_pnl": 1.0,
                "regimes_tested": 23,
            },
        }
        seeds = order_seeds({name18: False, name23: True}, None, results)
        self.assertEqual(seeds, [name18])

    def test_loader_inside_lift_raises_on_a_sealed_bar(self):
        from hedge_fund.trading.live_tape import TAPE_DTYPE
        from hedge_fund.trading.price_history import load_canonical_history

        arr = np.array(
            [(HOLDOUT_START_MS, 1.0, 1.1, 0.9, 1.0, 1.0)],
            dtype=TAPE_DTYPE,
        )
        with patch(
            "hedge_fund.trading.price_history.canonical_arrays",
            return_value=({"BTC/USDT": arr}, "live_tape", HOLDOUT_START_MS),
        ):
            with scoring_purpose("lift"):
                with self.assertRaises(HoldoutLeak):
                    load_canonical_history()
            loaded = load_canonical_history()
        self.assertEqual(loaded["BTC/USDT"][0][0], HOLDOUT_START_MS)


if __name__ == "__main__":
    unittest.main()
