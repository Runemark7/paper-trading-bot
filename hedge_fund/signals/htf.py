"""Causal higher-timeframe buyer-regime atoms from the native 5m series.

Long-only book: these are discoverable AND gates, not a hardcoded oracle.
When HTF says sellers, the predicate is False → no new long (flat), not short.
When HTF says buyers and a 5m dip/mom atom is also True, that is buy-the-dip;
if they disagree, HTF wins (no long).

Resample is index-aligned on a contiguous 5m tape (48 bars = 4h, 12 = 1h).
Only **completed** HTF buckets are used: the forming bar's current 5m close
is not the HTF close. No timestamps required — live and qual both walk a
single 5m series. Calendar resample lives in ``hedge_fund.data.resample``
for timestamped tests; the parser hot path stays pandas-free.
"""
from __future__ import annotations

import math

# 5m bars per completed HTF candle. Contiguous tape only.
BARS_PER_H4 = 48
BARS_PER_H1 = 12
TF_BARS = {"h4": BARS_PER_H4, "h1": BARS_PER_H1}

_HTF_CACHE_MAX = 24
_htf_close_cache: dict[tuple[int, int, int], list[float]] = {}


def clear_htf_cache() -> None:
    _htf_close_cache.clear()


def completed_htf_count(i: int, bars_per: int) -> int:
    """How many HTF bars have fully closed at 5m index ``i`` (inclusive)."""
    if i < 0 or bars_per <= 0:
        return 0
    return (i + 1) // bars_per


def htf_bucket_close_index(k: int, bars_per: int) -> int:
    """5m index of the close of completed HTF bucket ``k`` (0-based)."""
    return (k + 1) * bars_per - 1


def _htf_closes_all(closes: list[float], bars_per: int) -> list[float]:
    """All HTF closes implied by ``closes`` (complete buckets only)."""
    n = len(closes)
    key = (id(closes), bars_per, n)
    cached = _htf_close_cache.get(key)
    if cached is not None:
        return cached
    n_bars = n // bars_per
    series = [closes[htf_bucket_close_index(k, bars_per)] for k in range(n_bars)]
    if len(_htf_close_cache) >= _HTF_CACHE_MAX:
        _htf_close_cache.clear()
    _htf_close_cache[key] = series
    return series


def completed_htf_closes(closes: list[float], i: int | None, bars_per: int) -> list[float]:
    """HTF closes from buckets that have fully closed at 5m index ``i``.

    Bucket ``k`` uses 5m bars ``[k * bars_per, (k + 1) * bars_per)``.
    At ``i = 47`` (first 4h just closed) this returns one close;
    at ``i = 48`` the second 4h is forming and is omitted.
    """
    i = len(closes) - 1 if i is None else i
    n_complete = completed_htf_count(i, bars_per)
    if n_complete <= 0:
        return []
    return _htf_closes_all(closes, bars_per)[:n_complete]


def htf_close_above_ma(
    closes: list[float],
    i: int | None,
    *,
    bars_per: int,
    period: int,
    kind: str,
) -> bool:
    """Last completed HTF close above SMA/EMA(period) of completed HTF closes."""
    if period <= 0 or bars_per <= 0:
        return False
    htf = completed_htf_closes(closes, i, bars_per)
    if len(htf) < period:
        return False
    from hedge_fund.signals.dynamic import ema, sma

    if kind == "sma":
        ma = sma(htf, period)
    elif kind == "ema":
        ma = ema(htf, period)
    else:
        return False
    return not math.isnan(ma) and htf[-1] > ma


def h4_ema_abv(closes: list[float], period: int, i: int | None = None) -> bool:
    return htf_close_above_ma(closes, i, bars_per=BARS_PER_H4, period=period, kind="ema")


def h4_sma_abv(closes: list[float], period: int, i: int | None = None) -> bool:
    return htf_close_above_ma(closes, i, bars_per=BARS_PER_H4, period=period, kind="sma")


def h1_ema_abv(closes: list[float], period: int, i: int | None = None) -> bool:
    return htf_close_above_ma(closes, i, bars_per=BARS_PER_H1, period=period, kind="ema")
