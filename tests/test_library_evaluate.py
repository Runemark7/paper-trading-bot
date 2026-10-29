"""Gate v6 library evaluation: holdout seal, record shape, trial count."""
from __future__ import annotations

import unittest

import numpy as np

import pandas as pd

from hedge_fund.library.catalog import (
    LIBRARY,
    claim_names,
    grid_params,
    library_trial_count,
    signal_ready,
    strategy_by_key,
)
from hedge_fund.library.daily import DAY_MS
from hedge_fund.library.evaluate import (
    CHECK_ORDER,
    count_only_failure,
    discovery_metrics,
    evaluate_library_record,
    holdout_metrics,
    library_data_from_tape,
    tile_blocks,
)
from hedge_fund.trading.constants import (
    GATE_RULES,
    HOLDOUT_END_MS,
    HOLDOUT_START_MS,
    LIBRARY_BLOCK_TARGET_DAYS,
    LIBRARY_HOLDOUT_MIN_TRADES,
    LIBRARY_MIN_OOS_TRADES,
    LIBRARY_STOP_FRAC,
    LIBRARY_TAPE_START_MS,
    QUAL_CANONICAL_START_MS,
)
from hedge_fund.trading.gate_v4 import v4_attestation_ok

TAPE_DTYPE = np.dtype([("ts", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"), ("volume", "<f8")])
HOUR = 3_600_000


def synthetic_tape(
    end_ms: int = HOLDOUT_END_MS + 20 * DAY_MS,
    seed: int = 3,
    drift: float = 0.00004,
    start_ms: int = LIBRARY_TAPE_START_MS,
) -> np.ndarray:
    """Hourly bars are enough: daily bars only need the first and last bar of a day."""
    rng = np.random.default_rng(seed)
    n = (end_ms - start_ms) // HOUR
    ts = start_ms + np.arange(n, dtype=np.int64) * HOUR
    close = 30_000.0 * np.exp(np.cumsum(rng.normal(drift, 0.007, n)))
    opens = np.concatenate(([close[0]], close[:-1]))
    arr = np.zeros(n, dtype=TAPE_DTYPE)
    arr["ts"], arr["open"], arr["close"] = ts, opens, close
    arr["high"] = np.maximum(opens, close) * 1.001
    arr["low"] = np.minimum(opens, close) * 0.999
    return arr


class HoldoutSealTests(unittest.TestCase):
    def test_discovery_bars_end_before_the_holdout(self):
        data = library_data_from_tape(synthetic_tape())
        self.assertLess(int(data.discovery.ts[-1]), HOLDOUT_START_MS)
        self.assertEqual(int(data.holdout.ts[-1]), HOLDOUT_END_MS - DAY_MS)

    def test_discovery_metrics_ignore_holdout_bars(self):
        tape = synthetic_tape()
        poisoned = tape.copy()
        sealed = poisoned["ts"] >= HOLDOUT_START_MS
        # A crash that runs through the whole holdout year (prices fall 90%).
        ramp = np.linspace(1.0, 0.1, int(sealed.sum()))
        for field in ("open", "high", "low", "close"):
            poisoned[field][sealed] *= ramp
        a = library_data_from_tape(tape)
        b = library_data_from_tape(poisoned)
        holdout_moved = False
        for name in ("lib.sma200", "lib.tsmom12m", "lib.rsi2"):
            strategy = strategy_by_key(name[len("lib."):])
            ma = discovery_metrics(a, strategy, strategy.center)
            mb = discovery_metrics(b, strategy, strategy.center)
            np.testing.assert_allclose(ma.returns, mb.returns)
            self.assertEqual(ma.trades, mb.trades)
            ha = holdout_metrics(a, strategy, strategy.center)
            hb = holdout_metrics(b, strategy, strategy.center)
            self.assertIsNotNone(ha)
            self.assertIsNotNone(hb)
            # A book that never enters the holdout has nothing for the crash to change.
            if ha.trades or ha.exposure:
                self.assertNotEqual(round(ha.pnl, 6), round(hb.pnl, 6))
                holdout_moved = True
        self.assertTrue(holdout_moved)

    def test_short_holdout_fails_closed(self):
        data = library_data_from_tape(synthetic_tape(end_ms=HOLDOUT_START_MS + 30 * DAY_MS))
        self.assertIsNone(data.holdout)
        record = evaluate_library_record("lib.sma200", data)
        self.assertFalse(record["qualified"])
        self.assertFalse(record["gate_v4"]["holdout_pass"])
        self.assertTrue(any("holdout tape incomplete" in r for r in record["fail_reasons"]))

    def test_tape_that_starts_in_2021_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            library_data_from_tape(synthetic_tape(start_ms=QUAL_CANONICAL_START_MS))
        self.assertIn("2017-08-17", str(caught.exception))

    def test_tape_that_cannot_warm_the_longest_neighbour_is_refused(self):
        short_end = LIBRARY_TAPE_START_MS + 200 * DAY_MS
        with self.assertRaises(ValueError) as caught:
            library_data_from_tape(synthetic_tape(end_ms=short_end))
        self.assertIn("not scored as flat", str(caught.exception))


class RecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = library_data_from_tape(synthetic_tape())
        cls.records = {name: evaluate_library_record(name, cls.data) for name in claim_names()}

    def test_every_check_is_reported(self):
        for name, record in self.records.items():
            lib = record["library"]
            self.assertEqual(set(lib["checks"]), set(CHECK_ORDER), name)
            self.assertEqual(record["regimes_tested"], len(self.data.segments))
            self.assertEqual(len(lib["windows"]), len(self.data.segments))
            self.assertEqual(record["library"]["checks"]["oos_trades"]["threshold"], LIBRARY_MIN_OOS_TRADES)
            self.assertEqual(record["library"]["checks"]["holdout_trades"]["threshold"], LIBRARY_HOLDOUT_MIN_TRADES)
            self.assertEqual(record["library"]["safety_stop_pct"], LIBRARY_STOP_FRAC * 100.0)
            self.assertEqual(record["gate_rules"], GATE_RULES)
            self.assertEqual(record["engine"], "signal_exit")
            self.assertEqual(record["timeframe"], "1d")
            failing = [k for k in CHECK_ORDER if not lib["checks"][k]["pass"]]
            self.assertEqual(lib["failing_checks"], failing)
            self.assertEqual(record["qualified"], not failing)
            self.assertEqual(bool(record["fail_reasons"]), bool(failing))

    def test_trial_count_is_the_whole_library(self):
        for record in self.records.values():
            self.assertEqual(record["gate_v4"]["n_trials"], library_trial_count())
            self.assertEqual(record["library"]["dsr"]["n_trials"], library_trial_count())

    def test_buy_and_hold_is_informational(self):
        for record in self.records.values():
            self.assertIsNotNone(record["bh_daily_sharpe"])
            self.assertEqual(record["bh_daily_sharpe_role"], "informational")
            self.assertFalse(any("b&h" in r.lower() or "buy-and-hold" in r.lower() for r in record["fail_reasons"]))

    def test_sharpe_floor_uses_daily_equity(self):
        record = self.records["lib.sma200"]
        self.assertEqual(record["sharpe"], record["daily_sharpe"])
        self.assertAlmostEqual(record["library"]["checks"]["oos_sharpe"]["value"], record["daily_sharpe"], places=3)
        self.assertEqual(record["library"]["discovery"]["daily_sharpe"], record["library"]["checks"]["oos_sharpe"]["value"])

    def test_pass_attestation_shape(self):
        record = dict(self.records["lib.sma200"])
        block = dict(record["gate_v4"])
        for key in ("holdout_pass", "dsr_pass", "pbo_pass", "perturb_pass", "cost_pass", "passed"):
            block[key] = True
        record["gate_v4"] = block
        self.assertTrue(v4_attestation_ok(record))

    def test_trade_windows_sum(self):
        for record in self.records.values():
            windows = record["library"]["windows"]
            self.assertEqual(sum(w["trades"] for w in windows), record["trades"])
            self.assertAlmostEqual(sum(w["pnl"] for w in windows), record["test_pnl"], delta=0.05)


class CountOnlyTests(unittest.TestCase):
    def _checks(self, **fails):
        checks = {key: {"pass": True} for key in CHECK_ORDER}
        for key, extra in fails.items():
            checks[key] = {"pass": False, **(extra or {})}
        return checks

    def test_only_trade_floors(self):
        self.assertTrue(count_only_failure(self._checks(oos_trades=None, holdout_trades=None)))

    def test_neighbours_count_only_when_sharpe_alone_passes(self):
        self.assertTrue(count_only_failure(self._checks(oos_trades=None, neighbours={"sharpe_only_pass": True})))
        self.assertFalse(count_only_failure(self._checks(oos_trades=None, neighbours={"sharpe_only_pass": False})))

    def test_any_statistical_failure_is_not_count_only(self):
        self.assertFalse(count_only_failure(self._checks(oos_trades=None, dsr=None)))
        self.assertFalse(count_only_failure(self._checks()))


class WarmupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = library_data_from_tape(synthetic_tape())

    def test_no_nan_as_off_inside_scored_days(self):
        discovery = self.data.discovery
        a, b = self.data.discovery_bounds()
        hold = self.data.holdout
        self.assertIsNotNone(hold)
        ha = hold.index_of(HOLDOUT_START_MS)
        for strategy in LIBRARY:
            for params in grid_params(strategy):
                ready = signal_ready(strategy, discovery, params)
                self.assertTrue(
                    bool(ready[a - 1 : b - 1].all()),
                    f"{strategy.key} {params} scored a discovery day that is not warm",
                )
                hold_ready = signal_ready(strategy, hold, params)
                self.assertTrue(
                    bool(hold_ready[ha - 1 : len(hold) - 1].all()),
                    f"{strategy.key} {params} scored a holdout day that is not warm",
                )

    def test_sma200_matches_pandas_where_the_average_is_defined(self):
        bars = self.data.discovery
        strategy = strategy_by_key("sma200")
        signal = strategy.signal(bars, strategy.center)
        ready = signal_ready(strategy, bars, strategy.center)
        close = pd.Series(bars.close)
        average = close.rolling(200).mean()
        expected = (close > average).fillna(False).to_numpy()
        np.testing.assert_array_equal(signal, expected)
        np.testing.assert_array_equal(ready, average.notna().to_numpy())
        a, b = self.data.discovery_bounds()
        scored = slice(a - 1, b - 1)
        self.assertTrue(bool(average.iloc[scored].notna().all()))
        np.testing.assert_array_equal(
            signal[scored],
            (close.to_numpy()[scored] > average.to_numpy()[scored]),
        )

    def test_blocks_tile_the_warmed_span_in_about_390_day_pieces(self):
        score_ts = int(self.data.discovery.ts[self.data.score_index])
        segments = self.data.segments
        self.assertEqual(segments, tile_blocks(score_ts, HOLDOUT_START_MS))
        span = HOLDOUT_START_MS - score_ts
        expected = max(1, round(span / (LIBRARY_BLOCK_TARGET_DAYS * DAY_MS)))
        self.assertEqual(len(segments), expected)
        self.assertEqual(segments[0][0], score_ts)
        self.assertEqual(segments[-1][1], HOLDOUT_START_MS)
        self.assertGreater(expected, 1)
        self.assertLess(expected, 18)


if __name__ == "__main__":
    unittest.main()
