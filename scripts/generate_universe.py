"""CLI wrapper around hedge_fund.trading.universe.

Does not emit daily()/h1()/m5() or MFI: the live cycle is a single 4h
close series without a volume-aware MFI path.
"""
from __future__ import annotations

from hedge_fund.trading.universe import generate_5000_universe

if __name__ == "__main__":
    u = generate_5000_universe()
    print(f"Total strategy rules generated: {len(u):,}")
