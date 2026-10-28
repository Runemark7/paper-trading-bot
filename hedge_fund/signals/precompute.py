"""Per-bar entry flags, built once per tape and atom.

Stage-1 runs hundreds of backtests on the same window slices. The bar
loop indexes this array instead of calling ``dbl_bot`` / ``near_swing`` /
``sma`` on every bar (and rebuilding an ATR cache key each time).

The bool series is cached under the same byte budget as the structure
arrays, keyed by the tape content hash and the atom text. A second
backtest of the same name on the same slice does not recompute it.
"""
from __future__ import annotations

import re
from array import array

from hedge_fund.signals.series_cache import (
    cache_get,
    cache_put,
    fingerprint_series,
    tape_digest,
)
from hedge_fund.signals.structure import (
    HOLD_ATR_MULT,
    HOLD_PCT,
    NEAR_ATR_MULT,
    NEAR_ATR_PERIOD,
    NEAR_PCT,
    atr_series,
    pivot_arrays,
    _donchian_series,
)

_ZERO = b"\0"


def _zeros(n: int) -> array:
    if n <= 0:
        return array("B")
    return array("B", _ZERO) * n


def _nan_array(n: int) -> array:
    if n <= 0:
        return array("d")
    return array("d", [float("nan")]) * n


def sma_array(closes, period: int) -> array:
    """Window SMA. ``sum(closes[i-period+1:i+1]) / period``, cached."""
    n = len(closes)
    key = (fingerprint_series(closes), "sma", int(period), n)
    hit = cache_get(key)
    if hit is not None:
        return hit
    out = _nan_array(n)
    if period > 0 and n >= period:
        for idx in range(period - 1, n):
            out[idx] = sum(closes[idx - period + 1 : idx + 1]) / period
    return cache_put(key, out)


def ema_array(closes, period: int) -> array:
    """SMA-seeded EMA. Same recurrence as ``dynamic.ema``."""
    n = len(closes)
    key = (fingerprint_series(closes), "ema", int(period), n)
    hit = cache_get(key)
    if hit is not None:
        return hit
    out = _nan_array(n)
    if period > 0 and n >= period:
        k = 2.0 / (period + 1)
        val = sum(closes[:period]) / period
        out[period - 1] = val
        for idx in range(period, n):
            val = closes[idx] * k + val * (1.0 - k)
            out[idx] = val
    return cache_put(key, out)


def _near(close: float, level: float, atr_val: float) -> bool:
    band = NEAR_PCT * close
    if atr_val == atr_val and atr_val > 0:
        band = max(band, NEAR_ATR_MULT * atr_val)
    return abs(close - level) <= band


def _holds(first: float, second: float, atr_val: float) -> bool:
    band = HOLD_PCT * abs(first)
    if atr_val == atr_val and atr_val > 0:
        band = max(band, HOLD_ATR_MULT * atr_val)
    return abs(second - first) <= band


def _near_swing_signal(closes, highs, lows, k: int, *, want_high: bool) -> array:
    values = highs if want_high else lows
    idx, px = pivot_arrays(values, k, want_high)
    atr = atr_series(highs, lows, closes, NEAR_ATR_PERIOD)
    n = len(closes)
    out = _zeros(n)
    p = 0
    last = None
    n_piv = len(idx)
    for i in range(n):
        while p < n_piv and idx[p] + k <= i:
            last = px[p]
            p += 1
        if last is None:
            continue
        if _near(closes[i], last, atr[i]):
            out[i] = 1
    return out


def _dbl_signal(closes, highs, lows, k: int, *, want_high: bool) -> array:
    values = highs if want_high else lows
    idx, px = pivot_arrays(values, k, want_high)
    atr = atr_series(highs, lows, closes, NEAR_ATR_PERIOD)
    n = len(closes)
    out = _zeros(n)
    p = 0
    older = None
    newer = None
    n_piv = len(idx)
    for i in range(n):
        while p < n_piv and idx[p] + k <= i:
            older = newer
            newer = px[p]
            p += 1
        if older is None or newer is None:
            continue
        if not _holds(older, newer, atr[i]):
            continue
        close = closes[i]
        if want_high:
            if close < newer:
                out[i] = 1
        elif close > newer:
            out[i] = 1
    return out


def _don_signal(closes, highs, lows, n_bars: int, *, high: bool) -> array:
    n = len(closes)
    out = _zeros(n)
    if high:
        level = _donchian_series(highs, n_bars, high=True)
        for i in range(n):
            lv = level[i]
            if lv == lv and closes[i] > lv:
                out[i] = 1
        return out
    level = _donchian_series(lows, n_bars, high=False)
    atr = atr_series(highs, lows, closes, NEAR_ATR_PERIOD)
    for i in range(n):
        lv = level[i]
        if lv == lv and _near(closes[i], lv, atr[i]):
            out[i] = 1
    return out


