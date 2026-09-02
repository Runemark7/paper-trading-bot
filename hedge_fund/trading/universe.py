"""Combinatorial paper-strategy universe.

The live cycle fetches a single 5m OHLCV series (no 1h/4h bars). Close-only
atoms still parse without highs/lows. Structure atoms (Donchian / swing /
double bottom) use high/low from those same klines — this generator does
not emit daily()/h1()/m5() wrappers, MFI, H&S, flags, triangles, FVGs,
order-blocks, or standalone dbl_top longs: those would be silent lies, a
kitchen-sink arena, or a short we do not trade.

Amendment 2026-09-01: explicit ~50-name universe instead of ~3500
combinatorial clones. Amendment 2026-09-03: a handful of OHLC structure
AND-gates, then double-bottom pattern names, still inside UNIVERSE_TARGET_MAX.
Trend / breakout / momentum stay as sma_stack/sma_abv, don_hi_*, mom_* —
not a second stack. Near-duplicate keys collapse tiny param tweaks.
Lookbacks in names (e.g. dip_24b) are bar counts: on 5m, 24 bars = 2 hours.
"""
from __future__ import annotations

import re

# Documented size (tests lock this band). Was 3546 combinatorial names.
UNIVERSE_TARGET_MIN = 40
UNIVERSE_TARGET_MAX = 120


def near_duplicate_key(name: str) -> str:
    """Cheap canonical key so ``sma_stack_5_20_50`` and ``sma_stack_5_21_51`` collide.

    Used to skip re-testing graduated/active names' near-twins without a
    giant rewrite. AND-combinations are sorted so ``a&b`` matches ``b&a``.
    """
    parts = [p.strip() for p in name.split("&") if p.strip()]
    return "&".join(sorted(_canon_atom(p) for p in parts))


def _round_period(n: int) -> int:
    if n < 10:
        return 5 if n < 8 else 10
    if n < 40:
        return int(round(n / 5.0) * 5) or 5
    return int(round(n / 10.0) * 10) or 10


def _canon_atom(atom: str) -> str:
    m = re.match(r"^(sma_stack|ema_stack)_([\d_]+)$", atom)
    if m:
        periods = [_round_period(int(x)) for x in m.group(2).split("_") if x]
        return f"{m.group(1)}_{'_'.join(map(str, periods))}"
    m = re.match(r"^(sma_abv|ema_abv)_(\d+)$", atom)
    if m:
        return f"{m.group(1)}_{_round_period(int(m.group(2)))}"
    m = re.match(r"^rsi_(\d+)_>(\d+)(?:_<(\d+))?$", atom)
    if m:
        p = _round_period(int(m.group(1)))
        th = int(round(int(m.group(2)) / 5.0) * 5)
        if m.group(3):
            ov = int(round(int(m.group(3)) / 5.0) * 5)
            return f"rsi_{p}_>{th}_<{ov}"
        return f"rsi_{p}_>{th}"
    m = re.match(r"^(mom|dip)_(\d+)b_(gt|lt)(\d+)pc$", atom)
    if m:
        lb = int(round(int(m.group(2)) / 6.0) * 6) or 6
        thr = int(round(int(m.group(4)) / 2.0) * 2) or 2
        return f"{m.group(1)}_{lb}b_{m.group(3)}{thr}pc"
    m = re.match(r"^bb_(lower|upper)_(\d+)_(\d+)$", atom)
    if m:
        return f"bb_{m.group(1)}_{_round_period(int(m.group(2)))}_{m.group(3)}"
    m = re.match(r"^vol_lowsm_(\d+)_(\d+)$", atom)
    if m:
        return f"vol_lowsm_{_round_period(int(m.group(1)))}_{_round_period(int(m.group(2)))}"
    m = re.match(r"^(don_hi|don_lo|near_swing_hi|near_swing_lo|dbl_bot|dbl_top)_(\d+)$", atom)
    if m:
        n = int(round(int(m.group(2)) / 6.0) * 6) or 6
        return f"{m.group(1)}_{n}"
    if atom == "sma_stack":
        return "sma_stack_5_25_50"  # default 7,25,50 rounds near 5/25/50
    return atom


