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
    alma,
    aroon,
    atr_wilder,
    awesome_oscillator,
    balance_of_power,
    bollinger,
    cci,
    chande_momentum,
    choppiness_index,
    cyber_cycle,
    connors_rsi,
    day_of_month,
    days_in_month,
    dema,
    demarker,
    detrended_price,
    efficiency_ratio,
    ema,
    fisher_transform,
    frama,
    garman_klass,
    hold_between,
    HILBERT_READY,
    hull_ma,
    ibs,
    ichimoku_cloud_top,
    ichimoku_first_index,
    ichimoku_spans,
    instantaneous_trendline,
    is_month_end,
    is_week_end,
    kaufman_ama,
    keltner,
    know_sure_thing,
    laguerre_filter,
    laguerre_rsi,
    linreg_r2,
    linreg_slope,
    linreg_stderr,
    linreg_value,
    macd_histogram,
    mass_index,
    mcginley_dynamic,
    mesa_adaptive,
    month_number,
    month_of_year,
    parabolic_sar,
    prior_max,
    prior_min,
    psychological_line,
    qstick,
    random_walk_index,
    range_expansion_index,
    realized_vol,
    relative_momentum_index,
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
    smma,
    super_smoother,
    stoch_rsi,
    stochastic_k,
    stochastic_momentum_index,
    supertrend_long,
    swing_index,
    third_friday_dom,
    tillson_t3,
    trix,
    true_strength_index,
    ulcer_index,
    ultimate_oscillator,
    variance_ratio,
    vertical_horizontal_filter,
    vidya,
    vortex,
    week_number,
    weekday_utc,
    williams_r,
    zero_lag_ema,
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


def _elder_impulse(bars: DailyBars, p: dict) -> np.ndarray:
    """13-day EMA rising and the 12/26/9 MACD histogram rising. Lengths 12, 26, 9 stay fixed."""
    line = ema(bars.close, int(p["n"]))
    hist = macd_histogram(bars.close, 12, 26, 9)
    return _finite_gt(line, shift_previous(line, 1)) & _finite_gt(hist, shift_previous(hist, 1))


