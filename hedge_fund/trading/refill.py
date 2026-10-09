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
spines emit **first**. 2026-10-02: that ``sma_abv_40`` / ``rsi_14_>60``
neighborhood drained with +0 admits. Next prefix is h4 twins of the
paying ``h1_* & mom_18b_gt2pc & *_abv_30 & rsi_14_>50`` shape, plus
sparse h4×mom×``*_abv_30`` and under-emitted 5–7 atom stacks (dual
h1+h4, both mild MAs). Not another ``*_abv_40`` / ``rsi_>60`` lead.
Still no named candlesticks. OOS gates unchanged.
2026-10-07: densify #2b (h4 twins of that shape) measured FAIL
(Sharpe about −0.56 to −1.04, all negative while B&H was up).
#2c leads with h1 stacks on the paying ``mom_18b_gt2pc`` island
(``rsi_14_>45``, gap MAs 35/25/15/60, 5-atom extensions, modest
mom neighbors). The burned h4×mom family stays in the stream
after that prefix. HTF periods 15/18/36/40/48/72 are already
minted or collapse under ``_round_period``. OOS gates unchanged.
2026-10-08: farm dry at 15,568 tested; no >=30-trade name beat B&H on
the 23-window gate and the h1×mom densify is burned. Literature undry
#3 (``LITDIP_*``) leads: slow h4 trend gate (25–75 day SMA/EMA) AND an
8h–60h capitulation dip, two atoms, no mom. ~2.5k names. OOS gates
unchanged.
2026-10-09: #3 drained (~2.5k cell×gate names, 0 new OOS passes).
Densify #4 (``DSHARP_*``) leads. It does not re-mint those cells.
The current-gate near-misses are 2-atom ``dip×h4_ema`` with net
OOS P&L > 0 and trades >= 30 that miss beat-B&H daily Sharpe.
A 5m MA / RSI-above confirm on that h4 spine is ``filler_atom``
and is not minted. The new names are the untested one-axis
neighborhood: ema periods ``_round_period`` does not collapse onto
the #3 gates, and step-6 lookbacks the #3 12-bar grid skipped.
OOS gates unchanged.
2026-10-09 later: #4 drained (eligible 0). Two current-gate passes,
both ``dip_{204,222}b_lt8pc&h4_ema_abv_150`` (the ``ema_abv_160``
twin of 222b also passed). Densify #5 (``DENSE5_*``) leads: the
whole dip 186–246 (step 6) × h4 period 120–170 (step 10, the
``_round_period`` resolution) × 6/8/10% × ema/sma box around that
spine, ema 8% first, plus a ~20% exploratory share on two
current-gate near-miss families (trend-stack × swing-low support,
long 12% dip × slow h4 ema). OOS gates unchanged.
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
# 2026-10-02 admit-island densify #2b. #65 ``*_abv_40`` / ``rsi_14_>60``
# drained with +0 OOS admits. Paying shape already tested on h1 is
# ``h1_* & mom_18b_gt2pc & *_abv_30 & rsi_14_>50``. h4 regimes were
# 2-atom only — mint those twins and under-emitted 4–7 stacks.
# Periods already live in REGIME_ATOMS (no new HTF 2-atom cartesian).
# Skip ``h4_*_abv_40`` (burned neighborhood) and ``h4_ema_abv_50``
# (48→50, same canon as ``h4_ema_abv_48``). No mom_12. No mom∧dip.
# No structure. ``rsi_14_>50`` / ``*_abv_30`` stay the confirmation
# (not rsi_>60 / abv_40).
ISLAND2B_H4_CORE: tuple[str, ...] = (
    "h4_ema_abv_20",
    "h4_ema_abv_24",
    "h4_ema_abv_30",
    "h4_sma_abv_20",
    "h4_sma_abv_24",
    "h4_sma_abv_30",
)
ISLAND2B_H4_NEIGHBOR: tuple[str, ...] = (
    "h4_ema_abv_15",
    "h4_ema_abv_36",
    "h4_sma_abv_15",
    "h4_sma_abv_36",
    "h4_ema_abv_48",
    "h4_sma_abv_50",
    "h4_ema_abv_60",
)
ISLAND2B_H4: tuple[str, ...] = ISLAND2B_H4_CORE + ISLAND2B_H4_NEIGHBOR
ISLAND2B_MOM: tuple[str, ...] = (
    "mom_18b_gt2pc",
    "mom_24b_gt2pc",
)
ISLAND2B_CONT: tuple[str, ...] = (
    "sma_abv_30",
    "ema_abv_30",
)
ISLAND2B_RSI: str = "rsi_14_>50"
# Same-kind twins. ``h1_ema_abv_50`` pairs with ``h4_ema_abv_48``
# (48→50). Keys stay distinct (h1 vs h4). No period-40 pairs.
ISLAND2B_TF_PAIRS_CORE: tuple[tuple[str, str], ...] = (
    ("h1_ema_abv_20", "h4_ema_abv_20"),
    ("h1_ema_abv_24", "h4_ema_abv_24"),
    ("h1_ema_abv_30", "h4_ema_abv_30"),
    ("h1_sma_abv_20", "h4_sma_abv_20"),
    ("h1_sma_abv_24", "h4_sma_abv_24"),
    ("h1_sma_abv_30", "h4_sma_abv_30"),
)
ISLAND2B_TF_PAIRS_PAID: tuple[tuple[str, str], ...] = (
    ("h1_ema_abv_50", "h4_ema_abv_48"),
    ("h1_sma_abv_50", "h4_sma_abv_50"),
    ("h1_ema_abv_60", "h4_ema_abv_60"),
)
ISLAND2B_TF_PAIRS: tuple[tuple[str, str], ...] = (
    ISLAND2B_TF_PAIRS_CORE + ISLAND2B_TF_PAIRS_PAID
)
# Paid-off h1 spines. Deeper 5–7 use both mild MAs, not abv_40.
ISLAND2B_H1_SPINE: tuple[str, ...] = DEEP_STACK_REGIME
# 2026-10-07 admit-island densify #2c. #66 / island #2b h4×mom
# measured FAIL (all of today's h4×``*_abv_30`` evals, Sharpe
# negative). Do not lead with those twins. HTF period gaps in the
# prompt (15/18/36/40/48/72) are already in ``REGIME_ATOMS`` or
# collapse: 18→20, 48→50, 72→70. ``rsi_14_>52``→50 and
# ``rsi_14_>58``→60 are not new (``rsi_14_>60`` drained with +0
# admits). ``vol_lowsm_*`` parses but is not in ``_ALLOWED_ATOM_RES``
# — not minted. Lead is h1 only, one new axis at a time, on the
# paying ``mom_18b_gt2pc`` spines (admit frequency, newest 4-atoms
# first). No mom_12. No mom∧dip. No structure. No ``*_abv_40``.
ISLAND2C_H1_SPINE: tuple[str, ...] = (
    "h1_ema_abv_50",
    "h1_ema_abv_30",
    "h1_sma_abv_30",
    "h1_sma_abv_24",
    "h1_ema_abv_24",
    "h1_ema_abv_20",
    "h1_sma_abv_50",
    "h1_sma_abv_60",
    "h1_ema_abv_60",
)
ISLAND2C_MOM: str = "mom_18b_gt2pc"
# Newest measured passers use ``*_abv_30`` then 20 then 50.
ISLAND2C_CONT_PAID: tuple[str, ...] = (
    "sma_abv_30",
    "ema_abv_30",
    "sma_abv_20",
    "ema_abv_20",
    "sma_abv_50",
    "ema_abv_50",
)
# 45→45. Distinct from drained >50 / >55 / >60.
ISLAND2C_RSI: str = "rsi_14_>45"
# 35→35 sits between paying 30 and failed 40. 25→25 between 20 and
# 30. 15→15 and 60→60 are the next distinct canons. Skip 45 (→40)
# and 55 (→60).
ISLAND2C_CONT_GAP: tuple[str, ...] = (
    "sma_abv_35",
    "ema_abv_35",
    "sma_abv_25",
    "ema_abv_25",
    "sma_abv_15",
    "ema_abv_15",
    "sma_abv_60",
    "ema_abv_60",
)
# One extra paying MA on ``…&sma_abv_30&rsi_14_>50``. Not
# ``ema_abv_30`` — that twin is island #2b's h1 5-atom.
ISLAND2C_EXTRA_ON_SMA30: tuple[str, ...] = (
    "ema_abv_20",
    "sma_abv_20",
    "ema_abv_50",
    "sma_abv_50",
)
# Same idea on the ema_30 twin. Not ``sma_abv_30`` (island #2b).
ISLAND2C_EXTRA_ON_EMA30: tuple[str, ...] = (
    "sma_abv_20",
    "ema_abv_20",
    "sma_abv_50",
    "ema_abv_50",
)
# Modest mom neighbors of ``mom_18b_gt2pc``. Not short-12.
# gt3→gt4, so gt4 is the next even canon. ``mom_30b_gt6pc`` and
# ``mom_24b_gt8pc`` are unused canons (30 already has gt2/gt4;
# 24 already has gt2/gt4/gt6). Stacked only on ``*_abv_30``.
ISLAND2C_MOM_NEIGHBOR: tuple[str, ...] = (
    "mom_18b_gt4pc",
    "mom_24b_gt4pc",
    "mom_18b_gt6pc",
    "mom_30b_gt6pc",
    "mom_24b_gt8pc",
)
# 2026-10-08 literature undry #3: slow trend gate × capitulation dip.
# Farm dry at 15,568 tested / 0 admits on the 23-window gate. No name with
# >=30 trades beats B&H; the only net-positive >=30-trade rows are banned
# h4×mom. The h1 ``*_abv_50/60`` × ``mom_*b_gt4/6pc`` densify is burned
# (negative Sharpe, lost to B&H). This family is not h1×mom and has no
# mom atom at all (no mom∧dip, no h4×mom).
#
# Gate (Detzel et al. 2021 Financial Management 50(1) 107–137: price/MA
# ratios over daily-scale MAs forecast Bitcoin returns and beat B&H;
# Brock, Lakonishok & LeBaron 1992 JF long-MA rules): completed-4h close
# above a 25–75 day SMA/EMA (150–450 h4 bars). The OOS cut sits ~77 days
# into each window's prefix, so 450 h4 bars are seeded. h1 twins
# (``h1_*_abv_1200`` ≈ ``h4_*_abv_300``) evaluate the same and are skipped.
#
# Entry (Wen, Bouri, Xu & Zhao 2022 NAJEF 62 101733: intraday reversal in
# BTC/ETH from overreaction; Caporale & Plastun 2019 JES 46(5): overreaction
# counter-moves alone do not pay after costs, hence the trend gate):
# 8h–60h drop of 6–18%. Cells keep the unconditional dip frequency in a
# 0.6–3.5% band (≥0.85% past 300 bars, ≥1.1% past 500) so most names land
# at 30–300 OOS trades instead of the ~1,400 median. Even thresholds and
# 12-bar lookback steps stay distinct under ``near_duplicate_key``.
# Thresholds sit under the 5m tape's 99th-percentile move.
# Ordered by local-screen EV: seeds that printed net P&L > 0 with >=30
# trades first, then the band by distance from ~1% frequency.
LITDIP_GATE_PERIODS: tuple[int, ...] = (
    300, 360, 330, 270, 240, 390, 420, 210, 180, 450, 150,
)
LITDIP_GATES: tuple[str, ...] = tuple(
    f"h4_{kind}_abv_{period}"
    for period in LITDIP_GATE_PERIODS
    for kind in ("sma", "ema")
)
# (lookback bars, drop %). First six are measured seeds.
LITDIP_CELLS: tuple[tuple[int, int], ...] = (
    (144, 8), (576, 10), (720, 10), (288, 10), (120, 8), (96, 8),
    (312, 10), (468, 12), (192, 8), (480, 12), (96, 6), (324, 10),
    (456, 12), (492, 12), (300, 10), (180, 8), (444, 12), (204, 8),
    (336, 10), (696, 14), (108, 6), (516, 12), (708, 14), (348, 10),
    (720, 14), (432, 12), (528, 12), (168, 8), (276, 10), (216, 8),
    (360, 10), (540, 12), (264, 10), (372, 10), (552, 12), (120, 6),
    (564, 12), (228, 8), (384, 10), (156, 8), (576, 12), (252, 10),
    (396, 10), (588, 12), (240, 8), (240, 10), (600, 12), (408, 10),
    (612, 12), (420, 10), (132, 6), (624, 12), (252, 8), (432, 10),
    (636, 12), (228, 10), (648, 12), (264, 8), (444, 10), (660, 12),
    (216, 10), (456, 10), (144, 6), (672, 12), (276, 8), (468, 10),
    (684, 12), (696, 12), (480, 10), (708, 12), (288, 8), (492, 10),
    (156, 6), (720, 12), (300, 8), (504, 10), (516, 10), (312, 8),
    (168, 6), (528, 10), (324, 8), (540, 10), (552, 10), (180, 6),
    (336, 8), (564, 10), (348, 8), (360, 8), (192, 6), (588, 10),
    (372, 8), (600, 10), (612, 10), (384, 8), (204, 6), (624, 10),
    (396, 8), (636, 10), (216, 6), (648, 10), (408, 8), (660, 10),
    (420, 8), (672, 10), (228, 6), (432, 8), (684, 10), (696, 10),
    (444, 8), (240, 6), (708, 10), (456, 8), (252, 6), (468, 8),
    (480, 8), (264, 6),
)
# Gate tiers: 300/360 first, then 330/270/240, then the rest.
LITDIP_GATE_TIERS: tuple[tuple[str, ...], ...] = (
    LITDIP_GATES[:4],
    LITDIP_GATES[4:10],
    LITDIP_GATES[10:],
)
# 2026-10-09 densify #4. #3 drained with 0 admits. These four 2-atom
# names cleared net OOS P&L > 0, trades >= 30, and 23 windows on tag
# sltp_cap100_bhdsr_tiled87, and missed beat-B&H daily Sharpe
# (bh_daily_sharpe 0.415) rather than the trade floor:
#   dip_300b_lt8pc&h4_ema_abv_180  +1551 / 161 tr / daily Sharpe 0.388
#   dip_264b_lt8pc&h4_ema_abv_150  +1508 / 127 tr / daily Sharpe 0.370
#   dip_216b_lt8pc&h4_ema_abv_180   +894 / 107 tr / daily Sharpe 0.251
#   dip_264b_lt8pc&h4_ema_abv_180   +885 / 137 tr / daily Sharpe 0.215
# (lookback, drop %, seed gate). Best daily Sharpe first.
DSHARP_SEEDS: tuple[tuple[int, int, int], ...] = (
    (300, 8, 180),
    (264, 8, 150),
    (216, 8, 180),
    (264, 8, 180),
)
# Periods whose ``_round_period`` is the identity and is not a
# LITDIP gate. 185→180 and 195→200, so those spellings are not
# emitted. 7% and 9% dips round onto 8% and are not emitted either.
# Close to the seed gates (150/180) first; a tie prefers the longer
# gate (lower turnover). Then the unused band around 300.
DSHARP_PERIODS_NEAR_180: tuple[int, ...] = (190, 170, 200, 160)
DSHARP_PERIODS_NEAR_150: tuple[int, ...] = (160, 170, 190, 200)
DSHARP_PERIODS_NEAR_300: tuple[int, ...] = (310, 290, 320, 280)
DSHARP_NEW_PERIODS: tuple[int, ...] = DSHARP_PERIODS_NEAR_180 + DSHARP_PERIODS_NEAR_300
# Step-6 lookbacks (near-duplicate identity) that LITDIP's 12-bar
# grid never crossed. Longer first: a rarer dip, fewer trades.
DSHARP_LB_NEIGHBORS: dict[int, tuple[int, ...]] = {
    300: (306, 294),
    264: (270, 258),
    216: (222, 210),
}
DSHARP_GAP_LOOKBACKS: tuple[int, ...] = (318, 306, 294, 282, 270, 258, 222, 210)
# In-grid (step 12) lookbacks on the same cluster, already crossed
# with every LITDIP gate at 8%. Not the measured seed dips. Crossing
# them with DSHARP_NEW_PERIODS is one axis (the gate period).
DSHARP_CLUSTER_LOOKBACKS: tuple[int, ...] = (288, 312, 276, 324, 252, 240, 228)
DSHARP_SEED_GATES: tuple[int, ...] = (180, 150)
# 2026-10-09 densify #5. Current-gate passes (tag
# sltp_cap100_bhdsr_tiled87_20261008, 23 tiled windows):
#   dip_204b_lt8pc&h4_ema_abv_150  102 tr / gate Sharpe 0.40 / daily 0.504
#   dip_222b_lt8pc&h4_ema_abv_150  101 tr / gate Sharpe 0.42 / daily 0.500
#   dip_222b_lt8pc&h4_ema_abv_160  (same trades/P&L as the 150 gate)
# #4 axis read (673 current-gate dip×h4 evals): ema beats sma (113 vs 4
# net-positive >=30-trade names); 8% > 10% > 6%; gate 160–190 has the
# best median daily Sharpe; dip 216–240 is where gate Sharpe stays >0.
# Box: every dip lookback 186–246 (step 6, ``near_duplicate_key``'s
# resolution) × h4 period 120–170 (step 10; 125/135… collapse) ×
# 6/8/10% (7/9 collapse onto 8) × ema/sma. Cells already minted by
# #3/#4 or older families are skipped by key. Nearest to (222, 155)
# first; a tie prefers the longer dip and slower gate.
DENSE5_CENTER: tuple[int, int] = (222, 155)
DENSE5_LOOKBACKS: tuple[int, ...] = (222, 216, 228, 210, 234, 204, 240, 198, 246, 192, 186)
DENSE5_PERIODS: tuple[int, ...] = (150, 160, 140, 170, 130, 120)
# (kind, drop %) stages. ema 8% (the pass cell) first, ema 10% next
# (second-best threshold on #4), then the exploratory share, then the
# weaker ema 6% and the sma twins.
DENSE5_SPINE_HEAD: tuple[tuple[str, int], ...] = (("ema", 8), ("ema", 10))
DENSE5_SPINE_TAIL: tuple[tuple[str, int], ...] = (
    ("ema", 6),
    ("sma", 8),
    ("sma", 10),
    ("sma", 6),
)
# Exploratory A: trend stack × swing-low support. rules-v3 requalify
# near-miss ``sma_stack_20_50_100&near_swing_lo_54`` (145 tr, daily
# Sharpe 0.609 vs B&H 0.415, +2,741; gate Sharpe -0.07). Brock,
# Lakonishok & LeBaron (1992, JF) trading-range support rules. N<=96;
# its 42–72 neighbours were tested under older rules, so only 78/90,
# other stack spellings, and an h4 ema 150/160 trend gate (a stack is
# not ``filler_atom``) are new. Canonical atom order.
DENSE5_STACK_SUPPORT: tuple[str, ...] = (
    "near_swing_lo_78&sma_stack_20_50_100",
    "near_swing_lo_90&sma_stack_20_50_100",
    "ema_stack_20_50_100&near_swing_lo_78",
    "ema_stack_20_50_100&near_swing_lo_90",
    "h4_ema_abv_150&near_swing_lo_54&sma_stack_20_50_100",
    "h4_ema_abv_160&near_swing_lo_54&sma_stack_20_50_100",
    "h4_ema_abv_150&near_swing_lo_60&sma_stack_20_50_100",
    "h4_ema_abv_160&near_swing_lo_60&sma_stack_20_50_100",
    "h4_ema_abv_150&near_swing_lo_48&sma_stack_20_50_100",
    "h4_ema_abv_160&near_swing_lo_48&sma_stack_20_50_100",
    "ema_stack_20_50_100&h4_ema_abv_150&near_swing_lo_54",
    "ema_stack_20_50_100&h4_ema_abv_160&near_swing_lo_54",
    "ema_stack_20_50_100&h4_ema_abv_150&near_swing_lo_60",
    "ema_stack_20_50_100&h4_ema_abv_160&near_swing_lo_60",
    "ema_stack_20_50_100&h4_ema_abv_150&near_swing_lo_48",
    "ema_stack_20_50_100&h4_ema_abv_160&near_swing_lo_48",
)
DENSE5_STACK_TWINS: tuple[str, ...] = (
    "ema_stack_13_34_89",
    "sma_stack_13_34_89",
    "sma_stack_20_50_200",
    "sma_stack_10_20_50",
)
DENSE5_SUPPORT_NS: tuple[int, ...] = (54, 60, 48, 66, 42)
# Exploratory B: long 12% (and 10%) dip × slow h4 ema. Near-misses
# dip_456b_lt12pc&h4_ema_abv_450 (116 tr, gate Sharpe 0.30, daily
# 0.344), dip_348b_lt10pc&h4_ema_abv_450 (daily 0.317) and the sma
# twin dip_444b_lt12pc&h4_sma_abv_420 (daily 0.379). Same #79
# literature as LITDIP; one axis at a time off those seeds.
DENSE5_LONG_DIPS: tuple[tuple[tuple[int, ...], int, str, tuple[int, ...]], ...] = (
    ((456, 450, 462, 444, 468, 438, 474), 12, "ema", (450, 440, 460, 430, 470)),
    ((348, 342, 354, 336, 360), 10, "ema", (440, 460, 470)),
    ((444, 450, 456), 12, "sma", (410, 420, 430, 440)),
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
    re.compile(r"^rsi_\d+_<\d+$"),
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


def _regime_island_2b(blocked_keys: set[str]) -> Iterator[str]:
    """h4 twins and deeper stacks around the paying admit island.

    Emit first on dry refill. The h1 4-atom
    ``regime & mom_18b_gt2pc & *_abv_30 & rsi_14_>50`` is already in
    the drained stream. This prefix is the h4 twin of that shape,
    sparse h4×mom×``*_abv_30``, then 5–7 atom stacks (both mild MAs,
    optional h1+h4). ``blocked_keys`` are near-duplicate keys of the
    already-emitted recipe. No ``*_abv_40``, no ``rsi_14_>60``, no
    ``mom_12b_*``, no dip, no structure.
    """
    seen = set(blocked_keys)

    def emit(name: str) -> Iterator[str]:
        key = near_duplicate_key(name)
        if key in seen:
            return
        seen.add(key)
        yield name

    def h4_shaped(regimes: tuple[str, ...], moms: tuple[str, ...]) -> Iterator[str]:
        for regime in regimes:
            for mom in moms:
                for cont in ISLAND2B_CONT:
                    yield from emit(f"{regime}&{mom}&{cont}&{ISLAND2B_RSI}")
        for regime in regimes:
            for mom in moms:
                for cont in ISLAND2B_CONT:
                    yield from emit(f"{regime}&{mom}&{cont}")
        for regime in regimes:
            for mom in moms:
                yield from emit(
                    f"{regime}&{mom}&sma_abv_30&ema_abv_30&{ISLAND2B_RSI}"
                )

    yield from h4_shaped(ISLAND2B_H4_CORE, ISLAND2B_MOM)
    yield from h4_shaped(ISLAND2B_H4_NEIGHBOR, ("mom_18b_gt2pc",))
    mom = "mom_18b_gt2pc"
    for h1, h4 in ISLAND2B_TF_PAIRS:
        yield from emit(f"{h1}&{h4}&{mom}&sma_abv_30&{ISLAND2B_RSI}")
        yield from emit(f"{h1}&{h4}&{mom}&ema_abv_30&{ISLAND2B_RSI}")
    for h1, h4 in ISLAND2B_TF_PAIRS:
        yield from emit(f"{h1}&{h4}&{mom}&sma_abv_30&ema_abv_30&{ISLAND2B_RSI}")
    for h1, h4 in ISLAND2B_TF_PAIRS:
        yield from emit(
            f"{h1}&{h4}&{mom}&sma_abv_30&ema_abv_30&sma_abv_20&{ISLAND2B_RSI}"
        )
    for regime in ISLAND2B_H1_SPINE:
        yield from emit(f"{regime}&{mom}&sma_abv_30&ema_abv_30&{ISLAND2B_RSI}")
    for regime in ISLAND2B_H1_SPINE:
        yield from emit(
            f"{regime}&{mom}&sma_abv_30&ema_abv_30&sma_abv_20&{ISLAND2B_RSI}"
        )
    for regime in ISLAND2B_H1_SPINE:
        yield from emit(
            f"{regime}&{mom}&sma_abv_30&ema_abv_30&sma_abv_20&ema_abv_20&{ISLAND2B_RSI}"
        )


def _regime_island_2c(blocked_keys: set[str]) -> Iterator[str]:
    """h1 densify around the paying admit island. Emit first.

    One new axis at a time, highest-EV first. Spine is h1 and
    ``mom_18b_gt2pc`` (110/111 h1 admits). Then:

    1. ``rsi_14_>45`` on paying continuation (4-atom, then 3-atom).
       ``rsi_14_>52`` / ``>58`` are not minted (canon 50 / 60).
    2. Gap MAs ``sma/ema_abv_{35,25,15,60}`` as 3-atoms, then the
       same MA with paying ``rsi_14_>50``.
    3. One extra paying MA on the newest 4-atom
       ``…&*_abv_30&rsi_14_>50`` (not the same-period twin).
    4. Modest mom neighbors on ``*_abv_30`` only (3-atom, then
       × ``rsi_14_>50``). Not ``mom_12b_*``.

    ``blocked_keys`` are near-duplicate keys of island #2b and the
    drained recipe. No h4 lead. No ``*_abv_40``. No dip. No structure.
    """
    seen = set(blocked_keys)

    def emit(name: str) -> Iterator[str]:
        if name_has_mom_gt_and_dip(name) or not name_is_parseable(name):
            return
        key = near_duplicate_key(name)
        if key in seen:
            return
        seen.add(key)
        yield name

    mom = ISLAND2C_MOM
    rsi_new = ISLAND2C_RSI
    rsi_paid = DEEP_STACK_RSI
    for cont in ISLAND2C_CONT_PAID:
        for regime in ISLAND2C_H1_SPINE:
            yield from emit(f"{regime}&{mom}&{cont}&{rsi_new}")
    for regime in ISLAND2C_H1_SPINE:
        yield from emit(f"{regime}&{mom}&{rsi_new}")
    for cont in ISLAND2C_CONT_GAP:
        for regime in ISLAND2C_H1_SPINE:
            yield from emit(f"{regime}&{mom}&{cont}")
    for cont in ISLAND2C_CONT_GAP:
        for regime in ISLAND2C_H1_SPINE:
            yield from emit(f"{regime}&{mom}&{cont}&{rsi_paid}")
    for extra in ISLAND2C_EXTRA_ON_SMA30:
        for regime in ISLAND2C_H1_SPINE:
            yield from emit(f"{regime}&{mom}&sma_abv_30&{extra}&{rsi_paid}")
    for extra in ISLAND2C_EXTRA_ON_EMA30:
        for regime in ISLAND2C_H1_SPINE:
            yield from emit(f"{regime}&{mom}&ema_abv_30&{extra}&{rsi_paid}")
    for nmom in ISLAND2C_MOM_NEIGHBOR:
        for cont in ("sma_abv_30", "ema_abv_30"):
            for regime in ISLAND2C_H1_SPINE:
                yield from emit(f"{regime}&{nmom}&{cont}")
    for nmom in ISLAND2C_MOM_NEIGHBOR:
        for cont in ("sma_abv_30", "ema_abv_30"):
            for regime in ISLAND2C_H1_SPINE:
                yield from emit(f"{regime}&{nmom}&{cont}&{rsi_paid}")


def _dsharp_periods_for_gate(gate: int) -> tuple[int, ...]:
    close = DSHARP_PERIODS_NEAR_150 if gate == 150 else DSHARP_PERIODS_NEAR_180
    return close + DSHARP_PERIODS_NEAR_300


def _daily_sharpe_dip(blocked_keys: set[str]) -> Iterator[str]:
    """Densify #4: untested dip×h4 neighborhood of the daily-Sharpe near-misses.

    Emit first, ahead of literature undry #3. Two atoms, ema before sma.
    Order is the measured seeds (best daily Sharpe first), each varied on
    one axis: the nearest new gate period, then a step-6 lookback at the
    seed gate (longer first), then the unused periods around 300. Cluster
    lookbacks (288/312 and the step-12 rungs between the seeds) times the
    new periods follow, then the step-6 gaps times those periods, then the
    sma twin of each seed dip. A 5m ``sma_abv`` / ``ema_abv`` / ``rsi_>``
    confirm on an h4 gate is ``filler_atom`` and is not minted. No mom,
    no structure, no new parser atom. ``blocked_keys`` are near-duplicate
    keys of LITDIP and the older recipe.
    """
    from hedge_fund.trading.mint_quality import mint_block_reason

    seen = set(blocked_keys)

    def emit(lookback: int, pct: int, kind: str, period: int) -> Iterator[str]:
        name = f"dip_{lookback}b_lt{pct}pc&h4_{kind}_abv_{period}"
        if name_has_mom_gt_and_dip(name) or not name_is_parseable(name):
            return
        if mint_block_reason(name):
            return
        key = near_duplicate_key(name)
        if key in seen:
            return
        seen.add(key)
        yield name

    for lookback, pct, gate in DSHARP_SEEDS:
        close = DSHARP_PERIODS_NEAR_150 if gate == 150 else DSHARP_PERIODS_NEAR_180
        for period in close:
            yield from emit(lookback, pct, "ema", period)
        for neighbor in DSHARP_LB_NEIGHBORS[lookback]:
            yield from emit(neighbor, pct, "ema", gate)
        for period in DSHARP_PERIODS_NEAR_300:
            yield from emit(lookback, pct, "ema", period)
    for gate in DSHARP_SEED_GATES:
        for lookback in DSHARP_GAP_LOOKBACKS:
            yield from emit(lookback, 8, "ema", gate)
    for period in DSHARP_NEW_PERIODS:
        for lookback in DSHARP_CLUSTER_LOOKBACKS:
            yield from emit(lookback, 8, "ema", period)
    for period in DSHARP_NEW_PERIODS:
        for lookback in DSHARP_GAP_LOOKBACKS:
            yield from emit(lookback, 8, "ema", period)
    for lookback, pct, gate in DSHARP_SEEDS:
        for period in _dsharp_periods_for_gate(gate):
            yield from emit(lookback, pct, "sma", period)


def iter_daily_sharpe_dip_names() -> Iterator[str]:
    """Densify #4 names in emit order (the recipe prefix)."""
    yield from _daily_sharpe_dip(set())


def _dense5_box(kind: str, pct: int) -> list[str]:
    cl, cp = DENSE5_CENTER
    cells = [(lb, p) for lb in DENSE5_LOOKBACKS for p in DENSE5_PERIODS]
    cells.sort(key=lambda c: (abs(c[0] - cl) / 6 + abs(c[1] - cp) / 10, -c[0], -c[1]))
    return [f"dip_{lb}b_lt{pct}pc&h4_{kind}_abv_{p}" for lb, p in cells]


def _dense5_explore() -> list[str]:
    out = list(DENSE5_STACK_SUPPORT)
    for stack in DENSE5_STACK_TWINS:
        for n in DENSE5_SUPPORT_NS:
            out.append(f"{stack}&near_swing_lo_{n}")
    for lookbacks, pct, kind, periods in DENSE5_LONG_DIPS:
        for lb in lookbacks:
            for p in periods:
                out.append(f"dip_{lb}b_lt{pct}pc&h4_{kind}_abv_{p}")
    return out


def _dense_ema150(blocked_keys: set[str]) -> Iterator[str]:
    """Densify #5: the dip×h4 box around the ``ema_abv_150`` passes.

    Emit first, ahead of densify #4. ema 8% / 10% box, then the
    exploratory share (stack × swing-low support, long 12% dip × slow
    h4 ema), then ema 6% and the sma twins. Names are emitted in
    canonical atom order. Every name passes ``mint_block_reason``
    (no 5m filler on an h4 spine, no dead zone), has no mom atom, at
    most 3 atoms, and structure N<=96. ``blocked_keys`` are the
    near-duplicate keys of #4, #3 and the older regime recipe.
    """
    from hedge_fund.trading.mint_quality import canonical_name, mint_block_reason

    seen = set(blocked_keys)

    def emit(raw: str) -> Iterator[str]:
        name = canonical_name(raw) or raw
        if name_has_mom_gt_and_dip(name) or not name_is_parseable(name):
            return
        if mint_block_reason(name):
            return
        key = near_duplicate_key(name)
        if key in seen:
            return
        seen.add(key)
        yield name

    for kind, pct in DENSE5_SPINE_HEAD:
        for name in _dense5_box(kind, pct):
            yield from emit(name)
    for name in _dense5_explore():
        yield from emit(name)
    for kind, pct in DENSE5_SPINE_TAIL:
        for name in _dense5_box(kind, pct):
            yield from emit(name)


def iter_dense_ema150_names() -> Iterator[str]:
    """Densify #5 names in emit order (the recipe prefix)."""
    yield from _regime_ands_parts()["dense5"]


def _lit_trend_dip(blocked_keys: set[str]) -> Iterator[str]:
    """Literature undry #3: slow h4 trend gate × capitulation dip.

    Emitted immediately after densify #4.

    ``dip_{L}b_lt{T}pc&h4_{sma,ema}_abv_{P}``, already in canonical (sorted)
    atom order. Gate tiers outer, dip cells (EV order) inner, so the
    best-measured gates walk every cell before wider periods. Two atoms:
    no mom, no 5m MA / RSI filler, no structure. ``blocked_keys`` are
    near-duplicate keys of the rest of the recipe.
    """
    seen = set(blocked_keys)
    for tier in LITDIP_GATE_TIERS:
        for lookback, pct in LITDIP_CELLS:
            for gate in tier:
                name = f"dip_{lookback}b_lt{pct}pc&{gate}"
                if name_has_mom_gt_and_dip(name) or not name_is_parseable(name):
                    continue
                key = near_duplicate_key(name)
                if key in seen:
                    continue
                seen.add(key)
                yield name


def iter_lit_trend_dip_names() -> Iterator[str]:
    """Literature undry #3 names in emit order (after densify #4)."""
    yield from _lit_trend_dip(set())

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
    prefix. 2026-10-02: that abv_40 / rsi_60 prefix drained (+0 admits).
    Island #2b minted h4 twins of
    ``mom_18b_gt2pc & *_abv_30 & rsi_14_>50``. 2026-10-07 those h4
    evals measured FAIL. **Island #2c** emits first (h1 +
    ``rsi_14_>45`` / gap MAs / 5-atom extensions / modest mom
    neighbors).     Island #2b stays next, deprioritized. 2026-10-08 literature
    undry #3 (``LITDIP_*``) led and drained. 2026-10-09 densify #4
    (``DSHARP_*``, the dip×h4 daily-Sharpe neighborhood) emits
    first. No ``don_hi`` / ``near_swing_lo``. Cheap ``near_swing_hi``
    N≤48 only on depth-7 stacks (not minted without a dip extra).
    HTF False → no new long (flat). No named candlesticks.
    """
    yield from _iter_without_mom_and_dip(_regime_ands_raw())


def _drained_regime_raw() -> Iterator[str]:
    """Recipe families already walked by the farm before island #2b."""
    yield from _regime_undry_winner_shaped()
    yield from _regime_fresh_winner_shaped()
    yield from _regime_fresh_pairs()
    yield from _regime_winner_3atoms()
    yield from _regime_deep_stacks()
    yield from _regime_pair_ands(REGIME_MOM_BASES_PRIOR, regimes=REGIME_ATOMS_PRIOR)
    yield from _regime_pair_ands(REGIME_DIP_BASES, regimes=REGIME_ATOMS_PRIOR)


