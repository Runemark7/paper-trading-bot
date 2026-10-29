"""Gate v7: library profit vetoes, and the checks that are only warnings.

The 5m path still vetoes on Sharpe. A v6 worker gets no names.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from hedge_fund.library.catalog import library_trial_count
from hedge_fund.library.engine import ENGINE_ID, RISK_POLICY, TIMEFRAME
from hedge_fund.library.evaluate import (
    library_attestation_ok,
    library_pass_attestation,
    library_verdict,
)
from hedge_fund.trading.constants import (
    DSR_MIN_PROBABILITY,
    GATE_RULES,
    HOLDOUT_MIN_TRADES,
    LIBRARY_HOLDOUT_MIN_TRADES,
    LIBRARY_MIN_OOS_TRADES,
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    NEIGHBOR_PASS_MIN_FRACTION,
    PBO_MAX,
    V6_WARMUP_GATE_RULES,
)
from hedge_fund.trading.gate_v4 import v4_attestation_ok, v4_pass_attestation
from hedge_fund.trading.qualify import aggregate_fail_reasons


def _facts(**overrides) -> dict:
    base = dict(
        windows_ok=True,
        n_windows=6,
        expected_windows=6,
        window_span="2018-12-01 through the day before the holdout",
        oos_pnl=100.0,
        oos_trades=10,
        oos_sharpe=0.50,
        sma_oos_pnl=40.0,
        bh_oos_pnl=30.0,
        holdout_present=True,
        holdout_note=None,
        holdout_pnl=20.0,
        holdout_trades=3,
        holdout_sharpe=0.40,
        sma_holdout_pnl=5.0,
        bh_holdout_pnl=4.0,
        stress_oos_pnl=15.0,
        stress_holdout_pnl=8.0,
        dsr_value=0.97,
        n_trials=64,
        pbo=0.10,
        pbo_undefined=None,
        neighbour_passes=[True, True, True, False],
    )
    base.update(overrides)
    return library_verdict(**base)


class TagTests(unittest.TestCase):
    def test_gate_tag_is_v7_and_v6_is_history(self):
        self.assertEqual(GATE_RULES, "gate_v7_library_profit_20261029")
        self.assertEqual(V6_WARMUP_GATE_RULES, "gate_v6_library_warmup_20261029")
        self.assertEqual(LIBRARY_MIN_OOS_TRADES, 8)
        self.assertEqual(LIBRARY_HOLDOUT_MIN_TRADES, 2)
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(HOLDOUT_MIN_TRADES, 8)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(DSR_MIN_PROBABILITY, 0.95)
        self.assertEqual(PBO_MAX, 0.20)
        self.assertEqual(NEIGHBOR_PASS_MIN_FRACTION, 0.70)

    def test_five_minute_path_still_vetoes_sharpe_and_dsr(self):
        reasons = aggregate_fail_reasons(
            tot_test_pnl=500.0,
            tot_oos_trades=40,
            avg_sharpe=0.10,
            bh_oos_pnl=10.0,
            sma_stack_oos_pnl=10.0,
            n_windows=18,
            expected_windows=18,
        )
        self.assertTrue(any(r.startswith("oos_sharpe") for r in reasons))
        self.assertFalse(any(r.endswith("<= 0") for r in reasons))
        library = library_pass_attestation(dsr_pass=False, pbo_pass=False)
        self.assertTrue(library_attestation_ok({"gate_v4": library}))
        five_m = v4_pass_attestation()
        five_m["dsr_pass"] = False
        self.assertFalse(v4_attestation_ok({"gate_v4": five_m}))


class VetoTests(unittest.TestCase):
    def test_passing_book_is_qualified(self):
        out = _facts()
        self.assertTrue(out["qualified"])
        self.assertEqual(out["fail_reasons"], [])
        self.assertEqual(out["warnings"], [])
        self.assertEqual(out["failing_checks"], [])

    def test_oos_pnl_must_be_positive(self):
        for pnl in (0.0, -12.5):
            out = _facts(oos_pnl=pnl)
            self.assertFalse(out["qualified"], pnl)
            self.assertEqual(out["failing_checks"], ["oos_pnl"])
            self.assertIn(f"oos_pnl {pnl:.2f} <= 0", out["fail_reasons"])

    def test_holdout_pnl_must_be_positive(self):
        out = _facts(holdout_pnl=0.0)
        self.assertFalse(out["qualified"])
        self.assertEqual(out["failing_checks"], ["holdout_pnl"])
        self.assertIn("holdout_pnl 0.00 <= 0", out["fail_reasons"])
        self.assertFalse(any("holdout_sharpe" in r for r in out["fail_reasons"]))

    def test_cost_stress_requires_both_spans(self):
        oos = _facts(stress_oos_pnl=0.0)
        self.assertFalse(oos["qualified"])
        self.assertEqual(oos["failing_checks"], ["cost_stress"])
        self.assertIn("cost stress oos pnl 0.00 <= 0", oos["fail_reasons"][0])
        hold = _facts(stress_holdout_pnl=-3.0)
        self.assertFalse(hold["qualified"])
        self.assertIn("cost stress holdout pnl -3.00 <= 0", hold["fail_reasons"][0])
        both = _facts(stress_oos_pnl=1.0, stress_holdout_pnl=1.0, oos_sharpe=0.01)
        self.assertTrue(both["cost_pass"])
        self.assertNotIn("cost_stress", both["failing_checks"])

    def test_trade_floors_stay(self):
        oos = _facts(oos_trades=7)
        self.assertFalse(oos["qualified"])
        self.assertEqual(oos["failing_checks"], ["oos_trades"])
        self.assertIn("oos_trades 7 < 8", oos["fail_reasons"])
        self.assertTrue(oos["count_only"])
        hold = _facts(holdout_trades=1)
        self.assertFalse(hold["qualified"])
        self.assertEqual(hold["failing_checks"], ["holdout_trades"])
        self.assertIn("holdout_trades 1 < 2", hold["fail_reasons"])

    def test_missing_holdout_still_fails_closed(self):
        out = _facts(holdout_present=False, holdout_note="holdout tape incomplete")
        self.assertFalse(out["qualified"])
        self.assertIn("holdout_pnl", out["failing_checks"])
        self.assertIn("holdout_trades", out["failing_checks"])
        self.assertIn("holdout tape incomplete", out["fail_reasons"])
        self.assertNotIn("holdout_sharpe", out["warning_checks"])

    def test_windows_that_do_not_tile_fail(self):
        out = _facts(windows_ok=False, n_windows=5)
        self.assertFalse(out["qualified"])
        self.assertEqual(out["failing_checks"], ["windows"])
        self.assertIn("do not tile the warmed span", out["fail_reasons"][0])

    def test_neighbours_must_clear_the_three_profit_checks(self):
        short = _facts(neighbour_passes=[True, True, False, False])
        self.assertFalse(short["qualified"])
        self.assertEqual(short["failing_checks"], ["neighbours"])
        self.assertIn("neighbours 2/4 = 50% < 70%", short["fail_reasons"])
        exact = _facts(neighbour_passes=[True] * 7 + [False] * 3)
        self.assertTrue(exact["qualified"])
        self.assertEqual(exact["neighbours_pass"], 7)
        lone = _facts(neighbour_passes=[])
        self.assertFalse(lone["qualified"])
        self.assertIn("no parameter neighbours (lone spike)", lone["fail_reasons"])


class WarningTests(unittest.TestCase):
    def test_dsr_is_a_warning(self):
        out = _facts(dsr_value=0.848)
        self.assertTrue(out["qualified"])
        self.assertEqual(out["fail_reasons"], [])
        self.assertIn("dsr", out["warning_checks"])
        self.assertIn("dsr 0.848 < 0.95 (n_trials=64)", out["warnings"])
        self.assertFalse(out["dsr_pass"])
        missing = _facts(dsr_value=None)
        self.assertTrue(missing["qualified"])
        self.assertIn("dsr daily returns missing", missing["warnings"])

    def test_pbo_is_a_warning(self):
        out = _facts(pbo=0.30)
        self.assertTrue(out["qualified"])
        self.assertIn("pbo", out["warning_checks"])
        self.assertIn("pbo 0.300 > 0.2", out["warnings"])
        self.assertFalse(out["pbo_pass"])
        undefined = _facts(pbo=None, pbo_undefined="pbo undefined (fewer than 2 configurations)")
        self.assertTrue(undefined["qualified"])
        self.assertIn("pbo undefined (fewer than 2 configurations)", undefined["warnings"])

    def test_sharpe_floors_are_warnings(self):
        oos = _facts(oos_sharpe=0.10)
        self.assertTrue(oos["qualified"])
        self.assertIn("oos_sharpe", oos["warning_checks"])
        self.assertIn("oos_sharpe 0.10 < 0.3 (daily-equity)", oos["warnings"])
        self.assertNotIn("oos_sharpe", oos["failing_checks"])
        hold = _facts(holdout_sharpe=0.05)
        self.assertTrue(hold["qualified"])
        self.assertIn("holdout_sharpe", hold["warning_checks"])
        self.assertIn("holdout_sharpe 0.05 < 0.3 (daily-equity)", hold["warnings"])

    def test_sma_stack_is_a_warning_on_both_spans(self):
        oos = _facts(sma_oos_pnl=250.0)
        self.assertTrue(oos["qualified"])
        self.assertIn("beat_sma_stack", oos["warning_checks"])
        self.assertIn("oos_pnl 100.00 <= sma_stack 250.00", oos["warnings"])
        hold = _facts(sma_holdout_pnl=80.0)
        self.assertTrue(hold["qualified"])
        self.assertIn("holdout_beat_sma_stack", hold["warning_checks"])
        self.assertIn("holdout_pnl 20.00 <= sma_stack 80.00", hold["warnings"])
        missing = _facts(sma_oos_pnl=None)
        self.assertTrue(missing["qualified"])
        self.assertIn("sma_stack missing", missing["warnings"])

    def test_buy_and_hold_is_a_warning(self):
        out = _facts(bh_oos_pnl=400.0, bh_holdout_pnl=50.0)
        self.assertTrue(out["qualified"])
        self.assertEqual(out["fail_reasons"], [])
        self.assertIn("buy_and_hold", out["warning_checks"])
        text = " ".join(out["warnings"])
        self.assertIn("oos_pnl 100.00 <= buy-and-hold 400.00", text)
        self.assertIn("holdout_pnl 20.00 <= buy-and-hold 50.00", text)

    def test_every_warning_together_still_passes(self):
        out = _facts(
            oos_sharpe=0.0,
            sma_oos_pnl=999.0,
            holdout_sharpe=-0.2,
            sma_holdout_pnl=999.0,
            dsr_value=0.1,
            pbo=0.9,
            bh_oos_pnl=999.0,
            bh_holdout_pnl=999.0,
        )
        self.assertTrue(out["qualified"])
        self.assertEqual(out["fail_reasons"], [])
        self.assertEqual(
            out["warning_checks"],
            [
                "oos_sharpe",
                "beat_sma_stack",
                "holdout_sharpe",
                "holdout_beat_sma_stack",
                "dsr",
                "pbo",
                "buy_and_hold",
            ],
        )


class ProposeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"PAPER_STATE": self._tmp.name, "PAPER_DISCOVERY_SOURCE": "library"})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_a_pass_with_dsr_and_pbo_warnings_is_proposed_not_seated(self):
        from hedge_fund.trading.champions import load_pool
        from hedge_fund.trading.ingest import ingest_discovery_payload

        row = {
            "strategy": "lib.sma100",
            "tested_at": "2026-10-29T10:35:00+01:00",
            "qualified": True,
            "sharpe": 0.40,
            "daily_sharpe": 0.40,
            "trades": 12,
            "test_pnl": 100.0,
            "train_pnl": None,
            "win_rate_pct": 40.0,
            "fail_reasons": [],
            "warnings": [
                "dsr 0.848 < 0.95 (n_trials=64)",
                "pbo 0.300 > 0.2",
            ],
            "timeframe": TIMEFRAME,
            "risk_policy": RISK_POLICY,
            "engine": ENGINE_ID,
            "gate_rules": GATE_RULES,
            "gate_v4": library_pass_attestation(
                n_trials=library_trial_count(),
                dsr_pass=False,
                pbo_pass=False,
            ),
            "symbols": ["BTC/USDT"],
        }
        body = ingest_discovery_payload({"evaluations": [row]})
        self.assertEqual(body["ingested"], ["lib.sma100"])
        self.assertEqual(body["proposed"], ["lib.sma100"])
        self.assertEqual(body["admitted"], [])
        self.assertEqual(body["admission"], "manual")
        self.assertEqual(load_pool().get("champions"), [])

    def test_v6_worker_gets_stale_rules(self):
        from hedge_fund.trading.leases import claim_discovery_batch

        body = claim_discovery_batch(
            "zealous-lion",
            4,
            parallel=2,
            now=datetime(2026, 10, 29, 10, 35, tzinfo=timezone.utc),
            gate_rules=V6_WARMUP_GATE_RULES,
            require_gate_rules=True,
        )
        self.assertEqual(body["names"], [])
        self.assertTrue(body["stale_rules"])
        self.assertEqual(body["gate_rules"], GATE_RULES)
        self.assertEqual(body["worker_gate_rules"], V6_WARMUP_GATE_RULES)


if __name__ == "__main__":
    unittest.main()
