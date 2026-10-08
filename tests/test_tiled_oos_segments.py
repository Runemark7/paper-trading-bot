"""Amendment 2026-10-08 18:08 (option b): tiled OOS segments.

OOS segments are contiguous, non-overlapping and tile the tape end to end,
anchored to a fixed start timestamp. Each segment only sees bars before its
own end (train + warm-up strictly before its OOS start). A new day extends
the last segment and never moves an earlier one. B&H and the strategy are
measured on the same OOS days.
"""
from __future__ import annotations

import math
import unittest

from hedge_fund.trading import constants as C
from hedge_fund.trading.buy_and_hold import buy_and_hold_daily_equity, equity_returns
from scripts.tournament_engine import (
    _benchmark_bh_daily_sharpe,
    _tiled_slices,
    _window_slices,
    evaluate_windows,
)

TF = 300_000
DAY = 288
# Small layout so tests stay fast: 4 segments of 3 days, 2d train, 1d warm-up.
P = dict(n_windows=4, segment_ms=3 * DAY * TF, train_bars=2 * DAY, warmup_bars=DAY)
START = 1_700_000_000_000 - (1_700_000_000_000 % (DAY * TF))  # a UTC midnight
OOS0 = START + (P["train_bars"] + P["warmup_bars"]) * TF


def _rows(t0, n, base=100.0):
    out = []
    for i in range(n):
        j = (t0 - START) // TF + i  # price is a function of the timestamp
        c = base * (1.0 + 0.02 * math.sin(j / 37.0) + 0.00002 * j)
        out.append([t0 + i * TF, c, c * 1.003, c * 0.997, c, 1.0])
    return out


def _data(days_after_last_start=2, lead_days=1):
    t0 = START - lead_days * DAY * TF
    last_start = OOS0 + (P["n_windows"] - 1) * P["segment_ms"]
    n = (last_start - t0) // TF + days_after_last_start * DAY
    return {"BTC/USDT": _rows(t0, n, 100.0), "ETH/USDT": _rows(t0, n, 10.0)}


def _ts(rows):
    return [r[0] for r in rows]


def _slices(data):
    return _tiled_slices(data, oos_start_ms=OOS0, **P)


class TiledLayoutTests(unittest.TestCase):
    def setUp(self):
        self.data = _data()
        self.slices = _slices(self.data)
        self.ts = _ts(self.data["BTC/USDT"])

    def _oos_ts(self, k, sym="BTC/USDT"):
        import bisect

        closes, _h, _l, warm, cut = self.slices[k][sym]
        rows = self.data[sym]
        lead_ms = (P["train_bars"] + P["warmup_bars"]) * TF
        first = bisect.bisect_left(_ts(rows), OOS0 + k * P["segment_ms"] - lead_ms)
        # The slice is exactly these tape rows (no copies from elsewhere).
        self.assertEqual(closes, [r[4] for r in rows[first:first + len(closes)]])
        return [r[0] for r in rows[first + cut:first + len(closes)]], first, warm, cut

    def test_segments_are_contiguous_non_overlapping_and_tile_to_end(self):
        self.assertEqual(len(self.slices), P["n_windows"])
        prev_last = None
        covered = []
        for k in range(P["n_windows"]):
            oos, _first, _warm, _cut = self._oos_ts(k)
            self.assertTrue(oos, k)
            self.assertEqual(oos[0], OOS0 + k * P["segment_ms"])
            if prev_last is not None:
                self.assertEqual(oos[0], prev_last + TF)  # back to back, no overlap
            prev_last = oos[-1]
            covered += oos
        self.assertEqual(len(covered), len(set(covered)))
        self.assertEqual(covered[-1], self.ts[-1])  # last segment runs to tape end
        self.assertEqual(covered[0], OOS0)

    def test_no_lookahead_train_and_warmup_strictly_before_oos(self):
        for k in range(P["n_windows"]):
            oos, first, warm, cut = self._oos_ts(k)
            closes = self.slices[k]["BTC/USDT"][0]
            slice_ts = self.ts[first:first + len(closes)]
            seg_start = OOS0 + k * P["segment_ms"]
            self.assertEqual(cut - warm, P["train_bars"])
            self.assertEqual(warm, P["warmup_bars"])
            self.assertTrue(all(t < seg_start for t in slice_ts[:cut]))
            if k < P["n_windows"] - 1:
                self.assertTrue(all(t < seg_start + P["segment_ms"] for t in slice_ts))

    def test_one_more_day_only_extends_last_segment(self):
        longer = _data(days_after_last_start=3)
        more = _slices(longer)
        for k in range(P["n_windows"] - 1):
            for sym in self.slices[k]:
                self.assertEqual(more[k][sym], self.slices[k][sym], (k, sym))
        for sym in self.slices[-1]:
            c0, h0, l0, w0, cut0 = self.slices[-1][sym]
            c1, h1, l1, w1, cut1 = more[-1][sym]
            self.assertEqual((w0, cut0), (w1, cut1))
            self.assertEqual(c1[: len(c0)], c0)
            self.assertEqual(len(c1) - len(c0), DAY)

    def test_extra_history_before_start_does_not_move_segments(self):
        deeper = _slices(_data(lead_days=5))
        self.assertEqual(deeper, self.slices)

    def test_bh_and_strategy_measured_on_identical_days(self):
        from hedge_fund.signals.dynamic import parse_strategy

        scores = evaluate_windows(parse_strategy("dip_12b_lt1pc"), self.slices)
        for k, (w, row) in enumerate(zip(self.slices, scores)):
            closes = {s: t[0][t[4]:] for s, t in w.items()}
            bh_days = len(equity_returns(buy_and_hold_daily_equity(closes)))
            self.assertEqual(len(row["daily_returns"]), bh_days, k)
            self.assertGreater(bh_days, 0)
        self.assertIsNotNone(_benchmark_bh_daily_sharpe(self.slices))

    def test_tape_starting_after_lead_in_is_refused(self):
        data = {s: rows[DAY * 2:] for s, rows in self.data.items()}
        with self.assertRaises(ValueError):
            _slices(data)

    def test_tape_ending_before_last_segment_is_refused(self):
        last_start = OOS0 + (P["n_windows"] - 1) * P["segment_ms"]
        data = {s: [r for r in rows if r[0] < last_start] for s, rows in self.data.items()}
        with self.assertRaises(ValueError):
            _slices(data)

    def test_window_slices_tiled_flag_uses_production_layout(self):
        # Legacy end-aligned call stays available for old fixtures.
        legacy = _window_slices(self.data, 3 * DAY, 4, 1, warmup_bars=DAY)
        self.assertEqual(len(legacy), 4)
        with self.assertRaises(ValueError):
            # A 2023 synthetic tape starts after the production lead-in.
            _window_slices(self.data, C.QUAL_WINDOW_BARS, C.QUAL_N_WINDOWS, 1, tiled=True)


