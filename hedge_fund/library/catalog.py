"""The strategy library: named, parameterised, cited long-or-flat BTC rules.

Every entry is pre-registered here, with the rule the cited source actually
tested and how this implementation differs. Practitioner entries (no
peer-reviewed test) are marked ``peer_reviewed=False``. Citations were
checked against the papers on 2026-10-28; nothing here is inferred from a
title.

Each strategy has one canonical parameter set (the ``center``, normally the
value the source tested) and a small one-at-a-time grid of nearby values.
Only the canonical variant is claimed, evaluated as a candidate and can be
proposed. The grid exists for the neighbour and PBO checks and is not a
search space. Every grid point is a backtested configuration, so the
deflated-Sharpe trial count is the number of distinct configurations in the
whole library (``library_trial_count``).

A signal is the desired position after the close of each completed UTC day
(``True`` long, ``False`` flat). The engine acts on day ``t``'s value at the
open of day ``t + 1``. A fresh book whose signal is already on at the close
before the first scored open buys at that open; that entry is accepted.

A NaN indicator is not a flat signal. :func:`signal_ready` is false on those
days, and the scorer trims or refuses the window instead of trading it.

Names: ``lib.<key>`` is the canonical variant; ``lib.<key>.<v1>_<v2>...`` is
the same strategy with explicit parameter values in declaration order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from hedge_fund.library.daily import DAY_MS, DailyBars
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC
from hedge_fund.library.indicators import (
    adx_first_index,
    adx_wilder,
    aroon,
    bollinger,
    calendar_month,
    cci,
    connors_rsi,
    day_of_month,
    days_in_month,
    ema,
    hold_between,
    ibs,
    ichimoku_cloud_top,
    ichimoku_first_index,
    is_month_end,
    is_week_end,
    kama,
    keltner,
    lag,
    month_number,
    parabolic_sar_long,
    prior_max,
    prior_min,
    roc,
    rolling_max,
    rsi_wilder,
    sma,
    sma_tail,
    smma,
    stochastic_slow,
    supertrend_long,
    vortex,
    week_number,
    weekday,
    williams_r,
)

NAME_PREFIX = "lib."


@dataclass(frozen=True)
class Param:
    name: str
    center: int
    neighbours: tuple[int, ...]
    unit: str = "days"


@dataclass(frozen=True)
class Citation:
    authors: str
    year: int
    title: str
    venue: str
    peer_reviewed: bool
    tested_rule: str
    implemented_rule: str
    differences: str
    link: str = ""

    def short(self) -> str:
        base = f"{self.authors} ({self.year})" if self.year else self.authors
        if not self.peer_reviewed and "practitioner" not in base.lower():
            base += " (practitioner)"
        return base

    def as_dict(self) -> dict:
        return {
            "authors": self.authors,
            "year": self.year,
            "title": self.title,
            "venue": self.venue,
            "peer_reviewed": self.peer_reviewed,
            "tested_rule": self.tested_rule,
            "implemented_rule": self.implemented_rule,
            "differences": self.differences,
            "link": self.link,
            "short": self.short(),
        }


@dataclass(frozen=True)
class LibraryStrategy:
    key: str
    title: str
    family: str
    evaluation: str  # "daily" | "week_end" | "month_end"
    params: tuple[Param, ...]
    citation: Citation
    signal: Callable[[DailyBars, dict], np.ndarray] = field(repr=False, compare=False)
    # Catastrophic stop below the entry fill. Pre-registered at 20% for
    # every strategy (LIBRARY_STOP_FRAC). It is not a grid axis: scoring
    # a no-stop twin would double the deflated-Sharpe trial count.
    stop_frac: float = LIBRARY_STOP_FRAC

    @property
    def center(self) -> dict[str, int]:
        return {p.name: int(p.center) for p in self.params}


def _finite_gt(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.isfinite(a) & np.isfinite(b) & (a > b)


def _sma_cross(bars: DailyBars, p: dict) -> np.ndarray:
    close = bars.close
    short = close if int(p["short"]) == 1 else sma(close, int(p["short"]))
    return _finite_gt(short, sma(close, int(p["long"])))


def _price_sma(bars: DailyBars, p: dict) -> np.ndarray:
    return _finite_gt(bars.close, sma(bars.close, int(p["n"])))


def _ema_cross(bars: DailyBars, p: dict) -> np.ndarray:
    return _finite_gt(ema(bars.close, int(p["fast"])), ema(bars.close, int(p["slow"])))


def _macd(bars: DailyBars, p: dict) -> np.ndarray:
    line = ema(bars.close, int(p["fast"])) - ema(bars.close, int(p["slow"]))
    return _finite_gt(line, ema(line, int(p["signal"])))


def _detzel_log_ma(bars: DailyBars, p: dict) -> np.ndarray:
    logs = np.log(bars.close)
    return _finite_gt(logs, sma(logs, int(p["n"])))


def _period_ends(bars: DailyBars, mask: np.ndarray, period: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    idx = np.flatnonzero(mask)
    return idx, period[idx]


def _faber_monthly(bars: DailyBars, p: dict) -> np.ndarray:
    """Month-end close above the mean of the last ``months`` month-end closes."""
    m = int(p["months"])
    mask = is_month_end(bars.ts)
    idx, months = _period_ends(bars, mask, month_number(bars.ts))
    value = np.full(len(bars), np.nan)
    for j in range(m - 1, idx.shape[0]):
        if months[j] - months[j - m + 1] != m - 1:
            continue
        window = bars.close[idx[j - m + 1 : j + 1]]
        value[idx[j]] = 1.0 if bars.close[idx[j]] > float(np.mean(window)) else -1.0
    return hold_between(mask, value)


def _tsmom_periodic(bars: DailyBars, k: int, mask: np.ndarray, period: np.ndarray) -> np.ndarray:
    """Sign of the return since the evaluation close ``k`` periods earlier."""
    idx, per = _period_ends(bars, mask, period)
    value = np.full(len(bars), np.nan)
    for j in range(k, idx.shape[0]):
        if per[j] - per[j - k] != k:
            continue
        ret = bars.close[idx[j]] / bars.close[idx[j - k]] - 1.0
        value[idx[j]] = 1.0 if ret > 0.0 else -1.0
    return hold_between(mask, value)


def _tsmom_monthly(bars: DailyBars, p: dict) -> np.ndarray:
    return _tsmom_periodic(bars, int(p["months"]), is_month_end(bars.ts), month_number(bars.ts))


def _tsmom_weekly(bars: DailyBars, p: dict) -> np.ndarray:
    return _tsmom_periodic(bars, int(p["weeks"]), is_week_end(bars.ts), week_number(bars.ts))


def _bll_trb(bars: DailyBars, p: dict) -> np.ndarray:
    """Close above the prior ``n``-day closing high; hold ``hold`` days, ignore repeats."""
    n, hold = int(p["n"]), int(p["hold"])
    high = prior_max(bars.close, n)
    breakout = _finite_gt(bars.close, high)
    out = np.zeros(len(bars), dtype=bool)
    remaining = 0
    for t in range(len(bars)):
        if remaining > 0:
            out[t] = True
            remaining -= 1
        elif breakout[t]:
            out[t] = True
            remaining = hold - 1
    return out


def _turtle(bars: DailyBars, p: dict) -> np.ndarray:
    """Close above the prior ``entry``-day high; flat on a close below the prior ``exit``-day low."""
    upper = prior_max(bars.high, int(p["entry"]))
    lower = prior_min(bars.low, int(p["exit"]))
    out = np.zeros(len(bars), dtype=bool)
    long = False
    for t in range(len(bars)):
        c = bars.close[t]
        if not long:
            if np.isfinite(upper[t]) and c > upper[t]:
                long = True
        elif np.isfinite(lower[t]) and c < lower[t]:
            long = False
        out[t] = long
    return out


def _rsi2(bars: DailyBars, p: dict) -> np.ndarray:
    """RSI(2) below ``rsi_lt`` above the ``trend``-day SMA; flat on a close above the 5-day SMA."""
    close = bars.close
    rsi = rsi_wilder(close, 2)
    trend = sma(close, int(p["trend"]))
    exit_ma = sma(close, int(p["exit_ma"]))
    thr = float(p["rsi_lt"])
    out = np.zeros(len(bars), dtype=bool)
    long = False
    for t in range(len(bars)):
        if not long:
            if np.isfinite(trend[t]) and np.isfinite(rsi[t]) and close[t] > trend[t] and rsi[t] < thr:
                long = True
        elif np.isfinite(exit_ma[t]) and close[t] > exit_ma[t]:
            long = False
        out[t] = long
    return out


def _stateful(enter: np.ndarray, leave: np.ndarray) -> np.ndarray:
    """Long from the bar ``enter`` becomes true until a later ``leave`` bar.

    A bar where both are true enters and does not exit. Indicators that
    are still NaN must be false in both masks: that is "not a signal",
    and ``signal_ready`` is what keeps those days out of the score.
    """
    out = np.zeros(int(enter.shape[0]), dtype=bool)
    long = False
    for t in range(out.shape[0]):
        if not long:
            if bool(enter[t]):
                long = True
        elif bool(leave[t]):
            long = False
        out[t] = long
    return out


def _turtle20(bars: DailyBars, p: dict) -> np.ndarray:
    """Faith (2003), Turtle System 1: 20-day high to enter, 10-day low to exit."""
    return _turtle(bars, p)


def _donchian20(bars: DailyBars, p: dict) -> np.ndarray:
    """Donchian 4-week rule: enter on the prior 20-day high, exit on the prior 20-day low."""
    n = int(p["n"])
    return _turtle(bars, {"entry": n, "exit": n})


def _ema12x26(bars: DailyBars, p: dict) -> np.ndarray:
    """Appel (1979): the 12/26 EMA cross, which is the MACD line above zero."""
    return _ema_cross(bars, p)


def _ema50x200(bars: DailyBars, p: dict) -> np.ndarray:
    """Practitioner 50/200 EMA golden cross. Distinct from the SMA ``lib.golden_cross``."""
    return _ema_cross(bars, p)


def _bb_revert(bars: DailyBars, p: dict) -> np.ndarray:
    """Bollinger (2001): close below the lower band, exit at the middle band."""
    mid, lower, _upper = bollinger(bars.close, int(p["n"]), float(p["width"]))
    with np.errstate(invalid="ignore"):
        enter = np.isfinite(lower) & (bars.close < lower)
        leave = np.isfinite(mid) & (bars.close >= mid)
    return _stateful(enter, leave)


def _bb_break(bars: DailyBars, p: dict) -> np.ndarray:
    """Bollinger (2001): close above the upper band, exit back through the middle band."""
    mid, _lower, upper = bollinger(bars.close, int(p["n"]), float(p["width"]))
    with np.errstate(invalid="ignore"):
        leave = np.isfinite(mid) & (bars.close < mid)
    return _stateful(_finite_gt(bars.close, upper), leave)


def _keltner_break(bars: DailyBars, p: dict) -> np.ndarray:
    """Keltner channel breakout: close above the upper band, exit below the EMA."""
    mid, upper = keltner(
        bars.high, bars.low, bars.close, int(p["ema"]), int(p["atr"]), float(p["width"]),
    )
    with np.errstate(invalid="ignore"):
        leave = np.isfinite(mid) & (bars.close < mid)
    return _stateful(_finite_gt(bars.close, upper), leave)


def _rsi_level(bars: DailyBars, p: dict) -> np.ndarray:
    """Wilder (1978): long while RSI(n) is above the centerline (textbook 50)."""
    value = rsi_wilder(bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > float(p["thr"]))


def _rsi2_10(bars: DailyBars, p: dict) -> np.ndarray:
    """Connors & Alvarez (2008): RSI(2) below 10 above the 200-day SMA, exit at the 5-day SMA."""
    return _rsi2(bars, p)


def _tsmom_daily(bars: DailyBars, p: dict) -> np.ndarray:
    """Moskowitz, Ooi & Pedersen (2012) sign rule: close above the close ``n`` days ago."""
    n = int(p["n"])
    prev = np.full(len(bars), np.nan, dtype=np.float64)
    if 0 < n < len(bars):
        prev[n:] = bars.close[:-n]
    return _finite_gt(bars.close, prev)


def _adx_trend(bars: DailyBars, p: dict) -> np.ndarray:
    """Wilder (1978): +DI above -DI and ADX above 25, period 14."""
    plus, minus, adx = adx_wilder(bars.high, bars.low, bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return (
            np.isfinite(plus) & np.isfinite(minus) & np.isfinite(adx)
            & (plus > minus) & (adx > float(p["adx_gt"]))
        )


def _ichimoku(bars: DailyBars, p: dict) -> np.ndarray:
    """Hosoda (1969): long when the close is above the cloud plotted on that day."""
    top = ichimoku_cloud_top(
        bars.high, bars.low, int(p["conv"]), int(p["base"]), int(p["span"]),
    )
    return _finite_gt(bars.close, top)


def _supertrend(bars: DailyBars, p: dict) -> np.ndarray:
    """Seban Supertrend: long while the ATR trailing band's direction is up."""
    return supertrend_long(bars.high, bars.low, bars.close, int(p["n"]), float(p["mult"]))


