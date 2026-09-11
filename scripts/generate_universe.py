"""CLI wrapper around hedge_fund.trading.universe.

Does not emit daily()/h1()/m5() or MFI: the live cycle is a single 5m
series without a volume-aware MFI path. Structure names (Donchian / swing /
double bottom) and WaveTrend (HLC3 green-dot) are OHLC on that same series.
Static universe is an explicit list (not thousands of combinatorial clones),
inside UNIVERSE_TARGET_MAX. When leftovers run dry, tournament auto-refills
discovery_extended.json from a bounded structure-AND recipe — not this CLI.
"""
from __future__ import annotations

from hedge_fund.trading.universe import generate_universe

if __name__ == "__main__":
    u = generate_universe()
    print(f"Total strategy rules generated: {len(u):,}")
