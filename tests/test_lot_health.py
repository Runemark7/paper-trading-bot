"""Per-lot signal (on / would_exit / unknown) and path (near_stop / mid / near_tp)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from hedge_fund.data.binance import Candle
from hedge_fund.web.lot_health import (
    annotate_open_lot,
    fetch_signal_closes,
    lot_signal,
    mom_or_dip_gate,
    path_in_trade,
    strategy_from_lot,
)

REPO = Path(__file__).resolve().parents[1]


def _candles(closes: list[float]) -> list[Candle]:
    return [
        Candle(
            ts=1_700_000_000_000 + i * 300_000,
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1.0,
        )
        for i, c in enumerate(closes)
    ]


def _lot_row(**kwargs) -> dict:
    row = {
        "lot_id": 1,
        "symbol": "BTC/USDT",
        "entry": 100.0,
        "stop": 90.0,
        "take_profit": 120.0,
        "quantity": 0.01,
        "condition": "mom_12b_gt2pc_long",
        "account": "mom_12b_gt2pc",
    }
    row.update(kwargs)
    return row


def _broker_lot(ticker: str, lot_id: int, entry: float, stop: float, condition: str) -> dict:
    return {
        "lot_id": lot_id,
        "ticker": ticker,
        "quantity": 0.01,
        "entry_price": entry,
        "stop_loss": stop,
        "entry_fee": 0.1,
        "entry_condition": condition,
    }


class PathInTradeTests(unittest.TestCase):
    def test_price_at_stop_is_near_stop(self):
        path, pct = path_in_trade(90.0, 90.0, 120.0)
        self.assertEqual(path, "near_stop")
        self.assertAlmostEqual(pct, 0.0)

    def test_price_at_take_profit_is_near_tp(self):
        path, pct = path_in_trade(120.0, 90.0, 120.0)
        self.assertEqual(path, "near_tp")
        self.assertAlmostEqual(pct, 1.0)

    def test_midway_is_mid(self):
        path, pct = path_in_trade(105.0, 90.0, 120.0)
        self.assertEqual(path, "mid")
        self.assertAlmostEqual(pct, 0.5)

    def test_bottom_thirty_percent_is_near_stop(self):
        # span 30; 30% = +9 → mark 99
        path, pct = path_in_trade(99.0, 90.0, 120.0)
        self.assertEqual(path, "near_stop")
        self.assertAlmostEqual(pct, 0.3)

    def test_just_inside_mid_band(self):
        path, _ = path_in_trade(99.1, 90.0, 120.0)
        self.assertEqual(path, "mid")

    def test_past_stop_still_near_stop(self):
        path, pct = path_in_trade(80.0, 90.0, 120.0)
        self.assertEqual(path, "near_stop")
        self.assertAlmostEqual(pct, 0.0)

    def test_past_tp_still_near_tp(self):
        path, pct = path_in_trade(130.0, 90.0, 120.0)
        self.assertEqual(path, "near_tp")
        self.assertAlmostEqual(pct, 1.0)


class SignalPredicateTests(unittest.TestCase):
    def test_predicate_true_is_on(self):
        # mom_12b_gt2pc: last/prev-1 > 0.02. 100 → 103 over 12 bars = +3%.
        closes = [100.0] * 12 + [103.0]
        self.assertEqual(lot_signal("mom_12b_gt2pc", _candles(closes), "BTC/USDT"), "on")

    def test_predicate_false_is_would_exit(self):
        closes = [100.0] * 12 + [100.5]  # +0.5% < 2%
        self.assertEqual(lot_signal("mom_12b_gt2pc", _candles(closes), "BTC/USDT"), "would_exit")

    def test_missing_klines_is_unknown(self):
        self.assertEqual(lot_signal("mom_12b_gt2pc", None, "BTC/USDT"), "unknown")
        self.assertEqual(lot_signal("mom_12b_gt2pc", [], "BTC/USDT"), "unknown")
        self.assertEqual(lot_signal("mom_12b_gt2pc", _candles([100.0]), "BTC/USDT"), "unknown")

    def test_missing_strategy_is_unknown_even_with_klines(self):
        self.assertEqual(lot_signal(None, _candles([100.0] * 20), "BTC/USDT"), "unknown")


class StrategyFromLotTests(unittest.TestCase):
    def test_calibration_key_strips_to_strategy(self):
        self.assertEqual(
            strategy_from_lot("BTC/USDT|5m|sma_stack_long"),
            "sma_stack",
        )

    def test_short_tag_and_account_fallback(self):
        self.assertEqual(strategy_from_lot("dip_6b_lt1pc_long"), "dip_6b_lt1pc")
        self.assertEqual(strategy_from_lot(None, "trades_sma_stack"), "sma_stack")


class GateSnapshotTests(unittest.TestCase):
    def test_dip_rule_reports_return_vs_threshold(self):
        closes = [100.0] * 24 + [99.6]  # 24-bar −0.4% vs −1%
        gate = mom_or_dip_gate("dip_24b_lt1pc", closes)
        self.assertIsNotNone(gate)
        self.assertEqual(gate["kind"], "dip")
        self.assertEqual(gate["lookback"], 24)
        self.assertAlmostEqual(gate["ret"], -0.004, places=4)
        self.assertAlmostEqual(gate["threshold"], -0.01)

    def test_sma_stack_has_no_gate(self):
        self.assertIsNone(mom_or_dip_gate("sma_stack", [100.0] * 60))
        self.assertIsNone(mom_or_dip_gate("sma_stack_7_25_50", [100.0] * 60))


class AnnotateAndLivePreviewTests(unittest.TestCase):
    def test_missing_klines_unknown_signal_path_from_mark(self):
        row = annotate_open_lot(
            _lot_row(stop=90.0, take_profit=120.0),
            mark=90.0,
            candles=None,
        )
        self.assertEqual(row["signal"], "unknown")
        self.assertEqual(row["path"], "near_stop")
        self.assertAlmostEqual(row["path_pct"], 0.0)
        self.assertNotIn("gate", row)
        self.assertNotIn("strength", row)

    def test_two_pyramid_lots_keep_their_own_path(self):
        # Same mark, different stops/TPs — honest per lot, not an average.
        near = annotate_open_lot(
            _lot_row(lot_id=1, stop=100.0, take_profit=110.0, entry=103.0),
            mark=101.0,
            candles=None,
        )
        far = annotate_open_lot(
            _lot_row(lot_id=2, stop=90.0, take_profit=120.0, entry=100.0),
            mark=101.0,
            candles=None,
        )
        self.assertEqual(near["path"], "near_stop")
        self.assertEqual(far["path"], "mid")

    def test_live_preview_without_closes_is_unknown_with_path(self):
        from hedge_fund.trading.store import TradeStore
        from hedge_fund.web.live import live_preview, take_profit_price

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "trades_mom_12b_gt2pc.sqlite"
            store = TradeStore(db)
            store.save_account_state({
                "broker": {
                    "cash": 8_000.0,
                    "lots": [
                        _broker_lot("BTC/USDT", 1, 100.0, 90.0, "BTC/USDT|5m|mom_12b_gt2pc_long"),
                    ],
                },
            })
            with patch(
                "hedge_fund.web.live.live_prices",
                return_value={"BTC/USDT": 90.0, "ETH/USDT": 1.0},
            ):
                prev = live_preview(str(db))
        lot = prev["lots"][0]
        self.assertEqual(lot["signal"], "unknown")
        self.assertEqual(lot["path"], "near_stop")
        self.assertAlmostEqual(lot["path_pct"], 0.0)
        self.assertAlmostEqual(lot["take_profit"], take_profit_price(100.0, 90.0))
        self.assertAlmostEqual(lot["current"], 90.0)

    def test_live_preview_with_closes_sets_on_and_would_exit(self):
        from hedge_fund.trading.store import TradeStore
        from hedge_fund.web.live import live_preview

        on_closes = _candles([100.0] * 12 + [103.0])
        off_closes = _candles([100.0] * 12 + [100.5])
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "trades_mom_12b_gt2pc.sqlite"
            TradeStore(db).save_account_state({
                "broker": {
                    "cash": 8_000.0,
                    "lots": [
                        _broker_lot("BTC/USDT", 1, 100.0, 90.0, "BTC/USDT|5m|mom_12b_gt2pc_long"),
                    ],
                },
            })
            prices = {"BTC/USDT": 105.0, "ETH/USDT": 1.0}
            with patch("hedge_fund.web.live.live_prices", return_value=prices):
                on = live_preview(str(db), closes_by_symbol={"BTC/USDT": on_closes})
            with patch("hedge_fund.web.live.live_prices", return_value=prices):
                off = live_preview(str(db), closes_by_symbol={"BTC/USDT": off_closes})
        self.assertEqual(on["lots"][0]["signal"], "on")
        self.assertEqual(off["lots"][0]["signal"], "would_exit")
        self.assertEqual(on["lots"][0]["path"], "mid")
        self.assertIn("gate", on["lots"][0])
        self.assertEqual(on["lots"][0]["gate"]["kind"], "mom")

    def test_fetch_signal_closes_reuses_candles_cache_not_full_window(self):
        from hedge_fund.web import lot_health as lh

        lh._SIGNAL_CACHE["ts"] = 0.0
        lh._SIGNAL_CACHE["closes"] = {}
        bars = [
            {"t": 1, "o": 1, "h": 1, "l": 1, "c": 100.0, "v": 1},
            {"t": 2, "o": 1, "h": 1, "l": 1, "c": 101.0, "v": 1},
        ]

        def _recent(symbol, timeframe, limit):
            self.assertLessEqual(limit, 200)
            if symbol == "ETH/USDT":
                raise RuntimeError("no eth")
            return bars

        with patch("hedge_fund.web.candles.candles_payload") as full:
            with patch("hedge_fund.web.candles.try_recent_candles", side_effect=_recent) as fetch:
                out = fetch_signal_closes()
        full.assert_not_called()
        self.assertIn("BTC/USDT", out)
        self.assertNotIn("ETH/USDT", out)
        self.assertEqual(len(out["BTC/USDT"]), 2)
        self.assertEqual(fetch.call_count, 2)
        for c in fetch.call_args_list:
            self.assertEqual(c.args[2], 200)

    def test_fetch_signal_closes_skips_when_venue_busy(self):
        from hedge_fund.web import candles as candles_mod
        from hedge_fund.web import lot_health as lh

        lh._SIGNAL_CACHE["ts"] = 0.0
        lh._SIGNAL_CACHE["closes"] = {}
        candles_mod._CACHE.clear()
        candles_mod._SOURCES["binance"] = MagicMock()
        candles_mod._SOURCES["binanceus"] = MagicMock()
        candles_mod._venue_lock("binance").acquire()
        candles_mod._venue_lock("binanceus").acquire()
        try:
            out = fetch_signal_closes()
        finally:
            candles_mod._venue_lock("binance").release()
            candles_mod._venue_lock("binanceus").release()
        self.assertEqual(out, {})

    def test_api_live_fetches_klines_once(self):
        src = (REPO / "hedge_fund" / "web" / "server.py").read_text()
        live_route = src.split('route == "/api/live"')[1].split("elif route")[0]
        self.assertIn("fetch_signal_closes", live_route)
        self.assertIn("closes_by_symbol=closes", live_route)
        self.assertIn("from hedge_fund.web.lot_health import fetch_signal_closes", live_route)


class LotHealthUiTests(unittest.TestCase):
    def test_expanded_champion_and_positions_show_per_lot_chips(self):
        champs = (REPO / "frontend" / "src" / "pages" / "Champions.tsx").read_text()
        detail = (REPO / "frontend" / "src" / "pages" / "ChampionDetail.tsx").read_text()
        lots_ui = (REPO / "frontend" / "src" / "status" / "ChampionLots.tsx").read_text()
        positions = (REPO / "frontend" / "src" / "pages" / "Positions.tsx").read_text()
        tape = (REPO / "frontend" / "src" / "chart" / "ChampionTape.tsx").read_text()
        types = (REPO / "frontend" / "src" / "api" / "types.ts").read_text()
        fmt = (REPO / "frontend" / "src" / "status" / "format.ts").read_text()
        self.assertIn("openLotsForChampion", champs)
        self.assertIn("LotHealthSummaryChips", champs)
        list_body = champs.split("Graduated paper")[0]
        self.assertIn("LotHealthSummaryChips", list_body)
        self.assertIn("LotHealthChips", lots_ui)
        self.assertIn("not a third strategy state", lots_ui)
        self.assertIn("openLotsForChampion", detail)
        self.assertIn("flatOpenLots", positions)
        self.assertIn("LotHealthChips", positions)
        self.assertIn("not a third strategy state", positions)
        self.assertIn("LotHealthChips", tape)
        self.assertIn("Trade {n} open", tape)
        self.assertIn('signal?: LotSignal | string', types)
        self.assertIn('path?: LotPath | string', types)
        self.assertIn('path_pct?: number', types)
        self.assertIn('export type LotSignal = "on" | "would_exit" | "unknown"', types)
        self.assertIn('export type LotPath = "near_stop" | "mid" | "near_tp"', types)
        self.assertIn("function openLotsForChampion", fmt)
        self.assertIn('"signal on"', fmt)
        self.assertIn('"would exit"', fmt)
        self.assertIn('"near stop"', fmt)
        self.assertIn('"near TP"', fmt)
        self.assertIn("flex-wrap", (REPO / "frontend" / "src" / "status" / "LotHealth.tsx").read_text())
        self.assertIn("shrink-0", (REPO / "frontend" / "src" / "status" / "LotHealth.tsx").read_text())

    def test_collapsed_card_does_not_clutter_with_mid_on_counts(self):
        health = (REPO / "frontend" / "src" / "status" / "LotHealth.tsx").read_text()
        self.assertIn("would exit", health)
        self.assertIn("near stop", health)
        self.assertIn("near TP", health)
        summary = health.split("LotHealthSummaryChips")[1]
        self.assertNotIn("signal on", summary)
        self.assertNotIn(">mid<", summary.lower())


if __name__ == "__main__":
    unittest.main()
