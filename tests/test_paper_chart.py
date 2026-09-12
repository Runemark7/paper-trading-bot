"""Paper chart: 5m candles, per-lot entry/stop/TP, numbered pyramids."""
from __future__ import annotations

import json
import os
import tempfile
import time
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


TF_MS = 5 * 60 * 1000
BARS_PER_DAY = 288
DEFAULT_CANDLE_BARS = 3 * BARS_PER_DAY  # 864
MAX_CANDLE_BARS = 7 * BARS_PER_DAY  # 2016
ENTRY_PAD_BARS = 12


def _one_bar():
    from hedge_fund.data.binance import Candle

    return Candle(ts=1_700_000_000_000, open=1, high=2, low=0.5, close=1.5, volume=10)


def _now_aligned_ms() -> int:
    now = int(time.time() * 1000)
    return now - (now % TF_MS)


class FakeVenue:
    """Public klines with unique timestamps. Honors since; never new-ups ccxt."""

    def __init__(self, n=4000, end_ts=None):
        self.step = TF_MS
        self.end_ts = _now_aligned_ms() if end_ts is None else end_ts
        self.start_ts = self.end_ts - (n - 1) * self.step
        self.calls: list[dict] = []

    def fetch_klines(self, symbol, timeframe="5m", limit=200, since=None):
        from hedge_fund.data.binance import Candle

        self.calls.append({"symbol": symbol, "timeframe": timeframe, "limit": limit, "since": since})
        if since is None:
            start = self.end_ts - (limit - 1) * self.step
        else:
            start = since
            rem = (start - self.start_ts) % self.step
            if rem:
                start += self.step - rem
            start = max(start, self.start_ts)
        out = []
        t = start
        while len(out) < limit and t <= self.end_ts:
            out.append(Candle(ts=t, open=1, high=2, low=0.5, close=1.5, volume=10))
            t += self.step
        if not out:
            raise RuntimeError("no klines")
        return out


