"""Mostly-random discovery mint. Approved by Alexander, 2026-10-27.

Prod refill used to be a deterministic recipe (`iter_recipe_names` →
`next_refill_batch`). That stream is still the exploit share. Each batch
is now about 70% random AND-combinations and 30% exploit. About a third
of the exploit share is a paired single-atom swap of an already-tested
name; the rest is the recipe, then densify, still ranked by the lift
model.

The random proposal draws a depth from the weights below, then that many
atoms from the parser-allowed vocabulary. Parameters sit on the
near-duplicate lattice (periods, lookbacks, and thresholds are fixed
points of ``_canon_atom``) so two grid points do not collide with each
other. A draw that fails a mint ban is thrown away and another index is
drawn. The loop is capped.

Depth weights, before the mint ban:

    2: 30%   3: 30%   4: 20%   5: 10%   6: 6%   7: 4%

``mint_block_reason`` rejects stacks of ``DEAD_DEPTH`` (5) or more atoms
(``dead_depth``). The sampler draws only from the rows of that table
that the ban still allows, and renormalises their weights. With the ban
at 5 the draw is 2: 37.5%, 3: 37.5%, 4: 25%. Raising ``DEAD_DEPTH``
above a row puts that row back into the draw with its table weight.
The ban itself is unchanged.

The generator also refuses to *propose* combinations that the bans make
impossible: mom with dip, h4 with mom, and a 5m MA on an HTF moving-average
spine. RSI on an HTF spine is proposed as an upper bound only (a lower
bound there is the measured filler). The filter still runs on every name
and is what decides.

Reproducibility: each attempt uses ``Random(mix(seed, draw_index))``.
The seed and the next draw index live in ``discovery_explorer.json`` on
the paper-state directory (same PVC as ``discovery_extended.json``). A
restart continues at that index, so it does not repeat a draw or skip
one. The file's seed wins over the env once the file exists. Pair links
are appended to ``discovery_pairs.jsonl`` (parent, axis). Neither file
is a gate input.

Shares (clamped to [0, 1]):

- ``DISCOVERY_RANDOM_SHARE`` default 0.70
- ``DISCOVERY_PAIRED_SHARE`` default 1/3 of the exploit share (not of the batch)
- ``DISCOVERY_EXPLORER_SEED`` default 20261027, used only when no cursor exists

If a side cannot fill its quota, the shortfall comes from the other sides
that are turned on. A share of 0 is off, not an empty side.
"""
from __future__ import annotations

import json
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from hedge_fund.paths import state_root
from hedge_fund.trading.discovery_guard import lookback_too_expensive_reason
from hedge_fund.trading.holdout import seal
from hedge_fund.trading.mint_quality import (
    DEAD_DEPTH,
    DEAD_MOM_PCT,
    canonical_key_set,
    canonical_name,
    mint_block_reason,
)
from hedge_fund.trading.mint_quality import _static_cap
from hedge_fund.trading.refill import (
    RECIPE_MAX_ATOMS,
    STRUCTURE_NS,
    name_has_mom_gt_and_dip,
    name_is_parseable,
)
from hedge_fund.trading.universe import near_duplicate_key

DISCOVERY_EXPLORER = "discovery_explorer.json"
DISCOVERY_PAIRS = "discovery_pairs.jsonl"

DEFAULT_RANDOM_SHARE = 0.70
DEFAULT_PAIRED_OF_EXPLOIT = 1.0 / 3.0
DEFAULT_SEED = 20261027
# Attempts per accepted name, including rejects. A batch stops even if
# the vocabulary cannot fill (the other side of the mix then covers it).
MAX_ATTEMPTS_PER_NAME = 48

# Full proposal table. Sums to 100. ``draw_depth`` keeps only the rows
# ``dead_depth`` still allows (depth < ``DEAD_DEPTH``) and renormalises.
# The table stays so a higher ``DEAD_DEPTH`` turns the deeper rows back on.
DEPTH_WEIGHTS: tuple[tuple[int, int], ...] = (
    (2, 30),
    (3, 30),
    (4, 20),
    (5, 10),
    (6, 6),
    (7, 4),
)