def _macd_hist(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the 12/26 MACD histogram is above its previous value. Fast and slow stay fixed."""
    hist = macd_histogram(bars.close, 12, 26, int(p["signal"]))
    return _finite_gt(hist, shift_previous(hist, 1))


def _tsi_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the true strength index is above zero."""
    value = true_strength_index(bars.close, int(p["long"]), int(p["short"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.0)


def _awesome_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the awesome oscillator (fast median SMA minus slow) is above zero."""
    value = awesome_oscillator(bars.high, bars.low, int(p["fast"]), int(p["slow"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.0)


def _alligator(bars: DailyBars, p: dict) -> np.ndarray:
    """Lips above teeth above jaw. Each line is an older SMMA, displaced by Williams' counts.

    Teeth stay at length 8, displaced 5. Lips are displaced 3 and the jaw 8.
    The value plotted on day t was computed on an earlier day, so nothing after t is used.
    """
    lips = shift_previous(smma(bars.close, int(p["lips"])), 3)
    teeth = shift_previous(smma(bars.close, 8), 5)
    jaw = shift_previous(smma(bars.close, int(p["jaw"])), 8)
    return _finite_gt(lips, teeth) & _finite_gt(teeth, jaw)


def _bop_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the smoothed balance of power is above zero."""
    value = balance_of_power(bars.open, bars.high, bars.low, bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.0)


def _psych_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the percent of up closes is above the threshold."""
    value = psychological_line(bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > float(p["thr"]))


def _chop(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while choppiness is below 38.2 and the close is above the trend SMA.

    38.2 is Dreiss's trending band (100 minus 61.8). It is not a grid axis.
    """
    value = choppiness_index(bars.high, bars.low, bars.close, int(p["n"]))
    trend = sma(bars.close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        quiet = np.isfinite(value) & (value < 38.2)
        return quiet & np.isfinite(trend) & (bars.close > trend)


def _rwi_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the RWI of the highs is above 1 and above the RWI of the lows."""
    up, down = random_walk_index(bars.high, bars.low, bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(up) & np.isfinite(down) & (up > 1.0) & (up > down)


def _asi_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the swing index, averaged over ``n`` days, is positive.

    ``n`` 1 is Wilder's one-bar ASI slope. The limit move is half the previous
    close and is not a grid axis; it does not change the sign.
    """
    si = swing_index(bars.open, bars.high, bars.low, bars.close)
    value = si if int(p["n"]) == 1 else sma_skip_leading(si, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.0)


def _darvas(bars: DailyBars, p: dict) -> np.ndarray:
    """Long on a close above a completed Darvas box; flat on a close under its bottom.

    A box starts when the high exceeds the prior ``look`` highs. It completes
    after ``confirm`` later days that do not take that high out. The top is
    that high. The bottom is the lowest low of those confirming days. A new
    high during the count restarts it. A newly completed box replaces the old
    one and the book goes flat until the new top breaks.
    """
    look = int(p["look"])
    confirm = int(p["confirm"])
    high = bars.high
    low = bars.low
    close = bars.close
    prior = prior_max(high, look)
    out = np.zeros(len(bars), dtype=bool)
    pending = False
    top = np.nan
    bot_run = np.nan
    age = 0
    active_top = np.nan
    active_bot = np.nan
    long = False
    for t in range(len(bars)):
        if pending:
            if high[t] > top:
                top = float(high[t])
                age = 0
                bot_run = np.nan
            else:
                age += 1
                bot_run = float(low[t]) if not np.isfinite(bot_run) else min(bot_run, float(low[t]))
                if age >= confirm:
                    active_top = top
                    active_bot = bot_run
                    pending = False
                    long = False
                    age = 0
                    bot_run = np.nan
        elif np.isfinite(prior[t]) and high[t] > prior[t]:
            pending = True
            top = float(high[t])
            age = 0
            bot_run = np.nan
        if np.isfinite(active_top) and np.isfinite(active_bot):
            if not long:
                if close[t] > active_top:
                    long = True
            elif close[t] < active_bot:
                long = False
        out[t] = long
    return out


def _ichimoku_cloud(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while senkou span A is above senkou span B. Price is not in the rule."""
    span_a, span_b = ichimoku_spans(
        bars.high, bars.low, int(p["conv"]), int(p["base"]), int(p["span"]),
    )
    return _finite_gt(span_a, span_b)


def _td_setup(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while at least ``count`` consecutive closes each beat the close four days earlier.

    The four-day comparison is DeMark's and is not a grid axis. The streak
    resets on the first close that does not beat that earlier close. The
    close also has to be above the trend SMA.
    """
    close = bars.close
    need = int(p["count"])
    above = np.zeros(len(bars), dtype=bool)
    if len(bars) > 4:
        above[4:] = close[4:] > close[:-4]
    streak = np.zeros(len(bars), dtype=np.int64)
    for t in range(4, len(bars)):
        streak[t] = streak[t - 1] + 1 if above[t] else 0
    trend = sma(close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        return (streak >= need) & np.isfinite(trend) & (close > trend)


def _rei_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the range expansion index is above zero."""
    value = range_expansion_index(bars.high, bars.low, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.0)


def _linreg_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the least-squares slope of the last n closes is positive."""
    value = linreg_slope(bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.0)


def _mom_mid(bars: DailyBars, p: dict) -> np.ndarray:
    """Long when the close ``mid`` days ago is above the close ``slow`` days ago.

    That is the return from ``slow`` days ago to ``mid`` days ago, not the
    return that ends today.
    """
    close = bars.close
    formed = shift_previous(close, int(p["mid"]))
    base = shift_previous(close, int(p["slow"]))
    return _finite_gt(formed, base)


def _first_half(bars: DailyBars, p: dict) -> np.ndarray:
    """Long when tomorrow's UTC calendar day is 1 through ``through``.

    Today's close decides tomorrow. The rest of the month is flat.
    """
    tomorrow = day_of_month(bars.ts + np.int64(DAY_MS))
    return (tomorrow >= 1) & (tomorrow <= int(p["through"]))


def _laguerre_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the Laguerre RSI is above the threshold. Both are stored as hundredths."""
    value = laguerre_rsi(bars.close, float(p["gamma"]) / 100.0)
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > float(p["thr"]) / 100.0)


def _vidya_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is above VIDYA. The CMO length and the average length are the same."""
    value = vidya(bars.close, int(p["n"]))
    return _finite_gt(bars.close, value)


def _t3_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is above T3. The volume factor is stored as hundredths."""
    value = tillson_t3(bars.close, int(p["n"]), float(p["vf"]) / 100.0)
    return _finite_gt(bars.close, value)


def _rmi_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the relative momentum index is above the threshold."""
    value = relative_momentum_index(bars.close, int(p["mom"]), int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > float(p["thr"]))


def _double7(bars: DailyBars, p: dict) -> np.ndarray:
    """Buy a close that is the lowest of n days, above the trend SMA; flat on an n-day closing high."""
    close = bars.close
    n = int(p["n"])
    window_min = rolling_min(close, n)
    window_max = rolling_max(close, n)
    trend = sma(close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        enter = (
            np.isfinite(window_min) & np.isfinite(trend)
            & (close > trend) & (close <= window_min)
        )
        leave = np.isfinite(window_max) & (close >= window_max)
    return _stateful(enter, leave)


def _mcginley_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is above the McGinley dynamic average."""
    value = mcginley_dynamic(bars.close, int(p["n"]))
    return _finite_gt(bars.close, value)


def _stoch_pop(bars: DailyBars, p: dict) -> np.ndarray:
    """Long after fast %K crosses up through the pop line; flat after it crosses back down.

    The cross uses the previous finite %K. A series that is already above the
    line before any cross stays flat.
    """
    value = stochastic_k(bars.high, bars.low, bars.close, int(p["k"]))
    prev = shift_previous(value, 1)
    pop = float(p["pop"])
    with np.errstate(invalid="ignore"):
        finite = np.isfinite(value) & np.isfinite(prev)
        enter = finite & (prev <= pop) & (value > pop)
        leave = finite & (prev >= pop) & (value < pop)
    return _stateful(enter, leave)


def _outside_day(bars: DailyBars, p: dict) -> np.ndarray:
    """Long on the close that breaks an outside day's high. A break of its low cancels it.

    Center ``n`` is 1: the high is above the prior high and the low is below
    the prior low. A larger ``n`` has to take out the prior ``n`` bars. The
    long is that one close, the same way ``lib.inside_day`` records its break.
    """
    n = int(p["n"])
    ceiling = prior_max(bars.high, n)
    floor = prior_min(bars.low, n)
    with np.errstate(invalid="ignore"):
        outside = (
            np.isfinite(ceiling) & np.isfinite(floor)
            & (bars.high > ceiling) & (bars.low < floor)
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
        if bool(outside[t]):
            ref_high = float(bars.high[t])
            ref_low = float(bars.low[t])
    return out


def _three_dn(bars: DailyBars, p: dict) -> np.ndarray:
    """n consecutive lower closes above the trend SMA; flat on a close above the exit SMA."""
    close = bars.close
    n = int(p["n"])
    lower = np.zeros(len(bars), dtype=bool)
    if len(bars) > 1:
        lower[1:] = close[1:] < close[:-1]
    streak = np.zeros(len(bars), dtype=bool)
    if n > 0 and len(bars) > n:
        window = sliding_window_view(lower.view(np.uint8), n)
        # window[i] is lower[i:i+n] and ends at index i + n - 1.
        # window[1] ends at index n, which is the first full streak.
        streak[n:] = window[1:].all(axis=1)[: len(bars) - n]
    trend = sma(close, int(p["trend"]))
    exit_ma = sma(close, int(p["exit_ma"]))
    with np.errstate(invalid="ignore"):
        enter = streak & np.isfinite(trend) & (close > trend)
        leave = np.isfinite(exit_ma) & (close > exit_ma)
    return _stateful(enter, leave)


def _demarker_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while DeMarker is above one half."""
    value = demarker(bars.high, bars.low, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.5)


def _td_countdown(bars: DailyBars, p: dict) -> np.ndarray:
    """Long on the bar a buy countdown completes.

    A buy setup is ``setup`` consecutive closes each below the close four
    days earlier. The four-day gap stays fixed. Counting starts on the
    setup's last bar. A bar counts when its close is at or below the low
    two bars earlier. The bars need not be consecutive. The final bar, when
    the target is above 8, also needs its low at or below the close of
    countdown bar 8. An opposing setup of the same length cancels an
    unfinished count. A new buy setup restarts it. The long is that one close.

    DeMark's buy is the completed countdown. Requiring that close to sit
    above the 200-day SMA rejected every completion on the 2017-2026 tape:
    the countdown is the exhaustion of a decline, and those nine completions
    all finished below the average. The average is not part of the cited buy.
    """
    close = bars.close
    low = bars.low
    setup_n = int(p["setup"])
    need = int(p["count"])
    out = np.zeros(len(bars), dtype=bool)
    buy_streak = 0
    sell_streak = 0
    active = False
    counted = 0
    close8 = np.nan
    for t in range(len(bars)):
        if t >= 4:
            if close[t] < close[t - 4]:
                buy_streak += 1
                sell_streak = 0
            elif close[t] > close[t - 4]:
                sell_streak += 1
                buy_streak = 0
            else:
                buy_streak = 0
                sell_streak = 0
        if t >= 4 and sell_streak == setup_n:
            active = False
            counted = 0
            close8 = np.nan
        if t >= 4 and buy_streak == setup_n:
            active = True
            counted = 0
            close8 = np.nan
        if active and t >= 2 and close[t] <= low[t - 2]:
            if counted + 1 == need and need > 8:
                if np.isfinite(close8) and low[t] <= close8:
                    counted = need
            else:
                counted += 1
                if counted == 8:
                    close8 = float(close[t])
            if counted == need:
                out[t] = True
                active = False
                counted = 0
                close8 = np.nan
    return out


def _trix_signal(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while TRIX(15) is above its signal average. The triple length stays 15."""
    value = trix(bars.close, 15)
    line = sma_skip_leading(value, int(p["signal"]))
    return _finite_gt(value, line)


def _stoch_slow(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while slow %D is above its own 3-day average.

    Slow %D is an SMA of length ``d`` of an SMA of length ``d`` of fast %K.
    The further 3-day average is Lane's signal on that slow line and is not
    a grid axis.
    """
    fast_k = stochastic_k(bars.high, bars.low, bars.close, int(p["k"]))
    slow_d = sma_skip_leading(sma_skip_leading(fast_k, int(p["d"])), int(p["d"]))
    line = sma_skip_leading(slow_d, 3)
    return _finite_gt(slow_d, line)


def _zlema_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is above the zero-lag EMA."""
    return _finite_gt(bars.close, zero_lag_ema(bars.close, int(p["n"])))


def _frama_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is above FRAMA. The -4.6 alpha map stays fixed."""
    return _finite_gt(bars.close, frama(bars.close, int(p["n"])))


def _ssmooth_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is above the two-pole super smoother."""
    return _finite_gt(bars.close, super_smoother(bars.close, int(p["n"])))


def _cyber_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the cyber cycle is above its one-bar trigger.

    The alpha is stored in hundredths. The price is the median,
    ``(high + low) / 2``, which is the series in the book.
    """
    price = (bars.high + bars.low) / 2.0
    cycle = cyber_cycle(price, float(p["alpha"]) / 100.0)
    out = _finite_gt(cycle, shift_previous(cycle, 1))
    # The seed formula covers indexes 2..5. The book filter starts at index 6.
    out[:6] = False
    return out


def _lfilter_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is above the Laguerre filter. Gamma is hundredths."""
    return _finite_gt(bars.close, laguerre_filter(bars.close, float(p["gamma"]) / 100.0))


def _alma_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is above ALMA. Sigma stays 6. Offset is hundredths."""
    value = alma(bars.close, int(p["n"]), float(p["offset"]) / 100.0, 6.0)
    return _finite_gt(bars.close, value)


def _tlbreak(bars: DailyBars, p: dict) -> np.ndarray:
    """Long after a white line breaks the high of the prior ``n`` lines.

    Lines are built from closes only, one new line on a bar at most. A line
    in the current direction needs a close beyond that line. A reversal needs
    a close beyond the extreme of the prior ``n`` lines. The long starts on
    the bullish reversal and ends on the bearish one. The opening line, and
    a continuation, do not enter.
    """
    n = int(p["n"])
    close = bars.close
    out = np.zeros(len(bars), dtype=bool)
    lines: list[tuple[int, float, float]] = []
    long = False
    for t in range(1, len(bars)):
        price = float(close[t])
        if not lines:
            base = float(close[0])
            if price != base:
                if price > base:
                    lines.append((1, base, price))
                else:
                    lines.append((-1, price, base))
            out[t] = long
            continue
        direction, bot, top = lines[-1]
        hi = lo = None
        if len(lines) >= n:
            recent = lines[-n:]
            hi = max(item[2] for item in recent)
            lo = min(item[1] for item in recent)
        added: tuple[int, float, float] | None = None
        if direction == 1:
            if price > top:
                added = (1, top, price)
            elif hi is not None and lo is not None and price < lo:
                added = (-1, price, top)
        elif price < bot:
            added = (-1, price, bot)
        elif hi is not None and price > hi:
            added = (1, bot, price)
        if added is not None:
            if added[0] == 1 and direction == -1:
                long = True
            elif added[0] == -1 and direction == 1:
                long = False
            lines.append(added)
        out[t] = long
    return out


def _engulf(bars: DailyBars, p: dict) -> np.ndarray:
    """Bullish engulfing close, and only above the trend SMA. The long is that close."""
    o = bars.open
    c = bars.close
    trend = sma(c, int(p["trend"]))
    out = np.zeros(len(bars), dtype=bool)
    for t in range(1, len(bars)):
        prior_bear = c[t - 1] < o[t - 1]
        this_bull = c[t] > o[t]
        covers = o[t] <= c[t - 1] and c[t] >= o[t - 1]
        larger = (c[t] - o[t]) > (o[t - 1] - c[t - 1])
        if prior_bear and this_bull and covers and larger and np.isfinite(trend[t]) and c[t] > trend[t]:
            out[t] = True
    return out


def _pierce(bars: DailyBars, p: dict) -> np.ndarray:
    """Piercing line: the session trades under the prior low and closes through the midpoint.

    Nison's second session opens below the prior low. On this 24-hour tape
    the open is the prior close (the largest open-to-prior-low gap in
    2017-2026 is still above that low), so the open test never passes. The
    low is the print that can take out the prior low, and it is known at
    the close. The close still has to finish above the midpoint of the prior
    black body and below the prior open, so a bullish engulfing is not this
    bar. The candle is white. The long is that close, and only above the
    trend SMA.
    """
    o = bars.open
    low = bars.low
    c = bars.close
    trend = sma(c, int(p["trend"]))
    out = np.zeros(len(bars), dtype=bool)
    for t in range(1, len(bars)):
        if not (c[t - 1] < o[t - 1] and c[t] > o[t]):
            continue
        midpoint = (o[t - 1] + c[t - 1]) / 2.0
        if (
            low[t] < low[t - 1]
            and c[t] > midpoint
            and c[t] < o[t - 1]
            and np.isfinite(trend[t])
            and c[t] > trend[t]
        ):
            out[t] = True
    return out


def _soldiers(bars: DailyBars, p: dict) -> np.ndarray:
    """Three white soldiers. Each opens within the prior body and closes near its high.

    The long is the third close, and only above the trend SMA. Near the high
    means the upper shadow is no larger than the body. The prior body is a
    closed interval: on this tape the next open prints at the prior close,
    which is the top of a white body, and a strict interior test never
    chained three soldiers. A doji has no body to open within.
    """
    o = bars.open
    h = bars.high
    c = bars.close
    trend = sma(c, int(p["trend"]))
    out = np.zeros(len(bars), dtype=bool)
    for t in range(3, len(bars)):
        good = True
        for i in (t - 2, t - 1, t):
            body_lo = min(o[i - 1], c[i - 1])
            body_hi = max(o[i - 1], c[i - 1])
            white = c[i] > o[i] and c[i] > c[i - 1]
            inside = body_hi > body_lo and body_lo <= o[i] <= body_hi
            near_high = (h[i] - c[i]) <= (c[i] - o[i])
            if not (white and inside and near_high):
                good = False
                break
        if good and np.isfinite(trend[t]) and c[t] > trend[t]:
            out[t] = True
    return out


def _oops(bars: DailyBars, p: dict) -> np.ndarray:
    """Low takes out the prior ``n``-day low and the close finishes back above it."""
    floor = prior_min(bars.low, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(floor) & (bars.low < floor) & (bars.close > floor)


def _vratio_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Variance ratio above 1 and the close above the trend SMA.

    The horizon has to fit inside the return window. A window that does not
    is flat, and it is not a grid point of this name.
    """
    q = int(p["q"])
    window = int(p["win"])
    if window <= q:
        return np.zeros(len(bars), dtype=bool)
    ratio = variance_ratio(bars.close, q, window)
    trend = sma(bars.close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        trending = np.isfinite(ratio) & (ratio > 1.0)
        return trending & np.isfinite(trend) & (bars.close > trend)


def _vhf_up(bars: DailyBars, p: dict) -> np.ndarray:
    """VHF above its 100-day median and the close above the trend SMA.

    The median length is fixed, the same way lib.ulcer freezes its median.
    """
    value = vertical_horizontal_filter(bars.close, int(p["n"]))
    med = rolling_median_skip_leading(value, 100)
    trend = sma(bars.close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        directional = np.isfinite(value) & np.isfinite(med) & (value > med)
        return directional & np.isfinite(trend) & (bars.close > trend)


def _accel_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Awesome oscillator above its own average. The 5/34 lengths stay fixed."""
    ao = awesome_oscillator(bars.high, bars.low, 5, 34)
    line = sma_skip_leading(ao, int(p["smooth"]))
    return _finite_gt(ao, line)


def _gator(bars: DailyBars, p: dict) -> np.ndarray:
    """Both alligator gaps wider than yesterday, and the lips above the jaw.

    Teeth stay length 8, displaced 5. Lips are displaced 3 and the jaw 8.
    The lines are shifted backward, so the gap on day t was known earlier.
    """
    lips = shift_previous(smma(bars.close, int(p["lips"])), 3)
    teeth = shift_previous(smma(bars.close, 8), 5)
    jaw = shift_previous(smma(bars.close, int(p["jaw"])), 8)
    jaw_teeth = np.abs(jaw - teeth)
    teeth_lips = np.abs(teeth - lips)
    wider = _finite_gt(jaw_teeth, shift_previous(jaw_teeth, 1)) & _finite_gt(
        teeth_lips, shift_previous(teeth_lips, 1),
    )
    return wider & _finite_gt(lips, jaw)


def _camarilla(bars: DailyBars, p: dict) -> np.ndarray:
    """Close above yesterday's Camarilla H3 and above the trend SMA.

    H3 is prior close plus the prior range times 1.1/4. The 1.1 factor stays fixed.
    """
    span = shift_previous(bars.high, 1) - shift_previous(bars.low, 1)
    h3 = shift_previous(bars.close, 1) + span * 1.1 / 4.0
    trend = sma(bars.close, int(p["trend"]))
    return _finite_gt(bars.close, h3) & _finite_gt(bars.close, trend)


def _regline_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is above the fitted value of the regression line."""
    return _finite_gt(bars.close, linreg_value(bars.close, int(p["n"])))


def _gk_calm(bars: DailyBars, p: dict) -> np.ndarray:
    """Close above the trend SMA and Garman-Klass variance below its 100-day median.

    The median length is fixed. One bar of the estimator can be negative;
    the average is what is compared with the median.
    """
    value = garman_klass(bars.open, bars.high, bars.low, bars.close, int(p["n"]))
    med = rolling_median_skip_leading(value, 100)
    trend = sma(bars.close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        calm = np.isfinite(value) & np.isfinite(med) & (value < med)
        return calm & np.isfinite(trend) & (bars.close > trend)


def _harami(bars: DailyBars, p: dict) -> np.ndarray:
    """Bullish harami: today's body sits inside the prior down body, above the trend SMA.

    The long is that close. Highs and lows outside the bodies do not count.
    """
    o = bars.open
    c = bars.close
    trend = sma(c, int(p["trend"]))
    out = np.zeros(len(bars), dtype=bool)
    for t in range(1, len(bars)):
        if not (c[t - 1] < o[t - 1]):
            continue
        body_hi = max(float(o[t]), float(c[t]))
        body_lo = min(float(o[t]), float(c[t]))
        if body_hi < o[t - 1] and body_lo > c[t - 1] and np.isfinite(trend[t]) and c[t] > trend[t]:
            out[t] = True
    return out


def _expiry(bars: DailyBars, p: dict) -> np.ndarray:
    """Long when tomorrow is one of the ``n`` UTC sessions ending on the third Friday.

    Today's close decides tomorrow. The third Friday is day 15 through 21,
    so a window of four sessions stays inside that month.
    """
    tomorrow = bars.ts + np.int64(DAY_MS)
    dom = day_of_month(tomorrow)
    third = third_friday_dom(tomorrow)
    n = int(p["n"])
    return (dom >= third - (n - 1)) & (dom <= third)


def _smi_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the stochastic momentum index is above zero. Both smooths stay 3."""
    value = stochastic_momentum_index(bars.high, bars.low, bars.close, int(p["n"]), 3)
    with np.errstate(invalid="ignore"):
        return np.isfinite(value) & (value > 0.0)


def _tsi_signal(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while TSI(25, 13) is above its signal EMA. The 25 and 13 stay fixed."""
    value = true_strength_index(bars.close, 25, 13)
    line = ema(value, int(p["signal"]))
    return _finite_gt(value, line)


def _cmo_signal(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while CMO(14) is above its SMA. The oscillator length stays 14."""
    value = chande_momentum(bars.close, 14)
    line = sma_skip_leading(value, int(p["signal"]))
    return _finite_gt(value, line)


def _mama_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is above MAMA. The slow limit stays 0.05."""
    mama, _phase = mesa_adaptive(bars.high, bars.low, float(p["fast"]) / 100.0, 0.05)
    out = _finite_gt(bars.close, mama)
    out[:HILBERT_READY] = False
    return out


def _itrend_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the close is above the instantaneous trendline. Alpha is hundredths."""
    line = instantaneous_trendline(bars.high, bars.low, float(p["alpha"]) / 100.0)
    out = _finite_gt(bars.close, line)
    out[:6] = False
    return out


def _ebsw_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the sine of the dominant-cycle phase is above the lead sine.

    The lead is ``lead`` degrees ahead. 45 is the book's advance. The phase
    is the Hilbert arctangent, not the one-bar slope of the cyber cycle.
    """
    _mama, phase = mesa_adaptive(bars.high, bars.low, 0.5, 0.05)
    rad = np.deg2rad(phase)
    lead = np.deg2rad(float(p["lead"]))
    sine = np.sin(rad)
    ahead = np.sin(rad + lead)
    out = _finite_gt(sine, ahead)
    out[:HILBERT_READY] = False
    return out


def _eratio_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Efficiency ratio above 0.30 and the close above the trend SMA. 0.30 stays fixed."""
    ratio = efficiency_ratio(bars.close, int(p["n"]))
    trend = sma(bars.close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        directional = np.isfinite(ratio) & (ratio > 0.30)
        return directional & np.isfinite(trend) & (bars.close > trend)


def _rsquared_up(bars: DailyBars, p: dict) -> np.ndarray:
    """R-squared above 0.40 and the least-squares slope positive. 0.40 stays fixed."""
    r2 = linreg_r2(bars.close, int(p["n"]))
    slope = linreg_slope(bars.close, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(r2) & np.isfinite(slope) & (r2 > 0.40) & (slope > 0.0)


def _seband_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Close above the regression value plus one residual standard error. The width stays 1."""
    center = linreg_value(bars.close, int(p["n"]))
    band = center + linreg_stderr(bars.close, int(p["n"]))
    return _finite_gt(bars.close, band)


def _kagi_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long after a yang reversal of at least ``rev`` percent, until a yin reversal.

    Built from closes, in order. The first line only sets the direction.
    A continuation does not enter. ``rev`` is stored in percent.
    """
    rev = float(p["rev"]) / 100.0
    close = bars.close
    out = np.zeros(len(bars), dtype=bool)
    if len(bars) == 0 or rev <= 0.0:
        return out
    direction = 0
    extreme = float(close[0])
    long = False
    for t in range(1, len(bars)):
        price = float(close[t])
        if direction == 0:
            if price >= extreme * (1.0 + rev):
                direction = 1
                extreme = price
            elif price <= extreme * (1.0 - rev):
                direction = -1
                extreme = price
        elif direction == 1:
            if price > extreme:
                extreme = price
            elif price <= extreme * (1.0 - rev):
                direction = -1
                extreme = price
                long = False
        elif price < extreme:
            extreme = price
        elif price >= extreme * (1.0 + rev):
            direction = 1
            extreme = price
            long = True
        out[t] = long
    return out


def _renko_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while the latest brick is white. A brick is ``brick`` percent of the level it leaves.

    Built from closes, in order. One bar can print more than one brick.
    The last brick of the bar sets the position. ``brick`` is stored in percent.
    """
    pct = float(p["brick"]) / 100.0
    close = bars.close
    out = np.zeros(len(bars), dtype=bool)
    if len(bars) == 0 or pct <= 0.0:
        return out
    level = float(close[0])
    white = False
    for t in range(1, len(bars)):
        price = float(close[t])
        guard = 0
        while guard < 1000 and level > 0.0 and price >= level * (1.0 + pct):
            level = level * (1.0 + pct)
            white = True
            guard += 1
        guard = 0
        while guard < 1000 and level > 0.0 and price <= level * (1.0 - pct):
            level = level * (1.0 - pct)
            white = False
            guard += 1
        out[t] = white
    return out


def _above_trend(close: np.ndarray, trend: np.ndarray, t: int) -> bool:
    return bool(np.isfinite(trend[t]) and close[t] > trend[t])


def _morning_star(bars: DailyBars, p: dict) -> np.ndarray:
    """Morning star: long black, a smaller body, then a white close through the first midpoint.

    A gap between the bodies does not occur on this tape, so it is not
    required. The long is the third close, and only above the trend SMA.
    """
    o = bars.open
    c = bars.close
    trend = sma(c, int(p["trend"]))
    out = np.zeros(len(bars), dtype=bool)
    for t in range(2, len(bars)):
        body0 = o[t - 2] - c[t - 2]
        if body0 <= 0.0:
            continue
        body1 = abs(c[t - 1] - o[t - 1])
        midpoint = (o[t - 2] + c[t - 2]) / 2.0
        star_top = max(o[t - 1], c[t - 1])
        if (
            body1 < body0
            and star_top < midpoint
            and c[t] > o[t]
            and c[t] > midpoint
            and _above_trend(c, trend, t)
        ):
            out[t] = True
    return out


def _hammer(bars: DailyBars, p: dict) -> np.ndarray:
    """Hammer: lower shadow at least twice the body, body in the top third, prior close down."""
    o = bars.open
    h = bars.high
    low = bars.low
    c = bars.close
    trend = sma(c, int(p["trend"]))
    out = np.zeros(len(bars), dtype=bool)
    for t in range(2, len(bars)):
        rng = h[t] - low[t]
        if rng <= 0.0 or not (c[t - 1] < c[t - 2]):
            continue
        body_lo = min(o[t], c[t])
        body_hi = max(o[t], c[t])
        body = body_hi - body_lo
        lower = body_lo - low[t]
        if lower >= 2.0 * body and body_lo >= low[t] + 2.0 * rng / 3.0 and _above_trend(c, trend, t):
            out[t] = True
    return out


def _rising_three(bars: DailyBars, p: dict) -> np.ndarray:
    """Rising three methods: a white candle, three bars inside its range, then a higher white close."""
    o = bars.open
    h = bars.high
    low = bars.low
    c = bars.close
    trend = sma(c, int(p["trend"]))
    out = np.zeros(len(bars), dtype=bool)
    for t in range(4, len(bars)):
        first = t - 4
        body0 = c[first] - o[first]
        if body0 <= 0.0:
            continue
        held = True
        for i in (first + 1, first + 2, first + 3):
            small = abs(c[i] - o[i]) < body0
            inside = h[i] <= h[first] and low[i] >= low[first]
            if not (small and inside):
                held = False
                break
        if held and c[t] > o[t] and c[t] > c[first] and _above_trend(c, trend, t):
            out[t] = True
    return out


def _three_outside(bars: DailyBars, p: dict) -> np.ndarray:
    """Three outside up: a bullish engulfing, then a close above that engulfing close."""
    o = bars.open
    c = bars.close
    trend = sma(c, int(p["trend"]))
    out = np.zeros(len(bars), dtype=bool)
    for t in range(2, len(bars)):
        prior_bear = c[t - 2] < o[t - 2]
        bull = c[t - 1] > o[t - 1]
        covers = o[t - 1] <= c[t - 2] and c[t - 1] >= o[t - 2]
        larger = (c[t - 1] - o[t - 1]) > (o[t - 2] - c[t - 2])
        if prior_bear and bull and covers and larger and c[t] > c[t - 1] and _above_trend(c, trend, t):
            out[t] = True
    return out


def _two_bar(bars: DailyBars, p: dict) -> np.ndarray:
    """Low under the prior ``n``-bar low and the close above the prior ``n``-bar high."""
    floor = prior_min(bars.low, int(p["n"]))
    ceiling = prior_max(bars.high, int(p["n"]))
    with np.errstate(invalid="ignore"):
        return np.isfinite(floor) & np.isfinite(ceiling) & (bars.low < floor) & (bars.close > ceiling)


def _td_combo(bars: DailyBars, p: dict) -> np.ndarray:
    """Long on the bar a buy combo completes.

    After a buy setup, count closes at or below the close two bars earlier.
    That is not the countdown, which uses the low two bars earlier. There is
    no bar-8 qualifier. An opposing setup cancels. A new buy setup restarts.
    The long is the completion. A 200-day filter is applied only when
    ``trend`` is a parameter; the registered rule leaves it off when that
    filter would reject every completion.
    """
    close = bars.close
    setup_n = int(p["setup"])
    need = int(p["count"])
    trend = sma(close, int(p["trend"])) if "trend" in p else None
    out = np.zeros(len(bars), dtype=bool)
    buy_streak = 0
    sell_streak = 0
    active = False
    counted = 0
    for t in range(len(bars)):
        if t >= 4:
            if close[t] < close[t - 4]:
                buy_streak += 1
                sell_streak = 0
            elif close[t] > close[t - 4]:
                sell_streak += 1
                buy_streak = 0
            else:
                buy_streak = 0
                sell_streak = 0
        if t >= 4 and sell_streak == setup_n:
            active = False
            counted = 0
        if t >= 4 and buy_streak == setup_n:
            active = True
            counted = 0
        if active and t >= 2 and close[t] <= close[t - 2]:
            counted += 1
            if counted == need:
                if trend is None or _above_trend(close, trend, t):
                    out[t] = True
                active = False
                counted = 0
    return out


def _di_cross(bars: DailyBars, p: dict) -> np.ndarray:
    """Long while +DI is above -DI. There is no ADX floor."""
    plus, minus, _adx = adx_wilder(bars.high, bars.low, bars.close, int(p["n"]))
    return _finite_gt(plus, minus)


def _woodie_up(bars: DailyBars, p: dict) -> np.ndarray:
    """Close above yesterday's Woodie pivot and above the trend SMA.

    The pivot is (high + low + 2 * close) / 4 of the prior day.
    """
    pivot = (
        shift_previous(bars.high, 1) + shift_previous(bars.low, 1) + 2.0 * shift_previous(bars.close, 1)
    ) / 4.0
    trend = sma(bars.close, int(p["trend"]))
    return _finite_gt(bars.close, pivot) & _finite_gt(bars.close, trend)


def _fib_hold(bars: DailyBars, p: dict) -> np.ndarray:
    """Close above the 200-day SMA and above the 61.8 percent level of the prior range.

    The level is the prior ``n``-day low plus 0.618 times that range, which
    is a 38.2 percent retracement off the prior high. Today is not inside
    the range. 0.618 stays fixed.
    """
    hi = prior_max(bars.high, int(p["n"]))
    lo = prior_min(bars.low, int(p["n"]))
    level = lo + 0.618 * (hi - lo)
    trend = sma(bars.close, int(p["trend"]))
    return _finite_gt(bars.close, level) & _finite_gt(bars.close, trend)


def _saucer(bars: DailyBars, p: dict) -> np.ndarray:
    """Two falling awesome-oscillator bars below zero, then a higher bar still below zero."""
    ao = awesome_oscillator(bars.high, bars.low, 5, 34)
    prev = shift_previous(ao, 1)
    prev2 = shift_previous(ao, 2)
    trend = sma(bars.close, int(p["trend"]))
    with np.errstate(invalid="ignore"):
        shape = (
            np.isfinite(ao) & np.isfinite(prev) & np.isfinite(prev2)
            & (prev2 < 0.0) & (prev < 0.0) & (ao < 0.0)
            & (prev < prev2) & (ao > prev)
        )
        return shape & np.isfinite(trend) & (bars.close > trend)


def _gator_wake(bars: DailyBars, p: dict) -> np.ndarray:
    """Both absolute gaps contracted yesterday and expanded today, with the lips above the jaw.

    Teeth stay length 8, displaced 5. Lips are displaced 3 and the jaw 8.
    """
    lips = shift_previous(smma(bars.close, int(p["lips"])), 3)
    teeth = shift_previous(smma(bars.close, 8), 5)
    jaw = shift_previous(smma(bars.close, int(p["jaw"])), 8)
    jaw_teeth = np.abs(jaw - teeth)
    teeth_lips = np.abs(teeth - lips)
    jt1 = shift_previous(jaw_teeth, 1)
    jt2 = shift_previous(jaw_teeth, 2)
    tl1 = shift_previous(teeth_lips, 1)
    tl2 = shift_previous(teeth_lips, 2)
    contracted = _finite_gt(jt2, jt1) & _finite_gt(tl2, tl1)
    expanded = _finite_gt(jaw_teeth, jt1) & _finite_gt(teeth_lips, tl1)
    return contracted & expanded & _finite_gt(lips, jaw)


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
    if key == "elder_impulse":
        # EMA slope at n. Histogram slope of the fixed 12/26/9 MACD at index 34.
        return _defined_from(n, max(int(params["n"]), (26 - 1) + 9))
    if key == "macd_hist":
        # Slow EMA at 25. The signal EMA needs ``signal`` finite values, and the
        # slope needs one bar after that seed.
        return _defined_from(n, (26 - 1) + int(params["signal"]))
    if key == "tsi":
        return _defined_from(n, int(params["long"]) + int(params["short"]) - 1)
    if key == "awesome":
        return _defined_from(n, max(int(params["fast"]), int(params["slow"])) - 1)
    if key == "alligator":
        # SMMA finite at length-1, then displaced by Williams' plot shift.
        lips = int(params["lips"]) - 1 + 3
        teeth = 8 - 1 + 5
        jaw = int(params["jaw"]) - 1 + 8
        return _defined_from(n, max(lips, teeth, jaw))
    if key == "bop":
        return _defined_from(n, int(params["n"]) - 1)
    if key == "psych":
        return _defined_from(n, int(params["n"]))
    if key == "chop":
        return _defined_from(n, max(int(params["n"]) - 1, int(params["trend"]) - 1))
    if key == "rwi":
        return _defined_from(n, int(params["n"]))
    if key == "asi":
        # Swing index at index 1. An n-day average of it ends at index n.
        return _defined_from(n, int(params["n"]))
    if key == "darvas":
        # The prior-high window exists at ``look``. The box then needs ``confirm`` days.
        return _defined_from(n, int(params["look"]) + int(params["confirm"]))
    if key == "ichimoku_cloud":
        disp = int(params["base"])
        first = max(int(params["conv"]), int(params["base"]), int(params["span"])) - 1 + disp
        return _defined_from(n, first)
    if key == "td_setup":
        # The four-day comparison is fixed. The count is a threshold on that streak.
        return _defined_from(n, max(4, int(params["trend"]) - 1))
    if key == "rei":
        # The six-day condition is finite at index 6. The sum needs n of those bars.
        return _defined_from(n, int(params["n"]) + 5)
    if key == "linreg":
        return _defined_from(n, int(params["n"]) - 1)
    if key == "mom_mid":
        return _defined_from(n, max(int(params["mid"]), int(params["slow"])))
    if key == "first_half":
        return _defined_from(n, 0)
    if key == "laguerre":
        return _defined_from(n, 1)
    if key == "vidya":
        return _defined_from(n, int(params["n"]))
    if key == "t3":
        return _defined_from(n, 6 * (int(params["n"]) - 1))
    if key == "rmi":
        return _defined_from(n, int(params["mom"]) + int(params["n"]) - 1)
    if key == "double7":
        return _defined_from(n, max(int(params["n"]) - 1, int(params["trend"]) - 1))
    if key == "mcginley":
        return _defined_from(n, int(params["n"]) - 1)
    if key == "stoch_pop":
        # %K exists at k-1. The cross needs the previous %K, so index k.
        return _defined_from(n, int(params["k"]))
    if key == "outside":
        return _defined_from(n, int(params["n"]))
    if key == "three_dn":
        return _defined_from(n, max(int(params["n"]), int(params["trend"]) - 1, int(params["exit_ma"]) - 1))
    if key == "demarker":
        # The one-day high/low comparison starts at index 1. n of them end at index n.
        return _defined_from(n, int(params["n"]))
    if key == "td_cd":
        # The four-day setup comparison is fixed. The countdown reads the low two bars back.
        return _defined_from(n, 4)
    if key == "trix_sig":
        # TRIX(15) is finite at index 43. The signal average needs ``signal`` of those values.
        return _defined_from(n, 3 * (15 - 1) + int(params["signal"]))
    if key == "stoch_slow":
        # %K at k-1, two averages of length d, then the fixed 3-day signal line.
        return _defined_from(n, int(params["k"]) - 1 + 2 * (int(params["d"]) - 1) + 2)
    if key == "zlema":
        lag = (int(params["n"]) - 1) // 2
        return _defined_from(n, lag + int(params["n"]) - 1)
    if key == "frama":
        return _defined_from(n, int(params["n"]) - 1)
    if key == "ssmooth":
        return _defined_from(n, 2)
    if key == "cyber":
        # The book formula replaces the seed at currentbar 7, which is index 6.
        return _defined_from(n, 6)
    if key == "lfilter":
        return _defined_from(n, 1)
    if key == "alma":
        return _defined_from(n, int(params["n"]) - 1)
    if key == "tlbreak":
        # The nth line can print by index n. The reversal that breaks it is the next bar.
        return _defined_from(n, int(params["n"]) + 1)
    if key in ("engulf", "pierce", "harami"):
        return _defined_from(n, max(1, int(params["trend"]) - 1))
    if key == "soldiers":
        # Three soldiers, each opening inside the prior body, so the bar before them exists.
        return _defined_from(n, max(3, int(params["trend"]) - 1))
    if key == "oops":
        return _defined_from(n, int(params["n"]))
    if key == "vratio":
        return _defined_from(n, max(int(params["win"]), int(params["trend"]) - 1))
    if key == "vhf":
        # VHF is finite at n. A 100-day median needs 99 further bars.
        return _defined_from(n, max(int(params["n"]) + 99, int(params["trend"]) - 1))
    if key == "accel":
        # Awesome oscillator (5, 34) is finite at index 33. The average needs ``smooth`` of those.
        return _defined_from(n, 33 + int(params["smooth"]) - 1)
    if key == "gator":
        lips = int(params["lips"]) - 1 + 3
        teeth = 8 - 1 + 5
        jaw = int(params["jaw"]) - 1 + 8
        return _defined_from(n, max(lips, teeth, jaw) + 1)
    if key == "camarilla":
        return _defined_from(n, max(1, int(params["trend"]) - 1))
    if key == "regline":
        return _defined_from(n, int(params["n"]) - 1)
    if key == "gk":
        # The n-day mean is finite at n-1. A 100-day median needs 99 further bars.
        return _defined_from(n, max(int(params["n"]) + 98, int(params["trend"]) - 1))
    if key == "expiry":
        return _defined_from(n, 0)
    if key == "smi":
        # Range ends at n-1. Each smooth of 3 then needs two more bars.
        return _defined_from(n, int(params["n"]) + 3)
    if key == "tsi_sig":
        # TSI(25, 13) is finite at index 37. The signal EMA needs ``signal`` of those values.
        return _defined_from(n, 25 + 13 - 1 + int(params["signal"]) - 1)
    if key == "cmo_sig":
        # CMO(14) is finite at index 14. The signal average needs ``signal`` of those values.
        return _defined_from(n, 14 + int(params["signal"]) - 1)
    if key in ("mama", "ebsw"):
        return _defined_from(n, HILBERT_READY)
    if key == "itrend":
        return _defined_from(n, 6)
    if key == "eratio":
        return _defined_from(n, max(int(params["n"]), int(params["trend"]) - 1))
    if key in ("rsquared", "seband"):
        return _defined_from(n, int(params["n"]) - 1)
    if key in ("kagi", "renko"):
        return _defined_from(n, 1)
    if key == "morning":
        return _defined_from(n, max(2, int(params["trend"]) - 1))
    if key == "hammer":
        return _defined_from(n, max(2, int(params["trend"]) - 1))
    if key == "rising3":
        return _defined_from(n, max(4, int(params["trend"]) - 1))
    if key == "three_out":
        return _defined_from(n, max(2, int(params["trend"]) - 1))
    if key == "twobar":
        return _defined_from(n, int(params["n"]))
    if key == "td_combo":
        # The four-day setup comparison is fixed. The combo reads the close two bars back.
        return _defined_from(n, max(4, int(params["trend"]) - 1))
    if key == "di_cross":
        # +DI and -DI exist at the Wilder seed, index n. ADX is not required.
        return _defined_from(n, int(params["n"]))
    if key == "woodie":
        return _defined_from(n, max(1, int(params["trend"]) - 1))
    if key == "fibhold":
        return _defined_from(n, max(int(params["n"]), int(params["trend"]) - 1))
    if key == "saucer":
        # Awesome oscillator (5, 34) is finite at index 33. The saucer reads two bars back.
        return _defined_from(n, max(35, int(params["trend"]) - 1))
    if key == "gator_wake":
        lips = int(params["lips"]) - 1 + 3
        teeth = 8 - 1 + 5
        jaw = int(params["jaw"]) - 1 + 8
        # The contraction compares the gap with the gap two days earlier.
        return _defined_from(n, max(lips, teeth, jaw) + 2)
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
    # Batch 5 (2026-11-01). Appended after the batch-4 twenty-six. Claims walk
    # this tuple in order, so these names are leased only once the names
    # above are tested or on the book. Do not insert above ``weekend``.
    LibraryStrategy(
        key="elder_impulse",
        title="Elder impulse: EMA(13) and MACD histogram both rising",
        family="trend",
        evaluation="daily",
        params=(Param("n", 13, (10, 16)),),
        signal=_elder_impulse,
        citation=Citation(
            authors="Elder",
            year=2002,
            title="Come Into My Trading Room",
            venue="Practitioner book (impulse system)",
            peer_reviewed=False,
            tested_rule=(
                "The impulse system is green when the 13-day EMA is rising and the "
                "MACD histogram is rising. It is red when both are falling. Elder uses "
                "it as a permission filter, not as a fitted bitcoin rule."
            ),
            implemented_rule=(
                "Long while the 13-day EMA is above its previous value and the "
                "MACD(12, 26, 9) histogram is above its previous value."
            ),
            differences=(
                "lib.elder_ray is bear power against the same 13-day EMA. lib.macd is "
                "the signal-line cross, and lib.macd_hist is the histogram slope with "
                "no EMA filter. The 12/26/9 lengths stay fixed. Long or flat, no red "
                "short, next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="macd_hist",
        title="MACD histogram rising",
        family="momentum",
        evaluation="daily",
        params=(Param("signal", 9, (7, 12)),),
        signal=_macd_hist,
        citation=Citation(
            authors="Appel",
            year=1979,
            title="Moving Average Convergence-Divergence (MACD)",
            venue="Practitioner indicator; the histogram is the line minus its signal average",
            peer_reviewed=False,
            tested_rule=(
                "The MACD histogram is the 12/26 line minus its 9-period signal average. "
                "A rising histogram is the early reading, before the line crosses the signal."
            ),
            implemented_rule=(
                "Long while the daily MACD(12, 26) histogram is above its previous value. "
                "The signal length is the only grid axis. 12 and 26 stay fixed."
            ),
            differences=(
                "lib.macd is long while the line is above the signal. Its signal "
                "neighbours are 7 and 11, for that cross. lib.ema12x26 is the zero line. "
                "This name is the slope of the histogram. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="tsi",
        title="True strength index (25, 13) above zero",
        family="momentum",
        evaluation="daily",
        params=(
            Param("long", 25, (20, 30)),
            Param("short", 13, (10, 16)),
        ),
        signal=_tsi_up,
        citation=Citation(
            authors="Blau",
            year=1991,
            title="True Strength Index",
            venue="Technical Analysis of Stocks & Commodities (November 1991)",
            peer_reviewed=False,
            tested_rule=(
                "Double-smoothed one-day momentum, divided by double-smoothed absolute "
                "momentum. The published lengths are 25 then 13. Above zero the smoothed "
                "up-moves dominate."
            ),
            implemented_rule="Long while TSI(25, 13) is above zero.",
            differences=(
                "lib.trix is the one-day change of a triple EMA of price. lib.cmo is a "
                "raw sum, not a double smooth. No signal-line cross. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="awesome",
        title="Awesome oscillator above zero",
        family="momentum",
        evaluation="daily",
        params=(
            Param("fast", 5, (3, 8)),
            Param("slow", 34, (28, 40)),
        ),
        signal=_awesome_up,
        citation=Citation(
            authors="Williams",
            year=1995,
            title="Trading Chaos",
            venue="Practitioner book (awesome oscillator)",
            peer_reviewed=False,
            tested_rule=(
                "The awesome oscillator is the 5-period SMA of the median price minus "
                "the 34-period SMA of the median price. Median price is (high + low) / 2. "
                "Above zero the short average is above the long one."
            ),
            implemented_rule=(
                "Long while SMA(5) of the median price is above SMA(34) of the median price."
            ),
            differences=(
                "lib.fractal is the five-bar break from the same book. lib.macd is an "
                "EMA of the close, not an SMA of the median. The saucer and the twin "
                "peaks are not this zero line. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="alligator",
        title="Alligator lips above teeth above jaw",
        family="trend",
        evaluation="daily",
        params=(
            Param("lips", 5, (3, 7)),
            Param("jaw", 13, (10, 16)),
        ),
        signal=_alligator,
        citation=Citation(
            authors="Williams",
            year=1995,
            title="Trading Chaos",
            venue="Practitioner book (alligator)",
            peer_reviewed=False,
            tested_rule=(
                "Jaw is a 13-period smoothed average displaced 8 bars, teeth an 8-period "
                "smoothed average displaced 5, lips a 5-period smoothed average displaced "
                "3. The alligator is awake when the lines separate in order, lips above "
                "teeth above jaw."
            ),
            implemented_rule=(
                "Long while the lips SMMA, read 3 bars after it was computed, is above "
                "the teeth (8, displaced 5) and the teeth are above the jaw. ``lips`` and "
                "``jaw`` replace only those two lengths. Teeth stay at 8."
            ),
            differences=(
                "The chart displacement is applied backward: the value on day t was "
                "computed on an earlier day. lib.awesome is the median-price difference "
                "and lib.fractal is the five-bar break. Not an EMA cross. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="bop",
        title="Balance of power above zero",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 14, (10, 20)),),
        signal=_bop_up,
        citation=Citation(
            authors="Livshin",
            year=2001,
            title="Balance of Power",
            venue="Technical Analysis of Stocks & Commodities (August 2001)",
            peer_reviewed=False,
            tested_rule=(
                "Livshin's balance of market power compares the open, high, low, and "
                "close. The expression reduces to (close - open) / (high - low). "
                "Positive means the close finished above the open. Chart use smooths it."
            ),
            implemented_rule=(
                "Long while the 14-day SMA of (close - open) / (high - low) is above zero. "
                "A zero-range bar contributes 0."
            ),
            differences=(
                "lib.qstick averages the body and does not divide by the range. lib.ibs "
                "is (close - low) / (high - low) for one day, with a 200-day filter. "
                "The later name balance of market power is this same ratio. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="psych",
        title="Psychological line above 50",
        family="momentum",
        evaluation="daily",
        params=(
            Param("n", 12, (8, 18)),
            Param("thr", 50, (45, 55), unit="percent"),
        ),
        signal=_psych_up,
        citation=Citation(
            authors="Achelis",
            year=1995,
            title="Technical Analysis from A to Z",
            venue="Practitioner reference (psychological line)",
            peer_reviewed=False,
            tested_rule=(
                "The psychological line is the percent of bars in the window that closed "
                "up. The usual length is 12. Above 50, up closes outnumber down closes."
            ),
            implemented_rule=(
                "Long while at least the threshold percent of the last 12 closes are "
                "strictly above the prior close. A tie is not an up close."
            ),
            differences=(
                "Not RSI: the size of the move does not enter. Not lib.cmo. 12 and 50 "
                "are the recorded defaults, not values fit on this tape. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="chop",
        title="Choppiness index below 38.2 above the 200-day SMA",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 14, (10, 20)),
            Param("trend", 200, (150, 250)),
        ),
        signal=_chop,
        citation=Citation(
            authors="Dreiss",
            year=1992,
            title="The Choppiness Index",
            venue="Technical Analysis of Stocks & Commodities",
            peer_reviewed=False,
            tested_rule=(
                "Choppiness is 100 times the log of the n-bar true-range sum divided by "
                "the n-bar high-low range, over log(n). Below 38.2 the path is trending. "
                "Above 61.8 it is chopping. 14 is the usual length."
            ),
            implemented_rule=(
                "Long while CI(14) is below 38.2 and the close is above the 200-day SMA."
            ),
            differences=(
                "38.2 stays fixed. The index has no direction, so the SMA supplies it. "
                "lib.adx14 is Wilder's directional index, not this range ratio. A zero "
                "range is missing, not a low reading. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="rwi",
        title="Random walk index of the highs above 1",
        family="trend",
        evaluation="daily",
        params=(Param("n", 14, (8, 20)),),
        signal=_rwi_up,
        citation=Citation(
            authors="Poulos",
            year=1991,
            title="Of Trends and Random Walks",
            venue="Technical Analysis of Stocks & Commodities (February 1991)",
            peer_reviewed=False,
            tested_rule=(
                "The random walk index divides an i-day high-low range by ATR(i) times "
                "the square root of i, and keeps the maximum over i from 2 to n. The "
                "highs' index above 1, and above the lows' index, is the uptrend reading."
            ),
            implemented_rule=(
                "Long while RWI high(14) is above 1 and above RWI low(14)."
            ),
            differences=(
                "Not lib.adx14 and not lib.vortex. The short RWI in later notes uses a "
                "shorter window; 8 is the neighbour, not a second key. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="asi",
        title="Accumulative swing index rising",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 1, (2, 5)),),
        signal=_asi_up,
        citation=Citation(
            authors="Wilder",
            year=1978,
            title="New Concepts in Technical Trading Systems",
            venue="Practitioner book (swing index)",
            peer_reviewed=False,
            tested_rule=(
                "The swing index prices the day's open, high, low, and close against "
                "the prior bar, scaled by a limit move. The accumulative swing index "
                "is the running sum. A rising ASI is the upswing."
            ),
            implemented_rule=(
                "Long while the swing index is positive. n = 1 is that one-bar slope. "
                "Neighbours average the swing index over 2 or 5 days. The limit move "
                "is half the previous close."
            ),
            differences=(
                "BTC has no futures limit. Half the previous close is a positive scale "
                "factor and does not change the sign, so it is not a grid axis. "
                "lib.wilder_vol is a band around yesterday's close, not this index. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="darvas",
        title="Darvas box breakout",
        family="breakout",
        evaluation="daily",
        params=(
            Param("look", 20, (15, 30)),
            Param("confirm", 3, (2, 4)),
        ),
        signal=_darvas,
        citation=Citation(
            authors="Darvas",
            year=1960,
            title="How I Made $2,000,000 in the Stock Market",
            venue="Practitioner book",
            peer_reviewed=False,
            tested_rule=(
                "After a stock makes a high, Darvas draws a box when the next days do "
                "not take that high out. He buys a break of the top of the box and "
                "uses the bottom of the box as the danger point."
            ),
            implemented_rule=(
                "A high that exceeds the prior 20 highs starts a box. The box completes "
                "after 3 later days that do not exceed it. Long on a later close above "
                "that high; flat on a close below the lowest low of the 3 days. A new "
                "high restarts the count. A new box replaces the old one."
            ),
            differences=(
                "He picked stocks and used a stop under the box, often intraday. This "
                "is one asset, close-confirmed, long or flat. The bottom is the "
                "confirming days only, not the high day's low. Not a fixed Donchian "
                "window (lib.donchian20, lib.donchian55). Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="ichimoku_cloud",
        title="Ichimoku cloud color (span A above span B)",
        family="trend",
        evaluation="daily",
        params=(
            Param("conv", 9, (7, 12)),
            Param("base", 26, (22, 30)),
            Param("span", 52, (42, 60)),
        ),
        signal=_ichimoku_cloud,
        citation=Citation(
            authors="Hosoda",
            year=1969,
            title="Ichimoku Kinko Hyo",
            venue="Practitioner; standard parameters 9, 26, and 52",
            peer_reviewed=False,
            tested_rule=(
                "Senkou span A is the average of the conversion and base lines. Senkou "
                "span B is the midpoint of the 52-period range. Both are plotted 26 "
                "periods ahead. Span A above span B is the bullish cloud."
            ),
            implemented_rule=(
                "Long while the span A plotted on that day is above the span B plotted "
                "on that day. The plotted values were computed one base-period earlier."
            ),
            differences=(
                "lib.ichimoku is the close above that same cloud. lib.ichimoku_tk is "
                "the tenkan/kijun cross and does not use the cloud. The parameters "
                "match lib.ichimoku so the predicate is the only change. The forward "
                "plot does not read bars after t minus the base. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="td_setup",
        title="TD sell-setup count above the 200-day SMA",
        family="trend",
        evaluation="daily",
        params=(
            Param("count", 9, (8, 10)),
            Param("trend", 200, (150, 250)),
        ),
        signal=_td_setup,
        citation=Citation(
            authors="DeMark",
            year=1997,
            title="New Market Timing Techniques",
            venue="Practitioner book (TD sequential)",
            peer_reviewed=False,
            tested_rule=(
                "A TD sell setup is nine consecutive closes each strictly above the "
                "close four bars earlier. The buy setup is the same comparison with "
                "the opposite sign. The countdown is a later count and is not part of "
                "the setup."
            ),
            implemented_rule=(
                "Long while the close is above the 200-day SMA and the running count "
                "of closes above the close four days earlier is at least 9. The count "
                "resets when a close fails that comparison."
            ),
            differences=(
                "DeMark treats a completed sell setup as exhaustion, often with a "
                "short. This keeps the long only, and only on the long side of the "
                "200-day SMA, for as long as the streak holds. The four-day gap stays "
                "fixed. The countdown is not implemented. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="rei",
        title="Range expansion index above zero",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 5, (4, 8)),),
        signal=_rei_up,
        citation=Citation(
            authors="DeMark",
            year=1994,
            title="The New Science of Technical Analysis",
            venue="Practitioner book (range expansion index)",
            peer_reviewed=False,
            tested_rule=(
                "The range expansion index compares each bar's high and low with the "
                "high and low two bars earlier, and drops the bar unless it overlaps "
                "the range of the bars five and six days back. The ratio of the signed "
                "sum to the absolute sum is the oscillator. Above zero the ranges expanded up."
            ),
            implemented_rule="Long while REI(5) is above zero.",
            differences=(
                "Not RSI and not lib.cmo: the inputs are highs and lows, and bars that "
                "fail the overlap test add zero. A zero denominator is 0, not a missing "
                "lookback. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="linreg",
        title="20-day linear regression slope positive",
        family="trend",
        evaluation="daily",
        params=(Param("n", 20, (14, 30)),),
        signal=_linreg_up,
        citation=Citation(
            authors="Achelis",
            year=1995,
            title="Technical Analysis from A to Z",
            venue="Practitioner reference (linear regression slope)",
            peer_reviewed=False,
            tested_rule=(
                "The linear regression slope is the least-squares slope of price "
                "against time over the window. A positive slope means the fitted line "
                "rises toward the latest bar."
            ),
            implemented_rule="Long while the slope of the last 20 closes is positive.",
            differences=(
                "Not a moving-average cross and not the sign of the n-day return. The "
                "endpoint can be up while the fitted slope is down. The time index "
                "puts the oldest close at zero. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="mom_mid",
        title="Intermediate momentum, 12 months to 6 months ago",
        family="momentum",
        evaluation="daily",
        params=(
            Param("mid", 126, (105, 147)),
            Param("slow", 252, (210, 294)),
        ),
        signal=_mom_mid,
        citation=Citation(
            authors="Novy-Marx",
            year=2012,
            title="Is Momentum Really Momentum?",
            venue="Journal of Financial Economics 103(3), 429-453",
            peer_reviewed=True,
            link="https://doi.org/10.1016/j.jfineco.2011.05.003",
            tested_rule=(
                "US stocks, 1926-2010. The return from 12 months ago to 7 months ago "
                "predicts the cross-section. The most recent six months add little. "
                "He is not reporting a one-asset timing backtest."
            ),
            implemented_rule=(
                "Long when the close 126 days ago is above the close 252 days ago. "
                "Flat otherwise. The recent half-year does not enter the sign."
            ),
            differences=(
                "One asset, long or flat, not a winner-minus-loser book. His "
                "intermediate window ends about seven months ago; 126 days is the "
                "six-month mark pre-registered for this tape. lib.tsmom12_1 skips "
                "only the latest month (21 days) and still uses the close 21 days ago. "
                "Daily closes, not month-end excess returns. Next-open fills, BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="first_half",
        title="Long the first half of the UTC month",
        family="seasonality",
        evaluation="daily",
        params=(Param("through", 15, (10, 20), unit="day"),),
        signal=_first_half,
        citation=Citation(
            authors="Ariel",
            year=1987,
            title="A Monthly Effect in Stock Returns",
            venue="Journal of Financial Economics 18(1), 161-174",
            peer_reviewed=True,
            link="https://doi.org/10.1016/0304-405X(87)90066-3",
            tested_rule=(
                "US stocks, 1963-1981. The average return over the first half of the "
                "month is positive and the second half is not. He measured returns. "
                "He did not publish an all-in timing backtest."
            ),
            implemented_rule=(
                "Long when tomorrow's UTC calendar day is between 1 and 15 inclusive. "
                "Flat from the 16th through month-end. ``through`` 10 and 20 are the "
                "neighbours."
            ),
            differences=(
                "His split is trading days, and the last day of the prior month is "
                "often in the strong half. This rule uses calendar days 1 through 15 "
                "and leaves that last day to lib.tom, which is only four days. "
                "Tomorrow's date is known at today's close. Next-open fills, BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="laguerre",
        title="Laguerre RSI above 0.5",
        family="momentum",
        evaluation="daily",
        params=(
            Param("gamma", 50, (40, 60), unit="hundredths"),
            Param("thr", 50, (40, 60), unit="hundredths"),
        ),
        signal=_laguerre_up,
        citation=Citation(
            authors="Ehlers",
            year=2004,
            title="Cybernetic Analysis for Stocks and Futures",
            venue="Practitioner book (Laguerre RSI)",
            peer_reviewed=False,
            tested_rule=(
                "Four Laguerre poles with gamma 0.5, then the fraction of the three "
                "adjacent differences that are up. The result sits between 0 and 1. "
                "Above 0.5 the short transform is rising through the poles."
            ),
            implemented_rule=(
                "Long while the Laguerre RSI at gamma 0.50 is above 0.50. Gamma and "
                "the threshold are stored as hundredths."
            ),
            differences=(
                "Not Wilder RSI (lib.rsi14) and not lib.fisher. The filter is seeded "
                "with the first close, so the first bar is undefined. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="vidya",
        title="Close above VIDYA(20)",
        family="trend",
        evaluation="daily",
        params=(Param("n", 20, (14, 30)),),
        signal=_vidya_up,
        citation=Citation(
            authors="Chande",
            year=1992,
            title="Adapting Moving Averages to Market Volatility",
            venue="Technical Analysis of Stocks & Commodities (March 1992)",
            peer_reviewed=False,
            tested_rule=(
                "VIDYA adjusts an exponential average by the absolute Chande momentum "
                "oscillator. A larger CMO speeds the average up. The constant is "
                "2 / (n + 1) times |CMO| / 100."
            ),
            implemented_rule=(
                "Long while the close is above VIDYA(20). The CMO window and the "
                "smoothing length are the same n."
            ),
            differences=(
                "lib.kama uses the efficiency ratio, not the CMO. lib.cmo is the "
                "oscillator alone, above zero. Some charts split a CMO length of 9 "
                "from the average length; this name uses one length. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="t3",
        title="Close above Tillson T3(5)",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 5, (3, 8)),
            Param("vf", 70, (60, 80), unit="hundredths"),
        ),
        signal=_t3_up,
        citation=Citation(
            authors="Tillson",
            year=1998,
            title="Smoothing Techniques for More Accurate Signals",
            venue="Technical Analysis of Stocks & Commodities (January 1998)",
            peer_reviewed=False,
            tested_rule=(
                "T3 is a triple generalized double EMA. Six EMAs and a volume factor "
                "of 0.7 are the published compromise. The factor is a constant, not "
                "traded volume. Price above T3 is the trend reading."
            ),
            implemented_rule=(
                "Long while the close is above T3(5) with the volume factor at 0.70. "
                "The factor is stored as hundredths."
            ),
            differences=(
                "lib.dema is one double EMA and is traded on its own slope. lib.hma "
                "is the Hull average. This book has no volume series; the factor is "
                "Tillson's constant. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="rmi",
        title="Relative momentum index above 50",
        family="momentum",
        evaluation="daily",
        params=(
            Param("mom", 5, (3, 8)),
            Param("n", 14, (10, 20)),
            Param("thr", 50, (45, 55), unit="RMI"),
        ),
        signal=_rmi_up,
        citation=Citation(
            authors="Altman",
            year=1993,
            title="Relative Momentum Index",
            venue="Technical Analysis of Stocks & Commodities",
            peer_reviewed=False,
            tested_rule=(
                "The relative momentum index is Wilder's RSI applied to an n-day "
                "momentum instead of a one-day change. Altman's illustration uses a "
                "momentum of 5 and a length near 14. Above 50 the smoothed up-momentum dominates."
            ),
            implemented_rule="Long while RMI of 5-day momentum, smoothed 14, is above 50.",
            differences=(
                "lib.rsi14 is the one-day RSI. lib.cmo does not use Wilder smoothing. "
                "The momentum length and the smooth are separate axes. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="double7",
        title="Connors double seven above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("n", 7, (5, 9)),
            Param("trend", 200, (150, 250)),
        ),
        signal=_double7,
        citation=Citation(
            authors="Connors & Alvarez",
            year=2008,
            title="Short Term Trading Strategies That Work",
            venue="Practitioner book (TradingMarkets)",
            peer_reviewed=False,
            tested_rule=(
                "The double seven buys the S&P when it is above the 200-day average "
                "and the close is the lowest close of the last seven days, and sells "
                "when the close is the highest close of the last seven days."
            ),
            implemented_rule=(
                "Long after a close above the 200-day SMA that is the lowest close of "
                "the last 7 days. Flat after a close that is the highest close of the "
                "last 7 days."
            ),
            differences=(
                "Not lib.rsi2 and not lib.rsi2_cum: there is no RSI in this rule. "
                "Not a Donchian breakout, which buys the high. A tie for the low "
                "counts as the low. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="mcginley",
        title="Close above the McGinley dynamic (20)",
        family="trend",
        evaluation="daily",
        params=(Param("n", 20, (14, 30)),),
        signal=_mcginley_up,
        citation=Citation(
            authors="McGinley",
            year=1997,
            title="McGinley Dynamic",
            venue="Journal of Technical Analysis (Market Technicians Association), 1997",
            peer_reviewed=False,
            tested_rule=(
                "The dynamic average adds (close - average) / (n * (close / average) ** 4). "
                "The ratio speeds the average up when price pulls away and slows it "
                "when price is close to the line. An outline appeared in 1991; the "
                "full study is 1997."
            ),
            implemented_rule=(
                "Long while the close is above the dynamic average of length 20, "
                "seeded by the SMA of the first 20 closes."
            ),
            differences=(
                "Not an EMA and not lib.kama. The fourth power and the length are "
                "the published adjustment; the power is not a grid axis. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="stoch_pop",
        title="Stochastic pop: fast %K crosses above 80",
        family="momentum",
        evaluation="daily",
        params=(
            Param("k", 14, (10, 20)),
            Param("pop", 80, (75, 85), unit="percent"),
        ),
        signal=_stoch_pop,
        citation=Citation(
            authors="Bernstein",
            year=2003,
            title="Stochastic Pop",
            venue="Practitioner rule; stated in AIQ Opening Bell, September 2003",
            peer_reviewed=False,
            tested_rule=(
                "Buy when the stochastic rises from below 80 to above 80. Sell when "
                "it crosses back below 80. The point is to buy the overbought push "
                "rather than to fade it."
            ),
            implemented_rule=(
                "Long after fast %K(14) crosses up through 80. Flat after it crosses "
                "back down through 80. A %K that is already above 80 before the first "
                "cross does not enter."
            ),
            differences=(
                "lib.stoch is %K above %D. lib.willr buys %R below -80 and exits "
                "above -20, which is the oversold side of the same scale. Fast %K is "
                "Williams %R plus 100, so 80 here is -20 there, used in the opposite "
                "direction. No %D. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="outside",
        title="Outside-day high break",
        family="breakout",
        evaluation="daily",
        params=(Param("n", 1, (2, 3)),),
        signal=_outside_day,
        citation=Citation(
            authors="Crabel",
            year=1990,
            title="Day Trading with Short Term Price Patterns and Opening Range Breakout",
            venue="Practitioner book",
            peer_reviewed=False,
            tested_rule=(
                "An outside day has a higher high and a lower low than the previous "
                "day. Crabel studies the next day's move off that range. The book is "
                "about the opening range, which this daily engine cannot see."
            ),
            implemented_rule=(
                "An outside day takes out the prior n bars' high and low. n = 1 is "
                "the classic one-bar outside day. Long on a later close above that "
                "day's high. A close through its low cancels the setup. The long is "
                "that one close."
            ),
            differences=(
                "lib.inside_day is the opposite range test. The opening-range fill "
                "is not expressible with one decision per UTC day. Neighbours have "
                "to engulf the prior 2 or 3 bars. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="three_dn",
        title="Three lower closes above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("n", 3, (2, 4)),
            Param("trend", 200, (150, 250)),
            Param("exit_ma", 5, (4, 6)),
        ),
        signal=_three_dn,
        citation=Citation(
            authors="Connors & Alvarez",
            year=2008,
            title="Short Term Trading Strategies That Work",
            venue="Practitioner book (TradingMarkets)",
            peer_reviewed=False,
            tested_rule=(
                "Short-term pullbacks in the direction of the 200-day average: a run "
                "of lower closes while price stays above that average, exited on a "
                "close back above a short average. The RSI(2) rules in the same book "
                "are a different entry."
            ),
            implemented_rule=(
                "Long after three consecutive lower closes while the close is above "
                "the 200-day SMA. Flat after a close above the 5-day SMA."
            ),
            differences=(
                "Not lib.rsi2, which needs RSI(2) under a cutoff, and not lib.turtle_soup, "
                "which needs a failed 20-day low. Two and four lower closes are the "
                "neighbours, not extra keys. Next-open fills, BTC."
            ),
        ),
    ),
    # Batch 6 (2026-11-01). Appended after the batch-5 twenty-six. Claims walk
    # this tuple in order, so these names are leased only once the names
    # above are tested or on the book. Do not insert above ``three_dn``.
    LibraryStrategy(
        key="demarker",
        title="DeMarker(14) above one half",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 14, (10, 20)),),
        signal=_demarker_up,
        citation=Citation(
            authors="DeMark",
            year=1994,
            title="The New Science of Technical Analysis",
            venue="Practitioner book (DeMarker indicator)",
            peer_reviewed=False,
            tested_rule=(
                "DeMax is today's high minus yesterday's high when that difference "
                "is positive. DeMin is yesterday's low minus today's low when that "
                "difference is positive. DeMarker is the smoothed DeMax divided by "
                "the sum of the smoothed DeMax and DeMin. Above 0.5 the highs are "
                "extending more than the lows."
            ),
            implemented_rule="Long while DeMarker(14) is above 0.5.",
            differences=(
                "The comparisons are highs and lows against the prior bar. lib.rsi14 "
                "is close-to-close. lib.rei compares closes with highs and lows two "
                "and three bars back. A window with no extension is 0.5, which is not "
                "long. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="td_cd",
        title="TD buy countdown completes",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("setup", 9, (8, 10), unit="bars"),
            Param("count", 13, (12, 14), unit="bars"),
        ),
        signal=_td_countdown,
        citation=Citation(
            authors="DeMark",
            year=1997,
            title="New Market Timing Techniques",
            venue="Practitioner book (TD Sequential countdown)",
            peer_reviewed=False,
            tested_rule=(
                "A buy setup is nine closes each less than the close four bars "
                "earlier. The buy countdown then counts thirteen closes each at or "
                "below the low two bars earlier. Bar 13 also needs its low at or "
                "below the close of countdown bar 8. The completed countdown is the "
                "buy."
            ),
            implemented_rule=(
                "Long on the close that completes a 13-bar buy countdown after a "
                "9-bar buy setup."
            ),
            differences=(
                "lib.td_setup is the other direction: closes above the close four "
                "days earlier, held while the streak lasts. This name is the "
                "exhaustion countdown, and the long is the completion bar only. An "
                "opposing setup of the same length cancels an unfinished count. A "
                "new buy setup restarts it. The four-day and two-day gaps stay fixed. "
                "A 200-day SMA filter was tried and blocked every completion on the "
                "2017-2026 tape, because a buy countdown finishes a decline. That "
                "filter is not in the cited buy, so it is not applied. Next-open "
                "fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="trix_sig",
        title="TRIX(15) above its 9-day average",
        family="momentum",
        evaluation="daily",
        params=(Param("signal", 9, (7, 12)),),
        signal=_trix_signal,
        citation=Citation(
            authors="Hutson",
            year=1983,
            title="Good TRIX",
            venue="Technical Analysis of Stocks & Commodities, 1983",
            peer_reviewed=False,
            tested_rule=(
                "TRIX is the one-day percent change of a triple exponential average. "
                "A signal line is a short average of TRIX. TRIX above that average "
                "turns up before TRIX itself crosses zero."
            ),
            implemented_rule=(
                "Long while TRIX(15) is above its 9-day SMA. The triple-EMA length "
                "stays 15. The signal length is the only grid axis."
            ),
            differences=(
                "lib.trix is long while TRIX is positive. Its length neighbours are "
                "12 and 18, for that zero line. This name keeps 15 and reads the "
                "signal average. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="stoch_slow",
        title="Slow stochastic %D above its 3-day average",
        family="momentum",
        evaluation="daily",
        params=(
            Param("k", 14, (10, 20)),
            Param("d", 3, (2, 5)),
        ),
        signal=_stoch_slow,
        citation=Citation(
            authors="Lane",
            year=1984,
            title="Lane's Stochastics",
            venue="Technical Analysis of Stocks & Commodities, May/June 1984",
            peer_reviewed=False,
            tested_rule=(
                "Slow %K is a moving average of fast %K, and slow %D is a moving "
                "average of slow %K. Lane's signal is slow %D against a further "
                "short average of itself. The 14-period lookback and the 3-period "
                "averages are the chart defaults."
            ),
            implemented_rule=(
                "Long while SMA(d) of SMA(d) of fast %K(14) is above the 3-day SMA "
                "of that slow %D. d is 3 at the center."
            ),
            differences=(
                "lib.stoch is fast %K against one average. lib.stoch_pop is fast %K "
                "crossing 80. The further 3-day average on the slow line stays fixed. "
                "A zero-range window is fast %K 50, as in lib.stoch. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="zlema",
        title="Close above the zero-lag EMA(20)",
        family="trend",
        evaluation="daily",
        params=(Param("n", 20, (14, 30)),),
        signal=_zlema_up,
        citation=Citation(
            authors="Ehlers & Way",
            year=2010,
            title="Zero-Lag Data Smoothers",
            venue="Technical Analysis of Stocks & Commodities",
            peer_reviewed=False,
            tested_rule=(
                "A zero-lag EMA removes about (n - 1) / 2 bars of EMA lag by "
                "feeding the average close plus the close-minus-lagged-close. Price "
                "above that average is the trend reading."
            ),
            implemented_rule=(
                "Long while the close is above ZLEMA(20). The lag is (n - 1) // 2."
            ),
            differences=(
                "lib.dema is 2*EMA - EMA(EMA). lib.t3 is six EMAs and a volume factor. "
                "Neither adds the close-minus-lagged-close before a single EMA. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="frama",
        title="Close above the fractal adaptive moving average",
        family="trend",
        evaluation="daily",
        params=(Param("n", 16, (10, 20)),),
        signal=_frama_up,
        citation=Citation(
            authors="Ehlers",
            year=2005,
            title="FRAMA — Fractal Adaptive Moving Average",
            venue="Technical Analysis of Stocks & Commodities",
            peer_reviewed=False,
            tested_rule=(
                "The fractal dimension of the last window sets the EMA constant. "
                "A straight trend has dimension near 1 and a fast constant. A noisy "
                "window has a slower constant. The exp(-4.6 * (D - 1)) map is the "
                "one in the article, clipped at 0.01 and 1."
            ),
            implemented_rule="Long while the close is above FRAMA(16).",
            differences=(
                "lib.kama uses Kaufman's efficiency ratio. lib.vidya uses the Chande "
                "momentum oscillator. The dimension here is computed on closes. The "
                "article's illustration uses the high-low range; the -4.6 map is "
                "unchanged and is not a grid axis. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="ssmooth",
        title="Close above the two-pole super smoother",
        family="trend",
        evaluation="daily",
        params=(Param("n", 10, (7, 14)),),
        signal=_ssmooth_up,
        citation=Citation(
            authors="Ehlers",
            year=2004,
            title="Cybernetic Analysis for Stocks and Futures",
            venue="Practitioner book (two-pole super smoother)",
            peer_reviewed=False,
            tested_rule=(
                "The two-pole super smoother is a Butterworth-style filter. The "
                "coefficients are exp(-sqrt(2) pi / n) and the cosine of the same "
                "angle. It is a price average, not an oscillator."
            ),
            implemented_rule="Long while the close is above the super smoother of length 10.",
            differences=(
                "An ordinary EMA uses 2 / (n + 1) and one state. This filter uses "
                "two states and the two-pole coefficients. The first two bars only "
                "seed the recursion. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="cyber",
        title="Cyber cycle above its one-bar trigger",
        family="momentum",
        evaluation="daily",
        params=(Param("alpha", 7, (5, 10), unit="hundredths"),),
        signal=_cyber_up,
        citation=Citation(
            authors="Ehlers",
            year=2004,
            title="Cybernetic Analysis for Stocks and Futures",
            venue="Practitioner book (cyber cycle)",
            peer_reviewed=False,
            tested_rule=(
                "The cyber cycle is a high-pass of smoothed median price. The "
                "trigger is the cycle's previous value. Cycle above trigger is the "
                "long. The book uses alpha 0.07."
            ),
            implemented_rule=(
                "Long while the cyber cycle of (high + low) / 2 is above its "
                "previous value. Alpha 0.07 is the center. The filter replaces the "
                "seed at the seventh bar."
            ),
            differences=(
                "lib.fisher is a transformed stochastic. lib.laguerre is the "
                "up-move share of four Laguerre poles. The seed second-difference "
                "is not traded: the signal starts when the full filter does. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="lfilter",
        title="Close above the Laguerre filter",
        family="trend",
        evaluation="daily",
        params=(Param("gamma", 50, (40, 60), unit="hundredths"),),
        signal=_lfilter_up,
        citation=Citation(
            authors="Ehlers",
            year=2004,
            title="Cybernetic Analysis for Stocks and Futures",
            venue="Practitioner book (Laguerre filter)",
            peer_reviewed=False,
            tested_rule=(
                "Four Laguerre poles at gamma 0.5, combined as "
                "(L0 + 2 L1 + 2 L2 + L3) / 6. That is a moving average. Price above "
                "it is the trend reading. The Laguerre RSI uses the same poles and "
                "then the share of the up-moves."
            ),
            implemented_rule=(
                "Long while the close is above the Laguerre filter at gamma 0.50."
            ),
            differences=(
                "lib.laguerre is the RSI of these poles, long above 0.5. Same gamma "
                "at the center, different output. Gamma is stored in hundredths. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="alma",
        title="Close above ALMA(9, 0.85, 6)",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 9, (6, 12)),
            Param("offset", 85, (75, 95), unit="hundredths"),
        ),
        signal=_alma_up,
        citation=Citation(
            authors="Legoux & Kouzis-Loukas",
            year=2009,
            title="Arnaud Legoux Moving Average",
            venue="Practitioner; the 2009 ALMA note",
            peer_reviewed=False,
            tested_rule=(
                "ALMA is a Gaussian-weighted average. The offset places the peak "
                "of the window and sigma sets the width. The published short "
                "setting is window 9, offset 0.85, sigma 6. Price above the average "
                "is the trend reading."
            ),
            implemented_rule=(
                "Long while the close is above ALMA(9) with offset 0.85 and sigma 6."
            ),
            differences=(
                "lib.hma is a weighted combination of WMAs, and it is read from the "
                "slope of the average rather than from price crossing it. Sigma 6 "
                "stays fixed. Offset is stored in hundredths. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="tlbreak",
        title="Three-line break turns white",
        family="breakout",
        evaluation="daily",
        params=(Param("n", 3, (2, 4), unit="lines"),),
        signal=_tlbreak,
        citation=Citation(
            authors="Nison",
            year=1994,
            title="Beyond Candlesticks",
            venue="Practitioner book (three-line break)",
            peer_reviewed=False,
            tested_rule=(
                "A three-line break chart draws a new line only when the close "
                "breaks the prior line, or, for a reversal, the extreme of the "
                "prior three lines. A new white line through that extreme is the "
                "bullish turn. The chart is built from closes, in order."
            ),
            implemented_rule=(
                "Long on the close that prints a white reversal through the high "
                "of the prior three lines. Flat on a black reversal through the "
                "low of the prior three. Continuations do not enter."
            ),
            differences=(
                "The 1991 candlestick book is the candle patterns. The line chart "
                "is this 1994 method. lib.outside is a high-and-low engulfing bar "
                "and a later break of that high. Two lines and four lines are the "
                "neighbours. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="engulf",
        title="Bullish engulfing above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(Param("trend", 200, (150, 250)),),
        signal=_engulf,
        citation=Citation(
            authors="Nison",
            year=1991,
            title="Japanese Candlestick Charting Techniques",
            venue="Practitioner book (bullish engulfing)",
            peer_reviewed=False,
            tested_rule=(
                "A bullish engulfing is a white body that covers the prior black "
                "body. Shadows are not part of the test. It is a one-bar reversal "
                "pattern after a down candle."
            ),
            implemented_rule=(
                "Long on a close whose body engulfs the prior down body, and only "
                "when that close is above the 200-day SMA."
            ),
            differences=(
                "lib.outside waits for a later close through the high of a bar that "
                "took out both the prior high and the prior low. lib.bop is a "
                "smoothed (close - open) / range, not a two-candle body test. The "
                "white body has to be strictly larger. The long is that close only. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="pierce",
        title="Piercing line above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(Param("trend", 200, (150, 250)),),
        signal=_pierce,
        citation=Citation(
            authors="Nison",
            year=1991,
            title="Japanese Candlestick Charting Techniques",
            venue="Practitioner book (piercing pattern)",
            peer_reviewed=False,
            tested_rule=(
                "A piercing line opens below the prior low and closes above the "
                "midpoint of the prior black body, without covering that whole body."
            ),
            implemented_rule=(
                "Long on a white close that trades below the prior low, finishes "
                "above the midpoint of the prior black body, stays below the prior "
                "open, and is above the 200-day SMA."
            ),
            differences=(
                "The book puts the break on the open. Across 2017-2026 the daily "
                "open never printed below the prior low, because the open is the "
                "prior close. The low is that same break, known at the close, and "
                "the close still has to pierce the midpoint without engulfing. A "
                "close that also clears the prior open is lib.engulf. The long is "
                "that close only. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="soldiers",
        title="Three white soldiers above the 200-day SMA",
        family="trend",
        evaluation="daily",
        params=(Param("trend", 200, (150, 250)),),
        signal=_soldiers,
        citation=Citation(
            authors="Nison",
            year=1991,
            title="Japanese Candlestick Charting Techniques",
            venue="Practitioner book (three white soldiers)",
            peer_reviewed=False,
            tested_rule=(
                "Three white soldiers are three rising white bodies. Each opens "
                "inside the prior body and closes near its own high."
            ),
            implemented_rule=(
                "Long on the third close when each of the three opens within the "
                "prior real body, including the edge, each closes up, each upper "
                "shadow is no larger than its body, and the close is above the "
                "200-day SMA."
            ),
            differences=(
                "lib.three_dn buys a run of lower closes. The upper-shadow test "
                "stays fixed. The first soldier also has to open within the body "
                "before the three. A strict interior missed every chain on this "
                "tape, because the open prints on the prior close, the top of a "
                "white body. The body is a closed interval, and a doji does not "
                "count. The long is the third close only. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="oops",
        title="Williams oops: failed break of yesterday's low",
        family="mean_reversion",
        evaluation="daily",
        params=(Param("n", 1, (2, 3)),),
        signal=_oops,
        citation=Citation(
            authors="Williams",
            year=1999,
            title="Long-Term Secrets to Short-Term Trading",
            venue="Practitioner book (oops reversal)",
            peer_reviewed=False,
            tested_rule=(
                "The oops pattern is a break of yesterday's low that fails. Price "
                "trades back through that low and traps the breakdown. The book "
                "enters on the intraday cross after a gap open."
            ),
            implemented_rule=(
                "Long on a close that is back above the prior n-day low after the "
                "low of the day took that level out. n is 1 at the center, so the "
                "level is yesterday's low."
            ),
            differences=(
                "On a daily bar the path inside the session is not on the tape. "
                "The observable form is the low through the prior low and the close "
                "back above it. lib.turtle_soup needs a 20-day low to fail. "
                "lib.outside also takes out the prior high and then waits for a "
                "later break. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="vratio",
        title="Variance ratio above 1 in an uptrend",
        family="trend",
        evaluation="daily",
        params=(
            Param("q", 16, (8, 32), unit="days"),
            Param("win", 128, (96, 192), unit="days"),
            Param("trend", 200, (150, 250)),
        ),
        signal=_vratio_up,
        citation=Citation(
            authors="Lo & MacKinlay",
            year=1988,
            title="Stock Market Prices Do Not Follow Random Walks: Evidence from a Simple Specification Test",
            venue="The Review of Financial Studies 1(1), 41-66",
            peer_reviewed=True,
            link="https://doi.org/10.1093/rfs/1.1.41",
            tested_rule=(
                "US stocks, 1962-1985. The variance ratio of overlapping q-period "
                "returns to q times the one-period variance is above 1 when returns "
                "are positively autocorrelated. They test the random-walk hypothesis. "
                "They do not publish a one-asset timing rule."
            ),
            implemented_rule=(
                "Long while the variance ratio of daily log returns at horizon 16, "
                "measured on the last 128 one-day returns, is above 1 and the close "
                "is above the 200-day SMA."
            ),
            differences=(
                "Population variances, no finite-sample bias correction. The horizon "
                "fits inside the window. lib.tsmom30d is the sign of one return. "
                "lib.linreg is the sign of a fitted slope. One asset, long or flat, "
                "next-open fills, BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="vhf",
        title="Vertical horizontal filter above its median",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 28, (21, 35)),
            Param("trend", 200, (150, 250)),
        ),
        signal=_vhf_up,
        citation=Citation(
            authors="White",
            year=1991,
            title="The Vertical Horizontal Filter",
            venue="Technical Analysis of Stocks & Commodities",
            peer_reviewed=False,
            tested_rule=(
                "VHF divides the range of closes in the window by the sum of the "
                "absolute close-to-close changes. A high reading is a directional "
                "path. A low reading is chop. 28 is the published length."
            ),
            implemented_rule=(
                "Long while VHF(28) is above its own 100-day median and the close "
                "is above the 200-day SMA."
            ),
            differences=(
                "The 100-day median is fixed, the same way lib.ulcer freezes its "
                "median. lib.chop is a log true-range ratio and is long when that "
                "ratio is low. lib.adx14 is Wilder's directional index. A window "
                "with no movement is undefined, not a high reading. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="accel",
        title="Accelerator oscillator above zero",
        family="momentum",
        evaluation="daily",
        params=(Param("smooth", 5, (3, 8)),),
        signal=_accel_up,
        citation=Citation(
            authors="Williams",
            year=1995,
            title="Trading Chaos",
            venue="Practitioner book (accelerator oscillator)",
            peer_reviewed=False,
            tested_rule=(
                "The accelerator is the awesome oscillator minus a 5-period average "
                "of itself. Above zero the oscillator is above that average, which "
                "Williams reads as acceleration."
            ),
            implemented_rule=(
                "Long while the awesome oscillator (5, 34) is above its own "
                "5-period SMA. The 5 and 34 stay fixed."
            ),
            differences=(
                "lib.awesome is the zero line of the oscillator itself. The "
                "smoothing length is the only grid axis. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="gator",
        title="Gator expanding with lips above the jaw",
        family="trend",
        evaluation="daily",
        params=(
            Param("lips", 5, (3, 7)),
            Param("jaw", 13, (10, 16)),
        ),
        signal=_gator,
        citation=Citation(
            authors="Williams",
            year=1995,
            title="Trading Chaos",
            venue="Practitioner book (gator oscillator)",
            peer_reviewed=False,
            tested_rule=(
                "The gator plots the absolute jaw-to-teeth gap and the absolute "
                "teeth-to-lips gap. Both wider than the day before is the expanding "
                "phase. The alligator lines are the 5, 8, and 13 smoothed averages "
                "with their forward shifts read backward, so only older bars are used."
            ),
            implemented_rule=(
                "Long while both absolute gaps are larger than yesterday and the "
                "lips are above the jaw. Teeth stay length 8, displaced 5."
            ),
            differences=(
                "lib.alligator is lips above teeth above jaw, with no expansion "
                "test. The displacements stay fixed. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="camarilla",
        title="Close above yesterday's Camarilla H3",
        family="trend",
        evaluation="daily",
        params=(Param("trend", 200, (150, 250)),),
        signal=_camarilla,
        citation=Citation(
            authors="Stott",
            year=1989,
            title="Camarilla pivot equation",
            venue="Practitioner; Nick Stott's Camarilla levels",
            peer_reviewed=False,
            tested_rule=(
                "Camarilla H3 of a session is its close plus its range times 1.1/4. "
                "A close through H3 is the long trigger in the equation's upper band."
            ),
            implemented_rule=(
                "Long while the close is above yesterday's H3 and above the 200-day SMA. "
                "The 1.1 factor stays fixed."
            ),
            differences=(
                "lib.floor_pivot is (high + low + close) / 3 of the prior session. "
                "H3 is a different level of a different equation. The level is "
                "yesterday's, because today's own H3 is always at or above today's "
                "close. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="regline",
        title="Close above the 20-day regression value",
        family="trend",
        evaluation="daily",
        params=(Param("n", 20, (14, 30)),),
        signal=_regline_up,
        citation=Citation(
            authors="Widner",
            year=1995,
            title="Automated Support and Resistance",
            venue="Technical Analysis of Stocks & Commodities (projection bands)",
            peer_reviewed=False,
            tested_rule=(
                "A linear-regression value is the fitted point of the least-squares "
                "line on the current bar. Price can close above that point while the "
                "line's slope is still negative. Widner's bands are built around this value."
            ),
            implemented_rule=(
                "Long while the close is above the fitted value of the last 20 closes "
                "on the current bar."
            ),
            differences=(
                "lib.linreg is the sign of the slope of the same line, at the same "
                "center and the same neighbours. The fitted value is mean + slope * "
                "(n - 1) / 2. The two signs are not the same test. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="gk",
        title="Quiet Garman-Klass variance above the 200-day SMA",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 14, (10, 20)),
            Param("trend", 200, (150, 250)),
        ),
        signal=_gk_calm,
        citation=Citation(
            authors="Garman & Klass",
            year=1980,
            title="On the Estimation of Security Price Volatilities from Historical Data",
            venue="Journal of Business 53(1), 67-78",
            peer_reviewed=True,
            link="https://doi.org/10.1086/296072",
            tested_rule=(
                "The Garman-Klass estimator uses the open, high, low, and close. "
                "One bar is 0.5 * ln(H/L)^2 minus (2 ln 2 - 1) * ln(C/O)^2. They "
                "estimate variance. They do not publish a timing rule."
            ),
            implemented_rule=(
                "Long while the close is above the 200-day SMA and the 14-day mean "
                "of the Garman-Klass estimator is below its own 100-day median."
            ),
            differences=(
                "The 100-day median is fixed. lib.vol_trend is close-to-close "
                "volatility against a 12-month sign. lib.ulcer is drawdown from the "
                "recent high. The Garman-Klass average can be negative, and a "
                "negative reading can still sit below its median. Next-open fills, "
                "BTC, fees."
            ),
        ),
    ),
    LibraryStrategy(
        key="harami",
        title="Bullish harami above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(Param("trend", 200, (150, 250)),),
        signal=_harami,
        citation=Citation(
            authors="Nison",
            year=1991,
            title="Japanese Candlestick Charting Techniques",
            venue="Practitioner book (bullish harami)",
            peer_reviewed=False,
            tested_rule=(
                "A bullish harami is a small real body inside the prior long black "
                "body, after that down close. The high and the low are not the test."
            ),
            implemented_rule=(
                "Long on a close whose body sits strictly inside the prior down body, "
                "and only when that close is above the 200-day SMA."
            ),
            differences=(
                "lib.inside_day uses the full high and low and waits for a later "
                "break of that high. This name trades the harami close itself and "
                "ignores the wicks. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="expiry",
        title="Long the three sessions into the third Friday",
        family="seasonality",
        evaluation="daily",
        params=(Param("n", 3, (2, 4), unit="days"),),
        signal=_expiry,
        citation=Citation(
            authors="Stoll & Whaley",
            year=1987,
            title="Program Trading and Expiration-Day Effects",
            venue="Journal of Finance 42(1), 127-155",
            peer_reviewed=True,
            link="https://doi.org/10.1111/j.1540-6261.1987.tb02554.x",
            tested_rule=(
                "US stocks and futures, 1982-1986. Volume and price effects cluster "
                "on expiration days, the third Friday. They measure the day. They "
                "do not publish an all-in timing backtest."
            ),
            implemented_rule=(
                "Long for the three UTC sessions ending on the third Friday of the "
                "month. The Friday close turns the signal off. Two sessions and four "
                "sessions are the neighbours."
            ),
            differences=(
                "Twelve turns a year, so this is not a one-trade-a-year calendar. "
                "lib.tom is the month-end window. lib.first_half is calendar days "
                "1 through 15. Tomorrow's date is known at today's close. Cash earns "
                "nothing. Next-open fills, BTC, fees."
            ),
        ),
    ),
    # Batch 7 (2026-11-01). Appended after the batch-6 twenty-four. Claims walk
    # this tuple in order, so these names are leased only once the names
    # above are tested or on the book. Do not insert above ``expiry``.
    LibraryStrategy(
        key="smi",
        title="Stochastic momentum index(10, 3, 3) above zero",
        family="momentum",
        evaluation="daily",
        params=(Param("n", 10, (8, 14)),),
        signal=_smi_up,
        citation=Citation(
            authors="Blau",
            year=1993,
            title="Stochastic Momentum",
            venue="Technical Analysis of Stocks & Commodities (stochastic momentum index)",
            peer_reviewed=False,
            tested_rule=(
                "SMI is the double-smoothed distance of the close from the midpoint "
                "of the high-low range, divided by the smoothed half-range. Above "
                "zero the close is above that midpoint. Blau's index is not Lane's "
                "%K, which measures the close from the bottom of the range."
            ),
            implemented_rule=(
                "Long while SMI(10, 3, 3) is above zero. Both exponential smooths "
                "stay at 3."
            ),
            differences=(
                "lib.stoch is fast %K above %D. lib.stoch_slow is slow %D above a "
                "further average. lib.tsi double-smooths close-to-close momentum, "
                "not the distance from the range midpoint. A zero-width range is 0, "
                "which is not long. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="tsi_sig",
        title="TSI(25, 13) above its 7-day EMA",
        family="momentum",
        evaluation="daily",
        params=(Param("signal", 7, (5, 9)),),
        signal=_tsi_signal,
        citation=Citation(
            authors="Blau",
            year=1991,
            title="True Strength Index",
            venue="Technical Analysis of Stocks & Commodities, November 1991",
            peer_reviewed=False,
            tested_rule=(
                "TSI is double-smoothed momentum divided by double-smoothed absolute "
                "momentum. A signal line is a short EMA of TSI. TSI above that line "
                "turns up before TSI itself crosses zero."
            ),
            implemented_rule=(
                "Long while TSI(25, 13) is above its 7-day EMA. The 25 and the 13 "
                "stay fixed."
            ),
            differences=(
                "lib.tsi is the zero line of the same 25/13 index. lib.trix_sig is "
                "a triple EMA of price, not double-smoothed momentum. The signal "
                "length is the only grid axis. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="cmo_sig",
        title="CMO(14) above its 9-day SMA",
        family="momentum",
        evaluation="daily",
        params=(Param("signal", 9, (7, 12)),),
        signal=_cmo_signal,
        citation=Citation(
            authors="Chande",
            year=1994,
            title="The New Technical Trader",
            venue="Practitioner book (Chande momentum oscillator)",
            peer_reviewed=False,
            tested_rule=(
                "The Chande momentum oscillator is the raw up-sum minus the down-sum, "
                "over their total, with no Wilder smoothing. A signal line is a "
                "short average of that oscillator."
            ),
            implemented_rule=(
                "Long while CMO(14) is above its 9-day SMA. The oscillator length "
                "stays 14."
            ),
            differences=(
                "lib.cmo is the zero line, and its center length is 20. Length 14 "
                "here is the published oscillator under the signal line, not a new "
                "zero-line key. Not an RSI. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="mama",
        title="Close above the MESA adaptive moving average",
        family="trend",
        evaluation="daily",
        params=(Param("fast", 50, (40, 60), unit="hundredths"),),
        signal=_mama_up,
        citation=Citation(
            authors="Ehlers",
            year=2001,
            title="MAMA — The Mother of Adaptive Moving Averages",
            venue="mesa software note; Technical Analysis of Stocks & Commodities",
            peer_reviewed=False,
            tested_rule=(
                "MAMA is an EMA whose alpha is the fast limit divided by the phase "
                "rate of the Hilbert homodyne discriminator, clamped between a fast "
                "limit of 0.5 and a slow limit of 0.05. Price is the median."
            ),
            implemented_rule=(
                "Long while the close is above MAMA. The fast limit is 0.5 at the "
                "center. The slow limit stays 0.05."
            ),
            differences=(
                "The paper also plots FAMA, at half the alpha, and trades the cross. "
                "This name is price versus MAMA, the same reading as close versus "
                "KAMA. lib.frama uses the fractal dimension. lib.kama uses the "
                "efficiency ratio. lib.vidya uses the Chande oscillator. The "
                "Hilbert startup is not traded: the signal starts at bar 40. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="itrend",
        title="Close above the instantaneous trendline",
        family="trend",
        evaluation="daily",
        params=(Param("alpha", 7, (5, 10), unit="hundredths"),),
        signal=_itrend_up,
        citation=Citation(
            authors="Ehlers",
            year=2004,
            title="Cybernetic Analysis for Stocks and Futures",
            venue="Practitioner book (instantaneous trendline)",
            peer_reviewed=False,
            tested_rule=(
                "The instantaneous trendline is a two-pole smoother of a 4-bar "
                "weighted median price. Alpha 0.07 is the published coefficient. "
                "The book replaces the filter with price until the seventh bar."
            ),
            implemented_rule=(
                "Long while the close is above that trendline. Alpha 0.07 is the "
                "center. The seed bars are not a position."
            ),
            differences=(
                "lib.ssmooth is the two-pole super smoother with a different "
                "coefficient. lib.cyber is the high-pass cycle above its previous "
                "value, not price above a trendline. Alpha is stored in hundredths. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="ebsw",
        title="Sine wave above the lead sine",
        family="momentum",
        evaluation="daily",
        params=(Param("lead", 45, (30, 60), unit="degrees"),),
        signal=_ebsw_up,
        citation=Citation(
            authors="Ehlers",
            year=2004,
            title="Cybernetic Analysis for Stocks and Futures",
            venue="Practitioner book (sine wave indicator)",
            peer_reviewed=False,
            tested_rule=(
                "The sine wave indicator takes the sine of the dominant-cycle "
                "phase and the sine of that phase plus 45 degrees. Sine above the "
                "lead sine is the long. The phase comes from the Hilbert transform."
            ),
            implemented_rule=(
                "Long while sin(phase) is above sin(phase + 45 degrees). The phase "
                "is the homodyne arctangent. The lead is the only grid axis."
            ),
            differences=(
                "The 2013 Even Better Sinewave is one normalized line and has no "
                "lead sine. This is the 2004 phase rule the backlog names. It is "
                "not the one-bar slope of lib.cyber and not lib.fisher. The "
                "Hilbert startup is not traded. Fast and slow limits stay 0.5 and "
                "0.05 because they only build the phase. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="eratio",
        title="Efficiency ratio above 0.30 in an uptrend",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 10, (8, 14)),
            Param("trend", 200, (150, 250)),
        ),
        signal=_eratio_up,
        citation=Citation(
            authors="Kaufman",
            year=1995,
            title="Smarter Trading",
            venue="Practitioner book (efficiency ratio)",
            peer_reviewed=False,
            tested_rule=(
                "The efficiency ratio is the net close-to-close change divided by "
                "the sum of the absolute one-day changes. Kaufman uses it as the "
                "adaptive constant inside an average. A high ratio is a directional "
                "path."
            ),
            implemented_rule=(
                "Long while the 10-day efficiency ratio is above 0.30 and the close "
                "is above the 200-day SMA. The 0.30 stays fixed."
            ),
            differences=(
                "lib.kama folds the ratio into an average and then asks whether "
                "price is above that average. This name thresholds the ratio. A "
                "window with no movement is 0, which is not above 0.30. Next-open "
                "fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="rsquared",
        title="20-day r-squared above 0.40 with a positive slope",
        family="trend",
        evaluation="daily",
        params=(Param("n", 20, (15, 30)),),
        signal=_rsquared_up,
        citation=Citation(
            authors="Achelis",
            year=1995,
            title="Technical Analysis from A to Z",
            venue="Practitioner reference (r-squared)",
            peer_reviewed=False,
            tested_rule=(
                "R-squared is the fraction of the close's variance explained by a "
                "straight time trend. Achelis records it as the companion of the "
                "regression slope: a high reading means the slope is a clean line."
            ),
            implemented_rule=(
                "Long while the 20-day r-squared is above 0.40 and the least-squares "
                "slope is positive. The 0.40 stays fixed."
            ),
            differences=(
                "lib.linreg is any positive slope, including a noisy one. A flat "
                "window has r-squared 0. lib.vhf is a range over the path length, "
                "not a regression fit. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="seband",
        title="Close above the 20-day regression value plus one standard error",
        family="trend",
        evaluation="daily",
        params=(Param("n", 20, (15, 30)),),
        signal=_seband_up,
        citation=Citation(
            authors="Andersen",
            year=1996,
            title="Standard Error Bands",
            venue="Technical Analysis of Stocks & Commodities, February 1996",
            peer_reviewed=False,
            tested_rule=(
                "Standard error bands sit one residual standard error above and "
                "below the linear regression value. A close through the upper band "
                "is the strong side of the channel."
            ),
            implemented_rule=(
                "Long while the close is above the 20-day regression value plus "
                "one residual standard error. The width stays 1. The divisor is "
                "n - 2."
            ),
            differences=(
                "lib.regline is the line with no band. A perfect line has a zero "
                "band, so a close above the line still qualifies. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="kagi",
        title="Kagi turns yang by 2 percent",
        family="breakout",
        evaluation="daily",
        params=(Param("rev", 2, (1, 3), unit="percent"),),
        signal=_kagi_up,
        citation=Citation(
            authors="Nison",
            year=1994,
            title="Beyond Candlesticks",
            venue="Practitioner book (kagi)",
            peer_reviewed=False,
            tested_rule=(
                "A kagi line reverses when price retraces a set amount from the "
                "prior extreme. A reversal up is a yang line. The chart is built "
                "from closes, in order. The traditional reversal was a fixed price; "
                "a percent of price is the form used when the level has to travel "
                "with bitcoin."
            ),
            implemented_rule=(
                "Long on a close that reverses up by at least 2 percent from the "
                "prior swing low. Flat on a reversal down by that amount. The first "
                "line does not enter."
            ),
            differences=(
                "lib.tlbreak counts lines, not a price amount. One percent and "
                "three percent are the neighbours. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="renko",
        title="Renko white brick of 1 percent",
        family="breakout",
        evaluation="daily",
        params=(Param("brick", 1, (2, 3), unit="percent"),),
        signal=_renko_up,
        citation=Citation(
            authors="Nison",
            year=1994,
            title="Beyond Candlesticks",
            venue="Practitioner book (renko)",
            peer_reviewed=False,
            tested_rule=(
                "A renko chart prints a brick when price has moved the brick size "
                "from the prior brick. A white brick is the up move. The chart is "
                "built from closes, in order."
            ),
            implemented_rule=(
                "Long while the latest brick is white. A brick is 1 percent of the "
                "level it leaves. One bar may print more than one brick, and the "
                "last brick of the bar decides."
            ),
            differences=(
                "lib.tlbreak reverses through a count of lines. lib.kagi reverses "
                "by a percent of the swing, not by equal bricks. A one-brick "
                "reversal is the classic chart. Two percent and three percent are "
                "the neighbours. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="morning",
        title="Morning star above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(Param("trend", 200, (150, 250)),),
        signal=_morning_star,
        citation=Citation(
            authors="Nison",
            year=1991,
            title="Japanese Candlestick Charting Techniques",
            venue="Practitioner book (morning star)",
            peer_reviewed=False,
            tested_rule=(
                "A morning star is a long black candle, a small real body, and a "
                "white candle that closes back through the midpoint of the first "
                "body. The book draws a gap into the star. The third close is the "
                "confirmation."
            ),
            implemented_rule=(
                "Long on a white close through the midpoint of a black body, after "
                "a smaller body whose top stays below that midpoint, and only when "
                "the close is above the 200-day SMA."
            ),
            differences=(
                "The star's gap is not on this tape, so it is not required. "
                "lib.pierce is a two-candle midpoint test. lib.soldiers is three "
                "rising white bodies. The long is the third close only. Next-open "
                "fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="hammer",
        title="Hammer above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(Param("trend", 200, (150, 250)),),
        signal=_hammer,
        citation=Citation(
            authors="Nison",
            year=1991,
            title="Japanese Candlestick Charting Techniques",
            venue="Practitioner book (hammer)",
            peer_reviewed=False,
            tested_rule=(
                "A hammer has a lower shadow at least twice the real body and a "
                "small body at the top of the range, after a decline."
            ),
            implemented_rule=(
                "Long on that close when the lower shadow is at least twice the "
                "body, the body sits in the top third of the range, the prior close "
                "is down, and the close is above the 200-day SMA."
            ),
            differences=(
                "lib.oops reclaims a broken low and does not look at the body. "
                "lib.ibs is the close's location in the range, smoothed into a "
                "hold. A zero range is not a hammer. The long is that close only. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="rising3",
        title="Rising three methods above the 200-day SMA",
        family="trend",
        evaluation="daily",
        params=(Param("trend", 200, (150, 250)),),
        signal=_rising_three,
        citation=Citation(
            authors="Nison",
            year=1991,
            title="Japanese Candlestick Charting Techniques",
            venue="Practitioner book (rising three methods)",
            peer_reviewed=False,
            tested_rule=(
                "Rising three methods is a long white candle, three small bodies "
                "held inside its range, and a white close above the first candle's "
                "close. It is a continuation, not a reversal."
            ),
            implemented_rule=(
                "Long on that fifth close when each of the three middle bars stays "
                "inside the first bar's high and low, each middle body is smaller "
                "than the first, and the close is above the 200-day SMA."
            ),
            differences=(
                "lib.soldiers is three rising white bodies with no resting bars. "
                "lib.inside_day is one bar inside the prior bar, then a later break. "
                "The long is the fifth close only. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="three_out",
        title="Three outside up above the 200-day SMA",
        family="mean_reversion",
        evaluation="daily",
        params=(Param("trend", 200, (150, 250)),),
        signal=_three_outside,
        citation=Citation(
            authors="Nison",
            year=1991,
            title="Japanese Candlestick Charting Techniques",
            venue="Practitioner book (three outside up)",
            peer_reviewed=False,
            tested_rule=(
                "Three outside up is a bullish engulfing followed by a third close "
                "above the engulfing close. The third bar confirms the engulfing."
            ),
            implemented_rule=(
                "Long on that third close, and only when it is above the 200-day SMA."
            ),
            differences=(
                "lib.engulf is the engulfing bar alone. The white body still has "
                "to be strictly larger than the black body it covers. The long is "
                "the third close only. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="twobar",
        title="Two-bar reversal: close above yesterday's high after a lower low",
        family="mean_reversion",
        evaluation="daily",
        params=(Param("n", 1, (2, 3)),),
        signal=_two_bar,
        citation=Citation(
            authors="Bulkowski",
            year=2008,
            title="Encyclopedia of Candlestick Charts",
            venue="Practitioner book (two-bar reversal)",
            peer_reviewed=False,
            tested_rule=(
                "A bullish two-bar reversal makes a lower low than the previous "
                "bar and closes above the previous high. The close is the "
                "confirmation."
            ),
            implemented_rule=(
                "Long on a close above the prior n-bar high after the low took out "
                "the prior n-bar low. n is 1 at the center, so the bars are "
                "yesterday and today."
            ),
            differences=(
                "lib.oops only has to reclaim the low. lib.outside takes out both "
                "sides and then waits for a later break of that high. This name "
                "trades the reversal close itself. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="td_combo",
        title="TD buy combo completes",
        family="mean_reversion",
        evaluation="daily",
        params=(
            Param("setup", 9, (8, 10), unit="bars"),
            Param("count", 13, (12, 14), unit="bars"),
            Param("trend", 200, (150, 250)),
        ),
        signal=_td_combo,
        citation=Citation(
            authors="DeMark",
            year=1997,
            title="New Market Timing Techniques",
            venue="Practitioner book (TD Combo)",
            peer_reviewed=False,
            tested_rule=(
                "After a buy setup, the combo counts thirteen closes each at or "
                "below the close two bars earlier. The countdown, by contrast, "
                "compares the close with the low two bars earlier. The completed "
                "combo is the buy."
            ),
            implemented_rule=(
                "Long on the close that completes a 13-bar buy combo after a 9-bar "
                "buy setup, and only when that close is above the 200-day SMA."
            ),
            differences=(
                "lib.td_cd uses the low two bars earlier and the bar-8 qualifier, "
                "and it does not use the average: that filter blocked every "
                "countdown on this tape. The combo still completes above the "
                "average, so the filter stays. An opposing setup cancels an "
                "unfinished count. A new buy setup restarts it. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="di_cross",
        title="+DI above -DI",
        family="trend",
        evaluation="daily",
        params=(Param("n", 14, (10, 20)),),
        signal=_di_cross,
        citation=Citation(
            authors="Wilder",
            year=1978,
            title="New Concepts in Technical Trading Systems",
            venue="Practitioner book (directional movement)",
            peer_reviewed=False,
            tested_rule=(
                "Plus directional movement is the part of today's high above "
                "yesterday's high when that is the larger positive move. Minus "
                "directional movement is the symmetric low. +DI above -DI is the "
                "up direction. ADX is the smoothed size of that gap, a separate test."
            ),
            implemented_rule="Long while +DI(14) is above -DI(14). There is no ADX floor.",
            differences=(
                "lib.adx14 also requires ADX above 25. A zero-range day contributes "
                "no directional movement and a zero index, which is not a cross. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="woodie",
        title="Close above the Woodie pivot and the 200-day SMA",
        family="trend",
        evaluation="daily",
        params=(Param("trend", 200, (150, 250)),),
        signal=_woodie_up,
        citation=Citation(
            authors="Woodie",
            year=2000,
            title="Woodie's pivot",
            venue="Practitioner pivot (CCI Club); the equation is (H + L + 2C) / 4",
            peer_reviewed=False,
            tested_rule=(
                "Woodie's pivot of a completed day weights the close twice: "
                "(high + low + 2 × close) / 4. A later close above that pivot is "
                "the long side of the day."
            ),
            implemented_rule=(
                "Long while the close is above yesterday's Woodie pivot and above "
                "the 200-day SMA."
            ),
            differences=(
                "lib.floor_pivot weights high, low, and close equally. lib.camarilla "
                "is a fraction of the range added to the close, not this average. "
                "The pivot uses only the prior day. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="fibhold",
        title="Close holds the 61.8 percent level of the prior 20-day range",
        family="trend",
        evaluation="daily",
        params=(
            Param("n", 20, (14, 30)),
            Param("trend", 200, (150, 250)),
        ),
        signal=_fib_hold,
        citation=Citation(
            authors="Fischer",
            year=1993,
            title="Fibonacci Applications and Strategies for Traders",
            venue="Practitioner book",
            peer_reviewed=False,
            tested_rule=(
                "A retracement of 38.2 percent off a high is the 61.8 percent "
                "level of that high-low range, measured up from the low. Holding "
                "above it means the pullback has not given back more than 38.2 "
                "percent of the range."
            ),
            implemented_rule=(
                "Long while the close is above the 200-day SMA and above the prior "
                "20-day low plus 0.618 times that high-low range. Today is outside "
                "the range. 0.618 stays fixed."
            ),
            differences=(
                "Not a moving-average cross. The range is the prior n bars, so "
                "today's high cannot widen the level it is tested against. "
                "Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="saucer",
        title="Awesome oscillator saucer below zero, above the 200-day SMA",
        family="momentum",
        evaluation="daily",
        params=(Param("trend", 200, (150, 250)),),
        signal=_saucer,
        citation=Citation(
            authors="Williams",
            year=1995,
            title="Trading Chaos",
            venue="Practitioner book (awesome oscillator saucer)",
            peer_reviewed=False,
            tested_rule=(
                "A saucer is two falling bars of the awesome oscillator below zero, "
                "then a higher bar that is still below zero. It is a long before "
                "the oscillator itself crosses zero."
            ),
            implemented_rule=(
                "Long on that third bar when the close is also above the 200-day "
                "SMA. The oscillator stays 5 and 34."
            ),
            differences=(
                "lib.awesome is the zero line of the same oscillator. lib.accel is "
                "the oscillator minus its own average. The long is the saucer bar "
                "only. Next-open fills, BTC."
            ),
        ),
    ),
    LibraryStrategy(
        key="gator_wake",
        title="Gator awakens after a contraction",
        family="trend",
        evaluation="daily",
        params=(
            Param("lips", 5, (3, 7)),
            Param("jaw", 13, (10, 16)),
        ),
        signal=_gator_wake,
        citation=Citation(
            authors="Williams",
            year=1995,
            title="Trading Chaos",
            venue="Practitioner book (gator oscillator)",
            peer_reviewed=False,
            tested_rule=(
                "The gator is asleep while both absolute gaps are shrinking and "
                "awakens when both turn wider. The lips above the jaw keeps the "
                "awakening on the long side. The lines are the alligator averages "
                "read backward, so only older bars are used."
            ),
            implemented_rule=(
                "Long while both absolute gaps were smaller yesterday than the day "
                "before and are larger today, and the lips are above the jaw. "
                "Teeth stay length 8, displaced 5."
            ),
            differences=(
                "lib.gator is the expansion alone, with no prior contraction. The "
                "displacements stay fixed. Next-open fills, BTC."
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
