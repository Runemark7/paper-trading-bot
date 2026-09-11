"""Auto-refill never-tested discovery names when the leftover drain runs dry.

Fail-once parks rejects forever, so a static ``generate_universe()`` list
eventually has eligible=0. This module walks a **bounded** combinatorial
recipe of allowed OHLC structure atoms ANDed with existing 5m dip/mom/sma
filters, skips near-duplicates of champions / graduated / discovery_log
fails, and appends the next handful to ``discovery_extended.json`` on the
paper-state PVC. Tournament then drains those names under the existing
2 / 120s cycle budget. No human PR per batch.

The static universe stays inside ``UNIVERSE_TARGET_MAX`` (~40–120). The
sidecar is the pending queue: after a refill, never-tested extras are one
``DISCOVERY_REFILL_BATCH_SIZE`` handful, not thousands of clones. Recipe
generation is string-only (no history load) so the 1 CPU / 1.5GiB sidecar
does not OOM.
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

# Existing 5m filters (already in the static universe as standalone atoms).
DIP_FILTERS: tuple[str, ...] = (
    "dip_6b_lt2pc",
    "dip_12b_lt3pc",
    "dip_24b_lt5pc",
)
MOM_FILTERS: tuple[str, ...] = (
    "mom_6b_gt2pc",
    "mom_12b_gt3pc",
    "mom_24b_gt5pc",
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
# 6 bars = 30m on 5m; 72 bars = 6h. Distinct from a 1-bar param tweak.
STRUCTURE_NS: tuple[int, ...] = (6, 12, 18, 24, 30, 36, 42, 48, 54, 60, 66, 72)

# Hard refuse: chart-pattern zoo, MFI, WaveTrend clones, MTF wrappers.
_REFUSED_RE = re.compile(
    r"head_and_shoulders|flag|triangle|engulfing|mfi_|wt_|sommi|gold_dot|"
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


def iter_recipe_names() -> Iterator[str]:
    """Deterministic bounded stream. Not a full cartesian of every atom.

    Dip tags support (don_lo / near_swing_lo); mom tags breakout
    (don_hi / near_swing_hi); dbl_bot is the one pattern family.
    Trend filters AND onto those structure atoms. No WaveTrend, no MFI.
    """
    for n in STRUCTURE_NS:
        for dip in DIP_FILTERS:
            yield f"{dip}&don_lo_{n}"
            yield f"{dip}&near_swing_lo_{n}"
        for mom in MOM_FILTERS:
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
        for dip in DIP_FILTERS:
            yield f"{dip}&don_lo_{n}&sma_abv_50"
            yield f"{dip}&near_swing_lo_{n}&sma_abv_50"
        for mom in MOM_FILTERS:
            yield f"{mom}&don_hi_{n}&sma_abv_50"


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
