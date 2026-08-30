"""Paper broker for crypto with realistic taker fees + slippage.

Supports multiple concurrent open lots per ticker (pyramiding into an
uptrend) — each entry is its own lot with its own stop/TP, so each resolves
independently. Same fee/slippage model as before: fills happen at a worse
price than the reference (slippage) and a taker fee is deducted.

Position model: ``self.lots`` is a list of OpenPosition (one per entry).
Public helpers expose aggregate views (equity, per-ticker quantity) for
the loop and dashboard.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from itertools import count

# PROTOCOL §6 — charged on every simulated fill. Dashboard imports these.
TAKER_FEE = 0.001    # 0.1%
SLIPPAGE = 0.0002    # 2 bps

# PAPER_ONLY defaults on. Unset or any value other than 0/false/no/off is paper.
_PAPER_ONLY_OFF = frozenset({"0", "false", "no", "off"})


def _assert_paper_only() -> None:
    """Refuse to construct a live-path broker unless this process is paper-only."""
    raw = os.environ.get("PAPER_ONLY", "1").strip().lower()
    if raw in _PAPER_ONLY_OFF:
        raise RuntimeError(
            "REFUSING TO CONSTRUCT PaperBroker: PAPER_ONLY="
            f"{os.environ.get('PAPER_ONLY')!r}. This process is paper-trading "
            "only — no real-money broker. Set PAPER_ONLY=1 (the default) or unset it."
        )


@dataclass
class Fill:
    """An executed fill. `quantity` is fractional (crypto)."""

    ticker: str
    side: str  # "buy" | "sell"
    quantity: float
    price: float
    fee: float = 0.0


@dataclass
class Order:
    ticker: str
    side: str  # "buy" | "sell"
    quantity: float  # fractional for crypto
    price: float  # reference price at decision time (mid)
    stop_loss: float | None = None  # price at which position is force-closed
    condition: str = ""  # calibration condition key, carried to the position


@dataclass
class OpenPosition:
    """One open lot — a single entry that resolves with its own stop/TP."""

    lot_id: int
    ticker: str
    quantity: float
    entry_price: float
    stop_loss: float
    entry_fee: float
    entry_condition: str = ""


class PaperBroker:
    """In-memory paper broker with fractional sizing, fees, and slippage."""

    _next_lot = count(1)

    def __init__(
        self,
        cash: float,
        taker_fee: float = TAKER_FEE,
        slippage: float = SLIPPAGE,
        fee_asset: str = "quote",
    ) -> None:
        _assert_paper_only()
        self._cash = cash
        self.taker_fee = taker_fee
        self.slippage = slippage
        self.fee_asset = fee_asset
        self.lots: list[OpenPosition] = []
        self.fills: list[Fill] = []

    # -- public account view -------------------------------------------------
    def cash(self) -> float:
        return self._cash

    def equity(self, prices: dict[str, float]) -> float:
        pos_val = sum(p.quantity * prices.get(p.ticker, p.entry_price) for p in self.lots)
        return self._cash + pos_val

    def quantity(self, ticker: str) -> float:
        return sum(p.quantity for p in self.lots if p.ticker == ticker)

    def has_position(self, ticker: str) -> bool:
        return any(p.ticker == ticker for p in self.lots)

    def lots_for(self, ticker: str) -> list[OpenPosition]:
        return [p for p in self.lots if p.ticker == ticker]

    # -- state persistence ---------------------------------------------------
    def to_state(self) -> dict:
        return {
            "cash": self._cash,
            "lots": [
                {
                    "lot_id": p.lot_id, "ticker": p.ticker, "quantity": p.quantity,
                    "entry_price": p.entry_price, "stop_loss": p.stop_loss,
                    "entry_fee": p.entry_fee, "entry_condition": p.entry_condition,
                }
                for p in self.lots
            ],
        }

    def restore_state(self, state: dict) -> None:
        self._cash = float(state.get("cash", self._cash))
        lots = state.get("lots") or state.get("positions") or []
        self.lots = []
        for p in lots:
            self.lots.append(OpenPosition(
                lot_id=p.get("lot_id", next(self._next_lot)),
                ticker=p["ticker"], quantity=p["quantity"],
                entry_price=p["entry_price"], stop_loss=p["stop_loss"],
                entry_fee=p.get("entry_fee", 0.0),
                entry_condition=p.get("entry_condition", ""),
            ))
        # keep the lot-id counter ahead of any restored lots so new entries
        # in this process never collide with existing lot ids.
        max_lot = max((p.lot_id for p in self.lots), default=0)
        for _ in range(max_lot):
            next(self._next_lot)
        self.fills = []

    # -- order execution ----------------------------------------------------------
    def place_order(self, order: Order, market_price: float | None = None) -> Fill | None:
        base = market_price if market_price is not None else order.price
        fill_price = base * (1 + self.slippage) if order.side == "buy" else base * (1 - self.slippage)
        notional = order.quantity * fill_price
        fee = notional * self.taker_fee

        if order.side == "buy":
            if order.stop_loss is None:
                raise ValueError("paper buy needs a stop_loss")
            self.lots.append(OpenPosition(
                lot_id=next(self._next_lot), ticker=order.ticker,
                quantity=order.quantity, entry_price=fill_price,
                stop_loss=order.stop_loss, entry_fee=fee,
                entry_condition=order.condition,
            ))
            self._cash -= notional + fee
        else:  # sell
            target = next((p for p in self.lots if p.ticker == order.ticker), None)
            if target is None:
                raise ValueError(f"no open position to sell {order.ticker}")
            if order.quantity > target.quantity + 1e-9:
                raise ValueError(
                    f"sell {order.quantity} > held {target.quantity} for {order.ticker}"
                )
            remaining = target.quantity - order.quantity
            self._cash += notional - fee
            if remaining <= 1e-9:
                self.lots.remove(target)
            else:
                target.quantity = remaining

        fill = Fill(ticker=order.ticker, side=order.side,
                    quantity=order.quantity, price=fill_price, fee=fee)
        self.fills.append(fill)
        return fill

    def close_lot(self, lot: OpenPosition, market_price: float) -> Fill:
        """Fully close one specific lot (stop/TP)."""
        return self.place_order(Order(lot.ticker, "sell", lot.quantity, market_price),
                                market_price)

    def check_stops(self, prices: dict[str, float]) -> list[Fill]:
        """Force-close any lot whose stop is breached."""
        closed = []
        for lot in list(self.lots):
            px = prices.get(lot.ticker)
            if px is not None and px <= lot.stop_loss:
                fill = self.close_lot(lot, px)
                closed.append((lot, fill))
        return closed