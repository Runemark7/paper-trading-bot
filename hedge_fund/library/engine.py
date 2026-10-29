"""``signal_exit`` engine: long-or-flat, all-in, one position, exit on the signal.

One state machine decides for both the backtest (:func:`run`) and the live
paper cycle (``hedge_fund.library.live``):

* A signal is the desired position after the close of a completed UTC day.
  It is acted on at the next open: a backtest fills at the next daily open,
  the live cycle at the price of its first run after that close. A fresh
  live book whose close is already long waits for the next 00:00 UTC open
  window instead of buying at an intraday cycle price.
* Flat and the signal is on: buy with all cash. Long and the signal is off:
  sell everything. No take-profit, no pyramiding, no partial sizing.
* A fresh book enters at the first scored open when the previous close is
  already long. That entry is part of the rule, not a lookahead.
* Safety stop (``stop_frac`` below the entry fill). The library pre-registers
  20% and leaves it on; the engine still accepts 0 for a test. A day's low
  at or through the stop does not fill at the stop price. The position is
  sold at the next day's open, then re-entry stays locked until the signal
  has been off at a close.
* Fees are the paper broker's: 0.1% taker plus 2 bps slippage per side,
  charged on every fill (``hedge_fund.brokers.paper``).

The old ``rm_v1`` stop/take-profit engine (``hedge_fund.backtest.strategies``)
is untouched; it still runs every 5m strategy name.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from hedge_fund.library.daily import DailyBars
from hedge_fund.risk.rm_v1 import FEE_SLIPPAGE, FEE_TAKER
from hedge_fund.trading.constants import PAPER_START_CASH

ENGINE_ID = "signal_exit"
TIMEFRAME = "1d"
RISK_POLICY = "signal_exit_all_in"

ENTER = "enter"
EXIT = "exit"

REASON_SIGNAL = "signal"
REASON_STOP = "safety_stop"
REASON_END = "window_end"


@dataclass(frozen=True)
class Costs:
    taker_fee: float = FEE_TAKER
    slippage: float = FEE_SLIPPAGE


def buy_fill(cash: float, ref_price: float, costs: Costs) -> tuple[float, float, float]:
    """All-in buy: ``(quantity, fill price, fee)``. Spends all of ``cash``.

    Same arithmetic as ``PaperBroker.place_order``: fill = ref × (1 + slip),
    fee = quantity × fill × taker, cash falls by notional + fee.
    """
    fill = float(ref_price) * (1.0 + costs.slippage)
    if cash <= 0.0 or fill <= 0.0:
        return 0.0, fill, 0.0
    qty = float(cash) / (fill * (1.0 + costs.taker_fee))
    return qty, fill, qty * fill * costs.taker_fee


def sell_fill(qty: float, ref_price: float, costs: Costs) -> tuple[float, float, float]:
    """Sell everything: ``(fill price, fee, cash received)``."""
    fill = float(ref_price) * (1.0 - costs.slippage)
    gross = float(qty) * fill
    fee = gross * costs.taker_fee
    return fill, fee, gross - fee


def stop_price(entry_fill: float, stop_frac: float) -> float | None:
    if not stop_frac or stop_frac <= 0.0:
        return None
    return float(entry_fill) * (1.0 - float(stop_frac))


@dataclass
class MachineState:
    """What the decision rule needs to remember between closes."""

    long: bool = False
    lockout: bool = False
    # A completed day traded through the stop. The next open sells.
    stop_pending: bool = False

    def to_dict(self) -> dict:
        return {
            "long": bool(self.long),
            "lockout": bool(self.lockout),
            "stop_pending": bool(self.stop_pending),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "MachineState":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            long=bool(raw.get("long")),
            lockout=bool(raw.get("lockout")),
            stop_pending=bool(raw.get("stop_pending")),
        )


def step(state: MachineState, signal_on: bool) -> str | None:
    """Apply one completed day's signal. Returns ``ENTER``, ``EXIT`` or ``None``.

    A signal that is off re-arms entries after a safety stop.
    """
    on = bool(signal_on)
    action: str | None = None
    if state.long:
        if not on:
            action = EXIT
            state.long = False
    elif on and not state.lockout:
        action = ENTER
        state.long = True
    if not on:
        state.lockout = False
    return action


def stop_out(state: MachineState) -> None:
    state.long = False
    state.lockout = True
    state.stop_pending = False


@dataclass
class Trade:
    entry_ts: int
    entry_price: float
    qty: float
    entry_fee: float
    exit_ts: int = 0
    exit_price: float = 0.0
    exit_fee: float = 0.0
    reason: str = ""
    days: int = 0

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.qty - self.entry_fee - self.exit_fee

    @property
    def pnl_pct(self) -> float:
        return self.exit_price / self.entry_price - 1.0 if self.entry_price else 0.0

    def as_dict(self) -> dict:
        return {
            "entry_ts": int(self.entry_ts),
            "exit_ts": int(self.exit_ts),
            "entry_price": round(self.entry_price, 2),
            "exit_price": round(self.exit_price, 2),
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 6),
            "reason": self.reason,
            "days": int(self.days),
        }


@dataclass
class RunResult:
    start: int
    end: int
    trades: list[Trade] = field(default_factory=list)
    # Point 0 is the starting cash before the first scored open; point i is
    # the equity at the close of scored day i - 1; the last point is the
    # cash after the window-end sale.
    equity: np.ndarray = field(default_factory=lambda: np.empty(0))
    days_long: int = 0
    fees: float = 0.0

    @property
    def n_days(self) -> int:
        return max(0, self.end - self.start)

    def returns(self) -> np.ndarray:
        eq = self.equity
        if eq.shape[0] < 2:
            return np.empty(0)
        return eq[1:] / eq[:-1] - 1.0


def run(
    bars: DailyBars,
    signal: np.ndarray,
    *,
    start: int,
    end: int,
    start_cash: float = PAPER_START_CASH,
    costs: Costs = Costs(),
    stop_frac: float = 0.0,
) -> RunResult:
    """Score days ``[start, end)``. The book starts flat with ``start_cash``.

    At day ``d``'s open the signal of day ``d - 1`` is applied. A fresh book
    whose previous close is already long buys at ``bars.open[start]``. A
    safety stop is checked against day ``d``'s low after the open. The fill
    is the next day's open, not the stop price and not this day's open.
    Equity is marked at each close. An open position is sold at the close
    of day ``end - 1`` (as a stop, if that last day was the breach).
    """
    sig = np.asarray(signal, dtype=bool)
    n = len(bars)
    start = max(0, int(start))
    end = min(n, int(end))
    result = RunResult(start=start, end=end)
    if end <= start:
        result.equity = np.asarray([float(start_cash)])
        return result
    state = MachineState()
    cash = float(start_cash)
    qty = 0.0
    stop: float | None = None
    trade: Trade | None = None
    entry_day = start
    points = np.empty(end - start + 1, dtype=np.float64)
    points[0] = cash
    fees = 0.0
    days_long = 0

    def close_trade(ts: int, ref: float, reason: str, days: int) -> None:
        # ``days``: days on which the position was open (an exit at an open
        # does not count that day).
        nonlocal cash, qty, trade, stop, fees
        fill, fee, proceeds = sell_fill(qty, ref, costs)
        cash += proceeds
        fees += fee
        assert trade is not None
        trade.exit_ts = int(ts)
        trade.exit_price = fill
        trade.exit_fee = fee
        trade.reason = reason
        trade.days = int(days)
        result.trades.append(trade)
        trade = None
        qty = 0.0
        stop = None

    for d in range(start, end):
        ts = int(bars.ts[d])
        if state.stop_pending and state.long:
            close_trade(ts, float(bars.open[d]), REASON_STOP, d - entry_day)
            stop_out(state)
        on = bool(sig[d - 1]) if d >= 1 else False
        action = step(state, on)
        if action == EXIT:
            close_trade(ts, float(bars.open[d]), REASON_SIGNAL, d - entry_day)
        elif action == ENTER:
            q, fill, fee = buy_fill(cash, float(bars.open[d]), costs)
            cash -= q * fill + fee
            fees += fee
            qty = q
            stop = stop_price(fill, stop_frac)
            trade = Trade(entry_ts=ts, entry_price=fill, qty=q, entry_fee=fee)
            entry_day = d
        if state.long and stop is not None and float(bars.low[d]) <= stop:
            state.stop_pending = True
        if state.long:
            days_long += 1
        points[d - start + 1] = cash + qty * float(bars.close[d])

    if trade is not None:
        last = end - 1
        reason = REASON_STOP if state.stop_pending else REASON_END
        close_trade(int(bars.ts[last]), float(bars.close[last]), reason, last - entry_day + 1)
        if state.stop_pending:
            stop_out(state)
        points[-1] = cash
    result.equity = points
    result.days_long = days_long
    result.fees = fees
    return result
