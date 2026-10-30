"""Qualified paper admits of lib.rsi2 and lib.sma200 under gate v7b.

Alexander, 2026-10-29 11:01 CET. Neighbours are a warning, so both names
pass. The batch seats each once from a qualified verdict. lib.sma100 stays
the unproven forward test. A fresh book whose signal is already on fills
at the next 00:00 UTC open.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

from hedge_fund.brokers.paper import SLIPPAGE, TAKER_FEE, PaperBroker
from hedge_fund.library.catalog import claim_names, parse_variant
from hedge_fund.library.daily import DailyBars
from hedge_fund.library.daily_cache import parse_daily_npz
from hedge_fund.library.engine import ENGINE_ID, RISK_POLICY, TIMEFRAME, Costs, buy_fill
from hedge_fund.library.evaluate import build_library_data, evaluate_library_record
from hedge_fund.library.live import account_path, run_library_account
from hedge_fund.trading.constants import GATE_RULES, LIBRARY_TAPE_START_MS, PAPER_START_CASH
from hedge_fund.trading.store import TradeStore

RSI = "lib.rsi2"
SMA200 = "lib.sma200"
SMA100 = "lib.sma100"
BATCH = "lib-rsi2-sma200-v7-20261029"
A = "dip_204b_lt8pc&h4_ema_abv_150"
B = "h1_ema_abv_50&mom_18b_gt2pc&sma_abv_30&rsi_14_>45"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "btc_library_daily_2017.npz"
PASSES = {SMA100, RSI, SMA200}


def _record(name: str, *, qualified: bool = True, warnings: list[str] | None = None) -> dict:
    shown = ["neighbours 0/3 = 0% < 70%"] if warnings is None and name == SMA200 else list(warnings or [])
    if warnings is None and name == RSI:
        shown = ["dsr 0.064 < 0.95 (n_trials=64)"]
    return {
        "strategy": name,
        "tested_at": "2026-10-29T11:01:00+01:00",
        "qualified": qualified,
        "sharpe": 0.9,
        "daily_sharpe": 0.9,
        "win_rate_pct": 40.0,
        "test_pnl": 100.0,
        "trades": 27,
        "gate_rules": GATE_RULES,
        "backtest_max_dd": 0.33,
        "fail_reasons": [] if qualified else ["oos_pnl -1.00 <= 0"],
        "warnings": shown,
        "timeframe": TIMEFRAME,
        "risk_policy": RISK_POLICY,
        "engine": ENGINE_ID,
        "gate_v4": {
            "passed": qualified,
            "rules": GATE_RULES,
            "dsr": 0.59,
            "pbo": 0.3,
            "n_trials": 64,
            "neighbours": 3,
            "neighbours_pass": 0,
            "backtest_max_dd": 0.33,
            "cost_sharpe": 0.4,
            "cost_daily_sharpe": 0.4,
        },
        "library": {
            "key": name.split(".", 1)[1],
            "title": name,
            "params": {"n": 2},
            "citation": "test",
            "safety_stop_pct": 20.0,
            "warning_checks": ["neighbours"] if name == SMA200 else ["dsr"],
        },
    }


def _env(root: Path):
    env = dict(os.environ)
    env["PAPER_STATE"] = str(root)
    return patch.dict(os.environ, env, clear=True)


def _seed(root: Path) -> dict:
    sma100 = {
        "name": SMA100,
        "source": "forward_test_unproven",
        "admission": "forward_test_unproven",
        "qualified": False,
        "engine": ENGINE_ID,
        "timeframe": TIMEFRAME,
        "risk_policy": RISK_POLICY,
        "closed": 0,
        "pnl": 0.0,
        "wins": 0,
        "champion_since": "2026-10-29T08:30:00+00:00",
    }
    champs = [
        {"name": A, "source": "5m_qualification_filter", "champion_since": "2026-10-09T03:43:40+00:00", "closed": 4, "pnl": 12.5, "wins": 2},
        {"name": B, "source": "5m_qualification_filter", "champion_since": "2026-10-11T09:00:00+00:00", "closed": 1, "pnl": -3.0, "wins": 0},
        sma100,
    ]
    (root / "champions.json").write_text(json.dumps({
        "champions": champs,
        "synced_until": "2026-10-28T00:00:00+00:00",
    }))
    (root / "graduated.json").write_text("[]")
    (root / "retired.json").write_text(json.dumps({"retired": {}, "applied": [], "batches": []}))
    (root / "discovery_log.json").write_text(json.dumps([
        {"strategy": RSI, "qualified": False, "fail_reasons": ["old"]},
        {"strategy": SMA200, "qualified": False, "fail_reasons": ["neighbours 0/3 = 0% < 70%"]},
    ]))
    (root / "approved_admits.json").write_text(json.dumps({
        "applied": ["rules-v3-admit-20261009", "lib-sma100-forward-test-20261029"],
        "batches": [],
        "paper_only": True,
    }))
    return sma100


class SpecTests(unittest.TestCase):
    def test_batch_is_the_qualified_path_and_leaves_sma100_alone(self):
        from hedge_fund.trading.approved_admit import (
            APPROVED_ADMITS,
            FORWARD_TEST_ADMITS,
            LIBRARY_APPROVED_ADMITS,
            LIBRARY_APPROVED_BATCH,
        )

        self.assertEqual(LIBRARY_APPROVED_BATCH, BATCH)
        self.assertEqual([row["name"] for row in LIBRARY_APPROVED_ADMITS], [RSI, SMA200])
        for row in LIBRARY_APPROVED_ADMITS:
            self.assertEqual(row["approved_by"], "Alexander")
            self.assertEqual(row["approved_at"], "2026-10-29T11:01+01:00")
        self.assertEqual([row["name"] for row in FORWARD_TEST_ADMITS], [SMA100])
        self.assertNotIn(RSI, [row["name"] for row in FORWARD_TEST_ADMITS])
        self.assertEqual([row["name"] for row in APPROVED_ADMITS], [A])


class AdmitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sma100 = _seed(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, records: dict[str, dict]):
        from hedge_fund.trading.approved_admit import run_approved_admits

        def evaluate(name, _data, tested_at=None):
            row = dict(records[name])
            if tested_at:
                row["tested_at"] = tested_at
            return row

        with _env(self.root), patch(
            "hedge_fund.library.tape.load_library_daily",
            return_value=DailyBars(
                ts=np.array([0], dtype=np.int64),
                open=np.array([1.0]),
                high=np.array([1.0]),
                low=np.array([1.0]),
                close=np.array([1.0]),
            ),
        ), patch(
            "hedge_fund.library.evaluate.build_library_data",
            return_value=object(),
        ), patch(
            "hedge_fund.library.evaluate.evaluate_library_record",
            side_effect=evaluate,
        ) as ev:
            out = run_approved_admits()
        return out, ev

    def test_each_name_seats_once_with_warnings_and_approval(self):
        before = json.loads((self.root / "champions.json").read_text())
        before_log = (self.root / "discovery_log.json").read_bytes()
        warnings = {
            RSI: ["dsr 0.064 < 0.95 (n_trials=64)", "oos_pnl 2546.03 <= buy-and-hold 271534.12"],
            SMA200: ["dsr 0.590 < 0.95 (n_trials=64)", "neighbours 0/3 = 0% < 70%"],
        }
        records = {name: _record(name, warnings=warnings[name]) for name in (RSI, SMA200)}
        first, ev1 = self._run(records)
        second, ev2 = self._run(records)
        self.assertEqual(first["admitted"], [RSI, SMA200])
        self.assertEqual(first["batch_id"], BATCH)
        self.assertIsNone(second)
        self.assertEqual(ev1.call_count, 2)
        self.assertEqual(ev2.call_count, 0)
        pool = json.loads((self.root / "champions.json").read_text())
        self.assertEqual(pool["synced_until"], "2026-10-28T00:00:00+00:00")
        by_name = {c["name"]: c for c in pool["champions"]}
        self.assertEqual(list(by_name), [A, B, SMA100, RSI, SMA200])
        self.assertEqual(by_name[A], before["champions"][0])
        self.assertEqual(by_name[B], before["champions"][1])
        self.assertEqual(by_name[SMA100], before["champions"][2])
        self.assertEqual(by_name[SMA100]["admission"], "forward_test_unproven")
        self.assertIs(by_name[SMA100]["qualified"], False)
        self.assertEqual((self.root / "discovery_log.json").read_bytes(), before_log)
        for name in (RSI, SMA200):
            row = by_name[name]
            self.assertIs(row["qualified"], True)
            self.assertEqual(row["source"], "library_qualification")
            self.assertEqual(row["engine"], ENGINE_ID)
            self.assertEqual(row["timeframe"], TIMEFRAME)
            self.assertEqual(row["risk_policy"], RISK_POLICY)
            self.assertEqual(row["library"]["safety_stop_pct"], 20.0)
            self.assertIs(row["gate_v4"]["passed"], True)
            self.assertEqual(row["warnings"], warnings[name])
            self.assertEqual(row["provenance"]["approved_by"], "Alexander")
            self.assertEqual(row["provenance"]["approved_at"], "2026-10-29T11:01+01:00")
            self.assertEqual(row["provenance"]["admit_batch"], BATCH)
            self.assertEqual(row["provenance"]["gate_rules"], GATE_RULES)
            self.assertEqual(row["provenance"]["warnings"], warnings[name])
            self.assertNotIn("admission", row)
        marker = json.loads((self.root / "approved_admits.json").read_text())
        self.assertEqual(marker["applied"].count(BATCH), 1)
        self.assertTrue(marker["paper_only"])

    def test_an_unqualified_verdict_seats_nobody_and_leaves_no_marker(self):
        before = (self.root / "champions.json").read_bytes()
        before_marker = (self.root / "approved_admits.json").read_bytes()
        records = {
            RSI: _record(RSI),
            SMA200: _record(SMA200, qualified=False),
        }
        out, ev = self._run(records)
        self.assertIsNone(out)
        self.assertEqual(ev.call_count, 2)
        self.assertEqual((self.root / "champions.json").read_bytes(), before)
        self.assertEqual((self.root / "approved_admits.json").read_bytes(), before_marker)
        out2, ev2 = self._run(records)
        self.assertIsNone(out2)
        self.assertEqual(ev2.call_count, 2)

    def test_missing_tape_does_not_seat(self):
        from hedge_fund.library.daily import empty_daily
        from hedge_fund.trading.approved_admit import run_approved_admits

        before = (self.root / "champions.json").read_bytes()
        before_marker = (self.root / "approved_admits.json").read_bytes()
        with _env(self.root), patch(
            "hedge_fund.library.tape.load_library_daily",
            return_value=empty_daily(),
        ), patch("hedge_fund.library.evaluate.evaluate_library_record") as ev:
            self.assertIsNone(run_approved_admits())
        ev.assert_not_called()
        self.assertEqual((self.root / "champions.json").read_bytes(), before)
        self.assertEqual((self.root / "approved_admits.json").read_bytes(), before_marker)


class NextOpenTests(unittest.TestCase):
    def test_already_on_signal_fills_at_the_next_utc_open(self):
        """The same first-entry rule as lib.sma100, for both new champions."""
        ts0 = 1_704_067_200_000
        n = 4
        px = np.full(n, 100.0)
        bars = DailyBars(
            ts=ts0 + np.arange(n, dtype=np.int64) * 86_400_000,
            open=px.copy(),
            high=px * 1.01,
            low=px * 0.99,
            close=px.copy(),
        )
        for name in (RSI, SMA200):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    base, params = parse_variant(name)

                    def signal(got, _params, _name=name):
                        del _name
                        return np.ones(len(got), dtype=bool)

                    strategy = replace(base, signal=signal, stop_frac=0.0)
                    with patch.dict(os.environ, {"PAPER_STATE": str(root)}), patch(
                        "hedge_fund.library.live.parse_variant",
                        return_value=(strategy, params),
                    ):
                        intraday = run_library_account(
                            name, root, bars=bars, price=111.0, now="2026-10-29T15:30:00+00:00",
                        )
                        self.assertFalse(intraday["long"])
                        self.assertEqual(intraday["actions"], [])
                        store = TradeStore(account_path(root, name))
                        try:
                            saved = store.load_account_state()
                        finally:
                            store.close()
                        self.assertTrue(saved["library"]["defer_entry"])
                        opened = run_library_account(
                            name, root, bars=bars, price=105.0, now="2026-10-30T00:02:00+00:00",
                        )
                    self.assertTrue(opened["long"])
                    self.assertEqual(opened["actions"][0]["action"], "ENTER")
                    _qty, fill, _fee = buy_fill(
                        PAPER_START_CASH, 105.0, Costs(taker_fee=TAKER_FEE, slippage=SLIPPAGE),
                    )
                    store = TradeStore(account_path(root, name))
                    try:
                        saved = store.load_account_state()
                    finally:
                        store.close()
                    broker = PaperBroker(cash=PAPER_START_CASH, taker_fee=TAKER_FEE, slippage=SLIPPAGE)
                    broker.restore_state(saved["broker"])
                    self.assertAlmostEqual(broker.lots[0].entry_price, fill, places=6)
                    self.assertNotIn("defer_entry", saved["library"])


class FixturePassTests(unittest.TestCase):
    def test_three_names_pass_on_the_warmed_tape(self):
        self.assertTrue(FIXTURE.is_file())
        bars, _meta = parse_daily_npz(FIXTURE.read_bytes())
        assert bars is not None
        self.assertEqual(int(bars.ts[0]), LIBRARY_TAPE_START_MS)
        data = build_library_data(bars)
        original = set(claim_names()[:15])
        self.assertEqual(original, {
            "lib.sma200", "lib.sma5x150", "lib.sma100", "lib.golden_cross", "lib.ema20x50",
            "lib.faber10m", "lib.tsmom12m", "lib.tsmom6m", "lib.tsmom3m", "lib.tsmom1w",
            "lib.detzel2w", "lib.bll_trb50", "lib.turtle55", "lib.rsi2", "lib.macd",
        })
        passed = []
        for name in claim_names():
            record = evaluate_library_record(name, data, tested_at="2026-10-29T11:01:00+01:00")
            self.assertEqual(record["gate_rules"], GATE_RULES)
            if record["qualified"]:
                passed.append(name)
                self.assertNotIn("neighbours", record["library"]["failing_checks"])
        # The original three still pass. A batch-2 name may also pass;
        # that does not change the original fifteen.
        self.assertEqual({name for name in passed if name in original}, PASSES)


if __name__ == "__main__":
    unittest.main()
