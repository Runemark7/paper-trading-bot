"""A/B backtest — compare candidate strategies on the same window.

Replays several named strategies over the same historic OHLC, with the same
fees/slippage and 1% fixed-fractional risk, and ranks by total P&L, win rate,
and Sharpe. This is the honest way to pick a stronger strategy than the
current RSI filter: run the same data, same risk rules, different entry
signals, and let the numbers decide.

Strategies implemented (deterministic, longs only). A strategy decides when
to be LONG (take an entry) vs flat. The paper bot runs many of these on the
same window; the A/B test ranks them by P&L / win rate / Sharpe.

  rsi_momentum    - baseline: 20-EMA trend + RSI>50
  sma_stack       - trend-following: close > SMA(7,25,50) stacked (Börslabbet-ish)
  momentum_gt     - pure momentum: 2w return above threshold
  multi_timeframe - 1d/20-bar trend gate + momentum timing

Extended set (28 total) uses the same backtest machinery with parameterized
entry signals: SMA-family, EMA-family, RSI-family, momentum variants,
mean-reversion, volatility regime, and combined gates.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# indicator helpers
# ---------------------------------------------------------------------------


def sma(vals, period, i):
    if i < period - 1:
        return float("nan")
    return sum(vals[i - period + 1 : i + 1]) / period


def rsi(vals, period=14, i=None):
    i = len(vals) - 1 if i is None else i
    if i < period:
        return 50.0
    gains = losses = 0.0
    for j in range(i - period + 1, i + 1):
        chg = vals[j] - vals[j - 1]
        if chg >= 0:
            gains += chg
        else:
            losses += -chg
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))


def atr(highs, lows, closes, period=14, i=None):
    """Average True Range at bar i."""
    i = len(closes) - 1 if i is None else i
    if i < period:
        return float("nan")
    trs = []
    for j in range(i - period + 1, i + 1):
        h, l, c, pc = highs[j], lows[j], closes[j], closes[j - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def ema(vals, period, i):
    """Exponential moving average up to bar i."""
    if i < 0:
        return float("nan")
    k = 2 / (period + 1)
    e = vals[0]
    start = 1
    for j in range(start, i + 1):
        e = vals[j] * k + e * (1 - k)
    return e


def rolling_ret(vals, i, lookback):
    """Total return from i-lookback to i, or nan."""
    if i < lookback:
        return float("nan")
    return vals[i] / vals[i - lookback] - 1


def rsi_for(period=14, thresh=50, over=100.0):
    """Build an RSI entry predicate: RSI > thresh (and not overbought if over<100)."""
    def pred(closes, i):
        r = rsi(closes, period, i)
        return r > thresh and (r < over)
    return pred


def sma_above(period):
    """close > SMA(period)."""
    def pred(closes, i):
        s = sma(closes, period, i)
        return not math.isnan(s) and closes[i] > s
    return pred


def sma_below(period):
    """close < SMA(period) — mean-reversion / oversold entry."""
    def pred(closes, i):
        s = sma(closes, period, i)
        return not math.isnan(s) and closes[i] < s
    return pred


def sma_stack_pred(periods):
    """price > SMA(p0) > SMA(p1) > ... (momentum stack)."""
    def pred(closes, i):
        vals = [sma(closes, p, i) for p in periods]
        if any(math.isnan(v) for v in vals):
            return False
        return closes[i] > vals[0] and all(vals[j] > vals[j + 1] for j in range(len(vals) - 1))
    return pred


def ema_above(period):
    def pred(closes, i):
        e = ema(closes, period, i)
        return not math.isnan(e) and closes[i] > e
    return pred


def ema_stack_pred(periods):
    def pred(closes, i):
        vals = [ema(closes, p, i) for p in periods]
        if any(math.isnan(v) for v in vals):
            return False
        return closes[i] > vals[0] and all(vals[j] > vals[j + 1] for j in range(len(vals) - 1))
    return pred


def mom_gt(lookback=12, thr=0.03):
    def pred(closes, i):
        r = rolling_ret(closes, i, lookback)
        return not math.isnan(r) and r > thr
    return pred


def mom_lt(lookback=12, thr=-0.03):
    def pred(closes, i):
        r = rolling_ret(closes, i, lookback)
        return not math.isnan(r) and r < thr
    return pred


def vol_regime(lookback=30, hi_q=0.75, lo_q=0.25):
    """High-vol breakout (buy when recent vol in top quartile, momentum up)."""
    def pred(closes, i):
        if i < lookback:
            return False
        window = [abs(closes[j]-closes[j-1])/closes[j-1] for j in range(i-lookback+1, i+1)]
        cur = abs(closes[i]-closes[i-1])/closes[i-1]
        # vol percentile vs lookback
        n_above = sum(1 for v in window if v <= cur)
        over_hi = n_above/len(window) >= hi_q
        r = rolling_ret(closes, i, lookback//2)
        return over_hi and (not math.isnan(r)) and r > 0
    return pred


# ---------------------------------------------------------------------------
# result + runner
# ---------------------------------------------------------------------------


@dataclass
class BacktestResult:
    strategy: str = ""
    trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    final_equity: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    fees_paid: float = 0.0
    error: str | None = None


def _sharpe(pnl_pcts):
    if len(pnl_pcts) < 2:
        return 0.0
    m = statistics.mean(pnl_pcts)
    sd = statistics.stdev(pnl_pcts)
    if sd == 0:
        return 0.0
    return m / sd * math.sqrt(len(pnl_pcts))


def backtest(closes, highs, lows, strategy, start_cash=10_000.0,
             risk_frac=0.01, taker_fee=0.001, slippage=0.0002,
             rr=2.0, atr_mult=2.5, momentum_lookback=12, momentum_thr=0.03,
             long_stack=(7, 25, 50)):
    """Backtest one strategy. `closes/highs/lows` are parallel arrays (bars)."""
    n = len(closes)
    cash = float(start_cash)
    peak = float(start_cash)
    max_dd = 0.0
    wins = trades = 0
    fees = 0.0
    pnl_pcts = []
    open_qty = 0.0
    entry = stop = 0.0

    warmup = max(long_stack[2], momentum_lookback, 14, 20) + 2

    def equity():
        return cash  # we're flat except when open; track prices as we go

    for i in range(warmup, n):
        cur = closes[i]

        # --- manage open position: exit at stop or TP ---
        if open_qty > 0:
            risk_px = entry - stop
            tp = entry + rr * risk_px
            exit_px = None
            if cur >= tp:
                exit_px = tp * (1 - slippage)
                hit = "tp"
            elif cur <= stop:
                exit_px = stop * (1 - slippage)
                hit = "stop"
            if exit_px is not None:
                proceeds = exit_px * open_qty
                fee = proceeds * taker_fee
                cash += proceeds - fee
                fees += fee
                pnl = (exit_px - entry) * open_qty
                pnl_pct = (exit_px - entry) / entry
                pnl_pcts.append(pnl_pct)
                wins += 1 if pnl > 0 else 0
                trades += 1
                open_qty = 0.0
                # update peak/drawdown at this close
                if cash > peak:
                    peak = cash
                dd = (peak - cash) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                continue  # just closed, no re-entry this bar
            else:
                continue  # still open, hold

        # --- decide entry ---
        take = False
        if not callable(strategy):
            strategy = strategy.lower()
        if strategy == "rsi_momentum":
            take = (closes[i] > sma(closes, 20, i) and rsi(closes, 14, i) > 50)
        elif strategy == "sma_stack":
            s7 = sma(closes, long_stack[0], i)
            s25 = sma(closes, long_stack[1], i)
            s50 = sma(closes, long_stack[2], i)
            take = not any(math.isnan(x) for x in (s7, s25, s50)) and (closes[i] > s7 > s25 > s50)
        elif strategy == "momentum_gt":
            if i >= momentum_lookback:
                take = closes[i] / closes[i - momentum_lookback] - 1 > momentum_thr
        elif strategy == "multi_timeframe":
            sma_20 = sma(closes, 20, i)
            sma_50 = sma(closes, 50, i)
            mom = (closes[i] / closes[i - 6] - 1) if i >= 6 else 0
            take = (not math.isnan(sma_50)) and closes[i] > sma_20 and closes[i] > sma_50 and mom > 0
        elif callable(strategy):
            take = strategy(closes, i)
        elif strategy in _PRED:
            take = _PRED[strategy](closes, i)
        else:
            take = False

        if not take:
            continue

        # size: 1% risk / (entry - ATR stop)
        a = atr(highs, lows, closes, 14, i)
        if math.isnan(a) or a <= 0:
            a = cur * 0.02
        stop = cur - atr_mult * a
        entry_px = cur * (1 + slippage)  # buy slippage against us
        risk_per_coin = entry_px - stop
        if risk_per_coin <= 0:
            continue
        qty = (cash * risk_frac) / risk_per_coin
        fee = entry_px * qty * taker_fee
        cash -= entry_px * qty + fee
        fees += fee
        open_qty = qty
        entry = entry_px
        # don't track drawdown mid-position precisely; approximated at close

    # close any open at last price
    if open_qty > 0:
        exit_px = closes[-1] * (1 - slippage)
        proceeds = exit_px * open_qty
        fee = proceeds * taker_fee
        cash += proceeds - fee
        fees += fee
        pnl = (exit_px - entry) * open_qty
        pnl_pct = (exit_px - entry) / entry
        pnl_pcts.append(pnl_pct)
        wins += 1 if pnl > 0 else 0
        trades += 1
        if cash > peak:
            peak = cash
        dd = (peak - cash) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    return BacktestResult(
        strategy=strategy, trades=trades, wins=wins,
        win_rate=(wins / trades) if trades else 0.0,
        total_pnl=cash - start_cash, final_equity=cash,
        sharpe=_sharpe(pnl_pcts), max_drawdown=max_dd, fees_paid=fees,
    )


# mean reversion helper (buy low vol, dip relative to long vol)
def vol_low_smooth_pred(lookback=30, comp=90):
    def pred(closes, i):
        if i < comp:
            return False
        cur_win = [abs(closes[j]-closes[j-1])/closes[j-1] for j in range(max(1,i-lookback+1), i+1)]
        comp_win = [abs(closes[j]-closes[j-1])/closes[j-1] for j in range(max(1,i-comp+1), i+1)]
        if not cur_win or not comp_win:
            return False
        return sum(cur_win)/len(cur_win) < sum(comp_win)/len(comp_win)
    return pred


def _and(*preds):
    def pred(closes, i):
        return all(p(closes, i) for p in preds)
    return pred


# ---------------------------------------------------------------------------
# 28-strategy registry (long-only entry predicates). Each is parameterized.
# ---------------------------------------------------------------------------
_PRED = {
    # --- SMA family ---
    "sma_20_abv":       sma_above(20),
    "sma_50_abv":       sma_above(50),
    "sma_100_abv":      sma_above(100),
    "sma_stack_7_25_50":sma_stack_pred((7, 25, 50)),
    "sma_stack_5_20_50":sma_stack_pred((5, 20, 50)),
    "sma_rev_50":       sma_below(50),          # mean-reversion buy under SMA50
    # --- EMA family ---
    "ema_20_abv":       ema_above(20),
    "ema_50_abv":       ema_above(50),
    "ema_stack_5_20_50":ema_stack_pred((5, 20, 50)),
    "ema_stack_10_30_90":ema_stack_pred((10, 30, 90)),
    # --- RSI family ---
    "rsi_oversold":     rsi_for(14, 30, 100),   # buy dip, not overbought
    "rsi_breakout":     rsi_for(14, 55, 100),
    "rsi_50_above":     rsi_for(14, 50, 100),
    "rsi_trend_50":     rsi_for(30, 55, 100),   # longer RSI trending
    # --- Momentum ---
    "mom_2w_gt3":       mom_gt(12, 0.03),
    "mom_1w_gt2":       mom_gt(6, 0.02),
    "mom_1m_gt5":       mom_gt(30, 0.05),
    "mom_2w_lt":        mom_lt(12, -0.02),      # buy the dip
    # --- Volatility regime ---
    "vol_breakout":     vol_regime(30, 0.75, 0.25),
    "vol_low_smooth":   vol_low_smooth_pred(30, 90),
    # --- Combined gates ---
    "sma40_rsi50":      _and(sma_above(40), rsi_for(14, 50, 100)),
    "sma20_mom":        _and(sma_above(20), mom_gt(12, 0.02)),
    "sma50_mom_1w":     _and(sma_above(50), mom_gt(6, 0.01)),
    "sma200_abv":       sma_above(200),
    "sma_rev_rsi":      _and(sma_below(200), rsi_for(14, 45, 100)),
    "hyper_short":      mom_gt(3, 0.02),       # fast 12h momentum
    "robust_open":      _and(sma_stack_pred((7, 25, 50)), rsi_for(20, 55, 100)),
    "dip_register":     _and(sma_below(100), mom_lt(30, -0.05)),  # deep dip + long base
}

STRATEGIES = list(_PRED.keys()) + [
    "rsi_momentum", "sma_stack", "momentum_gt", "multi_timeframe",
]


def backtest_all(closes, highs, lows, strategies=STRATEGIES, **kw):
    results = []
    for s in strategies:
        try:
            results.append(backtest(closes, highs, lows, s, **kw))
        except Exception as e:  # noqa: BLE001
            results.append(BacktestResult(strategy=s, error=str(e)))
    return results


def format_results(results, label="") -> str:
    lines = [f"--- A/B backtest {label} ---"]
    lines.append(f"{'strategy':<16}{'trades':>7}{'win%':>8}{'PnL':>10}{'final':>10}"
                 f"{'sharpe':>8}{'maxDD':>8}{'fees':>8}")
    for r in sorted(results, key=lambda x: x.total_pnl, reverse=True):
        if r.error:
            lines.append(f"{r.strategy:<16} ERROR: {r.error}")
            continue
        lines.append(
            f"{r.strategy:<16}{r.trades:>7}{r.win_rate:>7.0%}{r.total_pnl:>10,.0f}"
            f"{r.final_equity:>10,.0f}{r.sharpe:>8.2f}{r.max_drawdown:>7.0%}{r.fees_paid:>8,.0f}"
        )
    return "\n".join(lines)
