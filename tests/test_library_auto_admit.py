"""Automatic paper seats for daily-library gate v7b passes.

Alexander, 2026-11-01 17:05 CET: a canonical lib.* pass is a paper
forward-test champion. The ten passes that were still unseated are one
qualified batch, each at the center params stored for that name. A later
pass is seated on ingest, once. A fail is not. A retired name stays
retired until a new evaluation passes.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from hedge_fund.library.catalog import library_trial_count, strategy_by_key
from hedge_fund.library.daily import DailyBars
from hedge_fund.library.engine import ENGINE_ID, RISK_POLICY, TIMEFRAME
from hedge_fund.library.evaluate import library_pass_attestation
from hedge_fund.trading.constants import GATE_RULES
from hedge_fund.trading.gate_v4 import v4_pass_attestation

from tests.test_gate_v7b_admit import A, B, _env, _record

BATCH = "lib-ten-passes-v7b-20261101"
APPROVED_AT = "2026-11-01T17:05+01:00"
NEW = [
    "lib.kst",
    "lib.schaff",
    "lib.rsi2_cum",
    "lib.stochrsi",
    "lib.sma200_band",
    "lib.rmi",
    "lib.awesome",
    "lib.vidya",
    "lib.chop",
    "lib.three_dn",
]
# Center of each canonical name. A passing evaluation of lib.<key> stores this.
CENTERS = {
    "lib.kst": {"sig": 9},
    "lib.schaff": {"cycle": 10, "buy": 25},
    "lib.rsi2_cum": {"sum_lt": 35, "trend": 200, "exit_ma": 5},
    "lib.stochrsi": {"n": 14, "thr": 50},
    "lib.sma200_band": {"n": 200, "band": 1},
    "lib.rmi": {"mom": 5, "n": 14, "thr": 50},
    "lib.awesome": {"fast": 5, "slow": 34},
    "lib.vidya": {"n": 20},
    "lib.chop": {"n": 14, "trend": 200},
    "lib.three_dn": {"n": 3, "trend": 200, "exit_ma": 5},
}
EARLIER = [
    "rules-v3-admit-20261009",
    "lib-sma100-forward-test-20261029",
    "lib-rsi2-sma200-v7-20261029",
    "lib-batch2-four-v7b-20261030",
]


def _bars() -> DailyBars:
    return DailyBars(
        ts=np.array([0], dtype=np.int64),
        open=np.array([1.0]),
        high=np.array([1.0]),
        low=np.array([1.0]),
        close=np.array([1.0]),
    )


def _pass(name: str, *, params: dict | None = None) -> dict:
    row = _record(name, warnings=[f"dsr low {name}"])
    row["tested_at"] = "2026-11-01T16:00:00+00:00"
    row["timeframe"] = TIMEFRAME
    row["risk_policy"] = RISK_POLICY
    row["engine"] = ENGINE_ID
    row["gate_rules"] = GATE_RULES
    row["gate_v4"] = library_pass_attestation(n_trials=library_trial_count())
    row["library"] = {
        "key": name.split(".", 1)[1],
        "title": name,
        "params": dict(params if params is not None else CENTERS[name]),
        "citation": "test",
        "safety_stop_pct": 20.0,
    }
    return row


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "champions.json").write_text(json.dumps({
            "champions": [
                {"name": A, "source": "5m_qualification_filter", "risk_policy": "rm_v1", "closed": 1, "pnl": 1.0, "wins": 1},
                {"name": B, "source": "5m_qualification_filter", "risk_policy": "rm_v1", "closed": 0, "pnl": 0.0, "wins": 0},
            ],
            "synced_until": "2026-11-01T00:00:00+00:00",
        }))
        (self.root / "graduated.json").write_text("[]")
        (self.root / "retired.json").write_text(json.dumps({"retired": {}, "applied": [], "batches": []}))
        (self.root / "discovery_log.json").write_text("[]")
        (self.root / "approved_admits.json").write_text(json.dumps({
            "applied": list(EARLIER),
            "batches": [],
            "paper_only": True,
        }))

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, records: dict[str, dict]):
        from hedge_fund.trading.approved_admit import run_approved_admits

        def evaluate(name, _data, tested_at=None):
            row = dict(records[name])
            if tested_at:
                row["tested_at"] = tested_at
            return row

        with _env(self.root), patch("hedge_fund.library.tape.load_library_daily", return_value=_bars()), patch(
            "hedge_fund.library.evaluate.build_library_data", return_value=object()
        ), patch("hedge_fund.library.evaluate.evaluate_library_record", side_effect=evaluate) as ev:
            out = run_approved_admits()
        return out, ev

    def test_spec_is_exactly_those_ten_centers(self):
        from hedge_fund.trading.approved_admit import (
            LIBRARY_APPROVED_ADMITS_3,
            LIBRARY_APPROVED_BATCH_3,
            LIBRARY_APPROVED_BATCHES,
        )

        self.assertEqual(LIBRARY_APPROVED_BATCH_3, BATCH)
        self.assertEqual([row["name"] for row in LIBRARY_APPROVED_ADMITS_3], NEW)
        self.assertEqual(len(LIBRARY_APPROVED_ADMITS_3), 10)
        self.assertEqual([b for b, _ in LIBRARY_APPROVED_BATCHES][-1], BATCH)
        for row in LIBRARY_APPROVED_ADMITS_3:
            self.assertEqual(row["approved_by"], "Alexander")
            self.assertEqual(row["approved_at"], APPROVED_AT)
        for name, params in CENTERS.items():
            self.assertEqual(strategy_by_key(name.split(".", 1)[1]).center, params)

    def test_batch_seats_exactly_those_ten_once(self):
        before = json.loads((self.root / "champions.json").read_text())
        before_log = (self.root / "discovery_log.json").read_bytes()
        records = {name: _pass(name) for name in NEW}
        # A drifted block must not become the live params. The center is
        # what the passing canonical evaluation stores.
        records["lib.awesome"] = _pass("lib.awesome", params={"fast": 3, "slow": 34})
        first, ev1 = self._run(records)
        second, ev2 = self._run(records)
        self.assertEqual(first["batch_id"], BATCH)
        self.assertEqual(first["admitted"], NEW)
        self.assertIsNone(second)
        self.assertEqual(ev1.call_count, 10)
        self.assertEqual(ev2.call_count, 0)
        pool = json.loads((self.root / "champions.json").read_text())
        names = [c["name"] for c in pool["champions"]]
        self.assertEqual(names, [A, B, *NEW])
        self.assertEqual(pool["champions"][:2], before["champions"])
        self.assertEqual(pool["champions"][0]["risk_policy"], "rm_v1")
        self.assertEqual((self.root / "discovery_log.json").read_bytes(), before_log)
        for row in pool["champions"][2:]:
            self.assertIs(row["qualified"], True)
            self.assertEqual(row["source"], "library_qualification")
            self.assertEqual(row["engine"], ENGINE_ID)
            self.assertEqual(row["timeframe"], TIMEFRAME)
            self.assertEqual(row["risk_policy"], RISK_POLICY)
            self.assertEqual(row["library"]["params"], CENTERS[row["name"]])
            self.assertEqual(row["provenance"]["admit_batch"], BATCH)
            self.assertEqual(row["provenance"]["approved_by"], "Alexander")
            self.assertEqual(row["provenance"]["approved_at"], APPROVED_AT)
            self.assertEqual(row["provenance"]["gate_rules"], GATE_RULES)
            self.assertNotIn("admission", row)
        marker = json.loads((self.root / "approved_admits.json").read_text())
        self.assertEqual(marker["applied"].count(BATCH), 1)
        self.assertEqual(marker["applied"][:4], EARLIER)

    def test_one_unqualified_name_is_skipped_and_the_rest_seat(self):
        before = json.loads((self.root / "champions.json").read_text())
        records = {name: _pass(name) for name in NEW}
        records["lib.three_dn"] = _record("lib.three_dn", qualified=False)
        with self.assertLogs("hedge_fund.trading.approved_admit", level="INFO") as logs:
            out, ev = self._run(records)
        seated = [name for name in NEW if name != "lib.three_dn"]
        self.assertEqual(out["batch_id"], BATCH)
        self.assertEqual(out["admitted"], seated)
        self.assertEqual(out["skipped"], [{"strategy": "lib.three_dn", "reason": "not_qualified"}])
        self.assertEqual(ev.call_count, 10)
        self.assertTrue(any("skipped lib.three_dn" in line and GATE_RULES in line for line in logs.output))
        pool = json.loads((self.root / "champions.json").read_text())
        self.assertEqual([c["name"] for c in pool["champions"]], [A, B, *seated])
        self.assertEqual(pool["champions"][:2], before["champions"])
        marker = json.loads((self.root / "approved_admits.json").read_text())
        self.assertEqual(marker["applied"].count(BATCH), 1)
        again, ev2 = self._run(records)
        self.assertIsNone(again)
        self.assertEqual(ev2.call_count, 0)


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _ingest(self, evaluations):
        from hedge_fund.trading.ingest import ingest_discovery_payload

        with _env(self.root):
            return ingest_discovery_payload({"evaluations": evaluations})

    def _pool(self) -> list[dict]:
        path = self.root / "champions.json"
        if not path.exists():
            return []
        return json.loads(path.read_text()).get("champions") or []

    def test_pass_seats_once_fail_is_ignored_and_repeat_is_idempotent(self):
        fail = _pass("lib.rmi")
        fail["qualified"] = False
        fail["fail_reasons"] = ["oos_pnl -1.00 <= 0"]
        fail["gate_v4"] = {
            "passed": False,
            "rules": GATE_RULES,
            "n_trials": library_trial_count(),
        }
        passed = _pass("lib.vidya")
        with self.assertLogs("hedge_fund.trading.approved_admit", level="INFO") as logs:
            first = self._ingest([fail, passed, dict(passed)])
        second = self._ingest([passed])
        self.assertEqual(first["ingested"], ["lib.rmi", "lib.vidya"])
        self.assertEqual(first["proposed"], ["lib.vidya"])
        self.assertEqual(first["admitted"], ["lib.vidya"])
        self.assertEqual(first["auto_admitted"], ["lib.vidya"])
        self.assertEqual(first["admission"], "auto")
        self.assertEqual(second["ingested"], [])
        self.assertEqual(second["admitted"], [])
        self.assertEqual(second["auto_admitted"], [])
        self.assertEqual(second["admission"], "manual")
        champs = self._pool()
        self.assertEqual([c["name"] for c in champs], ["lib.vidya"])
        row = champs[0]
        self.assertEqual(row["library"]["params"], CENTERS["lib.vidya"])
        self.assertEqual(row["risk_policy"], RISK_POLICY)
        self.assertEqual(row["engine"], ENGINE_ID)
        self.assertEqual(row["source"], "library_qualification")
        self.assertIs(row["qualified"], True)
        self.assertEqual(row["provenance"]["admit"], "library_auto_admit")
        self.assertEqual(row["provenance"]["gate_rules"], GATE_RULES)
        self.assertEqual(row["provenance"]["admitted_at"], row["auto_admitted_at"])
        self.assertIn("T", row["auto_admitted_at"])
        self.assertTrue(any("lib.vidya" in line and GATE_RULES in line for line in logs.output))
        self.assertEqual(sum("seated lib.vidya" in line for line in logs.output), 1)

    def test_drifted_params_are_replaced_with_the_center(self):
        body = self._ingest([_pass("lib.awesome", params={"fast": 3, "slow": 34})])
        self.assertEqual(body["auto_admitted"], ["lib.awesome"])
        self.assertEqual(self._pool()[0]["library"]["params"], CENTERS["lib.awesome"])

    def test_five_minute_pass_stays_proposed(self):
        row = {
            "strategy": "fresh_pass",
            "tested_at": "2026-11-01T16:00:00+00:00",
            "qualified": True,
            "sharpe": 0.4,
            "daily_sharpe": 0.5,
            "trades": 40,
            "test_pnl": 12.0,
            "fail_reasons": [],
            "timeframe": "5m",
            "risk_policy": "rm_v1",
            "gate_rules": GATE_RULES,
            "gate_v4": v4_pass_attestation(),
        }
        body = self._ingest([row])
        self.assertEqual(body["proposed"], ["fresh_pass"])
        self.assertEqual(body["admitted"], [])
        self.assertEqual(body["auto_admitted"], [])
        self.assertEqual(body["admission"], "manual")
        self.assertEqual(self._pool(), [])

    def test_retired_name_stays_retired_until_a_new_pass(self):
        from hedge_fund.trading.requeue_library_gate import BATCH_ID

        name = "lib.kst"
        first = self._ingest([_pass(name)])
        self.assertEqual(first["auto_admitted"], [name])
        (self.root / "champions.json").write_text(json.dumps({"champions": [], "synced_until": ""}))
        (self.root / "retired.json").write_text(json.dumps({
            "retired": {name: {"name": name, "role": "champion", "reason": "owner"}},
            "applied": [],
            "batches": [],
        }))
        replay = self._ingest([_pass(name)])
        self.assertEqual(replay["admitted"], [])
        self.assertEqual(replay["auto_admitted"], [])
        self.assertEqual(self._pool(), [])
        self.assertIn(name, json.loads((self.root / "retired.json").read_text())["retired"])
        (self.root / "discovery_library_gate_requeue.json").write_text(json.dumps({
            "batch_id": BATCH_ID,
            "order": [name],
            "entries": {
                name: {
                    "status": "pending",
                    "requeued_at": "2026-11-01T17:00:00+01:00",
                    "prior_gate_rules": "gate_v6_library_warmup_20261029",
                    "prior_fail_reasons": ["old"],
                    "seated": False,
                    "completed_at": None,
                },
            },
        }))
        fresh = _pass(name)
        fresh["tested_at"] = "2026-11-01T18:00:00+00:00"
        # The stored center, not a neighbour the retest might scribble.
        fresh["library"]["params"] = {"sig": 12}
        again = self._ingest([fresh])
        self.assertEqual(again["auto_admitted"], [name])
        self.assertEqual([c["name"] for c in self._pool()], [name])
        self.assertEqual(self._pool()[0]["library"]["params"], CENTERS[name])
        self.assertEqual(self._pool()[0]["provenance"]["gate_rules"], GATE_RULES)
        retired = json.loads((self.root / "retired.json").read_text())["retired"]
        self.assertNotIn(name, retired)
        third = self._ingest([fresh])
        self.assertEqual(third["auto_admitted"], [])
        self.assertEqual([c["name"] for c in self._pool()], [name])


class BackfillTests(unittest.TestCase):
    """Passes ingested before this code deploys are seated on startup."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "champions.json").write_text(json.dumps({
            "champions": [{"name": A, "source": "5m_qualification_filter", "risk_policy": "rm_v1"}],
            "synced_until": "",
        }))
        (self.root / "graduated.json").write_text("[]")
        (self.root / "retired.json").write_text(json.dumps({"retired": {}, "applied": [], "batches": []}))
        (self.root / "approved_admits.json").write_text(json.dumps({
            "applied": [*EARLIER, BATCH],
            "batches": [],
            "paper_only": True,
        }))

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        from hedge_fund.trading.approved_admit import run_approved_admits

        with _env(self.root):
            return run_approved_admits()

    def _names(self) -> list[str]:
        return [c["name"] for c in json.loads((self.root / "champions.json").read_text())["champions"]]

    def test_stored_pass_seats_once_and_a_fail_does_not(self):
        from hedge_fund.library.catalog import strategy_by_key

        passed = _pass("lib.demarker", params=dict(strategy_by_key("demarker").center))
        passed["tested_at"] = "2026-11-01T15:00:00+00:00"
        failed = _pass("lib.expiry", params={"n": 1})
        failed["qualified"] = False
        failed["gate_v4"] = {"passed": False, "rules": GATE_RULES}
        failed["tested_at"] = "2026-11-01T15:00:00+00:00"
        # Newest row wins. An older pass under a later fail stays unseated.
        older_pass = _pass("lib.expiry", params={"n": 1})
        older_pass["tested_at"] = "2026-11-01T14:00:00+00:00"
        (self.root / "discovery_log.json").write_text(json.dumps([passed, failed, older_pass]))
        with self.assertLogs("hedge_fund.trading.approved_admit", level="INFO") as logs:
            first = self._run()
            second = self._run()
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(self._names(), [A, "lib.demarker"])
        row = json.loads((self.root / "champions.json").read_text())["champions"][1]
        self.assertEqual(row["library"]["params"], strategy_by_key("demarker").center)
        self.assertEqual(row["provenance"]["gate_rules"], GATE_RULES)
        self.assertEqual(row["provenance"]["admit"], "library_auto_admit")
        self.assertIn("T", row["auto_admitted_at"])
        self.assertEqual(row["risk_policy"], RISK_POLICY)
        self.assertTrue(any("seated lib.demarker" in line and GATE_RULES in line and "backfill" in line for line in logs.output))
        self.assertEqual(sum("seated lib.demarker" in line for line in logs.output), 1)

    def test_library_results_pass_is_seated_when_the_log_has_no_row(self):
        from hedge_fund.library.catalog import strategy_by_key

        row = _pass("lib.gk", params=dict(strategy_by_key("gk").center))
        (self.root / "library_results.json").write_text(json.dumps({
            "paper_only": True,
            "results": {"lib.gk": row},
        }))
        self._run()
        self._run()
        self.assertEqual(self._names(), [A, "lib.gk"])
        seated = json.loads((self.root / "champions.json").read_text())["champions"][1]
        self.assertEqual(seated["library"]["params"], strategy_by_key("gk").center)

    def test_graduated_and_already_seated_are_not_duplicated(self):
        seated = _pass("lib.kst")
        (self.root / "champions.json").write_text(json.dumps({
            "champions": [
                {"name": A, "source": "5m_qualification_filter"},
                {"name": "lib.kst", "source": "library_qualification", "qualified": True},
            ],
            "synced_until": "",
        }))
        (self.root / "graduated.json").write_text(json.dumps([
            {"name": "lib.vidya", "source": "library_qualification"},
        ]))
        (self.root / "discovery_log.json").write_text(json.dumps([seated, _pass("lib.vidya")]))
        before = (self.root / "champions.json").read_bytes()
        self._run()
        self.assertEqual((self.root / "champions.json").read_bytes(), before)

    def test_retired_after_the_pass_stays_retired(self):
        row = _pass("lib.chop")
        row["tested_at"] = "2026-11-01T12:00:00+00:00"
        (self.root / "discovery_log.json").write_text(json.dumps([row]))
        (self.root / "retired.json").write_text(json.dumps({
            "retired": {
                "lib.chop": {
                    "name": "lib.chop",
                    "role": "champion",
                    "retired_at": "2026-11-01T18:00:00+00:00",
                },
            },
            "applied": [],
            "batches": [],
        }))
        self._run()
        self.assertEqual(self._names(), [A])
        self.assertIn("lib.chop", json.loads((self.root / "retired.json").read_text())["retired"])

    def test_pass_after_retirement_is_seated(self):
        row = _pass("lib.awesome", params={"fast": 3, "slow": 34})
        row["tested_at"] = "2026-11-01T18:00:00+00:00"
        (self.root / "discovery_log.json").write_text(json.dumps([row]))
        (self.root / "retired.json").write_text(json.dumps({
            "retired": {
                "lib.awesome": {
                    "name": "lib.awesome",
                    "role": "champion",
                    "retired_at": "2026-11-01T12:00:00+00:00",
                },
            },
            "applied": [],
            "batches": [],
        }))
        self._run()
        self.assertEqual(self._names(), [A, "lib.awesome"])
        seated = json.loads((self.root / "champions.json").read_text())["champions"][1]
        self.assertEqual(seated["library"]["params"], CENTERS["lib.awesome"])
        self.assertEqual(seated["provenance"]["gate_rules"], GATE_RULES)
        self.assertNotIn("lib.awesome", json.loads((self.root / "retired.json").read_text())["retired"])


class AttestationTests(unittest.TestCase):
    def test_pass_helper_uses_the_current_gate(self):
        row = _pass("lib.chop")
        self.assertEqual(row["gate_rules"], GATE_RULES)
        self.assertEqual(row["gate_v4"]["rules"], GATE_RULES)
        self.assertTrue(row["gate_v4"]["passed"])
        self.assertEqual(set(library_pass_attestation()), {"passed", "rules", "oos_pnl_pass", "trades_pass", "holdout_pass", "cost_pass"})


if __name__ == "__main__":
    unittest.main()
