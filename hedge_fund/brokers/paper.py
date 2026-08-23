"""Paper broker for crypto with realistic taker fees + slippage.

Differs from ai-hedge-fund's SimBroker in two ways that matter for crypto:

1. **Fractional / continuous sizing.** Crypto trades in fractional units
   (e.g. 0.013 BTC), not integer shares. Position sizes are floats.

2. **Taker fee + slippage on every fill.** The upstream SimBroker explicitly
   declares this "a declared future addition inside place_order" — we implement
   it now, because ignoring fees/slippage is exactly how paper bots report
   profits no live account could match. Fill happens at a *worse* price than
   the quoted reference (slippage), and a taker fee is deducted from cash.

Margin is not modeled (same as upstream): a long-only mandate with
sells-before-buys ordering and floor-toward-zero sizing keeps cash non-negative
in practice, but nothing hard-enforces it here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    ticker: str
    quantity: float
    entry_price: float
    stop_loss: float
    entry_fee: float
    entry_condition: str = ""  # calibration condition key at entry


class PaperBroker:
    """In-memory paper broker with fractional sizing, fees, and slippage.

    ``taker_fee`` is a fraction (e.g. 0.001 = 0.1 %). ``slippage_bps`` is
    applied as a fraction of price against the trader (e.g. 2 bps on a buy
    raises the fill price).
    """

    def __init__(
        self,
        cash: float,
        taker_fee: float = 0.001,
        slippage: float = 0.0002,
        fee_asset: str = "quote",
    ) -> None:
        self._cash = cash
        self.taker_fee = taker_fee
        self.slippage = slippage
        self.fee_asset = fee_asset
        self.positions: dict[str, OpenPosition] = {}
        self.fills: list[Fill] = []

    # -- public account view -------------------------------------------------
    def cash(self) -> float:
        return self._cash

    def equity(self, prices: dict[str, float]) -> float:
        pos_val = sum(p.quantity * prices.get(t, p.entry_price) for t, p in self.positions.items())
        return self._cash + pos_val

    def is_open(self, ticker: str) -> bool:
        return ticker in self.positions

    # -- order execution -----------------------------------------------------
    def place_order(self, order: Order, market_price: float | None = None) -> Fill | None:
        """Fill an order with slippage + taker fee.

        ``market_price`` is the price at fill time (used by the decision loop
        when it re-prices on a later bar). If None, uses order.price.
        """
        if market_price is not None:
            base = market_price
        else:
            base = order.price

        # Slippage acts against the trader.
        if order.side == "buy":
            fill_price = base * (1 + self.slippage)
        else:
            fill_price = base * (1 - self.slippage)

        notional = order.quantity * fill_price
        fee = notional * self.taker_fee

        if order.side == "buy":
            prev = self.positions.get(order.ticker)
            if prev is not None:
                # average up: blend entry price and add stop handling at caller
                new_qty = prev.quantity + order.quantity
                new_entry = (prev.quantity * prev.entry_price + notional) / new_qty
                new_stop = order.stop_loss if order.stop_loss is not None else prev.stop_loss
                new_cond = order.condition or prev.entry_condition
                self.positions[order.ticker] = OpenPosition(
                    order.ticker, new_qty, new_entry, new_stop, prev.entry_fee + fee, new_cond
                )
            else:
                if order.stop_loss is None:
                    raise ValueError("paper buy needs a stop_loss")
                self.positions[order.ticker] = OpenPosition(
                    order.ticker, order.quantity, fill_price, order.stop_loss, fee,
                    order.condition,
                )
            self._cash -= notional + fee
        else:  # sell
            pos = self.positions.get(order.ticker)
            if pos is None:
                raise ValueError(f"no open position to sell {order.ticker}")
            if order.quantity > pos.quantity + 1e-9:
                raise ValueError(
                    f"sell {order.quantity} > held {pos.quantity} for {order.ticker}"
                )
            remaining = pos.quantity - order.quantity
            self._cash += notional - fee
            if remaining <= 1e-9:
                del self.positions[order.ticker]
            else:
                self.positions[order.ticker] = OpenPosition(
                    order.ticker, remaining, pos.entry_price, pos.stop_loss, pos.entry_fee
                )

        fill = Fill(
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            fee=fee,
        )
        self.fills.append(fill)
        return fill

    def close_position(self, ticker: str, market_price: float) -> Fill | None:
        """Close a position fully (used by stop-loss / take-profit / flat)."""
        pos = self.positions.get(ticker)
        if pos is None:
            return None
        return self.place_order(Order(ticker, "sell", pos.quantity, market_price), market_price)

    def check_stops(self, prices: dict[str, float]) -> list[Fill]:
        """Force-close any position whose stop is breached by current price."""
        closed = []
        for ticker, pos in list(self.positions.items()):
            px = prices.get(ticker)
            if px is None:
                continue
            # stop is a price level; breach means price <= stop for longs
            if px <= pos.stop_loss:
                fill = self.close_position(ticker, px)
                if fill is not None:
                    closed.append(fill)
        return closed
