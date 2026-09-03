"""GET /api/champions is read-only; live/candles must not 502 each other."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[1]


def _start_server():
    from hedge_fund.web.server import Handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, httpd.server_address[1]


def _stop_server(httpd, thread):
    httpd.shutdown()
    thread.join(timeout=3)
    httpd.server_close()


class ChampionsReadOnlyTests(unittest.TestCase):
    def test_get_champions_does_not_call_collect_live_results(self):
        from hedge_fund.web.server import Handler

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [
                    {"name": "sma_stack", "closed": 4, "pnl": 1.5, "wins": 2,
                     "champion_since": "2026-08-01T00:00:00+00:00"},
                ],
                "synced_until": "2026-08-31T00:00:00+00:00",
            }))
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.handle_request, daemon=True)
            thread.start()
            try:
                with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                    with patch(
                        "hedge_fund.trading.champions.collect_live_results",
                    ) as collect:
                        raw = urlopen(f"http://127.0.0.1:{port}/api/champions", timeout=3)
                        body = json.loads(raw.read().decode())
                self.assertEqual(raw.status, 200)
                collect.assert_not_called()
                self.assertEqual(body["active_champions"][0]["name"], "sma_stack")
                self.assertEqual(body["active_champions"][0]["closed"], 4)
                self.assertEqual(
                    body["active_champions"][0]["champion_since"],
                    "2026-08-01T00:00:00+00:00",
                )
                self.assertNotIn("error", body)
            finally:
                thread.join(timeout=3)
                httpd.server_close()

    def test_live_cycle_script_still_calls_collect_live_results(self):
        src = (REPO / "scripts" / "live_cycle.py").read_text()
        self.assertIn("collect_live_results", src)
        self.assertIn("from hedge_fund.trading.champions import collect_live_results", src)


class ConcurrentReadPathTests(unittest.TestCase):
    def test_slow_candles_does_not_502_champions(self):
        started = threading.Event()
        payload = {
            "symbol": "BTC/USDT",
            "timeframe": "5m",
            "candles": [{"t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
            "as_of": "now",
            "source": "binance_public",
            "paper_only": True,
        }

        def slow_candles(*_a, **_k):
            started.set()
            time.sleep(2.5)
            return payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [{"name": "alpha", "closed": 0, "pnl": 0.0, "wins": 0}],
                "synced_until": "",
            }))
            httpd, thread, port = _start_server()
            try:
                with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                    with patch(
                        "hedge_fund.web.candles.candles_payload",
                        side_effect=slow_candles,
                    ):
                        with patch(
                            "hedge_fund.trading.champions.collect_live_results",
                            side_effect=AssertionError("GET /api/champions must not collect"),
                        ) as collect:
                            candle_err = []

                            def _candles():
                                try:
                                    urlopen(
                                        f"http://127.0.0.1:{port}/api/candles"
                                        "?symbol=BTC/USDT&timeframe=5m&limit=50",
                                        timeout=8,
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    candle_err.append(exc)

                            bg = threading.Thread(target=_candles, daemon=True)
                            bg.start()
                            self.assertTrue(started.wait(timeout=2), "slow candles never started")
                            t0 = time.monotonic()
                            raw = urlopen(
                                f"http://127.0.0.1:{port}/api/champions",
                                timeout=1.5,
                            )
                            elapsed = time.monotonic() - t0
                            body = json.loads(raw.read().decode())
                            self.assertEqual(raw.status, 200)
                            self.assertNotEqual(raw.status, 502)
                            self.assertLess(elapsed, 1.4)
                            self.assertEqual(body["active_champions"][0]["name"], "alpha")
                            collect.assert_not_called()
                            bg.join(timeout=8)
            finally:
                _stop_server(httpd, thread)

    def test_live_returns_json_when_signal_closes_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from hedge_fund.trading.store import TradeStore

            store = TradeStore(root / "trades_alpha.sqlite")
            store.save_account_state({
                "broker": {
                    "cash": 8_000.0,
                    "lots": [{
                        "lot_id": 1,
                        "ticker": "BTC/USDT",
                        "quantity": 0.01,
                        "entry_price": 100.0,
                        "stop_loss": 90.0,
                        "entry_fee": 0.1,
                        "entry_condition": "test_long",
                    }],
                },
            })
            httpd, thread, port = _start_server()
            try:
                with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                    with patch(
                        "hedge_fund.web.lot_health.fetch_signal_closes",
                        side_effect=RuntimeError("ccxt boom"),
                    ):
                        with patch(
                            "hedge_fund.web.live.live_prices",
                            return_value={"BTC/USDT": 101.0, "ETH/USDT": 3.0},
                        ):
                            raw = urlopen(
                                f"http://127.0.0.1:{port}/api/live",
                                timeout=3,
                            )
                            body = json.loads(raw.read().decode())
                self.assertEqual(raw.status, 200)
                self.assertNotEqual(raw.status, 502)
                self.assertTrue(body.get("lots") or body.get("positions"))
                lot = (body.get("lots") or [{}])[0]
                self.assertEqual(lot.get("signal"), "unknown")
            finally:
                _stop_server(httpd, thread)

    def test_live_outer_failure_is_json_503_not_502(self):
        httpd, thread, port = _start_server()
        try:
            with patch(
                "hedge_fund.web.server.open_lots_snapshot",
                side_effect=RuntimeError("disk gone"),
            ):
                try:
                    urlopen(f"http://127.0.0.1:{port}/api/live", timeout=3)
                    self.fail("expected HTTPError")
                except HTTPError as err:
                    self.assertEqual(err.code, 503)
                    self.assertNotEqual(err.code, 502)
                    body = json.loads(err.read().decode())
                    self.assertIn("error", body)
                    self.assertTrue(body.get("paper_only"))
        finally:
            _stop_server(httpd, thread)


class LivePricesReuseTests(unittest.TestCase):
    def test_live_prices_does_not_construct_ccxt(self):
        from hedge_fund.web import live as live_mod

        live_mod._PRICE_CACHE["ts"] = 0.0
        live_mod._PRICE_CACHE["prices"] = {}
        with patch("hedge_fund.data.binance.CcxtSource") as ctor:
            with patch(
                "hedge_fund.web.candles.try_live_prices",
                return_value={"BTC/USDT": 100.0, "ETH/USDT": 3.0},
            ):
                px = live_mod.live_prices(fresh=True)
        ctor.assert_not_called()
        self.assertEqual(px["BTC/USDT"], 100.0)

    def test_live_prices_keeps_stale_when_venue_busy(self):
        from hedge_fund.web import live as live_mod

        live_mod._PRICE_CACHE["ts"] = 0.0
        live_mod._PRICE_CACHE["prices"] = {"BTC/USDT": 99.0, "ETH/USDT": 2.0}
        with patch("hedge_fund.web.candles.try_live_prices", return_value={}):
            px = live_mod.live_prices(fresh=True)
        self.assertEqual(px["BTC/USDT"], 99.0)


if __name__ == "__main__":
    unittest.main()
