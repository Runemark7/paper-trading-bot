"""Crypto OHLCV data source via ccxt (default: Binance).

Focused on what swing/day trading needs: closing-price klines (candles) at a
configurable timeframe. Kept lean on purpose — the stock DataClient protocol
(financial metrics, news, insider trades) does not apply to a BTC/ETH market.

Uses public Binance endpoints, no API key required for klines.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import ccxt

# Binance timeframes ccxt accepts; we only need the swing-relevant ones.
TIMEFRAMES = Literal["1m", "5m", "15m", "1h", "4h", "1d"]


@dataclass(frozen=True)
class Candle:
    """One OHLCV bar. `ts` is the bar open time in milliseconds (UTC)."""

    ts: int  # ms epoch of bar open
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def datetime_iso(self) -> str:
        import datetime as _dt

        return _dt.datetime.fromtimestamp(self.ts / 1000, tz=_dt.timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        )


class CcxtSource:
    """Thin ccxt wrapper exposing klines for a symbol/timeframe.

    Deliberately raises on network/parse failures (never silently returns a
    short series) so downstream consumers can't mistake a dropped fetch for
    "no data".
    """

    def __init__(self, exchange_id: str = "binance", sandbox: bool = False) -> None:
        ex_cls = getattr(ccxt, exchange_id)
        self.exchange = ex_cls(
            {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        )
        if sandbox:
            self.exchange.set_sandbox_mode(True)
        self._last_ts: dict[tuple[str, str], int] = {}

    def fetch_klines(
        self,
        symbol: str,
        timeframe: str = "4h",
        limit: int = 500,
        since: int | None = None,
    ) -> list[Candle]:
        """Fetch OHLCV bars from an exchange.

        Returns oldest->newest list of Candle. Raises on failure.
        """
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        candles = [
            Candle(ts=row[0], open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5])
            for row in raw
        ]
        if not candles:
            raise RuntimeError(f"{exchange_id}: no klines for {symbol} {timeframe}")
        self._last_ts[(symbol, timeframe)] = candles[-1].ts
        return candles

    def fetch_price(self, symbol: str) -> float:
        """Latest spot price for a symbol."""
        ticker = self.exchange.fetch_ticker(symbol)
        return float(ticker["last"])

    def fetch_timeframes(self, symbol: str) -> list[str]:
        """List of timeframe strings the exchange actually supports for a symbol."""
        return list(self.exchange.markets_by_id.get(symbol, []) and [])


# Convenience: the two symbols we trade and a set of supported timeframes.
SUPPORTED_SYMBOLS = ("BTC/USDT", "ETH/USDT")
DEFAULT_TIMEFRAME = "4h"