def _rest_of_recipe_keys() -> set[str]:
    """Keys of the non-regime recipe families (structure / trend tails)."""
    keys: set[str] = set()
    for n in STRUCTURE_NS:
        for fam in (
            _grind_participation_ands,
            _trend_participation_ands,
            _legacy_structure_ands,
            _near_level_ands,
            _leftover_trend_ands,
        ):
            keys.update(near_duplicate_key(name) for name in fam(n))
    return keys


def _regime_ands_parts() -> dict[str, list[str]]:
    """Regime families in emit order, each deduped against the ones after it."""
    drained = list(_drained_regime_raw())
    blocked = {near_duplicate_key(name) for name in drained}
    island_2b = list(_regime_island_2b(blocked))
    blocked.update(near_duplicate_key(name) for name in island_2b)
    island_2c = list(_regime_island_2c(blocked))
    blocked.update(near_duplicate_key(name) for name in island_2c)
    lit = list(_lit_trend_dip(blocked))
    blocked.update(near_duplicate_key(name) for name in lit)
    sharp = list(_daily_sharpe_dip(blocked))
    blocked.update(near_duplicate_key(name) for name in sharp)
    blocked.update(_rest_of_recipe_keys())
    dense = list(_dense_ema150(blocked))
    return {
        "dense5": dense,
        "dsharp": sharp,
        "lit": lit,
        "island_2c": island_2c,
        "island_2b": island_2b,
        "drained": drained,
    }