def _compare_ma(closes, ma: array, *, above: bool) -> array:
    n = len(closes)
    out = _zeros(n)
    for i in range(n):
        v = ma[i]
        if v != v:
            continue
        if above and closes[i] > v:
            out[i] = 1
        elif not above and closes[i] < v:
            out[i] = 1
    return out


def _stack_signal(closes, periods: tuple[int, ...], *, kind: str) -> array:
    n = len(closes)
    out = _zeros(n)
    series = [
        sma_array(closes, p) if kind == "sma" else ema_array(closes, p)
        for p in periods
    ]
    width = len(series)
    for i in range(n):
        vals = [series[j][i] for j in range(width)]
        if any(v != v for v in vals):
            continue
        if closes[i] > vals[0] and all(vals[j] > vals[j + 1] for j in range(width - 1)):
            out[i] = 1
    return out


def _mom_signal(closes, lookback: int, threshold: float, *, gt: bool) -> array:
    n = len(closes)
    out = _zeros(n)
    if lookback <= 0:
        return out
    for i in range(lookback, n):
        r = closes[i] / closes[i - lookback] - 1.0
        if r != r:
            continue
        if gt and r > threshold:
            out[i] = 1
        elif not gt and r < threshold:
            out[i] = 1
    return out


def _htf_signal(closes, bars_per: int, period: int, kind: str) -> array:
    """Last completed HTF close above SMA/EMA of completed HTF closes."""
    from hedge_fund.signals.htf import completed_htf_count, htf_bucket_close_index

    n = len(closes)
    out = _zeros(n)
    if period <= 0 or bars_per <= 0:
        return out
    n_bars = n // bars_per
    htf = [closes[htf_bucket_close_index(k, bars_per)] for k in range(n_bars)]
    ma = sma_array(htf, period) if kind == "sma" else ema_array(htf, period)
    for i in range(n):
        n_complete = completed_htf_count(i, bars_per)
        if n_complete < period:
            continue
        if n_complete > len(htf):
            n_complete = len(htf)
        if n_complete < period:
            continue
        idx = n_complete - 1
        v = ma[idx]
        if v == v and htf[idx] > v:
            out[i] = 1
    return out


def _rsi_signal(closes, period: int, min_val: float, max_val: float) -> array:
    """Same window as ``dynamic.rsi``: mean gain / mean loss over ``period``."""
    n = len(closes)
    out = _zeros(n)
    if period <= 0:
        return out
    for i in range(period, n):
        gains = 0.0
        losses = 0.0
        for idx in range(i - period + 1, i + 1):
            diff = closes[idx] - closes[idx - 1]
            if diff >= 0:
                gains += diff
            else:
                losses += -diff
        avg_gain = gains / period
        avg_loss = losses / period
        if avg_loss == 0.0:
            r = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            r = 100.0 - (100.0 / (1.0 + rs))
        if r > min_val and r <= max_val:
            out[i] = 1
    return out


def _combine(parts: list[array], *, how: str) -> array:
    if not parts:
        return array("B")
    acc = array("B", parts[0])
    for extra in parts[1:]:
        if how == "&":
            for i in range(len(acc)):
                if not extra[i]:
                    acc[i] = 0
        else:
            for i in range(len(acc)):
                if extra[i]:
                    acc[i] = 1
    return acc


def _split_top(expr: str) -> tuple[str, list[str]] | None:
    """Same ``&`` / ``|`` split as ``parse_strategy``."""
    if "&" in expr and not (
        expr.startswith("(") and expr.endswith(")") and "&" not in expr[1:-1]
    ):
        return "&", [part.strip("() ") for part in expr.split("&")]
    if "|" in expr and not (
        expr.startswith("(") and expr.endswith(")") and "|" not in expr[1:-1]
    ):
        return "|", [part.strip("() ") for part in expr.split("|")]
    return None


