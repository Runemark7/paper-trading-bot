"""Gate v5: discovery-window buy-and-hold is informational. Other v4 checks stay."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hedge_fund.trading.constants import (
    DISCOVERY_N_WINDOWS,
    DSR_MIN_PROBABILITY,
    GATE_RULES,
    HOLDOUT_END_MS,
    HOLDOUT_MIN_TRADES,
    HOLDOUT_START_MS,
    PBO_MAX,
    V4_GATE_RULES,
    V5_GATE_RULES,
)
from hedge_fund.trading.gate_v4 import finish_gate_v4, stress_fees
from hedge_fund.trading.qualify import qualification_decision
from hedge_fund.trading.trials import trial_census


def _windows(n=3):
    return [
        {
            "test_pnl": 20.0,
            "test_trades": 12,
            "sharpe": 0.5,
            "train_pnl": 0.0,
            "skipped": False,
            "failed": False,
        }
        for _ in range(n)
    ]


def _scored(pnls, sharpe=0.8):
    return [
        {
            "sharpe": sharpe,
            "test_trades": 10,
            "test_pnl": pnl,
            "daily_returns": [0.002, 0.001 * ((i % 3) + 1), 0.0005],
            "max_drawdown": 0.04,
        }
        for i, pnl in enumerate(pnls)
    ]


class DiscoveryWindowTests(unittest.TestCase):
    def test_losing_to_buy_and_hold_still_qualifies(self):
        decision = qualification_decision(
            _windows(),
            expected_windows=3,
            bh_oos_pnl=10_000.0,
            sma_stack_oos_pnl=1.0,
            strategy_daily_sharpe=0.10,
            bh_daily_sharpe=1.50,
        )
        self.assertTrue(decision["passed"], decision["reasons"])
        self.assertEqual(decision["bh_role"], "informational")
        self.assertFalse(any("bh" in reason for reason in decision["reasons"]))

    def test_other_floors_still_fail(self):
        short = qualification_decision(
            _windows(2),
            expected_windows=3,
            bh_oos_pnl=0.0,
            sma_stack_oos_pnl=0.0,
            strategy_daily_sharpe=2.0,
            bh_daily_sharpe=0.0,
        )
        self.assertFalse(short["passed"])
        self.assertTrue(any("windows" in reason for reason in short["reasons"]))

        few = qualification_decision(
            [
                {"test_pnl": 20.0, "test_trades": 5, "sharpe": 0.5, "train_pnl": 0.0,
                 "skipped": False, "failed": False}
                for _ in range(3)
            ],
            expected_windows=3,
            bh_oos_pnl=0.0,
            sma_stack_oos_pnl=0.0,
            strategy_daily_sharpe=2.0,
            bh_daily_sharpe=0.0,
        )
        self.assertFalse(few["passed"])
        self.assertTrue(any("oos_trades" in reason for reason in few["reasons"]))

        soft = qualification_decision(
            [
                {"test_pnl": 20.0, "test_trades": 12, "sharpe": 0.1, "train_pnl": 0.0,
                 "skipped": False, "failed": False}
                for _ in range(3)
            ],
            expected_windows=3,
            bh_oos_pnl=0.0,
            sma_stack_oos_pnl=0.0,
            strategy_daily_sharpe=2.0,
            bh_daily_sharpe=0.0,
        )
        self.assertFalse(soft["passed"])
        self.assertTrue(any("oos_sharpe" in reason for reason in soft["reasons"]))

        sma = qualification_decision(
            _windows(),
            expected_windows=3,
            bh_oos_pnl=0.0,
            sma_stack_oos_pnl=10_000.0,
            strategy_daily_sharpe=2.0,
            bh_daily_sharpe=0.0,
        )
        self.assertFalse(sma["passed"])
        self.assertTrue(any("sma_stack" in reason for reason in sma["reasons"]))


class UnchangedV4ChecksTests(unittest.TestCase):
    def test_holdout_constants_and_trial_formula_stay(self):
        self.assertEqual(GATE_RULES, "gate_v6_library_signal_exit_20261028")
        self.assertEqual(V5_GATE_RULES, "gate_v5_btc_holdout_dsr_pbo_20261027")
        self.assertEqual(V4_GATE_RULES, "gate_v4_btc_holdout_dsr_pbo_20261027")
        self.assertEqual(HOLDOUT_START_MS, 1_759_276_800_000)
        self.assertEqual(HOLDOUT_END_MS, 1_790_812_800_000)
        self.assertEqual(HOLDOUT_MIN_TRADES, 8)
        self.assertEqual(DISCOVERY_N_WINDOWS, 18)
        self.assertEqual(DSR_MIN_PROBABILITY, 0.95)
        self.assertEqual(PBO_MAX, 0.20)
        self.assertEqual(stress_fees(), (0.002, 0.0007))
        import hedge_fund.trading.constants as constants

        self.assertFalse(hasattr(constants, "FORWARD_CHAMPION_CAP"))

    def test_trial_count_is_backtested_plus_one_and_skips_ops_parks(self):
        payload = {
            "names": {"a": False, "b": False, "ops": False},
            "metrics": {
                "a": {"sharpe": 0.2, "trades": 10},
                "b": {"sharpe": 0.4, "trades": 12},
                "ops": {"sharpe": 1.0, "trades": 1, "ops_park": True},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_tested.json").write_text(json.dumps(payload))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                census = trial_census()
        self.assertEqual(census["n_backtested"], 2)
        self.assertEqual(census["n_trials"], 3)
        self.assertEqual(census["n_ops_park"], 1)

    def _finish(self, *, n_trials, holdout, stress_bh, neighbours):
        def fake(name, slices, taker_fee=None, slippage=None):
            if slices == ["eth"]:
                return _scored([-1.0, -0.5, -1.2, -0.4], sharpe=0.4)
            if name == "cand":
                return _scored([10.0, 12.0, 11.0, 13.0])
            return _scored([1.0, 0.5, 1.2, 0.4])

        with patch("hedge_fund.trading.gate_v4.score_name", side_effect=fake), patch(
            "hedge_fund.trading.gate_v4._holdout_metrics",
            return_value=holdout,
        ), patch(
            "hedge_fund.trading.gate_v4._stressed_bh_daily",
            return_value=stress_bh,
        ), patch(
            "hedge_fund.trading.gate_v4.neighbor_names",
            return_value=list(neighbours),
        ):
            return finish_gate_v4(
                "cand",
                {"qualified": True, "fail_reasons": [], "sharpe": 0.4,
                 "daily_sharpe": 0.1, "bh_daily_sharpe": 2.0},
                btc_slices=["btc"],
                eth_slices=["eth"],
                holdout_sample={"ok": True},
                holdout_benchmarks={"ok": True},
                n_trials=n_trials,
            )

    def test_holdout_buy_and_hold_loss_does_not_fail_the_gate(self):
        out = self._finish(
            n_trials=1,
            holdout={
                "pass": True,
                "sharpe": 0.5,
                "trades": 10,
                "pnl": 3.0,
                "daily_sharpe": 0.10,
                "bh_daily_sharpe": 0.40,
                "bh_role": "informational",
            },
            stress_bh=1e9,
            neighbours=["n1", "n2"],
        )
        self.assertTrue(out["qualified"], out["fail_reasons"])
        self.assertEqual(out["gate_v4"]["holdout"]["bh_daily_sharpe"], 0.40)
        self.assertTrue(out["gate_v4"]["cost_pass"])
        self.assertEqual(out["gate_v4"]["cost_bh_daily_sharpe"], 1e9)
        self.assertEqual(out["gate_v4"]["cost_bh_role"], "informational")
        self.assertFalse(any("bh" in reason for reason in out["fail_reasons"]))

    def test_holdout_metrics_keep_buy_and_hold_and_do_not_fail_on_it(self):
        from hedge_fund.trading.gate_v4 import _holdout_metrics

        result = SimpleNamespace(total_pnl=10.0, trades=10, sharpe=0.5, max_drawdown=0.01)
        weak = SimpleNamespace(total_pnl=10.0, trades=10, sharpe=0.1, max_drawdown=0.01)
        with patch("scripts.tournament_engine._eval_slice", return_value=[result]), patch(
            "scripts.tournament_engine._book_daily_returns",
            return_value=[0.01, 0.02],
        ), patch(
            "hedge_fund.trading.buy_and_hold.daily_returns_sharpe",
            return_value=0.10,
        ):
            lost = _holdout_metrics(
                "sma_abv_20",
                {"ok": True},
                {"bh_daily_sharpe": 0.40, "sma_pnl": 1.0},
            )
            missing = _holdout_metrics(
                "sma_abv_20",
                {"ok": True},
                {"bh_daily_sharpe": None, "sma_pnl": 1.0},
            )
        self.assertTrue(lost["pass"], lost["reason"])
        self.assertEqual(lost["daily_sharpe"], 0.1)
        self.assertEqual(lost["bh_daily_sharpe"], 0.4)
        self.assertEqual(lost["bh_role"], "informational")
        self.assertNotIn("bh", lost["reason"])
        self.assertTrue(missing["pass"], missing["reason"])
        self.assertIsNone(missing["bh_daily_sharpe"])
        self.assertEqual(missing["bh_role"], "informational")

        with patch("scripts.tournament_engine._eval_slice", return_value=[weak]), patch(
            "scripts.tournament_engine._book_daily_returns",
            return_value=[0.01],
        ), patch(
            "hedge_fund.trading.buy_and_hold.daily_returns_sharpe",
            return_value=0.10,
        ):
            soft = _holdout_metrics(
                "sma_abv_20",
                {"ok": True},
                {"bh_daily_sharpe": 0.40, "sma_pnl": 1.0},
            )
        self.assertFalse(soft["pass"])
        self.assertIn("holdout_sharpe", soft["reason"])
        self.assertNotIn("bh", soft["reason"])
        self.assertEqual(soft["bh_daily_sharpe"], 0.4)

    def test_cost_stress_keeps_the_sharpe_floor_and_records_buy_and_hold(self):
        out = self._finish(
            n_trials=1,
            holdout={"pass": True, "sharpe": 0.5, "trades": 10, "pnl": 3.0},
            stress_bh=1e9,
            neighbours=["n1", "n2"],
        )
        self.assertTrue(out["qualified"], out["fail_reasons"])
        self.assertTrue(out["gate_v4"]["cost_pass"])
        self.assertEqual(out["gate_v4"]["cost_bh_role"], "informational")
        self.assertFalse(any("vs bh" in reason for reason in out["fail_reasons"]))

        def cheap(name, slices, taker_fee=None, slippage=None):
            if taker_fee is not None:
                return _scored([1.0, 0.5, 1.2, 0.4], sharpe=0.1)
            if slices == ["eth"]:
                return _scored([-1.0, -0.5, -1.2, -0.4], sharpe=0.4)
            if name == "cand":
                return _scored([10.0, 12.0, 11.0, 13.0])
            return _scored([1.0, 0.5, 1.2, 0.4])

        with patch("hedge_fund.trading.gate_v4.score_name", side_effect=cheap), patch(
            "hedge_fund.trading.gate_v4._holdout_metrics",
            return_value={"pass": True, "sharpe": 0.5, "trades": 10, "pnl": 3.0},
        ), patch(
            "hedge_fund.trading.gate_v4._stressed_bh_daily",
            return_value=-1.0,
        ), patch(
            "hedge_fund.trading.gate_v4.neighbor_names",
            return_value=["n1", "n2"],
        ):
            failed = finish_gate_v4(
                "cand",
                {"qualified": True, "fail_reasons": [], "sharpe": 0.4},
                btc_slices=["btc"],
                eth_slices=["eth"],
                holdout_sample={"ok": True},
                holdout_benchmarks={"ok": True},
                n_trials=1,
            )
        self.assertFalse(failed["qualified"])
        self.assertFalse(failed["gate_v4"]["cost_pass"])
        self.assertTrue(any(reason.startswith("cost stress sharpe") for reason in failed["fail_reasons"]))
        self.assertFalse(any("vs bh" in reason for reason in failed["fail_reasons"]))
        self.assertEqual(failed["gate_v4"]["cost_bh_daily_sharpe"], -1.0)
        self.assertEqual(failed["gate_v4"]["cost_bh_role"], "informational")

    def test_missing_trials_and_lone_spike_still_fail(self):
        missing = self._finish(
            n_trials=None,
            holdout={"pass": True, "sharpe": 0.5, "trades": 10, "pnl": 3.0},
            stress_bh=-0.5,
            neighbours=["n1", "n2"],
        )
        self.assertFalse(missing["qualified"])
        self.assertTrue(any("n_trials" in reason for reason in missing["fail_reasons"]))

        spike = self._finish(
            n_trials=1,
            holdout={"pass": True, "sharpe": 0.5, "trades": 10, "pnl": 3.0},
            stress_bh=-0.5,
            neighbours=[],
        )
        self.assertFalse(spike["qualified"])
        self.assertTrue(any("lone spike" in reason for reason in spike["fail_reasons"]))