def _regime_ands_raw() -> Iterator[str]:
    parts = _regime_ands_parts()
    for fam in ("dense5", "dsharp", "lit", "island_2c", "island_2b", "drained"):
        yield from parts[fam]


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

    Densify #5 (``DENSE5_*``: dip 186–246 × h4 120–170 box around the
    ``dip×h4_ema_abv_150`` passes, plus a ~20% exploratory share) is
    first. Densify #4 (``DSHARP_*``: one-axis neighborhood of the dip×h4
    daily-Sharpe near-misses, no mom, no 5m filler) follows it.
    Literature undry #3 (``LITDIP_*``: slow h4 trend gate AND a
    capitulation dip, no mom) follows it. Island #2c follows so a dry refill mints h1 densify of the paying
    ``mom_18b_gt2pc`` island (``rsi_14_>45`` on ``*_abv_{30,20,50}``,
    gap MAs 35/25/15/60, one extra MA on ``…&*_abv_30&rsi_14_>50``,
    then modest mom neighbors) immediately. Island #2b (h4×mom) follows,
    deprioritized after the 2026-10-07 FAIL. The drained #65 prefix
    (``sma_abv_40`` / ``ema_abv_40``, ``rsi_14_>60``, intermediate mom,
    gap-fill ``sma_abv_50`` / ``ema_abv_20``) follows that.
    Then drained winner-shaped stacks (``sma_abv_30`` / ``ema_abv_30``,
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
    3×3.     Central guard drops any ``mom_*_gt*`` ∧ ``dip_*`` stack, any
    same-indicator redundant threshold (or empty band), and any mom/dip
    percent above the reachability cap. No WaveTrend, no MFI.
    """
    from hedge_fund.trading.mint_quality import (
        redundant_bound_reason,
        unreachable_threshold_reason,
    )

    # The catalog keeps historical shapes, including measured zero-trade
    # zones. next_refill_batch / densify / claim apply the full mint block
    # (dead zones, canonical duplicates) and are what actually gets leased.
    for name in _iter_without_mom_and_dip(_iter_recipe_families()):
        if redundant_bound_reason(name) or unreachable_threshold_reason(name):
            continue
        yield name


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
    lift=None,
    lift_seed: int | None = None,
) -> list[str]:
    """Next parseable, non-near-duplicate, never-logged names from the recipe.

    An informative lift model reranks a window of those names. A missing
    or flat model returns the recipe order unchanged.
    """
    from hedge_fund.trading.atom_lift import LIFT_SEED, STEER_WINDOW, steer_candidates
    from hedge_fund.trading.mint_quality import (
        canonical_key_set,
        canonical_name,
        mint_block_reason as _mint_block_reason,
    )

    want = max(0, int(n))
    if want == 0:
        return []
    informative = lift is not None and bool(getattr(lift, "informative", False))
    target = want * STEER_WINDOW if informative else want
    taken = {name for name in taken_names if name}
    taken_keys = {near_duplicate_key(name) for name in taken}
    taken_canon = canonical_key_set(taken)
    out: list[str] = []
    for cand in iter_recipe_names():
        if len(out) >= target:
            break
        canon = canonical_name(cand)
        if not canon or not name_is_parseable(cand):
            continue
        if name_has_mom_gt_and_dip(cand):
            continue
        if _mint_block_reason(cand, taken_canon):
            continue
        if not _is_refillable_name(cand):
            continue
        if cand in taken or canon in taken_canon:
            continue
        key = near_duplicate_key(cand)
        if key in taken_keys:
            continue
        out.append(cand)
        taken.add(cand)
        taken_keys.add(key)
        taken_canon.add(canon)
    if not informative:
        return out
    return steer_candidates(
        out,
        lift,
        want,
        seed=LIFT_SEED if lift_seed is None else int(lift_seed),
    )


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
    from hedge_fund.trading.atom_lift import lift_model_for_mint

    taken = set(taken_names)
    taken.update(load_extended_names())
    taken.update(generate_universe())
    added = next_refill_batch(taken_names=taken, n=batch_size, lift=lift_model_for_mint())
    if added:
        append_extended_batch(added)
    return added
