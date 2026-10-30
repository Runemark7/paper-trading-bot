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

from hedge_fund.library.daily import DailyBars
from hedge_fund.trading.constants import LIBRARY_STOP_FRAC
from hedge_fund.library.indicators import (
    adx_first_index,
    adx_wilder,
    bollinger,
    ema,
    hold_between,
    ibs,
    ichimoku_cloud_top,
    ichimoku_first_index,
    is_month_end,
    is_week_end,
    keltner,
    month_number,
    prior_max,
    prior_min,
    rsi_wilder,
    sma,
    supertrend_long,
    week_number,
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
