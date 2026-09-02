"""CLI wrapper around hedge_fund.trading.universe.

Does not emit daily()/h1()/m5() or MFI: the live cycle is a single 5m
close series without a volume-aware MFI path. Universe is an explicit
~50-name list (not thousands of combinatorial clones).
"""
from __future__ import annotations

from hedge_fund.trading.universe import generate_universe

if __name__ == "__main__":
    u = generate_universe()
    print(f"Total strategy rules generated: {len(u):,}")
