"""Fast candidate backtesting & replenishment engine.

Evaluates 5m history via the tournament gate
(scripts/tournament_engine.replenish_and_evaluate). A pass is proposed,
not seated. Runs while the universe has untested names.
4h history does not admit.
"""
from __future__ import annotations

import json

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.tournament_engine import replenish_and_evaluate


def replenish_pool(stride: int = 1, recent_bars: int = 0) -> dict:
    _ = stride, recent_bars  # stride sweeps no longer admit; native 5m only
    res = replenish_and_evaluate()
    res = dict(res)
    # Seating is manual. A qualified name stays a proposal.
    res["replenished"] = False
    res["admitted_new_count"] = 0
    proposed = [name for name in (res.get("proposed") or []) if isinstance(name, str)]
    res["proposed"] = proposed
    if proposed:
        res["reason"] = "qualified names are proposed, awaiting approval"
    else:
        res.setdefault("reason", res.get("reason") or "no new 5m-qualified names")
    return res


if __name__ == "__main__":
    print(json.dumps(replenish_pool(), indent=2))
