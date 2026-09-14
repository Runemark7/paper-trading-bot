"""Auto-refill never-tested discovery names when the leftover drain runs dry.

Fail-once parks rejects forever, so a static ``generate_universe()`` list
eventually has eligible=0. This module walks a **bounded** combinatorial
recipe of allowed OHLC structure atoms ANDed with existing 5m dip/mom/sma
filters, skips near-duplicates of champions / graduated / discovery_log
fails, and appends the next handful to ``discovery_extended.json`` on the
paper-state PVC. Tournament then drains those names under the existing
1 / 90s cycle budget. No human PR per batch.

The static universe stays inside ``UNIVERSE_TARGET_MAX`` (~40–120). The
sidecar is the pending queue: after a refill, never-tested extras are one
``DISCOVERY_REFILL_BATCH_SIZE`` handful, not thousands of clones. Recipe
generation is string-only (no history load). 2026-09-12 first added unused
Donchian / swing / near-level lookbacks through 96; a same-date later
pass briefly extended ``STRUCTURE_NS`` through 192 (9h–16h on 5m) and
leftover TREND / ``ema_stack`` / 3-atom families already in
``parse_strategy``. 2026-09-13 later: mint no longer emits structure
``N>96`` (``STRUCTURE_NS`` == ``STRUCTURE_NS_THROUGH_96``) so those
names are not fail-parked as ``lookback_too_expensive``. Already-tested
leftover 108–192 names stay parked. Worker fail-park remains the
backstop. Dip/mom/HTF lookbacks that are not structure atoms stay.
A same-date later pass broadens parser-allowed dip/mom lookbacks and
``%`` thresholds and adds continuation (trend-participation) ANDs so
new names can stay in a strong B&H OOS window long enough to clear
trades + Sharpe without softening the gate — still no named candlesticks.
A 2026-09-13 pass adds shallower ``gt1pc`` / ``lt1pc`` grind bases
(lookbacks unused by legacy/wide so ``near_duplicate_key`` stays
distinct) and expands short-MA 3-atoms onto every WIDE mom/dip ×
``don_hi`` / ``near_swing_hi``. Same-date later: causal HTF
buyer-regime atoms (``h4_ema_abv_24`` / ``h4_sma_abv_50`` /
``h1_ema_abv_24``) AND onto those DIP/MOM/WIDE/GRIND bases.
Same-date later: densify HTF periods around the first natural
OOS admit and emit HTF×mom (incl. a short-continuation dense
mom set) before HTF×dip. Same-date later: HTF×mom (and HTF×dip)
are 2-atom only — no ``don_hi`` / ``near_swing_lo`` AND on that
family. Same-date later: densify ``h1_ema_abv_18`` / ``h1_ema_abv_36``
around the admit island and emit ``mom_18b_gt2pc`` first on the
regime path. Same-date later: mint non-collapsing ``h1_sma_abv_{20,24,30}``
twins of that island and emit mild pullback dips
(``dip_24b_lt5pc`` / ``dip_24b_lt6pc`` / ``dip_18b_lt2pc``) first on
the regime×dip path. Same-date 2026-09-14: un-dry the farm by
combining winning HTF×mom with continuation / RSI 3-atoms
(not structure) and densifying unused distinct HTF periods + mom
grids. Same-date later: densify AND stacks on that admit island
(role buckets, not a cartesian). Cheap ``near_swing_hi`` N≤48 only
at depth 7; no HTF×mom×expensive structure. Same-date later:
un-dry again — unused distinct HTF periods / mom lookbacks/% plus
winner-shaped stacks (REGIME+MOM × new continuation × rsi)
emit **first** so dry refill does not stall on already-parked depth-7
``near_swing`` names. Same-date later: never AND ``mom_*_gt*`` with
``dip_*`` in the same stack (trades=0 on the full tape; RSI siblings
stay). Already-queued mom∧dip extended names may still drain once
(fail-once). Same-date later: un-dry from the admit island —
unused continuation ``sma_abv_40`` / ``ema_abv_40`` (distinct from
20/30/50), ``rsi_14_>60``, intermediate mom (not short-12),
``h1_sma_abv_70``, and DEEP 4–5 stacks on paid-off ``h1_*_abv_50/60``
spines emit **first**. Still no named candlesticks. OOS gates unchanged.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from hedge_fund.paths import state_root
from hedge_fund.trading.constants import DISCOVERY_REFILL_BATCH_SIZE
from hedge_fund.trading.universe import generate_universe, near_duplicate_key

DISCOVERY_EXTENDED = "discovery_extended.json"

# Frozen 2026-09-11 / morning 3×3 (already in the static universe).
# Old recipe families keep these so a mid-drain farm's parked keys stay
# stable and we do not multiply more dip×support mean-reversion clones.
DIP_FILTERS_LEGACY: tuple[str, ...] = (
    "dip_6b_lt2pc",
    "dip_12b_lt3pc",
    "dip_24b_lt5pc",
)
MOM_FILTERS_LEGACY: tuple[str, ...] = (
    "mom_6b_gt2pc",
    "mom_12b_gt3pc",
    "mom_24b_gt5pc",
)

# Parser already allows mom_\\d+b_gt\\d+pc / dip_\\d+b_lt\\d+pc.
# Extra lookbacks + % thresholds, spaced so near_duplicate_key does not
# collapse onto the legacy three (canon: 6/2, 12/4, 24/4).
# Shallower % / longer lookbacks: more time in a grind-up than 6b/2%
# noise (already missed B&H) or 5% crash-dips (sit out the trend).
DIP_FILTERS_WIDE: tuple[str, ...] = (
    "dip_12b_lt2pc",
    "dip_18b_lt2pc",
    "dip_24b_lt2pc",
    "dip_36b_lt2pc",
    "dip_48b_lt2pc",
    "dip_36b_lt4pc",
)
MOM_FILTERS_WIDE: tuple[str, ...] = (
    "mom_12b_gt2pc",
    "mom_18b_gt2pc",
    "mom_24b_gt2pc",
    "mom_36b_gt2pc",
    "mom_48b_gt2pc",
    "mom_72b_gt2pc",
    "mom_36b_gt4pc",
    "mom_48b_gt6pc",
)

# Parser-allowed 1% grind. near_duplicate_key maps gt1pc→gt2pc / lt1pc→lt2pc,
# so lookbacks must not already appear in LEGACY/WIDE (6/12/18/24/36/48/72).
# 30=2.5h, 42=3.5h, 60=5h, 84=7h on 5m — shallower % stays in a grind-up.
DIP_FILTERS_GRIND: tuple[str, ...] = (
    "dip_30b_lt1pc",
    "dip_42b_lt1pc",
    "dip_60b_lt1pc",
)
MOM_FILTERS_GRIND: tuple[str, ...] = (
    "mom_30b_gt1pc",
    "mom_42b_gt1pc",
    "mom_60b_gt1pc",
    "mom_84b_gt1pc",
)

# Public mint bases = legacy ∪ wide ∪ grind. Old generators use *_LEGACY.
DIP_FILTERS: tuple[str, ...] = DIP_FILTERS_LEGACY + DIP_FILTERS_WIDE + DIP_FILTERS_GRIND
MOM_FILTERS: tuple[str, ...] = MOM_FILTERS_LEGACY + MOM_FILTERS_WIDE + MOM_FILTERS_GRIND

# Shorter MAs stay above price longer in a bull OOS than sma_abv_200.
# GRIND_FILTERS is the same pair — HTF recipe names them grind (stay-in-trend).
CONTINUATION_TRENDS: tuple[str, ...] = (
    "sma_abv_20",
    "ema_abv_20",
)
GRIND_FILTERS: tuple[str, ...] = CONTINUATION_TRENDS

# Causal HTF buyer-regime (parse_strategy). Small set — not a candlestick zoo.
# Long-only: sellers' HTF → atom False → no new long (flat).
# Extra periods stay distinct under near_duplicate_key / _round_period
# vs the shipped three (24→25, sma_50, h1/24→25), except 18→20
# (same canon as 20; still minted). Skip 10 (collides with 12→10)
# and ema_50 (collides with ema_48→50) on the same tf+kind.
# Frozen 2026-09-14 morning + #59/#60 neighbors (25). Leftover 2-atom
# pair generator stays on this set so new HTF 2-atoms are not mixed
# into the already-drained prefix.
REGIME_ATOMS_PRIOR: tuple[str, ...] = (
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
)
# 2026-09-14 later: unused canons. Verified against _round_period:
# 60→60, 70→70, 12→10 (h1/h4 sma; h1 ema 12 already minted),
# 15→15, 36→35, 40→40. Do not mint h1_ema_abv_8 (8→10, same as 12)
# or h4_ema_abv_50 / h4_sma_abv_48 (48→50). Same-date later:
# h1_sma_abv_70 (70→70, twin of minted ema_70). Skip 25 (24→25)
# and 80/90 (far from the 20–60 admit island).
REGIME_ATOMS_FRESH: tuple[str, ...] = (
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
)
REGIME_ATOMS: tuple[str, ...] = REGIME_ATOMS_PRIOR + REGIME_ATOMS_FRESH
# Winning HTF island + nearby distinct (or exact-period 18) for
# selective 3-atoms. Not the full REGIME_ATOMS cartesian — h4
# leftovers stay 2-atom. Combine what already works.
REGIME_ADMIT_ATOMS: tuple[str, ...] = (
    "h1_ema_abv_20",
    "h1_ema_abv_24",
    "h1_ema_abv_30",
    "h1_sma_abv_24",
    "h1_sma_abv_30",
    "h1_sma_abv_20",
    "h1_ema_abv_15",
    "h1_ema_abv_18",
    "h1_ema_abv_36",
    "h1_ema_abv_12",
    "h1_ema_abv_40",
    "h1_ema_abv_50",
    "h1_sma_abv_15",
    "h1_sma_abv_36",
    "h1_sma_abv_40",
)
# Parser-allowed 5m continuation tags for regime&mom&trend 3-atoms.
REGIME_CONT_ATOMS: tuple[str, ...] = (
    "sma_abv_50",
    "ema_abv_20",
)
# 4–7 atom stacks sit on the paid-off island only — not every
# REGIME_ADMIT_ATOMS neighbor. Spine is always REGIME+MOM.
# Optional extras: 0–2 continuation, 0–1 RSI. Never a dip on this
# spine (mom_gt ∧ dip → 0 trades). Cheap near_swing_hi (N≤48) at
# depth 7 only was dip-gated and is no longer minted.
DEEP_STACK_REGIME: tuple[str, ...] = (
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
)
DEEP_STACK_TREND: tuple[str, ...] = (
    "sma_abv_50",
    "ema_abv_20",
    "sma_abv_20",
    "ema_abv_50",
)
DEEP_STACK_TREND_PAIRS: tuple[tuple[str, str], ...] = (
    ("sma_abv_50", "ema_abv_20"),
    ("sma_abv_20", "ema_abv_50"),
)
DEEP_STACK_RSI: str = "rsi_14_>50"
DEEP_STACK_STRUCTURE_NS: tuple[int, ...] = (12, 24, 48)
DEEP_STACK_STRUCTURE_TAGS: tuple[str, ...] = ("near_swing_hi",)
RECIPE_MAX_ATOMS: int = 7
# Winner-shaped 3–5 atom densify (2026-09-14 later). Unused 5m
# continuation (30→30, distinct from 20/50) and rsi_14_>55 (55→55,
# distinct from >50). Extra h1 periods join admit for these stacks
# only — h4 leftovers stay 2-atom. No near_swing / don_hi here.
FRESH_CONT_ATOMS: tuple[str, ...] = (
    "sma_abv_30",
    "ema_abv_30",
)
FRESH_RSI: str = "rsi_14_>55"
# 2026-09-14 later: unused continuation 40 (40→40, distinct from
# 20/30/50) and rsi_14_>60 (60→60, distinct from >50/>55). Skip
# sma_abv_25 (too close to 20/30 in spirit, still free) as the
# primary undry — 40 sits between paid-off 30 and 50.
UNDRY_CONT_ATOMS: tuple[str, ...] = (
    "sma_abv_40",
    "ema_abv_40",
)
UNDRY_RSI: str = "rsi_14_>60"
# Admit-island first so dry refill hits highest-EV spines before
# drained leftovers. Neighbors (15/18/36/12/40/70) follow.
# h1_ema_abv_70 already had 2-atoms; it lacked winner stacks.
FRESH_STACK_REGIME: tuple[str, ...] = (
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
    "h1_ema_abv_15",
    "h1_ema_abv_18",
    "h1_ema_abv_36",
    "h1_ema_abv_12",
    "h1_ema_abv_40",
    "h1_ema_abv_70",
    "h1_sma_abv_15",
    "h1_sma_abv_36",
    "h1_sma_abv_40",
    "h1_sma_abv_12",
    "h1_sma_abv_70",
)
# Paid-off 50/60 spines were FRESH 3–4 only; they lacked
# REGIME_CONT (sma_abv_50 / ema_abv_20) 3-atoms. Emit those in
# the undry prefix. ema_50 is already in REGIME_ADMIT_ATOMS.
UNDRY_GAP_REGIME: tuple[str, ...] = (
    "h1_ema_abv_60",
    "h1_sma_abv_50",
    "h1_sma_abv_60",
)
UNDRY_GAP_CONT: tuple[str, ...] = (
    "sma_abv_50",
    "ema_abv_20",
)
# Skip mom_12b on the undry prefix (research + Sharpe near-miss).
UNDRY_MOM_PRIORITY: tuple[str, ...] = (
    "mom_18b_gt2pc",
    "mom_24b_gt2pc",
)
# Intermediate mom lookbacks/% unused vs MOM_FILTERS + DENSE +
# EXPAND + FRESH. Not short-12. 36b–66b = 3h–5.5h on 5m.
MOM_FILTERS_HTF_INTERMEDIATE: tuple[str, ...] = (
    "mom_36b_gt8pc",
    "mom_42b_gt6pc",
    "mom_48b_gt8pc",
    "mom_54b_gt6pc",
    "mom_60b_gt4pc",
    "mom_66b_gt4pc",
)
# Short-continuation mom around the first admit (12–24b / gt2–gt4).
# near_duplicate_key: lb rounds to 6, thr to even. gt3pc→gt4pc so
# mom_18b_gt3pc is the same canon as gt4 — emit the canon only.
# Do not emit lbs that round onto parked 18b_gt2 (15/16/20).
# 12b_gt4 / 24b_gt4 collide with legacy 12b_gt3 / 24b_gt5.
# h1_ema_abv_18 → 20 (same canon as 20); still in the recipe so the
# exact 18-period name evaluates. next_refill_batch skips it when
# 20&same_entry is already taken. h1_ema_abv_36 → 35 (distinct).
# h1_sma_abv_{20,24,30} stay distinct from each other and from the
# ema twins (sma vs ema is in the key). Do not mint h1_sma_abv_18
# (18→20, same no-op as the measured ema_18 densify).
MOM_FILTERS_HTF_DENSE: tuple[str, ...] = (
    "mom_18b_gt4pc",
    "mom_18b_gt6pc",
    "mom_12b_gt6pc",
)
# Unused lookbacks / % that stay distinct from MOM_FILTERS + DENSE.
# 54/66 are unused lbs (gt1 would collapse onto gt2 — emit gt2).
# gt4 at 6/30/42 does not share canon with legacy gt3/gt5 or grind gt1.
# gt6 at 24/36 is distinct from 24b_gt5→gt4 and 36b_gt4 / 36b_gt2.
MOM_FILTERS_HTF_EXPAND: tuple[str, ...] = (
    "mom_54b_gt2pc",
    "mom_66b_gt2pc",
    "mom_6b_gt4pc",
    "mom_30b_gt4pc",
    "mom_42b_gt4pc",
    "mom_24b_gt6pc",
    "mom_36b_gt6pc",
)
# Unused lb/% vs MOM_FILTERS + DENSE + EXPAND. 78/90/96 are new lbs
# (round-to-6 identity). gt4 at 48/54/72 is distinct from existing
# 48b_gt2 / 48b_gt6 / 54b_gt2 / 72b_gt2. gt6 at 60 is distinct from
# grind 60b_gt1→gt2. gt8 at 18 is distinct from 18b gt2/gt4/gt6.
# Do not emit 84b_gt2 (grind 84b_gt1→gt2) or 16b/20b (round onto 18).
MOM_FILTERS_HTF_FRESH: tuple[str, ...] = (
    "mom_78b_gt2pc",
    "mom_90b_gt2pc",
    "mom_96b_gt2pc",
    "mom_48b_gt4pc",
    "mom_54b_gt4pc",
    "mom_72b_gt4pc",
    "mom_60b_gt6pc",
    "mom_18b_gt8pc",
)
# Regime-path mom order: winning-neighborhood first so dry refill
# emits HTF×mom_18b_gt2pc before leftover mom / dense / grind.
# Public MOM_FILTERS (leftover structure families) stay as-is.
REGIME_MOM_PRIORITY: tuple[str, ...] = (
    "mom_18b_gt2pc",
    "mom_12b_gt2pc",
    "mom_24b_gt2pc",
)
DEEP_STACK_MOM: tuple[str, ...] = REGIME_MOM_PRIORITY
REGIME_MOM_BASES_PRIOR: tuple[str, ...] = REGIME_MOM_PRIORITY + tuple(
    m
    for m in (
        MOM_FILTERS
        + MOM_FILTERS_HTF_DENSE
        + MOM_FILTERS_HTF_EXPAND
        + GRIND_FILTERS
    )
    if m not in REGIME_MOM_PRIORITY
)
REGIME_MOM_BASES: tuple[str, ...] = REGIME_MOM_PRIORITY + tuple(
    m
    for m in (
        MOM_FILTERS
        + MOM_FILTERS_HTF_DENSE
        + MOM_FILTERS_HTF_EXPAND
        + MOM_FILTERS_HTF_FRESH
        + GRIND_FILTERS
    )
    if m not in REGIME_MOM_PRIORITY
)
# Mild pullback-in-trend first on HTF×dip. lt5 is the winning 2-atom
# admit; lt6 is nearby and distinct (canon 6). lt4 shares canon with
# lt5 (banker's round 5→4) — do not emit. Public DIP_FILTERS (leftover
# structure families) stay as-is.
REGIME_DIP_PRIORITY: tuple[str, ...] = (
    "dip_24b_lt5pc",
    "dip_24b_lt6pc",
    "dip_18b_lt2pc",
)
REGIME_DIP_BASES: tuple[str, ...] = REGIME_DIP_PRIORITY + tuple(
    d for d in DIP_FILTERS if d not in REGIME_DIP_PRIORITY
)
# Existing 5m entry bases the HTF tag ANDs onto (mom-first ∪ dip).
REGIME_ENTRY_BASES: tuple[str, ...] = REGIME_MOM_BASES + REGIME_DIP_BASES
# Former light structure set for HTF 3-atoms. Not applied: HTF×mom
# and HTF×dip are 2-atom only (structure ANDs burned the farm).
REGIME_STRUCTURE_NS: tuple[int, ...] = (12, 24, 48)
REGIME_STRUCTURE_TAGS: tuple[str, ...] = ("don_hi", "near_swing_lo")
TREND_FILTERS: tuple[str, ...] = (
    "sma_abv_50",
    "sma_abv_100",
    "sma_abv_200",
    "ema_abv_50",
    "sma_stack_20_50_100",
    "rsi_14_>50",
)

# Multiples of 6 so near_duplicate_key is the identity for structure N.
# 6 bars = 30m on 5m. After 72 the step is 12 (84=7h, 96=8h).
# Capped at DISCOVERY_STRUCTURE_LOOKBACK_MAX default 96 — don_hi /
# don_lo / near_swing_* / dbl_bot (and dbl_top if present) with N>96
# are fail-parked as lookback_too_expensive. Do not mint those names.
# Distinct from a 1-bar param tweak. Dip/mom/HTF lookbacks are not
# structure atoms and are not capped here.
# Frozen 2026-09-12 morning set (through 96). Live mint uses the same
# tuple so leftover-AND generators that iterate STRUCTURE_NS stop at 96.
STRUCTURE_NS_THROUGH_96: tuple[int, ...] = (
    6, 12, 18, 24, 30, 36, 42, 48, 54, 60, 66, 72, 84, 96,
)
STRUCTURE_NS: tuple[int, ...] = STRUCTURE_NS_THROUGH_96

# Trend tags for support / near-level ANDs (not every TREND_FILTERS × atom).
# The 2026-09-12 morning pass left sma_abv_100 / 200 / rsi on don_hi only.
LEVEL_TRENDS: tuple[str, ...] = (
    "sma_abv_50",
    "ema_abv_50",
    "sma_stack_20_50_100",
)

# Parser + static universe already have these; morning recipe never ANDed
# them onto support / near-swing / don_hi (ema_abv_100) / dbl_bot extras.
SUPPORT_EXTRA_TRENDS: tuple[str, ...] = (
    "sma_abv_100",
    "sma_abv_200",
    "rsi_14_>50",
    "ema_abv_100",
)
STACK_TREND: str = "ema_stack_20_50_100"
THREE_ATOM_EXTRA_TRENDS: tuple[str, ...] = (
    "sma_abv_100",
    "sma_stack_20_50_100",
)

# Hard refuse: chart-pattern zoo, named candlesticks, MFI, WaveTrend clones, MTF.
_REFUSED_RE = re.compile(
    r"head_and_shoulders|flag|triangle|engulfing|hammer|doji|morning_star|"
    r"evening_star|mfi_|wt_|sommi|gold_dot|"
    r"dbl_top_|daily\(|h1\(|m5\(|chart_pattern|order_block|fvg_|candlestick",
    re.IGNORECASE,
)

_ALLOWED_ATOM_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^sma_stack(?:_[\d_]+)?$"),
    re.compile(r"^ema_stack(?:_[\d_]+)?$"),
    re.compile(r"^sma_abv_\d+$"),
    re.compile(r"^ema_abv_\d+$"),
    re.compile(r"^rsi_\d+_>\d+(?:_<\d+)?$"),
    re.compile(r"^mom_\d+b_gt\d+pc$"),
    re.compile(r"^dip_\d+b_lt\d+pc$"),
    re.compile(r"^don_(hi|lo)_\d+$"),
    re.compile(r"^near_swing_(hi|lo)_\d+$"),
    re.compile(r"^dbl_bot_\d+$"),
    re.compile(r"^h4_(ema|sma)_abv_\d+$"),
    re.compile(r"^h1_(ema|sma)_abv_\d+$"),
)

_STRUCTURE_PREFIXES: tuple[str, ...] = (
    "don_hi_",
    "don_lo_",
    "near_swing_hi_",
    "near_swing_lo_",
    "dbl_bot_",
)

_REGIME_PREFIXES: tuple[str, ...] = (
    "h4_ema_abv_",
    "h4_sma_abv_",
    "h1_ema_abv_",
    "h1_sma_abv_",
)


def extended_path() -> Path:
    return state_root() / DISCOVERY_EXTENDED


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_extended_names() -> list[str]:
    """Persisted refill names. Missing/corrupt → empty (static universe still runs)."""
    path = extended_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if isinstance(data, list):
        return [n for n in data if isinstance(n, str) and n.strip()]
    if isinstance(data, dict):
        raw = data.get("names") or []
        if isinstance(raw, list):
            return [n for n in raw if isinstance(n, str) and n.strip()]
    return []


def load_extended_meta() -> dict:
    path = extended_path()
    if not path.exists():
        return {"names": [], "batches_emitted": 0}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"names": [], "batches_emitted": 0}
    if isinstance(data, dict):
        names = data.get("names") or []
        if not isinstance(names, list):
            names = []
        return {
            "names": [n for n in names if isinstance(n, str) and n.strip()],
            "batches_emitted": int(data.get("batches_emitted") or 0),
            "updated_at": data.get("updated_at"),
            "source": data.get("source"),
        }
    if isinstance(data, list):
        names = [n for n in data if isinstance(n, str) and n.strip()]
        return {"names": names, "batches_emitted": 0}
    return {"names": [], "batches_emitted": 0}


def save_extended_names(
    names: list[str],
    *,
    batches_emitted: int,
    last_added: list[str] | None = None,
) -> Path:
    path = extended_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "names": names,
        "batches_emitted": batches_emitted,
        "updated_at": _now(),
        "source": "hedge_fund.trading.refill",
        "last_added": list(last_added or []),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)
    return path


def discovery_universe(extra: Iterable[str] | None = None) -> list[str]:
    """Static generate_universe() plus persisted refill names (sorted unique)."""
    names = set(generate_universe())
    if extra is None:
        extra = load_extended_names()
    names.update(n for n in extra if n and isinstance(n, str))
    return sorted(names)


def name_is_refused(name: str) -> bool:
    return bool(_REFUSED_RE.search(name))


def atom_is_allowed(atom: str) -> bool:
    return any(rx.match(atom) for rx in _ALLOWED_ATOM_RES)


def name_is_parseable(name: str) -> bool:
    """Honest parse: every atom is a known regex; no silent False fallback."""
    if not name or name_is_refused(name):
        return False
    parts = [p.strip() for p in name.split("&") if p.strip()]
    if not parts or len(parts) > RECIPE_MAX_ATOMS:
        return False
    return all(atom_is_allowed(p) for p in parts)


_MOM_GT_ATOM_RE = re.compile(r"^mom_\d+b_gt\d+pc$")
_DIP_ATOM_RE = re.compile(r"^dip_")


def name_has_mom_gt_and_dip(name: str) -> bool:
    """True when a stack ANDs a momentum-up atom with a dip atom.

    ``mom_*_gt*`` (price up) and ``dip_*`` (price down) on overlapping
    lookbacks almost never co-fire — measured trades=0 over the full
    ~5.7y tape. RSI siblings of the same spine are fine. Parser still
    accepts these names so already-queued extended rows can drain once.
    """
    has_mom_gt = False
    has_dip = False
    for tok in name.split("&"):
        tok = tok.strip()
        if not tok:
            continue
        if _MOM_GT_ATOM_RE.match(tok) or (tok.startswith("mom_") and "_gt" in tok):
            has_mom_gt = True
        elif _DIP_ATOM_RE.match(tok):
            has_dip = True
        if has_mom_gt and has_dip:
            return True
    return False


def _iter_without_mom_and_dip(names: Iterable[str]) -> Iterator[str]:
    """Central recipe emit guard: drop contradictory mom∧dip stacks."""
    for name in names:
        if not name_has_mom_gt_and_dip(name):
            yield name


def _has_structure_atom(name: str) -> bool:
    return any(
        tok.startswith(p)
        for tok in name.split("&")
        for p in _STRUCTURE_PREFIXES
    )


def _has_regime_atom(name: str) -> bool:
    return any(
        tok.startswith(p)
        for tok in name.split("&")
        for p in _REGIME_PREFIXES
    )


def _is_refillable_name(name: str) -> bool:
    """Dry refill accepts structure tags and/or HTF regime ANDs."""
    return _has_structure_atom(name) or _has_regime_atom(name)


def _legacy_structure_ands(n: int) -> Iterator[str]:
    """2026-09-11 recipe families. Frozen 3×3 dip/mom; still in the stream."""
    for dip in DIP_FILTERS_LEGACY:
        yield f"{dip}&don_lo_{n}"
        yield f"{dip}&near_swing_lo_{n}"
    for mom in MOM_FILTERS_LEGACY:
        yield f"{mom}&don_hi_{n}"
        yield f"{mom}&near_swing_hi_{n}"
    yield f"dbl_bot_{n}"
    yield f"dbl_bot_{n}&sma_abv_50"
    yield f"dbl_bot_{n}&don_lo_{n}"
    yield f"dbl_bot_{n}&sma_stack_20_50_100"
    yield f"dbl_bot_{n}&ema_abv_50"
    yield f"don_hi_{n}"
    for trend in TREND_FILTERS:
        yield f"{trend}&don_hi_{n}"
    for dip in DIP_FILTERS_LEGACY:
        yield f"{dip}&don_lo_{n}&sma_abv_50"
        yield f"{dip}&near_swing_lo_{n}&sma_abv_50"
    for mom in MOM_FILTERS_LEGACY:
        yield f"{mom}&don_hi_{n}&sma_abv_50"


def _near_level_ands(n: int) -> Iterator[str]:
    """2026-09-12: unused near-level / support / swing ANDs already in the parser.

    don_lo / near_swing_* already use the documented near-band. Standalone
    tags and trend×support were missing; mom×near_swing_hi lacked the sma/ema
    3-atoms that mom×don_hi already had. No named candlesticks.
    """
    yield f"don_lo_{n}"
    yield f"near_swing_lo_{n}"
    yield f"near_swing_hi_{n}"
    for trend in LEVEL_TRENDS:
        yield f"{trend}&don_lo_{n}"
        yield f"{trend}&near_swing_lo_{n}"
        yield f"{trend}&near_swing_hi_{n}"
    for mom in MOM_FILTERS_LEGACY:
        yield f"{mom}&near_swing_hi_{n}&sma_abv_50"
        yield f"{mom}&near_swing_hi_{n}&ema_abv_50"
        yield f"{mom}&don_hi_{n}&ema_abv_50"
    for dip in DIP_FILTERS_LEGACY:
        yield f"{dip}&don_lo_{n}&ema_abv_50"
        yield f"{dip}&near_swing_lo_{n}&ema_abv_50"
    yield f"dbl_bot_{n}&rsi_14_>50"
    yield f"dbl_bot_{n}&sma_abv_100"


def _leftover_trend_ands(n: int) -> Iterator[str]:
    """2026-09-12 later: leftover TREND / ema_stack / 3-atom ANDs.

    Parser already allows ``ema_stack_*``, ``ema_abv_100``, and the rest of
    TREND_FILTERS on support / near-swing. Morning recipe left those on
    ``don_hi`` (or skipped them). ``dbl_bot`` × ``near_swing_lo`` is the
    same fractal as the pattern atom. No named candlesticks.
    """
    for trend in SUPPORT_EXTRA_TRENDS:
        yield f"{trend}&don_lo_{n}"
        yield f"{trend}&near_swing_lo_{n}"
        yield f"{trend}&near_swing_hi_{n}"
    yield f"ema_abv_100&don_hi_{n}"
    yield f"{STACK_TREND}&don_hi_{n}"
    yield f"{STACK_TREND}&don_lo_{n}"
    yield f"{STACK_TREND}&near_swing_hi_{n}"
    yield f"{STACK_TREND}&near_swing_lo_{n}"
    yield f"dbl_bot_{n}&near_swing_lo_{n}"
    yield f"dbl_bot_{n}&sma_abv_200"
    yield f"dbl_bot_{n}&ema_abv_100"
    yield f"dbl_bot_{n}&{STACK_TREND}"
    for dip in DIP_FILTERS_LEGACY:
        for trend in THREE_ATOM_EXTRA_TRENDS:
            yield f"{dip}&don_lo_{n}&{trend}"
            yield f"{dip}&near_swing_lo_{n}&{trend}"
    for mom in MOM_FILTERS_LEGACY:
        for trend in THREE_ATOM_EXTRA_TRENDS:
            yield f"{mom}&don_hi_{n}&{trend}"
            yield f"{mom}&near_swing_hi_{n}&{trend}"


def _continuation_3atoms(n: int, filters: tuple[str, ...]) -> Iterator[str]:
    """Short MA × breakout / near-high. Parser atoms only; max 3 tokens."""
    for base in filters:
        for struct in (f"don_hi_{n}", f"near_swing_hi_{n}"):
            for ma in CONTINUATION_TRENDS:
                yield f"{base}&{struct}&{ma}"


def _grind_participation_ands(n: int) -> Iterator[str]:
    """2026-09-13: 1% grind 2-atoms at unused lookbacks. Highest participation."""
    for mom in MOM_FILTERS_GRIND:
        yield f"{mom}&don_hi_{n}"
        yield f"{mom}&near_swing_hi_{n}"
    for dip in DIP_FILTERS_GRIND:
        yield f"{dip}&don_hi_{n}"
        yield f"{dip}&near_swing_hi_{n}"


def _regime_pair_ands(
    entries: tuple[str, ...],
    regimes: tuple[str, ...] | None = None,
) -> Iterator[str]:
    for regime in regimes if regimes is not None else REGIME_ATOMS:
        for entry in entries:
            yield f"{regime}&{entry}"


def _regime_undry_winner_shaped() -> Iterator[str]:
    """Admit-island densify. Emit first on dry refill.

    Unused continuation ``sma_abv_40`` / ``ema_abv_40`` and
    ``rsi_14_>60`` on ``FRESH_STACK_REGIME`` (island first). Gap-fill
    ``sma_abv_50`` / ``ema_abv_20`` 3-atoms on paid-off 50/60 spines
    that lacked ``REGIME_CONT`` coverage. Intermediate mom (not
    short-12) on ``DEEP_STACK_REGIME`` only — 3-atom winner
    continuation before 2-atom. Spine is REGIME+MOM. Skip
    ``mom_12b_*`` on this prefix. Never AND a dip. No ``near_swing``
    / ``don_hi``.
    """
    for regime in FRESH_STACK_REGIME:
        for mom in UNDRY_MOM_PRIORITY:
            for cont in UNDRY_CONT_ATOMS:
                yield f"{regime}&{mom}&{cont}"
    for regime in FRESH_STACK_REGIME:
        for mom in UNDRY_MOM_PRIORITY:
            yield f"{regime}&{mom}&{UNDRY_RSI}"
    for regime in UNDRY_GAP_REGIME:
        for mom in UNDRY_MOM_PRIORITY:
            for cont in UNDRY_GAP_CONT:
                yield f"{regime}&{mom}&{cont}"
    for regime in FRESH_STACK_REGIME:
        for mom in UNDRY_MOM_PRIORITY:
            for cont in UNDRY_CONT_ATOMS:
                yield f"{regime}&{mom}&{cont}&{DEEP_STACK_RSI}"
    for regime in FRESH_STACK_REGIME:
        for mom in UNDRY_MOM_PRIORITY:
            for cont in UNDRY_CONT_ATOMS:
                yield f"{regime}&{mom}&{cont}&{UNDRY_RSI}"
    for regime in DEEP_STACK_REGIME:
        for mom in MOM_FILTERS_HTF_INTERMEDIATE:
            yield f"{regime}&{mom}&sma_abv_50"
            yield f"{regime}&{mom}&ema_abv_20"
    for regime in DEEP_STACK_REGIME:
        for mom in MOM_FILTERS_HTF_INTERMEDIATE:
            for cont in UNDRY_CONT_ATOMS:
                yield f"{regime}&{mom}&{cont}"
    for regime in DEEP_STACK_REGIME:
        for mom in MOM_FILTERS_HTF_INTERMEDIATE:
            yield f"{regime}&{mom}"


def _regime_fresh_winner_shaped() -> Iterator[str]:
    """Never-tested 3–4 atom winner-shaped stacks. Emit first on dry refill.

    Spine is REGIME+MOM. New continuation (``sma_abv_30`` / ``ema_abv_30``)
    and ``rsi_14_>55`` are unused canons. ``rsi_14_>50`` 3-atoms cover
    admit neighbors + dense mom that leftover depth-3 did not. 4-atoms
    are continuation × rsi. Never AND a dip onto this mom spine.
    No ``near_swing`` / ``don_hi``. Depth-7 skipped.
    """
    extra_rsi_regimes = tuple(
        r for r in FRESH_STACK_REGIME if r not in DEEP_STACK_REGIME
    )
    for regime in FRESH_STACK_REGIME:
        for mom in REGIME_MOM_PRIORITY:
            for cont in FRESH_CONT_ATOMS:
                yield f"{regime}&{mom}&{cont}"
    for regime in FRESH_STACK_REGIME:
        for mom in REGIME_MOM_PRIORITY:
            yield f"{regime}&{mom}&{FRESH_RSI}"
    for regime in extra_rsi_regimes:
        for mom in REGIME_MOM_PRIORITY:
            yield f"{regime}&{mom}&{DEEP_STACK_RSI}"
    for regime in FRESH_STACK_REGIME:
        for mom in MOM_FILTERS_HTF_DENSE:
            yield f"{regime}&{mom}&{DEEP_STACK_RSI}"
    for regime in FRESH_STACK_REGIME:
        for mom in REGIME_MOM_PRIORITY:
            for cont in FRESH_CONT_ATOMS:
                yield f"{regime}&{mom}&{cont}&{DEEP_STACK_RSI}"


def _regime_fresh_pairs() -> Iterator[str]:
    """Unused HTF × all mom/dip, plus parked HTF × unused mom. Mom first."""
    yield from _regime_pair_ands(REGIME_MOM_BASES, regimes=REGIME_ATOMS_FRESH)
    yield from _regime_pair_ands(MOM_FILTERS_HTF_FRESH, regimes=REGIME_ATOMS_PRIOR)
    yield from _regime_pair_ands(REGIME_DIP_BASES, regimes=REGIME_ATOMS_FRESH)


def _regime_winner_3atoms() -> Iterator[str]:
    """Admit-island 3-atoms: combine HTF×mom with continuation (not dip).

    Not HTF×mom×structure (burned). Not HTF×mom×dip (contradictory;
    trades=0). Parser already ANDs three atoms. ``h1_ema_abv_18``
    shares canon with 20 — exact 18 name is still emitted;
    ``next_refill_batch`` skips it when the 20 twin is taken.
    """
    for regime in REGIME_ADMIT_ATOMS:
        for mom in REGIME_MOM_PRIORITY:
            for cont in REGIME_CONT_ATOMS:
                yield f"{regime}&{mom}&{cont}"


def _and_join(*atoms: str) -> str:
    return "&".join(a for a in atoms if a)


def _iter_deep_trend_choices(n_trend: int) -> Iterator[tuple[str, ...]]:
    if n_trend == 0:
        yield ()
        return
    if n_trend == 1:
        for trend in DEEP_STACK_TREND:
            yield (trend,)
        return
    for pair in DEEP_STACK_TREND_PAIRS:
        yield pair


def _iter_deep_dip_choices(n_dip: int) -> Iterator[tuple[str, ...]]:
    if n_dip == 0:
        yield ()
        return
    for dip in REGIME_DIP_PRIORITY:
        yield (dip,)


def _iter_deep_rsi_choices(n_rsi: int) -> Iterator[tuple[str, ...]]:
    if n_rsi == 0:
        yield ()
        return
    yield (DEEP_STACK_RSI,)


def _iter_deep_struct_choices(n_struct: int) -> Iterator[tuple[str, ...]]:
    if n_struct == 0:
        yield ()
        return
    for tag in DEEP_STACK_STRUCTURE_TAGS:
        for n in DEEP_STACK_STRUCTURE_NS:
            yield (f"{tag}_{n}",)


def _deep_stacks_at_depth(depth: int) -> Iterator[str]:
    """Role-bucket stacks of exact ``depth``. Spine is REGIME+MOM.

    At most one atom per role except continuation (0–2). Never a dip
    on the REGIME+MOM spine. Structure only at depth 7 (cheap
    ``near_swing_hi``, N≤48) — that slot previously required a dip
    extra and therefore yields nothing. Not a cartesian of every
    parser atom.
    """
    extras = depth - 2
    winner_3_extras = frozenset(REGIME_CONT_ATOMS)
    for n_trend in range(3):
        for n_dip in (0,):
            for n_rsi in (0, 1):
                for n_struct in (0, 1):
                    if n_trend + n_dip + n_rsi + n_struct != extras:
                        continue
                    if n_struct and depth != 7:
                        continue
                    if n_struct and not (n_trend == 2 and n_dip == 1 and n_rsi == 1):
                        continue
                    for regime in DEEP_STACK_REGIME:
                        for mom in DEEP_STACK_MOM:
                            for trends in _iter_deep_trend_choices(n_trend):
                                for dips in _iter_deep_dip_choices(n_dip):
                                    for rsis in _iter_deep_rsi_choices(n_rsi):
                                        for structs in _iter_deep_struct_choices(n_struct):
                                            extras_atoms = trends + dips + rsis + structs
                                            if (
                                                depth == 3
                                                and len(extras_atoms) == 1
                                                and extras_atoms[0] in winner_3_extras
                                            ):
                                                continue
                                            yield _and_join(
                                                regime, mom, *trends, *dips, *rsis, *structs
                                            )


def _regime_deep_stacks() -> Iterator[str]:
    """Admit-island stacks. Depth 4–5 first, then leftover 3, then 6–7.

    Builds on winning HTF×mom×continuation / RSI. Never AND a dip onto
    the REGIME+MOM spine. Skip names already emit-equivalent via
    ``near_duplicate_key``. Prefer trend / RSI over Donchian. Cheap
    ``near_swing_hi`` N≤48 only at depth 7 was dip-gated and is not
    minted. No ``don_hi`` / ``near_swing_lo`` / N>48.
    """
    seen: set[str] = set()
    for depth in (4, 5, 3, 6, 7):
        for name in _deep_stacks_at_depth(depth):
            parts = [p for p in name.split("&") if p]
            if not (3 <= len(parts) <= RECIPE_MAX_ATOMS):
                continue
            key = near_duplicate_key(name)
            if key in seen:
                continue
            seen.add(key)
            yield name


def _regime_ands() -> Iterator[str]:
    """2026-09-13: HTF buyer-regime AND existing 5m DIP/MOM/WIDE/GRIND.

    Same-date later: denser HTF periods + short-continuation mom.
    Same-date later: no structure AND on HTF×mom / HTF×dip.
    Same-date later: ``h1_ema_abv_18`` / ``36`` and
    ``mom_18b_gt2pc`` first among regime mom bases. Same-date later:
    ``h1_sma_abv_{20,24,30}`` twins and ``REGIME_DIP_PRIORITY``
    (``dip_24b_lt5pc`` / ``dip_24b_lt6pc`` / ``dip_18b_lt2pc``)
    first among regime dip bases. 2026-09-14: winner 3-atoms first
    (``regime&mom&continuation``), then role-bucket stacks (depth 4–5
    before leftover 3), then ``regime&mom`` then ``regime&dip``.
    Same-date later: **fresh** winner-shaped continuation / rsi and
    unused HTF/mom 2-atoms emit before that drained prefix so dry
    refill is not idle.     Same-date later: never AND ``mom_*_gt*`` with
    ``dip_*`` — HTF×dip without mom stays. Same-date later: **undry**
    admit-island densify (``sma_abv_40`` / ``ema_abv_40``,
    ``rsi_14_>60``, intermediate mom, gap-fill 50/60 continuation,
    ``h1_sma_abv_70``) emits before the drained fresh-30 / rsi-55
    prefix so dry refill is not idle. No ``don_hi`` /
    ``near_swing_lo``. Cheap ``near_swing_hi`` N≤48 only on depth-7
    stacks (not minted without a dip extra). HTF False → no new long
    (flat). No named candlesticks.
    """
    yield from _iter_without_mom_and_dip(_regime_ands_raw())


def _regime_ands_raw() -> Iterator[str]:
    yield from _regime_undry_winner_shaped()
    yield from _regime_fresh_winner_shaped()
    yield from _regime_fresh_pairs()
    yield from _regime_winner_3atoms()
    yield from _regime_deep_stacks()
    yield from _regime_pair_ands(REGIME_MOM_BASES_PRIOR, regimes=REGIME_ATOMS_PRIOR)
    yield from _regime_pair_ands(REGIME_DIP_BASES, regimes=REGIME_ATOMS_PRIOR)


def _trend_participation_ands(n: int) -> Iterator[str]:
    """2026-09-12 later + 2026-09-13 full short-MA 3-atoms.

    Prod 2026-09-13: PR #42 WIDE is on the farm; still 0 names beat B&H
    (bh_oos ≈ 192). Sharpe+trades near-misses exist but print negative
    vs B&H. Next lever is shallower 1% grind + more time-in-market
    3-atoms (parser-allowed atoms only). Gate constants stay frozen.

    Wide mom / shallow dip × continuation stay. Short MA × breakout
    participates more than sma_abv_200. Every WIDE mom/dip now gets
    both ``sma_abv_20`` and ``ema_abv_20`` on ``don_hi`` /
    ``near_swing_hi`` (was gt2pc/lt2pc × don_hi × sma_abv_20 only).
    Parser atoms only. No named candlesticks.
    """
    for mom in MOM_FILTERS_WIDE:
        yield f"{mom}&don_hi_{n}"
        yield f"{mom}&near_swing_hi_{n}"
    for dip in DIP_FILTERS_WIDE:
        yield f"{dip}&don_hi_{n}"
        yield f"{dip}&near_swing_hi_{n}"
    for trend in CONTINUATION_TRENDS:
        yield f"{trend}&don_hi_{n}"
        yield f"{trend}&near_swing_hi_{n}"
    yield from _continuation_3atoms(n, MOM_FILTERS_WIDE)
    yield from _continuation_3atoms(n, DIP_FILTERS_WIDE)


def iter_recipe_names() -> Iterator[str]:
    """Deterministic bounded stream. Not a full cartesian of every atom.

    Fresh never-tested families are first so a dry refill mints unused
    admit-island densify (``sma_abv_40`` / ``ema_abv_40`` continuation,
    ``rsi_14_>60``, intermediate mom on paid-off spines, gap-fill
    ``sma_abv_50`` / ``ema_abv_20`` on 50/60 HTF) immediately. Then
    drained winner-shaped stacks (``sma_abv_30`` / ``ema_abv_30``,
    ``rsi_14_>55``, extra-regime ``rsi_14_>50``, continuation × rsi).
    Then admit-island ``regime&mom&continuation`` 3-atoms, then
    role-bucket stacks (depth 4–5 continuation / RSI; never mom∧dip),
    then ``regime&mom`` 2-atoms, then HTF×dip (no mom_gt). No HTF×mom×expensive
    structure (``don_hi`` / ``near_swing_lo`` / N>48). Cheap
    ``near_swing_hi`` N≤48 only at depth 7 is not minted without a
    dip extra. Grind 1% 2-atoms and wide / short-MA continuation
    follow, then legacy 2026-09-11 families, the 2026-09-12
    near-level pass, then leftover TREND / ema_stack / 3-atom
    families. Dip×support and short-horizon mom stay on the frozen
    3×3. Central guard drops any ``mom_*_gt*`` ∧ ``dip_*`` stack.
    No WaveTrend, no MFI.
    """
    yield from _iter_without_mom_and_dip(_iter_recipe_families())


def _iter_recipe_families() -> Iterator[str]:
    yield from _regime_ands()
    for n in STRUCTURE_NS:
        yield from _grind_participation_ands(n)
    for n in STRUCTURE_NS:
        yield from _trend_participation_ands(n)
    for n in STRUCTURE_NS:
        yield from _legacy_structure_ands(n)
    for n in STRUCTURE_NS:
        yield from _near_level_ands(n)
    for n in STRUCTURE_NS:
        yield from _leftover_trend_ands(n)


def next_refill_batch(
    *,
    taken_names: Iterable[str],
    n: int = DISCOVERY_REFILL_BATCH_SIZE,
) -> list[str]:
    """Next parseable, non-near-duplicate, never-logged names from the recipe."""
    want = max(0, int(n))
    if want == 0:
        return []
    taken = {name for name in taken_names if name}
    taken_keys = {near_duplicate_key(name) for name in taken}
    out: list[str] = []
    for cand in iter_recipe_names():
        if len(out) >= want:
            break
        if not name_is_parseable(cand):
            continue
        if name_has_mom_gt_and_dip(cand):
            continue
        if not _is_refillable_name(cand):
            continue
        if cand in taken:
            continue
        key = near_duplicate_key(cand)
        if key in taken_keys:
            continue
        out.append(cand)
        taken.add(cand)
        taken_keys.add(key)
    return out


def append_extended_batch(added: list[str]) -> list[str]:
    """Persist ``added`` onto the sidecar. Returns the full extended name list."""
    if not added:
        return load_extended_names()
    meta = load_extended_meta()
    names = list(meta["names"])
    seen = set(names)
    for name in added:
        if name not in seen:
            names.append(name)
            seen.add(name)
    save_extended_names(
        names,
        batches_emitted=int(meta.get("batches_emitted") or 0) + 1,
        last_added=added,
    )
    return names


def maybe_refill_discovery(
    *,
    eligible_count: int,
    cap: int,
    taken_names: Iterable[str],
    batch_size: int = DISCOVERY_REFILL_BATCH_SIZE,
) -> list[str]:
    """If eligible is empty or about to be (< cap), append the next recipe batch.

    Returns newly added names (possibly empty when the recipe is exhausted
    or eligible already feeds this cycle).
    """
    if int(eligible_count) >= int(cap):
        return []
    taken = set(taken_names)
    taken.update(load_extended_names())
    taken.update(generate_universe())
    added = next_refill_batch(taken_names=taken, n=batch_size)
    if added:
        append_extended_batch(added)
    return added
