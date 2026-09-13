"""Golden / snapshot parity for discovery walk-forward qualify.

Locks current-main OOS decisions and metrics on a deterministic 5m fixture
so later speedups (indicator caches, less pandas/Python churn) cannot
change admit semantics.

Exercises the live path used by ``scripts/discovery_worker.py``:
``evaluate_strategy_record`` → ``evaluate_windows`` → ``bs.backtest``
(not ``hedge_fund.backtest.fast_quant``).

Fixture is trimmed: 8 × 960 5m bars/symbol (~3.3d/window), not jensa's
8 × 25920. Same window count, stride, 70/30 cut, rm_v1, and frozen
gates. See ``tests/fixtures/walkforward_parity.py``.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from hedge_fund.signals.dynamic import parse_strategy
from hedge_fund.trading.constants import (
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    QUAL_N_WINDOWS,
    QUAL_STRIDE,
    QUAL_TIMEFRAME,
    QUAL_WINDOW_BARS,
    RISK_POLICY,
)
from tests.fixtures.walkforward_parity import (
    PARITY_N_WINDOWS,
    PARITY_STRATEGY_NAMES,
    PARITY_STRIDE,
    PARITY_WINDOW_BARS,
    RECORD_COMPARE_KEYS,
    WINDOW_COMPARE_KEYS,
    build_golden_payload,
)

GOLDEN_PATH = Path(__file__).resolve().parent / "fixtures" / "walkforward_parity_golden.json"

# Documented float tolerance for raw window aggregates (record fields are
# already rounded to 2 decimals by evaluate_strategy_record). Default is
# exact equality (0.0). Widen only with a comment if a justified numeric
# rewrite needs it — never to hide an admit-bit flip.
WINDOW_FLOAT_ABS_TOL = 0.0


def _load_golden() -> dict:
    if not GOLDEN_PATH.exists():
        raise FileNotFoundError(
            f"missing {GOLDEN_PATH}; generate with: "
            "python3 -m tests.fixtures.walkforward_parity"
        )
    return json.loads(GOLDEN_PATH.read_text())


def _assert_close(tc: unittest.TestCase, got, exp, *, path: str) -> None:
    if type(got) is not type(exp) and not (
        isinstance(got, (int, float)) and isinstance(exp, (int, float))
    ):
        tc.assertEqual(type(got), type(exp), f"{path} type {type(got)} != {type(exp)}")
    if got is None or exp is None:
        tc.assertEqual(got, exp, path)
        return
    if isinstance(exp, bool) or isinstance(got, bool):
        tc.assertEqual(got, exp, path)
        return
    if isinstance(exp, float) or isinstance(got, float):
        if WINDOW_FLOAT_ABS_TOL == 0.0:
            tc.assertEqual(got, exp, path)
        else:
            tc.assertAlmostEqual(got, exp, delta=WINDOW_FLOAT_ABS_TOL, msg=path)
        return
    if isinstance(exp, list):
        tc.assertEqual(len(got), len(exp), f"{path} len")
        for i, (g, e) in enumerate(zip(got, exp)):
            _assert_close(tc, g, e, path=f"{path}[{i}]")
        return
    if isinstance(exp, dict):
        tc.assertEqual(set(got), set(exp), path)
        for k in exp:
            _assert_close(tc, got[k], exp[k], path=f"{path}.{k}")
        return
    tc.assertEqual(got, exp, path)


class FrozenQualifyGatesTests(unittest.TestCase):
    def test_oos_gate_thresholds_untouched(self):
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 8)
        self.assertEqual(PARITY_N_WINDOWS, QUAL_N_WINDOWS)
        self.assertEqual(QUAL_STRIDE, 1)
        self.assertEqual(PARITY_STRIDE, QUAL_STRIDE)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(QUAL_WINDOW_BARS, 90 * 24 * 12)
        self.assertLess(PARITY_WINDOW_BARS, QUAL_WINDOW_BARS)

    def test_fast_quant_is_not_on_qualify_path(self):
        import scripts.discovery_worker as worker
        import scripts.tournament_engine as te

        leftover = Path(te.__file__).resolve().parents[1] / "hedge_fund" / "backtest" / "fast_quant.py"
        self.assertTrue(leftover.exists())
        self.assertIn("NOT the live qualification filter", leftover.read_text()[:400])
        src = Path(te.__file__).read_text()
        self.assertNotIn("from hedge_fund.backtest.fast_quant", src)
        self.assertNotIn("import hedge_fund.backtest.fast_quant", src)
        self.assertIn("hedge_fund.backtest.strategies", src)
        self.assertIn("evaluate_strategy_record", Path(worker.__file__).read_text())


class WalkForwardParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = _load_golden()
        cls.payload = build_golden_payload()

    def test_fixture_meta_matches_live_topology(self):
        meta = self.payload["meta"]
        gmeta = self.golden["meta"]
        self.assertEqual(meta["n_windows"], 8)
        self.assertEqual(meta["window_bars"], PARITY_WINDOW_BARS)
        self.assertEqual(meta["stride"], 1)
        self.assertEqual(meta["live_window_bars"], QUAL_WINDOW_BARS)
        self.assertEqual(meta["timeframe"], "5m")
        self.assertEqual(meta["risk_policy"], "rm_v1")
        self.assertEqual(meta["n_windows"], gmeta["n_windows"])
        self.assertEqual(meta["window_bars"], gmeta["window_bars"])
        self.assertEqual(meta["seed"], gmeta["seed"])

    def test_golden_covers_htf_mom_and_structure(self):
        names = [row["name"] for row in self.golden["strategies"]]
        self.assertEqual(tuple(names), PARITY_STRATEGY_NAMES)
        self.assertTrue(any("&mom_" in n and n.startswith("h1_") for n in names))
        self.assertTrue(any("&mom_" in n and n.startswith("h4_") for n in names))
        self.assertTrue(any("don_hi_" in n for n in names))
        for name in names:
            pred = parse_strategy(name)
            self.assertTrue(callable(pred))

    def test_benchmark_oos_matches_golden(self):
        self.assertEqual(self.payload["bh_oos_pnl"], self.golden["bh_oos_pnl"])
        self.assertEqual(self.payload["sma_stack_oos_pnl"], self.golden["sma_stack_oos_pnl"])
        _assert_close(
            self,
            self.payload["bh_oos_pnl_raw"],
            self.golden["bh_oos_pnl_raw"],
            path="bh_oos_pnl_raw",
        )
        _assert_close(
            self,
            self.payload["sma_stack_oos_pnl_raw"],
            self.golden["sma_stack_oos_pnl_raw"],
            path="sma_stack_oos_pnl_raw",
        )

    def test_strategy_records_and_windows_match_golden(self):
        got_by_name = {row["name"]: row for row in self.payload["strategies"]}
        self.assertEqual(len(got_by_name), len(self.golden["strategies"]))
        for exp in self.golden["strategies"]:
            name = exp["name"]
            got = got_by_name[name]
            self.assertIsNotNone(got["record"], name)
            self.assertIsNotNone(exp["record"], name)
            for key in RECORD_COMPARE_KEYS:
                if key == "score" and key not in exp["record"] and key not in got["record"]:
                    continue
                _assert_close(
                    self,
                    got["record"].get(key),
                    exp["record"].get(key),
                    path=f"{name}.record.{key}",
                )
            self.assertEqual(len(got["windows"]), 8, name)
            self.assertEqual(len(exp["windows"]), 8, name)
            for i, (gw, ew) in enumerate(zip(got["windows"], exp["windows"])):
                for key in WINDOW_COMPARE_KEYS:
                    _assert_close(
                        self,
                        gw.get(key),
                        ew.get(key),
                        path=f"{name}.windows[{i}].{key}",
                    )

    def test_records_use_frozen_qual_fields(self):
        for row in self.payload["strategies"]:
            rec = row["record"]
            self.assertEqual(rec["timeframe"], "5m")
            self.assertEqual(rec["risk_policy"], "rm_v1")
            self.assertEqual(rec["regimes_tested"], 8)
            self.assertIsInstance(rec["qualified"], bool)
            self.assertIsInstance(rec["fail_reasons"], list)
            self.assertIsInstance(rec["trades"], int)
            self.assertIsInstance(rec["all_windows_nonneg"], bool)
