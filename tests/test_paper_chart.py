"""Paper chart: 5m candles, per-lot entry/stop/TP, numbered pyramids."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[1]


def _lot(ticker: str, lot_id: int, entry: float, stop: float) -> dict:
    return {
        "lot_id": lot_id,
        "ticker": ticker,
        "quantity": 0.01,
        "entry_price": entry,
        "stop_loss": stop,
        "entry_fee": 0.1,
        "entry_condition": "test_long",
    }


class TakeProfitMatchesLiveTests(unittest.TestCase):
    def test_two_to_one_vs_stop_not_five_percent(self):
        from hedge_fund.risk.managed import RiskManager
        from hedge_fund.risk.rm_v1 import TAKE_PROFIT_RR
        from hedge_fund.web.live import take_profit_price

        entry, stop = 100_000.0, 97_000.0
        expected = RiskManager.take_profit_price(None, entry, stop, TAKE_PROFIT_RR)
        self.assertAlmostEqual(take_profit_price(entry, stop), expected)
        self.assertAlmostEqual(expected, 106_000.0)
        self.assertNotAlmostEqual(expected, entry * 1.05)


class LiveLotsForChartTests(unittest.TestCase):
    def test_two_btc_lots_are_two_entries_stops_tps(self):
        from hedge_fund.trading.store import TradeStore
        from hedge_fund.web.live import live_preview, take_profit_price

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "trades_pyramid.sqlite"
            store = TradeStore(db)
            store.save_account_state({
                "broker": {
                    "cash": 8_000.0,
                    "lots": [
                        _lot("BTC/USDT", 1, 100_000.0, 97_000.0),
                        _lot("BTC/USDT", 2, 101_000.0, 98_000.0),
                    ],
                },
            })
            store.open_trade("BTC/USDT", "5m", "test_long", 0.6, 100_000.0, 0.01, 0.1, lot_id=1)
            store.open_trade("BTC/USDT", "5m", "test_long", 0.6, 101_000.0, 0.01, 0.1, lot_id=2)
            with patch(
                "hedge_fund.web.live.live_prices",
                return_value={"BTC/USDT": 102_000.0, "ETH/USDT": 1.0},
            ):
                prev = live_preview(str(db))
        lots = prev["lots"]
        self.assertEqual(len(lots), 2)
        self.assertEqual(prev["positions"][0]["lot_count"], 2)
        self.assertEqual(len(prev["positions"][0]["lots"]), 2)
        ids = {l["lot_id"] for l in lots}
        self.assertEqual(ids, {1, 2})
        by_id = {l["lot_id"]: l for l in lots}
        self.assertAlmostEqual(by_id[1]["entry"], 100_000.0)
        self.assertAlmostEqual(by_id[1]["stop"], 97_000.0)
        self.assertAlmostEqual(by_id[1]["take_profit"], take_profit_price(100_000.0, 97_000.0))
        self.assertAlmostEqual(by_id[2]["entry"], 101_000.0)
        self.assertAlmostEqual(by_id[2]["stop"], 98_000.0)
        self.assertAlmostEqual(by_id[2]["take_profit"], take_profit_price(101_000.0, 98_000.0))
        self.assertIsNotNone(by_id[1]["entry_ts"])
        self.assertNotEqual(by_id[1]["take_profit"], by_id[2]["take_profit"])

    def test_eth_lot_does_not_appear_in_btc_symbol_row_lots(self):
        from hedge_fund.trading.store import TradeStore
        from hedge_fund.web.live import live_preview

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "trades_mix.sqlite"
            store = TradeStore(db)
            store.save_account_state({
                "broker": {
                    "cash": 8_000.0,
                    "lots": [
                        _lot("BTC/USDT", 1, 100_000.0, 97_000.0),
                        _lot("ETH/USDT", 2, 3_000.0, 2_900.0),
                    ],
                },
            })
            with patch(
                "hedge_fund.web.live.live_prices",
                return_value={"BTC/USDT": 102_000.0, "ETH/USDT": 3_100.0},
            ):
                prev = live_preview(str(db))
        btc = [p for p in prev["positions"] if p["symbol"] == "BTC/USDT"][0]
        eth = [p for p in prev["positions"] if p["symbol"] == "ETH/USDT"][0]
        self.assertEqual([l["lot_id"] for l in btc["lots"]], [1])
        self.assertEqual([l["lot_id"] for l in eth["lots"]], [2])
        self.assertEqual(len(prev["lots"]), 2)


class CandlesEndpointTests(unittest.TestCase):
    def setUp(self):
        from hedge_fund.web import candles as candles_mod

        candles_mod._CACHE.clear()

    def test_rejects_unknown_symbol_and_4h(self):
        from hedge_fund.web.candles import CandleRequestError, candles_payload

        with self.assertRaises(CandleRequestError):
            candles_payload("DOGE/USDT", "5m")
        with self.assertRaises(CandleRequestError):
            candles_payload("BTC/USDT", "4h")

    def test_accepts_hyphen_and_uses_public_klines(self):
        from hedge_fund.data.binance import Candle
        from hedge_fund.web.candles import candles_payload

        src = MagicMock()
        src.fetch_klines.return_value = [
            Candle(ts=1_700_000_000_000, open=1, high=2, low=0.5, close=1.5, volume=10),
        ]
        out = candles_payload("btc-usdt", "5m", 10, source=src)
        src.fetch_klines.assert_called_once_with("BTC/USDT", timeframe="5m", limit=10)
        self.assertEqual(out["symbol"], "BTC/USDT")
        self.assertEqual(out["timeframe"], "5m")
        self.assertEqual(out["source"], "binance_public")
        self.assertTrue(out["paper_only"])
        self.assertEqual(out["candles"][0]["t"], 1_700_000_000_000)
        self.assertEqual(out["candles"][0]["c"], 1.5)

    def test_falls_back_to_binanceus_when_dot_com_blocked(self):
        from hedge_fund.data.binance import Candle
        from hedge_fund.web.candles import _public_klines

        good = MagicMock()
        good.fetch_klines.return_value = [
            Candle(ts=1, open=1, high=1, low=1, close=1, volume=1),
        ]
        blocked = MagicMock()
        blocked.fetch_klines.side_effect = RuntimeError("451 restricted location")
        with patch("hedge_fund.data.binance.CcxtSource", side_effect=[blocked, good]) as ctor:
            bars = _public_klines("BTC/USDT", "5m", 5)
        self.assertEqual(len(bars), 1)
        self.assertEqual(ctor.call_count, 2)
        self.assertEqual(ctor.call_args_list[0].kwargs["exchange_id"], "binance")
        self.assertEqual(ctor.call_args_list[1].kwargs["exchange_id"], "binanceus")

    def test_server_registers_candles_route(self):
        src = (REPO / "hedge_fund" / "web" / "server.py").read_text()
        self.assertIn('route == "/api/candles"', src)
        self.assertIn("candles_payload", src)
        self.assertIn("CcxtSource", (REPO / "hedge_fund" / "web" / "candles.py").read_text())
        self.assertNotIn("live broker", (REPO / "hedge_fund" / "web" / "candles.py").read_text().lower())
        self.assertIn("binanceus", (REPO / "hedge_fund" / "web" / "candles.py").read_text())


class TradesFilterTests(unittest.TestCase):
    def test_symbol_filter_keeps_lot_id_and_drops_other_coin(self):
        from hedge_fund.trading.store import TradeStore
        from hedge_fund.web.server import build_trades

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            st = TradeStore(root / "trades_champ.sqlite")
            a = st.open_trade("BTC/USDT", "5m", "long", 0.6, 100.0, 0.01, 0.01, lot_id=11)
            b = st.open_trade("ETH/USDT", "5m", "long", 0.6, 3.0, 0.01, 0.01, lot_id=22)
            st.close_trade(a, 110.0, "take_profit", 0.01, 1.0, 0.1, 1)
            st.close_trade(b, 2.5, "stop_loss", 0.01, -0.5, -0.1, 0)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                btc = build_trades("BTC/USDT", limit=50)
                eth = build_trades("ETH/USDT", limit=50)
        self.assertEqual(len(btc), 1)
        self.assertEqual(btc[0]["symbol"], "BTC/USDT")
        self.assertEqual(btc[0]["lot_id"], 11)
        self.assertEqual(eth[0]["lot_id"], 22)


class ChartUiTests(unittest.TestCase):
    def test_chart_page_in_bottom_nav_with_btc_eth_toggle(self):
        app = (REPO / "frontend" / "src" / "App.tsx").read_text()
        self.assertIn('to: "/chart"', app)
        self.assertIn('label: "Chart"', app)
        self.assertIn("grid-cols-5", app)
        self.assertIn('path="/chart"', app)
        chart = (REPO / "frontend" / "src" / "pages" / "Chart.tsx").read_text()
        self.assertIn("useState", chart)
        self.assertIn("BTC/USDT", chart)
        self.assertIn("ETH/USDT", chart)
        self.assertIn("aria-pressed", chart)
        self.assertIn("min-h-12", chart)
        self.assertIn("Trade {n} open", chart)
        self.assertIn("${n} close", chart)
        self.assertIn("Take-profit", chart)
        self.assertIn("2:1", chart)
        self.assertIn("paper", chart.lower())
        self.assertNotIn("READY_FOR_LIVE", chart)
        numbering = (REPO / "frontend" / "src" / "chart" / "numberTrades.ts").read_text()
        self.assertIn("TAKE_PROFIT_RR = 2", numbering)
        self.assertIn("function numberOpenLots", numbering)
        self.assertIn("function numberClosedTrades", numbering)
        paper = (REPO / "frontend" / "src" / "chart" / "PaperChart.tsx").read_text()
        self.assertIn("lightweight-charts", paper)
        self.assertIn("${n} entry", paper)
        self.assertIn("${n} stop", paper)
        self.assertIn("${n} TP", paper)
        self.assertIn("${n} open", paper)
        self.assertIn("${n} close", paper)
        self.assertIn("min-h-[280px]", paper)
        bar = (REPO / "frontend" / "src" / "status" / "StatusBar.tsx").read_text()
        self.assertIn("Running now", bar)
        self.assertIn("In progress", bar)

    def test_positions_not_replaced_by_chart(self):
        positions = (REPO / "frontend" / "src" / "pages" / "Positions.tsx").read_text()
        self.assertIn("Open lots", positions)
        self.assertIn("Closed paper trades", positions)
        self.assertNotIn("lightweight-charts", positions)


if __name__ == "__main__":
    unittest.main()
