"""Equal-weight buy-and-hold overlay after paper fees + slippage.

PROTOCOL §3: "profitable" means vs buy-and-hold of the same assets over the
same period, after fees. Amendment 2026-08-30 noted the overlay was not
computed. Amendment 2026-09-01: it is computed here and used by
``snapshot_equity`` (live curve) and graduation (paper PnL vs B&H).
"""
from __future__ import annotations

import math
import statistics

from hedge_fund.brokers.paper import SLIPPAGE, TAKER_FEE
from hedge_fund.trading.constants import PAPER_START_CASH, QUAL_SYMBOLS

BH_ANCHOR_KEY = "bh_start"
DAYS_PER_YEAR = 365  # crypto trades every calendar day
BARS_PER_DAY_5M = 288


def equity_returns(points: list[float]) -> list[float]:
    """Simple returns between consecutive equity samples (skips non-positive bases)."""
    out: list[float] = []
    for prev, cur in zip(points, points[1:]):
        if prev and prev > 0 and math.isfinite(prev) and math.isfinite(cur):
            out.append(cur / prev - 1.0)
    return out


def daily_returns_sharpe(returns: list[float]) -> float:
    """Annualized Sharpe of daily returns (mean / stdev * sqrt(365), rf = 0).

    0.0 when there are fewer than two returns, zero / non-finite stdev
    (a book that never trades is flat, Sharpe 0).
    """
    vals = [float(r) for r in returns if r is not None and math.isfinite(float(r))]
    if len(vals) < 2:
        return 0.0
    sd = statistics.stdev(vals)
    if not math.isfinite(sd) or sd <= 1e-12:
        return 0.0
    return float(statistics.mean(vals) / sd * math.sqrt(DAYS_PER_YEAR))


def daily_equity_sharpe(points: list[float]) -> float:
    """Annualized Sharpe of a daily-sampled equity curve."""
    return daily_returns_sharpe(equity_returns(points))


def _qty_bought(cash: float, start_price: float, taker_fee: float, slippage: float) -> float:
    fill = start_price * (1.0 + slippage)
    if fill <= 0:
        return 0.0
    # Paper buy: cash -= notional + fee, fee = notional * taker, notional = qty * fill.
    return cash / (fill * (1.0 + taker_fee))


def buy_and_hold_mtm(
    start_cash: float,
    start_prices: dict[str, float],
    current_prices: dict[str, float],
    taker_fee: float = TAKER_FEE,
    slippage: float = SLIPPAGE,
) -> float | None:
    """Mark-to-market equity of an equal-weight long opened at ``start_prices``.

    Entry fees + slippage are charged; no exit fee (still holding). Used for
    the live equity-curve overlay.
    """
    symbols = [
        s for s in start_prices
        if s in current_prices
        and start_prices[s]
        and current_prices[s]
        and start_prices[s] > 0
        and current_prices[s] > 0
    ]
    if not symbols:
        return None
    per = start_cash / len(symbols)
    equity = 0.0
    for s in symbols:
        qty = _qty_bought(per, float(start_prices[s]), taker_fee, slippage)
        equity += qty * float(current_prices[s])
    return equity


def buy_and_hold_realized_pnl(
    start_cash: float,
    start_prices: dict[str, float],
    end_prices: dict[str, float],
    taker_fee: float = TAKER_FEE,
    slippage: float = SLIPPAGE,
) -> float | None:
    """Round-trip B&H PnL: buy at start, sell at end, fees+slippage both sides.

    Used for OOS window gates and graduation when comparing a completed period.
    """
    symbols = [
        s for s in start_prices
        if s in end_prices
        and start_prices[s]
        and end_prices[s]
        and start_prices[s] > 0
        and end_prices[s] > 0
    ]
    if not symbols:
        return None
    per = start_cash / len(symbols)
    equity = 0.0
    for s in symbols:
        qty = _qty_bought(per, float(start_prices[s]), taker_fee, slippage)
        fill_out = float(end_prices[s]) * (1.0 - slippage)
        proceeds = qty * fill_out
        fee = proceeds * taker_fee
        equity += proceeds - fee
    return equity - start_cash


