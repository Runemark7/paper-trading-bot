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
from hedge_fund.data.binance import CcxtSource
from hedge_fund.risk.managed import RiskManager
from hedge_fund.signals.momentum import compute_signal
from hedge_fund.trading.store import TradeStore

TAKE_PROFIT_RR = 2.0  # 2:1 reward:risk


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
        timeframe: str = "4h",
        horizon_bars: int = 6,  # ~1 day at 4h; success = close above entry at horizon
        kline_limit: int = 300,
        strategy: str = "sma_stack",
    ) -> None:
        self.data = data
        self.broker = broker
        self.risk = risk
        self.calib = calib
        self.store = store
        self.regime = regime
        self.strategy = strategy
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
                sig = compute_signal(klines, sym, self.timeframe, strategy=self.strategy)
            except Exception as exc:
                results.append(CycleResult(sym, now, "ERR", 0.0, "flat", "REJECTED",
                                           reason=f"data error: {exc}"))
                continue

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

            # Stop: ~2.5% below entry (simple initial risk). Deterministic.
            stop = entry * (1 - 0.025)

            # Existing open risk (for the cap): account for ALL open lots.
            open_pos = [
                (p.entry_price, p.stop_loss, p.quantity)
                for p in self.broker.lots
            ]
            rd = self.risk.size_position(equity, entry, stop, open_pos)
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
            self.store.snapshot_equity(self.broker.equity(px_now), baseline=None)

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
