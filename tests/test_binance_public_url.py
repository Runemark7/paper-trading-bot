"""Public Binance market-data host override. No network."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from hedge_fund.data.binance import CcxtSource


class BinancePublicUrlTests(unittest.TestCase):
    def test_default_host_is_binance_spot(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PAPER_BINANCE_PUBLIC_URL", None)
            src = CcxtSource()
        self.assertIn("api.binance.com", src.exchange.urls["api"]["public"])
        self.assertNotEqual(src.exchange.options.get("fetchMarkets"), ["spot"])

    def test_public_url_uses_spot_mirror(self):
        url = "https://data-api.binance.vision/api/v3"
        with patch.dict(os.environ, {"PAPER_BINANCE_PUBLIC_URL": url + "/"}):
            src = CcxtSource()
        self.assertEqual(src.exchange.urls["api"]["public"], url)
        self.assertEqual(src.exchange.urls["api"]["private"], url)
        self.assertEqual(src.exchange.options.get("fetchMarkets"), ["spot"])

    def test_override_does_not_apply_to_other_venues(self):
        url = "https://data-api.binance.vision/api/v3"
        with patch.dict(os.environ, {"PAPER_BINANCE_PUBLIC_URL": url}):
            src = CcxtSource(exchange_id="binanceus")
        public = src.exchange.urls["api"]["public"]
        self.assertNotIn("data-api.binance.vision", public)


if __name__ == "__main__":
    unittest.main()