# Fixed points of ``_round_period`` / the 6-bar and 2-percent canons.
MA_PERIODS: tuple[int, ...] = (
    10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 80, 90, 100, 120, 140, 150, 160, 180, 200,
)
HTF_PERIODS: tuple[int, ...] = MA_PERIODS
RSI_PERIODS: tuple[int, ...] = (10, 15, 20, 25, 30)
RSI_GT: tuple[int, ...] = (35, 40, 45, 50, 55, 60, 65, 70)
RSI_LT: tuple[int, ...] = (40, 50, 60, 70, 80)
MOM_LOOKBACKS: tuple[int, ...] = (
    6, 12, 18, 24, 30, 36, 42, 48, 60, 72, 84, 96, 120, 144, 180,
)
# 18 and 24 at >=5% are ``dead_dip``. They are not on this grid.
DIP_LOOKBACKS: tuple[int, ...] = (
    6, 12, 30, 36, 42, 48, 60, 72, 84, 96, 120, 144, 168, 186, 204, 222, 240, 264,
)
_PCTS: tuple[int, ...] = (2, 4, 6, 8, 10, 12)
STACKS: tuple[tuple[int, ...], ...] = (
    (10, 30, 60),
    (10, 40, 80),
    (15, 40, 100),
    (20, 50, 100),
    (20, 50, 200),
    (20, 60, 120),
    (30, 70, 150),
    (40, 80, 160),
    (50, 100, 200),
)

FAMILIES: tuple[str, ...] = (
    "sma_abv",
    "ema_abv",
    "sma_stack",
    "ema_stack",
    "rsi",
    "mom",
    "dip",
    "don_hi",
    "don_lo",
    "near_swing_hi",
    "near_swing_lo",
    "dbl_bot",
    "h1_ema",
    "h1_sma",
    "h4_ema",
    "h4_sma",
)
HTF_FAMILIES = frozenset({"h1_ema", "h1_sma", "h4_ema", "h4_sma"})
H4_FAMILIES = frozenset({"h4_ema", "h4_sma"})
AXES: tuple[str, ...] = ("timeframe", "ma_kind", "param_step", "add_atom", "remove_atom")

_HTF_ATOM = re.compile(r"^(h[14])_(ema|sma)_abv_(\d+)$")
_MA_ATOM = re.compile(r"^(sma_abv|ema_abv)_(\d+)$")
_STACK_ATOM = re.compile(r"^(sma_stack|ema_stack)_([\d_]+)$")
_MOM_DIP_ATOM = re.compile(r"^(mom|dip)_(\d+)b_(gt|lt)(\d+)pc$")
_STRUCT_ATOM = re.compile(
    r"^(don_hi|don_lo|near_swing_hi|near_swing_lo|dbl_bot)_(\d+)$"
)
_RSI_GT_ATOM = re.compile(r"^rsi_(\d+)_>(\d+)(?:_<(\d+))?$")
_RSI_LT_ATOM = re.compile(r"^rsi_(\d+)_<(\d+)$")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    return min(1.0, max(0.0, value))


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def random_share() -> float:
    """Fraction of a batch drawn from the random sampler. Default 0.70."""
    return _env_float("DISCOVERY_RANDOM_SHARE", DEFAULT_RANDOM_SHARE)


def paired_share() -> float:
    """Fraction of the *exploit* share used for paired swaps. Default 1/3."""
    return _env_float("DISCOVERY_PAIRED_SHARE", DEFAULT_PAIRED_OF_EXPLOIT)


def explorer_seed() -> int:
    """Seed for a new cursor. Ignored once ``discovery_explorer.json`` exists."""
    return _env_int("DISCOVERY_EXPLORER_SEED", DEFAULT_SEED)


def split_counts(
    n: int,
    *,
    random_share_value: float | None = None,
    paired_share_value: float | None = None,
) -> tuple[int, int, int]:
    """``(n_random, n_paired, n_recipe)`` for a batch of ``n``.

    ``n_paired + n_recipe`` is the exploit share. ``n_paired`` is about
    ``paired_share`` of that exploit share. Python's half-to-even ``round``
    is what the implementation uses (``16 * 0.70`` → 11 random, 5 exploit,
    2 paired, 3 recipe).
    """
    want = max(0, int(n))
    rshare = random_share() if random_share_value is None else min(1.0, max(0.0, float(random_share_value)))
    pshare = paired_share() if paired_share_value is None else min(1.0, max(0.0, float(paired_share_value)))
    n_random = min(want, max(0, int(round(want * rshare))))
    n_exploit = want - n_random
    n_paired = min(n_exploit, max(0, int(round(n_exploit * pshare))))
    n_recipe = n_exploit - n_paired
    return n_random, n_paired, n_recipe


