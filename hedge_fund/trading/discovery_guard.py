"""Farm ops guards: expensive structure lookbacks and eval timeouts.

These are throughput controls for the Windows discovery worker. They
fail-park a name so the drain can move on. They are **not** OOS gate
changes and do not soften Sharpe / trades / beat-B&H / sma_stack.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from hedge_fund.trading.constants import QUAL_TIMEFRAME, RISK_POLICY

# don_hi / don_lo / near_swing_* / dbl_bot_* (and parsed dbl_top).
# 168-bar leftover structure names are O(n·k) and stalled jensa ~2h.
STRUCTURE_LOOKBACK_ATOM_RE = re.compile(
    r"^(don_hi|don_lo|near_swing_hi|near_swing_lo|dbl_bot|dbl_top)_(\d+)$"
)

DEFAULT_STRUCTURE_LOOKBACK_MAX = 96
DEFAULT_EVAL_TIMEOUT_SECONDS = 600

LOOKBACK_TOO_EXPENSIVE_PREFIX = "lookback_too_expensive"
EVAL_TIMEOUT_PREFIX = "eval_timeout"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def structure_lookback_max() -> int:
    """Hard cap for structure/swing/dbl_bot lookbacks. 0 disables the guard."""
    return _env_int("DISCOVERY_STRUCTURE_LOOKBACK_MAX", DEFAULT_STRUCTURE_LOOKBACK_MAX)


def eval_timeout_seconds() -> int:
    """Per-name wall-clock backstop. 0 disables the timeout."""
    return _env_int("DISCOVERY_EVAL_TIMEOUT_SECONDS", DEFAULT_EVAL_TIMEOUT_SECONDS)


def structure_lookbacks(name: str) -> list[tuple[str, int]]:
    """Return ``(atom, N)`` for structure lookbacks in a ``&``-joined name."""
    if not name or not isinstance(name, str):
        return []
    out: list[tuple[str, int]] = []
    for atom in name.split("&"):
        m = STRUCTURE_LOOKBACK_ATOM_RE.match(atom.strip())
        if m:
            out.append((m.group(1), int(m.group(2))))
    return out


def max_structure_lookback(name: str) -> int | None:
    ns = [n for _, n in structure_lookbacks(name)]
    return max(ns) if ns else None


def lookback_too_expensive_reason(name: str, cap: int | None = None) -> str | None:
    """If any structure lookback exceeds *cap*, return a fail_reason string."""
    limit = structure_lookback_max() if cap is None else int(cap)
    if limit <= 0:
        return None
    worst = max_structure_lookback(name)
    if worst is None or worst <= limit:
        return None
    return f"{LOOKBACK_TOO_EXPENSIVE_PREFIX} {worst}>{limit}"


def eval_timeout_reason(timeout_s: float | int) -> str:
    return f"{EVAL_TIMEOUT_PREFIX} after {int(timeout_s)}s"


def is_ops_park_reason(reason: object) -> bool:
    text = str(reason)
    return text.startswith(LOOKBACK_TOO_EXPENSIVE_PREFIX) or text.startswith(
        EVAL_TIMEOUT_PREFIX
    )


def is_ops_park_record(row: dict) -> bool:
    if row.get("ops_park"):
        return True
    reasons = row.get("fail_reasons") if isinstance(row.get("fail_reasons"), list) else []
    return any(is_ops_park_reason(r) for r in reasons)


def ops_fail_record(name: str, reason: str) -> dict:
    """qualified=false discovery_log row. Fail-once parks forever."""
    return {
        "strategy": name,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "timeframe": QUAL_TIMEFRAME,
        "risk_policy": RISK_POLICY,
        "train_pnl": 0.0,
        "test_pnl": 0.0,
        "sharpe": 0.0,
        "win_rate_pct": 0.0,
        "trades": 0,
        "regimes_tested": 0,
        "qualified": False,
        "bh_oos_pnl": None,
        "sma_stack_oos_pnl": None,
        "fail_reasons": [reason],
        "all_windows_nonneg": False,
        "ops_park": True,
    }
