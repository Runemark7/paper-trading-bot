"""Per-open-lot signal + path-in-trade for the paper dashboard.

Honest fields only:

* ``signal`` — re-run this lot's strategy on the latest 5m closes
  (``compute_signal`` / ``parse_strategy``). Still long → ``on`` (stay).
  Not long → ``would_exit`` (same invalidation TradingLoop uses to close).
  No klines / cannot evaluate → ``unknown``. Not a continuous strength.
* ``path`` — where the last mark sits between this lot's stop and 2:1 TP.
  Bottom 30% of (stop→TP) = ``near_stop``, top 30% = ``near_tp``, else ``mid``.
  ``mid`` is the middle of that range, not a third strategy state.
* ``gate`` — only for whole-name dip/mom threshold rules (lookback return vs
  the numeric gate). SMA stacks and other booleans get nothing.
"""

from __future__ import annotations

import math
import re
from typing import Any

from hedge_fund.trading.constants import QUAL_SYMBOLS, QUAL_TIMEFRAME

# Inclusive bands: [0, 0.30] near stop, [0.70, 1] near TP, else mid.
NEAR_STOP_FRAC = 0.30
NEAR_TP_FRAC = 0.70

_SIGNAL_ON = "on"
_SIGNAL_EXIT = "would_exit"
_SIGNAL_UNKNOWN = "unknown"
_PATH_STOP = "near_stop"
_PATH_MID = "mid"
_PATH_TP = "near_tp"


def path_in_trade(mark: float, stop: float, take_profit: float) -> tuple[str, float]:
    """Map mark onto stop→TP. Returns ``(path, path_pct)`` with pct in [0, 1]."""
    span = float(take_profit) - float(stop)
    if span == 0 or not math.isfinite(span):
        return _PATH_MID, 0.5
    pct = (float(mark) - float(stop)) / span
    if not math.isfinite(pct):
        return _PATH_MID, 0.5
    clamped = min(1.0, max(0.0, pct))
    if clamped <= NEAR_STOP_FRAC:
        path = _PATH_STOP
    elif clamped >= NEAR_TP_FRAC:
        path = _PATH_TP
    else:
        path = _PATH_MID
    return path, round(clamped, 4)


def strategy_from_lot(condition: str | None, account: str | None = None) -> str | None:
    """Strategy string this lot was entered under.

    ``entry_condition`` is either a calibration key (``BTC/USDT|5m|sma_stack_long``)
    or a short tag (``sma_stack_long``). Isolated account names are the strategy.
    """
    candidates: list[str] = []
    cond = (condition or "").strip()
    if cond:
        if "|" in cond:
            cond = cond.split("|")[-1]
        for suffix in ("_long", "_flat"):
            if cond.endswith(suffix) and len(cond) > len(suffix):
                cond = cond[: -len(suffix)]
                break
        if cond:
            candidates.append(cond)
    if account:
        name = account.strip()
        if name.startswith("trades_"):
            name = name[len("trades_") :]
        if name and name not in {"legacy"}:
            candidates.append(name)
    return candidates[0] if candidates else None


def lot_signal(strategy: str | None, candles: list | None, symbol: str) -> str:
    """``on`` / ``would_exit`` / ``unknown``. Missing klines stay unknown."""
    if not strategy or not candles or len(candles) < 2:
        return _SIGNAL_UNKNOWN
    try:
        from hedge_fund.signals.momentum import compute_signal

        sig = compute_signal(list(candles), symbol, QUAL_TIMEFRAME, strategy=strategy)
    except Exception:
        return _SIGNAL_UNKNOWN
    return _SIGNAL_ON if sig.direction == "long" else _SIGNAL_EXIT


def mom_or_dip_gate(strategy: str, closes: list[float]) -> dict | None:
    """Lookback return vs threshold for a dip/mom *rule*, else None.

    Does not invent a number for SMA stacks or other boolean predicates.
    """
    expr = (strategy or "").strip()
    m_mom = re.match(r"^mom_(\d+)b?_gt_?(\d+)(?:pc)?$", expr)
    m_dip = re.match(r"^dip_(\d+)b?_lt_?(\d+)(?:pc)?$", expr)
    if m_mom:
        lookback = int(m_mom.group(1))
        val = int(m_mom.group(2))
        threshold = val / 100.0 if val >= 1 else float(val)
        kind = "mom"
    elif m_dip:
        lookback = int(m_dip.group(1))
        val = int(m_dip.group(2))
        threshold = -(val / 100.0) if val >= 1 else -abs(float(val))
        kind = "dip"
    else:
        return None
    if len(closes) <= lookback:
        return None
    from hedge_fund.signals.dynamic import rolling_ret

    ret = rolling_ret(closes, None, lookback)
    if ret != ret:  # NaN
        return None
    return {
        "kind": kind,
        "lookback": lookback,
        "ret": round(float(ret), 4),
        "threshold": round(float(threshold), 4),
    }


def _bars_to_candles(rows: list[dict]) -> list:
    from hedge_fund.data.binance import Candle

    return [
        Candle(
            ts=int(c["t"]),
            open=float(c["o"]),
            high=float(c["h"]),
            low=float(c["l"]),
            close=float(c["c"]),
            volume=float(c["v"]),
        )
        for c in rows
    ]


def fetch_signal_closes(limit: int | None = None) -> dict[str, list]:
    """5m OHLCV for BTC and ETH, once. Reuses the paper-chart candles cache.

    Per-symbol failures are skipped so the other coin still gets a signal.
    """
    from hedge_fund.web.candles import (
        CandleFetchError,
        CandleRequestError,
        DEFAULT_LIMIT,
        candles_payload,
    )

    n = DEFAULT_LIMIT if limit is None else limit
    out: dict[str, list] = {}
    for sym in QUAL_SYMBOLS:
        try:
            payload = candles_payload(sym, QUAL_TIMEFRAME, n)
        except (CandleFetchError, CandleRequestError, Exception):
            continue
        rows = payload.get("candles") or []
        if len(rows) < 2:
            continue
        try:
            out[sym] = _bars_to_candles(rows)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def annotate_open_lot(
    row: dict,
    *,
    mark: float | None,
    candles: list | None,
    strategy_account: str | None = None,
) -> dict[str, Any]:
    """Add ``signal`` / ``path`` / ``path_pct`` (and optional ``gate``) in place."""
    entry = float(row["entry"])
    stop = float(row["stop"])
    tp = float(row["take_profit"])
    px = float(mark) if mark is not None else entry
    path, path_pct = path_in_trade(px, stop, tp)
    strategy = strategy_from_lot(row.get("condition"), strategy_account or row.get("account"))
    row["signal"] = lot_signal(strategy, candles, row["symbol"])
    row["path"] = path
    row["path_pct"] = path_pct
    if mark is not None:
        row["current"] = round(float(mark), 2)
        qty = float(row.get("quantity") or 0)
        row["unrealized_pnl"] = round((float(mark) - entry) * qty, 2)
        row["unrealized_pct"] = round(float(mark) / entry - 1.0, 4) if entry else None
    else:
        row["current"] = None
        row["unrealized_pnl"] = None
        row["unrealized_pct"] = None
    if candles and strategy:
        gate = mom_or_dip_gate(strategy, [c.close for c in candles])
        if gate:
            row["gate"] = gate
    return row
