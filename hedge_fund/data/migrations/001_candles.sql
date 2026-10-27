-- Idempotent candle store for Binance 5m OHLCV.
-- PostgreSQL 18. Safe to re-run. No secrets belong in this file.
-- Primary key makes a second insert of the same bar an upsert, not a duplicate.

CREATE TABLE IF NOT EXISTS candles (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_time TIMESTAMPTZ NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (exchange, symbol, timeframe, open_time)
);
