"""Multi-Timeframe Resampling & Alignment Engine.

Converts 5-minute base OHLCV candles into higher timeframes (15m, 1h, 4h, 1d)
with strict lookahead-bias prevention (higher timeframe features on candle i
only use completed candles up to that timestamp).
"""
from __future__ import annotations

import pandas as pd
import numpy as np

# Contiguous 5m bars that make one HTF candle. Parser atoms use the same
# counts (see hedge_fund.signals.htf) so index-aligned and midnight-aligned
# calendar resamples agree.
BARS_5M_PER = {"15m": 3, "1h": 12, "4h": 48, "1d": 288}

_TF_RULE = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
    "daily": "1D",
    "h4": "4h",
    "h1": "1h",
    "m15": "15min",
}


def _rule_for(target_tf: str) -> str:
    return _TF_RULE.get(target_tf.lower(), target_tf)


def resample_ohlcv(df_5m: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    """Resample 5m OHLCV dataframe to target timeframe (e.g. '15min', '1h', '4h', '1D').

    This uses whatever 5m rows are in ``df_5m``. For a causal cut, pass a
    prefix (or use ``causal_resample_ohlcv``) — resampling the full series
    then slicing HTF labels still leaks the incomplete bar's future close.
    """
    rule = _rule_for(target_tf)

    resampled = df_5m.resample(rule, closed="left", label="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return resampled


def causal_resample_ohlcv(
    df_5m: pd.DataFrame,
    target_tf: str,
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Resample 5m bars at or before ``as_of``; drop the incomplete last HTF bar.

    A left-labeled HTF bar starting at ``T`` covers ``[T, T+freq)``. It is
    complete only once the last 5m of that period (``T+freq - 5min``) is
    in the prefix. Forming-bar 5m closes are not the HTF close.
    """
    if df_5m.empty:
        return df_5m.iloc[0:0]
    if as_of is None:
        as_of = df_5m.index[-1]
    available = df_5m.loc[:as_of]
    resampled = resample_ohlcv(available, target_tf)
    if resampled.empty:
        return resampled
    rule = _rule_for(target_tf)
    freq = pd.tseries.frequencies.to_offset(rule)
    period_end = resampled.index[-1] + freq
    last_closed_5m = period_end - pd.Timedelta(minutes=5)
    if as_of < last_closed_5m:
        resampled = resampled.iloc[:-1]
    return resampled


class MultiTimeframeDataset:
    """Pre-computes aligned multi-timeframe views from 5m base bars for an asset."""

    def __init__(self, raw_5m_rows: list[list]):
        """raw_5m_rows: list of [ts_ms, open, high, low, close, (volume)]"""
        has_vol = len(raw_5m_rows[0]) >= 6 if raw_5m_rows else False
        cols = ["ts", "open", "high", "low", "close", "volume"] if has_vol else ["ts", "open", "high", "low", "close"]
        
        df = pd.DataFrame(raw_5m_rows, columns=cols)
        if "volume" not in df.columns:
            # Synthetic volume proxy if absent (e.g. range proxy)
            df["volume"] = (df["high"] - df["low"]).abs() + 1.0
            
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df.set_index("ts", inplace=True)
        df.sort_index(inplace=True)
        
        self.df_5m = df
        self.df_15m = resample_ohlcv(df, "15m")
        self.df_1h = resample_ohlcv(df, "1h")
        self.df_4h = resample_ohlcv(df, "4h")
        self.df_1d = resample_ohlcv(df, "1d")

    def get_closes(self, tf: str = "5m") -> list[float]:
        tf_l = tf.lower()
        if tf_l in ("1d", "daily"):
            return self.df_1d["close"].tolist()
        if tf_l in ("4h", "h4"):
            return self.df_4h["close"].tolist()
        if tf_l in ("1h", "h1"):
            return self.df_1h["close"].tolist()
        if tf_l in ("15m", "m15"):
            return self.df_15m["close"].tolist()
        return self.df_5m["close"].tolist()

    def get_aligned_history(self, tf: str, timestamp: pd.Timestamp) -> pd.DataFrame:
        """Completed HTF bars only, from 5m rows at or before ``timestamp``.

        Does not use the precomputed full-series HTF frames — those would
        leak the forming bar's future close. 5m itself is just the prefix.
        """
        tf_l = tf.lower()
        if tf_l in ("5m", "m5"):
            return self.df_5m.loc[:timestamp]
        return causal_resample_ohlcv(self.df_5m, tf_l, as_of=timestamp)