def buy_and_hold_window_pnl(
    closes_by_symbol: dict[str, list[float]],
    start_cash: float = PAPER_START_CASH,
    taker_fee: float = TAKER_FEE,
    slippage: float = SLIPPAGE,
) -> float | None:
    """B&H PnL over parallel close series (first bar → last bar), after fees."""
    start_prices, end_prices = {}, {}
    for sym, closes in closes_by_symbol.items():
        if closes and len(closes) >= 2 and closes[0] > 0 and closes[-1] > 0:
            start_prices[sym] = float(closes[0])
            end_prices[sym] = float(closes[-1])
    return buy_and_hold_realized_pnl(start_cash, start_prices, end_prices, taker_fee, slippage)


def buy_and_hold_daily_equity(
    closes_by_symbol: dict[str, list[float]],
    start_cash_per_symbol: float = PAPER_START_CASH,
    bars_per_day: int = BARS_PER_DAY_5M,
    taker_fee: float = TAKER_FEE,
    slippage: float = SLIPPAGE,
) -> list[float]:
    """Daily-sampled equity of B&H on the same capital base as a backtest.

    Each symbol gets ``start_cash_per_symbol`` (the discovery backtest runs
    each symbol on its own 10k book, so the B&H book is 10k x symbols too).
    Bought at the first close with fee + slippage, marked at every
    ``bars_per_day``-th close, sold at the last close with fee + slippage.
    Sample points line up with ``backtest(...).daily_equity``.
    """
    series = {
        s: c for s, c in closes_by_symbol.items()
        if c and len(c) >= 2 and c[0] > 0 and c[-1] > 0
    }
    if not series:
        return []
    n = min(len(c) for c in series.values())
    qty = {
        s: _qty_bought(start_cash_per_symbol, float(c[0]), taker_fee, slippage)
        for s, c in series.items()
    }
    every = max(1, int(bars_per_day))
    points = [start_cash_per_symbol * len(series)]
    last_sample = -1
    for i in range(every - 1, n, every):
        points.append(sum(qty[s] * float(c[i]) for s, c in series.items()))
        last_sample = i
    final = 0.0
    for s, c in series.items():
        proceeds = qty[s] * float(c[n - 1]) * (1.0 - slippage)
        final += proceeds - proceeds * taker_fee
    if last_sample == n - 1 and len(points) > 1:
        points[-1] = final
    else:
        points.append(final)
    return points


def buy_and_hold_daily_sharpe(
    closes_by_symbol: dict[str, list[float]],
    start_cash_per_symbol: float = PAPER_START_CASH,
    bars_per_day: int = BARS_PER_DAY_5M,
) -> float:
    """Annualized daily-equity Sharpe of B&H over one span."""
    return daily_equity_sharpe(
        buy_and_hold_daily_equity(closes_by_symbol, start_cash_per_symbol, bars_per_day)
    )


def buy_and_hold_from_trades(
    trades: list[dict],
    start_cash: float = PAPER_START_CASH,
) -> float | None:
    """Thin reconstruction: first entry vs last exit per symbol in chronological trades."""
    if not trades:
        return None
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    for t in trades:
        sym = t.get("symbol")
        entry = t.get("entry_price")
        exit_ = t.get("exit_price")
        if not sym or not entry or not exit_:
            continue
        if sym not in first:
            first[sym] = float(entry)
        last[sym] = float(exit_)
    return buy_and_hold_realized_pnl(start_cash, first, last)


def ensure_bh_anchor(store, prices: dict[str, float], start_cash: float = PAPER_START_CASH) -> dict:
    """Persist first-seen prices for the overlay. Returns the anchor dict."""
    saved = store.load_kv(BH_ANCHOR_KEY)
    if saved and saved.get("prices"):
        return saved
    clean = {s: float(p) for s, p in prices.items() if p and s in QUAL_SYMBOLS}
    if not clean:
        clean = {s: float(p) for s, p in prices.items() if p}
    anchor = {"cash": start_cash, "prices": clean}
    if clean:
        store.save_kv(BH_ANCHOR_KEY, anchor)
    return anchor


def overlay_equity(store, current_prices: dict[str, float], start_cash: float = PAPER_START_CASH) -> float | None:
    """B&H mark-to-market for ``snapshot_equity(..., baseline=)``."""
    usable = {s: p for s, p in current_prices.items() if p}
    if not usable:
        return None
    anchor = ensure_bh_anchor(store, usable, start_cash)
    return buy_and_hold_mtm(float(anchor.get("cash") or start_cash), anchor.get("prices") or {}, usable)
