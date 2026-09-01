"""Honest 4h qualification gates, small arena, paper vs buy-and-hold graduation."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.tournament_engine import (
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


class OosGateTests(unittest.TestCase):
    def test_all_windows_non_negative_required(self):
        # Sum of OOS is positive, but one window is negative → fail.
        windows = [
            _win(test_pnl=400, test_trades=15),
            _win(test_pnl=-10, test_trades=15),
            _win(test_pnl=50, test_trades=15),
        ]
        d = qualification_decision(windows, expected_windows=3, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0)
        self.assertFalse(d["passed"])
        self.assertGreater(d["tot_test_pnl"], 0)
        self.assertFalse(d["all_windows_nonneg"])

        ok = [_win(test_pnl=50, test_trades=12) for _ in range(3)]
        d_ok = qualification_decision(ok, expected_windows=3, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0)
        self.assertTrue(d_ok["passed"], d_ok["reasons"])

    def test_skipped_or_empty_window_fails(self):
        windows = [_win(), _win(skipped=True), _win()]
        d = qualification_decision(windows, expected_windows=3, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0)
        self.assertFalse(d["passed"])
        empty = [_win(), _win(test_pnl=80, test_trades=0), _win()]
        d2 = qualification_decision(empty, expected_windows=3, bh_oos_pnl=1.0, sma_stack_oos_pnl=1.0)
        self.assertFalse(d2["passed"])

    def test_train_positive_test_negative_fails(self):
        windows = [
            _win(test_pnl=-5, test_trades=20, train_pnl=10_000),
            _win(test_pnl=1, test_trades=20, train_pnl=10_000),
            _win(test_pnl=1, test_trades=20, train_pnl=10_000),
        ]
        d = qualification_decision(windows, expected_windows=3, bh_oos_pnl=-100, sma_stack_oos_pnl=-100)
        self.assertFalse(d["passed"])

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


class QualTapeTests(unittest.TestCase):
    def _ohlcv(self, n=400, start=100.0, drift=0.001):
        rows = []
        px = start
        ts = 1_600_000_000_000
        for i in range(n):
            px *= 1.0 + drift
            rows.append([ts + i * 14_400_000, px, px * 1.01, px * 0.99, px])
        return rows

    def test_5m_only_history_does_not_admit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crypto_history_5m.json").write_text(json.dumps({
                "BTC/USDT": self._ohlcv(),
                "ETH/USDT": self._ohlcv(start=10.0),
            }))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                qualified, evaluated = discover_and_qualify(batch_size=5, window_size=80, n_windows=3)
        self.assertEqual(qualified, [])
        self.assertEqual(evaluated, [])

    def test_4h_path_used_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "BTC/USDT": self._ohlcv(),
                "ETH/USDT": self._ohlcv(start=10.0),
            }
            (root / "crypto_history_4h.json").write_text(json.dumps(payload))
            (root / "crypto_history_5m.json").write_text(json.dumps({"should": "not_be_used"}))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with patch("scripts.tournament_engine.random.sample", side_effect=lambda pop, k: list(pop)[:k]):
                    _q, evaluated = discover_and_qualify(batch_size=3, window_size=80, n_windows=3)
        self.assertTrue(evaluated, "4h tape should produce evaluations")
        for rec in evaluated:
            self.assertEqual(rec["timeframe"], "4h")
            self.assertEqual(rec["risk_policy"], "rm_v1")


class PoolCapTests(unittest.TestCase):
    def test_pool_will_not_exceed_20_and_replenish_only_free_slots(self):
        from hedge_fund.trading.champions import load_pool, promote_candidates, save_pool
        from hedge_fund.trading.constants import MAX_ACTIVE_CHAMPIONS

        self.assertEqual(MAX_ACTIVE_CHAMPIONS, 20)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({"champions": [], "synced_until": ""})
                cands = [{"strategy": f"sma_abv_{20 + i}"} for i in range(50)]
                out = promote_candidates(cands)
                self.assertEqual(len(out["added"]), 20)
                self.assertEqual(out["active_count"], 20)
                self.assertEqual(len(load_pool()["champions"]), 20)

                more = promote_candidates([{"strategy": "rsi_14_>50"}])
                self.assertEqual(more["added"], [])
                self.assertEqual(more["active_count"], 20)

                st = load_pool()
                st["champions"] = st["champions"][:19]
                save_pool(st)
                one = promote_candidates([
                    {"strategy": "mom_12b_gt3pc"},
                    {"strategy": "dip_6b_lt2pc"},
                    {"strategy": "bb_lower_20_2"},
                ])
                self.assertEqual(len(one["added"]), 1)
                self.assertEqual(one["active_count"], 20)

    def test_replenish_skips_work_when_pool_full(self):
        from hedge_fund.trading.champions import save_pool
        from hedge_fund.trading.constants import MAX_ACTIVE_CHAMPIONS

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            champs = [{"name": f"n{i}", "closed": 0, "pnl": 0.0, "wins": 0} for i in range(MAX_ACTIVE_CHAMPIONS)]
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_pool({"champions": champs, "synced_until": ""})
                res = replenish_and_evaluate(batch_size=30)
        self.assertEqual(res["admitted_new_count"], 0)
        self.assertEqual(res["total_tested_in_batch"], 0)
        self.assertIn("pool full", res["reason"])


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
                        "BTC/USDT", "4h", "sma_stack_long", 0.6, 100.0, 0.01, 0.01, lot_id=i,
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
                        "BTC/USDT", "4h", "sma_stack_long", 0.6, 100.0, 0.01, 0.01, lot_id=i,
                    )
                    store.close_trade(tid, 100.0, "take_profit", 0.01, 1.0, 0.0, 1)
                (root / "champions.json").write_text(json.dumps({
                    "champions": [{"name": "beater", "closed": 0, "pnl": 0.0, "wins": 0}],
                    "synced_until": "",
                }))
                out = collect_live_results()
                self.assertEqual(out["graduated"][0]["status"], GRADUATED_PAPER)


class NearDuplicateTests(unittest.TestCase):
    def test_tiny_param_tweaks_share_a_key(self):
        from hedge_fund.trading.universe import near_duplicate_key

        self.assertEqual(near_duplicate_key("sma_stack_5_20_50"), near_duplicate_key("sma_stack_5_21_51"))
        self.assertEqual(near_duplicate_key("rsi_14_>50&sma_abv_50"), near_duplicate_key("sma_abv_50&rsi_14_>52"))


if __name__ == "__main__":
    unittest.main()
