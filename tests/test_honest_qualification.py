"""Honest 5m qualification gates, unbounded paper pool, paper vs buy-and-hold graduation."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.qualify import qualification_from_record, requalify_parked_log
from scripts.tournament_engine import (
    _leftover_batch,
    discover_and_qualify,
    oos_admission_score,
    qualification_decision,
    replenish_and_evaluate,
)


def _win(test_pnl=100.0, test_trades=12, sharpe=0.5, train_pnl=0.0, skipped=False):
    return {
        "test_pnl": None if skipped else test_pnl,
        "test_trades": 0 if skipped else test_trades,
        "sharpe": 0.0 if skipped else sharpe,
        "train_pnl": train_pnl,
        "skipped": skipped,
        "failed": skipped,
    }


def _assert_no_window_veto(reasons: list[str]) -> None:
    joined = " ".join(reasons)
    assert "window[" not in joined
    assert "not all windows non-negative" not in joined


class OosGateTests(unittest.TestCase):
    def test_all_windows_non_negative_required(self):
        # One negative window + passing aggregates → pass. Diagnostic stays.
        windows = [
            _win(test_pnl=400, test_trades=15),
            _win(test_pnl=-10, test_trades=15),
            _win(test_pnl=50, test_trades=15),
        ]
        d = qualification_decision(windows, expected_windows=3, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0)
        self.assertTrue(d["passed"], d["reasons"])
        self.assertGreater(d["tot_test_pnl"], 0)
        self.assertFalse(d["all_windows_nonneg"])
        _assert_no_window_veto(d["reasons"])

        ok = [_win(test_pnl=50, test_trades=12) for _ in range(3)]
        d_ok = qualification_decision(ok, expected_windows=3, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0)
        self.assertTrue(d_ok["passed"], d_ok["reasons"])
        self.assertTrue(d_ok["all_windows_nonneg"])

    def test_skipped_or_empty_window_fails(self):
        # One skipped/empty window + passing aggregates (30+ trades) → pass.
        windows = [
            _win(test_pnl=100, test_trades=20),
            _win(skipped=True),
            _win(test_pnl=100, test_trades=20),
        ]
        d = qualification_decision(windows, expected_windows=3, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0)
        self.assertTrue(d["passed"], d["reasons"])
        self.assertFalse(d["all_windows_nonneg"])
        _assert_no_window_veto(d["reasons"])

        empty = [
            _win(test_pnl=100, test_trades=20),
            _win(test_pnl=80, test_trades=0),
            _win(test_pnl=100, test_trades=20),
        ]
        d2 = qualification_decision(empty, expected_windows=3, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0)
        self.assertTrue(d2["passed"], d2["reasons"])
        self.assertFalse(d2["all_windows_nonneg"])
        _assert_no_window_veto(d2["reasons"])

    def test_train_positive_test_negative_fails(self):
        windows = [
            _win(test_pnl=-5, test_trades=20, train_pnl=10_000),
            _win(test_pnl=1, test_trades=20, train_pnl=10_000),
            _win(test_pnl=1, test_trades=20, train_pnl=10_000),
        ]
        # Aggregate OOS is -3; huge train must not rescue a miss vs B&H.
        d = qualification_decision(windows, expected_windows=3, bh_oos_pnl=0.0, sma_stack_oos_pnl=0.0)
        self.assertFalse(d["passed"])
        _assert_no_window_veto(d["reasons"])

    def test_train_pnl_does_not_boost_weak_oos_score(self):
        weak_oos = oos_admission_score(tot_test_pnl=10, avg_sharpe=0.3)
        strong_oos = oos_admission_score(tot_test_pnl=50, avg_sharpe=0.3)
        # Old rule tot_test + tot_train*0.5 would let train=200 dominate.
        self.assertGreater(strong_oos, weak_oos)
        self.assertEqual(weak_oos, 10 + 3.0)
        windows = [_win(test_pnl=5, test_trades=12, train_pnl=1_000_000) for _ in range(3)]
        d = qualification_decision(windows, expected_windows=3, bh_oos_pnl=0.0, sma_stack_oos_pnl=0.0)
        self.assertAlmostEqual(d["score"], oos_admission_score(15.0, 0.5))
        self.assertNotIn("train", str(d["score"]))

    def test_twenty_nine_oos_trades_fail_thirty_can_pass(self):
        fail = [
            _win(test_pnl=40, test_trades=10),
            _win(test_pnl=40, test_trades=10),
            _win(test_pnl=40, test_trades=9),
        ]
        d_fail = qualification_decision(fail, expected_windows=3, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0)
        self.assertEqual(d_fail["tot_oos_trades"], 29)
        self.assertFalse(d_fail["passed"])

        ok = [
            _win(test_pnl=40, test_trades=10),
            _win(test_pnl=40, test_trades=10),
            _win(test_pnl=40, test_trades=10),
        ]
        d_ok = qualification_decision(ok, expected_windows=3, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0)
        self.assertEqual(d_ok["tot_oos_trades"], 30)
        self.assertTrue(d_ok["passed"], d_ok["reasons"])

    def test_must_beat_buy_and_hold_and_sma_stack(self):
        windows = [_win(test_pnl=20, test_trades=12) for _ in range(3)]  # tot 60
        vs_bh = qualification_decision(windows, expected_windows=3, bh_oos_pnl=80.0, sma_stack_oos_pnl=1.0)
        self.assertFalse(vs_bh["passed"])
        vs_sma = qualification_decision(windows, expected_windows=3, bh_oos_pnl=1.0, sma_stack_oos_pnl=80.0)
        self.assertFalse(vs_sma["passed"])
        beat = qualification_decision(windows, expected_windows=3, bh_oos_pnl=10.0, sma_stack_oos_pnl=10.0)
        self.assertTrue(beat["passed"], beat["reasons"])

    def test_missing_window_slice_still_fails(self):
        windows = [_win(test_pnl=50, test_trades=15) for _ in range(2)]
        d = qualification_decision(windows, expected_windows=3, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0)
        self.assertFalse(d["passed"])
        self.assertTrue(any("windows" in r for r in d["reasons"]))

    def test_window_veto_not_emitted_when_other_gates_fail(self):
        windows = [
            _win(test_pnl=20, test_trades=12),
            _win(test_pnl=20, test_trades=12),
            _win(test_pnl=-5, test_trades=12),
        ]
        d = qualification_decision(windows, expected_windows=3, bh_oos_pnl=80.0, sma_stack_oos_pnl=1.0)
        self.assertFalse(d["passed"])
        self.assertFalse(d["all_windows_nonneg"])
        _assert_no_window_veto(d["reasons"])
        self.assertTrue(any("bh" in r for r in d["reasons"]))

    def test_stored_aggregates_window_only_fail_now_passes(self):
        # Fixture that used to fail only on windows (dbl_bot_120-shaped, but beats B&H).
        row = {
            "strategy": "window_veto_only",
            "qualified": False,
            "sharpe": 0.58,
            "trades": 296,
            "test_pnl": 946.0,
            "bh_oos_pnl": 100.0,
            "sma_stack_oos_pnl": 50.0,
            "regimes_tested": 8,
            "fail_reasons": [
                "window[1] failed/skipped/neg/empty",
                "not all windows non-negative",
            ],
        }
        d = qualification_from_record(row)
        self.assertTrue(d["passed"], d["reasons"])
        self.assertFalse(d["all_windows_nonneg"])

        # Prod-like dbl_bot_120: same window veto, still loses to B&H → stay parked.
        dbl = {
            **row,
            "strategy": "dbl_bot_120",
            "bh_oos_pnl": 2000.0,
        }
        d_dbl = qualification_from_record(dbl)
        self.assertFalse(d_dbl["passed"])
        self.assertTrue(any("bh" in r for r in d_dbl["reasons"]))
        _assert_no_window_veto(d_dbl["reasons"])

        flipped, names = requalify_parked_log([row, dbl], existing_names=set())
        self.assertEqual(names, ["window_veto_only"])
        self.assertTrue(row["qualified"])
        self.assertFalse(dbl["qualified"])
        self.assertEqual(flipped[0]["strategy"], "window_veto_only")

    def test_eight_by_ninety_is_the_default_walk_forward(self):
        from hedge_fund.trading.constants import (
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_COVERAGE_DAYS,
            QUAL_N_WINDOWS,
            QUAL_WINDOW_BARS,
            QUAL_WINDOW_DAYS,
        )

        self.assertEqual(QUAL_N_WINDOWS, 8)
        self.assertEqual(QUAL_WINDOW_DAYS, 90)
        self.assertEqual(QUAL_WINDOW_BARS, 25920)
        self.assertEqual(QUAL_COVERAGE_DAYS, 720)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(MIN_BACKTEST_TRADES, 30)

        windows = [_win(test_pnl=20, test_trades=8) for _ in range(QUAL_N_WINDOWS)]
        d = qualification_decision(
            windows, expected_windows=QUAL_N_WINDOWS, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0
        )
        self.assertTrue(d["passed"], d["reasons"])
        self.assertEqual(d["tot_oos_trades"], 64)

        short = [_win(test_pnl=50, test_trades=15) for _ in range(3)]
        d_short = qualification_decision(
            short, expected_windows=QUAL_N_WINDOWS, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0
        )
        self.assertFalse(d_short["passed"])
        self.assertTrue(any("windows" in r for r in d_short["reasons"]))

        old_row = {
            "strategy": "three_window_legacy",
            "qualified": False,
            "sharpe": 0.58,
            "trades": 296,
            "test_pnl": 946.0,
            "bh_oos_pnl": 100.0,
            "sma_stack_oos_pnl": 50.0,
            "regimes_tested": 3,
        }
        d_old = qualification_from_record(old_row)
        self.assertFalse(d_old["passed"])
        self.assertTrue(any("windows" in r for r in d_old["reasons"]))


class QualTapeTests(unittest.TestCase):
    def _ohlcv(self, n=400, start=100.0, drift=0.001):
        rows = []
        px = start
        ts = 1_600_000_000_000
        for i in range(n):
            px *= 1.0 + drift
            rows.append([ts + i * 300_000, px, px * 1.01, px * 0.99, px])
        return rows

    def test_4h_only_history_does_not_admit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crypto_history_4h.json").write_text(json.dumps({
                "BTC/USDT": self._ohlcv(),
                "ETH/USDT": self._ohlcv(start=10.0),
            }))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                qualified, evaluated = discover_and_qualify(batch_size=5, window_size=80, n_windows=3)
        self.assertEqual(qualified, [])
        self.assertEqual(evaluated, [])

    def test_5m_path_used_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "BTC/USDT": self._ohlcv(),
                "ETH/USDT": self._ohlcv(start=10.0),
            }
            (root / "crypto_history_5m.json").write_text(json.dumps(payload))
            (root / "crypto_history_4h.json").write_text(json.dumps({"should": "not_be_used"}))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                _q, evaluated = discover_and_qualify(batch_size=3, window_size=80, n_windows=3)
        self.assertTrue(evaluated, "5m tape should produce evaluations")
        for rec in evaluated:
            self.assertEqual(rec["timeframe"], "5m")
            self.assertEqual(rec["risk_policy"], "rm_v1")


class PoolAdmitTests(unittest.TestCase):
    def test_pool_at_31_still_admits_new_qualified_and_skips_dup_graduated(self):
        from hedge_fund.trading.champions import load_pool, promote_candidates, save_graduated, save_pool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            champs = [{"name": f"n{i}", "closed": 0, "pnl": 0.0, "wins": 0} for i in range(31)]
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({"champions": champs, "synced_until": ""})
                save_graduated([{"name": "already_grad"}])
                out = promote_candidates([
                    {"strategy": "n0"},
                    {"strategy": "already_grad"},
                    {"strategy": "dbl_bot_12"},
                    {"strategy": "dbl_bot_12&sma_abv_50"},
                ])
                pool = load_pool()["champions"]
        self.assertEqual(out["added"], ["dbl_bot_12", "dbl_bot_12&sma_abv_50"])
        self.assertEqual(out["active_count"], 33)
        self.assertEqual(len(pool), 33)
        new_rows = [c for c in pool if c["name"] in out["added"]]
        self.assertEqual(len(new_rows), 2)
        for row in new_rows:
            self.assertTrue(row.get("champion_since"))

    def test_replenish_still_runs_when_pool_has_31(self):
        from hedge_fund.trading.champions import save_pool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            champs = [{"name": f"n{i}", "closed": 0, "pnl": 0.0, "wins": 0} for i in range(31)]
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({"champions": champs, "synced_until": ""})
                res = replenish_and_evaluate(batch_size=30)
        self.assertNotIn("pool full", str(res.get("reason", "")))
        self.assertEqual(res["active_champions_count"], 31)
        # No 5m history here, so nothing qualifies — but discovery was attempted.
        self.assertEqual(res["admitted_new_count"], 0)


class GraduationVsBuyAndHoldTests(unittest.TestCase):
    def test_graduation_at_80_uses_vs_bh_not_raw_pnl(self):
        from hedge_fund.trading.champions import collect_live_results, load_graduated
        from hedge_fund.trading.constants import (
            GRADUATED_PAPER,
            REJECTED_NEGATIVE_PNL,
            TRADE_EVALUATION_LIMIT,
        )
        from hedge_fund.trading.store import TradeStore

        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                store = TradeStore(root / "trades_loser_vs_bh.sqlite")
                for i in range(TRADE_EVALUATION_LIMIT):
                    tid = store.open_trade(
                        "BTC/USDT", "5m", "sma_stack_long", 0.6, 100.0, 0.01, 0.01, lot_id=i,
                    )
                    # Tiny paper PnL, huge B&H (100 → 150).
                    store.close_trade(tid, 150.0, "take_profit", 0.01, 0.05, 0.5, 1)
                (root / "champions.json").write_text(json.dumps({
                    "champions": [{"name": "loser_vs_bh", "closed": 0, "pnl": 0.0, "wins": 0}],
                    "synced_until": "",
                }))
                out = collect_live_results()
                self.assertTrue(out["graduated"])
                self.assertGreater(out["graduated"][0]["total_pnl"], 0)
                self.assertEqual(out["graduated"][0]["status"], REJECTED_NEGATIVE_PNL)
                self.assertEqual(load_graduated()[0]["status"], REJECTED_NEGATIVE_PNL)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                store = TradeStore(root / "trades_beater.sqlite")
                for i in range(TRADE_EVALUATION_LIMIT):
                    tid = store.open_trade(
                        "BTC/USDT", "5m", "sma_stack_long", 0.6, 100.0, 0.01, 0.01, lot_id=i,
                    )
                    store.close_trade(tid, 100.0, "take_profit", 0.01, 1.0, 0.0, 1)
                (root / "champions.json").write_text(json.dumps({
                    "champions": [{"name": "beater", "closed": 0, "pnl": 0.0, "wins": 0}],
                    "synced_until": "",
                }))
                out = collect_live_results()
                self.assertEqual(out["graduated"][0]["status"], GRADUATED_PAPER)


class LeftoverDrainTests(unittest.TestCase):
    def test_leftover_batch_returns_all_untested_not_a_sample_of_30(self):
        leftovers = [f"cand_{i}" for i in range(40)]
        blocked = {"cand_0"}
        out = _leftover_batch(leftovers, blocked)
        self.assertGreater(len(out), 30)
        self.assertEqual(len(out), 39)
        self.assertNotIn("cand_0", out)
        capped = _leftover_batch(leftovers, set(), batch_size=5)
        self.assertEqual(len(capped), 5)

    def test_replenish_one_cycle_stays_within_budget_and_later_cycles_drain(self):
        leftovers = [f"cand_{i}" for i in range(40)]
        from hedge_fund.trading.constants import QUAL_N_WINDOWS

        dummy_windows = [
            {
                "train_pnl": 0.0,
                "test_pnl": -1.0,
                "test_trades": 1,
                "trades": 1,
                "wins": 0,
                "sharpe": 0.0,
                "skipped": False,
                "failed": False,
            }
            for _ in range(QUAL_N_WINDOWS)
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with (
                    patch(
                        "scripts.tournament_engine.generate_candidate_pool",
                        return_value=leftovers,
                    ),
                    patch(
                        "scripts.tournament_engine._load_qual_history",
                        return_value={"BTC/USDT": [[0] * 5] * 10, "ETH/USDT": [[0] * 5] * 10},
                    ),
                    patch(
                        "scripts.tournament_engine._window_slices",
                        return_value=[{} for _ in range(QUAL_N_WINDOWS)],
                    ),
                    patch(
                        "scripts.tournament_engine._benchmark_oos",
                        return_value=(0.0, 0.0),
                    ),
                    patch(
                        "scripts.tournament_engine.parse_strategy",
                        return_value=lambda *a, **k: True,
                    ),
                    patch(
                        "scripts.tournament_engine.evaluate_windows",
                        return_value=dummy_windows,
                    ) as ev,
                ):
                    from hedge_fund.trading.constants import DISCOVER_CYCLE_MAX_NAMES
                    from hedge_fund.trading.discovery import load_cursor, load_discovery_log

                    res = replenish_and_evaluate(cooldown_seconds=0)
                    self.assertLessEqual(res["total_tested_in_batch"], DISCOVER_CYCLE_MAX_NAMES)
                    self.assertGreater(res["total_tested_in_batch"], 0)
                    self.assertEqual(ev.call_count, res["total_tested_in_batch"])
                    self.assertEqual(res["admitted_new_count"], 0)
                    first_names = {r["strategy"] for r in load_discovery_log()}
                    self.assertEqual(len(first_names), res["total_tested_in_batch"])
                    self.assertTrue(load_cursor().get("next_name"))

                    res2 = replenish_and_evaluate(cooldown_seconds=0)
                    self.assertEqual(res2["total_tested_in_batch"], DISCOVER_CYCLE_MAX_NAMES)
                    log = load_discovery_log()
                    unique = {r["strategy"] for r in log}
                    self.assertEqual(len(unique), DISCOVER_CYCLE_MAX_NAMES * 2)
                    self.assertTrue(first_names.isdisjoint({r["strategy"] for r in log[: res2["total_tested_in_batch"]]}))
        self.assertEqual(res["active_champions_count"], 0)


class NearDuplicateTests(unittest.TestCase):
    def test_tiny_param_tweaks_share_a_key(self):
        from hedge_fund.trading.universe import near_duplicate_key

        self.assertEqual(near_duplicate_key("sma_stack_5_20_50"), near_duplicate_key("sma_stack_5_21_51"))
        self.assertEqual(near_duplicate_key("rsi_14_>50&sma_abv_50"), near_duplicate_key("sma_abv_50&rsi_14_>52"))


if __name__ == "__main__":
    unittest.main()
