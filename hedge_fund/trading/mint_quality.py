"""Shared mint gate for redundant thresholds and unreachable mom/dip steps.

One parser turns an atom into ``(family, params, op, threshold)``. Recipe,
refill, densify, and the claim path all reject a stack whose atoms are the
same indicator at two same-direction bounds (``rsi_14_>50`` already implies
``rsi_14_>45``). Opposite bounds stay only when the parser evaluates them
and the band is non-empty.

Momentum and dip percents are capped against the ~99th percentile move
over that many 5m bars. The cap is measured on the BTC/ETH tape when that
file is on disk; otherwise a static table that still admits the recipe's
own thresholds and cuts densify extremes such as ``mom_18b_gt16pc``.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from hedge_fund.paths import state_root

REASON_REDUNDANT = "redundant_threshold"
REASON_EMPTY_BAND = "empty_band"
REASON_UNSUPPORTED_BAND = "unsupported_band"
REASON_UNREACHABLE = "unreachable_threshold"

DISCOVERY_MINT_SKIPS = "discovery_mint_skips.json"

# Surface forms parse_strategy actually evaluates (not the silent False fallback).
_RSI_GT_RE = re.compile(r"^rsi_(\d+)_>_?(\d+)(?:_<_?(\d+))?$")
_RSI_LT_RE = re.compile(r"^rsi_(\d+)_<_?(\d+)$")
_RSI_SHORT_RE = re.compile(r"^rsi_(\d+)_(\d+)$")
_MOM_DIP_RE = re.compile(r"^(mom|dip)_(\d+)b_(gt|lt)(\d+)pc$")
_HTF_RE = re.compile(r"^(h[14])_(ema|sma)_abv_(\d+)$")
_MA_RE = re.compile(r"^(sma_abv|ema_abv)_(\d+)$")
_STACK_RE = re.compile(r"^(sma_stack|ema_stack)(?:_([\d_]+))?$")
_STRUCT_RE = re.compile(
    r"^(don_hi|don_lo|near_swing_hi|near_swing_lo|dbl_bot|dbl_top)_(\d+)$"
)
_BB_RE = re.compile(r"^bb_(lower|upper)_(\d+)_(\d+)$")
_VOL_RE = re.compile(r"^vol_lowsm_(\d+)_(\d+)$")

# Lookback (5m bars) → inclusive max percent. Stands in for the ~99th
# percentile absolute move when crypto_history_5m / the live tape is absent.
# Short lookbacks match the recipe ceiling (``mom_18b_gt8pc`` stays,
# ``mom_18b_gt16pc`` does not). Longer lookbacks scale with sqrt(time).
_MIN_EMPIRICAL_RETURNS = 500

_cap_cache: dict[tuple, dict[int, float | None]] = {}
_closes_cache: dict[tuple, list] = {}


@dataclass(frozen=True)
class AtomParts:
    """One atom.

    ``bounds`` are normalized for dominance: ``gt`` means a larger threshold
    is stricter (mom percent, dip magnitude, RSI lower bound). ``lt`` means
    a smaller threshold is stricter (RSI upper bound). ``params`` omit those
    thresholds, so ``mom_18b_gt2pc`` and ``mom_18b_gt4pc`` share a key.
    """

    family: str
    params: tuple
    op: str
    threshold: float | tuple | None
    bounds: tuple[tuple[str, float], ...]
    parser_supported: bool
    raw: str


def parse_atom(atom: str) -> tuple[str, tuple, str, float | tuple | None] | None:
    """Parse one atom into ``(family, params, op, threshold)``.

    Unknown atoms return None. A band atom (``rsi_14_>45_<60``) uses
    ``op="band"`` and ``threshold=(lo, hi)``.
    """
    parts = inspect_atom(atom)
    if parts is None:
        return None
    return (parts.family, parts.params, parts.op, parts.threshold)


def inspect_atom(atom: str) -> AtomParts | None:
    """Full parse, including every bound and whether the live parser evaluates it."""
    text = atom.strip()
    if not text:
        return None

    m = _RSI_GT_RE.match(text)
    if m:
        period = int(m.group(1))
        lo = float(m.group(2))
        bounds: list[tuple[str, float]] = [("gt", lo)]
        if m.group(3):
            bounds.append(("lt", float(m.group(3))))
        return _pack("rsi", (period,), bounds, True, text)

    m = _RSI_LT_RE.match(text)
    if m:
        # ``rsi_N_<T`` is a real upper bound (parser: RSI <= T).
        return _pack("rsi", (int(m.group(1)),), [("lt", float(m.group(2)))], True, text)

    m = _RSI_SHORT_RE.match(text)
    if m:
        return _pack("rsi", (int(m.group(1)),), [("gt", float(m.group(2)))], True, text)

    m = _MOM_DIP_RE.match(text)
    if m:
        kind, lookback, _word, pct = m.group(1), int(m.group(2)), m.group(3), float(m.group(4))
        # Dip's ``lt`` is a decline magnitude: a larger percent is the stricter atom.
        return _pack(kind, (lookback,), [("gt", pct)], True, text)

    m = _HTF_RE.match(text)
    if m:
        family = f"{m.group(1)}_{m.group(2)}_abv"
        return _pack(family, (int(m.group(3)),), [], True, text)

    m = _MA_RE.match(text)
    if m:
        return _pack(m.group(1), (int(m.group(2)),), [], True, text)

    m = _STACK_RE.match(text)
    if m:
        raw_periods = m.group(2)
        periods = tuple(int(p) for p in raw_periods.split("_") if p) if raw_periods else ()
        return _pack(m.group(1), periods, [], True, text)

    m = _STRUCT_RE.match(text)
    if m:
        return _pack(m.group(1), (int(m.group(2)),), [], True, text)

    m = _BB_RE.match(text)
    if m:
        return _pack("bb", (m.group(1), int(m.group(2)), int(m.group(3))), [], True, text)

    m = _VOL_RE.match(text)
    if m:
        return _pack("vol_lowsm", (int(m.group(1)), int(m.group(2))), [], True, text)

    if text in {"wt_cross_up_os", "wt_below_os", "wt_cross_down_ob", "sma_stack"}:
        return _pack(text, (), [], True, text)
    return None


def _pack(
    family: str,
    params: tuple,
    bounds: list[tuple[str, float]] | tuple[tuple[str, float], ...],
    parser_supported: bool,
    raw: str,
) -> AtomParts:
    frozen = tuple(bounds)
    if len(frozen) == 0:
        op = "abv" if family == "sma_abv" or family == "ema_abv" or family.endswith("_abv") else "eq"
        threshold = None
    elif len(frozen) == 1:
        op, threshold = frozen[0][0], frozen[0][1]
    else:
        op = "band"
        threshold = tuple(thr for _op, thr in frozen)
    return AtomParts(family, params, op, threshold, frozen, parser_supported, raw)


def _merged_interval(bound_groups: Sequence[Sequence[tuple[str, float]]]) -> tuple[float, float]:
    lo = -math.inf
    hi = math.inf
    for bounds in bound_groups:
        for op, thr in bounds:
            if op == "gt":
                lo = max(lo, thr)
            elif op == "lt":
                hi = min(hi, thr)
    return lo, hi


def group_bound_reason(group: Sequence[AtomParts]) -> str | None:
    """Why this same-family group cannot be leased, or None if it is a real stack."""
    if not group:
        return None
    lo, hi = _merged_interval([atom.bounds for atom in group])
    if lo >= hi:
        return REASON_EMPTY_BAND
    if len(group) == 1:
        return None
    if not all(atom.parser_supported for atom in group):
        return REASON_UNSUPPORTED_BAND
    full = (lo, hi)
    for index in range(len(group)):
        others = [atom.bounds for j, atom in enumerate(group) if j != index]
        if _merged_interval(others) == full:
            return REASON_REDUNDANT
    return None


def redundant_bound_reason(name: str) -> str | None:
    """``redundant_threshold``, ``empty_band``, ``unsupported_band``, or None.

    Atoms that do not parse are ignored here; ``name_is_parseable`` still
    refuses them. Two copies of ``h1_ema_abv_50`` are redundant. ``sma_abv_30``
    and ``sma_abv_50`` are different params and stay.
    """
    if not name or not isinstance(name, str):
        return None
    groups: dict[tuple, list[AtomParts]] = {}
    for tok in name.split("&"):
        tok = tok.strip()
        if not tok:
            continue
        atom = inspect_atom(tok)
        if atom is None:
            continue
        groups.setdefault((atom.family, atom.params), []).append(atom)
    for group in groups.values():
        reason = group_bound_reason(group)
        if reason:
            return reason
    return None


def _static_cap(lookback: int) -> int:
    n = max(1, int(lookback))
    if n <= 6:
        return 4
    if n <= 12:
        return 6
    if n <= 36:
        return 8
    if n <= 48:
        return 10
    if n <= 96:
        return 14
    est = int(math.ceil((2.0 * math.sqrt(n)) / 2.0) * 2)
    return max(14, min(est, 40))


def _history_token() -> tuple:
    root = state_root()
    json_path = root / "crypto_history_5m.json"
    if json_path.is_file():
        st = json_path.stat()
        return ("json", str(json_path), int(st.st_mtime_ns), int(st.st_size))
    tapes = []
    for slug in ("BTC_USDT", "ETH_USDT"):
        path = root / "live_tape" / f"{slug}.npy"
        if path.is_file():
            st = path.stat()
            tapes.append((str(path), int(st.st_mtime_ns), int(st.st_size)))
    if tapes:
        return ("npy", tuple(tapes))
    return ("static",)


def _load_closes(token: tuple) -> list:
    cached = _closes_cache.get(token)
    if cached is not None:
        return cached
    series: list = []
    if token[0] == "json":
        path = Path(token[1])
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            for key in ("BTC/USDT", "ETH/USDT", "BTC_USDT", "ETH_USDT"):
                rows = data.get(key)
                if not isinstance(rows, list) or len(rows) < 2:
                    continue
                closes = []
                for row in rows:
                    if isinstance(row, (list, tuple)) and len(row) >= 5:
                        try:
                            closes.append(float(row[4]))
                        except (TypeError, ValueError):
                            continue
                if len(closes) >= 2:
                    series.append(closes)
    elif token[0] == "npy":
        import numpy as np

        for path_s, _mtime, _size in token[1]:
            try:
                arr = np.load(path_s, mmap_mode="r")
                closes = [float(x) for x in arr["close"]]
            except (OSError, ValueError, KeyError):
                continue
            if len(closes) >= 2:
                series.append(closes)
    _closes_cache[token] = series
    return series


def _percentile_99(closes: Sequence[float], lookback: int) -> float | None:
    n = int(lookback)
    if n < 1 or len(closes) <= n + _MIN_EMPIRICAL_RETURNS:
        return None
    import numpy as np

    arr = np.asarray(closes, dtype=np.float64)
    prev = arr[:-n]
    cur = arr[n:]
    ok = (prev > 0) & np.isfinite(prev) & np.isfinite(cur)
    if int(ok.sum()) < _MIN_EMPIRICAL_RETURNS:
        return None
    rets = np.abs(cur[ok] / prev[ok] - 1.0) * 100.0
    if rets.size < _MIN_EMPIRICAL_RETURNS:
        return None
    return float(np.percentile(rets, 99))


def _empirical_cap(lookback: int) -> float | None:
    token = _history_token()
    if token[0] == "static":
        return None
    per_token = _cap_cache.setdefault(token, {})
    if lookback in per_token:
        return per_token[lookback]
    caps = []
    for closes in _load_closes(token):
        p99 = _percentile_99(closes, lookback)
        if p99 is not None:
            caps.append(p99)
    value = max(caps) if caps else None
    per_token[lookback] = value
    return value


def max_move_pct(lookback: int) -> float:
    """Inclusive percent cap for a mom/dip threshold at this 5m lookback.

    History wins when BTC or ETH closes are on disk (the looser of the two
    99th percentiles, so a move that either symbol actually prints counts).
    Otherwise the static table.
    """
    empirical = _empirical_cap(int(lookback))
    if empirical is None:
        return float(_static_cap(lookback))
    return float(empirical)


def clear_move_cap_cache() -> None:
    """Drop the tape-derived cap cache. Tests change ``PAPER_STATE`` under us."""
    _cap_cache.clear()
    _closes_cache.clear()


def unreachable_threshold_reason(name: str) -> str | None:
    """``unreachable_threshold`` when a mom/dip percent sits above the cap."""
    if not name or not isinstance(name, str):
        return None
    for tok in name.split("&"):
        m = _MOM_DIP_RE.match(tok.strip())
        if not m:
            continue
        lookback = int(m.group(2))
        pct = int(m.group(4))
        if pct > max_move_pct(lookback):
            return REASON_UNREACHABLE
    return None


def mint_block_reason(name: str) -> str | None:
    """Why this name must not be minted or leased. None when it is eligible."""
    return redundant_bound_reason(name) or unreachable_threshold_reason(name)


def _atoms(name: str) -> list[str]:
    return [p.strip() for p in name.split("&") if p.strip()]


def count_mint_blocks(
    names: Iterable[str],
    *,
    tested: Iterable[str] | None = None,
) -> dict[str, int]:
    """Count untested names each rule would drop. Tested history is ignored."""
    parked = {n for n in (tested or ()) if n}
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for name in names:
        if not name or name in parked or name in seen:
            continue
        seen.add(name)
        reason = mint_block_reason(name)
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def mint_skips_path() -> Path:
    return state_root() / DISCOVERY_MINT_SKIPS


def load_mint_skips() -> dict[str, str]:
    """Name → reason. Missing file is an empty skip set. Not a tested-name index."""
    path = mint_skips_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    raw = data.get("names") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for name, reason in raw.items():
        if isinstance(name, str) and name and isinstance(reason, str) and reason:
            out[name] = reason
    return out


def save_mint_skips(skips: dict[str, str]) -> None:
    """Atomic replace. Caller holds ``paper_state_lock('discovery')``."""
    path = mint_skips_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "v": 1,
        "paper_only": True,
        "names": {name: skips[name] for name in sorted(skips)},
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def record_untested_mint_skips(
    names: Iterable[str],
    already: Iterable[str],
) -> dict[str, int]:
    """Mark untested violators skipped. Does not write the tested-name index or the log.

    ``already`` is tested / pooled / graduated names; those stay untouched.
    Returns counts by reason for names that violate (including ones already skipped).
    """
    parked = {n for n in already if n}
    counts: dict[str, int] = {}
    skips = load_mint_skips()
    changed = False
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not name or name in parked or name in seen:
            continue
        seen.add(name)
        reason = mint_block_reason(name)
        if not reason:
            continue
        counts[reason] = counts.get(reason, 0) + 1
        if skips.get(name) != reason:
            skips[name] = reason
            changed = True
    if changed:
        save_mint_skips(skips)
    return counts