def mix_seed(seed: int, index: int) -> int:
    """Stable 64-bit mix. Not Python's salted ``hash()``."""
    x = (int(seed) + 0x9E3779B97F4A7C15 * (int(index) + 1)) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 30
    x = (x * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 27
    x = (x * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 31
    return x or 1


def rng_for(seed: int, index: int) -> random.Random:
    return random.Random(mix_seed(seed, index))


def allowed_depth_weights(
    weights: Sequence[tuple[int, int]] | None = None,
    *,
    dead_depth: int | None = None,
) -> tuple[tuple[int, int], ...]:
    """Rows of the depth table that ``dead_depth`` does not reject.

    The ban is ``len(atoms) >= DEAD_DEPTH`` in ``dead_zone_reason``. A
    depth survives while it is strictly below that threshold. Weights are
    not rescaled here; ``draw_depth`` divides by the surviving total.
    If the ban covers every row, the full table is returned so the draw
    still has a defined depth (the filter then rejects it).
    """
    table = tuple(weights) if weights is not None else DEPTH_WEIGHTS
    limit = DEAD_DEPTH if dead_depth is None else int(dead_depth)
    allowed = tuple(
        (int(depth), int(weight))
        for depth, weight in table
        if int(weight) > 0 and int(depth) < limit
    )
    return allowed or table


def draw_depth(rng: random.Random) -> int:
    """One depth from the ban-filtered ``DEPTH_WEIGHTS``."""
    table = allowed_depth_weights()
    total = sum(weight for _depth, weight in table)
    pick = rng.randrange(total)
    acc = 0
    for depth, weight in table:
        acc += weight
        if pick < acc:
            return depth
    return table[-1][0]


def legal_pcts(lookback: int) -> tuple[int, ...]:
    """Even percents at or under the static move cap and under the dead mom zone.

    The live filter still applies the tape's 99th percentile when a tape
    is on disk. A percent that clears this table can still be rejected there.
    """
    cap = min(int(_static_cap(int(lookback))), int(DEAD_MOM_PCT) - 1)
    return tuple(pct for pct in _PCTS if pct <= cap)


def _compatible(chosen: set[str], fam: str) -> bool:
    if fam in chosen:
        return False
    trial = chosen | {fam}
    if "mom" in trial and "dip" in trial:
        return False
    if "mom" in trial and (trial & H4_FAMILIES):
        return False
    if (trial & HTF_FAMILIES) and (trial & {"sma_abv", "ema_abv"}):
        return False
    return True


def _pick_families(rng: random.Random, depth: int) -> list[str] | None:
    chosen: list[str] = []
    chosen_set: set[str] = set()
    for _ in range(depth):
        options = [fam for fam in FAMILIES if _compatible(chosen_set, fam)]
        if not options:
            return None
        fam = options[rng.randrange(len(options))]
        chosen.append(fam)
        chosen_set.add(fam)
    return chosen


def _choice(rng: random.Random, options: Sequence) -> object:
    return options[rng.randrange(len(options))]


def _sample_atom(rng: random.Random, fam: str, *, lt_only: bool) -> str | None:
    if fam in ("sma_abv", "ema_abv"):
        return f"{fam}_{_choice(rng, MA_PERIODS)}"
    if fam in ("sma_stack", "ema_stack"):
        periods = _choice(rng, STACKS)
        return fam + "_" + "_".join(str(p) for p in periods)  # type: ignore[arg-type]
    if fam == "rsi":
        period = _choice(rng, RSI_PERIODS)
        # RSI-above on an HTF moving average is filler. Propose an upper bound.
        if lt_only:
            return f"rsi_{period}_<{_choice(rng, RSI_LT)}"
        roll = rng.randrange(3)
        if roll == 0:
            return f"rsi_{period}_>{_choice(rng, RSI_GT)}"
        if roll == 1:
            return f"rsi_{period}_<{_choice(rng, RSI_LT)}"
        lo = int(_choice(rng, RSI_GT))
        hi_options = [hi for hi in RSI_LT if hi >= lo + 10]
        if not hi_options:
            return f"rsi_{period}_>{lo}"
        return f"rsi_{period}_>{lo}_<{_choice(rng, hi_options)}"
    if fam in ("mom", "dip"):
        grid = MOM_LOOKBACKS if fam == "mom" else DIP_LOOKBACKS
        lookback = int(_choice(rng, grid))
        pcts = legal_pcts(lookback)
        if not pcts:
            return None
        word = "gt" if fam == "mom" else "lt"
        return f"{fam}_{lookback}b_{word}{_choice(rng, pcts)}pc"
    if fam in ("don_hi", "don_lo", "near_swing_hi", "near_swing_lo", "dbl_bot"):
        return f"{fam}_{_choice(rng, STRUCTURE_NS)}"
    if fam in HTF_FAMILIES:
        tf, kind = fam.split("_", 1)
        return f"{tf}_{kind}_abv_{_choice(rng, HTF_PERIODS)}"
    return None


def propose_random(seed: int, index: int) -> tuple[str | None, int]:
    """One unfiltered proposal. Returns ``(name or None, requested depth)``."""
    rng = rng_for(seed, index)
    depth = draw_depth(rng)
    families = _pick_families(rng, depth)
    if not families:
        return None, depth
    lt_only = bool(HTF_FAMILIES & set(families))
    atoms: list[str] = []
    for fam in families:
        atom = _sample_atom(rng, fam, lt_only=lt_only)
        if not atom:
            return None, depth
        atoms.append(atom)
    return "&".join(sorted(atoms)), depth


def _h4_and_mom(name: str) -> bool:
    atoms = name.split("&")
    return any(atom.startswith("h4_") for atom in atoms) and any(
        atom.startswith("mom_") for atom in atoms
    )


class _Taken:
    """Names already tested, queued, or emitted earlier in this batch."""

    def __init__(self, names: Iterable[str]):
        self.names = {name for name in names if isinstance(name, str) and name}
        self.keys = {near_duplicate_key(name) for name in self.names}
        self.canon = canonical_key_set(self.names)

    def add(self, name: str) -> None:
        self.names.add(name)
        self.keys.add(near_duplicate_key(name))
        key = canonical_name(name)
        if key:
            self.canon.add(key)


def reject_reason(
    name: str,
    taken_names: Iterable[str] = (),
    *,
    min_atoms: int = 2,
    book: _Taken | None = None,
) -> str | None:
    """Why ``name`` must not be minted, or None when it clears every ban.

    Random names use ``min_atoms=2``. A paired removal may leave one atom
    (``min_atoms=1``). Both still have to pass ``mint_block_reason``.
    """
    taken = book if book is not None else _Taken(taken_names)
    if not name or not name_is_parseable(name):
        return "unparseable"
    n_atoms = name.count("&") + 1
    if n_atoms > RECIPE_MAX_ATOMS:
        return "too_many_atoms"
    if n_atoms < min_atoms:
        return "too_few_atoms"
    if name_has_mom_gt_and_dip(name):
        return "mom_and_dip"
    if _h4_and_mom(name):
        return "h4_mom"
    if lookback_too_expensive_reason(name):
        return "lookback"
    blocked = mint_block_reason(name, taken.canon)
    if blocked:
        return blocked
    if name in taken.names:
        return "taken"
    canon = canonical_name(name)
    if canon and canon in taken.canon:
        return "canonical_tested"
    if near_duplicate_key(name) in taken.keys:
        return "near_duplicate"
    return None


def sample_random_names(
    n: int,
    *,
    seed: int,
    start_index: int,
    taken_names: Iterable[str] = (),
    book: _Taken | None = None,
) -> tuple[list[str], int]:
    """Accept up to ``n`` random names. Returns ``(names, next_draw_index)``.

    Every attempt, including a reject, consumes one draw index.
    """
    want = max(0, int(n))
    taken = book if book is not None else _Taken(taken_names)
    index = int(start_index)
    if want == 0:
        return [], index
    out: list[str] = []
    limit = want * MAX_ATTEMPTS_PER_NAME
    attempts = 0
    while len(out) < want and attempts < limit:
        attempts += 1
        name, _depth = propose_random(seed, index)
        index += 1
        if not name or reject_reason(name, book=taken, min_atoms=2):
            continue
        canon = canonical_name(name) or name
        if reject_reason(canon, book=taken, min_atoms=2):
            continue
        taken.add(canon)
        out.append(canon)
    return out, index


def _atoms(name: str) -> list[str]:
    return [part.strip() for part in name.split("&") if part.strip()]


def _step_choices(grid: Sequence[int], value: int) -> list[int]:
    higher = next((item for item in grid if item > value), None)
    lower = next((item for item in reversed(list(grid)) if item < value), None)
    out: list[int] = []
    if higher is not None:
        out.append(higher)
    if lower is not None and lower not in out:
        out.append(lower)
    return out


def _pick_step(rng: random.Random, grid: Sequence[int], value: int) -> int | None:
    choices = _step_choices(grid, value)
    if not choices:
        return None
    return int(_choice(rng, choices))


def _step_atom(atom: str, rng: random.Random) -> str | None:
    """One grid step of one parameter. None when the atom has no legal step."""
    matched = _HTF_ATOM.match(atom)
    if matched:
        nxt = _pick_step(rng, HTF_PERIODS, int(matched.group(3)))
        if nxt is None:
            return None
        return f"{matched.group(1)}_{matched.group(2)}_abv_{nxt}"
    matched = _MA_ATOM.match(atom)
    if matched:
        nxt = _pick_step(rng, MA_PERIODS, int(matched.group(2)))
        if nxt is None:
            return None
        return f"{matched.group(1)}_{nxt}"
    matched = _STACK_ATOM.match(atom)
    if matched:
        periods = [int(part) for part in matched.group(2).split("_") if part]
        candidates: list[tuple[int, ...]] = []
        for index, period in enumerate(periods):
            for nxt in _step_choices(MA_PERIODS, period):
                trial = list(periods)
                trial[index] = nxt
                if len(set(trial)) == len(trial) and trial == sorted(trial):
                    candidates.append(tuple(trial))
        if not candidates:
            return None
        chosen = _choice(rng, candidates)
        return f"{matched.group(1)}_" + "_".join(str(part) for part in chosen)  # type: ignore[union-attr]
    matched = _MOM_DIP_ATOM.match(atom)
    if matched:
        kind, lookback, word, pct = (
            matched.group(1),
            int(matched.group(2)),
            matched.group(3),
            int(matched.group(4)),
        )
        grid = MOM_LOOKBACKS if kind == "mom" else DIP_LOOKBACKS
        lb_choices = [nxt for nxt in _step_choices(grid, lookback) if pct in legal_pcts(nxt)]
        pct_choices = _step_choices(legal_pcts(lookback), pct)
        dims: list[tuple[str, int]] = [("lb", nxt) for nxt in lb_choices]
        dims.extend(("pct", nxt) for nxt in pct_choices)
        if not dims:
            return None
        dim, nxt = _choice(rng, dims)  # type: ignore[misc]
        if dim == "lb":
            return f"{kind}_{nxt}b_{word}{pct}pc"
        return f"{kind}_{lookback}b_{word}{nxt}pc"
    matched = _STRUCT_ATOM.match(atom)
    if matched:
        nxt = _pick_step(rng, STRUCTURE_NS, int(matched.group(2)))
        if nxt is None or nxt > 96:
            return None
        return f"{matched.group(1)}_{nxt}"
    matched = _RSI_GT_ATOM.match(atom)
    if matched:
        period = int(matched.group(1))
        lo = int(matched.group(2))
        hi = int(matched.group(3)) if matched.group(3) else None
        options: list[str] = []
        for nxt in _step_choices(RSI_PERIODS, period):
            if hi is None:
                options.append(f"rsi_{nxt}_>{lo}")
            elif hi > lo:
                options.append(f"rsi_{nxt}_>{lo}_<{hi}")
        for nxt in _step_choices(RSI_GT, lo):
            if hi is not None and nxt >= hi:
                continue
            if hi is None:
                options.append(f"rsi_{period}_>{nxt}")
            else:
                options.append(f"rsi_{period}_>{nxt}_<{hi}")
        if hi is not None:
            for nxt in _step_choices(RSI_LT, hi):
                if nxt > lo:
                    options.append(f"rsi_{period}_>{lo}_<{nxt}")
        options = [item for item in options if item != atom]
        if not options:
            return None
        return str(_choice(rng, options))
    matched = _RSI_LT_ATOM.match(atom)
    if matched:
        period = int(matched.group(1))
        hi = int(matched.group(2))
        options = [f"rsi_{nxt}_<{hi}" for nxt in _step_choices(RSI_PERIODS, period)]
        options.extend(f"rsi_{period}_<{nxt}" for nxt in _step_choices(RSI_LT, hi))
        options = [item for item in options if item != atom]
        if not options:
            return None
        return str(_choice(rng, options))
    return None


def _family_id(atom: str) -> str | None:
    matched = _HTF_ATOM.match(atom)
    if matched:
        return f"{matched.group(1)}_{matched.group(2)}"
    matched = _MA_ATOM.match(atom)
    if matched:
        return matched.group(1)
    matched = _STACK_ATOM.match(atom)
    if matched:
        return matched.group(1)
    if atom.startswith("mom_"):
        return "mom"
    if atom.startswith("dip_"):
        return "dip"
    if atom.startswith("rsi_"):
        return "rsi"
    matched = _STRUCT_ATOM.match(atom)
    if matched:
        return matched.group(1)
    return None


def _has_htf_atom(atoms: Sequence[str]) -> bool:
    return any(_HTF_ATOM.match(atom) for atom in atoms)


def swap_on_axis(parent: str, axis: str, rng: random.Random) -> str | None:
    """Parent with exactly one atom changed along ``axis``, or None.

    Atom order is the parent's order, so a pairwise diff stays readable.
    """
    atoms = _atoms(parent)
    if not atoms or axis not in AXES:
        return None
    if axis == "timeframe":
        order = [i for i, atom in enumerate(atoms) if _HTF_ATOM.match(atom)]
        rng.shuffle(order)
        for index in order:
            atom = atoms[index]
            swapped = ("h1" + atom[2:]) if atom.startswith("h4") else ("h4" + atom[2:])
            if swapped in atoms:
                continue
            out = list(atoms)
            out[index] = swapped
            return "&".join(out)
        return None
    if axis == "ma_kind":
        order = list(range(len(atoms)))
        rng.shuffle(order)
        for index in order:
            swapped = _swap_ma_kind(atoms[index])
            if swapped is None or swapped in atoms:
                continue
            out = list(atoms)
            out[index] = swapped
            return "&".join(out)
        return None
    if axis == "param_step":
        order = list(range(len(atoms)))
        rng.shuffle(order)
        for index in order:
            swapped = _step_atom(atoms[index], rng)
            if swapped is None or swapped == atoms[index] or swapped in atoms:
                continue
            out = list(atoms)
            out[index] = swapped
            return "&".join(out)
        return None
    if axis == "add_atom":
        present = {fam for fam in (_family_id(atom) for atom in atoms) if fam}
        options = [fam for fam in FAMILIES if _compatible(present, fam)]
        if not options:
            return None
        rng.shuffle(options)
        lt_only = _has_htf_atom(atoms)
        for fam in options:
            atom = _sample_atom(rng, fam, lt_only=lt_only)
            if atom and atom not in atoms:
                return "&".join([*atoms, atom])
        return None
    if axis == "remove_atom":
        if len(atoms) < 2:
            return None
        index = rng.randrange(len(atoms))
        return "&".join(atoms[:index] + atoms[index + 1 :])
    return None


def _swap_ma_kind(atom: str) -> str | None:
    matched = _HTF_ATOM.match(atom)
    if matched:
        kind = "ema" if matched.group(2) == "sma" else "sma"
        return f"{matched.group(1)}_{kind}_abv_{matched.group(3)}"
    matched = _MA_ATOM.match(atom)
    if matched:
        kind = "ema_abv" if matched.group(1) == "sma_abv" else "sma_abv"
        return f"{kind}_{matched.group(2)}"
    matched = _STACK_ATOM.match(atom)
    if matched:
        kind = "ema_stack" if matched.group(1) == "sma_stack" else "sma_stack"
        return f"{kind}_{matched.group(2)}"
    return None


def one_swap(parent: str, rng: random.Random) -> tuple[str, str] | None:
    """First axis (in random order) that yields a one-atom change."""
    axes = list(AXES)
    rng.shuffle(axes)
    for axis in axes:
        child = swap_on_axis(parent, axis, rng)
        if child and _one_atom_change(parent, child, axis):
            return child, axis
    return None


def _one_atom_change(parent: str, child: str, axis: str) -> bool:
    before = _atoms(parent)
    after = _atoms(child)
    if axis == "add_atom":
        return len(after) == len(before) + 1 and set(before) <= set(after) and len(set(after) - set(before)) == 1
    if axis == "remove_atom":
        return len(before) == len(after) + 1 and set(after) <= set(before) and len(set(before) - set(after)) == 1
    return len(before) == len(after) and len(set(before) ^ set(after)) == 2


def sample_paired_names(
    n: int,
    parents: Iterable[str],
    *,
    seed: int,
    start_index: int,
    book: _Taken,
) -> tuple[list[tuple[str, str, str, int]], int]:
    """Up to ``n`` legal swaps. Each item is ``(child, parent, axis, draw_index)``."""
    want = max(0, int(n))
    pool = [parent for parent in parents if isinstance(parent, str) and parent.strip()]
    index = int(start_index)
    if want == 0 or not pool:
        return [], index
    out: list[tuple[str, str, str, int]] = []
    attempts = 0
    limit = want * MAX_ATTEMPTS_PER_NAME
    while len(out) < want and attempts < limit:
        attempts += 1
        draw = index
        rng = rng_for(seed, draw)
        index += 1
        parent = pool[rng.randrange(len(pool))]
        swapped = one_swap(parent, rng)
        if swapped is None:
            continue
        child, axis = swapped
        if not _one_atom_change(parent, child, axis):
            continue
        if reject_reason(child, book=book, min_atoms=1):
            continue
        book.add(child)
        out.append((child, parent, axis, draw))
    return out, index


def explorer_path() -> Path:
    return state_root() / DISCOVERY_EXPLORER


def pairs_path() -> Path:
    return state_root() / DISCOVERY_PAIRS


def load_explorer_cursor() -> dict:
    """Persisted seed and next draw index. Missing file uses the env seed at 0."""
    path = explorer_path()
    seed = explorer_seed()
    if not path.exists():
        return {"seed": seed, "draw_index": 0}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"seed": seed, "draw_index": 0}
    if not isinstance(data, dict):
        return {"seed": seed, "draw_index": 0}
    try:
        file_seed = int(data.get("seed"))
        draw_index = int(data.get("draw_index") or 0)
    except (TypeError, ValueError):
        return {"seed": seed, "draw_index": 0}
    return {"seed": file_seed, "draw_index": max(0, draw_index)}


def save_explorer_cursor(seed: int, draw_index: int) -> Path:
    path = explorer_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "v": 1,
        "paper_only": True,
        "seed": int(seed),
        "draw_index": int(draw_index),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)
    return path