def _iso(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _candle_limit_for_entries(entry_times, now_ms: int) -> int:
    """Mirror frontend candleLimitForEntries. Null/empty times are skipped, not invented."""
    earliest = None
    for iso in entry_times:
        if not iso:
            continue
        try:
            ms = int(datetime_parse(iso))
        except (TypeError, ValueError):
            continue
        if earliest is None or ms < earliest:
            earliest = ms
    if earliest is None:
        return DEFAULT_CANDLE_BARS
    bars = -(-(now_ms - earliest) // TF_MS) + ENTRY_PAD_BARS  # ceil
    return min(MAX_CANDLE_BARS, max(DEFAULT_CANDLE_BARS, bars))


def datetime_parse(iso: str) -> int:
    from datetime import datetime, timezone

    raw = iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


class CandlesEndpointTests(unittest.TestCase):
    def setUp(self):
        from hedge_fund.web import candles as candles_mod

        candles_mod._CACHE.clear()
        candles_mod._SOURCES.clear()
        candles_mod._VENUE_LOCKS.clear()

    def test_rejects_unknown_symbol_and_4h(self):
        from hedge_fund.web.candles import CandleRequestError, candles_payload

        with self.assertRaises(CandleRequestError):
            candles_payload("DOGE/USDT", "5m")
        with self.assertRaises(CandleRequestError):
            candles_payload("BTC/USDT", "4h")

    def test_limit_50_ok(self):
        from hedge_fund.web.candles import candles_payload

        src = MagicMock()
        src.fetch_klines.return_value = [_one_bar()]
        out = candles_payload("ETH/USDT", "5m", 50, source=src)
        src.fetch_klines.assert_called_once_with("ETH/USDT", timeframe="5m", limit=50)
        self.assertEqual(len(out["candles"]), 1)
        self.assertEqual(out["symbol"], "ETH/USDT")
        self.assertTrue(out["paper_only"])

    def test_default_is_three_days_cap_is_seven(self):
        from hedge_fund.web.candles import (
            BARS_PER_DAY,
            CHUNK_SIZE,
            DEFAULT_LIMIT,
            MAX_LIMIT,
            candles_payload,
        )

        self.assertEqual(BARS_PER_DAY, 288)
        self.assertEqual(DEFAULT_LIMIT, 3 * BARS_PER_DAY)
        self.assertEqual(MAX_LIMIT, 7 * BARS_PER_DAY)
        self.assertEqual(CHUNK_SIZE, 200)
        self.assertLessEqual(CHUNK_SIZE, 200)
        self.assertGreater(MAX_LIMIT, DEFAULT_LIMIT)
        self.assertGreater(MAX_LIMIT, 500)
        src = FakeVenue()
        out = candles_payload("BTC/USDT", "5m", None, source=src)
        self.assertEqual(len(out["candles"]), DEFAULT_LIMIT)
        for call in src.calls:
            self.assertLessEqual(call["limit"], CHUNK_SIZE)

    def test_limit_500_is_chunked_not_oneshot(self):
        from hedge_fund.web.candles import CHUNK_SIZE, candles_payload

        src = FakeVenue()
        out = candles_payload("BTC/USDT", "5m", 500, source=src)
        self.assertEqual(len(out["candles"]), 500)
        self.assertGreater(len(src.calls), 1)
        for call in src.calls:
            self.assertLessEqual(call["limit"], CHUNK_SIZE)
            self.assertLessEqual(call["limit"], 200)
        ts = [c["t"] for c in out["candles"]]
        self.assertEqual(ts, sorted(ts))
        self.assertEqual(len(set(ts)), 500)

    def test_chunked_fetch_does_not_construct_ccxt_per_chunk(self):
        from hedge_fund.web.candles import CHUNK_SIZE, candles_payload

        venue = FakeVenue()
        with patch("hedge_fund.data.binance.CcxtSource", return_value=venue) as ctor:
            out = candles_payload("BTC/USDT", "5m", 500)
        self.assertEqual(ctor.call_count, 1)
        self.assertGreater(len(venue.calls), 1)
        for call in venue.calls:
            self.assertLessEqual(call["limit"], CHUNK_SIZE)
        self.assertEqual(len(out["candles"]), 500)

    def test_since_older_than_three_days_extends_up_to_seven(self):
        from hedge_fund.web.candles import DEFAULT_LIMIT, MAX_LIMIT, candles_payload

        src = FakeVenue()
        now = _now_aligned_ms()
        since = now - 4 * 24 * 60 * 60 * 1000
        out = candles_payload("ETH/USDT", "5m", None, source=src, since=since)
        self.assertGreater(len(out["candles"]), DEFAULT_LIMIT)
        self.assertLessEqual(len(out["candles"]), MAX_LIMIT)
        self.assertLessEqual(out["candles"][0]["t"], since)

    def test_ten_day_since_caps_at_seven_days(self):
        from hedge_fund.web.candles import MAX_LIMIT, candles_payload

        src = FakeVenue(n=5000)
        now = _now_aligned_ms()
        since = now - 10 * 24 * 60 * 60 * 1000
        out = candles_payload("BTC/USDT", "5m", 5000, source=src, since=since)
        self.assertEqual(len(out["candles"]), MAX_LIMIT)
        self.assertGreater(out["candles"][0]["t"], since)
        for call in src.calls:
            self.assertLessEqual(call["limit"], 200)

    def test_bad_since_is_400(self):
        from hedge_fund.web.candles import CandleRequestError, candles_payload

        with self.assertRaises(CandleRequestError):
            candles_payload("BTC/USDT", "5m", 50, since="yesterday")
        with self.assertRaises(CandleRequestError):
            candles_payload("BTC/USDT", "5m", 50, since=-1)

    def test_accepts_hyphen_and_uses_public_klines(self):
        from hedge_fund.web.candles import candles_payload

        src = MagicMock()
        src.fetch_klines.return_value = [_one_bar()]
        out = candles_payload("btc-usdt", "5m", 10, source=src)
        src.fetch_klines.assert_called_once_with("BTC/USDT", timeframe="5m", limit=10)
        self.assertEqual(out["symbol"], "BTC/USDT")
        self.assertEqual(out["timeframe"], "5m")
        self.assertEqual(out["source"], "binance_public")
        self.assertTrue(out["paper_only"])
        self.assertEqual(out["candles"][0]["t"], 1_700_000_000_000)
        self.assertEqual(out["candles"][0]["c"], 1.5)

    def test_falls_back_to_binanceus_when_dot_com_blocked(self):
        from hedge_fund.web.candles import _public_klines

        good = MagicMock()
        good.fetch_klines.return_value = [_one_bar()]
        blocked = MagicMock()
        blocked.fetch_klines.side_effect = RuntimeError("451 restricted location")
        with patch("hedge_fund.data.binance.CcxtSource", side_effect=[blocked, good]) as ctor:
            bars = _public_klines("BTC/USDT", "5m", 5)
        self.assertEqual(len(bars), 1)
        self.assertEqual(ctor.call_count, 2)
        self.assertEqual(ctor.call_args_list[0].kwargs["exchange_id"], "binance")
        self.assertEqual(ctor.call_args_list[1].kwargs["exchange_id"], "binanceus")

    def test_both_venues_fail_as_fetch_error_not_502(self):
        from hedge_fund.web.candles import CandleFetchError, candles_payload

        dead = MagicMock()
        dead.fetch_klines.side_effect = TimeoutError("binance timed out")
        with patch("hedge_fund.data.binance.CcxtSource", return_value=dead):
            with self.assertRaises(CandleFetchError) as ctx:
                candles_payload("BTC/USDT", "5m", 50)
        self.assertEqual(ctx.exception.status, 504)

    def test_handler_catches_fetch_errors_as_json_503(self):
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.error import HTTPError
        from urllib.request import urlopen

        from hedge_fund.web.server import Handler

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.handle_request, daemon=True)
        thread.start()
        try:
            with patch(
                "hedge_fund.web.candles.candles_payload",
                side_effect=RuntimeError("ccxt boom"),
            ):
                try:
                    urlopen(
                        f"http://127.0.0.1:{port}/api/candles?symbol=BTC/USDT&timeframe=5m&limit=50",
                        timeout=3,
                    )
                    self.fail("expected HTTPError")
                except HTTPError as err:
                    self.assertEqual(err.code, 503)
                    self.assertNotEqual(err.code, 502)
                    body = json.loads(err.read().decode())
                    self.assertIn("error", body)
                    self.assertIn("ccxt boom", body["error"])
                    self.assertTrue(body.get("paper_only"))
        finally:
            thread.join(timeout=3)
            httpd.server_close()

    def test_handler_limit_50_ok_json(self):
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import urlopen

        from hedge_fund.web.server import Handler

        payload = {
            "symbol": "ETH/USDT",
            "timeframe": "5m",
            "candles": [{"t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
            "as_of": "now",
            "source": "binance_public",
            "paper_only": True,
        }
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.handle_request, daemon=True)
        thread.start()
        try:
            with patch(
                "hedge_fund.web.candles.candles_payload",
                return_value=payload,
            ) as fetch:
                raw = urlopen(
                    f"http://127.0.0.1:{port}/api/candles?symbol=ETH/USDT&timeframe=5m&limit=50",
                    timeout=3,
                )
                body = json.loads(raw.read().decode())
                self.assertEqual(raw.status, 200)
                self.assertEqual(len(body["candles"]), 1)
                fetch.assert_called_once()
                self.assertEqual(fetch.call_args.args[2], "50")
        finally:
            thread.join(timeout=3)
            httpd.server_close()

    def test_server_registers_candles_route(self):
        src = (REPO / "hedge_fund" / "web" / "server.py").read_text()
        self.assertIn('route == "/api/candles"', src)
        self.assertIn("candles_payload", src)
        self.assertIn("CandleFetchError", src)
        self.assertIn("exc.status", src)
        candles_route = src.split('route == "/api/candles"')[1].split("elif route")[0]
        self.assertNotIn(", 502)", candles_route)
        self.assertIn(", 503)", candles_route)
        candles_py = (REPO / "hedge_fund" / "web" / "candles.py").read_text()
        self.assertIn("CcxtSource", candles_py)
        self.assertNotIn("live broker", candles_py.lower())
        self.assertIn("binanceus", candles_py)
        self.assertIn("CHUNK_SIZE = 200", candles_py)
        self.assertIn("DEFAULT_LIMIT = DEFAULT_DAYS * BARS_PER_DAY", candles_py)
        self.assertIn("MAX_LIMIT = MAX_DAYS * BARS_PER_DAY", candles_py)
        self.assertIn("since=qs.get(\"since\")", src)
        self.assertNotIn("MAX_LIMIT = 250", candles_py)
        self.assertNotIn("DEFAULT_LIMIT = 200", candles_py)
        self.assertIn("_fetch_last_n", candles_py)
        self.assertIn("_call_klines", candles_py)
        self.assertIn("FETCH_BUDGET", candles_py)
        self.assertIn("try_recent_candles", candles_py)
        self.assertIn("try_live_prices", candles_py)
        self.assertIn("SIGNAL_LIMIT = 200", candles_py)
        self.assertNotIn("with _SOURCE_LOCK:\n                return _fetch_last_n", candles_py)
        self.assertIn("blocking=False", candles_py)
        champs_route = src.split('route == "/api/champions"')[1].split("elif route")[0]
        self.assertNotIn("collect_live_results()", champs_route)
        self.assertIn("read_only=True", champs_route)
        self.assertIn("pool_status", champs_route)
        live_py = (REPO / "hedge_fund" / "web" / "live.py").read_text()
        self.assertIn("try_live_prices", live_py)
        self.assertNotIn("CcxtSource(exchange_id=", live_py)
        self.assertNotIn("from hedge_fund.data.binance import CcxtSource", live_py)
        self.assertNotIn("CcxtSource()", live_py)
        client = (REPO / "frontend" / "src" / "api" / "client.ts").read_text()
        self.assertIn("isTransientHttpError", client)
        self.assertIn("-> 502", client)
        self.assertIn("-> 503", client)
        main = (REPO / "frontend" / "src" / "main.tsx").read_text()
        self.assertIn("keepPreviousData", main)
        self.assertIn("retry: false", main)

    def test_paging_does_not_hold_source_lock(self):
        from hedge_fund.web import candles as c
        from hedge_fund.web.candles import candles_payload

        held = []
        real = c._call_klines

        def wrapped(*a, **k):
            held.append(c._SOURCE_LOCK.locked())
            return real(*a, **k)

        venue = FakeVenue()
        with patch.object(c, "_call_klines", wrapped):
            with patch("hedge_fund.data.binance.CcxtSource", return_value=venue):
                out = candles_payload("BTC/USDT", "5m", 500)
        self.assertEqual(len(out["candles"]), 500)
        self.assertGreater(len(held), 1)
        self.assertFalse(any(held))

    def test_try_recent_candles_uses_chart_cache(self):
        from hedge_fund.web import candles as c

        rows = [
            {"t": i, "o": 1, "h": 1, "l": 1, "c": 1.0, "v": 1}
            for i in range(40)
        ]
        c._CACHE[("BTC/USDT", "5m", 864)] = {
            "symbol": "BTC/USDT",
            "timeframe": "5m",
            "candles": rows,
            "_ts": time.time(),
        }
        with patch.object(c, "_public_klines", side_effect=AssertionError("must not fetch")):
            got = c.try_recent_candles("BTC/USDT", "5m", 200)
        self.assertEqual(got, rows[-200:] if len(rows) > 200 else rows)


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
        self.assertIn("grid-cols-6", app)
        self.assertIn('path="/chart"', app)
        chart = (REPO / "frontend" / "src" / "pages" / "Chart.tsx").read_text()
        tape = (REPO / "frontend" / "src" / "chart" / "ChampionTape.tsx").read_text()
        client = (REPO / "frontend" / "src" / "api" / "client.ts").read_text()
        self.assertIn("fetchChampions", chart)
        self.assertIn('aria-label="Champion"', chart)
        self.assertIn("min-h-12", chart)
        self.assertIn("defaultChampionName", chart)
        self.assertIn("<ChampionTape", chart)
        self.assertIn("Could not load /api/champions", chart)
        self.assertIn("showing last-known", chart)
        self.assertIn("openLotsByPair", chart)
        self.assertIn("BTC ${split.btc}", chart)
        self.assertIn("ETH ${split.eth}", chart)
        self.assertIn("BTC/USDT", tape)
        self.assertIn("ETH/USDT", tape)
        self.assertIn("aria-pressed", tape)
        self.assertIn("DEFAULT_CANDLE_BARS = 3 * BARS_PER_DAY", client)
        self.assertIn("MAX_CANDLE_BARS = 7 * BARS_PER_DAY", client)
        self.assertNotIn("CANDLE_LIMIT = 200", client)
        self.assertNotIn("limit = 500", client)
        self.assertNotIn("limit: 500", client)
        self.assertIn("candleLimitForEntries", tape)
        self.assertIn("api.candles(symbol, \"5m\", candleLimit)", tape)
        self.assertNotIn("CANDLE_LIMIT", tape)
        self.assertNotIn("limit = 500", tape)
        self.assertNotIn('"5m", 500', tape)
        self.assertNotIn('"4h"', tape)
        self.assertIn("7-day tape", tape)
        self.assertIn("lotsOlderThanTape", tape)
        self.assertIn("min-h-12", tape)
        self.assertIn("{n} lots", tape)
        self.assertIn("openLotsByPair", tape)
        self.assertIn("Could not load /api/candles", tape)
        self.assertIn("showing last-known tape", tape)
        self.assertIn("LotHealthChips", tape)
        self.assertIn("Trade {n} open", tape)
        self.assertIn("Trade {n} closed", tape)
        self.assertIn("2:1", tape)
        self.assertIn("paper", chart.lower())
        self.assertNotIn("READY_FOR_LIVE", chart)
        numbering = (REPO / "frontend" / "src" / "chart" / "numberTrades.ts").read_text()
        self.assertIn("TAKE_PROFIT_RR = 2", numbering)
        self.assertIn("function numberOpenLots", numbering)
        self.assertIn("function numberClosedTrades", numbering)
        self.assertIn("function lotsForChampionSymbol", numbering)
        self.assertIn("function closedTradesForChampionSymbol", numbering)
        self.assertIn("function defaultChampionName", numbering)
        self.assertIn("function candleLimitForEntries", numbering)
        self.assertIn("function lotsOlderThanTape", numbering)
        self.assertIn("if (!iso) continue", numbering)
        self.assertIn("accountsMatch(l.account, championName)", numbering)
        self.assertIn("accountsMatch(t.account, championName)", numbering)
        paper = (REPO / "frontend" / "src" / "chart" / "PaperChart.tsx").read_text()
        self.assertIn("lightweight-charts", paper)
        self.assertIn("axisLabelVisible: false", paper)
        self.assertNotIn("${n} entry", paper)
        self.assertNotIn("${n} stop", paper)
        self.assertNotIn("${n} TP", paper)
        self.assertNotIn("${n} open", paper)
        self.assertNotIn("${n} close", paper)
        self.assertIn("text: `${n}`", paper)
        self.assertIn("min-h-[280px]", paper)
        self.assertIn("compact", paper)
        bar = (REPO / "frontend" / "src" / "status" / "StatusBar.tsx").read_text()
        self.assertIn("Running now", bar)
        self.assertIn("In progress", bar)

    def test_positions_not_replaced_by_chart(self):
        positions = (REPO / "frontend" / "src" / "pages" / "Positions.tsx").read_text()
        self.assertIn("Open lots", positions)
        self.assertIn("Closed paper trades", positions)
        self.assertNotIn("lightweight-charts", positions)


def _account_slug(name: str) -> str:
    return name.replace("/", "_").replace(":", "_")


def _paper_account_keys(name: str) -> set[str]:
    stripped = name[len("trades_") :] if name.startswith("trades_") else name
    slug = _account_slug(name)
    stripped_slug = _account_slug(stripped)
    return {name, slug, stripped, stripped_slug, f"trades_{slug}", f"trades_{stripped_slug}"}


def _accounts_match(account: str | None, champion_name: str) -> bool:
    if not account:
        return False
    return bool(_paper_account_keys(account) & _paper_account_keys(champion_name))


def _lots_for_champion_symbol(lots: list[dict], champion: str, symbol: str) -> list[dict]:
    return [l for l in lots if l.get("symbol") == symbol and _accounts_match(l.get("account"), champion)]


def _default_champion_name(champs: list[dict]) -> str | None:
    if not champs:
        return None
    best = champs[0]
    for c in champs[1:]:
        if (c.get("open_lots") or 0) > (best.get("open_lots") or 0):
            best = c
    return best["name"]


class ChampionScopedChartTests(unittest.TestCase):
    def test_selecting_champion_a_drops_champion_b_btc_lots(self):
        lots = [
            {"account": "trades_alpha", "symbol": "BTC/USDT", "lot_id": 1},
            {"account": "trades_alpha", "symbol": "BTC/USDT", "lot_id": 2},
            {"account": "trades_bravo", "symbol": "BTC/USDT", "lot_id": 9},
            {"account": "trades_alpha", "symbol": "ETH/USDT", "lot_id": 3},
        ]
        alpha_btc = _lots_for_champion_symbol(lots, "alpha", "BTC/USDT")
        self.assertEqual([l["lot_id"] for l in alpha_btc], [1, 2])
        self.assertEqual([i for i, _ in enumerate(alpha_btc, 1)], [1, 2])
        bravo_btc = _lots_for_champion_symbol(lots, "bravo", "BTC/USDT")
        self.assertEqual([l["lot_id"] for l in bravo_btc], [9])
        self.assertNotIn("trades_bravo", [l["account"] for l in alpha_btc])
        self.assertEqual(_lots_for_champion_symbol(lots, "bravo", "ETH/USDT"), [])

    def test_two_lots_on_one_champion_are_trade_1_and_trade_2(self):
        lots = [
            {"account": "trades_pair", "symbol": "BTC/USDT", "lot_id": 4},
            {"account": "trades_pair", "symbol": "BTC/USDT", "lot_id": 5},
        ]
        numbered = [(i, l["lot_id"]) for i, l in enumerate(
            _lots_for_champion_symbol(lots, "pair", "BTC/USDT"), 1
        )]
        self.assertEqual(numbered, [(1, 4), (2, 5)])

    def test_default_champion_is_most_open_lots_else_first(self):
        self.assertEqual(
            _default_champion_name([
                {"name": "quiet", "open_lots": 0},
                {"name": "busy", "open_lots": 3},
                {"name": "also_busy", "open_lots": 3},
            ]),
            "busy",
        )
        self.assertEqual(
            _default_champion_name([
                {"name": "first", "open_lots": 0},
                {"name": "second", "open_lots": 0},
            ]),
            "first",
        )
        self.assertIsNone(_default_champion_name([]))
        numbering = (REPO / "frontend" / "src" / "chart" / "numberTrades.ts").read_text()
        self.assertIn("((c.open_lots ?? 0) > (best.open_lots ?? 0) ? c : best)", numbering)

    def test_closed_lots_have_no_stop_or_tp_price_lines(self):
        paper = (REPO / "frontend" / "src" / "chart" / "PaperChart.tsx").read_text()
        closed_block = paper.split("Closed lots:")[1]
        self.assertIn("shape: \"arrowDown\"", closed_block)
        self.assertNotIn("createPriceLine", closed_block)
        self.assertNotIn("lot.stop", closed_block)
        self.assertNotIn("lot.take_profit", closed_block)
        tape = (REPO / "frontend" / "src" / "chart" / "ChampionTape.tsx").read_text()
        self.assertIn("no stop/TP", tape)
        self.assertIn("Labels sit", tape)

    def test_champions_expanded_card_mounts_one_tape_not_twenty(self):
        champs = (REPO / "frontend" / "src" / "pages" / "Champions.tsx").read_text()
        self.assertIn("ChampionTape", champs)
        self.assertIn("championName={c.name}", champs)
        self.assertIn("compact", champs)
        open_idx = champs.index("{isOpen &&")
        tape_idx = champs.index("<ChampionTape", open_idx)
        self.assertGreater(tape_idx, open_idx)
        collapsed_render = champs.split("{isOpen &&")[0]
        self.assertNotIn("<ChampionTape", collapsed_render)
        grad = champs[champs.index("Graduated paper") :]
        self.assertNotIn("ChampionTape", grad)


class CandleWindowTests(unittest.TestCase):
    def test_default_three_days_when_no_entry_ts(self):
        now = 1_800_000_000_000
        self.assertEqual(_candle_limit_for_entries([], now), DEFAULT_CANDLE_BARS)
        self.assertEqual(_candle_limit_for_entries([None, ""], now), DEFAULT_CANDLE_BARS)
        numbering = (REPO / "frontend" / "src" / "chart" / "numberTrades.ts").read_text()
        self.assertIn("if (!iso) continue", numbering)
        self.assertIn("return DEFAULT_CANDLE_BARS", numbering)

    def test_two_day_entry_stays_at_default(self):
        now = 1_800_000_000_000
        two_days = now - 2 * 24 * 60 * 60 * 1000
        self.assertEqual(
            _candle_limit_for_entries([_iso(two_days)], now),
            DEFAULT_CANDLE_BARS,
        )

    def test_four_day_entry_extends_past_three_days(self):
        now = 1_800_000_000_000
        four_days = now - 4 * 24 * 60 * 60 * 1000
        n = _candle_limit_for_entries([_iso(four_days), None], now)
        self.assertGreater(n, DEFAULT_CANDLE_BARS)
        self.assertLessEqual(n, MAX_CANDLE_BARS)
        self.assertGreaterEqual(n, 4 * BARS_PER_DAY + ENTRY_PAD_BARS)

    def test_ten_day_entry_caps_at_seven_days(self):
        now = 1_800_000_000_000
        ten_days = now - 10 * 24 * 60 * 60 * 1000
        self.assertEqual(
            _candle_limit_for_entries([_iso(ten_days)], now),
            MAX_CANDLE_BARS,
        )

    def test_null_entry_ts_does_not_invent_a_window(self):
        now = 1_800_000_000_000
        known = now - 5 * 24 * 60 * 60 * 1000
        n_known = _candle_limit_for_entries([_iso(known)], now)
        n_mixed = _candle_limit_for_entries([None, _iso(known), ""], now)
        self.assertEqual(n_known, n_mixed)
        self.assertEqual(_candle_limit_for_entries([None], now), DEFAULT_CANDLE_BARS)

    def test_paper_chart_does_not_glue_off_window_entries_to_bar_zero(self):
        paper = (REPO / "frontend" / "src" / "chart" / "PaperChart.tsx").read_text()
        snap = paper.split("function snapToCandle")[1].split("export default")[0]
        self.assertIn("if (t < candleSec[0]) return null", snap)
        self.assertNotIn("if (t <= candleSec[0]) return candleSec[0]", snap)


if __name__ == "__main__":
    unittest.main()
