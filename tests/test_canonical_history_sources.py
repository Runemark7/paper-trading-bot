"""load_canonical_history must accept every shared-tape source.

Once the Postgres candle store is caught up, ``canonical_arrays`` reports
source ``"candles"``. The tournament engine must still get the shared
tape instead of falling back to ``crypto_history_5m.json``.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from hedge_fund.trading.live_tape import TAPE_DTYPE
from hedge_fund.trading.price_history import load_canonical_history

TS = 1_611_619_200_000  # 2021-01-26 00:00 UTC


def _arr() -> np.ndarray:
    return np.array([(TS, 1.0, 1.1, 0.9, 1.05, 2.0)], dtype=TAPE_DTYPE)


class CanonicalHistorySourceTest(unittest.TestCase):
    def _load(self, source: str):
        with patch(
            "hedge_fund.trading.price_history.canonical_arrays",
            return_value=({"BTC/USDT": _arr(), "ETH/USDT": _arr()}, source, TS),
        ):
            return load_canonical_history()

    def test_live_tape_source_loads(self):
        loaded = self._load("live_tape")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["BTC/USDT"][0][0], TS)

    def test_candle_store_source_loads(self):
        loaded = self._load("candles")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["BTC/USDT"][0][0], TS)
        self.assertEqual(loaded["ETH/USDT"][0][0], TS)

    def test_json_source_is_not_the_shared_tape(self):
        self.assertIsNone(self._load("crypto_history_5m.json"))


if __name__ == "__main__":
    unittest.main()
