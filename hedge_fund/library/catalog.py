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
from numpy.lib.stride_tricks import sliding_window_view

from hedge_fund.library.daily import DAY_MS, DailyBars
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC
from hedge_fund.library.indicators import (
    adx_first_index,
    adx_wilder,
    aroon,
    atr_wilder,
    bollinger,
    cci,
    chande_momentum,
    connors_rsi,
    day_of_month,
    days_in_month,
    dema,
    detrended_price,
    ema,
    fisher_transform,
    hold_between,
    hull_ma,
    ibs,
    ichimoku_cloud_top,
    ichimoku_first_index,
    is_month_end,
    is_week_end,
    kaufman_ama,
    keltner,
    know_sure_thing,
    mass_index,
    month_number,
    month_of_year,
    parabolic_sar,
    prior_max,
    prior_min,
    qstick,
    realized_vol,
    relative_vigor,
    relative_volatility,
    rolling_max,
    rolling_median_skip_leading,
    rolling_min,
    rsi_wilder,
    schaff_trend,
    shift_previous,
    sma,
    sma_skip_leading,
    stoch_rsi,
    stochastic_k,
    supertrend_long,
    trix,
    ulcer_index,
    ultimate_oscillator,
    vortex,
    week_number,
    weekday_utc,
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
    mid, _lower, upper = keltner(
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


def _donchian55(bars: DailyBars, p: dict) -> np.ndarray:
    """Donchian channel with one window for the entry high and the exit low."""
    return _donchian20(bars, p)


def _keltner_revert(bars: DailyBars, p: dict) -> np.ndarray:
    """Close below the lower Keltner band, exit at the EMA of typical price."""
    mid, lower, _upper = keltner(
        bars.high, bars.low, bars.close, int(p["ema"]), int(p["atr"]), float(p["width"]),
    )
    with np.errstate(invalid="ignore"):
        enter = np.isfinite(lower) & (bars.close < lower)
        leave = np.isfinite(mid) & (bars.close >= mid)
    return _stateful(enter, leave)


def _sma_band(bars: DailyBars, p: dict) -> np.ndarray:
    """BLL 1% band: buy through the upper band, sell through the lower, hold inside."""
    ma = sma(bars.close, int(p["n"]))
    band = float(p["band"]) / 100.0
    upper = ma * (1.0 + band)
    lower = ma * (1.0 - band)
    with np.errstate(invalid="ignore"):
        leave = np.isfinite(lower) & (bars.close < lower)
    return _stateful(_finite_gt(bars.close, upper), leave)


def _psar(bars: DailyBars, p: dict) -> np.ndarray:
    """Wilder parabolic SAR. Acceleration factors are stored in hundredths."""
    return parabolic_sar(
        bars.high, bars.low,
        float(p["af"]) / 100.0, float(p["step"]) / 100.0, float(p["af_max"]) / 100.0,
    )


def _kama(bars: DailyBars, p: dict) -> np.ndarray:
    """Long when the close is above Kaufman's adaptive average."""
    return _finite_gt(bars.close, kaufman_ama(bars.close, int(p["er"]), int(p["fast"]), int(p["slow"])))


def _hma_rising(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the Hull average is rising. The slope uses yesterday's value, not tomorrow's."""
    value = hull_ma(bars.close, int(p["n"]))
    return _finite_gt(value, shift_previous(value, 1))


def _aroon_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while Aroon Up is above Aroon Down."""
    up, down = aroon(bars.high, bars.low, int(p["n"]))
    return _finite_gt(up, down)


def _cci_trend(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while CCI is above zero."""
    value = cci(bars.high, bars.low, bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.0)


def _cci_dip(bars: DailyBars, p: dict) -> np.ndarray:
    """CCI below -100 above the trend SMA; flat once CCI is back above zero."""
    value = cci(bars.high, bars.low, bars.close, int(p["n"]))
    trend = sma(bars.close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        enter = (
            np.isfinite(value) & np.isfinite(trend)
            & (bars.close > trend) & (value < -100.0)
        )
        leave = np.isfinite(value) & (value > 0.0)
    return _stateful(enter, leave)


def _stoch_cross(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while fast %K is above its %D average."""
    k = stochastic_k(bars.high, bars.low, bars.close, int(p["k"]))
    d = sma_skip_leading(k, int(p["d"]))
    return _finite_gt(k, d)


def _stochrsi_level(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while StochRSI is above the threshold on a 0-100 scale."""
    value = stoch_rsi(bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > float(p["thr"]))


def _ultimate(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the Ultimate Oscillator is above the threshold. Lengths 7, 14, 28 are fixed."""
    value = ultimate_oscillator(bars.high, bars.low, bars.close, 7, 14, 28)
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > float(p["thr"]))


def _rsi14_dip(bars: DailyBars, p: dict) -> np.ndarray:
    """RSI(n) below the oversold line above the trend SMA; flat above 70."""
    value = rsi_wilder(bars.close, int(p["n"]))
    trend = sma(bars.close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        enter = (
            np.isfinite(value) & np.isfinite(trend)
            & (bars.close > trend) & (value < float(p["os"]))
        )
        leave = np.isfinite(value) & (value > 70.0)
    return _stateful(enter, leave)


def _crsi_dip(bars: DailyBars, p: dict) -> np.ndarray:
    """ConnorsRSI(3,2,100) below the entry line while ADX(10) exceeds the filter; flat above the exit line."""
    value = connors_rsi(bars.close)
    _plus, _minus, adx = adx_wilder(bars.high, bars.low, bars.close, 10)
    with np.errstate(invalid="ignore"):
        enter = (
            np.isfinite(value) & np.isfinite(adx)
            & (adx > float(p["adx_gt"])) & (value < float(p["rsi_lt"]))
        )
        leave = np.isfinite(value) & (value > float(p["exit_gt"]))
    return _stateful(enter, leave)


def _adx_sma(bars: DailyBars, p: dict) -> np.ndarray:
    """+DI above -DI, ADX above the threshold, and the close above its SMA."""
    plus, minus, adx = adx_wilder(bars.high, bars.low, bars.close, int(p["n"]))
    trend = sma(bars.close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        return (
            np.isfinite(plus) & np.isfinite(minus) & np.isfinite(adx) & np.isfinite(trend)
            & (plus > minus) & (adx > float(p["adx_gt"])) & (bars.close > trend)
        )


def _vortex_trend(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while VI+ is above VI-."""
    plus, minus = vortex(bars.high, bars.low, bars.close, int(p["n"]))
    return _finite_gt(plus, minus)


def _trix_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the triple-EMA rate of change is positive."""
    value = trix(bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.0)


def _chandelier(bars: DailyBars, p: dict) -> np.ndarray:
    """Enter on a close above the prior high; exit below the highest high since entry minus k ATR.

    The entry bar stays long. The stop is applied from the next bar, matching
    :func:`_stateful` (a bar that both enters and would stop does not exit).
    """
    entry_n = int(p["entry"])
    upper = prior_max(bars.high, entry_n)
    atr = atr_wilder(bars.high, bars.low, bars.close, int(p["atr"]))
    mult = float(p["mult"])
    out = np.zeros(len(bars), dtype=bool)
    long = False
    peak = 0.0
    for t in range(len(bars)):
        if not long:
            if np.isfinite(upper[t]) and np.isfinite(atr[t]) and bars.close[t] > upper[t]:
                long = True
                peak = float(bars.high[t])
        else:
            peak = max(peak, float(bars.high[t]))
            if np.isfinite(atr[t]) and bars.close[t] < peak - mult * float(atr[t]):
                long = False
        out[t] = long
    return out


def _hi52(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is within ``near`` percent of the trailing high close."""
    high = rolling_max(bars.close, int(p["n"]))
    level = float(p["near"]) / 100.0
    with np.errstate(invalid="ignore"):
        return np.isfinite(high) & (high > 0.0) & (bars.close >= level * high)


def _tsmom_vote(bars: DailyBars, p: dict) -> np.ndarray:
    """Long when at least ``vote`` of the 21-day, 63-day, and ``slow``-day signs are positive.

    The signal stays off until the longest lookback exists, so a short window
    cannot turn it on during warm-up.
    """
    close = bars.close
    slow = int(p["slow"])
    signs = (
        _finite_gt(close, shift_previous(close, 21)).astype(np.int64)
        + _finite_gt(close, shift_previous(close, 63)).astype(np.int64)
        + _finite_gt(close, shift_previous(close, slow)).astype(np.int64)
    )
    ready = np.isfinite(shift_previous(close, slow))
    return ready & (signs >= int(p["vote"]))


def _vol_trend(bars: DailyBars, p: dict) -> np.ndarray:
    """12-month sign, and flat when recent vol is above its own trailing median.

    The median length is 126 days and is not a grid axis. The signal stays
    off until both the return lookback and the vol median exist.
    """
    close = bars.close
    mom = int(p["mom"])
    vol_n = int(p["vol"])
    vol = realized_vol(close, vol_n)
    med = rolling_median_skip_leading(vol, 126)
    trend = _finite_gt(close, shift_previous(close, mom))
    with np.errstate(invalid="ignore"):
        calm = np.isfinite(vol) & np.isfinite(med) & (vol <= med)
    return trend & calm


def _dow(bars: DailyBars, p: dict) -> np.ndarray:
    """Long for one UTC weekday. Today's close decides tomorrow's session."""
    tomorrow = weekday_utc(bars.ts + np.int64(DAY_MS))
    return tomorrow == int(p["dow"])


def _tom(bars: DailyBars, p: dict) -> np.ndarray:
    """Long when tomorrow is one of the last or first calendar days of a month."""
    tomorrow = bars.ts + np.int64(DAY_MS)
    dom = day_of_month(tomorrow)
    dim = days_in_month(tomorrow)
    last_n = int(p["last"])
    first_n = int(p["first"])
    return (dom > dim - last_n) | (dom <= first_n)


def _halloween(bars: DailyBars, p: dict) -> np.ndarray:
    """Long when tomorrow falls in the six months that start at ``start`` (November is 11)."""
    month = month_of_year(bars.ts + np.int64(DAY_MS))
    return ((month - int(p["start"])) % 12) < 6


def _ichimoku_tk(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the conversion line is above the base line. The cloud is not used."""
    conv = int(p["conv"])
    base = int(p["base"])
    tenkan = (rolling_max(bars.high, conv) + rolling_min(bars.low, conv)) / 2.0
    kijun = (rolling_max(bars.high, base) + rolling_min(bars.low, base)) / 2.0
    return _finite_gt(tenkan, kijun)


def _guppy(bars: DailyBars, p: dict) -> np.ndarray:
    """Long when every short-group EMA sits above every long-group EMA.

    ``fast`` replaces the shortest trader average and ``slow`` replaces the
    longest investor average. The other ten lengths stay at Guppy's published
    set.
    """
    short = (int(p["fast"]), 5, 8, 10, 12, 15)
    long = (30, 35, 40, 45, 50, int(p["slow"]))
    close = bars.close
    short_min = ema(close, short[0])
    for length in short[1:]:
        short_min = np.fmin(short_min, ema(close, length))
    long_max = ema(close, long[0])
    for length in long[1:]:
        long_max = np.fmax(long_max, ema(close, length))
    return _finite_gt(short_min, long_max)


def _elder_ray(bars: DailyBars, p: dict) -> np.ndarray:
    """EMA rising, and bear power (low minus that EMA) negative but rising."""
    line = ema(bars.close, int(p["n"]))
    bear = bars.low - line
    return (
        _finite_gt(line, shift_previous(line, 1))
        & _finite_gt(np.zeros(len(bars)), bear)
        & _finite_gt(bear, shift_previous(bear, 1))
    )


def _dema_rising(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while Mulloy's double EMA is higher than its own previous value."""
    value = dema(bars.close, int(p["n"]))
    return _finite_gt(value, shift_previous(value, 1))


def _cmo_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the Chande momentum oscillator is above zero."""
    value = chande_momentum(bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.0)


def _qstick_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the average close-open body is positive."""
    value = qstick(bars.open, bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.0)


def _kst_cross(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while KST is above its signal average. ROC lengths stay fixed."""
    value = know_sure_thing(bars.close)
    signal = sma_skip_leading(value, int(p["sig"]))
    return _finite_gt(value, signal)


def _schaff(bars: DailyBars, p: dict) -> np.ndarray:
    """Buy when the trend cycle crosses up through the buy line; flat under 75.

    75 is Schaff's upper line and is not a grid axis. 23, 50, and the half
    smoother are the published defaults and are not grid axes either.
    """
    value = schaff_trend(bars.close, int(p["cycle"]))
    prev = shift_previous(value, 1)
    buy = float(p["buy"])
    with np.errstate(invalid="ignore"):
        finite = np.isfinite(value) & np.isfinite(prev)
        enter = finite & (prev <= buy) & (value > buy)
        leave = finite & (prev >= 75.0) & (value < 75.0)
    return _stateful(enter, leave)


def _fisher_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the Fisher transform is above its own previous value."""
    value = fisher_transform(bars.high, bars.low, int(p["n"]))
    return _finite_gt(value, shift_previous(value, 1))


def _rvi_cross(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the relative vigor index is above its signal line."""
    value, signal = relative_vigor(bars.open, bars.high, bars.low, bars.close, int(p["n"]))
    return _finite_gt(value, signal)


def _dpo_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the detrended price (older close minus today's SMA) is positive."""
    value = detrended_price(bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.0)


def _wilder_vol(bars: DailyBars, p: dict) -> np.ndarray:
    """Long after a close above yesterday's close plus k ATR; flat below the lower band.

    The band is redrawn around yesterday's close. It does not trail the
    best close of the trade.
    """
    band = float(p["mult"]) * atr_wilder(bars.high, bars.low, bars.close, int(p["atr"]))
    prev = shift_previous(bars.close, 1)
    with np.errstate(invalid="ignore"):
        finite = np.isfinite(band) & np.isfinite(prev)
        enter = finite & (bars.close > prev + band)
        leave = finite & (bars.close < prev - band)
    return _stateful(enter, leave)


def _bb_squeeze(bars: DailyBars, p: dict) -> np.ndarray:
    """Enter on a close above the upper band after a bar whose bands sat inside Keltner.

    Bollinger width stays 2. The Keltner width is stored in tenths (15 is 1.5,
    Carter's TTM multiple). Both channels use the same length. The exit is a
    close back through the Bollinger middle, as in ``lib.bb_break``.
    """
    n = int(p["n"])
    mid, lower, upper = bollinger(bars.close, n, 2.0)
    _kmid, klow, khigh = keltner(
        bars.high, bars.low, bars.close, n, n, float(p["kc"]) / 10.0,
    )
    with np.errstate(invalid="ignore"):
        squeezed = (
            np.isfinite(upper) & np.isfinite(lower)
            & np.isfinite(khigh) & np.isfinite(klow)
            & (upper < khigh) & (lower > klow)
        )
        prior = np.zeros(len(bars), dtype=bool)
        prior[1:] = squeezed[:-1]
        enter = prior & np.isfinite(upper) & (bars.close > upper)
        leave = np.isfinite(mid) & (bars.close < mid)
    return _stateful(enter, leave)


def _turtle_soup(bars: DailyBars, p: dict) -> np.ndarray:
    """Failed break of the prior n-day low, held for ``hold`` sessions.

    Today's low takes out that prior low and the close finishes back above
    it, the close is above the trend SMA, and the prior low itself is at
    least three days old. A break that is still inside the hold is ignored.
    """
    n = int(p["n"])
    hold = int(p["hold"])
    level = prior_min(bars.low, n)
    trend = sma(bars.close, int(p["trend"]))
    aged = np.zeros(len(bars), dtype=bool)
    if n < len(bars):
        windows = sliding_window_view(bars.low, n)
        # Window i is lows[i:i+n], which is the prior window for bar i+n.
        bars_ago = n - np.argmin(windows, axis=1)
        aged[n:] = bars_ago[: len(bars) - n] >= 3
    with np.errstate(invalid="ignore"):
        trigger = (
            np.isfinite(level) & np.isfinite(trend) & aged
            & (bars.low < level) & (bars.close > level) & (bars.close > trend)
        )
    out = np.zeros(len(bars), dtype=bool)
    remaining = 0
    for t in range(len(bars)):
        if remaining > 0:
            out[t] = True
            remaining -= 1
        elif bool(trigger[t]):
            out[t] = True
            remaining = hold - 1
    return out


def _rsi2_cum(bars: DailyBars, p: dict) -> np.ndarray:
    """Two-day sum of RSI(2) below the line, above the trend SMA; flat above the exit SMA."""
    close = bars.close
    value = rsi_wilder(close, 2)
    previous = shift_previous(value, 1)
    trend = sma(close, int(p["trend"]))
    exit_ma = sma(close, int(p["exit_ma"]))
    with np.errstate(invalid="ignore"):
        total = value + previous
        enter = (
            np.isfinite(total) & np.isfinite(trend)
            & (close > trend) & (total < float(p["sum_lt"]))
        )
        leave = np.isfinite(exit_ma) & (close > exit_ma)
    return _stateful(enter, leave)


def _rev1m(bars: DailyBars, p: dict) -> np.ndarray:
    """Long when the trailing n-day return is negative and the close is above its SMA."""
    close = bars.close
    previous = shift_previous(close, int(p["n"]))
    trend = sma(close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        down = np.isfinite(previous) & (close < previous)
        return down & np.isfinite(trend) & (close > trend)


def _tsmom12_1(bars: DailyBars, p: dict) -> np.ndarray:
    """Long when the close ``skip`` days ago is above the close ``slow`` days before that."""
    close = bars.close
    skip = int(p["skip"])
    recent = shift_previous(close, skip)
    formed = shift_previous(close, skip + int(p["slow"]))
    return _finite_gt(recent, formed)


def _nr7(bars: DailyBars, p: dict) -> np.ndarray:
    """Yesterday's range was the narrowest of n days; today's close breaks that high, above the SMA."""
    n = int(p["n"])
    span = bars.high - bars.low
    narrow = prior_min(span, n - 1) if n > 1 else np.full(len(bars), np.nan)
    with np.errstate(invalid="ignore"):
        is_nr = np.isfinite(narrow) & (span < narrow)
    yesterday = np.zeros(len(bars), dtype=bool)
    if len(bars) > 1:
        yesterday[1:] = is_nr[:-1]
    prior_high = shift_previous(bars.high, 1)
    trend = sma(bars.close, int(p["trend"]))
    return yesterday & _finite_gt(bars.close, prior_high) & _finite_gt(bars.close, trend)


def _inside_day(bars: DailyBars, p: dict) -> np.ndarray:
    """Long on the close that breaks an inside day's high. A break of its low cancels it.

    Center ``n`` is 1, the classic inside day (inside the previous bar only).
    A larger ``n`` requires the bar to sit inside the prior ``n`` bars' range.
    The long is that one close.
    """
    n = int(p["n"])
    ceiling = prior_max(bars.high, n)
    floor = prior_min(bars.low, n)
    with np.errstate(invalid="ignore"):
        inside = (
            np.isfinite(ceiling) & np.isfinite(floor)
            & (bars.high < ceiling) & (bars.low > floor)
        )
    out = np.zeros(len(bars), dtype=bool)
    ref_high = np.nan
    ref_low = np.nan
    for t in range(len(bars)):
        broken = False
        if np.isfinite(ref_high) and bars.close[t] > ref_high:
            out[t] = True
            broken = True
        elif np.isfinite(ref_low) and bars.close[t] < ref_low:
            broken = True
        if broken:
            ref_high = np.nan
            ref_low = np.nan
        if bool(inside[t]):
            ref_high = float(bars.high[t])
            ref_low = float(bars.low[t])
    return out


def _floor_pivot(bars: DailyBars, p: dict) -> np.ndarray:
    """Long when the close is above yesterday's floor pivot and above the trend SMA."""
    pivot = (
        shift_previous(bars.high, 1) + shift_previous(bars.low, 1) + shift_previous(bars.close, 1)
    ) / 3.0
    trend = sma(bars.close, int(p["trend"]))
    return _finite_gt(bars.close, pivot) & _finite_gt(bars.close, trend)


def _fractal(bars: DailyBars, p: dict) -> np.ndarray:
    """Close above the latest confirmed up-fractal; flat below the latest confirmed down-fractal.

    A fractal at the center needs ``wings`` bars on each side, so the one
    confirmed on this close is centered ``wings`` bars ago. Bill Williams'
    five-bar fractal is ``wings`` 2.
    """
    w = int(p["wings"])
    high = bars.high
    low = bars.low
    out = np.zeros(len(bars), dtype=bool)
    long = False
    up_level = np.nan
    down_level = np.nan
    for t in range(len(bars)):
        center = t - w
        if center >= w:
            left_h = high[center - w : center]
            right_h = high[center + 1 : center + w + 1]
            left_l = low[center - w : center]
            right_l = low[center + 1 : center + w + 1]
            if high[center] > np.max(left_h) and high[center] > np.max(right_h):
                up_level = float(high[center])
            if low[center] < np.min(left_l) and low[center] < np.min(right_l):
                down_level = float(low[center])
        if not long:
            if np.isfinite(up_level) and bars.close[t] > up_level:
                long = True
        elif np.isfinite(down_level) and bars.close[t] < down_level:
            long = False
        out[t] = long
    return out


def _mass_index(bars: DailyBars, p: dict) -> np.ndarray:
    """Bulge above 27 then back under 26.5, and only while the close is above the SMA.

    27 and 26.5 are Dorsey's reversal bulge. The index has no direction of
    its own; the trend average is the direction, and losing it is the exit.
    """
    value = mass_index(bars.high, bars.low, int(p["ema"]), int(p["span"]))
    trend = sma(bars.close, int(p["trend"]))
    prev = shift_previous(value, 1)
    out = np.zeros(len(bars), dtype=bool)
    long = False
    armed = False
    for t in range(len(bars)):
        if np.isfinite(value[t]):
            if value[t] > 27.0:
                armed = True
            elif (
                armed
                and np.isfinite(prev[t])
                and prev[t] >= 26.5
                and value[t] < 26.5
                and np.isfinite(trend[t])
                and bars.close[t] > trend[t]
            ):
                long = True
                armed = False
            elif armed and np.isfinite(prev[t]) and prev[t] >= 26.5 and value[t] < 26.5:
                armed = False
        if long and np.isfinite(trend[t]) and bars.close[t] < trend[t]:
            long = False
        out[t] = long
    return out


def _relvol(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the relative volatility index is above the threshold."""
    value = relative_volatility(bars.high, bars.low, bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > float(p["thr"]))


def _ulcer(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is above its SMA and the ulcer index is below its own median.

    The median length is 100 days and is not a grid axis.
    """
    value = ulcer_index(bars.close, int(p["n"]))
    med = rolling_median_skip_leading(value, 100)
    trend = sma(bars.close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        calm = np.isfinite(value) & np.isfinite(med) & (value < med)
        return calm & np.isfinite(trend) & (bars.close > trend)


def _weekend(bars: DailyBars, p: dict) -> np.ndarray:
    """Flat for the next ``span`` weekend sessions, counting back from Sunday.

    Span 2 is Saturday and Sunday. Span 1 is Sunday only. Span 3 adds Friday.
    Today's close decides tomorrow's session.
    """
    tomorrow = weekday_utc(bars.ts + np.int64(DAY_MS))
    span = int(p["span"])
    if span <= 1:
        flat = tomorrow == 6
    elif span == 2:
        flat = (tomorrow == 5) | (tomorrow == 6)
    else:
        flat = (tomorrow == 4) | (tomorrow == 5) | (tomorrow == 6)
    return ~flat


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
    if key == "donchian55":
        return _defined_from(n, int(params["n"]))
    if key == "keltner_rev":
        return _defined_from(n, max(int(params["ema"]) - 1, int(params["atr"])))
    if key == "sma200_band":
        return _defined_from(n, int(params["n"]) - 1)
    if key == "psar":
        # The first bar only seeds the extreme. The stop exists from index 1.
        return _defined_from(n, 1)
    if key == "kama":
        return _defined_from(n, int(params["er"]))
    if key == "hma":
        # WMA(n) at n-1, then WMA(round(sqrt(n))) of that, then one bar of slope.
        root = max(int(round(int(params["n"]) ** 0.5)), 1)
        return _defined_from(n, int(params["n"]) + root - 1)
    if key == "aroon":
        return _defined_from(n, int(params["n"]))
    if key in ("cci", "cci_dip"):
        # CCI is finite at n - 1. The dip also needs the trend SMA.
        first = int(params["n"]) - 1
        if key == "cci_dip":
            first = max(first, int(params["trend"]) - 1)
        return _defined_from(n, first)
    if key == "stoch":
        # %K at k - 1, then %D needs ``d`` finite %K values.
        return _defined_from(n, (int(params["k"]) - 1) + (int(params["d"]) - 1))
    if key == "stochrsi":
        # RSI at ``n``, then n RSI values, so the stochastic starts at 2n - 1.
        return _defined_from(n, 2 * int(params["n"]) - 1)
    if key == "uo":
        # Lengths 7, 14, 28 are fixed. The longest sum starts at index 28.
        return _defined_from(n, 28)
    if key == "rsi14_dip":
        return _defined_from(n, max(int(params["n"]), int(params["trend"]) - 1))
    if key == "crsi":
        # Percent rank of 100 one-day changes is finite at index 100.
        # ADX(10) is finite earlier (index 19).
        return _defined_from(n, max(100, adx_first_index(10)))
    if key == "adx_sma":
        return _defined_from(n, max(adx_first_index(int(params["n"])), int(params["trend"]) - 1))
    if key == "vortex":
        return _defined_from(n, int(params["n"]))
    if key == "trix":
        # Each EMA needs ``n`` finite inputs. The third starts at 3*(n-1),
        # and the one-day change of it starts one bar later.
        return _defined_from(n, 3 * (int(params["n"]) - 1) + 1)
    if key == "chandelier":
        return _defined_from(n, max(int(params["entry"]), int(params["atr"])))
    if key == "hi52":
        return _defined_from(n, int(params["n"]) - 1)
    if key == "tsmom_vote":
        return _defined_from(n, int(params["slow"]))
    if key == "vol_trend":
        # Log-vol is finite at ``vol``. The 126-day median needs 125 further bars.
        # The return lookback is finite at ``mom``.
        return _defined_from(n, max(int(params["mom"]), int(params["vol"]) + 125))
    if key in ("dow", "tom", "halloween"):
        # The next session's calendar date is known at this close. No price lookback.
        return _defined_from(n, 0)
    if key == "ichimoku_tk":
        return _defined_from(n, max(int(params["conv"]), int(params["base"])) - 1)
    if key == "guppy":
        # The investor group is the slow one. Its longest EMA is finite at slow - 1.
        return _defined_from(n, int(params["slow"]) - 1)
    if key == "elder_ray":
        # The EMA exists at n - 1. The slope needs one earlier value, so index n.
        return _defined_from(n, int(params["n"]))
    if key == "dema":
        # Outer EMA at 2*(n-1); the one-bar slope starts one bar later.
        return _defined_from(n, 2 * int(params["n"]) - 1)
    if key == "cmo":
        return _defined_from(n, int(params["n"]))
    if key == "qstick":
        return _defined_from(n, int(params["n"]) - 1)
    if key == "kst":
        # ROC(30) smoothed by 15 is finite at index 44. The signal average needs
        # ``sig`` of those values.
        return _defined_from(n, 30 + 15 - 1 + int(params["sig"]) - 1)
    if key == "schaff":
        # EMA(50) at index 49. Each stochastic window adds cycle-1 bars, and
        # the cross needs the previous value.
        return _defined_from(n, (50 - 1) + 2 * (int(params["cycle"]) - 1) + 1)
    if key == "fisher":
        # The transform exists at n - 1. "Above its previous value" starts at n.
        return _defined_from(n, int(params["n"]))
    if key == "rvi":
        # Weighted bars start at index 3, the sum at n+2, the signal three later.
        return _defined_from(n, int(params["n"]) + 5)
    if key == "dpo":
        return _defined_from(n, int(params["n"]) - 1)
    if key == "wilder_vol":
        return _defined_from(n, int(params["atr"]))
    if key == "bb_squeeze":
        # Bands exist at n (ATR). The entry reads the previous bar's squeeze.
        return _defined_from(n, int(params["n"]) + 1)
    if key == "turtle_soup":
        return _defined_from(n, max(int(params["n"]), int(params["trend"]) - 1))
    if key == "rsi2_cum":
        # RSI(2) at index 2, so a two-day sum exists at index 3.
        return _defined_from(n, max(3, int(params["trend"]) - 1, int(params["exit_ma"]) - 1))
    if key == "rev1m":
        return _defined_from(n, max(int(params["n"]), int(params["trend"]) - 1))
    if key == "tsmom12_1":
        return _defined_from(n, int(params["skip"]) + int(params["slow"]))
    if key == "bll_trb150":
        return _defined_from(n, int(params["n"]))
    if key == "nr7":
        # The narrowest-range flag exists at n - 1. The break uses yesterday's flag.
        return _defined_from(n, max(int(params["n"]), int(params["trend"]) - 1))
    if key == "inside_day":
        return _defined_from(n, int(params["n"]))
    if key == "floor_pivot":
        return _defined_from(n, max(1, int(params["trend"]) - 1))
    if key == "fractal":
        # Wings on both sides of a center that is itself wings bars ago.
        return _defined_from(n, 2 * int(params["wings"]))
    if key == "mass_index":
        # Second EMA at 2*(ema-1). The sum needs ``span`` ratios, and the
        # bulge cross needs the previous sum.
        ema_n = int(params["ema"])
        cross = 2 * (ema_n - 1) + int(params["span"])
        return _defined_from(n, max(cross, int(params["trend"]) - 1))
    if key == "relvol":
        return _defined_from(n, 2 * int(params["n"]) - 2)
    if key == "ulcer":
        # Ulcer at n-1. A 100-day median needs 99 further bars.
        return _defined_from(n, max(int(params["n"]) + 98, int(params["trend"]) - 1))
    if key == "weekend":
        return _defined_from(n, 0)
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
        key="donchian55",
        title="Donchian 55-day channel",
        family="breakout",
        evaluation="daily",
        params=(Param("n", 55, (44, 66)),),
        signal=_donchian55,
        citation=Citation(
            authors="Donchian",
            year=0,
            title="55-day Donchian channel",
            venue="Practitioner commodity channel; the 55-day window is Turtle System 2's entry",
            peer_reviewed=False,
            tested_rule=(
                "A Donchian channel buys a break of the prior N-day high and sells a break "
                "of the prior N-day low. The 55-day window is the long System 2 entry in "
                "Faith's turtle rules. Those rules exit on a shorter low."
            ),
            implemented_rule=(
                "Long after a daily close above the prior 55-day high, flat after a close "
                "below the prior 55-day low. Entry and exit use the same window."
            ),
            differences=(
                "lib.turtle55 enters on this 55-day high and exits on the 20-day low. "
                "lib.donchian20 is the same symmetric rule at 20 days. Close-confirmed, "
                "next-open fills, all-in, long only, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="keltner_rev",
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
                "The textbook channel is a 20-period EMA of typical price with bands at "
                "2 times Wilder ATR(10). A close through the lower band is the downside "
                "extreme; the EMA is the mean."
            ),
            implemented_rule=(
                "Long after a close below the lower band; flat after a close at or above "
                "the 20-day EMA of typical price."
            ),
            differences=(
                "lib.keltner20 buys the upper band instead. No published performance test "
                "of this exit is relied on. Long or flat, next-open fills, no short side, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="sma200_band",
        title="Price vs 200-day SMA with a 1% band",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 200, (150, 250)),
            Param("band", 1, (2, 3), unit="percent"),
        ),
        signal=_sma_band,
        citation=Citation(
            **_BLL,
            tested_rule=(
                "Variable-length moving average with a 1% band: buy when the price is more "
                "than 1% above the long average, sell when it is more than 1% below, and "
                "do not trade while the price stays inside the band. Reported for the "
                "1-200 rule among others. DJIA daily, 1897-1986."
            ),
            implemented_rule=(
                "Long after a close more than 1% above the 200-day SMA; flat after a close "
                "more than 1% below it. A close inside the band keeps the previous position."
            ),
            differences=(
                "lib.sma200 is the same average with no band. Long or flat, BTC, next-open "
                "fills after fees. Neighbours 2% and 3% are perturbations; BLL's reported "
                "band is 1%."
            ),
        ),
    ),
    LibraryStrategy(
        key="psar",
        title="Parabolic SAR (0.02, 0.02, 0.20)",
        family="trend",
        evaluation="daily",
        params=(
            Param("af", 2, (1, 3), unit="hundredths"),
            Param("step", 2, (1, 3), unit="hundredths"),
            Param("af_max", 20, (16, 24), unit="hundredths"),
        ),
        signal=_psar,
        citation=Citation(
            authors="Wilder",
            year=1978,
            title="New Concepts in Technical Trading Systems",
            venue="Practitioner book (parabolic time/price system)",
            peer_reviewed=False,
            tested_rule=(
                "The parabolic stop starts at 0.02 of the distance to the extreme price, "
                "increases by 0.02 each time the extreme extends, and caps at 0.20. Price "
                "through the stop reverses the position. The stop is not placed inside the "
                "prior two bars."
            ),
            implemented_rule=(
                "Long while the daily parabolic SAR is in an uptrend. Acceleration 0.02, "
                "step 0.02, cap 0.20. Parameters are stored as hundredths."
            ),
            differences=(
                "Wilder traded the stop intraday and reversed to short. This is long or "
                "flat, close-confirmed, next-open fills, all-in, no short side, BTC. The "
                "first bar only seeds the extreme."
            ),
        ),
    ),
    LibraryStrategy(
        key="kama",
        title="Kaufman adaptive moving average (10, 2, 30)",
        family="trend",
        evaluation="daily",
        params=(
            Param("er", 10, (8, 12)),
            Param("fast", 2, (1, 3)),
            Param("slow", 30, (20, 40)),
        ),
        signal=_kama,
        citation=Citation(
            authors="Kaufman",
            year=1995,
            title="Smarter Trading",
            venue="Practitioner book (Kaufman adaptive moving average)",
            peer_reviewed=False,
            tested_rule=(
                "The efficiency ratio over 10 days mixes a fast EMA constant (2) and a "
                "slow one (30). The squared constant smooths the price. Price above the "
                "adaptive average is the trend reading."
            ),
            implemented_rule=(
                "Long when the daily close is above KAMA(10, 2, 30), flat otherwise. "
                "The seed is the close at the end of the first efficiency window."
            ),
            differences=(
                "Not an SMA or EMA cross: the smoothing changes with the efficiency ratio. "
                "A zero-volatility window uses the slow constant. Long or flat, next-open "
                "fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="hma",
        title="Hull moving average (16) rising",
        family="trend",
        evaluation="daily",
        params=(Param("n", 16, (9, 25)),),
        signal=_hma_rising,
        citation=Citation(
            authors="Hull",
            year=2005,
            title="Hull Moving Average",
            venue="Practitioner; Alan Hull, hullmovingaverage.com and the 2005 description",
            peer_reviewed=False,
            tested_rule=(
                "HMA(n) = WMA(round(sqrt(n))) of 2*WMA(n/2) - WMA(n). Hull reads the trend "
                "from the direction of that average rather than from price crossing it. "
                "16 is a published short length."
            ),
            implemented_rule="Long while the 16-day Hull average is above its own prior value.",
            differences=(
                "The integer square root is round(sqrt(n)), and the half-length is n//2. "
                "Long or flat, next-open fills, BTC. Turning points use yesterday's HMA, "
                "which is known at today's close."
            ),
        ),
    ),
    LibraryStrategy(
        key="aroon",
        title="Aroon(25) Up above Down",
        family="trend",
        evaluation="daily",
        params=(Param("n", 25, (20, 30)),),
        signal=_aroon_up,
        citation=Citation(
            authors="Chande",
            year=1995,
            title="Aroon",
            venue="Technical Analysis of Stocks & Commodities, September 1995; also The New Technical Trader",
            peer_reviewed=False,
            tested_rule=(
                "Aroon Up is how recently the n-day high occurred, Aroon Down how recently "
                "the n-day low occurred, each scaled 0 to 100. Up above Down is the bullish "
                "reading. Chande's published length is 25."
            ),
            implemented_rule="Long while Aroon Up(25) is above Aroon Down(25), flat otherwise.",
            differences=(
                "A tie in the window uses the most recent extreme. This is not a Donchian "
                "break: the signal depends on the age of the extreme, not on price crossing "
                "it. Long or flat, next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="cci",
        title="CCI(20) above zero",
        family="trend",
        evaluation="daily",
        params=(Param("n", 20, (14, 26)),),
        signal=_cci_trend,
        citation=Citation(
            authors="Lambert",
            year=1980,
            title="Commodity Channel Index",
            venue="Commodities magazine, October 1980",
            peer_reviewed=False,
            tested_rule=(
                "CCI is the typical price's distance from its n-day mean, divided by "
                "0.015 times the mean absolute deviation. Lambert's length is 20. Zero is "
                "the mean; +100 and -100 are the unusual zones."
            ),
            implemented_rule="Long while CCI(20) of typical price is above zero, flat otherwise.",
            differences=(
                "Lambert traded the ±100 zones, not the zero line alone. Zero is his mean, "
                "not a threshold fit on this tape. A window with no deviation is 0. Long "
                "or flat, next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="cci_dip",
        title="CCI(20) below -100 above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("n", 20, (14, 26)),
            Param("trend", 200, (150, 250)),
        ),
        signal=_cci_dip,
        citation=Citation(
            authors="Lambert",
            year=1980,
            title="Commodity Channel Index",
            venue="Commodities magazine, October 1980",
            peer_reviewed=False,
            tested_rule=(
                "CCI(20) below -100 is Lambert's unusual downside zone. A return through "
                "zero is a return through the mean of typical price."
            ),
            implemented_rule=(
                "Long after a close above the 200-day SMA with CCI(20) below -100; flat "
                "after CCI rises above zero."
            ),
            differences=(
                "The 200-day SMA filter is not in the 1980 article. -100 and 0 are his "
                "published lines and are not grid axes. lib.cci is the zero-line trend, "
                "not this dip. Next-open fills, BTC, long only."
            ),
        ),
    ),
    LibraryStrategy(
        key="stoch",
        title="Stochastic %K(14) above %D(3)",
        family="momentum",
        evaluation="daily",
        params=(
            Param("k", 14, (10, 20)),
            Param("d", 3, (2, 5)),
        ),
        signal=_stoch_cross,
        citation=Citation(
            authors="Lane",
            year=1984,
            title="Lane's Stochastics",
            venue="Technical Analysis of Stocks & Commodities, May/June 1984",
            peer_reviewed=False,
            tested_rule=(
                "Fast %K locates the close in the recent high-low range. %D is a short "
                "moving average of %K. Lane's signal line is 3 periods. The 14-period "
                "lookback is the later chart default of that oscillator."
            ),
            implemented_rule="Long while fast %K(14) is above the 3-day SMA of %K, flat otherwise.",
            differences=(
                "Not the slow (14, 3, 3) form: %K is unsmoothed. A zero-range window is "
                "%K 50 so %D stays defined. A 14-period oversold dip is not listed: it is "
                "the same entry as lib.willr, because %K = %R + 100. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="stochrsi",
        title="StochRSI(14) above 50",
        family="momentum",
        evaluation="daily",
        params=(
            Param("n", 14, (10, 20)),
            Param("thr", 50, (40, 60), unit="RSI"),
        ),
        signal=_stochrsi_level,
        citation=Citation(
            authors="Chande & Kroll",
            year=1994,
            title="The New Technical Trader",
            venue="Practitioner book (stochastic RSI)",
            peer_reviewed=False,
            tested_rule=(
                "StochRSI applies the stochastic formula to RSI rather than to price. "
                "The published length used with it is 14. Above 50, RSI sits in the upper "
                "half of its own recent range."
            ),
            implemented_rule=(
                "Long while StochRSI(14) is above 50, flat otherwise. The range is the "
                "last 14 RSI(14) values."
            ),
            differences=(
                "A pinned RSI (no range) keeps that RSI level, so a run of RSI 100 stays "
                "100 and a run of RSI 0 stays 0. Not lib.rsi14 (RSI versus 50) and not "
                "lib.stoch (price versus its range). Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="uo",
        title="Ultimate Oscillator above 50",
        family="momentum",
        evaluation="daily",
        params=(Param("thr", 50, (45, 55), unit="UO"),),
        signal=_ultimate,
        citation=Citation(
            authors="Williams",
            year=1985,
            title="The Ultimate Oscillator",
            venue="Technical Analysis of Stocks & Commodities, 1985",
            peer_reviewed=False,
            tested_rule=(
                "Buying pressure is the close minus the lower of today's low and yesterday's "
                "close. The oscillator is 100 times the 4/2/1 weighted average of that "
                "pressure over 7, 14, and 28 bars, each divided by its true range."
            ),
            implemented_rule=(
                "Long while the Ultimate Oscillator (7, 14, 28) is above 50, flat otherwise."
            ),
            differences=(
                "Williams' traded signals are divergences at 30 and 70. The 50 line is the "
                "weighted-average midpoint, not a fitted threshold, and the three lengths "
                "are not grid axes. Next-open fills, BTC, long only."
            ),
        ),
    ),
    LibraryStrategy(
        key="rsi14_dip",
        title="RSI(14) below 30 above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("n", 14, (10, 18)),
            Param("os", 30, (25, 35), unit="RSI"),
            Param("trend", 200, (150, 250)),
        ),
        signal=_rsi14_dip,
        citation=Citation(
            authors="Wilder",
            year=1978,
            title="New Concepts in Technical Trading Systems",
            venue="Practitioner book (RSI oversold zone)",
            peer_reviewed=False,
            tested_rule=(
                "Wilder's RSI defaults to 14 periods. He marked 30 as oversold and 70 as "
                "overbought. A dip buy in an uptrend uses the oversold zone and exits at "
                "the overbought zone."
            ),
            implemented_rule=(
                "Long after a close above the 200-day SMA with RSI(14) below 30; flat "
                "after RSI rises above 70."
            ),
            differences=(
                "The 200-day SMA is not in Wilder's RSI chapter. The 70 exit is his "
                "overbought line and is not a grid axis. lib.rsi14 is the 50-line trend. "
                "lib.rsi2 is Connors' 2-period rule, not this 14-period zone. Next-open "
                "fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="crsi",
        title="ConnorsRSI(3,2,100) below 10 with ADX(10) > 30",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("rsi_lt", 10, (5, 15), unit="CRSI"),
            Param("exit_gt", 70, (60, 80), unit="CRSI"),
            Param("adx_gt", 30, (25, 35), unit="ADX"),
        ),
        signal=_crsi_dip,
        citation=Citation(
            authors="Connors Research",
            year=2012,
            title="An Introduction to ConnorsRSI",
            venue="Practitioner guidebook (TradingMarkets / Connors Research)",
            peer_reviewed=False,
            tested_rule=(
                "ConnorsRSI(3,2,100) averages RSI(3) of the close, RSI(2) of the up/down "
                "streak, and the percent rank of the latest one-day change over 100 days. "
                "The pullback strategy buys when that value is below Y (Y from 5 to 15; "
                "the worked example is 10), while ADX(10) is above 30, and exits when "
                "ConnorsRSI closes above N (50, 60, 70, or 80; the worked example is 70)."
            ),
            implemented_rule=(
                "Long after ConnorsRSI(3,2,100) closes below 10 while ADX(10) is above 30; "
                "flat after ConnorsRSI closes above 70."
            ),
            differences=(
                "The guidebook also requires a price above $5, 21-day volume of 250,000 "
                "shares, a minimum sell-off, a close in the bottom of the day's range, and "
                "a further limit below the signal close. Those do not map onto one BTC "
                "series with next-open fills, so they are omitted. The percent rank uses "
                "the one-day percent change, divided by 100, so it is scale-free. Component "
                "lengths 3, 2, 100 and the ADX length 10 are not grid axes. This is not "
                "lib.rsi2, which is a single RSI(2). Next-open fills, BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="adx_sma",
        title="ADX(14) > 25 and +DI > -DI above the 200-day SMA",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 14, (10, 18)),
            Param("adx_gt", 25, (20, 30), unit="ADX"),
            Param("trend", 200, (150, 250)),
        ),
        signal=_adx_sma,
        citation=Citation(
            authors="Wilder",
            year=1978,
            title="New Concepts in Technical Trading Systems",
            venue="Practitioner book (directional movement plus a price filter)",
            peer_reviewed=False,
            tested_rule=(
                "+DI above -DI is Wilder's bullish directional reading, and ADX above 25 "
                "is the textbook 'trend is present' level on his 14-period system."
            ),
            implemented_rule=(
                "Long while +DI(14) is above -DI(14), ADX(14) is above 25, and the close "
                "is above its 200-day SMA."
            ),
            differences=(
                "lib.adx14 has no price average. The 200-day SMA is the filter this "
                "library uses on dip rules; Wilder did not require it. Long or flat, "
                "next-open fills, BTC. 25 is the textbook level, not fit here."
            ),
        ),
    ),
    LibraryStrategy(
        key="vortex",
        title="Vortex VI+(14) above VI-",
        family="trend",
        evaluation="daily",
        params=(Param("n", 14, (10, 21)),),
        signal=_vortex_trend,
        citation=Citation(
            authors="Botes & Siepman",
            year=2010,
            title="The Vortex Indicator",
            venue="Technical Analysis of Stocks & Commodities, January 2010",
            peer_reviewed=False,
            tested_rule=(
                "VI+ sums |high - prior low| over n bars and divides by the sum of true "
                "range. VI- uses |low - prior high|. VI+ above VI- is the bullish cross. "
                "The article's length is 14."
            ),
            implemented_rule="Long while VI+(14) is above VI-(14), flat otherwise.",
            differences=(
                "Sums start at the first bar that has a previous close, so index 0 is not "
                "in the first window. Long or flat, next-open fills, BTC. 21 is a longer "
                "published alternative used only as a neighbour."
            ),
        ),
    ),
    LibraryStrategy(
        key="trix",
        title="TRIX(15) above zero",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 15, (12, 18)),),
        signal=_trix_up,
        citation=Citation(
            authors="Hutson",
            year=1983,
            title="Good TRIX",
            venue="Technical Analysis of Stocks & Commodities, 1983",
            peer_reviewed=False,
            tested_rule=(
                "TRIX is the one-period percent change of a triple exponential average. "
                "Above zero, that triple average is rising. 15 is the chart default that "
                "followed the article."
            ),
            implemented_rule="Long while TRIX(15) of the close is positive, flat otherwise.",
            differences=(
                "Each EMA is seeded with the SMA of its first n finite values. This is "
                "not lib.ema12x26: three averages, then their slope, at length 15. "
                "Next-open fills, BTC, long only."
            ),
        ),
    ),
    LibraryStrategy(
        key="chandelier",
        title="22-day breakout with a 3-ATR chandelier exit",
        family="breakout",
        evaluation="daily",
        params=(
            Param("entry", 22, (18, 26)),
            Param("atr", 22, (14, 28)),
            Param("mult", 3, (2, 4), unit="ATR"),
        ),
        signal=_chandelier,
        citation=Citation(
            authors="LeBeau",
            year=1992,
            title="Technical Traders Guide to Computer Analysis of the Futures Markets",
            venue="Practitioner book (Charles LeBeau; the chandelier exit)",
            peer_reviewed=False,
            tested_rule=(
                "The chandelier stop hangs a multiple of ATR below the highest high since "
                "the trade began. The widely published pair is 3 ATR and a 22-day extreme. "
                "The entry used with it is a break of the prior 22-day high."
            ),
            implemented_rule=(
                "Long after a close above the prior 22-day high; flat after a close below "
                "the highest high since entry minus 3 times Wilder ATR(22)."
            ),
            differences=(
                "The exit is path-dependent, unlike lib.turtle55 and lib.donchian55, which "
                "exit on a prior low. The entry bar is kept long even if that close is "
                "already through the stop. No short side, no ATR position size, next-open "
                "fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="hi52",
        title="Within 5% of the 252-day high",
        family="momentum",
        evaluation="daily",
        params=(
            Param("n", 252, (210, 300)),
            Param("near", 95, (90, 98), unit="percent"),
        ),
        signal=_hi52,
        citation=Citation(
            authors="George & Hwang",
            year=2004,
            title="The 52-Week High and Momentum Investing",
            venue="The Journal of Finance 59(5), 2145-2176",
            peer_reviewed=True,
            link="https://doi.org/10.1111/j.1540-6261.2004.00695.x",
            tested_rule=(
                "Nearness to the 52-week high is the current price divided by the highest "
                "price of the past 252 trading days. Stocks nearer that high had higher "
                "subsequent returns than a past-return sort. The test is cross-sectional, "
                "on US equities, from 1963 to 2001."
            ),
            implemented_rule=(
                "Long while the daily close is at least 95% of the highest close of the "
                "last 252 days, flat otherwise."
            ),
            differences=(
                "One asset, long or flat, not a cross-sectional long/short book. The 95% "
                "band is a pre-registered nearness cut, not a portfolio quantile. The high "
                "is the trailing close, today included. Next-open fills, BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="tsmom_vote",
        title="Majority of 1-, 3-, and 12-month time-series momentum",
        family="momentum",
        evaluation="daily",
        params=(
            Param("slow", 252, (210, 294)),
            Param("vote", 2, (1, 3), unit="signs"),
        ),
        signal=_tsmom_vote,
        citation=Citation(
            authors="Hurst, Ooi & Pedersen",
            year=2017,
            title="A Century of Evidence on Trend-Following Investing",
            venue="The Journal of Portfolio Management 44(1), 15-29",
            peer_reviewed=True,
            link="https://doi.org/10.3905/jpm.2017.44.1.015",
            tested_rule=(
                "An equal-weighted combination of 1-month, 3-month, and 12-month "
                "time-series momentum. Each sign is long when the past excess return is "
                "positive and short when it is negative. Positions are sized to a volatility "
                "target. 67 futures and forwards, 1880-2016, rebalanced monthly."
            ),
            implemented_rule=(
                "Long when at least two of the 21-day, 63-day, and 252-day close-to-close "
                "signs are positive; flat otherwise."
            ),
            differences=(
                "Two up-signs and one down-sign are net long in their sum, which is this "
                "vote of 2. A net short sum is flat here, not short. No volatility target "
                "(the book is all-in or flat). Daily signs, not month-end excess returns. "
                "The 21- and 63-day windows are fixed. lib.tsmom30d, lib.tsmom90d, and "
                "lib.tsmom12m are the separate horizons. One asset, raw close-to-close, "
                "next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="vol_trend",
        title="12-month trend, flat when vol is above its median",
        family="momentum",
        evaluation="daily",
        params=(
            Param("mom", 252, (210, 294)),
            Param("vol", 21, (14, 30)),
        ),
        signal=_vol_trend,
        citation=Citation(
            authors="Moreira & Muir",
            year=2017,
            title="Volatility-Managed Portfolios",
            venue="The Journal of Finance 72(4), 1611-1644",
            peer_reviewed=True,
            link="https://doi.org/10.1111/jofi.12513",
            tested_rule=(
                "Scale a factor's position by the inverse of its previous-month realized "
                "variance, so the book takes less risk when volatility was recently high. "
                "Barroso & Santa-Clara (2015, Journal of Financial Economics 116, 111-120, "
                "https://doi.org/10.1016/j.jfineco.2014.11.010) do the same for cross-sectional "
                "momentum and find that it removes the crashes."
            ),
            implemented_rule=(
                "Long when the close is above the close 252 days ago and 21-day realized "
                "volatility is at or below its own 126-day median; flat otherwise."
            ),
            differences=(
                "The papers scale size. This book cannot: it is all-in or flat, so a "
                "high-vol month is flat instead of a smaller long. The trend is the "
                "single-asset 252-day sign, not a factor portfolio. Realized volatility "
                "is the sample standard deviation of one-day log returns and is not "
                "annualized, because it is only compared with its own median. The 126-day "
                "median is fixed. Next-open fills, BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="dow",
        title="Long on UTC Monday only",
        family="seasonality",
        evaluation="daily",
        params=(Param("dow", 0, (6, 1), unit="weekday"),),
        signal=_dow,
        citation=Citation(
            authors="Aharon & Qadan",
            year=2019,
            title="Bitcoin and the day-of-the-week effect",
            venue="Finance Research Letters 31, 415-424",
            peer_reviewed=True,
            link="https://doi.org/10.1016/j.frl.2018.12.004",
            tested_rule=(
                "OLS and GARCH on daily bitcoin, 2010-2017. Monday returns and Monday "
                "volatility are higher than the other weekdays. They report the regression, "
                "not a trading rule."
            ),
            implemented_rule=(
                "Long for the UTC Monday session only: the Sunday close turns the signal "
                "on and the Monday close turns it off. Weekday 0 is Monday."
            ),
            differences=(
                "The paper did not trade the effect. Neighbours are Sunday (6) and Tuesday "
                "(1), not other findings. The decision uses tomorrow's date, which is known "
                "at today's close, and no later price. Long or flat, next-open fills, BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="tom",
        title="Turn of the month (last day and first 3)",
        family="seasonality",
        evaluation="daily",
        params=(
            Param("last", 1, (2, 3), unit="days"),
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
                "The four-day turn of the month is the last trading day and the first "
                "three trading days. Over 1926-2005 that window earned the equity premium; "
                "the other days did not. The effect showed up in 31 of 35 countries. "
                "Daily CRSP, not a bitcoin test."
            ),
            implemented_rule=(
                "Long when the next UTC calendar day is the last day of the month or one "
                "of the first three. Flat on the other days."
            ),
            differences=(
                "Bitcoin trades every calendar day, so the window is calendar days rather "
                "than exchange sessions. The paper measured returns; it did not publish an "
                "all-in timing backtest. Tomorrow's date is known at today's close. "
                "Next-open fills, BTC, fees, no cash yield."
            ),
        ),
    ),
    LibraryStrategy(
        key="halloween",
        title="Halloween indicator (long November through April)",
        family="seasonality",
        evaluation="daily",
        params=(Param("start", 11, (10, 12), unit="month"),),
        signal=_halloween,
        citation=Citation(
            authors="Bouman & Jacobsen",
            year=2002,
            title='The Halloween Indicator, "Sell in May and Go Away": Another Puzzle',
            venue="American Economic Review 92(5), 1618-1635",
            peer_reviewed=True,
            link="https://doi.org/10.1257/000282802762024683",
            tested_rule=(
                "November-April stock returns exceed May-October returns in 36 of 37 "
                "countries. The strategy implied by the saying is long from November "
                "through April and out of the market from May through October."
            ),
            implemented_rule=(
                "Long when the next UTC day falls in November through April; flat from "
                "May through October. The long window is six months starting at month 11."
            ),
            differences=(
                "The paper studied equity indexes, not bitcoin. The six-month length is "
                "fixed; only the starting month is a neighbour (October or December). "
                "Cash earns nothing. Next-open fills, BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="ichimoku_tk",
        title="Ichimoku tenkan/kijun cross",
        family="trend",
        evaluation="daily",
        params=(
            Param("conv", 9, (7, 12)),
            Param("base", 26, (22, 30)),
        ),
        signal=_ichimoku_tk,
        citation=Citation(
            authors="Hosoda",
            year=1969,
            title="Ichimoku Kinko Hyo (tenkan/kijun cross)",
            venue="Practitioner; conversion line 9, base line 26",
            peer_reviewed=False,
            tested_rule=(
                "The conversion line is the midpoint of the last 9 highs and lows. The "
                "base line is the same midpoint over 26. Conversion above base is the "
                "bullish cross. The cloud is a separate filter."
            ),
            implemented_rule=(
                "Long while the 9-period conversion line is above the 26-period base "
                "line, flat otherwise."
            ),
            differences=(
                "lib.ichimoku is the close versus the cloud and does not require this "
                "cross. The cloud, the lagging span, and the 26-period displacement are "
                "not used. Next-open fills, BTC, long only."
            ),
        ),
    ),
    LibraryStrategy(
        key="guppy",
        title="Guppy multiple moving average",
        family="trend",
        evaluation="daily",
        params=(
            Param("fast", 3, (2, 4)),
            Param("slow", 60, (55, 70)),
        ),
        signal=_guppy,
        citation=Citation(
            authors="Guppy",
            year=2004,
            title="Trend Trading",
            venue="Practitioner book (the multiple moving average; also Market Trading Tactics)",
            peer_reviewed=False,
            tested_rule=(
                "The trader group is the 3, 5, 8, 10, 12 and 15-period EMAs. The investor "
                "group is the 30, 35, 40, 45, 50 and 60-period EMAs. Agreement is the "
                "short group sitting entirely above the long group."
            ),
            implemented_rule=(
                "Long while the lowest of the short-group EMAs is above the highest of "
                "the long-group EMAs. fast replaces 3 and slow replaces 60; the other "
                "ten lengths stay put."
            ),
            differences=(
                "Not a two-average cross and not lib.sma_stack (7, 25, 50). One asset, "
                "long or flat, next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="elder_ray",
        title="Elder Ray bear-power recovery",
        family="trend",
        evaluation="daily",
        params=(Param("n", 13, (10, 16)),),
        signal=_elder_ray,
        citation=Citation(
            authors="Elder",
            year=1993,
            title="Trading for a Living",
            venue="Practitioner book (Elder Ray)",
            peer_reviewed=False,
            tested_rule=(
                "Bull power is the high minus a 13-day EMA. Bear power is the low minus "
                "that EMA. The buy he describes in an uptrend is bear power below zero "
                "but rising, with the EMA itself rising."
            ),
            implemented_rule=(
                "Long while the 13-day EMA is above its previous value and bear power "
                "is below zero and above its previous value."
            ),
            differences=(
                "Bull power is not a second condition. There is no separate exit "
                "oscillator: the bar fails the test and the book is flat. Not Elder "
                "Impulse (EMA slope plus the MACD histogram) and not lib.ema12x26. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="dema",
        title="DEMA(20) rising",
        family="trend",
        evaluation="daily",
        params=(Param("n", 20, (15, 25)),),
        signal=_dema_rising,
        citation=Citation(
            authors="Mulloy",
            year=1994,
            title="Smoothing Data with Faster Moving Averages",
            venue="Technical Analysis of Stocks & Commodities, 1994",
            peer_reviewed=False,
            tested_rule=(
                "DEMA is 2 * EMA(n) - EMA(EMA(n)). It tracks price with less lag than "
                "one EMA of the same length. A rising DEMA is that line above its "
                "previous value."
            ),
            implemented_rule="Long while DEMA(20) of the close is above its previous value.",
            differences=(
                "Not a price-versus-DEMA cross and not a single EMA. TEMA's slope is "
                "lib.trix; this is the double average, not the triple. Each EMA is "
                "seeded with the SMA of its first n finite values. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="cmo",
        title="Chande momentum oscillator above zero",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 20, (14, 30)),),
        signal=_cmo_up,
        citation=Citation(
            authors="Chande & Kroll",
            year=1994,
            title="The New Technical Trader",
            venue="Practitioner book (Chande momentum oscillator)",
            peer_reviewed=False,
            tested_rule=(
                "CMO is 100 times the sum of up-moves minus the sum of down-moves, "
                "divided by the sum of both, over n bars. Above zero, the raw gains "
                "exceed the raw losses. The book contrasts it with RSI, which smooths."
            ),
            implemented_rule="Long while CMO(20) is above zero, flat otherwise.",
            differences=(
                "The sums are not Wilder-smoothed, so this is not lib.rsi14 and not a "
                "rescaled RSI. A window with no movement is 0, which is not long. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="qstick",
        title="Qstick(8) above zero",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 8, (5, 12)),),
        signal=_qstick_up,
        citation=Citation(
            authors="Chande & Kroll",
            year=1994,
            title="The New Technical Trader",
            venue="Practitioner book (Qstick)",
            peer_reviewed=False,
            tested_rule=(
                "Qstick is the moving average of close minus open. Above zero, the "
                "bodies have closed up on balance. The book introduces it as a "
                "quantitative candlestick, not a chart pattern."
            ),
            implemented_rule="Long while the 8-day SMA of close minus open is above zero.",
            differences=(
                "8 is the length pre-registered here; the book treats the length as a "
                "choice and a common chart setting is 8. This averages the body. "
                "lib.ibs is one day's close location inside the high-low range and "
                "does not use the open. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="kst",
        title="Know Sure Thing above its signal line",
        family="momentum",
        evaluation="daily",
        params=(Param("sig", 9, (7, 12)),),
        signal=_kst_cross,
        citation=Citation(
            authors="Pring",
            year=1992,
            title="Summed Rate of Change (KST)",
            venue="Practitioner; Martin Pring, Technical Analysis Explained",
            peer_reviewed=False,
            tested_rule=(
                "The daily KST adds four smoothed rates of change: ROC(10), ROC(15) and "
                "ROC(20) each smoothed by 10, and ROC(30) smoothed by 15, with weights "
                "1, 2, 3 and 4. The signal line is a 9-period SMA of that sum. Long "
                "when KST is above the signal line."
            ),
            implemented_rule=(
                "Long while that KST is above its 9-period SMA. The four ROC lengths, "
                "the three smoothers of 10 and the one of 15, and the weights, stay fixed."
            ),
            differences=(
                "Only the signal length is a neighbour. This is not lib.macd and not "
                "a single rate of change. ROC is a close-to-close fraction, which does "
                "not change the sign of KST minus its average. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="schaff",
        title="Schaff trend cycle cross above 25",
        family="momentum",
        evaluation="daily",
        params=(
            Param("cycle", 10, (8, 12)),
            Param("buy", 25, (20, 30), unit="STC"),
        ),
        signal=_schaff,
        citation=Citation(
            authors="Schaff",
            year=2008,
            title="The Schaff Trend Cycle",
            venue="Practitioner; FX-Strategy description and the published 10/23/50 defaults",
            peer_reviewed=False,
            tested_rule=(
                "STC runs a 23/50 MACD line through a stochastic of length 10, smooths "
                "that fraction halfway toward its previous value, and repeats the "
                "stochastic and the smoother once. The published lines are 25 and 75: "
                "a turn up through 25 is the buy and a turn down through 75 is the sell."
            ),
            implemented_rule=(
                "Long after STC crosses above 25; flat after it crosses below 75. "
                "Cycle length and the buy line are the grid. 23, 50, the 0.5 smoother "
                "and the 75 exit stay fixed."
            ),
            differences=(
                "A zero-range window keeps the previous fraction, and the first such "
                "bar is 50. lib.macd is the 12/26 line against a 9-period signal, not "
                "this double stochastic of a 23/50 line. Next-open fills, BTC, long only."
            ),
        ),
    ),
    LibraryStrategy(
        key="fisher",
        title="Fisher transform rising",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 10, (7, 14)),),
        signal=_fisher_up,
        citation=Citation(
            authors="Ehlers",
            year=2004,
            title="Cybernetic Analysis for Stocks and Futures",
            venue="Practitioner book (the Fisher transform)",
            peer_reviewed=False,
            tested_rule=(
                "The midpoint's location in its n-day high-low range is smoothed with "
                "weights 0.33 and 0.67, clamped, and passed through one-half the log "
                "of (1+x)/(1-x), then smoothed again. The trade he shows is the Fisher "
                "value turning up through its previous value."
            ),
            implemented_rule=(
                "Long while the Fisher transform of length 10 is above its previous value."
            ),
            differences=(
                "The recursion is seeded at 0 when the first window exists. That seed "
                "is not a price. Not lib.stoch (a raw percent of the range) and not "
                "the relative vigor index from the same book. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="rvi",
        title="Relative vigor index above its signal",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 10, (8, 14)),),
        signal=_rvi_cross,
        citation=Citation(
            authors="Ehlers",
            year=2004,
            title="Cybernetic Analysis for Stocks and Futures",
            venue="Practitioner book (relative vigor index)",
            peer_reviewed=False,
            tested_rule=(
                "Numerator and denominator are the close-open and the high-low, each "
                "weighted 1, 2, 2, 1 across four bars and summed over n. The index is "
                "their ratio. The signal line is the same 1-2-2-1 weighting of the "
                "index. Long when the index is above the signal line."
            ),
            implemented_rule=(
                "Long while RVI(10) is above that signal line. A zero denominator is 0."
            ),
            differences=(
                "lib.ibs is a single bar's close location in the high-low range, with "
                "a 200-day filter and no signal line. This one averages the body "
                "against the range. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="dpo",
        title="Detrended price oscillator above zero",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 20, (14, 30)),),
        signal=_dpo_up,
        citation=Citation(
            authors="Achelis",
            year=1995,
            title="Technical Analysis from A to Z",
            venue="Practitioner reference (detrended price oscillator)",
            peer_reviewed=False,
            tested_rule=(
                "DPO subtracts an n-period moving average from the close n/2+1 bars "
                "earlier, which takes the trend out of that older price. Above zero, "
                "the displaced close is above the current average."
            ),
            implemented_rule=(
                "Long while the close from n/2+1 bars ago is above the n-day SMA "
                "through today. n is 20, so the displacement is 11."
            ),
            differences=(
                "The displaced close is older than the average, so the value at t does "
                "not use a later bar. This is not 'price above its SMA' (lib.sma100): "
                "the price in the comparison is shifted back. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="wilder_vol",
        title="Close outside yesterday plus or minus 3 ATR",
        family="breakout",
        evaluation="daily",
        params=(
            Param("atr", 14, (10, 20)),
            Param("mult", 3, (2, 4), unit="ATR"),
        ),
        signal=_wilder_vol,
        citation=Citation(
            authors="Wilder",
            year=1978,
            title="New Concepts in Technical Trading Systems",
            venue="Practitioner book (the volatility system)",
            peer_reviewed=False,
            tested_rule=(
                "The volatility system is a stop-and-reverse. Average true range times "
                "a constant near 3 (he discusses 2.8 to 3.1; the worked constant is 3) "
                "is subtracted from the highest close since the trade began, or added "
                "to the lowest close. His example uses a 7-day average. A close through "
                "that stop reverses the position."
            ),
            implemented_rule=(
                "Long after the close is above yesterday's close plus 3 times Wilder "
                "ATR(14); flat after the close is below yesterday's close minus that band."
            ),
            differences=(
                "The band is redrawn around yesterday's close every day. It is not his "
                "significant-close trail, and the average is 14 (the period of his "
                "directional system) rather than 7. The short side is flat. Not a "
                "Keltner band around an EMA, and not lib.chandelier, which trails the "
                "highest high of the trade. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="bb_squeeze",
        title="Bollinger squeeze, then a close above the upper band",
        family="breakout",
        evaluation="daily",
        params=(
            Param("n", 20, (15, 25)),
            Param("kc", 15, (12, 20), unit="tenths"),
        ),
        signal=_bb_squeeze,
        citation=Citation(
            authors="Bollinger; Carter",
            year=2001,
            title="Bollinger on Bollinger Bands",
            venue="Practitioner; the TTM form is John Carter, Mastering the Trade",
            peer_reviewed=False,
            tested_rule=(
                "Bollinger's squeeze is a volatility contraction. Carter's traded form "
                "is the 20-day, 2-standard-deviation band sitting inside a Keltner "
                "channel, then a release in the direction of the break. The usual "
                "Keltner multiple in that form is 1.5 ATR."
            ),
            implemented_rule=(
                "Long after a close above the upper Bollinger band when the previous "
                "bar had the Bollinger bands inside the Keltner channel; flat after a "
                "close back through the Bollinger average. Both channels use length n. "
                "Bollinger width stays 2. kc is the Keltner multiple in tenths, 15 "
                "meaning 1.5."
            ),
            differences=(
                "lib.bb_break buys the upper band with no squeeze. The Keltner here is "
                "this catalog's (EMA of typical price, Wilder ATR). The exit matches "
                "lib.bb_break and is not a momentum-histogram turn. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="turtle_soup",
        title="Failed 20-day low, two-day hold, above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("n", 20, (15, 25)),
            Param("hold", 2, (1, 3), unit="days"),
            Param("trend", 200, (150, 250)),
        ),
        signal=_turtle_soup,
        citation=Citation(
            authors="Raschke & Connors",
            year=1996,
            title="Street Smarts",
            venue="Practitioner book (Turtle Soup)",
            peer_reviewed=False,
            tested_rule=(
                "Turtle Soup fades a new 20-day extreme that fails. The 20-day low "
                "being broken has to be at least three bars old. The classic trade is "
                "the short of a failed 20-day high; the long is the mirror, a failed "
                "20-day low. It is a short-term reversal, not the turtle breakout."
            ),
            implemented_rule=(
                "Long for 2 sessions after the low takes out the prior 20-day low, the "
                "close finishes back above that low, that low is at least three days "
                "old, and the close is above the 200-day SMA. A new signal during the "
                "hold is ignored."
            ),
            differences=(
                "The book sells a failed high and often exits the same session. Short "
                "is not available, so this is the long mirror, and the daily book holds "
                "the signal day plus one more instead of an intraday scalp. The "
                "200-day SMA is the direction filter added for a long-only book. The "
                "three-day age is hers and is not a grid axis. lib.turtle20 buys the "
                "break and exits on a 10-day low. Next-open fills, BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="rsi2_cum",
        title="Two-day cumulative RSI(2) below 35 above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("sum_lt", 35, (25, 45), unit="RSI"),
            Param("trend", 200, (150, 250)),
            Param("exit_ma", 5, (4, 6)),
        ),
        signal=_rsi2_cum,
        citation=Citation(
            authors="Connors & Alvarez",
            year=2008,
            title="Short Term Trading Strategies That Work",
            venue="Practitioner book (cumulative RSI)",
            peer_reviewed=False,
            tested_rule=(
                "Cumulative RSI adds RSI(2) across consecutive days. The two-day sum "
                "below 35, with the close above the 200-day average, is the pullback. "
                "The exit is a close above the 5-day average."
            ),
            implemented_rule=(
                "Long after RSI(2) today plus RSI(2) yesterday is below 35 while the "
                "close is above the 200-day SMA; flat after a close above the 5-day SMA."
            ),
            differences=(
                "lib.rsi2 is a single RSI(2) below 5, and lib.crsi is ConnorsRSI "
                "(3, 2, 100). The component length 2 and the two-day window are not "
                "grid axes. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="rev1m",
        title="One-month reversal above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("n", 21, (15, 30)),
            Param("trend", 200, (150, 250)),
        ),
        signal=_rev1m,
        citation=Citation(
            authors="Jegadeesh",
            year=1990,
            title="Evidence of Predictable Behavior of Security Returns",
            venue="The Journal of Finance 45(3), 881-898",
            peer_reviewed=True,
            link="https://doi.org/10.1111/j.1540-6261.1990.tb05110.x",
            tested_rule=(
                "US stocks, 1934-1987. The prior month's return predicts the next "
                "month with the opposite sign: last month's losers beat last month's "
                "winners. The sort is cross-sectional. He is not reporting a one-asset "
                "timing rule."
            ),
            implemented_rule=(
                "Long when the close is below the close 21 days ago and above the "
                "200-day SMA; flat when the 21-day return is positive."
            ),
            differences=(
                "One asset, long or flat, not a loser-minus-winner book. The 200-day "
                "SMA is not in the paper; it keeps the reversal on the long side of "
                "the trend this library already uses. 21 days is the daily stand-in "
                "for his one-month gap, checked every day rather than at month-end. "
                "The sign is the opposite of lib.tsmom30d. Next-open fills, BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="tsmom12_1",
        title="12-month momentum, skipping the last month",
        family="momentum",
        evaluation="daily",
        params=(
            Param("skip", 21, (15, 30)),
            Param("slow", 252, (210, 294)),
        ),
        signal=_tsmom12_1,
        citation=Citation(
            authors="Novy-Marx",
            year=2012,
            title="Is Momentum Really Momentum?",
            venue="Journal of Financial Economics 103(3), 429-453",
            peer_reviewed=True,
            link="https://doi.org/10.1016/j.jfineco.2011.05.003",
            tested_rule=(
                "US stocks, 1926-2010. Momentum formed from month t-12 to t-2, skipping "
                "the most recent month, predicts returns. The recent month's "
                "contribution is small next to the intermediate past. The test is "
                "cross-sectional."
            ),
            implemented_rule=(
                "Long when the close 21 days ago is above the close 252 days before "
                "that; flat otherwise."
            ),
            differences=(
                "One asset, long or flat, not a winner-minus-loser book. lib.tsmom12m "
                "is the month-end 12-month sign and does not drop the latest month. "
                "The skip and the formation window are both grid axes, one at a time. "
                "Daily closes, not month-end excess returns. Next-open fills, BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="bll_trb150",
        title="150-day trading-range breakout, 10-day hold",
        family="breakout",
        evaluation="daily",
        params=(
            Param("n", 150, (120, 180)),
            Param("hold", 10, (8, 12)),
        ),
        signal=_bll_trb,
        citation=Citation(
            **_BLL,
            tested_rule=(
                "Trading-range break-out: buy when the price rises above the local "
                "maximum of the preceding 50, 150 or 200 days, with returns measured "
                "over a fixed 10-day holding period after the signal. A fresh breakout "
                "inside that window is not a second trade."
            ),
            implemented_rule=(
                "Long for 10 days after a close above the prior 150-day closing high. "
                "A breakout while a hold is already running is ignored."
            ),
            differences=(
                "lib.bll_trb50 is the 50-day specification from the same table. The "
                "200-day specification is not this name and is not a neighbour (the "
                "neighbours are 120 and 180). The high is the closing high, the rule "
                "is long or flat, and the fill is the next open. Dow Jones industrials "
                "in the paper, BTC here, with fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="nr7",
        title="NR7 break of the prior high, above the 200-day SMA",
        family="breakout",
        evaluation="daily",
        params=(
            Param("n", 7, (4, 10)),
            Param("trend", 200, (150, 250)),
        ),
        signal=_nr7,
        citation=Citation(
            authors="Crabel",
            year=1990,
            title="Day Trading with Short Term Price Patterns and Opening Range Breakout",
            venue="Practitioner book (the NR7 pattern)",
            peer_reviewed=False,
            tested_rule=(
                "An NR7 day has the narrowest range of the last seven days. The trade "
                "is a break of that day's high on the following session. He traded it "
                "as an opening-range breakout, intraday."
            ),
            implemented_rule=(
                "Long when yesterday's range is strictly the smallest of the last n "
                "days, today's close is above yesterday's high, and today's close is "
                "above the 200-day SMA. Flat on every other day."
            ),
            differences=(
                "The daily book cannot express his opening range, so the break is a "
                "close above the NR day's high and the position lasts one session. "
                "The 200-day SMA is not in the pattern; it is the direction filter. "
                "n of 4 is the NR4 neighbour, not a second strategy. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="inside_day",
        title="Inside-day breakout",
        family="breakout",
        evaluation="daily",
        params=(Param("n", 1, (2, 3), unit="bars"),),
        signal=_inside_day,
        citation=Citation(
            authors="Crabel",
            year=1990,
            title="Day Trading with Short Term Price Patterns and Opening Range Breakout",
            venue="Practitioner book (the inside day)",
            peer_reviewed=False,
            tested_rule=(
                "An inside day has a lower high and a higher low than the day before. "
                "The trade is a break of that inside day's high or low."
            ),
            implemented_rule=(
                "Long on a close above the high of the latest inside day; a close "
                "below that day's low drops the setup and is not a long. The center "
                "compares the day with the one before it. A neighbour must sit inside "
                "the prior 2 or 3 bars' range. The long is that one close."
            ),
            differences=(
                "His fill is an intraday break of the inside day's range. This book "
                "uses the daily close that breaks the high, for one session, because "
                "a hold until the inside low would stay long through a rising tape. "
                "Not a Donchian channel: the window is one bar (or the neighbour's "
                "two or three), not 20. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="floor_pivot",
        title="Close above the floor-trader pivot and the 200-day SMA",
        family="trend",
        evaluation="daily",
        params=(Param("trend", 200, (150, 250)),),
        signal=_floor_pivot,
        citation=Citation(
            authors="Floor traders",
            year=0,
            title="Floor-trader pivot",
            venue="Practitioner pit calculation; (high + low + close) / 3 of the prior session",
            peer_reviewed=False,
            tested_rule=(
                "The pivot is the average of the prior session's high, low and close. "
                "A close above that pivot is the long side of the day's map. The "
                "resistance levels built from it are a separate rule."
            ),
            implemented_rule=(
                "Long when the close is above yesterday's pivot and above the 200-day "
                "SMA; flat otherwise."
            ),
            differences=(
                "No journal test of this arithmetic was found. R1 and S1 are not "
                "used. The 200-day SMA is the direction filter, not part of the pivot. "
                "The pivot uses only the previous day. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="fractal",
        title="Williams five-bar fractal break",
        family="breakout",
        evaluation="daily",
        params=(Param("wings", 2, (1, 3), unit="bars"),),
        signal=_fractal,
        citation=Citation(
            authors="Williams",
            year=1995,
            title="Trading Chaos",
            venue="Practitioner book (the fractal)",
            peer_reviewed=False,
            tested_rule=(
                "An up fractal is a high with two lower highs on each side. A down "
                "fractal is a low with two higher lows on each side. The middle bar "
                "is confirmed two bars later. A break of that high is the long trigger."
            ),
            implemented_rule=(
                "Long after a close above the latest confirmed up-fractal; flat after "
                "a close below the latest confirmed down-fractal. wings is 2, so the "
                "pattern is five bars, and the fractal confirmed today is centered "
                "two bars ago."
            ),
            differences=(
                "The alligator is not required. A one-bar wing and a three-bar wing "
                "are the neighbours, not separate names. This is not a Donchian "
                "window: only a local extreme with lower bars on both sides qualifies, "
                "and it cannot be confirmed before those later bars exist. Next-open "
                "fills, BTC, long only."
            ),
        ),
    ),
    LibraryStrategy(
        key="mass_index",
        title="Mass-index reversal bulge above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("ema", 9, (7, 12)),
            Param("span", 25, (20, 30)),
            Param("trend", 200, (150, 250)),
        ),
        signal=_mass_index,
        citation=Citation(
            authors="Dorsey",
            year=1992,
            title="The Mass Index",
            venue="Technical Analysis of Stocks & Commodities, June 1992",
            peer_reviewed=False,
            tested_rule=(
                "The mass index is the 25-day sum of a 9-day EMA of the high-low range "
                "divided by a second 9-day EMA of that average. A reversal bulge is the "
                "index rising above 27 and then falling back below 26.5. The index has "
                "no direction; the trade is taken with the prevailing trend."
            ),
            implemented_rule=(
                "Long after the bulge completes while the close is above the 200-day "
                "SMA; flat after a close back below that SMA. 27 and 26.5 stay fixed."
            ),
            differences=(
                "Both EMAs use alpha 2/(n+1), seeded by an SMA, which is the EMA in "
                "the rest of this catalog. A zero double-smoothed range has ratio 1 so "
                "the sum does not go missing. The trend filter is the 200-day SMA "
                "rather than a separate directional indicator he left unnamed. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="relvol",
        title="Relative volatility index above 50",
        family="momentum",
        evaluation="daily",
        params=(
            Param("n", 14, (10, 20)),
            Param("thr", 50, (45, 55), unit="RVI"),
        ),
        signal=_relvol,
        citation=Citation(
            authors="Dorsey",
            year=1993,
            title="The Relative Volatility Index",
            venue="Technical Analysis of Stocks & Commodities, 1993",
            peer_reviewed=False,
            tested_rule=(
                "The relative volatility index replaces RSI's price change with a "
                "standard deviation: up closes contribute the deviation of the highs, "
                "down closes the deviation of the lows. Above 50, upside volatility "
                "dominates. He used a short deviation window and a 14-period smoother; "
                "the length here is 14 for both."
            ),
            implemented_rule=(
                "Long while the index is above 50. The deviation and the Wilder "
                "smoother share the length n."
            ),
            differences=(
                "The standard deviation is the population deviation used for Bollinger "
                "bands in this catalog. Unchanged closes add nothing. A day with no "
                "deviation on either side is 50, which is not long. This is not "
                "lib.rsi14 (RSI of price) and not Ehlers' relative vigor index "
                "(lib.rvi). Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="ulcer",
        title="Above the 200-day SMA with a quiet ulcer index",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 14, (10, 21)),
            Param("trend", 200, (150, 250)),
        ),
        signal=_ulcer,
        citation=Citation(
            authors="Martin & McCann",
            year=1989,
            title="The Investor's Guide to Fidelity Funds",
            venue="Practitioner book (the ulcer index)",
            peer_reviewed=False,
            tested_rule=(
                "The ulcer index is the square root of the mean squared percentage "
                "drawdown from the highest close in the window. A lower reading is a "
                "calmer path. It was published as a risk measure for funds, not as a "
                "bitcoin timing rule."
            ),
            implemented_rule=(
                "Long while the close is above the 200-day SMA and the 14-day ulcer "
                "index is below its own 100-day median."
            ),
            differences=(
                "The 100-day median is fixed, the same way lib.vol_trend freezes its "
                "vol median. lib.vol_trend stands aside when return volatility is high. "
                "This one stands aside when the drawdown from the recent high is high, "
                "which is a different series. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="weekend",
        title="Flat on the UTC weekend",
        family="seasonality",
        evaluation="daily",
        params=(Param("span", 2, (1, 3), unit="days"),),
        signal=_weekend,
        citation=Citation(
            authors="French",
            year=1980,
            title="Stock Returns and the Weekend Effect",
            venue="Journal of Financial Economics 8(1), 55-69",
            peer_reviewed=True,
            link="https://doi.org/10.1016/0304-405X(80)90021-5",
            tested_rule=(
                "US stocks, 1953-1977. The Friday-close to Monday-close return is "
                "negative on average, unlike the other weekdays. He measures the "
                "return; he does not publish an all-in timing backtest."
            ),
            implemented_rule=(
                "Flat for the Saturday and Sunday UTC sessions, long the other five. "
                "The Friday close turns the signal off and the Sunday close turns it "
                "back on. Span 1 drops only Sunday. Span 3 also drops Friday."
            ),
            differences=(
                "Bitcoin trades the weekend, so Saturday and Sunday are real sessions. "
                "lib.dow is long on Monday only, which is a different result (Aharon "
                "and Qadan on bitcoin) and not this equity weekend. Tomorrow's date is "
                "known at today's close. Cash earns nothing. Next-open fills, BTC, fees."
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
