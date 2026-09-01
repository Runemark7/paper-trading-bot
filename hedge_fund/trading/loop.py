"""Trading decision loop — orchestrates one full cycle.

For each symbol, one cycle:
  1. Fetch klines from the data source.
  2. Compute the signal condition (deterministic engine).
  3. Ask the calibration layer for the calibrated probability for that
     condition (this is the number logged against the trade — the agent's
     proposed probability is blended only during cold-start warm-up).
  4. Gate through the risk manager (1% sizing, open-risk cap, drawdown
     breaker). A rejected trade is recorded as a decision with reason.
  5. If approved, place the paper order (fees + slippage) at the current
     price, with a mandatory stop.
  6. Re-price open positions each cycle; close on stop breach or take-profit.

Every decision and every outcome is logged to a SQLite store — that is the
audit trail the PROTOCOL.md honesty contract requires. Outcomes are recorded
back into the calibration layer only once a position closes (horizon known),
so there is no lookahead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from hedge_fund.brokers.paper import PaperBroker, Order
from hedge_fund.calibration import CalibrationStore, condition_key
from hedge_fund.data.binance import Candle, CcxtSource
from hedge_fund.risk.managed import RiskManager
from hedge_fund.risk.rm_v1 import (
    ATR_PERIOD,
    ATR_STOP_MULT,
    CONFIDENCE_REF_PROB,
    MAX_LOTS_PER_SYMBOL,
    STOP_CAP_FRAC,
    STOP_FALLBACK_FRAC,
    STOP_FLOOR_FRAC,
    TAKE_PROFIT_RR,
    atr_stop_price,
    confidence_multiplier,
)
from hedge_fund.signals.momentum import Signal, compute_signal
from hedge_fund.trading.buy_and_hold import overlay_equity
from hedge_fund.trading.constants import PAPER_START_CASH, QUAL_TIMEFRAME
from hedge_fund.trading.store import TradeStore
from hedge_fund.backtest.strategies import atr

# Live-cycle constants — re-exported from rm_v1 so dashboard copy cannot
# drift from the frozen policy (PROTOCOL amendment 2026-09-01).


@dataclass
class CycleResult:
    symbol: str
    timestamp: str
    condition: str
    probability: float
    direction: str
    action: str  # "ENTER" | "HOLD" | "CLOSE_TP" | "CLOSE_STOP" | "REJECTED"
    reason: str = ""
    equity: float = 0.0
    size: float = 0.0
    entry: float = 0.0
    fills: list = field(default_factory=list)


class TradingLoop:
    def __init__(
        self,
        data: CcxtSource,
        broker: PaperBroker,
        risk: RiskManager,
        calib: CalibrationStore,
        store: TradeStore | None = None,
        regime: "RegimeGate | None" = None,
        timeframe: str = QUAL_TIMEFRAME,
        horizon_bars: int = 6,  # ~1 day at 4h; success = close above entry at horizon
        kline_limit: int = 300,
        strategy: str = "sma_stack",
        strategies: list[str] | None = None,
        strategy_file: str | None = None,
    ) -> None:
        self.data = data
        self.broker = broker
        self.risk = risk
        self.calib = calib
        self.store = store
        self.regime = regime
        self.strategy = strategy
        self.strategies = strategies or [strategy]
        # If a champion file is given, prefer the current self-learned winner.
        if strategy_file:
            try:
                import json as _json
                with open(strategy_file) as _f:
                    champ = _json.load(_f).get("champion") or {}
                if champ.get("strategy"):
                    self.strategies = [champ["strategy"]]
            except Exception:
                pass  # fall back to default on any read problem
        self.strategy_file = strategy_file
        self.timeframe = timeframe
        self.horizon_bars = horizon_bars
        self.kline_limit = kline_limit
        self.history: list[CycleResult] = []
        # open trade ids keyed by symbol, for close accounting
        self._open_ids: dict[str, int] = {}

    # ------------------------------------------------------------------
    def _propose_probability(self, signal, features) -> float:
        """Agent-style proposed probability (placeholder for LLM reasoning).

        For now a deterministic heuristic derived from how far RSI/trend lean,
        so the loop is runnable headless. When the LLM decision layer is wired
        later, this is the seam where Hermes's stated probability enters — and
        the calibration layer still blends it only during cold start.
        """
        # optimistic: pull a baseline in [0.52, 0.72] from raw_score and RSI
        base = 0.55 + 0.15 * abs(signal.raw_score)
        # RSI extremes (mean-reversion zones) justify slightly higher confidence
        if signal.features.rsi >= 70:
            base += 0.03
        elif signal.features.rsi <= 30:
            base += 0.03
        return round(max(0.5, min(0.8, base)), 3)

    # ------------------------------------------------------------------
    def run_cycle(self, symbols: list[str]) -> list[CycleResult]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        results: list[CycleResult] = []

        # Current prices (single point-in-time).
        px = {}
        for sym in symbols:
            try:
                px[sym] = self.data.fetch_price(sym)
            except Exception:
                px[sym] = None

        # Re-price open positions first: stops + take-profits.
        self._manage_open_positions(px, now)

        for sym in symbols:
            try:
                klines = self.data.fetch_klines(sym, self.timeframe, limit=self.kline_limit)
                sig = compute_signal(klines, sym, self.timeframe, strategy=self.strategies[0])
            except Exception as exc:
                results.append(CycleResult(sym, now, "ERR", 0.0, "flat", "REJECTED",
                                           reason=f"data error: {exc}"))
                continue

            # Active signal exit: if we have open lots for this symbol and the strategy
            # no longer says "long" (setup broke / invalidated), close them immediately!
            if sig.direction != "long":
                lots_to_close = [lot for lot in self.broker.lots if lot.ticker == sym]
                if lots_to_close:
                    cur = px.get(sym)
                    if cur is not None:
                        for lot in lots_to_close:
                            fill = self.broker.close_lot(lot, cur)
                            pnl = (fill.price - lot.entry_price) * lot.quantity - fill.fee
                            self._record_outcome(sym, lot, fill, tp=(pnl > 0))
                            self._close_store_lot(lot, fill, f"signal_exit_{sig.condition}")
                            results.append(CycleResult(
                                sym, now, sig.condition, 0.0, "flat", "CLOSE_SIGNAL",
                                reason=f"strategy exit: {sig.condition} (cur={cur:.2f})",
                                equity=self.broker.equity(px)
                            ))

            key = condition_key(sym, self.timeframe, sig.condition)
            proposed = self._propose_probability(sig, sig.features)
            prob, _ = self.calib.calibrated_probability(key, proposed=proposed)
            equity = self.broker.equity(px)

            if self.risk.is_halted():
                results.append(CycleResult(
                    sym, now, sig.condition, prob, sig.direction, "REJECTED",
                    reason=f"halted: {self.risk.halt_reason}", equity=equity))
                continue

            # Multiple concurrent lots per symbol allowed (pyramiding into an
            # uptrend). The 5% open-risk cap below bounds how many stack.
            # (No is_open hold here — a fresh long lot may open alongside.)

            # Only enter on an eligible long signal
            if sig.direction != "long":
                results.append(CycleResult(
                    sym, now, sig.condition, prob, sig.direction, "HOLD",
                    reason=f"no entry: direction={sig.direction}", equity=equity))
                continue

            # Regime gate: hard precondition for new longs.
            if self.regime is not None and not self.regime.allowed_to_trade():
                zone = self.regime.zone() or "?"
                results.append(CycleResult(
                    sym, now, sig.condition, prob, sig.direction, "REJECTED",
                    reason=f"regime {zone} blocks new longs (strict gate)",
                    equity=equity))
                continue

            entry = px.get(sym)
            if entry is None:
                results.append(CycleResult(sym, now, sig.condition, prob, sig.direction,
                                           "REJECTED", reason="no price", equity=equity))
                continue

            # ATR dynamic volatility stop — rm_v1 (same helper as discovery).
            highs_k = [c.high for c in klines]
            lows_k = [c.low for c in klines]
            closes_k = [c.close for c in klines]
            a = atr(highs_k, lows_k, closes_k, ATR_PERIOD)
            stop = atr_stop_price(entry, a)

            # Pyramiding guardrails:
            # 1. Max MAX_LOTS_PER_SYMBOL lots per symbol
            # 2. If existing lots exist for this symbol, only allow adding if all existing lots are in profit
            existing_sym_lots = [lot for lot in self.broker.lots if lot.ticker == sym]
            if len(existing_sym_lots) >= MAX_LOTS_PER_SYMBOL:
                results.append(CycleResult(
                    sym, now, sig.condition, prob, sig.direction, "REJECTED",
                    reason=f"max lots reached ({MAX_LOTS_PER_SYMBOL} lots open for {sym})", equity=equity))
                continue

            if existing_sym_lots:
                # Require previous lots to be in profit before pyramiding up
                unrealized_lots_pnl = [(entry - lot.entry_price) * lot.quantity for lot in existing_sym_lots]
                if any(p <= 0 for p in unrealized_lots_pnl):
                    results.append(CycleResult(
                        sym, now, sig.condition, prob, sig.direction, "HOLD",
                        reason=f"pyramid hold: prior {sym} lots not in profit yet", equity=equity))
                    continue

            # Confidence multiplier from stated probability vs CONFIDENCE_REF_PROB (rm_v1).
            conf_mult = confidence_multiplier(prob)

            # Existing open risk (for the cap): account for ALL open lots.
            open_pos = [
                (p.entry_price, p.stop_loss, p.quantity)
                for p in self.broker.lots
            ]
            rd = self.risk.size_position(equity, entry, stop, open_pos, confidence=conf_mult)
            if not rd.approved:
                results.append(CycleResult(
                    sym, now, sig.condition, prob, sig.direction, "REJECTED",
                    reason=rd.reason, equity=equity))
                continue

            # Place the paper order, carrying the calibration condition key.
            fill = self.broker.place_order(
                Order(sym, "buy", rd.size, entry, stop_loss=stop, condition=key),
                market_price=entry,
            )
            results.append(CycleResult(
                sym, now, sig.condition, prob, sig.direction, "ENTER",
                reason=f"size {rd.size:.4f} stop {stop:.0f}",
                equity=self.broker.equity(px), size=rd.size, entry=entry, fills=[fill]))

            # persist: open a trade row + log the decision
            if self.store is not None:
                # the lot just created is the last in broker.lots
                new_lot = self.broker.lots[-1] if self.broker.lots else None
                lot_id = new_lot.lot_id if new_lot else None
                self.store.open_trade(
                    sym, self.timeframe, sig.condition, prob, entry, rd.size,
                    entry_fee=fill.fee, lot_id=lot_id,
                )
                tid = self.store.open_trade_ids()[-1]["id"]

        # persist decisions + equity snapshot once per cycle
        if self.store is not None:
            for r in results:
                self.store.record_decision(
                    r.symbol, self.timeframe, r.condition, None,
                    r.probability, r.direction, r.action, r.reason,
                    r.equity, r.size, r.entry,
                    raw_signal={"condition": r.condition, "direction": r.direction},
                )
            px_now = {}
            for sym in symbols:
                try:
                    px_now[sym] = self.data.fetch_price(sym)
                except Exception:
                    px_now[sym] = None
            equity_now = self.broker.equity(px_now)
            baseline = overlay_equity(self.store, px_now, PAPER_START_CASH)
            self.store.snapshot_equity(equity_now, baseline=baseline)

        self.history.extend(results)
        self.calib.save()
        return results

    # ------------------------------------------------------------------
    def _manage_open_positions(self, px: dict, now: str) -> None:
        """Close any lot on stop breach or take-profit; record outcomes."""
        for lot in list(self.broker.lots):
            sym = lot.ticker
            cur = px.get(sym)
            if cur is None:
                continue
            if cur <= lot.stop_loss:
                fill = self.broker.close_lot(lot, cur)
                self._record_outcome(sym, lot, fill, tp=False)
                self._close_store_lot(lot, fill, "stop_loss")
                self.history.append(CycleResult(
                    sym, now, "closed", 0.0, "flat", "CLOSE_STOP",
                    reason=f"stop at {cur:.0f}", equity=self.broker.equity(px)))
            else:
                tp = self.risk.take_profit_price(lot.entry_price, lot.stop_loss, TAKE_PROFIT_RR)
                if cur >= tp:
                    fill = self.broker.close_lot(lot, cur)
                    self._record_outcome(sym, lot, fill, tp=True)
                    self._close_store_lot(lot, fill, "take_profit")
                    self.history.append(CycleResult(
                        sym, now, "closed", 0.0, "flat", "CLOSE_TP",
                        reason=f"tp at {cur:.2f}", equity=self.broker.equity(px)))

    def _close_store_lot(self, lot, fill, reason: str) -> None:
        """Persist a closed lot's P&L + hit flag into the store.

        Matches the open trade row by (symbol, lot_id) so multi-lot pyramids
        attribute P&L to the correct entry; falls back to symbol-only.
        """
        if self.store is None:
            return
        tid = None
        for t in self.store.open_trade_ids():
            if t["symbol"] == lot.ticker and t.get("lot_id") == lot.lot_id:
                tid = t["id"]
                break
        if tid is None:
            for t in self.store.open_trade_ids():
                if t["symbol"] == lot.ticker:
                    tid = t["id"]
                    break
        if tid is None:
            return
        entry = lot.entry_price
        exit_ = fill.price
        pnl = (exit_ - entry) * lot.quantity - fill.fee - lot.entry_fee
        pnl_pct = (exit_ - entry) / entry if entry else 0.0
        success = (exit_ > entry) if reason == "take_profit" else not (exit_ <= entry)
        self.store.close_trade(tid, exit_, reason, fill.fee, pnl, pnl_pct,
                               hit=1 if success else 0)

    def _record_outcome(self, sym, pos, fill, tp: bool) -> None:
        """Record a completed trade's outcome into the calibration layer.

        Success = the outcome matched the prediction at entry. For a long
        entered on a bullish condition, success means the position closed at
        a profit. The credential key is the ONE the position was entered
        under (`pos.entry_condition`), so each condition learns from its own
        outcomes — this is the core of the self-learning loop.
        """
        entry = pos.entry_price
        exit_ = fill.price if fill else entry
        success = (exit_ > entry) if tp else not (exit_ <= entry)
        key = pos.entry_condition or condition_key(sym, "closed", "any")
        self.calib.record_outcome(key, success)