def _willr(bars: DailyBars, p: dict) -> np.ndarray:
    """Williams (1973) %R: buy below -80 above the 200-day SMA, exit above -20."""
    wr = williams_r(bars.high, bars.low, bars.close, int(p["n"]))
    trend = sma(bars.close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        enter = (
            np.isfinite(wr) & np.isfinite(trend)
            & (bars.close > trend) & (wr < -float(p["os"]))
        )
        leave = np.isfinite(wr) & (wr > -20.0)
    return _stateful(enter, leave)


def _prev(x: np.ndarray) -> np.ndarray:
    """``x`` shifted forward one bar. Index 0 is NaN. No future bar is read."""
    out = np.full(x.shape[0], np.nan, dtype=np.float64)
    if x.shape[0] > 1:
        out[1:] = x[:-1]
    return out


def _psar(bars: DailyBars, p: dict) -> np.ndarray:
    """Wilder (1978): long while the parabolic SAR trend is up.

    The acceleration step is the parameter in hundredths (2 = 0.02). The
    cap stays 0.20, his published maximum.
    """
    step = int(p["af"]) / 100.0
    return parabolic_sar_long(bars.high, bars.low, bars.close, step, 0.20)


def _kama_trend(bars: DailyBars, p: dict) -> np.ndarray:
    """Kaufman (1995): long while the close is above KAMA. Fast 2 and slow 30 stay fixed."""
    return _finite_gt(bars.close, kama(bars.close, int(p["n"]), fast=2, slow=30))


def _aroon_trend(bars: DailyBars, p: dict) -> np.ndarray:
    """Chande (1995): long while Aroon up is above Aroon down."""
    up, down = aroon(bars.high, bars.low, int(p["n"]))
    return _finite_gt(up, down)


def _cci_break(bars: DailyBars, p: dict) -> np.ndarray:
    """Lambert (1980): long while CCI is above +100 (his trend threshold)."""
    value = cci(bars.high, bars.low, bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > float(p["level"]))


def _cci_revert(bars: DailyBars, p: dict) -> np.ndarray:
    """Later textbook fade of Lambert's bands: buy below -100, exit at zero.

    This is not Lambert's 1980 breakout. ``lib.cci100`` is that breakout.
    """
    value = cci(bars.high, bars.low, bars.close, int(p["n"]))
    level = float(p["level"])
    with np.errstate(invalid="ignore"):
        enter = np.isfinite(value) & (value < -level)
        leave = np.isfinite(value) & (value >= 0.0)
    return _stateful(enter, leave)


def _stoch(bars: DailyBars, p: dict) -> np.ndarray:
    """Lane: slow %K crosses above %D below 20, and back below %D above 80.

    The smoother is 3 and 3. The overbought line is 100 minus the oversold
    line, so the pair stays symmetric. A cross uses %K and %D at ``t`` and
    ``t - 1`` only.
    """
    slow_k, slow_d = stochastic_slow(bars.high, bars.low, bars.close, int(p["n"]), 3)
    os = float(p["os"])
    ob = 100.0 - os
    k_prev = _prev(slow_k)
    d_prev = _prev(slow_d)
    with np.errstate(invalid="ignore"):
        known = np.isfinite(slow_k) & np.isfinite(slow_d) & np.isfinite(k_prev) & np.isfinite(d_prev)
        enter = known & (slow_k > slow_d) & (k_prev <= d_prev) & (slow_k < os)
        leave = known & (slow_k < slow_d) & (k_prev >= d_prev) & (slow_k > ob)
    return _stateful(enter, leave)


def _crsi(bars: DailyBars, p: dict) -> np.ndarray:
    """Connors CRSI(3, 2, rank): buy below the oversold line, exit above 90.

    90 is his overbought zone and is not a grid axis. The three pieces are
    RSI(3) of the close, RSI(2) of the up/down streak, and the percent rank
    of today's one-day change against the previous ``rank`` changes.
    """
    value = connors_rsi(bars.close, int(p["rank"]))
    with np.errstate(invalid="ignore"):
        enter = np.isfinite(value) & (value < float(p["thr"]))
        leave = np.isfinite(value) & (value > 90.0)
    return _stateful(enter, leave)


def _tomorrow(ts: np.ndarray) -> np.ndarray:
    """The next UTC date. Known at today's close; the next bar is not read."""
    return np.asarray(ts, dtype=np.int64) + DAY_MS


def _tom(bars: DailyBars, p: dict) -> np.ndarray:
    """McConnell & Xu (2008): long on the last ``last`` days and the first ``first`` days.

    The signal at today's close is the position for tomorrow's open, so the
    window is evaluated on tomorrow's calendar date.
    """
    when = _tomorrow(bars.ts)
    dom = day_of_month(when)
    dim = days_in_month(when)
    last_n = int(p["last"])
    first_n = int(p["first"])
    return (dom <= first_n) | (dom > dim - last_n)


def _halloween(bars: DailyBars, p: dict) -> np.ndarray:
    """Bouman & Jacobsen (2002): long from ``entry`` month through ``exit`` month.

    The published window is November through April. A month is included on
    tomorrow's open, decided at today's close.
    """
    month = calendar_month(_tomorrow(bars.ts))
    entry = int(p["entry"])
    exit_m = int(p["exit"])
    if entry > exit_m:
        return (month >= entry) | (month <= exit_m)
    return (month >= entry) & (month <= exit_m)


def _weekend(bars: DailyBars, p: dict) -> np.ndarray:
    """French (1980): flat across the weekend, long the other days.

    ``span`` 1 is Sunday, 2 is Saturday and Sunday, 3 adds Friday. The
    position is for tomorrow, so Friday's close sells into Saturday.
    """
    span = int(p["span"])
    if span <= 1:
        flat = {6}
    elif span == 2:
        flat = {5, 6}
    else:
        flat = {4, 5, 6}
    tomorrow = weekday(_tomorrow(bars.ts))
    return ~np.isin(tomorrow, list(flat))


def _high52(bars: DailyBars, p: dict) -> np.ndarray:
    """George & Hwang (2004): long while the close is within ``band`` percent of the high.

    The high is the highest close of the last ``n`` days, today included.
    ``band`` 0 requires a close at that high. 5 means within 5 percent.
    """
    high = rolling_max(bars.close, int(p["n"]))
    floor = high * (1.0 - float(p["band"]) / 100.0)
    with np.errstate(invalid="ignore"):
        return np.isfinite(high) & (bars.close >= floor)


def _bll_band(bars: DailyBars, p: dict) -> np.ndarray:
    """Brock, Lakonishok & LeBaron (1992): the 1% band around the long SMA.

    Buy only through the upper band, sell only through the lower band.
    Inside the band the previous position stands. ``lib.sma200`` has no band.
    """
    ma = sma(bars.close, int(p["long"]))
    width = float(p["band"]) / 100.0
    with np.errstate(invalid="ignore"):
        upper = ma * (1.0 + width)
        lower = ma * (1.0 - width)
        enter = np.isfinite(upper) & (bars.close > upper)
        leave = np.isfinite(lower) & (bars.close < lower)
    return _stateful(enter, leave)


def _keltner_revert(bars: DailyBars, p: dict) -> np.ndarray:
    """Buy a close through the lower Keltner band; exit at the EMA.

    Same channel as ``lib.keltner20``. That name is the upside breakout.
    """
    mid, upper = keltner(
        bars.high, bars.low, bars.close, int(p["ema"]), int(p["atr"]), float(p["width"]),
    )
    lower = 2.0 * mid - upper
    with np.errstate(invalid="ignore"):
        enter = np.isfinite(lower) & (bars.close < lower)
        leave = np.isfinite(mid) & (bars.close >= mid)
    return _stateful(enter, leave)


def _rsi30(bars: DailyBars, p: dict) -> np.ndarray:
    """Wilder (1978): buy RSI below the oversold line, exit above the symmetric line.

    The exit is ``100 - os`` (30/70 at the center). ``lib.rsi14`` is the
    centerline trend filter, not this reversal.
    """
    value = rsi_wilder(bars.close, int(p["n"]))
    os = float(p["os"])
    with np.errstate(invalid="ignore"):
        enter = np.isfinite(value) & (value < os)
        leave = np.isfinite(value) & (value > 100.0 - os)
    return _stateful(enter, leave)


def _vortex_trend(bars: DailyBars, p: dict) -> np.ndarray:
    """Botes & Siepman (2010): long while VI+ is above VI-."""
    plus, minus = vortex(bars.high, bars.low, bars.close, int(p["n"]))
    return _finite_gt(plus, minus)


def _trix(bars: DailyBars, p: dict) -> np.ndarray:
    """Hutson (1983): long while the triple-smoothed EMA is rising (TRIX > 0)."""
    n = int(p["n"])
    third = ema(ema(ema(bars.close, n), n), n)
    return _finite_gt(third, _prev(third))


def _awesome(bars: DailyBars, p: dict) -> np.ndarray:
    """Williams: long while the 5-bar SMA of the median is above the 34-bar SMA.

    That difference is the awesome oscillator. Positive means the fast SMA
    is above the slow one.
    """
    median = (bars.high + bars.low) / 2.0
    return _finite_gt(sma(median, int(p["fast"])), sma(median, int(p["slow"])))


def _kst(bars: DailyBars, p: dict) -> np.ndarray:
    """Pring's daily KST above its signal SMA.

    The four rates are fixed at the published daily set: ROC 10/15/20/30,
    smoothed by SMA 10/10/10/15, weights 1/2/3/4. Only the signal length moves.
    """
    weights = ((10, 10, 1.0), (15, 10, 2.0), (20, 10, 3.0), (30, 15, 4.0))
    line = np.zeros(len(bars), dtype=np.float64)
    warm = np.ones(len(bars), dtype=bool)
    for roc_n, sma_n, weight in weights:
        piece = sma_tail(roc(bars.close, roc_n), sma_n)
        line = line + weight * np.where(np.isfinite(piece), piece, 0.0)
        warm &= np.isfinite(piece)
    line = np.where(warm, line, np.nan)
    signal = sma_tail(line, int(p["signal"]))
    return _finite_gt(line, signal)


def _nr7(bars: DailyBars, p: dict) -> np.ndarray:
    """Crabel (1990): hold after a close through the high of an NR day.

    Day ``t - 1`` is NR(``n``) when its high-low range is strictly the
    narrowest of the ``n`` days ending that day. The position then stays
    on for ``hold`` days, including the breakout day. Repeats during the
    hold are ignored, as in the fixed-hold breakout already in the library.
    """
    n = int(p["n"])
    hold = int(p["hold"])
    span = bars.high - bars.low
    out = np.zeros(len(bars), dtype=bool)
    remaining = 0
    for t in range(len(bars)):
        if remaining > 0:
            out[t] = True
            remaining -= 1
            continue
        setup = t - 1
        if setup < n - 1:
            continue
        window = span[setup - n + 1 : setup + 1]
        if float(span[setup]) < float(np.min(window[:-1])) and bars.close[t] > bars.high[setup]:
            out[t] = True
            remaining = hold - 1
    return out


def _alligator(bars: DailyBars, p: dict) -> np.ndarray:
    """Williams (1995): lips above teeth above jaw, using the lagged SMMA values.

    Jaw is SMMA(13) read 8 bars later, teeth SMMA(8) read 5 later, lips
    SMMA(5) read 3 later. The forward plot in the book is the value known
    that many bars afterwards, so the reading at ``t`` uses no bar after ``t``.
    Only the jaw length is a parameter; 8/5 and the three shifts stay put.
    """
    median = (bars.high + bars.low) / 2.0
    jaw = lag(smma(median, int(p["jaw"])), 8)
    teeth = lag(smma(median, 8), 5)
    lips = lag(smma(median, 5), 3)
    with np.errstate(invalid="ignore"):
        return np.isfinite(jaw) & np.isfinite(teeth) & np.isfinite(lips) & (lips > teeth) & (teeth > jaw)


def _ibs_revert(bars: DailyBars, p: dict) -> np.ndarray:
    """Pagonidis (2013) IBS, held until 0.80, and only entered above the 200-day SMA."""
    value = ibs(bars.high, bars.low, bars.close)
    trend = sma(bars.close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        enter = (
            np.isfinite(value) & np.isfinite(trend)
            & (bars.close > trend) & (value < float(p["ibs_lt"]) / 100.0)
        )
        leave = np.isfinite(value) & (value > 0.80)
    return _stateful(enter, leave)


def _defined_from(n: int, first: int) -> np.ndarray:
    out = np.zeros(int(n), dtype=bool)
    if 0 <= int(first) < int(n):
        out[int(first) :] = True
    return out


def _eval_ready(bars: DailyBars, start_j: int, lag: int, mask: np.ndarray, period: np.ndarray) -> np.ndarray:
    """True from the first evaluation whose ``lag`` earlier period exists, onward.

    A later evaluation that skips a period (a missing month-end) makes the
    whole mask false: that tape is not stably warm, and the caller refuses
    it instead of reading the gap as flat.
    """
    idx = np.flatnonzero(np.asarray(mask, dtype=bool))
    out = np.zeros(len(bars), dtype=bool)
    if lag <= 0 or idx.size <= start_j:
        return out
    per = np.asarray(period)
    first = None
    for j in range(int(start_j), int(idx.shape[0])):
        if int(per[idx[j]] - per[idx[j - lag]]) != lag:
            if first is not None:
                return np.zeros(len(bars), dtype=bool)
            continue
        if first is None:
            first = int(idx[j])
    if first is None:
        return out
    out[first:] = True
    return out


def signal_ready(strategy: LibraryStrategy, bars: DailyBars, params: dict) -> np.ndarray:
    """True where every input of this parameter set is finite.

    The longest neighbour is a separate ``params`` dict. Callers score a day
    only when this mask is true on the close that day acts on. False means
    "not defined yet", not "be flat".
    """
    n = len(bars)
    key = strategy.key
    if key in ("sma200", "sma5x150", "golden_cross"):
        short_n = int(params["short"])
        long_ok = _defined_from(n, int(params["long"]) - 1)
        if short_n <= 1:
            return long_ok
        return long_ok & _defined_from(n, short_n - 1)
    if key == "sma100":
        return _defined_from(n, int(params["n"]) - 1)
    if key in ("ema20x50", "ema12x26", "ema50x200"):
        return _defined_from(n, max(int(params["fast"]), int(params["slow"])) - 1)
    if key == "macd":
        # Slow EMA is finite at ``slow - 1``. The signal EMA then needs
        # ``signal`` finite values of that line.
        first = (int(params["slow"]) - 1) + (int(params["signal"]) - 1)
        return _defined_from(n, first)
    if key == "detzel2w":
        return _defined_from(n, int(params["n"]) - 1)
    if key == "faber10m":
        months = int(params["months"])
        return _eval_ready(
            bars, months - 1, months - 1, is_month_end(bars.ts), month_number(bars.ts),
        )
    if key in ("tsmom12m", "tsmom6m", "tsmom3m"):
        k = int(params["months"])
        return _eval_ready(bars, k, k, is_month_end(bars.ts), month_number(bars.ts))
    if key == "tsmom1w":
        k = int(params["weeks"])
        return _eval_ready(bars, k, k, is_week_end(bars.ts), week_number(bars.ts))
    if key == "bll_trb50":
        # prior_max is finite at index n (the window excludes day t).
        return _defined_from(n, int(params["n"]))
    if key in ("turtle55", "turtle20"):
        return _defined_from(n, max(int(params["entry"]), int(params["exit"])))
    if key == "donchian20":
        # Same prior-window rule as the turtle: finite at index ``n``.
        return _defined_from(n, int(params["n"]))
    if key in ("rsi2", "rsi2_10"):
        first = max(2, int(params["trend"]) - 1, int(params["exit_ma"]) - 1)
        return _defined_from(n, first)
    if key in ("bb_revert", "bb_break"):
        # SMA and population stdev of ``n`` closes are finite at index n - 1.
        return _defined_from(n, int(params["n"]) - 1)
    if key == "keltner20":
        # EMA seed at ema-1; Wilder ATR at index ``atr``.
        return _defined_from(n, max(int(params["ema"]) - 1, int(params["atr"])))
    if key == "rsi14":
        return _defined_from(n, int(params["n"]))
    if key in ("tsmom30d", "tsmom90d", "tsmom180d"):
        return _defined_from(n, int(params["n"]))
    if key == "adx14":
        return _defined_from(n, adx_first_index(int(params["n"])))
    if key == "ichimoku":
        return _defined_from(n, ichimoku_first_index(int(params["base"]), int(params["span"])))
    if key == "supertrend":
        return _defined_from(n, int(params["n"]))
    if key == "willr":
        return _defined_from(n, max(int(params["n"]) - 1, int(params["trend"]) - 1))
    if key == "ibs":
        return _defined_from(n, int(params["trend"]) - 1)
    # Batch 3. Same rule as above: the first bar whose inputs all exist,
    # and every bar after it. A calendar rule is known on the first close.
    if key == "psar":
        return _defined_from(n, 2)
    if key == "kama":
        # Seeded at index n (the close itself); the ER needs n changes.
        return _defined_from(n, int(params["n"]))
    if key == "aroon":
        # n days back through today.
        return _defined_from(n, int(params["n"]))
    if key in ("cci100", "cci_revert"):
        return _defined_from(n, int(params["n"]) - 1)
    if key == "stoch":
        # Fast %K at n-1, two SMA(3) smooths, then one prior bar for the cross.
        # smooth is the fixed 3: (n - 1) + 2 * 2 + 1 = n + 4.
        return _defined_from(n, int(params["n"]) + 4)
    if key == "crsi":
        # Percent rank of today's change vs the previous ``rank`` changes.
        return _defined_from(n, int(params["rank"]) + 1)
    if key in ("tom", "halloween", "weekend"):
        return np.ones(n, dtype=bool)
    if key == "high52":
        return _defined_from(n, int(params["n"]) - 1)
    if key == "bll_band":
        return _defined_from(n, int(params["long"]) - 1)
    if key == "keltner_revert":
        return _defined_from(n, max(int(params["ema"]) - 1, int(params["atr"])))
    if key == "rsi30":
        return _defined_from(n, int(params["n"]))
    if key == "vortex":
        return _defined_from(n, int(params["n"]))
    if key == "trix":
        # Three EMA seeds, then one prior triple-smoothed value.
        return _defined_from(n, 3 * int(params["n"]) - 2)
    if key == "awesome":
        return _defined_from(n, max(int(params["fast"]), int(params["slow"])) - 1)
    if key == "kst":
        # ROC(30) SMA(15) is finite at 44; the signal SMA needs ``signal`` of those.
        return _defined_from(n, 43 + int(params["signal"]))
    if key == "nr7":
        return _defined_from(n, int(params["n"]))
    if key == "alligator":
        # SMMA(jaw) at jaw-1, then the 8-bar lag. Teeth and lips are shorter.
        return _defined_from(n, int(params["jaw"]) - 1 + 8)
    raise ValueError(f"no warmup rule for {key}")


def library_score_index(bars: DailyBars) -> int:
    """First bar index whose open may be scored.

    Every grid point, including each strategy's longest neighbour, is finite
    on the previous close, and stays finite through the end of ``bars``.
    One index for the whole library, so every strategy and buy-and-hold
    share the span.
    """
    if len(bars) < 2:
        raise ValueError("BTC daily tape cannot warm the library (fewer than 2 days)")
    latest = 1
    for strategy in LIBRARY:
        for params in grid_params(strategy):
            ready = signal_ready(strategy, bars, params)
            if not bool(ready.any()) or not bool(ready[int(np.flatnonzero(ready)[0]) :].all()):
                raise ValueError(
                    f"{strategy.key} {params} is not warm on this tape; "
                    "a missing lookback is not scored as flat"
                )
            first = int(np.flatnonzero(ready)[0]) + 1
            if first >= len(bars):
                raise ValueError(
                    f"{strategy.key} {params} warms only on the last bar; "
                    "a missing lookback is not scored as flat"
                )
            latest = max(latest, first)
    return latest


def sma_stack_signal(bars: DailyBars) -> np.ndarray:
    """``sma_stack`` baseline on daily bars: close > SMA(7) > SMA(25) > SMA(50)."""
    close = bars.close
    s7, s25, s50 = sma(close, 7), sma(close, 25), sma(close, 50)
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(s7) & np.isfinite(s25) & np.isfinite(s50)
        return ok & (close > s7) & (s7 > s25) & (s25 > s50)


_BLL = dict(
    authors="Brock, Lakonishok & LeBaron",
    year=1992,
    title="Simple Technical Trading Rules and the Stochastic Properties of Stock Returns",
    venue="The Journal of Finance 47(5), 1731-1764",
    peer_reviewed=True,
    link="https://doi.org/10.1111/j.1540-6261.1992.tb04681.x",
)
_MOP = dict(
    authors="Moskowitz, Ooi & Pedersen",
    year=2012,
    title="Time Series Momentum",
    venue="Journal of Financial Economics 104(2), 228-250",
    peer_reviewed=True,
    link="https://doi.org/10.1016/j.jfineco.2011.11.003",
)
_MOP_TESTED = (
    "Each month, long a futures/forward contract if its past k-month excess return is "
    "positive and short if negative, held h months, sized to constant ex-ante volatility; "
    "58 instruments. Look-backs and holds of 1, 3, 6, 9, 12, 24, 36 and 48 months were "
    "tested; the headline strategy is k=12, h=1."
)
_MOP_DIFF = (
    "Long or flat instead of long/short, no volatility scaling, raw return instead of "
    "excess return over T-bills, one asset (BTC), fills at the next daily open."
)
_PRACTITIONER_MA = (
    "Practitioner convention. No peer-reviewed test of this exact rule was found; it is "
    "a baseline, not a literature result."
)

LIBRARY: tuple[LibraryStrategy, ...] = (
    LibraryStrategy(
        key="sma200",
        title="Price vs 200-day SMA",
        family="trend",
        evaluation="daily",
        params=(
            Param("short", 1, (2,)),
            Param("long", 200, (150, 250)),
        ),
        signal=_sma_cross,
        citation=Citation(
            **_BLL,
            tested_rule=(
                "Variable-length moving average (VMA) 1-200: a buy day when the 1-day "
                "(price) is above the 200-day MA, sell otherwise; also 1-50, 1-150, 5-150 "
                "and 2-200, with and without a 1% band. DJIA daily 1897-1986, buy-day vs "
                "sell-day returns."
            ),
            implemented_rule="Long when the daily close is above its 200-day SMA, flat otherwise.",
            differences=(
                "Long or flat (BLL compared buy-day and sell-day returns), no band, BTC, "
                "fills at the next daily open after fees. Neighbours 2-200 and 1-150 are "
                "BLL rules; 1-250 is a perturbation."
            ),
        ),
    ),
    LibraryStrategy(
        key="sma5x150",
        title="5-day SMA vs 150-day SMA",
        family="trend",
        evaluation="daily",
        params=(
            Param("short", 5, (4, 6)),
            Param("long", 150, (120, 180)),
        ),
        signal=_sma_cross,
        citation=Citation(
            **_BLL,
            tested_rule="VMA 5-150 (5-day MA vs 150-day MA), same setup as their 1-200 rule.",
            implemented_rule="Long when the 5-day SMA is above the 150-day SMA, flat otherwise.",
            differences="Long or flat, no band, BTC, next-open fills after fees.",
        ),
    ),
    LibraryStrategy(
        key="sma100",
        title="Price vs 100-day SMA",
        family="trend",
        evaluation="daily",
        params=(Param("n", 100, (80, 120)),),
        signal=_price_sma,
        citation=Citation(
            authors="Practitioner",
            year=0,
            title="Price above its 100-day simple moving average",
            venue="Practitioner convention",
            peer_reviewed=False,
            tested_rule=_PRACTITIONER_MA,
            implemented_rule="Long when the daily close is above its 100-day SMA, flat otherwise.",
            differences=(
                "A nearby variant of the BLL 1-150 / 1-200 rules. BLL did not test a "
                "100-day long average."
            ),
        ),
    ),
    LibraryStrategy(
        key="golden_cross",
        title="50/200-day SMA cross (golden / death cross)",
        family="trend",
        evaluation="daily",
        params=(
            Param("short", 50, (40, 60)),
            Param("long", 200, (150, 250)),
        ),
        signal=_sma_cross,
        citation=Citation(
            authors="Practitioner",
            year=0,
            title="Golden cross / death cross",
            venue="Practitioner convention",
            peer_reviewed=False,
            tested_rule=_PRACTITIONER_MA,
            implemented_rule="Long when the 50-day SMA is above the 200-day SMA, flat otherwise.",
            differences="BLL tested 5-150 and 2-200 crosses, not 50-200.",
        ),
    ),
    LibraryStrategy(
        key="ema20x50",
        title="20/50-day EMA cross",
        family="trend",
        evaluation="daily",
        params=(
            Param("fast", 20, (16, 24)),
            Param("slow", 50, (40, 60)),
        ),
        signal=_ema_cross,
        citation=Citation(
            authors="Practitioner",
            year=0,
            title="Exponential moving-average crossover",
            venue="Practitioner convention",
            peer_reviewed=False,
            tested_rule=_PRACTITIONER_MA,
            implemented_rule="Long when the 20-day EMA is above the 50-day EMA, flat otherwise.",
            differences="EMAs are seeded with the SMA of their first n closes.",
        ),
    ),
    LibraryStrategy(
        key="faber10m",
        title="Faber monthly 10-month SMA timing",
        family="trend",
        evaluation="month_end",
        params=(Param("months", 10, (8, 12), unit="months"),),
        signal=_faber_monthly,
        citation=Citation(
            authors="Faber",
            year=2007,
            title="A Quantitative Approach to Tactical Asset Allocation",
            venue="The Journal of Wealth Management 9(4), 69-79",
            peer_reviewed=True,
            link="https://doi.org/10.3905/jwm.2007.674809",
            tested_rule=(
                "Buy when the month-end price is above its 10-month SMA, sell to cash when "
                "below. Updated only on the last day of the month; trades at that close. "
                "Total-return series of US stocks and other asset classes."
            ),
            implemented_rule=(
                "At each UTC month-end close, long if the close is above the mean of the "
                "last 10 month-end closes, else flat; held until the next month-end."
            ),
            differences=(
                "Fills at the next daily open (Faber traded the month-end close), BTC price "
                "series, no T-bill return on cash, fees charged."
            ),
        ),
    ),
    LibraryStrategy(
        key="tsmom12m",
        title="12-month time-series momentum",
        family="momentum",
        evaluation="month_end",
        params=(Param("months", 12, (9, 15), unit="months"),),
        signal=_tsmom_monthly,
        citation=Citation(
            **_MOP,
            tested_rule=_MOP_TESTED,
            implemented_rule=(
                "At each UTC month-end close, long if the close is above the close 12 "
                "month-ends earlier, else flat; held one month."
            ),
            differences=_MOP_DIFF,
        ),
    ),
    LibraryStrategy(
        key="tsmom6m",
        title="6-month time-series momentum",
        family="momentum",
        evaluation="month_end",
        params=(Param("months", 6, (5, 7), unit="months"),),
        signal=_tsmom_monthly,
        citation=Citation(
            **_MOP,
            tested_rule=_MOP_TESTED,
            implemented_rule="Same rule with a 6-month look-back (MOP tested k=6).",
            differences=_MOP_DIFF,
        ),
    ),
    LibraryStrategy(
        key="tsmom3m",
        title="3-month time-series momentum",
        family="momentum",
        evaluation="month_end",
        params=(Param("months", 3, (2, 4), unit="months"),),
        signal=_tsmom_monthly,
        citation=Citation(
            **_MOP,
            tested_rule=_MOP_TESTED,
            implemented_rule="Same rule with a 3-month look-back (MOP tested k=3).",
            differences=_MOP_DIFF,
        ),
    ),
    LibraryStrategy(
        key="tsmom1w",
        title="1-week time-series momentum (crypto horizon)",
        family="momentum",
        evaluation="week_end",
        params=(Param("weeks", 1, (2, 3), unit="weeks"),),
        signal=_tsmom_weekly,
        citation=Citation(
            authors="Liu & Tsyvinski",
            year=2021,
            title="Risks and Returns of Cryptocurrency",
            venue="The Review of Financial Studies 34(6), 2689-2727",
            peer_reviewed=True,
            link="https://doi.org/10.1093/rfs/hhaa113",
            tested_rule=(
                "Time-series regressions: the current daily and weekly bitcoin return "
                "predicts returns 1-4 weeks ahead; weekly returns sorted into quintiles, "
                "top quintile outperforms. They did not test a sign rule as a strategy."
            ),
            implemented_rule=(
                "At each Sunday UTC close, long if the close is above the previous "
                "Sunday's close, else flat; held one week."
            ),
            differences=(
                "Relabelled: the sign rule is the Moskowitz, Ooi & Pedersen (2012) TSMOM "
                "form; Liu & Tsyvinski supply only the weekly horizon evidence for bitcoin."
            ),
        ),
    ),
    LibraryStrategy(
        key="detzel2w",
        title="Price vs 2-week log-price MA (bitcoin)",
        family="trend",
        evaluation="daily",
        params=(Param("n", 14, (10, 18)),),
        signal=_detzel_log_ma,
        citation=Citation(
            authors="Detzel, Liu, Strauss, Zhou & Zhu",
            year=2021,
            title=(
                "Learning and predictability via technical analysis: Evidence from bitcoin "
                "and stocks with hard-to-value fundamentals"
            ),
            venue="Financial Management 50(1), 107-137",
            peer_reviewed=True,
            link="https://doi.org/10.1111/fima.12310",
            tested_rule=(
                "Long bitcoin when the log price is above the equal-weight moving average "
                "of log prices over L = 1, 2, 4, 10 or 20 weeks (7-day weeks: 7 to 140 "
                "days), otherwise the risk-free asset. Daily bitcoin, Dec 2010 - Jun 2018."
            ),
            implemented_rule=(
                "Long when log(close) is above the 14-day mean of log closes (L = 2 weeks), "
                "flat otherwise."
            ),
            differences=(
                "No risk-free return when flat, fees and next-open fills. 10 and 18 days are "
                "perturbations; the paper's other lengths are 7, 28, 70 and 140 days."
            ),
        ),
    ),
    LibraryStrategy(
        key="bll_trb50",
        title="50-day trading-range breakout, 10-day hold",
        family="breakout",
        evaluation="daily",
        params=(
            Param("n", 50, (40, 60)),
            Param("hold", 10, (8, 12)),
        ),
        signal=_bll_trb,
        citation=Citation(
            **_BLL,
            tested_rule=(
                "Trading-range break-out (TRB): buy when the price rises above the local "
                "maximum of the preceding 50, 150 or 200 days, with returns measured over "
                "a fixed 10-day holding period after the signal."
            ),
            implemented_rule=(
                "Long for 10 days after a close above the prior 50-day closing high; "
                "breakouts inside the holding period are ignored, one on its last day "
                "extends it."
            ),
            differences="Long or flat, BTC, next-open fills after fees, no band.",
        ),
    ),
    LibraryStrategy(
        key="turtle55",
        title="Donchian 55/20 breakout (Turtle System 2)",
        family="breakout",
        evaluation="daily",
        params=(
            Param("entry", 55, (44, 66)),
            Param("exit", 20, (16, 24)),
        ),
        signal=_turtle,
        citation=Citation(
            authors="Faith (Turtle rules)",
            year=2003,
            title="The Original Turtle Trading Rules (System 2)",
            venue="Practitioner document; see also Faith, Way of the Turtle (2007)",
            peer_reviewed=False,
            tested_rule=(
                "System 2: enter long when price exceeds the high of the preceding 55 days, "
                "exit when it falls below the low of the preceding 20 days; traded intraday "
                "at the breakout, ATR-based sizing and a 2N stop."
            ),
            implemented_rule=(
                "Long after a daily close above the prior 55-day high, flat after a close "
                "below the prior 20-day low."
            ),
            differences=(
                "Close-confirmed signals with next-open fills (not intraday stops), all-in "
                "sizing, no 2N stop, no pyramiding, long only."
            ),
        ),
    ),
    LibraryStrategy(
        key="rsi2",
        title="RSI(2) dip-buy above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("rsi_lt", 5, (4, 6), unit="RSI"),
            Param("trend", 200, (150, 250)),
            Param("exit_ma", 5, (4, 6)),
        ),
        signal=_rsi2,
        citation=Citation(
            authors="Connors & Alvarez",
            year=2008,
            title="Short Term Trading Strategies That Work",
            venue="Practitioner book (TradingMarkets)",
            peer_reviewed=False,
            tested_rule=(
                "S&P 500: price above its 200-day MA and 2-period RSI below 5, buy on the "
                "close; exit on a close above the 5-day MA. No stop."
            ),
            implemented_rule=(
                "Long after a close above the 200-day SMA with Wilder RSI(2) below 5; flat "
                "after a close above the 5-day SMA."
            ),
            differences="Next-open fills instead of the signal close, BTC, fees charged.",
        ),
    ),
    LibraryStrategy(
        key="macd",
        title="MACD(12,26,9) above its signal line",
        family="momentum",
        evaluation="daily",
        params=(
            Param("fast", 12, (10, 14)),
            Param("slow", 26, (21, 31)),
            Param("signal", 9, (7, 11)),
        ),
        signal=_macd,
        citation=Citation(
            authors="Appel",
            year=1979,
            title="Moving Average Convergence-Divergence (MACD)",
            venue="Practitioner indicator; Appel, Technical Analysis: Power Tools for Active Investors (2005)",
            peer_reviewed=False,
            tested_rule=(
                "MACD line = 12-period EMA minus 26-period EMA; signal line = 9-period EMA "
                "of the MACD line; bullish when the MACD line is above the signal line."
            ),
            implemented_rule="Long while the daily MACD(12,26) line is above its 9-day signal line.",
            differences="No published performance test is relied on; practitioner baseline.",
        ),
    ),
    # Batch 2 (2026-10-30). Appended after the original 15. Claims walk
    # this tuple in order, so these names are leased only once the names
    # above are tested or on the book. Do not insert above ``macd``.
    LibraryStrategy(
        key="turtle20",
        title="Donchian 20/10 breakout (Turtle System 1)",
        family="breakout",
        evaluation="daily",
        params=(
            Param("entry", 20, (16, 24)),
            Param("exit", 10, (8, 12)),
        ),
        signal=_turtle20,
        citation=Citation(
            authors="Faith (Turtle rules)",
            year=2003,
            title="The Original Turtle Trading Rules (System 1)",
            venue="Practitioner document; see also Faith, Way of the Turtle (2007)",
            peer_reviewed=False,
            tested_rule=(
                "System 1: enter long when price exceeds the high of the preceding 20 days, "
                "exit when it falls below the low of the preceding 10 days; traded intraday "
                "at the breakout, with ATR sizing and a 2N stop."
            ),
            implemented_rule=(
                "Long after a daily close above the prior 20-day high, flat after a close "
                "below the prior 10-day low."
            ),
            differences=(
                "Close-confirmed signals with next-open fills, all-in sizing, no 2N stop, "
                "no pyramiding, long only. System 2 is already lib.turtle55 (55-day entry, "
                "20-day exit)."
            ),
        ),
    ),
    LibraryStrategy(
        key="donchian20",
        title="Donchian 20-day channel (4-week rule)",
        family="breakout",
        evaluation="daily",
        params=(Param("n", 20, (16, 24)),),
        signal=_donchian20,
        citation=Citation(
            authors="Donchian",
            year=0,
            title="4-week rule (20-day Donchian channel)",
            venue="Practitioner commodity trend rule; restated in Faith, Way of the Turtle (2007)",
            peer_reviewed=False,
            tested_rule=(
                "Buy when price exceeds the highest high of the prior four weeks (20 trading "
                "days) and sell when it falls below the lowest low of those four weeks."
            ),
            implemented_rule=(
                "Long after a daily close above the prior 20-day high, flat after a close "
                "below the prior 20-day low. Entry and exit use the same window."
            ),
            differences=(
                "Close-confirmed, next-open fills, all-in, long only. Distinct from "
                "lib.turtle20, which exits on the 10-day low, and from lib.turtle55."
            ),
        ),
    ),
    LibraryStrategy(
        key="ema12x26",
        title="12/26-day EMA cross (MACD line above zero)",
        family="trend",
        evaluation="daily",
        params=(
            Param("fast", 12, (10, 14)),
            Param("slow", 26, (21, 31)),
        ),
        signal=_ema12x26,
        citation=Citation(
            authors="Appel",
            year=1979,
            title="MACD line above zero",
            venue="Practitioner indicator; Appel, Technical Analysis: Power Tools for Active Investors (2005)",
            peer_reviewed=False,
            tested_rule=(
                "The MACD line is the 12-period EMA minus the 26-period EMA. The line is "
                "above zero exactly when the 12-period EMA is above the 26-period EMA. "
                "The 9-period signal-line cross is a separate rule."
            ),
            implemented_rule="Long while the 12-day EMA of the close is above the 26-day EMA.",
            differences=(
                "This is the zero-line rule. lib.macd is the signal-line cross on the same "
                "12/26 pair and is not this signal. EMAs are seeded with the SMA of their "
                "first n closes. Long or flat, next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="ema50x200",
        title="50/200-day EMA cross",
        family="trend",
        evaluation="daily",
        params=(
            Param("fast", 50, (40, 60)),
            Param("slow", 200, (150, 250)),
        ),
        signal=_ema50x200,
        citation=Citation(
            authors="Practitioner",
            year=0,
            title="50/200-day exponential moving-average cross",
            venue="Practitioner convention",
            peer_reviewed=False,
            tested_rule=_PRACTITIONER_MA,
            implemented_rule="Long when the 50-day EMA is above the 200-day EMA, flat otherwise.",
            differences=(
                "lib.golden_cross is the same 50/200 pair of simple averages. lib.ema20x50 "
                "is the shorter EMA cross. EMAs are seeded with the SMA of their first n closes."
            ),
        ),
    ),
    LibraryStrategy(
        key="bb_revert",
        title="Bollinger mean reversion (20, 2)",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("n", 20, (16, 24)),
            Param("width", 2, (1, 3), unit="stdev"),
        ),
        signal=_bb_revert,
        citation=Citation(
            authors="Bollinger",
            year=2001,
            title="Bollinger on Bollinger Bands",
            venue="Practitioner book (McGraw-Hill)",
            peer_reviewed=False,
            tested_rule=(
                "Bands are a 20-period simple average plus and minus 2 population standard "
                "deviations of the close. A close through the lower band is the downside "
                "extreme; the middle band is the mean the price is reverting toward."
            ),
            implemented_rule=(
                "Long after a close below the lower band; flat after a close at or above "
                "the middle band (the 20-day SMA)."
            ),
            differences=(
                "Bollinger presented the bands, not a single published performance test of "
                "this exit. Long or flat, next-open fills, BTC, fees. The width is in "
                "population standard deviations (divide by n)."
            ),
        ),
    ),
    LibraryStrategy(
        key="bb_break",
        title="Bollinger breakout (20, 2)",
        family="breakout",
        evaluation="daily",
        params=(
            Param("n", 20, (16, 24)),
            Param("width", 2, (1, 3), unit="stdev"),
        ),
        signal=_bb_break,
        citation=Citation(
            authors="Bollinger",
            year=2001,
            title="Bollinger on Bollinger Bands",
            venue="Practitioner book (McGraw-Hill)",
            peer_reviewed=False,
            tested_rule=(
                "The same 20-period, 2-standard-deviation bands. A close through the upper "
                "band is the upside breakout; a return through the middle band ends it."
            ),
            implemented_rule=(
                "Long after a close above the upper band; flat after a close below the "
                "middle band."
            ),
            differences=(
                "Not the mean-reversion rule in lib.bb_revert. No published performance "
                "test is relied on. Long or flat, next-open fills, BTC, no short side."
            ),
        ),
    ),
    LibraryStrategy(
        key="keltner20",
        title="Keltner channel breakout (20, 2 × ATR(10))",
        family="breakout",
        evaluation="daily",
        params=(
            Param("ema", 20, (16, 24)),
            Param("atr", 10, (7, 14)),
            Param("width", 2, (1, 3), unit="ATR"),
        ),
        signal=_keltner_break,
        citation=Citation(
            authors="Keltner",
            year=1960,
            title="How to Make Money in Commodities",
            venue="Practitioner book; the 20-day EMA and 2×ATR(10) channel is the later textbook form",
            peer_reviewed=False,
            tested_rule=(
                "Keltner's 1960 channel is a moving average of typical price with bands from "
                "the average high-low range. Later textbooks and chart packages set the "
                "middle line to a 20-period EMA of typical price and the bands at 2 times "
                "Wilder ATR(10)."
            ),
            implemented_rule=(
                "Long after a close above the upper band; flat after a close below the "
                "20-day EMA of typical price. The band is 2 × Wilder ATR(10)."
            ),
            differences=(
                "Not the 1960 SMA-of-range formula. EMA and Wilder ATR are the parameter "
                "set in current textbooks, not values fit on this tape. Long or flat, "
                "next-open fills, no short side, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="rsi14",
        title="RSI(14) above 50",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 14, (10, 18)),
            Param("thr", 50, (45, 55), unit="RSI"),
        ),
        signal=_rsi_level,
        citation=Citation(
            authors="Wilder",
            year=1978,
            title="New Concepts in Technical Trading Systems",
            venue="Practitioner book",
            peer_reviewed=False,
            tested_rule=(
                "Wilder's RSI defaults to 14 periods and is centered at 50. Above 50 the "
                "average gain exceeds the average loss (a bullish trend filter); below 50 "
                "the reverse."
            ),
            implemented_rule="Long while Wilder RSI(14) of the close is above 50, flat otherwise.",
            differences=(
                "Wilder's traded systems combine RSI with divergences and points, not this "
                "level alone. The 50 line is his center, not a fitted threshold. Long or "
                "flat, next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="rsi2_10",
        title="RSI(2) below 10 above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("rsi_lt", 10, (8, 12), unit="RSI"),
            Param("trend", 200, (150, 250)),
            Param("exit_ma", 5, (4, 6)),
        ),
        signal=_rsi2_10,
        citation=Citation(
            authors="Connors & Alvarez",
            year=2008,
            title="Short Term Trading Strategies That Work",
            venue="Practitioner book (TradingMarkets)",
            peer_reviewed=False,
            tested_rule=(
                "The same S&P 500 dip-buy as the RSI(2) rule: price above its 200-day MA, "
                "enter when the 2-period RSI is below a published cutoff, exit on a close "
                "above the 5-day MA. The cutoff of 5 is lib.rsi2. The looser cutoff "
                "associated with that rule is 10."
            ),
            implemented_rule=(
                "Long after a close above the 200-day SMA with Wilder RSI(2) below 10; flat "
                "after a close above the 5-day SMA."
            ),
            differences=(
                "Not a neighbour of lib.rsi2 (those cutoffs are 4 and 6). 10 is the "
                "standard looser cutoff of this rule, not a value chosen on this tape. "
                "Next-open fills, BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="tsmom30d",
        title="30-day time-series momentum",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 30, (21, 42)),),
        signal=_tsmom_daily,
        citation=Citation(
            **_MOP,
            tested_rule=_MOP_TESTED,
            implemented_rule=(
                "Long when the daily close is above the close 30 days earlier, flat otherwise."
            ),
            differences=(
                "MOP signed the past k-month excess return at month-end and went long or "
                "short, volatility-scaled, across futures. 30 days is the daily form of "
                "the 1-month look-back they tested. The month-end long/flat version is "
                "lib.tsmom3m (and lib.tsmom1w at a week). One asset, no scaling, raw "
                "close-to-close, next-open fills."
            ),
        ),
    ),
    LibraryStrategy(
        key="tsmom90d",
        title="90-day time-series momentum",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 90, (60, 120)),),
        signal=_tsmom_daily,
        citation=Citation(
            **_MOP,
            tested_rule=_MOP_TESTED,
            implemented_rule=(
                "Long when the daily close is above the close 90 days earlier, flat otherwise."
            ),
            differences=(
                "Daily form of the 3-month look-back MOP tested (k=3). lib.tsmom3m is that "
                "rule at month-end. Long or flat, no volatility scaling, raw close-to-close, "
                "next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="tsmom180d",
        title="180-day time-series momentum",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 180, (150, 210)),),
        signal=_tsmom_daily,
        citation=Citation(
            **_MOP,
            tested_rule=_MOP_TESTED,
            implemented_rule=(
                "Long when the daily close is above the close 180 days earlier, flat otherwise."
            ),
            differences=(
                "Daily form of the 6-month look-back MOP tested (k=6). lib.tsmom6m is that "
                "rule at month-end. Long or flat, no volatility scaling, raw close-to-close, "
                "next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="adx14",
        title="ADX(14) trend with +DI above -DI",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 14, (10, 18)),
            Param("adx_gt", 25, (20, 30), unit="ADX"),
        ),
        signal=_adx_trend,
        citation=Citation(
            authors="Wilder",
            year=1978,
            title="New Concepts in Technical Trading Systems",
            venue="Practitioner book (directional movement)",
            peer_reviewed=False,
            tested_rule=(
                "14-period plus and minus directional movement, smoothed, as +DI and -DI, "
                "and ADX as the smoothed DX. +DI above -DI is the bullish reading. A "
                "developed trend is the textbook ADX level of 25 used with that system."
            ),
            implemented_rule=(
                "Long while +DI(14) is above -DI(14) and ADX(14) is above 25, flat otherwise."
            ),
            differences=(
                "Wilder's traded system also uses an extreme-point stop and a rising ADX. "
                "This is the level filter only. A zero-range day is DI 0, not a missing "
                "lookback. Long or flat, next-open fills, BTC. 25 is the textbook level, "
                "not fit here."
            ),
        ),
    ),
    LibraryStrategy(
        key="ichimoku",
        title="Ichimoku price above the cloud (9, 26, 52)",
        family="trend",
        evaluation="daily",
        params=(
            Param("conv", 9, (7, 12)),
            Param("base", 26, (22, 30)),
            Param("span", 52, (42, 60)),
        ),
        signal=_ichimoku,
        citation=Citation(
            authors="Hosoda",
            year=1969,
            title="Ichimoku Kinko Hyo",
            venue="Practitioner; standard parameters 9, 26, and 52",
            peer_reviewed=False,
            tested_rule=(
                "Conversion line 9, base line 26, span B 52. The cloud (senkou A and B) is "
                "plotted 26 periods ahead. Price above the cloud is the bullish filter."
            ),
            implemented_rule=(
                "Long when the daily close is above both spans of the cloud plotted on "
                "that day. The plotted cloud is the spans computed one base-period earlier."
            ),
            differences=(
                "The tenkan/kijun cross and the chikou span are not required. The forward "
                "plot uses only bars through t minus the base period, so it is not a "
                "look-ahead. Long or flat, next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="supertrend",
        title="Supertrend (10, 3)",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 10, (7, 14)),
            Param("mult", 3, (2, 4), unit="ATR"),
        ),
        signal=_supertrend,
        citation=Citation(
            authors="Seban",
            year=0,
            title="Supertrend",
            venue="Practitioner indicator; the published chart default is ATR(10) and multiplier 3",
            peer_reviewed=False,
            tested_rule=(
                "Bands sit a multiple of ATR around the median price. The trend turns up "
                "after a close through the upper band and down after a close through the "
                "lower band. Each band ratchets so the stop only tightens with the trend."
            ),
            implemented_rule=(
                "Long while Supertrend direction is up: ATR period 10, multiplier 3, "
                "median price (high+low)/2."
            ),
            differences=(
                "No published performance test is relied on. The first ATR bar is long "
                "only if that close is already above the upper band. Close-confirmed, "
                "next-open fills, all-in, long only, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="willr",
        title="Williams %R(14) dip above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("n", 14, (10, 20)),
            Param("os", 80, (70, 90), unit="percent"),
            Param("trend", 200, (150, 250)),
        ),
        signal=_willr,
        citation=Citation(
            authors="Williams",
            year=1973,
            title="How I Made One Million Dollars Last Year Trading Commodities",
            venue="Practitioner book (Williams %R)",
            peer_reviewed=False,
            tested_rule=(
                "Williams %R places the close in the recent high-low range on a scale from "
                "0 down to -100. Below -80 is oversold and above -20 is overbought. "
                "Textbooks use 14 periods; his own short length is 10, which is a neighbour."
            ),
            implemented_rule=(
                "Long after a close above the 200-day SMA with %R(14) below -80; flat "
                "after %R rises above -20."
            ),
            differences=(
                "The 200-day SMA filter is not in the 1973 commodity method. The -20 exit "
                "is the textbook overbought line and is not a grid axis. Next-open fills, "
                "BTC, long only. 14 and -80 are the textbook pair, not fit here."
            ),
        ),
    ),
    LibraryStrategy(
        key="ibs",
        title="IBS mean reversion above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("ibs_lt", 20, (10, 30), unit="percent"),
            Param("trend", 200, (150, 250)),
        ),
        signal=_ibs_revert,
        citation=Citation(
            authors="Pagonidis",
            year=2013,
            title="The IBS Effect: Mean Reversion in Equity ETFs",
            venue="SSRN working paper",
            peer_reviewed=False,
            tested_rule=(
                "Internal bar strength is (close - low) / (high - low). An IBS below 0.2 "
                "was followed by a high next-day return in equity ETFs. The published "
                "trade buys that close and is flat the next day."
            ),
            implemented_rule=(
                "Long after IBS below 0.20 while the close is above the 200-day SMA; flat "
                "after IBS rises above 0.80."
            ),
            differences=(
                "The paper's trade is a one-day hold with no trend filter. This can stay "
                "long until IBS exceeds 0.80, and it enters only above the 200-day SMA. "
                "The 0.80 exit is the symmetric high threshold and is not a grid axis. "
                "Next-open fills instead of the signal close, BTC, fees."
            ),
        ),
    ),
    # Batch 3 (2026-11-01). Appended after batch 2. Claims walk this tuple
    # in order, so these names are leased only once every name above is
    # tested or on the book. Do not insert above ``ibs``.
    LibraryStrategy(
        key="psar",
        title="Parabolic SAR uptrend (step 0.02, max 0.20)",
        family="trend",
        evaluation="daily",
        params=(Param("af", 2, (1, 3), unit="hundredths"),),
        signal=_psar,
        citation=Citation(
            authors="Wilder",
            year=1978,
            title="New Concepts in Technical Trading Systems",
            venue="Practitioner book (parabolic time/price system)",
            peer_reviewed=False,
            tested_rule=(
                "SAR starts at 0.02 and steps up by 0.02, capped at 0.20, whenever a new "
                "extreme is made. The stop ratchets toward price and flips the trend when "
                "price trades through it."
            ),
            implemented_rule=(
                "Long while the SAR trend is up. The step is 0.02 and the cap stays 0.20. "
                "A completed day's low or high is what flips the trend."
            ),
            differences=(
                "Wilder flipped intrabar and traded both ways. This waits for the daily "
                "close, then fills at the next open, long or flat, all-in. The first two "
                "bars are not a position. Neighbours are steps of 0.01 and 0.03; the 0.20 "
                "cap is not a grid axis."
            ),
        ),
    ),
    LibraryStrategy(
        key="kama",
        title="Close above KAMA(10, 2, 30)",
        family="trend",
        evaluation="daily",
        params=(Param("n", 10, (8, 12)),),
        signal=_kama_trend,
        citation=Citation(
            authors="Kaufman",
            year=1995,
            title="Smarter Trading",
            venue="Practitioner book (McGraw-Hill)",
            peer_reviewed=False,
            tested_rule=(
                "The adaptive moving average blends a fast and a slow EMA by the square of "
                "the efficiency ratio. Kaufman's published constants are an ER of 10, a "
                "fast EMA of 2 and a slow EMA of 30."
            ),
            implemented_rule=(
                "Long when the daily close is above KAMA(10, 2, 30), flat otherwise."
            ),
            differences=(
                "The fast and slow constants stay 2 and 30; only the ER length is a grid "
                "axis. The seed at the ER length equals that close. Not an EMA cross "
                "(lib.ema20x50, lib.ema50x200) and not a price-versus-SMA rule. Long or "
                "flat, next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="aroon",
        title="Aroon(25) up above down",
        family="trend",
        evaluation="daily",
        params=(Param("n", 25, (20, 30)),),
        signal=_aroon_trend,
        citation=Citation(
            authors="Chande",
            year=1995,
            title="The Aroon Indicator",
            venue="Technical Analysis of Stocks & Commodities, September 1995",
            peer_reviewed=False,
            tested_rule=(
                "Aroon up is how recently the n-day high printed, Aroon down how recently "
                "the n-day low printed, each scaled from 0 to 100. The published length is "
                "25. Up above down is the bullish reading."
            ),
            implemented_rule=(
                "Long while Aroon up(25) is above Aroon down(25). The window runs from "
                "n days ago through today. A tie takes the most recent extreme."
            ),
            differences=(
                "The oscillator crossing zero is the same comparison and is not a second "
                "strategy. No published performance test is relied on. Long or flat, "
                "next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="cci100",
        title="CCI(20) above +100",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 20, (16, 24)),
            Param("level", 100, (80, 120), unit="CCI"),
        ),
        signal=_cci_break,
        citation=Citation(
            authors="Lambert",
            year=1980,
            title="Commodity Channel Index: Tool for Trading Cyclic Trends",
            venue="Commodities magazine, October 1980",
            peer_reviewed=False,
            tested_rule=(
                "CCI is the typical price's distance from its SMA in mean deviations, "
                "scaled by 0.015 so that most values fall inside ±100. Lambert treated a "
                "move through +100 as a new uptrend and a fall back through +100 as its end. "
                "The length he used was 20."
            ),
            implemented_rule=(
                "Long while CCI(20) is above +100, flat otherwise. The 0.015 constant is "
                "not a parameter."
            ),
            differences=(
                "This is the level, which is the state after his cross. lib.cci_revert is "
                "the later fade of the lower band and is not this rule. Long or flat, "
                "next-open fills, BTC. 20 and 100 are his pair, not fit here."
            ),
        ),
    ),
    LibraryStrategy(
        key="cci_revert",
        title="CCI(20) fade from -100 back to zero",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("n", 20, (16, 24)),
            Param("level", 100, (80, 120), unit="CCI"),
        ),
        signal=_cci_revert,
        citation=Citation(
            authors="Lambert",
            year=1980,
            title="Commodity Channel Index: Tool for Trading Cyclic Trends",
            venue="Commodities magazine, October 1980; the fade is the later textbook use of his ±100 bands",
            peer_reviewed=False,
            tested_rule=(
                "Lambert built ±100 as trend thresholds, not as levels to fade. Later "
                "charting texts buy a CCI below -100 and sell the return to zero, the "
                "mean-reversion reading of the same bands."
            ),
            implemented_rule=(
                "Long after CCI(20) closes below -100; flat after it closes at or above 0."
            ),
            differences=(
                "Not lib.cci100, which is Lambert's own above-+100 trend rule. The zero "
                "exit is the center of the indicator and is not a grid axis. Long or flat, "
                "next-open fills, BTC. No short side."
            ),
        ),
    ),
    LibraryStrategy(
        key="stoch",
        title="Slow stochastic 14, 3, 3 cross from 20",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("n", 14, (10, 20)),
            Param("os", 20, (15, 25), unit="percent"),
        ),
        signal=_stoch,
        citation=Citation(
            authors="Lane",
            year=1984,
            title="Lane's Stochastics",
            venue="Technical Analysis of Stocks & Commodities (Investment Educators method)",
            peer_reviewed=False,
            tested_rule=(
                "Stochastic %K places the close in the recent high-low range. Lane's "
                "traded reading is a %K cross of %D. The textbook slow form is %K(14) "
                "smoothed by 3, %D a further 3, with 20 oversold and 80 overbought."
            ),
            implemented_rule=(
                "Long after slow %K crosses above %D while %K is below 20; flat after "
                "slow %K crosses below %D while %K is above 80."
            ),
            differences=(
                "The 3, 3 smoothers are fixed. The overbought line is 100 minus the "
                "oversold line, so it is not a separate axis. A zero-range window is 50, "
                "not a hole. Next-open fills, long only, BTC. 14 and 20 are the textbook "
                "pair, not fit here."
            ),
        ),
    ),
    LibraryStrategy(
        key="crsi",
        title="Connors RSI(3, 2, 100) below 10",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("thr", 10, (5, 15), unit="CRSI"),
            Param("rank", 100, (80, 120)),
        ),
        signal=_crsi,
        citation=Citation(
            authors="Connors",
            year=2014,
            title="An Introduction to Connors RSI",
            venue="Connors Research white paper",
            peer_reviewed=False,
            tested_rule=(
                "CRSI is the average of RSI(3) of the close, a 2-period RSI of the "
                "consecutive up/down streak, and the percent rank of today's one-day "
                "return against the previous 100. Readings below 10 are the oversold zone "
                "in the return tables; readings above 90 are the overbought zone."
            ),
            implemented_rule=(
                "Long after CRSI(3, 2, 100) closes below 10; flat after it closes above 90."
            ),
            differences=(
                "The white paper's tables mostly hold for a fixed number of days. This "
                "exits at 90, which is not a grid axis. Not lib.rsi2 or lib.rsi2_10: those "
                "are RSI(2) with a 200-day filter, not the three-piece composite, and this "
                "rule has no trend filter. Next-open fills, BTC, long only."
            ),
        ),
    ),
    LibraryStrategy(
        key="tom",
        title="Turn of the month (last day and first three)",
        family="seasonality",
        evaluation="daily",
        params=(
            Param("last", 1, (2,), unit="days"),
            Param("first", 3, (2, 4), unit="days"),
        ),
        signal=_tom,
        citation=Citation(
            authors="McConnell & Xu",
            year=2008,
            title="Equity Returns at the Turn of the Month",
            venue="Financial Analysts Journal 64(2), 49-64",
            peer_reviewed=True,
            link="https://doi.org/10.2469/faj.v64.n2.11",
            tested_rule=(
                "The turn-of-the-month window is the last trading day of the month and the "
                "first three trading days of the next month. Over 1926-2005 that window "
                "accounted for the equity premium in the US and in most of the 35 countries "
                "they checked."
            ),
            implemented_rule=(
                "Long for the last UTC day of the month and the first three UTC days of "
                "the next month. The decision is taken at the previous close, for the "
                "next open."
            ),
            differences=(
                "Their days are exchange sessions. BTC trades every UTC day, so the window "
                "is calendar days, including weekends. Long or flat, all-in, next-open "
                "fills, one asset. The position does not scale with the premium they measured."
            ),
        ),
    ),
    LibraryStrategy(
        key="halloween",
        title="Sell in May (long November through April)",
        family="seasonality",
        evaluation="daily",
        params=(
            Param("entry", 11, (10, 12), unit="month"),
            Param("exit", 4, (3, 5), unit="month"),
        ),
        signal=_halloween,
        citation=Citation(
            authors="Bouman & Jacobsen",
            year=2002,
            title='The Halloween Indicator, "Sell in May and Go Away": Another Puzzle',
            venue="American Economic Review 92(5), 1618-1635",
            peer_reviewed=True,
            link="https://doi.org/10.1257/000282802762024683",
            tested_rule=(
                "Stock returns over November-April beat May-October in 36 of 37 countries. "
                "The Halloween rule they examine is long from the start of November through "
                "April and out from May through October."
            ),
            implemented_rule=(
                "Long on UTC days in November through April, flat in May through October. "
                "The close before the month changes is what flips the position at the next open."
            ),
            differences=(
                "They compared monthly index returns, not a traded BTC book. Long or flat, "
                "all-in, next-open fills, fees charged. Neighbours slide the entry or exit "
                "month by one. October is not in the center window."
            ),
        ),
    ),
    LibraryStrategy(
        key="weekend",
        title="Flat across the weekend",
        family="seasonality",
        evaluation="daily",
        params=(Param("span", 2, (1, 3), unit="days"),),
        signal=_weekend,
        citation=Citation(
            authors="French",
            year=1980,
            title="Stock returns and the weekend effect",
            venue="Journal of Financial Economics 8(1), 55-69",
            peer_reviewed=True,
            link="https://doi.org/10.1016/0304-405X(80)90021-5",
            tested_rule=(
                "Monday returns on the S&P composite, 1953-1977, are negative. The negative "
                "part is the Friday close to the Monday open, which he reads as a weekend effect."
            ),
            implemented_rule=(
                "Flat on Saturday and Sunday UTC, long Monday through Friday. Friday's close "
                "sells at Saturday's open; Sunday's close buys at Monday's open."
            ),
            differences=(
                "Equities could not be sold on Saturday. BTC trades both weekend days, so "
                "the flat is those two sessions rather than a Friday-close to Monday-open "
                "gap. Span 1 drops only Sunday; span 3 adds Friday. Long or flat, all-in, "
                "next-open fills, one asset."
            ),
        ),
    ),
    LibraryStrategy(
        key="high52",
        title="Within 5% of the 252-day high",
        family="momentum",
        evaluation="daily",
        params=(
            Param("n", 252, (200, 280)),
            Param("band", 5, (0, 10), unit="percent"),
        ),
        signal=_high52,
        citation=Citation(
            authors="George & Hwang",
            year=2004,
            title="The 52-Week High and Momentum Investing",
            venue="The Journal of Finance 59(5), 2145-2176",
            peer_reviewed=True,
            link="https://doi.org/10.1111/j.1540-6261.2004.00695.x",
            tested_rule=(
                "Each month, stocks are ranked by the ratio of the current price to the "
                "highest price of the past 12 months (about 252 sessions). The top 30% is "
                "the winner portfolio, held six months. Nearness to that high predicts "
                "returns better than a past-return sort."
            ),
            implemented_rule=(
                "Long while the daily close is within 5% of the highest close of the last "
                "252 days, today included. A band of 0 requires the close to equal that high."
            ),
            differences=(
                "The paper is a cross-sectional rank and a six-month hold, not a single-asset "
                "timing rule. 5% is a stated nearness band, not a cutoff they tested; 0% and "
                "10% are the neighbours. On this tape 252 bars are 252 UTC days, not 252 "
                "equity sessions. Not a Donchian stay-long (lib.turtle55, lib.donchian20): "
                "the position is off as soon as the close leaves the band. Long or flat, "
                "next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="bll_band",
        title="200-day SMA with a 1% band",
        family="trend",
        evaluation="daily",
        params=(
            Param("long", 200, (150, 250)),
            Param("band", 1, (2,), unit="percent"),
        ),
        signal=_bll_band,
        citation=Citation(
            **_BLL,
            tested_rule=(
                "The same VMA rules as their moving-average tests, plus a 1% band: buy when "
                "price rises above the MA by 1%, sell when it falls below the MA by 1%. "
                "Inside the band the previous signal stands. DJIA daily, 1897-1986."
            ),
            implemented_rule=(
                "Long after the close is more than 1% above the 200-day SMA; flat after the "
                "close is more than 1% below it. Between the bands the position is unchanged."
            ),
            differences=(
                "lib.sma200 is their no-band 1-200 rule and flips every time price crosses "
                "the average. This is the band they also tested. Long or flat, no short-day "
                "book, BTC, next-open fills after fees. The 2% neighbour is a perturbation; "
                "150 and 250 match the other VMA lengths already in the catalog."
            ),
        ),
    ),
    LibraryStrategy(
        key="keltner_revert",
        title="Keltner mean reversion (20, 2 × ATR(10))",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("ema", 20, (16, 24)),
            Param("atr", 10, (7, 14)),
            Param("width", 2, (1, 3), unit="ATR"),
        ),
        signal=_keltner_revert,
        citation=Citation(
            authors="Keltner",
            year=1960,
            title="How to Make Money in Commodities",
            venue="Practitioner book; the 20-day EMA and 2×ATR(10) channel is the later textbook form",
            peer_reviewed=False,
            tested_rule=(
                "The channel is the same textbook form as lib.keltner20: a 20-period EMA of "
                "typical price and bands at 2 times Wilder ATR(10). Buying the lower band "
                "and selling the EMA is the mean-reversion use of that channel."
            ),
            implemented_rule=(
                "Long after a close below the lower band; flat after a close at or above "
                "the 20-day EMA of typical price."
            ),
            differences=(
                "lib.keltner20 buys the upper band instead. Not the 1960 SMA-of-range "
                "formula. Not lib.bb_revert: the width is ATR, not a standard deviation. "
                "Long or flat, next-open fills, no short side, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="rsi30",
        title="RSI(14) buy below 30, exit above 70",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("n", 14, (10, 18)),
            Param("os", 30, (25, 35), unit="RSI"),
        ),
        signal=_rsi30,
        citation=Citation(
            authors="Wilder",
            year=1978,
            title="New Concepts in Technical Trading Systems",
            venue="Practitioner book",
            peer_reviewed=False,
            tested_rule=(
                "Wilder's RSI defaults to 14. He marked 30 as oversold and 70 as overbought. "
                "His own trades also asked for divergence or a failure swing; the mechanical "
                "pair is the textbook system built from those two levels."
            ),
            implemented_rule=(
                "Long after RSI(14) closes below 30; flat after it closes above 70. The "
                "exit is 100 minus the entry level."
            ),
            differences=(
                "lib.rsi14 is long while RSI is above 50 and is not this reversal. lib.rsi2 "
                "is the 2-period Connors dip, not RSI(14). Divergence is not required. "
                "Next-open fills, long only, BTC. 14, 30 and 70 are his levels, not fit here."
            ),
        ),
    ),
    LibraryStrategy(
        key="vortex",
        title="Vortex(14) VI+ above VI-",
        family="trend",
        evaluation="daily",
        params=(Param("n", 14, (10, 20)),),
        signal=_vortex_trend,
        citation=Citation(
            authors="Botes & Siepman",
            year=2010,
            title="The Vortex Indicator",
            venue="Technical Analysis of Stocks & Commodities, January 2010",
            peer_reviewed=False,
            tested_rule=(
                "VI+ is the sum of |high minus the prior low| over n bars, divided by the "
                "sum of true range. VI- uses |low minus the prior high|. The bullish "
                "reading is VI+ above VI-. The length in the article is 14."
            ),
            implemented_rule="Long while VI+(14) is above VI-(14), flat otherwise.",
            differences=(
                "A day with no true range contributes 0, not a missing lookback. No "
                "published performance test is relied on. Long or flat, next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="trix",
        title="TRIX(15) above zero",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 15, (12, 18)),),
        signal=_trix,
        citation=Citation(
            authors="Hutson",
            year=1983,
            title="Good TRIX",
            venue="Technical Analysis of Stocks & Commodities, Volume 1:5",
            peer_reviewed=False,
            tested_rule=(
                "TRIX is the one-day rate of change of a triple-smoothed EMA. Above zero "
                "the smoothed trend is rising. The length used with the indicator since "
                "that article is 15."
            ),
            implemented_rule=(
                "Long while a triple 15-day EMA of the close is higher than its own prior "
                "value, flat otherwise."
            ),
            differences=(
                "Not lib.macd and not lib.ema12x26: those are a 12/26 pair, once with a "
                "signal line and once as a zero line. EMAs are seeded with the SMA of "
                "their first finite window. Long or flat, next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="awesome",
        title="Awesome oscillator (5, 34) above zero",
        family="momentum",
        evaluation="daily",
        params=(
            Param("fast", 5, (4, 6)),
            Param("slow", 34, (28, 40)),
        ),
        signal=_awesome,
        citation=Citation(
            authors="Williams",
            year=1998,
            title="New Trading Dimensions",
            venue="Practitioner book (Wiley); the awesome oscillator is SMA(5) minus SMA(34) of the median price",
            peer_reviewed=False,
            tested_rule=(
                "The awesome oscillator is the 5-bar simple average of the median price "
                "(high+low)/2 minus the 34-bar simple average. Above zero the fast average "
                "is above the slow one."
            ),
            implemented_rule=(
                "Long while the 5-day SMA of the median price is above the 34-day SMA, "
                "flat otherwise."
            ),
            differences=(
                "Not lib.ema12x26 (the 12/26 EMA of the close) and not lib.sma5x150. The "
                "saucer and twin-peaks patterns are not required. Long or flat, next-open "
                "fills, BTC. 5 and 34 are the published pair, not fit here."
            ),
        ),
    ),
    LibraryStrategy(
        key="kst",
        title="Know Sure Thing above its 9-day signal",
        family="momentum",
        evaluation="daily",
        params=(Param("signal", 9, (7, 11)),),
        signal=_kst,
        citation=Citation(
            authors="Pring",
            year=1992,
            title="Know Sure Thing (KST)",
            venue="Practitioner; daily parameters published with the indicator",
            peer_reviewed=False,
            tested_rule=(
                "Daily KST is a weighted sum of four smoothed rates of change: ROC 10, 15, "
                "20 and 30, smoothed by SMA 10, 10, 10 and 15, weights 1, 2, 3 and 4. The "
                "signal line is a 9-bar SMA of that sum. Bullish when KST is above the signal."
            ),
            implemented_rule=(
                "Long while the daily KST is above its 9-day SMA. The four rates, smoothers "
                "and weights stay at the published set."
            ),
            differences=(
                "Not a single rate-of-change sign rule (those are the lib.tsmom names). "
                "Only the signal length is a grid axis. Long or flat, next-open fills, BTC. "
                "No published performance test of this exact daily book is relied on."
            ),
        ),
    ),
    LibraryStrategy(
        key="nr7",
        title="NR7 breakout, held one day",
        family="breakout",
        evaluation="daily",
        params=(
            Param("n", 7, (5, 9)),
            Param("hold", 1, (2, 3), unit="days"),
        ),
        signal=_nr7,
        citation=Citation(
            authors="Crabel",
            year=1990,
            title="Day Trading With Short Term Price Patterns and Opening Range Breakout",
            venue="Practitioner book (Traders Press)",
            peer_reviewed=False,
            tested_rule=(
                "An NR7 day has the narrowest high-low range of the last 7 days. Crabel "
                "bought a break of that day's high on the following session, as a short-term "
                "opening-range trade."
            ),
            implemented_rule=(
                "Long on a daily close above the high of an NR7 day, and flat again after "
                "that one day. The NR day is the strict minimum of the 7 ranges ending that day."
            ),
            differences=(
                "The book trades the next day's opening range intraday. This confirms the "
                "break on the daily close and holds a fixed number of daily bars, the same "
                "hold shape as lib.bll_trb50, which is a 50-day high rather than a narrow "
                "range. Long or flat, next-open fills, BTC. 7 and a one-day hold are his "
                "short-term pair, not fit here."
            ),
        ),
    ),
    LibraryStrategy(
        key="alligator",
        title="Williams Alligator lips above teeth above jaw",
        family="trend",
        evaluation="daily",
        params=(Param("jaw", 13, (11, 15)),),
        signal=_alligator,
        citation=Citation(
            authors="Williams",
            year=1995,
            title="Trading Chaos",
            venue="Practitioner book; jaw 13 shifted 8, teeth 8 shifted 5, lips 5 shifted 3",
            peer_reviewed=False,
            tested_rule=(
                "The alligator is three smoothed moving averages of the median price: the "
                "jaw SMMA(13) plotted 8 bars ahead, the teeth SMMA(8) plotted 5 ahead, and "
                "the lips SMMA(5) plotted 3 ahead. An uptrend is lips above teeth above jaw."
            ),
            implemented_rule=(
                "Long while the lagged lips are above the lagged teeth and those are above "
                "the lagged jaw. The value read at t is the SMMA from t minus the shift."
            ),
            differences=(
                "The book's forward plot is applied as a lag, so the signal does not read "
                "a bar that has not closed. Teeth, lips and the three shifts stay at 8, 5, "
                "3 and 8, 5, 3. Not lib.golden_cross or lib.ema50x200. The sleeping-alligator "
                "angle filter is not required. Long or flat, next-open fills, BTC."
            ),
        ),
    ),
)

_BY_KEY: dict[str, LibraryStrategy] = {s.key: s for s in LIBRARY}
if len(_BY_KEY) != len(LIBRARY):  # pragma: no cover - declaration error
    raise RuntimeError("duplicate library key")


def strategy_by_key(key: str) -> LibraryStrategy:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise ValueError(f"unknown library strategy {key!r}") from None


def is_library_name(name: object) -> bool:
    """Cheap prefix test. ``parse_variant`` validates the rest."""
    return isinstance(name, str) and name.startswith(NAME_PREFIX)


def variant_name(strategy: LibraryStrategy, params: dict[str, int]) -> str:
    values = tuple(int(params[p.name]) for p in strategy.params)
    if values == tuple(int(p.center) for p in strategy.params):
        return f"{NAME_PREFIX}{strategy.key}"
    return f"{NAME_PREFIX}{strategy.key}." + "_".join(str(v) for v in values)


def parse_variant(name: object) -> tuple[LibraryStrategy, dict[str, int]]:
    """``(strategy, params)`` for a library name. Unknown or malformed names raise."""
    if not is_library_name(name):
        raise ValueError(f"not a library name: {name!r}")
    body = str(name)[len(NAME_PREFIX):]
    key, _, values = body.partition(".")
    strategy = strategy_by_key(key)
    if not values:
        return strategy, strategy.center
    parts = values.split("_")
    if len(parts) != len(strategy.params) or not all(part.isdigit() for part in parts):
        raise ValueError(f"bad parameters in {name!r}")
    params = {p.name: int(v) for p, v in zip(strategy.params, parts)}
    if variant_name(strategy, params) != name:
        raise ValueError(f"non-canonical library name {name!r}")
    return strategy, params


def grid_params(strategy: LibraryStrategy) -> list[dict[str, int]]:
    """Center first, then each one-at-a-time perturbation."""
    out = [strategy.center]
    for p in strategy.params:
        for value in p.neighbours:
            params = dict(strategy.center)
            params[p.name] = int(value)
            out.append(params)
    return out


def neighbour_names(name: str) -> list[str]:
    """Declared grid points that differ from ``name`` in exactly one parameter."""
    strategy, params = parse_variant(name)
    out: list[str] = []
    for candidate in grid_params(strategy):
        diff = [k for k in candidate if candidate[k] != params[k]]
        if len(diff) == 1:
            out.append(variant_name(strategy, candidate))
    return out


def all_variant_names() -> list[str]:
    """Every distinct declared configuration in the library."""
    seen: list[str] = []
    for strategy in LIBRARY:
        for params in grid_params(strategy):
            name = variant_name(strategy, params)
            if name not in seen:
                seen.append(name)
    return seen


def library_trial_count() -> int:
    """Deflated-Sharpe N: every grid point of every strategy counts as a trial."""
    return len(all_variant_names())


def claim_names() -> list[str]:
    """The canonical variant of each strategy, in declaration order."""
    return [f"{NAME_PREFIX}{s.key}" for s in LIBRARY]


def strategy_manifest(strategy: LibraryStrategy) -> dict:
    return {
        "name": f"{NAME_PREFIX}{strategy.key}",
        "key": strategy.key,
        "title": strategy.title,
        "family": strategy.family,
        "evaluation": strategy.evaluation,
        "params": [
            {"name": p.name, "value": p.center, "neighbours": list(p.neighbours), "unit": p.unit}
            for p in strategy.params
        ],
        "safety_stop_pct": round(strategy.stop_frac * 100.0, 2) if strategy.stop_frac else None,
        "grid": [variant_name(strategy, params) for params in grid_params(strategy)],
        "citation": strategy.citation.as_dict(),
    }


def library_manifest() -> list[dict]:
    return [strategy_manifest(s) for s in LIBRARY]
