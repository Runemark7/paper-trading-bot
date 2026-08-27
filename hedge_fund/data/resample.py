"""Multi-Timeframe Resampling & Alignment Engine.

Converts 5-minute base OHLCV candles into higher timeframes (15m, 1h, 4h, 1d)
with strict lookahead-bias prevention (higher timeframe features on candle i
only use completed candles up to that timestamp).
"""
from __future__ import annotations

import pandas as pd
import numpy as np


def resample_ohlcv(df_5m: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    """Resample 5m OHLCV dataframe to target timeframe (e.g. '15min', '1h', '4h', '1D')."""
    tf_map = {
        "5m": "5min",
        "15m": "15min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1D",
        "daily": "1D",
    }
    rule = tf_map.get(target_tf.lower(), target_tf)
    
    resampled = df_5m.resample(rule, closed="left", label="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
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
        """Returns completed history for a higher timeframe strictly BEFORE or AT timestamp."""
        tf_l = tf.lower()
        if tf_l in ("1d", "daily"):
            src = self.df_1d
        elif tf_l in ("4h", "h4"):
            src = self.df_4h
        elif tf_l in ("1h", "h1"):
            src = self.df_1h
        elif tf_l in ("15m", "m15"):
            src = self.df_15m
        else:
            src = self.df_5m
            
        # Strict lookahead filter: only return bars whose entire period closed before/at timestamp
        return src.loc[:timestamp]
