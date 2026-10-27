"""Parameter neighbours, cost stress, and the v4 attestation."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.backtest.strategies import backtest
from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.trading.constants import DISCOVERY_N_WINDOWS
from hedge_fund.trading.gate_v4 import (
    finish_gate_v4,
    neighbour_fraction_ok,
    stress_fees,
    v4_attestation_ok,
    v4_pass_attestation,
)
from hedge_fund.trading.perturb import neighbor_names


CHAMP = "dip_234b_lt8pc&h4_ema_abv_140"
NEIGHBOURS = [
    "dip_210b_lt8pc&h4_ema_abv_140",
    "dip_258b_lt8pc&h4_ema_abv_140",
    "dip_186b_lt8pc&h4_ema_abv_140",
    "dip_282b_lt8pc&h4_ema_abv_140",
    "dip_234b_lt6pc&h4_ema_abv_140",
    "dip_234b_lt10pc&h4_ema_abv_140",
    "dip_234b_lt8pc&h4_ema_abv_130",
    "dip_234b_lt8pc&h4_ema_abv_150",
    "dip_234b_lt8pc&h4_ema_abv_110",
    "dip_234b_lt8pc&h4_ema_abv_170",
]


def _windows(pnls: list[float], sharpe: float = 0.8) -> list[dict]:
    # Four windows so a CSCV half has two observations and a real Sharpe.
    # A one-row half is zero-variance and ties every positive column.
    return [
        {
            "sharpe": sharpe,
            "test_trades": 10,
            "test_pnl": pnl,
            "daily_returns": [0.002, 0.001 * ((i % 3) + 1), 0.0005],
            "max_drawdown": 0.04 + 0.01 * (i % 3),
        }
        for i, pnl in enumerate(pnls)
    ]


class NeighbourTests(unittest.TestCase):
    def test_champion_neighbours_are_one_parameter_steps(self):
        self.assertEqual(neighbor_names(CHAMP), NEIGHBOURS)

    def test_fraction_floor(self):
        self.assertFalse(neighbour_fraction_ok(0, 10)[0])
        self.assertFalse(neighbour_fraction_ok(6, 10)[0])
        self.assertTrue(neighbour_fraction_ok(7, 10)[0])
        self.assertTrue(neighbour_fraction_ok(8, 10)[0])
        ok, reason = neighbour_fraction_ok(0, 0)
        self.assertFalse(ok)
        self.assertIn("lone spike", reason or "")


class CostStressTests(unittest.TestCase):
    def test_stress_fees_are_double_taker_plus_five_bps(self):
        self.assertEqual(stress_fees(), (0.002, 0.0007))

    def test_stressed_backtest_pays_more_fees(self):
        closes = [100.0 + i * 0.15 for i in range(500)]
        highs = [c * 1.004 for c in closes]
        lows = [c * 0.996 for c in closes]
        pred = parse_strategy("sma_abv_20")
        base = backtest(closes, highs, lows, pred)
        fee, slip = stress_fees()
        stressed = backtest(closes, highs, lows, pred, taker_fee=fee, slippage=slip)
        self.assertGreater(base.trades, 0)
        self.assertGreater(stressed.fees_paid, base.fees_paid)


class FinishGateTests(unittest.TestCase):
    def _run(self, *, n_trials, eth_sharpe=0.4, neighbours=None, record_sharpe=0.4):
        eth = ["eth"]
        btc = ["btc"]
        names = ["n1", "n2"] if neighbours is None else neighbours

        def fake(name, slices, taker_fee=None, slippage=None):
            if slices is eth:
                return _windows([-1.0, -0.5, -1.2, -0.4], sharpe=eth_sharpe)
            if name == "cand":
                return _windows([10.0, 12.0, 11.0, 13.0])
            return _windows([1.0, 0.5, 1.2, 0.4])

        with patch("hedge_fund.trading.gate_v4.score_name", side_effect=fake), patch(
            "hedge_fund.trading.gate_v4._holdout_metrics",
            return_value={"pass": True, "sharpe": 0.5, "trades": 10, "pnl": 3.0},
        ), patch(
            "hedge_fund.trading.gate_v4._stressed_bh_daily",
            return_value=-0.5,
        ), patch(
            "hedge_fund.trading.gate_v4.neighbor_names",
            return_value=list(names),
        ):
            return finish_gate_v4(
                "cand",
                {"qualified": True, "fail_reasons": [], "sharpe": record_sharpe},
                btc_slices=btc,
                eth_slices=eth,
                holdout_sample={"ok": True},
                holdout_benchmarks={"ok": True},
                n_trials=n_trials,
            )

    def test_hard_passes_keep_qualification_when_eth_is_soft_fail(self):
        out = self._run(n_trials=1, eth_sharpe=-0.4)
        self.assertTrue(out["qualified"], out["fail_reasons"])
        self.assertTrue(out["gate_v4"]["passed"])
        self.assertFalse(out["gate_v4"]["cross_asset_pass"])
        self.assertEqual(out["sharpe"], 0.4)
        self.assertEqual(out["gate_v4"]["holdout"]["sharpe"], 0.5)
        self.assertTrue(v4_attestation_ok(out))

    def test_missing_trial_count_fails_closed(self):
        out = self._run(n_trials=None)
        self.assertFalse(out["qualified"])
        self.assertFalse(out["gate_v4"]["dsr_pass"])
        self.assertFalse(v4_attestation_ok(out))

    def test_lone_spike_fails(self):
        out = self._run(n_trials=1, neighbours=[])
        self.assertFalse(out["qualified"])
        self.assertFalse(out["gate_v4"]["perturb_pass"])

    def test_attestation_rejects_a_qualified_row_without_the_block(self):
        self.assertFalse(v4_attestation_ok({"qualified": True, "gate_rules": "x"}))
        self.assertTrue(v4_attestation_ok({"gate_v4": v4_pass_attestation()}))


class ClaimTrialCountTests(unittest.TestCase):
    def test_claim_mode_does_not_substitute_a_local_census(self):
        from scripts.discovery_worker import run_batch

        seen = {}

        def track_eval(name, *args, **kwargs):
            return {
                "strategy": name,
                "qualified": True,
                "fail_reasons": [],
                "sharpe": 0.4,
                "trades": 40,
                "test_pnl": 1.0,
            }

        def fake_finish(name, record, **kwargs):
            seen["n_trials"] = kwargs.get("n_trials")
            out = dict(record)
            out["qualified"] = False
            out["fail_reasons"] = ["dsr n_trials missing"]
            return out

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crypto_history_5m.json").write_text("{}")
            env = {
                "PAPER_STATE": str(root),
                "DISCOVERY_EVAL_TIMEOUT_SECONDS": "0",
            }
            with patch.dict(os.environ, env), patch(
                "scripts.discovery_worker._load_qual_history",
                return_value={"BTC/USDT": [1]},
            ), patch(
                "scripts.discovery_worker._window_slices",
                return_value=[{}] * DISCOVERY_N_WINDOWS,
            ), patch(
                "scripts.discovery_worker._benchmark_oos",
                return_value=(1.0, 1.0),
            ), patch(
                "scripts.discovery_worker._plan_batch",
                return_value=(["sma_abv_20"], ["sma_abv_20"], []),
            ), patch(
                "scripts.discovery_worker.evaluate_strategy_record",
                side_effect=track_eval,
            ), patch(
                "hedge_fund.trading.gate_v4.finish_gate_v4",
                side_effect=fake_finish,
            ), patch(
                "hedge_fund.trading.trials.trial_census",
                return_value={"n_trials": 1},
            ):
                run_batch(
                    workers=1,
                    max_names=1,
                    ingest_url=None,
                    token=None,
                    names=["sma_abv_20"],
                    claim_mode=True,
                    n_trials=None,
                )
        self.assertIsNone(seen["n_trials"])


if __name__ == "__main__":
    unittest.main()