def _atom_signal(expr: str, closes, highs, lows) -> array | None:
    """Vectorised atom, or None when the name is not one of these."""
    m = re.match(r"^near_swing_(hi|lo)_(\d+)$", expr)
    if m:
        return _near_swing_signal(
            closes, highs, lows, int(m.group(2)), want_high=m.group(1) == "hi"
        )
    m = re.match(r"^dbl_(bot|top)_(\d+)$", expr)
    if m:
        return _dbl_signal(
            closes, highs, lows, int(m.group(2)), want_high=m.group(1) == "top"
        )
    m = re.match(r"^don_(hi|lo)_(\d+)$", expr)
    if m:
        return _don_signal(
            closes, highs, lows, int(m.group(2)), high=m.group(1) == "hi"
        )
    m = re.match(r"^sma_stack(?:_([\d_]+))?$", expr)
    if m:
        periods = tuple(int(p) for p in m.group(1).split("_")) if m.group(1) else (7, 25, 50)
        return _stack_signal(closes, periods, kind="sma")
    m = re.match(r"^ema_stack(?:_([\d_]+))?$", expr)
    if m:
        periods = tuple(int(p) for p in m.group(1).split("_")) if m.group(1) else (7, 25, 50)
        return _stack_signal(closes, periods, kind="ema")
    m = re.match(r"^sma(?:_abv|_above)?_?(\d+)(?:_abv|_above)?$", expr)
    if m and not expr.startswith("sma_stack"):
        return _compare_ma(closes, sma_array(closes, int(m.group(1))), above=True)
    m = re.match(r"^ema(?:_abv|_above)?_?(\d+)(?:_abv|_above)?$", expr)
    if m and not expr.startswith("ema_stack"):
        return _compare_ma(closes, ema_array(closes, int(m.group(1))), above=True)
    m = re.match(r"^mom_(\d+)b?_gt_?(\d+)(?:pc)?$", expr)
    if m:
        val = int(m.group(2))
        thr = val / 100.0 if val >= 1 else float(val)
        return _mom_signal(closes, int(m.group(1)), thr, gt=True)
    m = re.match(r"^dip_(\d+)b?_lt_?(\d+)(?:pc)?$", expr)
    if m:
        val = int(m.group(2))
        thr = -(val / 100.0) if val >= 1 else -abs(float(val))
        return _mom_signal(closes, int(m.group(1)), thr, gt=False)
    m = re.match(r"^(h4|h1)_(ema|sma)_abv_(\d+)$", expr)
    if m:
        from hedge_fund.signals.htf import BARS_PER_H1, BARS_PER_H4

        bars = BARS_PER_H4 if m.group(1) == "h4" else BARS_PER_H1
        return _htf_signal(closes, bars, int(m.group(3)), m.group(2))
    m = re.match(r"^rsi_(\d+)_>_?(\d+)(?:_<_?(\d+))?$", expr)
    if m:
        ov = int(m.group(3)) if m.group(3) else 100
        return _rsi_signal(closes, int(m.group(1)), int(m.group(2)), ov)
    m = re.match(r"^rsi_(\d+)_<_?(\d+)$", expr)
    if m:
        return _rsi_signal(closes, int(m.group(1)), -1, int(m.group(2)))
    m = re.match(r"^rsi_(\d+)_(\d+)$", expr)
    if m:
        return _rsi_signal(closes, int(m.group(1)), int(m.group(2)), 100)
    return None


def _fallback_signal(expr: str, closes, highs, lows) -> array:
    """One pass of the existing predicate. Cached by the caller."""
    from hedge_fund.signals.dynamic import _parse_strategy_impl, eval_predicate

    pred = _parse_strategy_impl(expr)
    n = len(closes)
    out = _zeros(n)
    for i in range(n):
        if eval_predicate(pred, closes, i, highs=highs, lows=lows):
            out[i] = 1
    return out


def _build(expr: str, closes, highs, lows) -> array:
    spec = expr.strip()
    split = _split_top(spec)
    if split is not None:
        how, parts = split
        built = [_build(part, closes, highs, lows) for part in parts if part]
        if not built:
            return _zeros(len(closes))
        return _combine(built, how=how)
    key = (tape_digest(closes, highs, lows), "atom", spec)
    hit = cache_get(key)
    if hit is not None:
        return hit
    series = _atom_signal(spec, closes, highs, lows)
    if series is None:
        series = _fallback_signal(spec, closes, highs, lows)
    return cache_put(key, series)


def signal_series(expr: str, closes, highs, lows) -> array:
    """Boolean entry flag at every bar. Cached for this tape and expression."""
    spec = expr.strip()
    key = (tape_digest(closes, highs, lows), "sig", spec)
    hit = cache_get(key)
    if hit is not None:
        return hit
    series = _build(spec, closes, highs, lows)
    # ``_build`` already cached atoms. The compound series is cached too so
    # train and test (and a cost-stress rerun) index the same buffer.
    if cache_get(key) is None:
        return cache_put(key, series)
    return series
