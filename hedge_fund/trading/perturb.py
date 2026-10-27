"""One-at-a-time parameter neighbours of a strategy name.

Each numeric parameter (lookback, percent threshold, moving-average
length) is moved by ±10% and ±20% and rounded onto the same grid the
mint already uses (lookbacks in steps of 6, percents in steps of 2,
MA lengths via ``_round_period``). A step that rounds back to the
original value is not a neighbour. A neighbour that the parser rejects,
or that the mint bans (mom∧dip, structure lookback > 96, redundant
thresholds), is not a neighbour either.

The full cartesian product is not used: that would score a new search,
not the local spike test.
"""
from __future__ import annotations

import re

from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.trading.constants import NEIGHBOR_RELATIVE_STEPS
from hedge_fund.trading.discovery_guard import lookback_too_expensive_reason
from hedge_fund.trading.mint_quality import mint_block_reason
from hedge_fund.trading.refill import name_has_mom_gt_and_dip, name_is_parseable
from hedge_fund.trading.universe import _round_period

_DIP_MOM = re.compile(r"^(mom|dip)_(\d+)b_(gt|lt)(\d+)pc$")
_HTF = re.compile(r"^(h[14])_(ema|sma)_abv_(\d+)$")
_MA = re.compile(r"^(sma_abv|ema_abv)_(\d+)$")
_STACK = re.compile(r"^(sma_stack|ema_stack)_(\d+(?:_\d+)*)$")
_RSI = re.compile(r"^rsi_(\d+)_([<>])(\d+)(?:_([<>])(\d+))?$")
_STRUCT = re.compile(
    r"^(don_hi|don_lo|near_swing_hi|near_swing_lo|dbl_bot|dbl_top)_(\d+)$"
)


def _step_round(raw: float, step: int, lo: int, hi: int | None) -> int | None:
    n = int(round(float(raw) / step) * step)
    if n < lo:
        return None
    if hi is not None and n > hi:
        return None
    return n


def _ma_round(raw: float) -> int | None:
    n = int(round(raw))
    if n < 5:
        return None
    rounded = _round_period(n)
    if rounded < 5:
        return None
    return int(rounded)


def _candidates(value: int, *, kind: str) -> list[int]:
    found: list[int] = []
    seen = {int(value)}
    for frac in NEIGHBOR_RELATIVE_STEPS:
        for sign in (-1.0, 1.0):
            raw = int(value) * (1.0 + sign * float(frac))
            if kind == "lookback":
                rounded = _step_round(raw, 6, 6, None)
            elif kind == "percent":
                rounded = _step_round(raw, 2, 2, 40)
            elif kind == "ma":
                rounded = _ma_round(raw)
            elif kind == "rsi_thr":
                rounded = _step_round(raw, 5, 5, 95)
            elif kind == "struct":
                rounded = _step_round(raw, 6, 6, 96)
            else:
                rounded = None
            if rounded is None or rounded in seen:
                continue
            seen.add(rounded)
            found.append(rounded)
    return found


def _replace_atom(atom: str) -> list[str]:
    """Neighbour atoms that differ in exactly one rounded parameter."""
    m = _DIP_MOM.match(atom)
    if m:
        kind, lb, op, pct = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        out = [f"{kind}_{n}b_{op}{pct}pc" for n in _candidates(lb, kind="lookback")]
        out += [f"{kind}_{lb}b_{op}{n}pc" for n in _candidates(pct, kind="percent")]
        return out
    m = _HTF.match(atom)
    if m:
        return [
            f"{m.group(1)}_{m.group(2)}_abv_{n}"
            for n in _candidates(int(m.group(3)), kind="ma")
        ]
    m = _MA.match(atom)
    if m:
        return [f"{m.group(1)}_{n}" for n in _candidates(int(m.group(2)), kind="ma")]
    m = _STACK.match(atom)
    if m:
        parts = [int(x) for x in m.group(2).split("_") if x]
        out = []
        for i, value in enumerate(parts):
            for n in _candidates(value, kind="ma"):
                nxt = list(parts)
                nxt[i] = n
                out.append(f"{m.group(1)}_{'_'.join(str(p) for p in nxt)}")
        return out
    m = _RSI.match(atom)
    if m:
        period, op1, thr1 = int(m.group(1)), m.group(2), int(m.group(3))
        out = []
        for n in _candidates(period, kind="ma"):
            atom_n = f"rsi_{n}_{op1}{thr1}"
            if m.group(4):
                atom_n += f"_{m.group(4)}{m.group(5)}"
            out.append(atom_n)
        for n in _candidates(thr1, kind="rsi_thr"):
            atom_n = f"rsi_{period}_{op1}{n}"
            if m.group(4):
                atom_n += f"_{m.group(4)}{m.group(5)}"
            out.append(atom_n)
        if m.group(5):
            for n in _candidates(int(m.group(5)), kind="rsi_thr"):
                out.append(f"rsi_{period}_{op1}{thr1}_{m.group(4)}{n}")
        return out
    m = _STRUCT.match(atom)
    if m:
        return [f"{m.group(1)}_{n}" for n in _candidates(int(m.group(2)), kind="struct")]
    return []


def _acceptable(name: str) -> bool:
    if name_has_mom_gt_and_dip(name):
        return False
    if lookback_too_expensive_reason(name):
        return False
    if mint_block_reason(name):
        return False
    if not name_is_parseable(name):
        return False
    try:
        parse_strategy(name)
    except Exception:
        return False
    return True


def neighbor_names(name: str) -> list[str]:
    """Distinct valid one-parameter neighbours, original excluded."""
    if not isinstance(name, str) or not name.strip():
        return []
    atoms = [part.strip() for part in name.split("&") if part.strip()]
    found: list[str] = []
    seen = {name}
    for index, atom in enumerate(atoms):
        for repl in _replace_atom(atom):
            parts = list(atoms)
            parts[index] = repl
            candidate = "&".join(parts)
            if candidate in seen:
                continue
            if not _acceptable(candidate):
                continue
            seen.add(candidate)
            found.append(candidate)
    return found
