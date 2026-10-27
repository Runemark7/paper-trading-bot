"""Pre-registered forward-test kill criteria. Flag only — never retire.

Alexander, 2026-10-27: a champion is eligible for the check after
max(90 days, 20 closed trades), i.e. once it is at least 90 days old
and has at least 20 closed paper trades. It is flagged when live trade
Sharpe < 0 or live max drawdown > 1.5 × the backtest max drawdown stored
at admission. Retirement stays a human decision.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.backtest.strategies import _sharpe
from hedge_fund.paths import state_root
from hedge_fund.trading.constants import (
    FORWARD_MAX_DD_MULTIPLE,
    FORWARD_MIN_CLOSED_TRADES,
    FORWARD_MIN_DAYS,
    FORWARD_SHARPE_FLOOR,
    GATE_RULES,
    PAPER_START_CASH,
)


def registration_for(record: dict, admitted_at: str) -> dict:
    """The criteria frozen onto the champion row at admission."""
    dd = record.get("backtest_max_dd")
    if dd is None and isinstance(record.get("gate_v4"), dict):
        dd = record["gate_v4"].get("backtest_max_dd")
    try:
        dd_f = None if dd is None else float(dd)
    except (TypeError, ValueError):
        dd_f = None
    if dd_f is not None and not math.isfinite(dd_f):
        dd_f = None
    return {
        "rule": GATE_RULES,
        "registered_at": admitted_at,
        "min_days": FORWARD_MIN_DAYS,
        "min_closed_trades": FORWARD_MIN_CLOSED_TRADES,
        "live_sharpe_floor": FORWARD_SHARPE_FLOOR,
        "max_dd_multiple": FORWARD_MAX_DD_MULTIPLE,
        "backtest_max_dd": dd_f,
        "action": "flag_only",
    }


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _max_drawdown(equity: list[float]) -> float | None:
    if len(equity) < 2:
        return None
    peak = equity[0]
    worst = 0.0
    seen = False
    for value in equity:
        if not math.isfinite(value):
            continue
        seen = True
        if value > peak:
            peak = value
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst if seen else None


def _account_path(name: str) -> Path:
    slug = name.replace("/", "_").replace(":", "_")
    return state_root() / f"trades_{slug}.sqlite"


def _live_metrics(name: str) -> dict:
    """Closed-trade Sharpe and max drawdown from the paper book. Missing → Nones."""
    path = _account_path(name)
    if not path.is_file():
        return {"closed": 0, "sharpe": None, "max_dd": None, "source": "no_db"}
    from hedge_fund.trading.store import TradeStore

    store = TradeStore(path)
    try:
        closed = list(store.trades(closed_only=True))
        snaps = list(store.equity_history())
    except Exception:
        return {"closed": 0, "sharpe": None, "max_dd": None, "source": "unreadable"}
    pnl_pcts = []
    pnls = []
    for row in closed:
        try:
            pct = row["pnl_pct"]
            pnl = row["pnl"]
        except (KeyError, IndexError, TypeError):
            continue
        if pct is not None:
            try:
                pnl_pcts.append(float(pct))
            except (TypeError, ValueError):
                pass
        if pnl is not None:
            try:
                pnls.append(float(pnl))
            except (TypeError, ValueError):
                pass
    sharpe = _sharpe(pnl_pcts) if len(pnl_pcts) >= 2 else (0.0 if pnl_pcts else None)
    equity = []
    for row in snaps:
        try:
            equity.append(float(row["equity"]))
        except (KeyError, TypeError, ValueError):
            continue
    source = "equity_snapshots"
    if len(equity) < 2 and pnls:
        equity = [PAPER_START_CASH]
        for pnl in pnls:
            equity.append(equity[-1] + pnl)
        source = "closed_pnl"
    return {
        "closed": len(closed),
        "sharpe": sharpe,
        "max_dd": _max_drawdown(equity),
        "source": source if equity else "no_equity",
    }


def evaluate_forward_status(champion: dict, *, now: datetime | None = None) -> dict:
    """Status for one champion row. Does not write and does not retire."""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    reg = champion.get("forward_test") if isinstance(champion, dict) else None
    name = champion.get("name") if isinstance(champion, dict) else None
    base = {
        "action": "flag_only",
        "min_days": FORWARD_MIN_DAYS,
        "min_closed_trades": FORWARD_MIN_CLOSED_TRADES,
        "live_sharpe_floor": FORWARD_SHARPE_FLOOR,
        "max_dd_multiple": FORWARD_MAX_DD_MULTIPLE,
        "flagged": False,
        "reasons": [],
    }
    if not isinstance(reg, dict) or reg.get("action") != "flag_only":
        return {**base, "status": "not_registered", "registered": False}
    live = _live_metrics(name) if isinstance(name, str) and name else {
        "closed": 0, "sharpe": None, "max_dd": None, "source": "no_name",
    }
    since = _parse_ts(champion.get("champion_since")) or _parse_ts(reg.get("registered_at"))
    days = None if since is None else (clock - since).total_seconds() / 86400.0
    closed = int(live["closed"])
    # Eligible once BOTH clocks have fired: the later of day 90 and trade 20.
    eligible = (
        days is not None
        and days >= float(reg.get("min_days") or FORWARD_MIN_DAYS)
        and closed >= int(reg.get("min_closed_trades") or FORWARD_MIN_CLOSED_TRADES)
    )
    reasons: list[str] = []
    sharpe = live["sharpe"]
    live_dd = live["max_dd"]
    try:
        backtest_dd = float(reg.get("backtest_max_dd"))
        if not math.isfinite(backtest_dd):
            backtest_dd = None
    except (TypeError, ValueError):
        backtest_dd = None
    floor = float(reg.get("live_sharpe_floor") if reg.get("live_sharpe_floor") is not None else FORWARD_SHARPE_FLOOR)
    multiple = float(reg.get("max_dd_multiple") or FORWARD_MAX_DD_MULTIPLE)
    if eligible:
        if sharpe is None:
            reasons.append("live_sharpe missing")
        elif sharpe < floor:
            reasons.append(f"live_sharpe {sharpe:.2f} < {floor:.2f}")
        if backtest_dd is None:
            reasons.append("backtest_max_dd missing")
        elif live_dd is None:
            reasons.append("live_max_dd missing")
        elif live_dd > multiple * backtest_dd:
            reasons.append(
                f"live_max_dd {live_dd:.3f} > {multiple:.1f}x backtest_max_dd {backtest_dd:.3f}"
            )
    status = "flagged" if reasons else ("ok" if eligible else "pending")
    return {
        **base,
        "status": status,
        "registered": True,
        "flagged": status == "flagged",
        "reasons": reasons,
        "eligible": eligible,
        "days_live": None if days is None else round(days, 2),
        "closed_trades": closed,
        "live_sharpe": None if sharpe is None else round(float(sharpe), 4),
        "live_max_dd": None if live_dd is None else round(float(live_dd), 4),
        "backtest_max_dd": backtest_dd,
        "equity_source": live.get("source"),
        "rule": reg.get("rule") or GATE_RULES,
    }