def generate_universe() -> list[str]:
    """Explicit 5m universe — well-spaced names, not a cartesian product."""
    universe: set[str] = set()

    stacks = [
        (5, 20, 50),
        (7, 25, 50),
        (8, 21, 55),
        (10, 20, 50),
        (13, 34, 89),
        (20, 50, 100),
    ]
    for c in stacks:
        s_name = "_".join(map(str, c))
        universe.add(f"sma_stack_{s_name}")
        universe.add(f"ema_stack_{s_name}")

    for ma in (20, 50, 100, 200):
        universe.add(f"sma_abv_{ma}")
    for ma in (20, 50, 100):
        universe.add(f"ema_abv_{ma}")

    universe.update({
        "rsi_7_>45",
        "rsi_14_>50",
        "rsi_14_>40",
        "rsi_21_>50",
        "rsi_14_>50_<70",
        "rsi_14_>40_<70",
        "mom_6b_gt2pc",
        "mom_12b_gt3pc",
        "mom_24b_gt5pc",
        "dip_6b_lt2pc",
        "dip_12b_lt3pc",
        "dip_24b_lt5pc",
        "bb_lower_20_2",
        "bb_upper_20_2",
        "vol_lowsm_20_90",
        "sma_stack",  # live-empty-pool fallback
    })

    # A handful of AND gates that are meaningfully different — not every combo.
    universe.update({
        "dip_6b_lt2pc&sma_abv_50",
        "dip_12b_lt3pc&sma_abv_100",
        "mom_12b_gt3pc&sma_abv_50",
        "mom_12b_gt3pc&sma_stack_7_25_50",
        "rsi_14_>50&sma_abv_50",
        "rsi_14_>50&sma_stack_7_25_50",
        "bb_lower_20_2&sma_abv_50",
        "dip_6b_lt2pc&ema_abv_50",
        "rsi_14_>40_<70&sma_abv_50",
        "mom_24b_gt5pc&sma_abv_200",
        "rsi_21_>50&sma_abv_100",
        "dip_12b_lt3pc&ema_abv_50",
        "bb_lower_20_2&sma_stack_20_50_100",
        "mom_6b_gt2pc&sma_abv_20",
        "rsi_7_>45&sma_abv_50",
    })

    # Structure atoms (OHLC Donchian / fractal swing) — handful of AND gates,
    # not a cartesian product. See hedge_fund/signals/structure.py.
    universe.update({
        "don_hi_24",
        "dip_6b_lt2pc&near_swing_lo_12",
        "dip_12b_lt3pc&don_lo_24",
        "mom_12b_gt3pc&don_hi_24",
        "sma_abv_50&don_hi_24",
        "rsi_14_>50&don_hi_24",
        "dip_6b_lt2pc&don_lo_24",
        "ema_abv_50&don_hi_24",
        "sma_stack_20_50_100&don_hi_24",
    })

    # Double bottom (pattern). dbl_top is parsed but not listed as a long.
    # Trend/breakout/momentum already exist as sma_* / don_hi_* / mom_* —
    # do not duplicate those families here.
    universe.update({
        "dbl_bot_12",
        "dbl_bot_12&sma_abv_50",
        "dbl_bot_12&don_lo_24",
        "dbl_bot_12&sma_stack_20_50_100",
    })

    return sorted(universe)


def untested_candidates(blocked_names: set[str], universe: list[str] | None = None) -> list[str]:
    """Universe names not in the pool/graduated, skipping near-duplicates of blocked."""
    if universe is None:
        universe = generate_universe()
    taken_keys = {near_duplicate_key(n) for n in blocked_names}
    out: list[str] = []
    for cand in universe:
        if cand in blocked_names:
            continue
        key = near_duplicate_key(cand)
        if key in taken_keys:
            continue
        out.append(cand)
        taken_keys.add(key)
    return out


def generate_5000_universe() -> list[str]:
    """Deprecated name kept so older scripts import the (now small) universe."""
    return generate_universe()