class ProductionConstantsTests(unittest.TestCase):
    def test_layout_constants(self):
        self.assertEqual(C.QUAL_N_WINDOWS, 23)
        self.assertEqual(C.QUAL_WARMUP_BARS, 4032)
        self.assertEqual(C.QUAL_TRAIN_BARS, 18144)
        self.assertEqual(C.QUAL_SEGMENT_DAYS, 87)
        self.assertEqual(C.QUAL_OOS_START_MS, 1_618_876_800_000)  # 2021-04-20 00:00 UTC
        self.assertEqual(C.QUAL_LAST_SEGMENT_START_MS, 1_784_246_400_000)  # 2026-07-17
        self.assertEqual(C.qual_segment_bounds(0), (C.QUAL_OOS_START_MS, C.QUAL_OOS_START_MS + C.QUAL_SEGMENT_MS))
        self.assertEqual(C.qual_segment_bounds(22), (C.QUAL_LAST_SEGMENT_START_MS, None))
        self.assertEqual(C.GATE_RULES, "sltp_cap100_bhdsr_tiled87_20261008")
        # Frozen thresholds.
        self.assertEqual(C.MIN_BACKTEST_TRADES, 30)
        self.assertEqual(C.MIN_BACKTEST_SHARPE, 0.30)

    def test_store_budget_grows_one_bar_per_5m_and_covers_lead_in(self):
        t = 1_791_417_600_000  # 2026-10-08 00:00 UTC
        a = C.qual_store_bars(t)
        self.assertEqual(C.qual_store_bars(t + TF) - a, 1)
        self.assertEqual(C.qual_store_bars(t + DAY * TF) - a, DAY)
        self.assertGreaterEqual(a, (t - C.QUAL_TAPE_START_MS) // TF + C.QUAL_STORE_MARGIN_BARS)

    def test_production_tiled_slices_on_real_timestamps(self):
        t0 = C.QUAL_CANONICAL_START_MS
        end = 1_791_417_600_000
        n = (end - t0) // TF + 1
        rows = [[t0 + i * TF, 1.0, 1.0, 1.0, 1.0] for i in range(n)]
        slices = _window_slices({"BTC/USDT": rows}, C.QUAL_WINDOW_BARS, C.QUAL_N_WINDOWS, 1, tiled=True)
        self.assertEqual(len(slices), 23)
        lens = [len(w["BTC/USDT"][0]) - w["BTC/USDT"][4] for w in slices]
        self.assertEqual(lens[:-1], [C.QUAL_SEGMENT_BARS] * 22)
        self.assertEqual(lens[-1], (end - C.QUAL_LAST_SEGMENT_START_MS) // TF + 1)
        self.assertEqual(sum(lens), (end - C.QUAL_OOS_START_MS) // TF + 1)
        for w in slices:
            _c, _h, _l, warm, cut = w["BTC/USDT"]
            self.assertEqual(warm, C.QUAL_WARMUP_BARS)
            self.assertEqual(cut - warm, C.QUAL_TRAIN_BARS)


class CanonicalTapeCutTests(unittest.TestCase):
    def test_cut_keeps_everything_from_canonical_start_to_pin(self):
        import numpy as np

        from hedge_fund.trading.live_tape import TAPE_DTYPE
        from hedge_fund.trading.price_history import _cut_to_pin

        t0 = C.QUAL_CANONICAL_START_MS - 10 * TF
        arr = np.zeros(40, dtype=TAPE_DTYPE)
        arr["ts"] = [t0 + i * TF for i in range(40)]
        pin = t0 + 30 * TF
        out = _cut_to_pin(arr, pin, 0)
        self.assertEqual(int(out["ts"][0]), C.QUAL_CANONICAL_START_MS)
        self.assertEqual(int(out["ts"][-1]), pin)


if __name__ == "__main__":
    unittest.main()
