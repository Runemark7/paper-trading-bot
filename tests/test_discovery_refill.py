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
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    QUAL_N_WINDOWS,
)
from hedge_fund.trading.discovery import prioritize_leftovers
from hedge_fund.trading.refill import (
    CONTINUATION_TRENDS,
    DIP_FILTERS,
    DIP_FILTERS_GRIND,
    DIP_FILTERS_LEGACY,
    DIP_FILTERS_WIDE,
    GRIND_FILTERS,
    LEVEL_TRENDS,
    MOM_FILTERS,
    MOM_FILTERS_GRIND,
    MOM_FILTERS_HTF_DENSE,
    MOM_FILTERS_LEGACY,
    MOM_FILTERS_WIDE,
    REGIME_ATOMS,
    REGIME_DIP_BASES,
    REGIME_ENTRY_BASES,
    REGIME_MOM_BASES,
    REGIME_MOM_PRIORITY,
    REGIME_STRUCTURE_NS,
    REGIME_STRUCTURE_TAGS,
    STACK_TREND,
    STRUCTURE_NS,
    STRUCTURE_NS_THROUGH_96,
    SUPPORT_EXTRA_TRENDS,
    THREE_ATOM_EXTRA_TRENDS,
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

_REGIME_MARKERS = (
    "h4_ema_abv_",
    "h4_sma_abv_",
    "h1_ema_abv_",
)


def _has_mint_tag(name: str) -> bool:
    toks = name.split("&")
    return any(
        any(tok.startswith(m) for m in _STRUCTURE_MARKERS + _REGIME_MARKERS)
        for tok in toks
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
        for dip in DIP_FILTERS_LEGACY:
            out.append(f"{dip}&don_lo_{n}")
            out.append(f"{dip}&near_swing_lo_{n}")
        for mom in MOM_FILTERS_LEGACY:
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
        for dip in DIP_FILTERS_LEGACY:
            out.append(f"{dip}&don_lo_{n}&sma_abv_50")
            out.append(f"{dip}&near_swing_lo_{n}&sma_abv_50")
        for mom in MOM_FILTERS_LEGACY:
            out.append(f"{mom}&don_hi_{n}&sma_abv_50")
    return out


def _snapshot_2026_09_12_leftover(ns: tuple[int, ...] | None = None) -> list[str]:
    """Frozen 2026-09-12 leftover stream (legacy + near-level + leftover TREND)."""
    from hedge_fund.trading.refill import (
        _leftover_trend_ands,
        _legacy_structure_ands,
        _near_level_ands,
    )

    out: list[str] = []
    use = ns or STRUCTURE_NS
    for n in use:
        out.extend(_legacy_structure_ands(n))
    for n in use:
        out.extend(_near_level_ands(n))
    for n in use:
        out.extend(_leftover_trend_ands(n))
    return out


def _snapshot_trend_participation(ns: tuple[int, ...] | None = None) -> list[str]:
    from hedge_fund.trading.refill import (
        _grind_participation_ands,
        _trend_participation_ands,
    )

    out: list[str] = []
    use = ns or STRUCTURE_NS
    for n in use:
        out.extend(_grind_participation_ands(n))
    for n in use:
        out.extend(_trend_participation_ands(n))
    return out


def _snapshot_2026_09_12_wide_two_atoms(ns: tuple[int, ...] | None = None) -> list[str]:
    """Frozen #42 WIDE 2-atoms + gt2pc/lt2pc × don_hi × sma_abv_20 only."""
    out: list[str] = []
    for n in ns or STRUCTURE_NS:
        for mom in MOM_FILTERS_WIDE:
            out.append(f"{mom}&don_hi_{n}")
            out.append(f"{mom}&near_swing_hi_{n}")
        for dip in DIP_FILTERS_WIDE:
            out.append(f"{dip}&don_hi_{n}")
            out.append(f"{dip}&near_swing_hi_{n}")
        for trend in CONTINUATION_TRENDS:
            out.append(f"{trend}&don_hi_{n}")
            out.append(f"{trend}&near_swing_hi_{n}")
        for mom in MOM_FILTERS_WIDE:
            if mom.endswith("_gt2pc"):
                out.append(f"{mom}&don_hi_{n}&sma_abv_20")
        for dip in DIP_FILTERS_WIDE:
            if dip.endswith("_lt2pc"):
                out.append(f"{dip}&don_hi_{n}&sma_abv_20")
    return out


def _snapshot_htf_regime() -> list[str]:
    from hedge_fund.trading.refill import _regime_ands

    return list(_regime_ands())


def _snapshot_2026_09_12_morning(ns: tuple[int, ...] | None = None) -> list[str]:
    """Frozen 2026-09-12 morning stream (legacy + near-level, lookbacks through 96)."""
    from hedge_fund.trading.refill import _legacy_structure_ands, _near_level_ands

    out: list[str] = []
    use = ns or STRUCTURE_NS_THROUGH_96
    for n in use:
        out.extend(_legacy_structure_ands(n))
    for n in use:
        out.extend(_near_level_ands(n))
    return out


class RecipeBoundsTests(unittest.TestCase):
    def test_recipe_is_finite_and_not_tens_of_thousands(self):
        names = list(iter_recipe_names())
        self.assertGreater(len(names), 1500)
        self.assertLessEqual(len(names), 8000)
        self.assertEqual(len(STRUCTURE_NS), 22)
        self.assertEqual(STRUCTURE_NS_THROUGH_96[-2:], (84, 96))
        self.assertIn(84, STRUCTURE_NS)
        self.assertIn(96, STRUCTURE_NS)
        self.assertIn(108, STRUCTURE_NS)
        self.assertIn(192, STRUCTURE_NS)
        self.assertEqual(STRUCTURE_NS[-2:], (180, 192))
        self.assertEqual(STRUCTURE_NS[: len(STRUCTURE_NS_THROUGH_96)], STRUCTURE_NS_THROUGH_96)
        self.assertTrue(all(n % 6 == 0 for n in STRUCTURE_NS))
        self.assertEqual(LEVEL_TRENDS, ("sma_abv_50", "ema_abv_50", "sma_stack_20_50_100"))
        self.assertEqual(
            SUPPORT_EXTRA_TRENDS,
            ("sma_abv_100", "sma_abv_200", "rsi_14_>50", "ema_abv_100"),
        )
        self.assertEqual(STACK_TREND, "ema_stack_20_50_100")
        self.assertEqual(THREE_ATOM_EXTRA_TRENDS, ("sma_abv_100", "sma_stack_20_50_100"))
        self.assertEqual(DIP_FILTERS_LEGACY, ("dip_6b_lt2pc", "dip_12b_lt3pc", "dip_24b_lt5pc"))
        self.assertEqual(MOM_FILTERS_LEGACY, ("mom_6b_gt2pc", "mom_12b_gt3pc", "mom_24b_gt5pc"))
        self.assertEqual(
            DIP_FILTERS_WIDE,
            (
                "dip_12b_lt2pc",
                "dip_18b_lt2pc",
                "dip_24b_lt2pc",
                "dip_36b_lt2pc",
                "dip_48b_lt2pc",
                "dip_36b_lt4pc",
            ),
        )
        self.assertEqual(
            MOM_FILTERS_WIDE,
            (
                "mom_12b_gt2pc",
                "mom_18b_gt2pc",
                "mom_24b_gt2pc",
                "mom_36b_gt2pc",
                "mom_48b_gt2pc",
                "mom_72b_gt2pc",
                "mom_36b_gt4pc",
                "mom_48b_gt6pc",
            ),
        )
        self.assertEqual(
            DIP_FILTERS_GRIND,
            ("dip_30b_lt1pc", "dip_42b_lt1pc", "dip_60b_lt1pc"),
        )
        self.assertEqual(
            MOM_FILTERS_GRIND,
            ("mom_30b_gt1pc", "mom_42b_gt1pc", "mom_60b_gt1pc", "mom_84b_gt1pc"),
        )
        self.assertEqual(
            DIP_FILTERS,
            DIP_FILTERS_LEGACY + DIP_FILTERS_WIDE + DIP_FILTERS_GRIND,
        )
        self.assertEqual(
            MOM_FILTERS,
            MOM_FILTERS_LEGACY + MOM_FILTERS_WIDE + MOM_FILTERS_GRIND,
        )
        self.assertEqual(CONTINUATION_TRENDS, ("sma_abv_20", "ema_abv_20"))
        self.assertEqual(GRIND_FILTERS, CONTINUATION_TRENDS)
        self.assertEqual(
            REGIME_ATOMS,
            (
                "h4_ema_abv_24",
                "h4_sma_abv_50",
                "h1_ema_abv_24",
                "h1_ema_abv_15",
                "h1_ema_abv_18",
                "h1_ema_abv_20",
                "h1_ema_abv_30",
                "h1_ema_abv_36",
                "h4_ema_abv_12",
                "h4_ema_abv_48",
                "h4_sma_abv_24",
            ),
        )
        self.assertEqual(
            MOM_FILTERS_HTF_DENSE,
            ("mom_18b_gt4pc", "mom_18b_gt6pc", "mom_12b_gt6pc"),
        )
        self.assertEqual(REGIME_STRUCTURE_NS, (12, 24, 48))
        self.assertEqual(REGIME_STRUCTURE_TAGS, ("don_hi", "near_swing_lo"))
        self.assertEqual(
            REGIME_MOM_PRIORITY,
            ("mom_18b_gt2pc", "mom_12b_gt2pc", "mom_24b_gt2pc"),
        )
        self.assertEqual(
            REGIME_MOM_BASES[:3],
            REGIME_MOM_PRIORITY,
        )
        self.assertEqual(
            set(REGIME_MOM_BASES),
            set(MOM_FILTERS + MOM_FILTERS_HTF_DENSE + GRIND_FILTERS),
        )
        self.assertEqual(REGIME_DIP_BASES, DIP_FILTERS)
        self.assertEqual(REGIME_ENTRY_BASES, REGIME_MOM_BASES + REGIME_DIP_BASES)
        self.assertEqual(DISCOVERY_REFILL_BATCH_SIZE, 16)
        self.assertLessEqual(DISCOVERY_REFILL_BATCH_SIZE, 24)
        self.assertGreaterEqual(DISCOVERY_REFILL_BATCH_SIZE, 8)
        # Frozen OOS gate — recipe expansion must not touch these.
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 8)
        # Later passes sit on top of 09-11 / morning near-level, not a rewrite.
        self.assertGreater(len(names), len(_snapshot_2026_09_11_recipe(STRUCTURE_NS)))
        self.assertGreater(len(names), len(_snapshot_2026_09_12_morning()))
        self.assertGreater(len(names), len(_snapshot_2026_09_12_leftover()))
        self.assertGreater(len(names), len(_snapshot_htf_regime()))

    def test_every_recipe_name_parses_and_has_structure(self):
        # Tape longer than STRUCTURE_NS max (192) so long lookbacks can evaluate.
        n_bars = 220
        closes = [100.0] * n_bars
        highs = [101.0] * n_bars
        lows = [99.0] * n_bars
        seen = set()
        for name in iter_recipe_names():
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertFalse(name_is_refused(name), msg=name)
            self.assertTrue(_has_mint_tag(name), msg=name)
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

    def test_recipe_includes_longer_lookbacks_and_leftover_ands(self):
        names = list(iter_recipe_names())
        # 9h / 16h on 5m (multiples of 6; step of 12 after 72).
        self.assertIn("dip_6b_lt2pc&don_lo_108", names)
        self.assertIn("mom_12b_gt3pc&don_hi_192", names)
        self.assertIn("don_hi_144", names)
        self.assertIn("near_swing_lo_168", names)
        # Parser already allowed ema_stack / ema_abv_100; morning recipe skipped them.
        self.assertIn(f"{STACK_TREND}&don_hi_12", names)
        self.assertIn(f"{STACK_TREND}&don_lo_24", names)
        self.assertIn(f"{STACK_TREND}&near_swing_lo_18", names)
        self.assertIn("ema_abv_100&don_hi_12", names)
        self.assertIn("ema_abv_100&don_lo_12", names)
        # Leftover TREND_FILTERS on support / near-swing (were don_hi-only).
        self.assertIn("sma_abv_100&don_lo_12", names)
        self.assertIn("sma_abv_200&near_swing_lo_18", names)
        self.assertIn("rsi_14_>50&near_swing_hi_24", names)
        # dbl_bot × same-N swing support + leftover trends.
        self.assertIn("dbl_bot_18&near_swing_lo_18", names)
        self.assertIn("dbl_bot_24&sma_abv_200", names)
        self.assertIn(f"dbl_bot_12&{STACK_TREND}", names)
        # 3-atom extras beyond sma_abv_50 / ema_abv_50.
        self.assertIn("dip_6b_lt2pc&don_lo_12&sma_abv_100", names)
        self.assertIn("dip_12b_lt3pc&near_swing_lo_18&sma_stack_20_50_100", names)
        self.assertIn("mom_12b_gt3pc&don_hi_12&sma_abv_100", names)
        self.assertIn("mom_6b_gt2pc&near_swing_hi_24&sma_stack_20_50_100", names)
        blob = " ".join(names)
        for needle in ("engulfing", "hammer", "doji", "wt_cross", "mfi_", "dbl_top_"):
            self.assertNotIn(needle, blob)

    def test_recipe_includes_wide_dip_mom_and_continuation_ands(self):
        names = list(iter_recipe_names())
        # Wider parser-allowed bases (not the frozen 3×3).
        self.assertIn("mom_12b_gt2pc&don_hi_12", names)
        self.assertIn("mom_24b_gt2pc&don_hi_36", names)
        self.assertIn("mom_36b_gt2pc&near_swing_hi_24", names)
        self.assertIn("mom_48b_gt2pc&don_hi_48", names)
        self.assertIn("mom_72b_gt2pc&don_hi_72", names)
        self.assertIn("mom_36b_gt4pc&near_swing_hi_18", names)
        self.assertIn("mom_48b_gt6pc&don_hi_24", names)
        self.assertIn("dip_12b_lt2pc&don_hi_12", names)
        self.assertIn("dip_24b_lt2pc&near_swing_hi_24", names)
        self.assertIn("dip_36b_lt2pc&don_hi_36", names)
        self.assertIn("dip_48b_lt2pc&near_swing_hi_18", names)
        self.assertIn("dip_36b_lt4pc&don_hi_24", names)
        # Short MA × breakout (more time in a grind than sma_abv_200).
        self.assertIn("sma_abv_20&don_hi_12", names)
        self.assertIn("ema_abv_20&near_swing_hi_24", names)
        # Loose 3-atom continuation, not dip×support.
        self.assertIn("mom_36b_gt2pc&don_hi_12&sma_abv_20", names)
        self.assertIn("dip_18b_lt2pc&don_hi_18&sma_abv_20", names)
        # Wide bases are not re-multiplied onto leftover dip×support.
        self.assertNotIn("dip_12b_lt2pc&don_lo_12", names)
        self.assertNotIn("mom_12b_gt2pc&don_lo_12", names)
        blob = " ".join(names)
        for needle in ("engulfing", "hammer", "doji", "wt_cross", "mfi_", "dbl_top_"):
            self.assertNotIn(needle, blob)

    def test_recipe_includes_grind_one_pct_and_full_wide_3atoms(self):
        names = list(iter_recipe_names())
        # 1% grind at unused lookbacks (canon 1%→2% would collide on 12/18/24/36…).
        self.assertIn("mom_30b_gt1pc&don_hi_12", names)
        self.assertIn("mom_42b_gt1pc&near_swing_hi_24", names)
        self.assertIn("mom_60b_gt1pc&don_hi_36", names)
        self.assertIn("mom_84b_gt1pc&near_swing_hi_18", names)
        self.assertIn("dip_30b_lt1pc&don_hi_12", names)
        self.assertIn("dip_42b_lt1pc&near_swing_hi_24", names)
        self.assertIn("dip_60b_lt1pc&don_hi_36", names)
        # Every WIDE mom/dip × both structures × both short MAs.
        self.assertIn("mom_36b_gt4pc&don_hi_12&sma_abv_20", names)
        self.assertIn("mom_36b_gt4pc&don_hi_12&ema_abv_20", names)
        self.assertIn("mom_12b_gt2pc&near_swing_hi_12&sma_abv_20", names)
        self.assertIn("mom_12b_gt2pc&near_swing_hi_12&ema_abv_20", names)
        self.assertIn("dip_36b_lt4pc&don_hi_18&ema_abv_20", names)
        self.assertIn("dip_24b_lt2pc&near_swing_hi_24&sma_abv_20", names)
        # Grind stays on breakout / near-high, not leftover dip×support.
        self.assertNotIn("dip_30b_lt1pc&don_lo_12", names)
        self.assertNotIn("mom_30b_gt1pc&don_lo_12", names)
        # Higher-participation families stay ahead of legacy mean-reversion.
        self.assertLess(
            names.index("mom_30b_gt1pc&don_hi_6"),
            names.index("dip_6b_lt2pc&don_lo_6"),
        )
        blob = " ".join(names)
        for needle in ("engulfing", "hammer", "doji", "wt_cross", "mfi_", "dbl_top_"):
            self.assertNotIn(needle, blob)

    def test_recipe_includes_htf_regime_ands(self):
        names = list(iter_recipe_names())
        # 2-atom regime × grind / wide (dry refill picks these first).
        self.assertIn("h4_ema_abv_24&sma_abv_20", names)
        self.assertIn("h4_ema_abv_24&mom_12b_gt2pc", names)
        self.assertIn("h4_sma_abv_50&dip_12b_lt2pc", names)
        self.assertIn("h1_ema_abv_24&ema_abv_20", names)
        # Densified HTF × winning-neighborhood mom (parser-allowed, distinct).
        self.assertIn("h1_ema_abv_15&mom_18b_gt2pc", names)
        self.assertIn("h1_ema_abv_18&mom_18b_gt2pc", names)
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc", names)
        self.assertIn("h1_ema_abv_30&mom_18b_gt2pc", names)
        self.assertIn("h1_ema_abv_36&mom_18b_gt2pc", names)
        self.assertIn("h1_ema_abv_24&mom_18b_gt4pc", names)
        self.assertIn("h4_ema_abv_12&mom_18b_gt2pc", names)
        self.assertIn("h4_ema_abv_48&mom_18b_gt4pc", names)
        self.assertIn("h4_sma_abv_24&mom_12b_gt6pc", names)
        # HTF×mom (and HTF×dip) are 2-atom only — no structure AND.
        self.assertNotIn("h4_ema_abv_24&dip_12b_lt2pc&near_swing_lo_12", names)
        self.assertNotIn("h4_sma_abv_50&mom_36b_gt2pc&don_hi_24", names)
        self.assertNotIn("h1_ema_abv_24&mom_18b_gt2pc&don_hi_12", names)
        self.assertNotIn("h1_ema_abv_15&mom_18b_gt4pc&near_swing_lo_24", names)
        self.assertNotIn("h1_ema_abv_18&mom_18b_gt2pc&don_hi_12", names)
        self.assertNotIn("h1_ema_abv_36&mom_18b_gt2pc&near_swing_lo_24", names)
        self.assertEqual("h1_ema_abv_18&mom_18b_gt2pc".count("&"), 1)
        self.assertEqual("h1_ema_abv_36&mom_18b_gt2pc".count("&"), 1)
        self.assertTrue(
            any("h4_ema_abv_24" in n and ("mom_12b_gt2pc" in n or "sma_abv_20" in n) for n in names)
        )
        htf = _snapshot_htf_regime()
        self.assertEqual(names[: len(htf)], htf)
        self.assertTrue(all(n.count("&") == 1 for n in htf), msg="HTF regime path must stay 2-atom")
        self.assertFalse(
            any(
                any(tok.startswith(("don_hi_", "near_swing_lo_")) for tok in n.split("&"))
                for n in htf
            )
        )
        self.assertLess(names.index("h4_ema_abv_24&sma_abv_20"), names.index("dip_6b_lt2pc&don_lo_6"))
        # Per regime: HTF×mom 2-atom before HTF×dip; no HTF×mom×structure.
        mom_bases = MOM_FILTERS + MOM_FILTERS_HTF_DENSE + GRIND_FILTERS
        for regime in REGIME_ATOMS:
            mom2 = next(
                n for n in names
                if n.startswith(f"{regime}&") and n.count("&") == 1
                and any(m in n.split("&") for m in mom_bases)
            )
            dip2 = next(
                n for n in names
                if n.startswith(f"{regime}&") and n.count("&") == 1
                and any(d in n.split("&") for d in DIP_FILTERS)
            )
            self.assertLess(names.index(mom2), names.index(dip2), msg=regime)
            self.assertFalse(
                any(
                    n.startswith(f"{regime}&")
                    and "&mom_" in n
                    and any(tok.startswith(("don_hi_", "near_swing_lo_")) for tok in n.split("&"))
                    for n in names
                ),
                msg=regime,
            )
        blob = " ".join(names)
        for needle in ("engulfing", "hammer", "doji", "wt_cross", "mfi_", "dbl_top_", "daily(", "h1("):
            self.assertNotIn(needle, blob)
        # Wrappers stay refused; token atoms are not wrappers.
        self.assertFalse(name_is_refused("h4_ema_abv_24&dip_12b_lt2pc"))
        self.assertTrue(name_is_refused("h1(sma_abv_50)"))
        self.assertTrue(name_is_parseable("h1_ema_abv_15&mom_18b_gt2pc"))
        self.assertTrue(name_is_parseable("h1_ema_abv_18&mom_18b_gt2pc"))
        self.assertTrue(name_is_parseable("h1_ema_abv_36&mom_18b_gt2pc"))
        self.assertTrue(name_is_parseable("h1_ema_abv_24&mom_18b_gt4pc"))
        self.assertFalse(name_is_parseable("h1_sma_abv_24&mom_18b_gt2pc"))

    def test_htf_dense_atoms_parse_and_stay_distinct(self):
        parked_htf = {
            near_duplicate_key("h4_ema_abv_24"),
            near_duplicate_key("h4_sma_abv_50"),
            near_duplicate_key("h1_ema_abv_24"),
        }
        new_htf = (
            "h1_ema_abv_15",
            "h1_ema_abv_20",
            "h1_ema_abv_30",
            "h1_ema_abv_36",
            "h4_ema_abv_12",
            "h4_ema_abv_48",
            "h4_sma_abv_24",
        )
        seen = set(parked_htf)
        for atom in new_htf:
            self.assertTrue(name_is_parseable(atom), msg=atom)
            parse_strategy(atom)
            key = near_duplicate_key(atom)
            self.assertNotIn(key, seen, msg=atom)
            seen.add(key)
        # 18 is in the recipe (exact period) but shares canon with 20.
        self.assertTrue(name_is_parseable("h1_ema_abv_18"))
        parse_strategy("h1_ema_abv_18")
        self.assertEqual(
            near_duplicate_key("h1_ema_abv_18"),
            near_duplicate_key("h1_ema_abv_20"),
        )
        parked_mom = {near_duplicate_key(n) for n in MOM_FILTERS}
        seen_mom = set(parked_mom)
        for atom in MOM_FILTERS_HTF_DENSE:
            self.assertTrue(name_is_parseable(atom), msg=atom)
            parse_strategy(atom)
            key = near_duplicate_key(atom)
            self.assertNotIn(key, seen_mom, msg=atom)
            seen_mom.add(key)
        # gt3pc is the same canon as the emitted gt4; 16b/20b collapse onto 18b_gt2.
        self.assertEqual(near_duplicate_key("mom_18b_gt3pc"), near_duplicate_key("mom_18b_gt4pc"))
        self.assertEqual(near_duplicate_key("mom_16b_gt2pc"), near_duplicate_key("mom_18b_gt2pc"))
        self.assertEqual(near_duplicate_key("mom_20b_gt2pc"), near_duplicate_key("mom_18b_gt2pc"))
        self.assertEqual(near_duplicate_key("h4_ema_abv_10"), near_duplicate_key("h4_ema_abv_12"))
        self.assertEqual(near_duplicate_key("h4_ema_abv_50"), near_duplicate_key("h4_ema_abv_48"))
        self.assertNotIn("mom_16b_gt2pc", MOM_FILTERS_HTF_DENSE)
        self.assertNotIn("mom_18b_gt3pc", MOM_FILTERS_HTF_DENSE)

    def test_wide_bases_do_not_near_dup_legacy_filters(self):
        legacy_keys = {near_duplicate_key(n) for n in DIP_FILTERS_LEGACY + MOM_FILTERS_LEGACY}
        wide_keys = {near_duplicate_key(n) for n in DIP_FILTERS_WIDE + MOM_FILTERS_WIDE}
        for name in DIP_FILTERS_WIDE + MOM_FILTERS_WIDE:
            key = near_duplicate_key(name)
            self.assertNotIn(key, legacy_keys, msg=f"{name} collapses onto {key}")
        for name in DIP_FILTERS_GRIND + MOM_FILTERS_GRIND:
            key = near_duplicate_key(name)
            self.assertNotIn(key, legacy_keys, msg=f"{name} collapses onto {key}")
            self.assertNotIn(key, wide_keys, msg=f"{name} collapses onto {key}")
        parked_mom = {near_duplicate_key(n) for n in MOM_FILTERS}
        for name in MOM_FILTERS_HTF_DENSE:
            key = near_duplicate_key(name)
            self.assertNotIn(key, parked_mom, msg=f"{name} collapses onto {key}")
        prior = _snapshot_2026_09_12_leftover()
        prior_keys = {near_duplicate_key(n) for n in prior}
        novel = 0
        for name in _snapshot_trend_participation():
            if near_duplicate_key(name) not in prior_keys:
                novel += 1
        self.assertGreater(novel, 200)

    def test_legacy_families_stay_contiguous_after_new_pass(self):
        names = list(iter_recipe_names())
        htf = _snapshot_htf_regime()
        new_pass = _snapshot_trend_participation(STRUCTURE_NS)
        legacy = _snapshot_2026_09_11_recipe(STRUCTURE_NS)
        self.assertEqual(names[: len(htf)], htf)
        self.assertEqual(names[len(htf) : len(htf) + len(new_pass)], new_pass)
        self.assertEqual(
            names[len(htf) + len(new_pass) : len(htf) + len(new_pass) + len(legacy)],
            legacy,
        )

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
    def test_dry_refill_emits_grind_or_wide_times_h4_regime(self):
        uni = generate_universe()
        added = next_refill_batch(taken_names=uni, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        wide_or_grind = set(DIP_FILTERS_WIDE + MOM_FILTERS_WIDE + GRIND_FILTERS)
        hit = [
            n for n in added
            if any(tok.startswith("h4_") for tok in n.split("&"))
            and any(base in n.split("&") for base in wide_or_grind)
        ]
        self.assertTrue(hit, msg=f"expected grind/wide × h4 in {added}")
        for name in added:
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertTrue(_has_mint_tag(name), msg=name)

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
        # New continuation pass is first in the stream — park it too so this
        # still proves the near-level families refill after a legacy drain.
        uni = generate_universe()
        legacy = _snapshot_2026_09_11_recipe(STRUCTURE_NS)
        taken = (
            set(uni)
            | set(legacy)
            | set(_snapshot_trend_participation())
            | set(_snapshot_htf_regime())
        )
        taken_keys = {near_duplicate_key(n) for n in taken}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                added = next_refill_batch(taken_names=taken, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        for name in added:
            self.assertNotIn(name, taken)
            self.assertNotIn(near_duplicate_key(name), taken_keys, msg=name)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertTrue(_has_mint_tag(name), msg=name)

    def test_parking_morning_recipe_still_feeds_longer_lookbacks(self):
        # Farm drained STRUCTURE_NS through 96 (~917 unique). Fail-once stays.
        # Longer lookbacks + leftover ANDs must still refill a full batch.
        uni = generate_universe()
        morning = _snapshot_2026_09_12_morning()
        taken = (
            set(uni)
            | set(morning)
            | set(_snapshot_trend_participation())
            | set(_snapshot_htf_regime())
        )
        taken_keys = {near_duplicate_key(n) for n in taken}
        leftover = [n for n in iter_recipe_names() if n not in taken]
        leftover = [n for n in leftover if near_duplicate_key(n) not in taken_keys]
        self.assertGreater(len(leftover), 400)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                added = next_refill_batch(taken_names=taken, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        for name in added:
            self.assertNotIn(name, taken)
            self.assertNotIn(near_duplicate_key(name), taken_keys, msg=name)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertTrue(_has_mint_tag(name), msg=name)
        # First unused names after the morning drain are the 108 lookbacks.
        self.assertTrue(any("_108" in n for n in added))

    def test_parking_leftover_recipe_still_feeds_trend_participation(self):
        # Farm chewed the 192 / leftover-TREND pass (~1000 unique, 0 pass).
        # Wide dip/mom + continuation ANDs must refill and must not collapse
        # onto those parked fails. Fail-once stays.
        uni = generate_universe()
        prior = _snapshot_2026_09_12_leftover()
        taken = set(uni) | set(prior) | set(_snapshot_htf_regime())
        taken_keys = {near_duplicate_key(n) for n in taken}
        leftover = [n for n in iter_recipe_names() if n not in taken]
        leftover = [n for n in leftover if near_duplicate_key(n) not in taken_keys]
        self.assertGreater(len(leftover), 200)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                added = next_refill_batch(taken_names=taken, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        continuation = set(_snapshot_trend_participation())
        for name in added:
            self.assertNotIn(name, taken)
            self.assertNotIn(near_duplicate_key(name), taken_keys, msg=name)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertIn(name, continuation)
            self.assertTrue(
                any(any(tok.startswith(m) for m in _STRUCTURE_MARKERS) for tok in name.split("&")),
                msg=name,
            )
        # Next dry refill after a leftover drain starts at 1% grind × don_hi.
        self.assertTrue(any("gt1pc" in n or "lt1pc" in n for n in added))

    def test_parking_wide_two_atoms_still_feeds_grind(self):
        # Farm is evaluating #42 WIDE 2-atoms. 1% grind + expanded 3-atoms
        # must still refill and must not collapse onto those parked fails.
        uni = generate_universe()
        prior = _snapshot_2026_09_12_leftover() + _snapshot_2026_09_12_wide_two_atoms()
        taken = set(uni) | set(prior) | set(_snapshot_htf_regime())
        taken_keys = {near_duplicate_key(n) for n in taken}
        leftover = [n for n in iter_recipe_names() if n not in taken]
        leftover = [n for n in leftover if near_duplicate_key(n) not in taken_keys]
        self.assertGreater(len(leftover), 100)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                added = next_refill_batch(taken_names=taken, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        for name in added:
            self.assertNotIn(name, taken)
            self.assertNotIn(near_duplicate_key(name), taken_keys, msg=name)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertTrue(
                any(tok.endswith("gt1pc") or tok.endswith("lt1pc") for tok in name.split("&")),
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
            for _ in range(QUAL_N_WINDOWS)
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
                    patch(
                        "scripts.tournament_engine._window_slices",
                        return_value=[{} for _ in range(QUAL_N_WINDOWS)],
                    ),
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
