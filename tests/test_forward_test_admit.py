"""Unproven paper forward-test admit of lib.sma100.

Alexander, 2026-10-29 09:30 CET: seat lib.sma100 even though gate v6 fails,
and only on DSR (0.848 < 0.95). The champion row comes from
evaluate_library_record on the library tape. qualified stays false in the
pool and in the discovery log. The two champions already on the book stay.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

import numpy as np

from hedge_fund.library.daily import DailyBars
from hedge_fund.library.engine import ENGINE_ID, RISK_POLICY, TIMEFRAME
from hedge_fund.trading.constants import GATE_RULES

NAME = "lib.sma100"
BATCH = "lib-sma100-forward-test-20261029"
ADMISSION = "forward_test_unproven"
REASON = "dsr 0.848 < 0.95 (n_trials=64)"
A = "dip_204b_lt8pc&h4_ema_abv_150"
B = "h1_ema_abv_50&mom_18b_gt2pc&sma_abv_30&rsi_14_>45"
CHAMPS = [
    {"name": A, "source": "5m_qualification_filter", "champion_since": "2026-10-09T03:43:40+00:00", "closed": 4, "pnl": 12.5, "wins": 2},
    {"name": B, "source": "5m_qualification_filter", "champion_since": "2026-10-11T09:00:00+00:00", "closed": 1, "pnl": -3.0, "wins": 0},
]


def _bars() -> DailyBars:
    return DailyBars(
        ts=np.array([0], dtype=np.int64),
        open=np.array([1.0]),
        high=np.array([1.0]),
        low=np.array([1.0]),
        close=np.array([1.0]),
    )


def _stub(*, qualified: bool = True) -> dict:
    """What evaluate_library_record returns. ``qualified`` is the gate flag.

    The admit must ignore a true flag. The discovery log is a separate file.
    """
    return {
        "strategy": NAME,
        "tested_at": "2026-10-29T08:00:00+00:00",
        "qualified": qualified,
        "sharpe": 0.85,
        "daily_sharpe": 0.85,
        "win_rate_pct": 40.0,
        "test_pnl": 100.0,
        "trades": 12,
        "gate_rules": GATE_RULES,
        "backtest_max_dd": 0.42,
        "fail_reasons": [REASON],
        "timeframe": TIMEFRAME,
        "risk_policy": RISK_POLICY,
        "engine": ENGINE_ID,
        "gate_v4": {
            "passed": False,
            "rules": GATE_RULES,
            "dsr": 0.848,
            "pbo": 0.10,
            "n_trials": 64,
            "neighbours": 2,
            "neighbours_pass": 2,
            "backtest_max_dd": 0.42,
            "cost_sharpe": 0.5,
            "cost_daily_sharpe": 0.5,
        },
        "library": {
            "key": "sma100",
            "title": "Price vs 100-day SMA",
            "params": {"n": 100},
            "citation": "Practitioner, Price above its 100-day simple moving average",
            "safety_stop_pct": 20.0,
        },
    }


def _env(root: Path):
    env = dict(os.environ)
    env["PAPER_STATE"] = str(root)
    return patch.dict(os.environ, env, clear=True)


def _seed(root: Path, *, champions: list | None = None, log: list | None = None) -> None:
    (root / "champions.json").write_text(json.dumps({
        "champions": champions if champions is not None else CHAMPS,
        "synced_until": "2026-10-28T00:00:00+00:00",
    }))
    (root / "graduated.json").write_text("[]")
    (root / "retired.json").write_text(json.dumps({"retired": {}, "applied": [], "batches": []}))
    if log is not None:
        (root / "discovery_log.json").write_text(json.dumps(log))


def _patch_eval(record: dict):
    return (
        patch("hedge_fund.library.tape.load_library_daily", return_value=_bars()),
        patch("hedge_fund.library.evaluate.build_library_data", return_value=object()),
        patch("hedge_fund.library.evaluate.evaluate_library_record", return_value=record),
    )


class ForwardTestSpecTests(unittest.TestCase):
    def test_sma100_does_not_require_a_qualified_verdict(self):
        from hedge_fund.trading.approved_admit import (
            APPROVED_ADMITS,
            FORWARD_TEST_ADMISSION,
            FORWARD_TEST_ADMITS,
            FORWARD_TEST_BATCH,
        )

        self.assertEqual(FORWARD_TEST_BATCH, BATCH)
        self.assertEqual(FORWARD_TEST_ADMISSION, ADMISSION)
        self.assertEqual([row["name"] for row in FORWARD_TEST_ADMITS], [NAME])
        spec = FORWARD_TEST_ADMITS[0]
        self.assertEqual(spec["batch"], BATCH)
        self.assertEqual(spec["approved_by"], "Alexander")
        self.assertEqual(spec["approved_at"], "2026-10-29T09:30+01:00")
        self.assertEqual(
            spec["note"],
            "unproven paper forward-test; fails gate v6 only on DSR 0.848 < 0.95",
        )
        self.assertNotIn("requalify_batch", spec)
        self.assertEqual([row["name"] for row in APPROVED_ADMITS], [A])


class ForwardTestAdmitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = [
            {"strategy": NAME, "qualified": False, "fail_reasons": [REASON], "tested_at": "2026-10-28T00:00:00+00:00"},
            {"strategy": "lib.sma200", "qualified": False, "fail_reasons": ["dsr 0.590 < 0.95 (n_trials=64)"]},
        ]
        _seed(self.root, log=self.log)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        from hedge_fund.trading.approved_admit import run_approved_admits

        with _env(self.root):
            return run_approved_admits()

    def test_seats_once_from_the_library_evaluation_and_leaves_the_log(self):
        from hedge_fund.trading.approved_admit import run_approved_admits

        before_champs = json.loads((self.root / "champions.json").read_text())
        before_log = (self.root / "discovery_log.json").read_bytes()
        before_retired = (self.root / "retired.json").read_bytes()
        record = _stub(qualified=True)
        load_p, build_p, eval_p = _patch_eval(record)
        with _env(self.root), load_p, build_p, eval_p as ev:
            first = run_approved_admits()
            second = run_approved_admits()
        self.assertEqual(ev.call_count, 1)
        self.assertEqual(ev.call_args.args[0], NAME)
        self.assertEqual(first["admitted"], [NAME])
        self.assertIsNone(second)
        pool = json.loads((self.root / "champions.json").read_text())
        self.assertEqual([c["name"] for c in pool["champions"]], [A, B, NAME])
        self.assertEqual(pool["synced_until"], "2026-10-28T00:00:00+00:00")
        by_name = {c["name"]: c for c in pool["champions"]}
        self.assertEqual(by_name[A], before_champs["champions"][0])
        self.assertEqual(by_name[B], before_champs["champions"][1])
        self.assertEqual((self.root / "discovery_log.json").read_bytes(), before_log)
        self.assertEqual((self.root / "retired.json").read_bytes(), before_retired)
        logged = json.loads(before_log)
        self.assertIs(logged[0]["qualified"], False)
        row = by_name[NAME]
        self.assertEqual(row["admission"], ADMISSION)
        self.assertIs(row["qualified"], False)
        self.assertEqual(row["source"], ADMISSION)
        self.assertEqual(row["engine"], ENGINE_ID)
        self.assertEqual(row["timeframe"], TIMEFRAME)
        self.assertEqual(row["risk_policy"], RISK_POLICY)
        self.assertEqual(row["library"]["safety_stop_pct"], 20.0)
        self.assertEqual(row["library"]["key"], "sma100")
        self.assertEqual(row["library"]["params"], {"n": 100})
        self.assertIs(row["gate_v4"]["passed"], False)
        self.assertEqual(row["gate_v4"]["fail_reasons"], [REASON])
        self.assertEqual(row["gate_v4"]["dsr"], 0.848)
        self.assertEqual(row["forward_test"]["backtest_max_dd"], 0.42)
        self.assertEqual(row["provenance"]["approved_by"], "Alexander")
        self.assertEqual(row["provenance"]["approved_at"], "2026-10-29T09:30+01:00")
        self.assertEqual(row["provenance"]["note"], FORWARD_TEST_ADMITS_NOTE)
        marker = json.loads((self.root / "approved_admits.json").read_text())
        self.assertEqual(marker["applied"].count(BATCH), 1)
        self.assertTrue(marker["paper_only"])
        self.assertIs(record["qualified"], True)

    def test_missing_tape_does_not_seat_or_mark(self):
        from hedge_fund.library.daily import empty_daily
        from hedge_fund.trading.approved_admit import run_approved_admits

        before = (self.root / "champions.json").read_bytes()
        before_log = (self.root / "discovery_log.json").read_bytes()
        with _env(self.root), patch(
            "hedge_fund.library.tape.load_library_daily", return_value=empty_daily(),
        ), patch("hedge_fund.library.evaluate.evaluate_library_record") as ev:
            self.assertIsNone(run_approved_admits())
            self.assertIsNone(run_approved_admits())
        ev.assert_not_called()
        self.assertEqual((self.root / "champions.json").read_bytes(), before)
        self.assertEqual((self.root / "discovery_log.json").read_bytes(), before_log)
        self.assertFalse((self.root / "approved_admits.json").exists())

    def test_api_shows_the_unproven_label(self):
        from hedge_fund.trading.approved_admit import FORWARD_TEST_ADMITS, run_approved_admits

        load_p, build_p, eval_p = _patch_eval(_stub(qualified=True))
        with _env(self.root), load_p, build_p, eval_p:
            run_approved_admits()
            from hedge_fund.web.server import Handler

            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                port = httpd.server_address[1]
                data = json.loads(urlopen(f"http://127.0.0.1:{port}/api/champions", timeout=10).read())
            finally:
                httpd.shutdown()
                httpd.server_close()
        rows = {r["name"]: r for r in data["active_champions"]}
        self.assertEqual(set(rows), {A, B, NAME})
        self.assertEqual(rows[NAME]["admission"], ADMISSION)
        self.assertIs(rows[NAME]["qualified"], False)
        self.assertIs(rows[NAME]["gate_v4"]["passed"], False)
        self.assertEqual(rows[NAME]["gate_v4"]["fail_reasons"], [REASON])
        self.assertEqual(rows[A]["source"], "5m_qualification_filter")
        self.assertEqual(rows[B]["closed"], 1)
        ui = Path(__file__).resolve().parents[1] / "frontend/src/status/admission.tsx"
        self.assertIn("Unproven - forward test", ui.read_text())
        for rel in (
            "frontend/src/status/ChampionBook.tsx",
            "frontend/src/pages/Champions.tsx",
            "frontend/src/pages/ChampionDetail.tsx",
        ):
            self.assertIn("AdmissionBadge", (Path(__file__).resolve().parents[1] / rel).read_text())
        self.assertEqual(FORWARD_TEST_ADMITS[0]["name"], NAME)


FORWARD_TEST_ADMITS_NOTE = "unproven paper forward-test; fails gate v6 only on DSR 0.848 < 0.95"


if __name__ == "__main__":
    unittest.main()
