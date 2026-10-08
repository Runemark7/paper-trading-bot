"""Mint quality: redundant thresholds, empty bands, unreachable mom/dip, claim skip."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.trading.densify import (
    next_densify_batch,
    zero_trade_names_from_rows,
)
from hedge_fund.trading.mint_quality import (
    REASON_CANONICAL,
    REASON_DEAD_DEPTH,
    REASON_DEAD_DIP,
    REASON_DEAD_MOM,
    REASON_EMPTY_BAND,
    REASON_FILLER,
    REASON_REDUNDANT,
    REASON_UNREACHABLE,
    REASON_UNSUPPORTED_BAND,
    AtomParts,
    canonical_name,
    clear_move_cap_cache,
    count_mint_blocks,
    group_bound_reason,
    load_mint_skips,
    max_move_pct,
    mint_block_reason,
    parse_atom,
    redundant_bound_reason,
    unreachable_threshold_reason,
)


PASS = "h1_ema_abv_50&mom_18b_gt2pc"


def _mom(name: str, lookback: int, pct: int) -> str:
    return name.replace("mom_18b_gt2pc", f"mom_{lookback}b_gt{pct}pc")


class ParseAtomTests(unittest.TestCase):
    def test_parse_shapes(self):
        self.assertEqual(parse_atom("rsi_14_>50"), ("rsi", (14,), "gt", 50.0))
        self.assertEqual(parse_atom("rsi_14_>45"), ("rsi", (14,), "gt", 45.0))
        self.assertEqual(parse_atom("rsi_14_<60"), ("rsi", (14,), "lt", 60.0))
        self.assertEqual(parse_atom("rsi_14_>45_<60"), ("rsi", (14,), "band", (45.0, 60.0)))
        self.assertEqual(parse_atom("mom_18b_gt2pc"), ("mom", (18,), "gt", 2.0))
        self.assertEqual(parse_atom("mom_18b_gt4pc"), ("mom", (18,), "gt", 4.0))
        self.assertEqual(parse_atom("dip_24b_lt5pc"), ("dip", (24,), "gt", 5.0))
        self.assertEqual(parse_atom("h1_ema_abv_50"), ("h1_ema_abv", (50,), "abv", None))
        self.assertIsNone(parse_atom("not_an_atom"))

    def test_duplicate_threshold_rejected(self):
        stacked = "h1_ema_abv_50&mom_18b_gt2pc&rsi_14_>50&rsi_14_>45"
        self.assertEqual(redundant_bound_reason(stacked), REASON_REDUNDANT)
        # h1 spine plus an RSI-above atom is a measured zero-trade filler,
        # so the mint reason is the dead zone rather than the duplicate bound.
        self.assertEqual(mint_block_reason(stacked), REASON_FILLER)
        self.assertEqual(
            mint_block_reason("mom_18b_gt2pc&mom_18b_gt4pc"),
            REASON_REDUNDANT,
        )
        self.assertEqual(
            redundant_bound_reason("mom_18b_gt2pc&mom_18b_gt4pc"),
            REASON_REDUNDANT,
        )
        self.assertEqual(
            redundant_bound_reason("dip_24b_lt4pc&dip_24b_lt6pc"),
            REASON_REDUNDANT,
        )
        self.assertEqual(
            redundant_bound_reason("h1_ema_abv_50&h1_ema_abv_50"),
            REASON_REDUNDANT,
        )
        # Nested band: the wider atom does not change the AND.
        self.assertEqual(
            redundant_bound_reason("rsi_14_>50_<70&rsi_14_>40_<90"),
            REASON_REDUNDANT,
        )

    def test_valid_band_kept(self):
        pair = "rsi_14_>45&rsi_14_<60"
        self.assertIsNone(redundant_bound_reason(pair))
        self.assertIsNone(mint_block_reason(pair))
        self.assertIsNone(redundant_bound_reason("rsi_14_>45_<60"))
        # Overlapping bands where each side still tightens the intersection.
        self.assertIsNone(
            redundant_bound_reason("rsi_14_>45_<70&rsi_14_>40_<60")
        )
        # Different params are different indicators.
        self.assertIsNone(redundant_bound_reason("mom_18b_gt2pc&mom_24b_gt2pc"))
        self.assertIsNone(redundant_bound_reason("sma_abv_30&sma_abv_50"))
        self.assertIsNone(redundant_bound_reason("h1_ema_abv_50&h1_ema_abv_40"))
        self.assertIsNone(redundant_bound_reason("rsi_14_>50&rsi_21_>50"))

    def test_empty_band_rejected(self):
        self.assertEqual(
            redundant_bound_reason("rsi_14_>60&rsi_14_<40"),
            REASON_EMPTY_BAND,
        )
        self.assertEqual(redundant_bound_reason("rsi_14_>60_<40"), REASON_EMPTY_BAND)
        self.assertEqual(redundant_bound_reason("rsi_14_>50&rsi_14_<50"), REASON_EMPTY_BAND)

    def test_unsupported_opposite_band_rejected(self):
        low = AtomParts("rsi", (14,), "gt", 45.0, (("gt", 45.0),), False, "rsi_14_>45")
        high = AtomParts("rsi", (14,), "lt", 60.0, (("lt", 60.0),), False, "rsi_14_<60")
        self.assertEqual(group_bound_reason([low, high]), REASON_UNSUPPORTED_BAND)

    def test_parser_evaluates_rsi_upper_bound(self):
        pred = parse_strategy("rsi_14_<60")
        rise = [float(100 + i) for i in range(40)]
        fall = [float(200 - i) for i in range(40)]
        self.assertFalse(pred(rise))
        self.assertTrue(pred(fall))
        band = parse_strategy("rsi_14_>45&rsi_14_<60")
        self.assertFalse(band(rise))
        self.assertFalse(band(fall))


class DeadZoneTests(unittest.TestCase):
    def test_dead_zones_and_filler_are_rejected(self):
        self.assertEqual(mint_block_reason("mom_96b_gt14pc"), REASON_DEAD_MOM)
        self.assertEqual(mint_block_reason("h1_ema_abv_50&mom_18b_gt14pc"), REASON_DEAD_MOM)
        self.assertIsNone(mint_block_reason("mom_18b_gt8pc"))
        self.assertEqual(mint_block_reason("dip_18b_lt5pc"), REASON_DEAD_DIP)
        self.assertEqual(mint_block_reason("dip_24b_lt6pc"), REASON_DEAD_DIP)
        self.assertIsNone(mint_block_reason("dip_24b_lt4pc"))
        self.assertIsNone(mint_block_reason("dip_12b_lt5pc"))
        deep = "h1_ema_abv_20&mom_18b_gt2pc&don_hi_12&near_swing_hi_12&vol_lowsm_20_20"
        self.assertEqual(mint_block_reason(deep), REASON_DEAD_DEPTH)
        filler = "h1_ema_abv_50&mom_18b_gt2pc&sma_abv_30&rsi_14_>45"
        self.assertEqual(mint_block_reason(filler), REASON_FILLER)
        self.assertEqual(mint_block_reason("h4_sma_abv_20&ema_abv_20"), REASON_FILLER)
        self.assertIsNone(mint_block_reason("sma_abv_30&rsi_14_>50"))
        self.assertIsNone(mint_block_reason("h1_ema_abv_50&mom_18b_gt2pc"))
        self.assertIsNone(mint_block_reason("h1_ema_abv_50&rsi_14_<40"))

    def test_canonical_form_collapses_thresholds_and_order(self):
        self.assertEqual(
            canonical_name("mom_18b_gt2pc&h1_ema_abv_50&mom_18b_gt4pc"),
            "h1_ema_abv_50&mom_18b_gt4pc",
        )
        self.assertEqual(
            canonical_name("rsi_14_>50&h1_ema_abv_20"),
            canonical_name("h1_ema_abv_20&rsi_14_>50"),
        )
        loose = "h1_ema_abv_50&mom_18b_gt2pc"
        strict = "mom_18b_gt4pc&h1_ema_abv_50"
        self.assertEqual(
            mint_block_reason(strict, {canonical_name(loose)}),
            None,
        )
        self.assertEqual(
            mint_block_reason("mom_18b_gt2pc&h1_ema_abv_50", {canonical_name(loose)}),
            REASON_CANONICAL,
        )

    def test_canonical_duplicate_is_counted_in_mint_skips(self):
        from hedge_fund.trading.mint_quality import record_untested_mint_skips

        tested = "h1_ema_abv_50&mom_18b_gt2pc"
        perm = "mom_18b_gt2pc&h1_ema_abv_50"
        fresh = "h1_ema_abv_40&mom_18b_gt2pc"
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                counts = record_untested_mint_skips([perm, fresh], [tested])
                skips = load_mint_skips()
        self.assertEqual(counts.get(REASON_CANONICAL), 1)
        self.assertEqual(skips.get(perm), REASON_CANONICAL)
        self.assertNotIn(fresh, skips)
        self.assertEqual(
            count_mint_blocks([perm, fresh], tested=[tested]),
            {REASON_CANONICAL: 1},
        )


class ReachabilityTests(unittest.TestCase):
    def tearDown(self):
        clear_move_cap_cache()

    def test_static_cap_allows_recipe_and_cuts_extremes(self):
        self.assertIsNone(unreachable_threshold_reason("mom_18b_gt8pc"))
        self.assertIsNone(unreachable_threshold_reason("mom_12b_gt6pc"))
        self.assertIsNone(unreachable_threshold_reason("mom_36b_gt8pc"))
        self.assertIsNone(unreachable_threshold_reason("mom_48b_gt8pc"))
        self.assertIsNone(unreachable_threshold_reason("dip_24b_lt6pc"))
        self.assertEqual(
            unreachable_threshold_reason("mom_18b_gt16pc"),
            REASON_UNREACHABLE,
        )
        self.assertEqual(
            unreachable_threshold_reason("h1_ema_abv_50&mom_18b_gt16pc"),
            REASON_UNREACHABLE,
        )
        self.assertLessEqual(8, max_move_pct(18))
        self.assertGreater(16, max_move_pct(18))

    def test_history_99p_overrides_static_table(self):
        # ~0.05% per bar → an 18-bar move is well under 2%. 99th percentile
        # of that tape is the cap, not the static 8%.
        closes = [100.0]
        for _ in range(800):
            closes.append(closes[-1] * 1.002)
        rows = [[i * 300_000, c, c, c, c] for i, c in enumerate(closes)]
        payload = {"BTC/USDT": rows, "ETH/USDT": rows}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "crypto_history_5m.json"
            path.write_text(json.dumps(payload))
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                clear_move_cap_cache()
                cap = max_move_pct(18)
                # ~3.7% over 18 bars, under the static ceiling of 8.
                self.assertGreater(cap, 2.0)
                self.assertLess(cap, 8.0)
                self.assertIsNone(unreachable_threshold_reason("mom_18b_gt2pc"))
                self.assertEqual(
                    unreachable_threshold_reason("mom_18b_gt8pc"),
                    REASON_UNREACHABLE,
                )
        clear_move_cap_cache()
        self.assertGreaterEqual(max_move_pct(18), 8)


class DensifyMintTests(unittest.TestCase):
    def test_densify_skips_dupes_and_unreachable_thresholds(self):
        index = {PASS: True, "h1_sma_abv_30&mom_18b_gt2pc&ema_abv_20": True}
        names, _exhausted = next_densify_batch(
            taken_names=set(index),
            n=80,
            index=index,
            zero_trade_names=[],
        )
        self.assertGreaterEqual(len(names), 20)
        for name in names:
            self.assertIsNone(mint_block_reason(name), name)
            atoms = name.split("&")
            self.assertFalse(
                "rsi_14_>50" in atoms and "rsi_14_>45" in atoms,
                name,
            )
            self.assertNotIn("mom_18b_gt16pc", name)
            self.assertNotIn("mom_18b_gt10pc", name)
            for atom in atoms:
                if atom.startswith("mom_") or atom.startswith("dip_"):
                    self.assertIsNone(unreachable_threshold_reason(atom), atom)

    def test_zero_trade_neighbor_stops_that_direction(self):
        dead = _mom(PASS, 18, 4)
        index = {PASS: True, dead: False}
        names, _ = next_densify_batch(
            taken_names=set(index),
            n=60,
            index=index,
            zero_trade_names=[dead],
        )
        self.assertIn(_mom(PASS, 12, 2), names)
        self.assertNotIn(dead, names)
        self.assertNotIn(_mom(PASS, 18, 6), names)
        self.assertNotIn(_mom(PASS, 18, 8), names)
        self.assertTrue(all(mint_block_reason(name) is None for name in names))

    def test_ops_park_is_not_a_dead_end(self):
        dead = _mom(PASS, 18, 4)
        rows = [{
            "strategy": dead,
            "trades": 0,
            "qualified": False,
            "ops_park": True,
            "fail_reasons": ["eval_timeout after 600s"],
        }]
        self.assertEqual(zero_trade_names_from_rows(rows), set())
        real = [{"strategy": dead, "trades": 0, "qualified": False, "fail_reasons": []}]
        self.assertEqual(zero_trade_names_from_rows(real), {dead})
        self.assertEqual(
            zero_trade_names_from_rows([{"strategy": dead, "qualified": False}]),
            set(),
        )

    def test_log_trades_zero_blocks_further_steps(self):
        dead = _mom(PASS, 18, 4)
        with tempfile.TemporaryDirectory() as tmp:
            log = [{
                "strategy": dead,
                "tested_at": "2026-10-08T00:00:00+00:00",
                "qualified": False,
                "trades": 0,
                "fail_reasons": ["oos_trades 0 < 30"],
            }]
            (Path(tmp) / "discovery_log.json").write_text(json.dumps(log))
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                names, _ = next_densify_batch(
                    taken_names={PASS, dead},
                    n=40,
                    index={PASS: True, dead: False},
                )
        self.assertNotIn(_mom(PASS, 18, 6), names)
        self.assertIn(_mom(PASS, 12, 2), names)


class ClaimSkipTests(unittest.TestCase):
    def test_claim_never_leases_blocked_names_and_skips_untested_only(self):
        from hedge_fund.trading.discovery import (
            append_discovery_evaluation,
            load_discovery_log,
        )
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.trading.tested_index import load_tested_index

        tested_bad = "mom_18b_gt2pc&mom_18b_gt4pc"
        tested_stack = "h1_ema_abv_40&mom_18b_gt2pc"
        queued_dup = "mom_18b_gt4pc&mom_18b_gt2pc"
        queued_perm = "mom_18b_gt2pc&h1_ema_abv_40"
        queued_far = "mom_6b_gt8pc"
        queued_empty = "rsi_14_>60&rsi_14_<40"
        band = "h1_ema_abv_50&rsi_14_>45&rsi_14_<60"
        good = "don_hi_12"
        also = "h1_ema_abv_50&mom_18b_gt2pc"
        universe = [
            tested_bad,
            tested_stack,
            queued_dup,
            queued_perm,
            queued_far,
            queued_empty,
            band,
            good,
            also,
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {
                "PAPER_STATE": tmp,
                "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
            }):
                for name in (tested_bad, tested_stack):
                    append_discovery_evaluation({
                        "strategy": name,
                        "tested_at": "2026-10-08T00:00:00+00:00",
                        "qualified": False,
                        "trades": 0,
                        "sharpe": 0.0,
                        "test_pnl": 0.0,
                        "train_pnl": 0.0,
                        "fail_reasons": ["fixture"],
                        "timeframe": "5m",
                        "risk_policy": "rm_v1",
                    })
                before = load_discovery_log()
                with patch(
                    "hedge_fund.trading.leases.discovery_universe",
                    return_value=universe,
                ):
                    body = claim_discovery_batch("linux-1", 2, parallel=1)
                handed = list(body["names"]) + list(body["refilled"])
                for blocked in (
                    tested_bad,
                    tested_stack,
                    queued_dup,
                    queued_perm,
                    queued_far,
                    queued_empty,
                    band,
                ):
                    self.assertNotIn(blocked, handed)
                for name in handed:
                    self.assertIsNone(mint_block_reason(name), name)
                self.assertEqual(body["names"], [good, also])
                skips = load_mint_skips()
                self.assertEqual(skips.get(queued_dup), REASON_REDUNDANT)
                self.assertEqual(skips.get(queued_perm), REASON_CANONICAL)
                self.assertEqual(skips.get(queued_far), REASON_UNREACHABLE)
                self.assertEqual(skips.get(queued_empty), REASON_EMPTY_BAND)
                self.assertEqual(skips.get(band), REASON_FILLER)
                self.assertNotIn(tested_bad, skips)
                self.assertNotIn(tested_stack, skips)
                self.assertNotIn(good, skips)
                self.assertNotIn(also, skips)
                self.assertIn(tested_bad, load_tested_index())
                self.assertFalse(load_tested_index()[tested_bad])
                self.assertEqual(load_discovery_log(), before)
                self.assertEqual(
                    count_mint_blocks(
                        universe,
                        tested={tested_bad, tested_stack},
                    ),
                    {
                        REASON_REDUNDANT: 1,
                        REASON_CANONICAL: 1,
                        REASON_UNREACHABLE: 1,
                        REASON_EMPTY_BAND: 1,
                        REASON_FILLER: 1,
                    },
                )


if __name__ == "__main__":
    unittest.main()
