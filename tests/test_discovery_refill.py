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
from hedge_fund.trading.discovery_guard import (
    DEFAULT_STRUCTURE_LOOKBACK_MAX,
    structure_lookbacks,
)
from hedge_fund.trading.refill import (
    CONTINUATION_TRENDS,
    DEEP_STACK_MOM,
    DEEP_STACK_REGIME,
    DEEP_STACK_RSI,
    DEEP_STACK_STRUCTURE_NS,
    DEEP_STACK_STRUCTURE_TAGS,
    DEEP_STACK_TREND,
    DEEP_STACK_TREND_PAIRS,
    DIP_FILTERS,
    DIP_FILTERS_GRIND,
    DIP_FILTERS_LEGACY,
    DIP_FILTERS_WIDE,
    FRESH_CONT_ATOMS,
    FRESH_RSI,
    FRESH_STACK_REGIME,
    GRIND_FILTERS,
    LEVEL_TRENDS,
    MOM_FILTERS,
    MOM_FILTERS_GRIND,
    MOM_FILTERS_HTF_DENSE,
    MOM_FILTERS_LEGACY,
    MOM_FILTERS_WIDE,
    MOM_FILTERS_HTF_EXPAND,
    MOM_FILTERS_HTF_FRESH,
    MOM_FILTERS_HTF_INTERMEDIATE,
    RECIPE_MAX_ATOMS,
    REGIME_ADMIT_ATOMS,
    REGIME_ATOMS,
    REGIME_ATOMS_FRESH,
    REGIME_ATOMS_PRIOR,
    REGIME_CONT_ATOMS,
    REGIME_DIP_BASES,
    REGIME_DIP_PRIORITY,
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
    UNDRY_CONT_ATOMS,
    UNDRY_GAP_CONT,
    UNDRY_GAP_REGIME,
    UNDRY_MOM_PRIORITY,
    UNDRY_RSI,
    _REGIME_PREFIXES,
    discovery_universe,
    iter_recipe_names,
    load_extended_names,
    maybe_refill_discovery,
    name_has_mom_gt_and_dip,
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
    "h1_sma_abv_",
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


_PRE_2026_09_14_REGIME_ATOMS: tuple[str, ...] = (
    "h4_ema_abv_24",
    "h4_sma_abv_50",
    "h1_ema_abv_24",
    "h1_ema_abv_15",
    "h1_ema_abv_18",
    "h1_ema_abv_20",
    "h1_ema_abv_30",
    "h1_ema_abv_36",
    "h1_sma_abv_20",
    "h1_sma_abv_24",
    "h1_sma_abv_30",
    "h4_ema_abv_12",
    "h4_ema_abv_48",
    "h4_sma_abv_24",
)
_PRE_2026_09_14_DENSE: tuple[str, ...] = (
    "mom_18b_gt4pc",
    "mom_18b_gt6pc",
    "mom_12b_gt6pc",
)


def _snapshot_pre_2026_09_14_htf() -> list[str]:
    """2-atom HTF stream as of the 2026-09-13 SMA-twins / dip-priority pass."""
    mom = REGIME_MOM_PRIORITY + tuple(
        m
        for m in (MOM_FILTERS + _PRE_2026_09_14_DENSE + GRIND_FILTERS)
        if m not in REGIME_MOM_PRIORITY
    )
    out: list[str] = []
    for regime in _PRE_2026_09_14_REGIME_ATOMS:
        for entry in mom:
            out.append(f"{regime}&{entry}")
    for regime in _PRE_2026_09_14_REGIME_ATOMS:
        for entry in REGIME_DIP_BASES:
            out.append(f"{regime}&{entry}")
    return out


def _snapshot_pre_2026_09_14_recipe() -> list[str]:
    return (
        _snapshot_pre_2026_09_14_htf()
        + _snapshot_trend_participation()
        + _snapshot_2026_09_12_leftover()
    )


def _snapshot_pre_deep_stack_recipe() -> list[str]:
    """Winner 3-atoms + HTF 2-atoms + leftover structure (pre 4–7 stacks).

    Frozen on the 25-atom HTF / pre-fresh mom grid so later unused
    HTF-60 / gt8 moms are not counted as already-drained.
    """
    from hedge_fund.trading.refill import (
        REGIME_ATOMS_PRIOR,
        REGIME_DIP_BASES,
        REGIME_MOM_BASES_PRIOR,
        _regime_pair_ands,
        _regime_winner_3atoms,
    )

    return (
        list(_regime_winner_3atoms())
        + list(_regime_pair_ands(REGIME_MOM_BASES_PRIOR, regimes=REGIME_ATOMS_PRIOR))
        + list(_regime_pair_ands(REGIME_DIP_BASES, regimes=REGIME_ATOMS_PRIOR))
        + _snapshot_trend_participation()
        + _snapshot_2026_09_12_leftover()
    )


def _snapshot_pre_fresh_recipe() -> list[str]:
    """Full recipe as of #60/#61 (4–7 stacks, no unused HTF-60 / sma_abv_30)."""
    from hedge_fund.trading.refill import (
        REGIME_ATOMS_PRIOR,
        REGIME_DIP_BASES,
        REGIME_MOM_BASES_PRIOR,
        _regime_deep_stacks,
        _regime_pair_ands,
        _regime_winner_3atoms,
    )

    return (
        list(_regime_winner_3atoms())
        + list(_regime_deep_stacks())
        + list(_regime_pair_ands(REGIME_MOM_BASES_PRIOR, regimes=REGIME_ATOMS_PRIOR))
        + list(_regime_pair_ands(REGIME_DIP_BASES, regimes=REGIME_ATOMS_PRIOR))
        + _snapshot_trend_participation()
        + _snapshot_2026_09_12_leftover()
    )


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
        self.assertEqual(STRUCTURE_NS, STRUCTURE_NS_THROUGH_96)
        self.assertEqual(len(STRUCTURE_NS), 14)
        self.assertEqual(STRUCTURE_NS_THROUGH_96[-2:], (84, 96))
        self.assertIn(84, STRUCTURE_NS)
        self.assertIn(96, STRUCTURE_NS)
        self.assertNotIn(108, STRUCTURE_NS)
        self.assertNotIn(192, STRUCTURE_NS)
        self.assertEqual(STRUCTURE_NS[-2:], (84, 96))
        self.assertEqual(max(STRUCTURE_NS), DEFAULT_STRUCTURE_LOOKBACK_MAX)
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
            REGIME_ATOMS[:14],
            (
                "h4_ema_abv_24",
                "h4_sma_abv_50",
                "h1_ema_abv_24",
                "h1_ema_abv_15",
                "h1_ema_abv_18",
                "h1_ema_abv_20",
                "h1_ema_abv_30",
                "h1_ema_abv_36",
                "h1_sma_abv_20",
                "h1_sma_abv_24",
                "h1_sma_abv_30",
                "h4_ema_abv_12",
                "h4_ema_abv_48",
                "h4_sma_abv_24",
            ),
        )
        self.assertEqual(
            REGIME_ATOMS[14:25],
            (
                "h1_ema_abv_12",
                "h1_ema_abv_40",
                "h1_ema_abv_50",
                "h1_sma_abv_15",
                "h1_sma_abv_36",
                "h1_sma_abv_40",
                "h4_ema_abv_20",
                "h4_ema_abv_30",
                "h4_ema_abv_36",
                "h4_sma_abv_20",
                "h4_sma_abv_30",
            ),
        )
        self.assertEqual(REGIME_ATOMS[25:], REGIME_ATOMS_FRESH)
        self.assertEqual(REGIME_ATOMS, REGIME_ATOMS_PRIOR + REGIME_ATOMS_FRESH)
        self.assertEqual(
            REGIME_ATOMS_FRESH,
            (
                "h1_ema_abv_60",
                "h1_ema_abv_70",
                "h1_sma_abv_12",
                "h1_sma_abv_50",
                "h1_sma_abv_60",
                "h1_sma_abv_70",
                "h4_ema_abv_15",
                "h4_ema_abv_40",
                "h4_ema_abv_60",
                "h4_sma_abv_12",
                "h4_sma_abv_15",
                "h4_sma_abv_36",
                "h4_sma_abv_40",
            ),
        )
        self.assertNotIn("h1_sma_abv_18", REGIME_ATOMS)
        self.assertNotIn("h1_ema_abv_8", REGIME_ATOMS)
        self.assertTrue(set(REGIME_ADMIT_ATOMS).issubset(REGIME_ATOMS))
        self.assertTrue(set(FRESH_STACK_REGIME).issubset(REGIME_ATOMS))
        self.assertEqual(REGIME_CONT_ATOMS, ("sma_abv_50", "ema_abv_20"))
        self.assertEqual(FRESH_CONT_ATOMS, ("sma_abv_30", "ema_abv_30"))
        self.assertEqual(FRESH_RSI, "rsi_14_>55")
        self.assertEqual(UNDRY_CONT_ATOMS, ("sma_abv_40", "ema_abv_40"))
        self.assertEqual(UNDRY_RSI, "rsi_14_>60")
        self.assertEqual(UNDRY_MOM_PRIORITY, ("mom_18b_gt2pc", "mom_24b_gt2pc"))
        self.assertEqual(UNDRY_GAP_CONT, ("sma_abv_50", "ema_abv_20"))
        self.assertEqual(
            UNDRY_GAP_REGIME,
            ("h1_ema_abv_60", "h1_sma_abv_50", "h1_sma_abv_60"),
        )
        self.assertEqual(FRESH_STACK_REGIME[:5], (
            "h1_ema_abv_20",
            "h1_ema_abv_24",
            "h1_ema_abv_30",
            "h1_ema_abv_50",
            "h1_ema_abv_60",
        ))
        self.assertIn("h1_sma_abv_70", FRESH_STACK_REGIME)
        self.assertIn("h1_ema_abv_70", FRESH_STACK_REGIME)
        self.assertNotIn("mom_12b_gt2pc", UNDRY_MOM_PRIORITY)
        self.assertEqual(
            DEEP_STACK_REGIME,
            (
                "h1_ema_abv_20",
                "h1_ema_abv_24",
                "h1_ema_abv_30",
                "h1_ema_abv_50",
                "h1_ema_abv_60",
                "h1_sma_abv_20",
                "h1_sma_abv_24",
                "h1_sma_abv_30",
                "h1_sma_abv_50",
                "h1_sma_abv_60",
            ),
        )
        self.assertTrue(set(DEEP_STACK_REGIME).issubset(REGIME_ATOMS))
        self.assertEqual(DEEP_STACK_MOM, REGIME_MOM_PRIORITY)
        self.assertEqual(
            DEEP_STACK_TREND,
            ("sma_abv_50", "ema_abv_20", "sma_abv_20", "ema_abv_50"),
        )
        self.assertEqual(
            DEEP_STACK_TREND_PAIRS,
            (("sma_abv_50", "ema_abv_20"), ("sma_abv_20", "ema_abv_50")),
        )
        self.assertEqual(DEEP_STACK_RSI, "rsi_14_>50")
        self.assertEqual(DEEP_STACK_STRUCTURE_NS, (12, 24, 48))
        self.assertEqual(DEEP_STACK_STRUCTURE_TAGS, ("near_swing_hi",))
        self.assertEqual(RECIPE_MAX_ATOMS, 7)
        self.assertTrue(all(n <= 48 for n in DEEP_STACK_STRUCTURE_NS))
        self.assertEqual(
            MOM_FILTERS_HTF_EXPAND,
            (
                "mom_54b_gt2pc",
                "mom_66b_gt2pc",
                "mom_6b_gt4pc",
                "mom_30b_gt4pc",
                "mom_42b_gt4pc",
                "mom_24b_gt6pc",
                "mom_36b_gt6pc",
            ),
        )
        self.assertEqual(
            MOM_FILTERS_HTF_FRESH,
            (
                "mom_78b_gt2pc",
                "mom_90b_gt2pc",
                "mom_96b_gt2pc",
                "mom_48b_gt4pc",
                "mom_54b_gt4pc",
                "mom_72b_gt4pc",
                "mom_60b_gt6pc",
                "mom_18b_gt8pc",
            ),
        )
        self.assertEqual(
            MOM_FILTERS_HTF_INTERMEDIATE,
            (
                "mom_36b_gt8pc",
                "mom_42b_gt6pc",
                "mom_48b_gt8pc",
                "mom_54b_gt6pc",
                "mom_60b_gt4pc",
                "mom_66b_gt4pc",
            ),
        )
        self.assertIn("h1_sma_abv_", _REGIME_PREFIXES)
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
            set(
                MOM_FILTERS
                + MOM_FILTERS_HTF_DENSE
                + MOM_FILTERS_HTF_EXPAND
                + MOM_FILTERS_HTF_FRESH
                + GRIND_FILTERS
            ),
        )
        self.assertEqual(
            REGIME_DIP_PRIORITY,
            ("dip_24b_lt5pc", "dip_24b_lt6pc", "dip_18b_lt2pc"),
        )
        self.assertEqual(
            REGIME_DIP_BASES[:3],
            REGIME_DIP_PRIORITY,
        )
        self.assertEqual(
            set(REGIME_DIP_BASES),
            set(DIP_FILTERS) | set(REGIME_DIP_PRIORITY),
        )
        self.assertNotIn("dip_24b_lt4pc", REGIME_DIP_BASES)
        self.assertEqual(REGIME_ENTRY_BASES, REGIME_MOM_BASES + REGIME_DIP_BASES)
        self.assertEqual(DISCOVERY_REFILL_BATCH_SIZE, 16)
        self.assertLessEqual(DISCOVERY_REFILL_BATCH_SIZE, 24)
        self.assertGreaterEqual(DISCOVERY_REFILL_BATCH_SIZE, 8)
        # Frozen OOS gate — recipe expansion must not touch these.
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)
        # Later passes sit on top of 09-11 / morning near-level, not a rewrite.
        self.assertGreater(len(names), len(_snapshot_2026_09_11_recipe(STRUCTURE_NS)))
        self.assertGreater(len(names), len(_snapshot_2026_09_12_morning()))
        self.assertGreater(len(names), len(_snapshot_2026_09_12_leftover()))
        self.assertGreater(len(names), len(_snapshot_htf_regime()))

    def test_every_recipe_name_parses_and_has_structure(self):
        # Tape longer than STRUCTURE_NS max (96) so lookbacks can evaluate.
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

    def test_recipe_does_not_mint_structure_lookbacks_above_96(self):
        names = list(iter_recipe_names())
        self.assertEqual(STRUCTURE_NS, STRUCTURE_NS_THROUGH_96)
        self.assertEqual(max(STRUCTURE_NS), DEFAULT_STRUCTURE_LOOKBACK_MAX)
        for name in names:
            for _atom, n in structure_lookbacks(name):
                self.assertLessEqual(n, 96, msg=name)
        self.assertIn("don_hi_96", names)
        self.assertIn("dbl_bot_48", names)
        self.assertIn("don_lo_12", names)
        self.assertIn("near_swing_hi_24", names)
        self.assertNotIn("don_hi_192", names)
        self.assertNotIn("dbl_bot_168&ema_abv_50", names)
        self.assertFalse(any("don_hi_192" in n for n in names))
        self.assertFalse(any("dbl_bot_168" in n for n in names))

    def test_recipe_includes_longer_lookbacks_and_leftover_ands(self):
        names = list(iter_recipe_names())
        # Mint stops at 96 (farm ops cap). Leftover TREND ANDs stay at ≤96.
        self.assertNotIn("dip_6b_lt2pc&don_lo_108", names)
        self.assertNotIn("mom_12b_gt3pc&don_hi_192", names)
        self.assertNotIn("don_hi_144", names)
        self.assertNotIn("near_swing_lo_168", names)
        self.assertIn("don_hi_96", names)
        self.assertIn("dbl_bot_48", names)
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
        self.assertIn("h1_sma_abv_20&mom_18b_gt2pc", names)
        self.assertIn("h1_sma_abv_24&mom_18b_gt2pc", names)
        self.assertIn("h1_sma_abv_30&mom_18b_gt2pc", names)
        self.assertNotIn("h1_sma_abv_18&mom_18b_gt2pc", names)
        self.assertIn("h1_ema_abv_24&mom_18b_gt4pc", names)
        self.assertIn("h4_ema_abv_12&mom_18b_gt2pc", names)
        self.assertIn("h4_ema_abv_48&mom_18b_gt4pc", names)
        self.assertIn("h4_sma_abv_24&mom_12b_gt6pc", names)
        # Admit-island undry first: unused continuation 40 / rsi 60, not structure.
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_40", names)
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc&ema_abv_40", names)
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc&rsi_14_>60", names)
        self.assertIn("h1_ema_abv_60&mom_18b_gt2pc&sma_abv_50", names)
        self.assertIn("h1_sma_abv_50&mom_18b_gt2pc&ema_abv_20", names)
        self.assertIn("h1_ema_abv_20&mom_36b_gt8pc&sma_abv_50", names)
        self.assertIn("h1_sma_abv_70&mom_18b_gt2pc", names)
        self.assertNotIn("h1_ema_abv_20&mom_12b_gt2pc&sma_abv_40", names)
        # Drained leftover 30-cont / rsi55 still in the stream.
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_30", names)
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc&ema_abv_30", names)
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc&rsi_14_>55", names)
        self.assertIn("h1_ema_abv_15&mom_18b_gt2pc&rsi_14_>50", names)
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_30&rsi_14_>50", names)
        self.assertNotIn("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_30&dip_24b_lt5pc", names)
        self.assertNotIn("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_40&dip_24b_lt5pc", names)
        self.assertIn("h1_ema_abv_60&mom_18b_gt2pc", names)
        self.assertIn("h1_sma_abv_12&mom_78b_gt2pc", names)
        self.assertIn("h4_ema_abv_15&mom_18b_gt8pc", names)
        self.assertEqual(names[0], "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_40")
        self.assertLess(
            names.index("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_40"),
            names.index("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_30"),
        )
        self.assertLess(
            names.index("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_30"),
            names.index("h1_ema_abv_20&dip_24b_lt5pc"),
        )
        # Winner 3-atoms (drained prefix): HTF×mom×continuation, not dip, not structure.
        self.assertNotIn("h1_ema_abv_20&mom_18b_gt2pc&dip_24b_lt5pc", names)
        self.assertNotIn("h1_ema_abv_24&mom_18b_gt2pc&dip_24b_lt6pc", names)
        self.assertNotIn("h1_sma_abv_24&mom_18b_gt2pc&dip_18b_lt2pc", names)
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_50", names)
        self.assertIn("h1_ema_abv_24&mom_12b_gt2pc&ema_abv_20", names)
        # HTF×mom×structure stays refused.
        self.assertNotIn("h4_ema_abv_24&dip_12b_lt2pc&near_swing_lo_12", names)
        self.assertNotIn("h4_sma_abv_50&mom_36b_gt2pc&don_hi_24", names)
        self.assertNotIn("h1_ema_abv_24&mom_18b_gt2pc&don_hi_12", names)
        self.assertNotIn("h1_ema_abv_15&mom_18b_gt4pc&near_swing_lo_24", names)
        self.assertNotIn("h1_ema_abv_18&mom_18b_gt2pc&don_hi_12", names)
        self.assertNotIn("h1_ema_abv_36&mom_18b_gt2pc&near_swing_lo_24", names)
        self.assertNotIn("h1_sma_abv_24&mom_18b_gt2pc&don_hi_12", names)
        self.assertNotIn("h1_sma_abv_20&dip_24b_lt5pc&near_swing_lo_24", names)
        self.assertEqual("h1_ema_abv_18&mom_18b_gt2pc".count("&"), 1)
        self.assertEqual("h1_ema_abv_36&mom_18b_gt2pc".count("&"), 1)
        self.assertTrue(
            any("h4_ema_abv_24" in n and ("mom_12b_gt2pc" in n or "sma_abv_20" in n) for n in names)
        )
        htf = _snapshot_htf_regime()
        self.assertEqual(names[: len(htf)], htf)
        self.assertTrue(any(n.count("&") == 2 for n in htf), msg="winner 3-atoms first")
        self.assertFalse(
            any(
                any(tok.startswith(("don_hi_", "near_swing_lo_")) for tok in n.split("&"))
                for n in htf
            )
        )
        two_atom = [n for n in htf if n.count("&") == 1]
        three_atom = [n for n in htf if n.count("&") == 2]
        self.assertTrue(three_atom)
        self.assertLess(names.index(three_atom[0]), names.index(two_atom[0]))
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
            self.assertLess(
                names.index(f"{regime}&dip_24b_lt5pc"),
                names.index(f"{regime}&dip_24b_lt6pc"),
                msg=regime,
            )
            self.assertLess(
                names.index(f"{regime}&dip_24b_lt6pc"),
                names.index(f"{regime}&dip_18b_lt2pc"),
                msg=regime,
            )
            self.assertLess(
                names.index(f"{regime}&dip_18b_lt2pc"),
                names.index(f"{regime}&dip_6b_lt2pc"),
                msg=regime,
            )
            self.assertNotIn(f"{regime}&dip_24b_lt4pc", names)
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
        self.assertTrue(name_is_parseable("h1_sma_abv_20&mom_18b_gt2pc"))
        self.assertTrue(name_is_parseable("h1_sma_abv_24&mom_18b_gt2pc"))
        self.assertTrue(name_is_parseable("h1_sma_abv_30&dip_24b_lt5pc"))
        self.assertTrue(name_is_parseable("h1_ema_abv_24&dip_24b_lt6pc"))
        # Parser accepts the period; refill must not mint the collapsing 18 twin.
        self.assertTrue(name_is_parseable("h1_sma_abv_18"))
        self.assertNotIn("h1_sma_abv_18", REGIME_ATOMS)

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
            "h1_sma_abv_20",
            "h1_sma_abv_24",
            "h1_sma_abv_30",
            "h4_ema_abv_12",
            "h4_ema_abv_48",
            "h4_sma_abv_24",
            "h1_ema_abv_12",
            "h1_ema_abv_40",
            "h1_ema_abv_50",
            "h1_sma_abv_15",
            "h1_sma_abv_36",
            "h1_sma_abv_40",
            "h4_ema_abv_20",
            "h4_ema_abv_30",
            "h4_ema_abv_36",
            "h4_sma_abv_20",
            "h4_sma_abv_30",
            *REGIME_ATOMS_FRESH,
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
        self.assertEqual(
            near_duplicate_key("h1_sma_abv_18"),
            near_duplicate_key("h1_sma_abv_20"),
        )
        self.assertNotEqual(
            near_duplicate_key("h1_sma_abv_20"),
            near_duplicate_key("h1_ema_abv_20"),
        )
        self.assertEqual(
            near_duplicate_key("dip_24b_lt4pc"),
            near_duplicate_key("dip_24b_lt5pc"),
        )
        self.assertNotEqual(
            near_duplicate_key("dip_24b_lt5pc"),
            near_duplicate_key("dip_24b_lt6pc"),
        )
        self.assertNotIn("h1_sma_abv_18", REGIME_ATOMS)
        self.assertNotIn("dip_24b_lt4pc", REGIME_DIP_BASES)
        parked_mom = {near_duplicate_key(n) for n in MOM_FILTERS}
        seen_mom = set(parked_mom)
        for atom in (
            MOM_FILTERS_HTF_DENSE
            + MOM_FILTERS_HTF_EXPAND
            + MOM_FILTERS_HTF_FRESH
            + MOM_FILTERS_HTF_INTERMEDIATE
        ):
            self.assertTrue(name_is_parseable(atom), msg=atom)
            parse_strategy(atom)
            key = near_duplicate_key(atom)
            self.assertNotIn(key, seen_mom, msg=atom)
            seen_mom.add(key)
        self.assertNotEqual(
            near_duplicate_key("sma_abv_30"),
            near_duplicate_key("sma_abv_20"),
        )
        self.assertNotEqual(
            near_duplicate_key("sma_abv_30"),
            near_duplicate_key("sma_abv_50"),
        )
        self.assertNotEqual(
            near_duplicate_key("sma_abv_40"),
            near_duplicate_key("sma_abv_30"),
        )
        self.assertNotEqual(
            near_duplicate_key("sma_abv_40"),
            near_duplicate_key("sma_abv_50"),
        )
        self.assertNotEqual(
            near_duplicate_key("rsi_14_>55"),
            near_duplicate_key("rsi_14_>50"),
        )
        self.assertNotEqual(
            near_duplicate_key("rsi_14_>60"),
            near_duplicate_key("rsi_14_>55"),
        )
        self.assertNotEqual(
            near_duplicate_key("mom_18b_gt8pc"),
            near_duplicate_key("mom_18b_gt6pc"),
        )
        self.assertNotEqual(
            near_duplicate_key("h1_ema_abv_60"),
            near_duplicate_key("h1_ema_abv_50"),
        )
        self.assertNotEqual(
            near_duplicate_key("h1_sma_abv_12"),
            near_duplicate_key("h1_sma_abv_15"),
        )
        self.assertNotEqual(
            near_duplicate_key("h1_sma_abv_70"),
            near_duplicate_key("h1_sma_abv_60"),
        )
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
        dense_keys = {near_duplicate_key(n) for n in MOM_FILTERS_HTF_DENSE}
        for name in MOM_FILTERS_HTF_DENSE + MOM_FILTERS_HTF_EXPAND + MOM_FILTERS_HTF_FRESH:
            key = near_duplicate_key(name)
            self.assertNotIn(key, parked_mom, msg=f"{name} collapses onto {key}")
        fresh_keys = {near_duplicate_key(n) for n in MOM_FILTERS_HTF_FRESH}
        for name in MOM_FILTERS_HTF_EXPAND + MOM_FILTERS_HTF_FRESH:
            key = near_duplicate_key(name)
            self.assertNotIn(key, dense_keys, msg=f"{name} collapses onto {key}")
        expand_keys = {near_duplicate_key(n) for n in MOM_FILTERS_HTF_EXPAND}
        for name in MOM_FILTERS_HTF_FRESH:
            key = near_duplicate_key(name)
            self.assertNotIn(key, expand_keys, msg=f"{name} collapses onto {key}")
        parked_plus = parked_mom | dense_keys | expand_keys | fresh_keys
        for name in MOM_FILTERS_HTF_INTERMEDIATE:
            key = near_duplicate_key(name)
            self.assertNotIn(key, parked_plus, msg=f"{name} collapses onto {key}")
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
    def test_dry_refill_emits_fresh_winner_shaped_3atoms(self):
        uni = generate_universe()
        added = next_refill_batch(taken_names=uni, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(added[0], "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_40")
        hit = [
            n for n in added
            if n.count("&") == 2
            and any(tok.startswith("h1_") for tok in n.split("&"))
            and any(tok.startswith("mom_") for tok in n.split("&"))
            and any(tok in ("sma_abv_40", "ema_abv_40") for tok in n.split("&"))
        ]
        self.assertTrue(hit, msg=f"expected HTF×mom×sma/ema_abv_40 in {added}")
        self.assertFalse(any("mom_12b_" in n for n in added))
        for name in added:
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertTrue(_has_mint_tag(name), msg=name)
            self.assertFalse(
                any(tok.startswith(("don_hi_", "near_swing_")) for tok in name.split("&")),
                msg=name,
            )

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

    def test_sma_twins_and_priority_dips_skip_near_dupes(self):
        uni = generate_universe()
        taken = set(uni) | {
            "h1_sma_abv_20&mom_18b_gt2pc",
            "h1_ema_abv_24&dip_24b_lt5pc",
        }
        added = next_refill_batch(taken_names=taken, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        taken_keys = {near_duplicate_key(n) for n in taken}
        for name in added:
            self.assertNotIn(name, taken)
            self.assertNotIn(near_duplicate_key(name), taken_keys, msg=name)
            self.assertTrue(name_is_parseable(name), msg=name)
        self.assertNotIn("h1_sma_abv_18&mom_18b_gt2pc", added)
        self.assertNotIn("h1_ema_abv_24&dip_24b_lt4pc", added)
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)

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

    def test_parking_morning_recipe_still_feeds_leftover_ands(self):
        # Farm drained STRUCTURE_NS through 96 (~917 unique). Fail-once stays.
        # Leftover TREND / ema_stack / 3-atom ANDs (still ≤96) must refill.
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
        leftover_set = set(_snapshot_2026_09_12_leftover())
        for name in added:
            self.assertNotIn(name, taken)
            self.assertNotIn(near_duplicate_key(name), taken_keys, msg=name)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertTrue(_has_mint_tag(name), msg=name)
            self.assertIn(name, leftover_set)
            for _atom, n in structure_lookbacks(name):
                self.assertLessEqual(n, 96, msg=name)

    def test_parking_leftover_recipe_still_feeds_trend_participation(self):
        # Farm chewed leftover-TREND through 96 (~1000 unique, 0 pass).
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

    def test_expansion_adds_200_plus_never_near_dup_keys(self):
        old_keys = {
            near_duplicate_key(n) for n in _snapshot_pre_2026_09_14_recipe()
        }
        new_keys = {
            near_duplicate_key(n)
            for n in iter_recipe_names()
            if name_is_parseable(n)
        }
        added = new_keys - old_keys
        self.assertGreaterEqual(len(added), 200, msg=f"new distinct keys={len(added)}")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)
        self.assertEqual(max(STRUCTURE_NS), 96)

    def test_refill_after_5037_taken_still_full_batch(self):
        # Prod farm is dry at ~5037 unique tested names. Simulate that
        # taken set as the pre-expansion recipe plus padding.
        taken = set(_snapshot_pre_2026_09_14_recipe()) | set(generate_universe())
        i = 0
        while len(taken) < 5037:
            taken.add(f"parked_dummy_{i}")
            i += 1
        self.assertGreaterEqual(len(taken), 5037)
        added = next_refill_batch(taken_names=taken, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        taken_keys = {near_duplicate_key(n) for n in taken}
        for name in added:
            self.assertNotIn(name, taken)
            self.assertNotIn(near_duplicate_key(name), taken_keys, msg=name)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertTrue(_has_mint_tag(name), msg=name)
            self.assertFalse(
                any(tok.startswith(("don_hi_", "near_swing_")) for tok in name.split("&")),
                msg=name,
            )
            self.assertFalse(name_has_mom_gt_and_dip(name), msg=name)

    def test_next_refill_batch_skips_mom_gt_and_dip_candidates(self):
        self.assertTrue(
            name_has_mom_gt_and_dip("h1_ema_abv_20&mom_18b_gt2pc&dip_24b_lt5pc")
        )
        self.assertTrue(
            name_has_mom_gt_and_dip(
                "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_50&ema_abv_20"
                "&dip_24b_lt5pc&rsi_14_>50&near_swing_hi_24"
            )
        )
        self.assertFalse(name_has_mom_gt_and_dip("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_50"))
        self.assertFalse(name_has_mom_gt_and_dip("h1_ema_abv_20&mom_18b_gt2pc&rsi_14_>50"))
        self.assertFalse(name_has_mom_gt_and_dip("h1_ema_abv_20&dip_24b_lt5pc"))
        self.assertFalse(name_has_mom_gt_and_dip("dip_24b_lt5pc&sma_abv_50"))
        self.assertFalse(name_has_mom_gt_and_dip("mom_18b_gt2pc&don_hi_12"))
        names = list(iter_recipe_names())
        bad = [n for n in names if name_has_mom_gt_and_dip(n)]
        self.assertEqual(bad, [])
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_30", names)
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_30&rsi_14_>50", names)
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_50", names)
        self.assertIn("h1_ema_abv_20&mom_18b_gt2pc&rsi_14_>55", names)
        self.assertIn("h1_ema_abv_20&dip_24b_lt5pc", names)
        self.assertIn("h4_sma_abv_50&dip_12b_lt2pc", names)
        self.assertNotIn("h1_ema_abv_20&mom_18b_gt2pc&dip_24b_lt5pc", names)
        self.assertNotIn("h1_ema_abv_20&mom_18b_gt2pc&sma_abv_30&dip_24b_lt5pc", names)
        self.assertNotIn(
            "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_50&dip_24b_lt5pc",
            names,
        )
        # Parser still accepts already-queued mom∧dip so they can drain once.
        queued = "h1_ema_abv_20&mom_18b_gt2pc&dip_24b_lt5pc"
        self.assertTrue(name_is_parseable(queued))
        parse_strategy(queued)
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)
        self.assertEqual(max(STRUCTURE_NS), 96)
        uni = generate_universe()
        added = next_refill_batch(taken_names=uni, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        self.assertFalse(any(name_has_mom_gt_and_dip(n) for n in added))
        with patch(
            "hedge_fund.trading.refill.iter_recipe_names",
            return_value=iter(
                [
                    "h1_ema_abv_20&mom_18b_gt2pc&dip_24b_lt5pc",
                    "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_30&dip_24b_lt5pc",
                    "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_30",
                    "h1_ema_abv_20&dip_24b_lt5pc",
                ]
            ),
        ):
            skipped = next_refill_batch(taken_names=[], n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(
            skipped,
            [
                "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_30",
                "h1_ema_abv_20&dip_24b_lt5pc",
            ],
        )
        self.assertFalse(any(name_has_mom_gt_and_dip(n) for n in skipped))

    def test_recipe_emits_depth_4_through_7_admit_stacks(self):
        names = list(iter_recipe_names())
        depths = [n.count("&") + 1 for n in names]
        self.assertGreaterEqual(max(depths), 4)
        self.assertGreaterEqual(max(depths), 5)
        self.assertLessEqual(max(depths), RECIPE_MAX_ATOMS)
        four = "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_50&rsi_14_>50"
        five = "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_50&ema_abv_20&rsi_14_>50"
        dip_four = "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_50&dip_24b_lt5pc"
        dip_five = "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_50&ema_abv_20&dip_24b_lt5pc"
        dip_six = "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_50&ema_abv_20&dip_24b_lt5pc&rsi_14_>50"
        dip_seven = (
            "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_50&ema_abv_20"
            "&dip_24b_lt5pc&rsi_14_>50&near_swing_hi_24"
        )
        for name in (four, five):
            self.assertIn(name, names, msg=name)
            self.assertTrue(name_is_parseable(name), msg=name)
            parse_strategy(name)
        for name in (dip_four, dip_five, dip_six, dip_seven):
            self.assertNotIn(name, names, msg=name)
            self.assertTrue(name_is_parseable(name), msg=name)
        self.assertLess(names.index(four), names.index(five))
        self.assertLess(names.index(four), names.index("dip_6b_lt2pc&don_lo_6"))
        self.assertLess(names.index(five), names.index("mom_30b_gt1pc&don_hi_6"))
        self.assertNotIn("h1_ema_abv_20&mom_18b_gt2pc&don_hi_12", names)
        for name in names:
            parts = [p for p in name.split("&") if p]
            self.assertLessEqual(len(parts), 7, msg=name)
            self.assertFalse(name_has_mom_gt_and_dip(name), msg=name)
            for _atom, n in structure_lookbacks(name):
                self.assertLessEqual(n, 96, msg=name)
            if name.count("&") >= 3 and any(
                tok.startswith("h1_") for tok in name.split("&")
            ):
                for tok in name.split("&"):
                    if tok.startswith(("don_hi_", "near_swing_lo_")):
                        self.fail(f"expensive structure on deep HTF stack: {name}")
                    if tok.startswith("near_swing_hi_"):
                        n = int(tok.rsplit("_", 1)[-1])
                        self.assertLessEqual(n, 48, msg=name)
        self.assertFalse(name_is_parseable(dip_seven + "&sma_abv_100"))
        self.assertTrue(name_is_parseable(five))
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)

    def test_deep_stack_family_adds_farm_feasible_new_keys(self):
        from hedge_fund.trading.refill import _regime_deep_stacks

        prior_keys = {near_duplicate_key(n) for n in _snapshot_pre_deep_stack_recipe()}
        new_keys = {
            near_duplicate_key(n)
            for n in _regime_deep_stacks()
            if name_is_parseable(n)
        }
        added = new_keys - prior_keys
        self.assertGreaterEqual(len(added), 150, msg=f"new distinct keys={len(added)}")
        self.assertLessEqual(len(added), 2000, msg=f"new distinct keys={len(added)}")
        self.assertEqual(max(STRUCTURE_NS), 96)
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)

    def test_fresh_family_adds_farm_feasible_new_keys(self):
        prior_keys = {near_duplicate_key(n) for n in _snapshot_pre_fresh_recipe()}
        new_keys = {
            near_duplicate_key(n)
            for n in iter_recipe_names()
            if name_is_parseable(n)
        }
        added = new_keys - prior_keys
        self.assertGreaterEqual(len(added), 500, msg=f"new distinct keys={len(added)}")
        self.assertLessEqual(len(added), 2000, msg=f"new distinct keys={len(added)}")
        self.assertEqual(max(STRUCTURE_NS), 96)
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)

    def test_next_refill_batch_fills_deep_stacks_against_large_taken(self):
        taken = set(_snapshot_pre_deep_stack_recipe()) | set(generate_universe())
        i = 0
        while len(taken) < 5037:
            taken.add(f"parked_dummy_{i}")
            i += 1
        added = next_refill_batch(taken_names=taken, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        taken_keys = {near_duplicate_key(n) for n in taken}
        for name in added:
            self.assertNotIn(name, taken)
            self.assertNotIn(near_duplicate_key(name), taken_keys, msg=name)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertGreaterEqual(name.count("&") + 1, 3, msg=name)
            self.assertLessEqual(name.count("&") + 1, 7, msg=name)
            self.assertTrue(_has_mint_tag(name), msg=name)
            self.assertFalse(name_has_mom_gt_and_dip(name), msg=name)
            for _atom, n in structure_lookbacks(name):
                self.assertLessEqual(n, 96, msg=name)

    def test_next_refill_batch_fills_fresh_against_7200_taken(self):
        taken = set(_snapshot_pre_fresh_recipe()) | set(generate_universe())
        i = 0
        while len(taken) < 7200:
            taken.add(f"parked_dummy_{i}")
            i += 1
        self.assertGreaterEqual(len(taken), 7200)
        added = next_refill_batch(taken_names=taken, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        taken_keys = {near_duplicate_key(n) for n in taken}
        for name in added:
            self.assertNotIn(name, taken)
            self.assertNotIn(near_duplicate_key(name), taken_keys, msg=name)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertGreaterEqual(name.count("&") + 1, 3, msg=name)
            self.assertLessEqual(name.count("&") + 1, 5, msg=name)
            self.assertTrue(_has_mint_tag(name), msg=name)
            self.assertFalse(
                any(tok.startswith(("don_hi_", "near_swing_")) for tok in name.split("&")),
                msg=name,
            )
            self.assertFalse(name_has_mom_gt_and_dip(name), msg=name)
        self.assertEqual(added[0], "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_40")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)

    def test_next_refill_batch_fills_undry_against_8726_taken(self):
        """Farm-dry: unique_tested ≈ 8726 drained the #64 recipe. Undry prefix must refill."""
        new_atoms = set(UNDRY_CONT_ATOMS) | {UNDRY_RSI, "h1_sma_abv_70"} | set(
            MOM_FILTERS_HTF_INTERMEDIATE
        )
        drained = [
            n
            for n in iter_recipe_names()
            if not (set(n.split("&")) & new_atoms)
        ]
        taken = set(drained) | set(generate_universe())
        i = 0
        while len(taken) < 8726:
            taken.add(f"parked_dummy_{i}")
            i += 1
        self.assertGreaterEqual(len(taken), 8726)
        added = next_refill_batch(taken_names=taken, n=20)
        self.assertGreaterEqual(len(added), 20)
        taken_keys = {near_duplicate_key(n) for n in taken}
        seen = set()
        for name in added:
            self.assertNotIn(name, taken)
            key = near_duplicate_key(name)
            self.assertNotIn(key, taken_keys, msg=name)
            self.assertNotIn(key, seen)
            seen.add(key)
            self.assertTrue(name_is_parseable(name), msg=name)
            self.assertTrue(_has_mint_tag(name), msg=name)
            self.assertFalse(name_has_mom_gt_and_dip(name), msg=name)
            self.assertFalse(
                any(tok.startswith(("don_hi_", "near_swing_")) for tok in name.split("&")),
                msg=name,
            )
            self.assertNotIn("mom_12b_gt2pc", name.split("&"), msg=name)
            for _atom, n in structure_lookbacks(name):
                self.assertLessEqual(n, 96, msg=name)
        self.assertEqual(added[0], "h1_ema_abv_20&mom_18b_gt2pc&sma_abv_40")
        self.assertTrue(
            any(tok in ("sma_abv_40", "ema_abv_40") for tok in added[0].split("&"))
        )
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)

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
