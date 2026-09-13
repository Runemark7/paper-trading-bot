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
Donchian / swing / near-level lookbacks through 96; the same-date later
pass extends ``STRUCTURE_NS`` through 192 (9h–16h on 5m) and leftover
TREND / ``ema_stack`` / 3-atom families already in ``parse_strategy``.
A same-date later pass broadens parser-allowed dip/mom lookbacks and
``%`` thresholds and adds continuation (trend-participation) ANDs so
new names can stay in a strong B&H OOS window long enough to clear
trades + Sharpe without softening the gate — still no named candlesticks.
A 2026-09-13 pass adds shallower ``gt1pc`` / ``lt1pc`` grind bases
(lookbacks unused by legacy/wide so ``near_duplicate_key`` stays
distinct) and expands short-MA 3-atoms onto every WIDE mom/dip ×
``don_hi`` / ``near_swing_hi``.
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
CONTINUATION_TRENDS: tuple[str, ...] = (
    "sma_abv_20",
    "ema_abv_20",
)
TREND_FILTERS: tuple[str, ...] = (
    "sma_abv_50",
    "sma_abv_100",
    "sma_abv_200",
    "ema_abv_50",
    "sma_stack_20_50_100",
    "rsi_14_>50",
)

# Multiples of 6 so near_duplicate_key is the identity for structure N.
# 6 bars = 30m on 5m. After 72 the step is 12 (84=7h … 192=16h).
# Distinct from a 1-bar param tweak.
STRUCTURE_NS: tuple[int, ...] = (
    6, 12, 18, 24, 30, 36, 42, 48, 54, 60, 66, 72, 84, 96,
    108, 120, 132, 144, 156, 168, 180, 192,
)

# Frozen 2026-09-12 morning set (through 96). Tests park this to prove
# the later lookback / leftover-AND pass still refills.
STRUCTURE_NS_THROUGH_96: tuple[int, ...] = (
    6, 12, 18, 24, 30, 36, 42, 48, 54, 60, 66, 72, 84, 96,
)

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
)

_STRUCTURE_PREFIXES: tuple[str, ...] = (
    "don_hi_",
    "don_lo_",
    "near_swing_hi_",
    "near_swing_lo_",
    "dbl_bot_",
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
    if not parts or len(parts) > 3:
        return False
    return all(atom_is_allowed(p) for p in parts)


def _has_structure_atom(name: str) -> bool:
    return any(
        tok.startswith(p)
        for tok in name.split("&")
        for p in _STRUCTURE_PREFIXES
    )


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

    Grind 1% 2-atoms are first (all lookbacks) so a mid-drain farm
    (eligible leftovers from the 3×3 recipe, or a farm still chewing
    #42 WIDE 2-atoms) mints higher-participation names on the next dry
    refill instead of more mean-reversion clones. Wide / short-MA
    continuation follows, then legacy 2026-09-11 families, the
    2026-09-12 near-level pass, then leftover TREND / ema_stack /
    3-atom families. Dip×support and short-horizon mom stay on the
    frozen 3×3. No WaveTrend, no MFI.
    """
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
        if not _has_structure_atom(cand):
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
