"""Fast candidate backtesting & replenishment engine.

Does not admit from 5m history. Delegates to the 4h tournament gate
(scripts/tournament_engine.replenish_and_evaluate). Replenish only runs
when the pool is below MAX_ACTIVE_CHAMPIONS (20).
"""
from __future__ import annotations

import json

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hedge_fund.trading.constants import MAX_ACTIVE_CHAMPIONS
from scripts.tournament_engine import replenish_and_evaluate


def replenish_pool(stride: int = 1, recent_bars: int = 0) -> dict:
    _ = stride, recent_bars  # 5m stride sweeps no longer admit
    res = replenish_and_evaluate()
    res = dict(res)
    res["replenished"] = res.get("admitted_new_count", 0) > 0
    res["target"] = MAX_ACTIVE_CHAMPIONS
    if not res["replenished"]:
        res.setdefault("reason", res.get("reason") or "no 4h-qualified free-slot admits")
    return res


if __name__ == "__main__":
    print(json.dumps(replenish_pool(), indent=2))