def load_pair_links() -> list[dict]:
    """Pair rows, last line per child wins. Missing file is an empty list."""
    path = pairs_path()
    if not path.exists():
        return []
    found: dict[str, dict] = {}
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                child = row.get("child")
                parent = row.get("parent")
                if isinstance(child, str) and child and isinstance(parent, str) and parent:
                    found[child] = row
    except OSError:
        return []
    return list(found.values())


def append_pair_links(rows: Sequence[dict]) -> None:
    if not rows:
        return
    path = pairs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def warm_explorer_caches() -> int:
    """Touch reachability caps for the sampler grids. Off the claim path.

    ``warm_refill_caches`` calls this and does not add it to its count.
    """
    from hedge_fund.trading.mint_quality import max_move_pct

    count = 0
    for lookback in MOM_LOOKBACKS + DIP_LOOKBACKS:
        max_move_pct(int(lookback))
        count += 1
    return count


def _take_exploit(name: str, book: _Taken) -> bool:
    if not isinstance(name, str) or not name or name in book.names:
        return False
    if near_duplicate_key(name) in book.keys:
        return False
    canon = canonical_name(name)
    if canon and canon in book.canon:
        return False
    book.add(name)
    return True


def _source(random_n: int, paired_n: int, exploit_n: int, densify_called: bool) -> str:
    kinds = [
        label
        for label, count in (("random", random_n), ("paired", paired_n), ("exploit", exploit_n))
        if count
    ]
    if len(kinds) >= 2:
        return "mixed"
    if random_n:
        return "random"
    if paired_n:
        return "paired"
    if densify_called:
        return "densify"
    return "recipe"


