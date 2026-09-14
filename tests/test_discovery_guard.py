"""Lookback-cost guard + eval-timeout backstop (farm ops, not OOS gates)."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from hedge_fund.trading.constants import (
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    QUAL_N_WINDOWS,
    QUAL_TIMEFRAME,
    RISK_POLICY,
)
from hedge_fund.trading.discovery_guard import (
    DEFAULT_EVAL_TIMEOUT_SECONDS,
    DEFAULT_STRUCTURE_LOOKBACK_MAX,
    eval_timeout_reason,
    eval_timeout_seconds,
    lookback_too_expensive_reason,
    max_structure_lookback,
    ops_fail_record,
    structure_lookback_max,
    structure_lookbacks,
)
from hedge_fund.trading.qualify import requalify_parked_log
from scripts.discovery_worker import _collect_wave


def _cheap_eval(name, *args, **kwargs):
    return {
        "strategy": name,
        "tested_at": "2026-09-13T00:00:00+00:00",
        "qualified": False,
        "sharpe": 0.1,
        "trades": 5,
        "test_pnl": -1.0,
        "train_pnl": 0.0,
        "win_rate_pct": 0.0,
        "fail_reasons": ["oos_sharpe 0.10 < 0.30"],
        "timeframe": QUAL_TIMEFRAME,
        "risk_policy": RISK_POLICY,
    }


class StructureLookbackParseTests(unittest.TestCase):
    def test_standalone_and_anded_atoms(self):
        self.assertEqual(structure_lookbacks("dbl_bot_168"), [("dbl_bot", 168)])
        self.assertEqual(structure_lookbacks("don_hi_96"), [("don_hi", 96)])
        self.assertEqual(
            structure_lookbacks("dip_6b_lt2pc&don_lo_120"),
            [("don_lo", 120)],
        )
        self.assertEqual(
            max_structure_lookback("dbl_bot_168&don_lo_48"),
            168,
        )
        self.assertEqual(
            structure_lookbacks("near_swing_hi_108&sma_abv_50"),
            [("near_swing_hi", 108)],
        )
        self.assertEqual(structure_lookbacks("near_swing_lo_192"), [("near_swing_lo", 192)])

    def test_mom_dip_lookbacks_are_not_structure(self):
        self.assertEqual(structure_lookbacks("sma_stack"), [])
        self.assertEqual(structure_lookbacks("mom_12b_gt3pc"), [])
        self.assertEqual(structure_lookbacks("dip_24b_lt5pc&sma_abv_50"), [])
        self.assertIsNone(max_structure_lookback("mom_168b_gt2pc"))

    def test_cap_96_allows_96_parks_above(self):
        self.assertIsNone(lookback_too_expensive_reason("don_hi_96", 96))
        self.assertIsNone(lookback_too_expensive_reason("dbl_bot_24&sma_abv_50", 96))
        self.assertEqual(
            lookback_too_expensive_reason("dbl_bot_168", 96),
            "lookback_too_expensive 168>96",
        )
        self.assertEqual(
            lookback_too_expensive_reason("mom_12b_gt3pc&don_hi_108", 96),
            "lookback_too_expensive 108>96",
        )
        self.assertIsNone(lookback_too_expensive_reason("dbl_bot_168", 0))

    def test_env_defaults(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISCOVERY_STRUCTURE_LOOKBACK_MAX", None)
            os.environ.pop("DISCOVERY_EVAL_TIMEOUT_SECONDS", None)
            self.assertEqual(structure_lookback_max(), DEFAULT_STRUCTURE_LOOKBACK_MAX)
            self.assertEqual(eval_timeout_seconds(), DEFAULT_EVAL_TIMEOUT_SECONDS)
            self.assertEqual(DEFAULT_STRUCTURE_LOOKBACK_MAX, 96)
            self.assertEqual(DEFAULT_EVAL_TIMEOUT_SECONDS, 600)
        with patch.dict(
            os.environ,
            {"DISCOVERY_STRUCTURE_LOOKBACK_MAX": "72", "DISCOVERY_EVAL_TIMEOUT_SECONDS": "0"},
        ):
            self.assertEqual(structure_lookback_max(), 72)
            self.assertEqual(eval_timeout_seconds(), 0)


class OpsFailRecordTests(unittest.TestCase):
    def test_lookback_record_is_fail_once_5m_rm_v1(self):
        rec = ops_fail_record("dbl_bot_168", "lookback_too_expensive 168>96")
        self.assertFalse(rec["qualified"])
        self.assertEqual(rec["strategy"], "dbl_bot_168")
        self.assertEqual(rec["timeframe"], "5m")
        self.assertEqual(rec["risk_policy"], "rm_v1")
        self.assertEqual(rec["fail_reasons"], ["lookback_too_expensive 168>96"])
        self.assertEqual(rec["trades"], 0)
        self.assertTrue(rec["ops_park"])

    def test_timeout_reason_and_record(self):
        self.assertEqual(eval_timeout_reason(600), "eval_timeout after 600s")
        rec = ops_fail_record("sma_stack", eval_timeout_reason(1))
        self.assertFalse(rec["qualified"])
        self.assertEqual(rec["fail_reasons"], ["eval_timeout after 1s"])

    def test_requalify_does_not_flip_ops_park_even_with_passing_aggregates(self):
        rec = ops_fail_record("dbl_bot_168", "lookback_too_expensive 168>96")
        rec.update({
            "sharpe": 0.58,
            "trades": 296,
            "test_pnl": 946.0,
            "bh_oos_pnl": 100.0,
            "sma_stack_oos_pnl": 50.0,
            "regimes_tested": 8,
        })
        timeout = ops_fail_record("slow_one", "eval_timeout after 600s")
        timeout.update({
            "sharpe": 0.58,
            "trades": 296,
            "test_pnl": 946.0,
            "bh_oos_pnl": 100.0,
            "sma_stack_oos_pnl": 50.0,
            "regimes_tested": 8,
        })
        flipped, names = requalify_parked_log([rec, timeout], existing_names=set())
        self.assertEqual(names, [])
        self.assertEqual(flipped, [])
        self.assertFalse(rec["qualified"])
        self.assertFalse(timeout["qualified"])
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)


class CollectWaveTimeoutTests(unittest.TestCase):
    def test_ready_results_return_and_never_ready_is_stuck(self):
        class Immediate:
            def __init__(self, value):
                self.value = value

            def ready(self):
                return True

            def get(self, timeout=None):
                return self.value

        class Never:
            def ready(self):
                return False

            def get(self, timeout=None):
                raise AssertionError("should not get stuck future")

        done, stuck = _collect_wave(
            [("fast", Immediate({"strategy": "fast"})), ("slow", Never())],
            timeout_s=0.15,
            sleeper=lambda _n: None,
            clock=lambda t=iter([0.0, 0.05, 0.20]): next(t),
        )
        self.assertEqual([n for n, _ in done], ["fast"])
        self.assertEqual(stuck, ["slow"])

    def test_no_timeout_waits_until_ready(self):
        class Flip:
            def __init__(self):
                self.n = 0

            def ready(self):
                self.n += 1
                return self.n >= 2

            def get(self, timeout=None):
                return {"strategy": "ok"}

        sleeps = []
        done, stuck = _collect_wave(
            [("ok", Flip())],
            timeout_s=0,
            sleeper=lambda n: sleeps.append(n),
        )
        self.assertEqual(stuck, [])
        self.assertEqual(done[0][0], "ok")


class SharedEvalEntryTests(unittest.TestCase):
    def test_evaluate_strategy_record_skips_walkforward_when_lookback_expensive(self):
        from scripts.tournament_engine import evaluate_strategy_record

        with patch("scripts.tournament_engine.evaluate_windows") as windows:
            rec = evaluate_strategy_record(
                "dbl_bot_168",
                [{}] * QUAL_N_WINDOWS,
                n_windows=QUAL_N_WINDOWS,
                bh_oos_pnl=1.0,
                sma_stack_oos_pnl=1.0,
            )
        windows.assert_not_called()
        self.assertFalse(rec["qualified"])
        self.assertIn("lookback_too_expensive 168>96", rec["fail_reasons"])


class WorkerLookbackAndTimeoutTests(unittest.TestCase):
    def _run_batch(self, planned, **env):
        from scripts.discovery_worker import run_batch

        calls = []

        def track_eval(name, *args, **kwargs):
            calls.append(name)
            return _cheap_eval(name)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crypto_history_5m.json").write_text("{}")
            base_env = {
                "PAPER_STATE": str(root),
                "DISCOVERY_EVAL_TIMEOUT_SECONDS": "0",
                "DISCOVERY_STRUCTURE_LOOKBACK_MAX": "96",
            }
            base_env.update(env)
            with patch.dict(os.environ, base_env):
                with patch(
                    "scripts.discovery_worker._load_qual_history",
                    return_value={"BTC/USDT": [1]},
                ), patch(
                    "scripts.discovery_worker._window_slices",
                    return_value=[{}] * QUAL_N_WINDOWS,
                ), patch(
                    "scripts.discovery_worker._benchmark_oos",
                    return_value=(1.0, 1.0),
                ), patch(
                    "scripts.discovery_worker._plan_batch",
                    return_value=(planned, planned, []),
                ), patch(
                    "scripts.discovery_worker.evaluate_strategy_record",
                    side_effect=track_eval,
                ), patch(
                    "scripts.discovery_worker._post_ingest",
                    return_value={},
                ):
                    result = run_batch(
                        workers=1,
                        max_names=len(planned),
                        ingest_url=None,
                        token=None,
                    )
                    from hedge_fund.trading.discovery import (
                        load_discovery_log,
                        read_in_flight,
                    )

                    log = load_discovery_log()
                    flight = read_in_flight()
        return result, log, flight, calls

    def test_expensive_lookback_parks_without_walkforward_and_clears_inflight(self):
        result, log, flight, calls = self._run_batch(
            ["dbl_bot_168", "sma_stack"],
        )
        self.assertNotIn("dbl_bot_168", calls)
        self.assertEqual(calls, ["sma_stack"])
        by_name = {r["strategy"]: r for r in log}
        self.assertFalse(by_name["dbl_bot_168"]["qualified"])
        self.assertIn("lookback_too_expensive 168>96", by_name["dbl_bot_168"]["fail_reasons"])
        self.assertEqual(by_name["dbl_bot_168"]["timeframe"], "5m")
        self.assertEqual(by_name["dbl_bot_168"]["risk_policy"], "rm_v1")
        self.assertEqual(result["evaluated"], 2)
        self.assertEqual(result["qualified"], 0)
        self.assertIsNone(flight)

    def test_timeout_parks_and_continues_next_name(self):
        from scripts.discovery_worker import run_batch

        planned = ["slow_one", "sma_stack"]
        eval_calls = []

        def track_eval(name, *args, **kwargs):
            eval_calls.append(name)
            return _cheap_eval(name)

        class FakePool:
            def apply_async(self, fn, args):
                name = args[0]
                ar = MagicMock()
                if name == "slow_one":
                    ar.ready.return_value = False
                    return ar
                ar.ready.return_value = True
                ar.get.side_effect = lambda timeout=None, _fn=fn, _n=name: _fn(_n)
                return ar

            def terminate(self):
                return None

            def join(self):
                return None

        def fake_collect(asyncs, timeout_s, **kwargs):
            names = [n for n, _ in asyncs]
            if "slow_one" in names:
                others = []
                for name, ar in asyncs:
                    if name == "slow_one":
                        continue
                    others.append((name, ar.get()))
                return others, ["slow_one"]
            done = [(name, ar.get()) for name, ar in asyncs]
            return done, []

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crypto_history_5m.json").write_text("{}")
            env = {
                "PAPER_STATE": str(root),
                "DISCOVERY_EVAL_TIMEOUT_SECONDS": "1",
                "DISCOVERY_STRUCTURE_LOOKBACK_MAX": "96",
            }
            with patch.dict(os.environ, env):
                with patch(
                    "scripts.discovery_worker._load_qual_history",
                    return_value={"BTC/USDT": [1]},
                ), patch(
                    "scripts.discovery_worker._window_slices",
                    return_value=[{}] * QUAL_N_WINDOWS,
                ), patch(
                    "scripts.discovery_worker._benchmark_oos",
                    return_value=(1.0, 1.0),
                ), patch(
                    "scripts.discovery_worker._plan_batch",
                    return_value=(planned, planned, []),
                ), patch(
                    "scripts.discovery_worker.evaluate_strategy_record",
                    side_effect=track_eval,
                ), patch(
                    "scripts.discovery_worker._new_eval_pool",
                    return_value=FakePool(),
                ), patch(
                    "scripts.discovery_worker._collect_wave",
                    side_effect=fake_collect,
                ):
                    result = run_batch(
                        workers=1,
                        max_names=2,
                        ingest_url=None,
                        token=None,
                    )
                    from hedge_fund.trading.discovery import (
                        load_discovery_log,
                        read_in_flight,
                    )

                    log = load_discovery_log()
                    flight = read_in_flight()

        by_name = {r["strategy"]: r for r in log}
        self.assertFalse(by_name["slow_one"]["qualified"])
        self.assertIn("eval_timeout after 1s", by_name["slow_one"]["fail_reasons"])
        self.assertEqual(by_name["slow_one"]["timeframe"], "5m")
        self.assertIn("sma_stack", by_name)
        self.assertNotIn("slow_one", eval_calls)
        self.assertIn("sma_stack", eval_calls)
        self.assertEqual(result["evaluated"], 2)
        self.assertIsNone(flight)

    def test_sleep_wave_marks_timeout_without_waiting_forever(self):
        class Sleepy:
            def ready(self):
                return False

            def get(self, timeout=None):
                time.sleep(30)
                return {}

        t0 = time.monotonic()
        done, stuck = _collect_wave(
            [("hang", Sleepy())],
            timeout_s=0.2,
        )
        elapsed = time.monotonic() - t0
        self.assertEqual(done, [])
        self.assertEqual(stuck, ["hang"])
        self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