def _default_parents() -> list[str]:
    from hedge_fund.trading.tested_index import load_tested_index

    return list(load_tested_index())


def _fill_exploit(
    n: int,
    book: _Taken,
    *,
    lift,
    lift_seed: int | None,
    metrics: dict | None,
    results: dict | None,
    tested_index: dict | None,
    recipe_batch: Callable,
    densify_batch: Callable,
) -> tuple[list[str], bool]:
    """Recipe first, densify only the shortfall. Ranking stays inside those calls."""
    want = max(0, int(n))
    if want == 0:
        return [], False
    recipe = recipe_batch(
        taken_names=set(book.names),
        n=want,
        lift=lift,
        lift_seed=lift_seed,
    )
    kept: list[str] = []
    for name in recipe or ():
        if _take_exploit(name, book):
            kept.append(name)
    if len(kept) >= want:
        return kept[:want], False
    more, _ran_out = densify_batch(
        taken_names=set(book.names),
        n=want - len(kept),
        lift=lift,
        metrics=metrics,
        results=results,
        index=tested_index,
        lift_seed=lift_seed,
    )
    for name in more or ():
        if len(kept) >= want:
            break
        if _take_exploit(name, book):
            kept.append(name)
    return kept, True


@seal("refill")
def mix_refill(
    *,
    taken_names: Iterable[str],
    n: int,
    lift=None,
    lift_seed: int | None = None,
    metrics: dict | None = None,
    results: dict | None = None,
    parents: Iterable[str] | None = None,
    index: dict | None = None,
    recipe_batch: Callable | None = None,
    densify_batch: Callable | None = None,
    persist: bool = True,
) -> tuple[list[str], dict]:
    """One refill batch: random, then paired swaps, then recipe/densify.

    The recipe and densify ranking is unchanged (lift steer when the model
    is informative). A side that comes up short is filled from the others
    that are enabled. A share of 0 is not a source. ``persist`` writes the
    draw cursor and any new pair links; the caller still appends the names
    to the extended sidecar.
    """
    want = max(0, int(n))
    strategy = getattr(lift, "strategy", None) or "recipe_order"
    if want == 0:
        return [], _meta("recipe", 0, False, strategy, 0, 0, 0)
    if recipe_batch is None:
        from hedge_fund.trading.refill import next_refill_batch as recipe_batch
    if densify_batch is None:
        from hedge_fund.trading.densify import next_densify_batch as densify_batch

    cursor = load_explorer_cursor() if persist else {"seed": explorer_seed(), "draw_index": 0}
    seed = int(cursor["seed"])
    draw_index = int(cursor["draw_index"])
    book = _Taken(taken_names)
    n_random, n_paired, n_recipe = split_counts(want)
    parent_pool = list(parents) if parents is not None else None

    def parents_for() -> list[str]:
        nonlocal parent_pool
        if parent_pool is None:
            parent_pool = _default_parents()
        return parent_pool

    random_names, draw_index = sample_random_names(
        n_random, seed=seed, start_index=draw_index, book=book
    )
    paired_rows, draw_index = sample_paired_names(
        n_paired,
        parents_for() if n_paired else (),
        seed=seed,
        start_index=draw_index,
        book=book,
    )
    exploit_names, densify_called = _fill_exploit(
        n_recipe,
        book,
        lift=lift,
        lift_seed=lift_seed,
        metrics=metrics,
        results=results,
        tested_index=index,
        recipe_batch=recipe_batch,
        densify_batch=densify_batch,
    )
    short = want - (len(random_names) + len(paired_rows) + len(exploit_names))
    if short and random_share() > 0:
        extra, draw_index = sample_random_names(
            short, seed=seed, start_index=draw_index, book=book
        )
        random_names.extend(extra)
        short -= len(extra)
    if short and paired_share() > 0:
        extra_pairs, draw_index = sample_paired_names(
            short, parents_for(), seed=seed, start_index=draw_index, book=book
        )
        paired_rows.extend(extra_pairs)
        short -= len(extra_pairs)
    if short:
        extra_exploit, densify_more = _fill_exploit(
            short,
            book,
            lift=lift,
            lift_seed=lift_seed,
            metrics=metrics,
            results=results,
            tested_index=index,
            recipe_batch=recipe_batch,
            densify_batch=densify_batch,
        )
        exploit_names.extend(extra_exploit)
        densify_called = densify_called or densify_more

    names = list(random_names) + [row[0] for row in paired_rows] + list(exploit_names)
    if persist:
        if paired_rows:
            append_pair_links(
                [
                    {
                        "child": child,
                        "parent": parent,
                        "axis": axis,
                        "draw_index": draw,
                        "seed": seed,
                    }
                    for child, parent, axis, draw in paired_rows
                ]
            )
        if draw_index != int(cursor["draw_index"]) or not explorer_path().exists():
            save_explorer_cursor(seed, draw_index)
    return names, _meta(
        _source(len(random_names), len(paired_rows), len(exploit_names), densify_called),
        len(names),
        len(names) < want,
        strategy,
        len(random_names),
        len(paired_rows),
        len(exploit_names),
    )


def _meta(
    source: str,
    generated: int,
    exhausted: bool,
    strategy: str,
    random_n: int,
    paired_n: int,
    exploit_n: int,
) -> dict:
    return {
        "source": source,
        "generated_last": int(generated),
        "exhausted": bool(exhausted),
        "strategy": strategy,
        "random": int(random_n),
        "paired": int(paired_n),
        "exploit": int(exploit_n),
    }
