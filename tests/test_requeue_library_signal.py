"""Corrected lib.td_cd, lib.pierce, and lib.soldiers are claimed again.

The old discovery-log row stays. One new row stamps signal_revision and
puts the name back under fail-once. A corrected pass is seated on ingest.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from hedge_fund.library.catalog import library_trial_count
from hedge_fund.library.engine import ENGINE_ID, RISK_POLICY, TIMEFRAME
from hedge_fund.trading.constants import GATE_RULES
from hedge_fund.trading.requeue_library_signal import (
    BATCH_ID,
    CORRECTED_NAMES,
    RETEST_REASON,
    REVISION,
)


def _old(name: str) -> dict:
    trials = library_trial_count()
    return {
        "strategy": name,
        "tested_at": "2026-11-01T12:00:00+00:00",
        "qualified": False,
        "sharpe": 0.0,
        "daily_sharpe": 0.0,
        "trades": 0,
        "test_pnl": 0.0,
        "fail_reasons": ["oos_trades 0 < 8"],
        "timeframe": TIMEFRAME,
        "risk_policy": RISK_POLICY,
        "engine": ENGINE_ID,
        "gate_rules": GATE_RULES,
        "gate_v4": {"passed": False, "rules": GATE_RULES, "n_trials": trials},
        "symbols": ["BTC/USDT"],
        "library": {"key": name.split(".", 1)[1], "title": name, "params": {}},
    }


def _fresh(name: str) -> dict:
    row = _old(name)
    row["tested_at"] = "2026-11-01T18:00:00+00:00"
    row["trades"] = 9
    row["fail_reasons"] = ["oos_pnl -1 <= 0"]
    return row


def _pass(name: str) -> dict:
    from hedge_fund.library.evaluate import library_pass_attestation

    row = _fresh(name)
    row["qualified"] = True
    row["fail_reasons"] = []
    row["warnings"] = []
    row["gate_v4"] = library_pass_attestation(n_trials=library_trial_count())
    return row


class SignalCorrectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = patch.dict(os.environ, {
            "PAPER_STATE": str(self.root),
            "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
            "PAPER_DISCOVERY_SOURCE": "random",
        })
        self._env.start()
        os.environ.pop("PAPER_DISCOVERY_SOURCE", None)
        self._sched = patch("hedge_fund.trading.leases.schedule_refill_buffer")
        self._sched.start()

    def tearDown(self):
        self._sched.stop()
        self._env.stop()
        self._tmp.cleanup()

    def test_old_rows_are_released_and_the_stored_fail_stays(self):
        from hedge_fund.trading.champions import load_pool
        from hedge_fund.trading.discovery import load_discovery_log, save_discovery_log
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch

        save_discovery_log([_old(name) for name in CORRECTED_NAMES])
        now = datetime(2026, 11, 1, 21, 0, tzinfo=timezone.utc)
        claimed = claim_discovery_batch(
            "zealous-lion", 3, parallel=2, now=now, gate_rules=GATE_RULES, require_gate_rules=True,
        )
        self.assertEqual(list(claimed["names"]), list(CORRECTED_NAMES))
        self.assertEqual(claimed["retest"], list(CORRECTED_NAMES))
        self.assertEqual(claimed["source"], "library")
        self.assertEqual(claimed["admitted"] if "admitted" in claimed else [], [])

        ingested = ingest_discovery_payload({"evaluations": [_fresh("lib.td_cd")]})
        self.assertEqual(ingested["ingested"], ["lib.td_cd"])
        self.assertEqual(ingested["admitted"], [])
        self.assertEqual(ingested["proposed"], [])
        logged = load_discovery_log()
        td = [row for row in logged if row.get("strategy") == "lib.td_cd"]
        self.assertEqual(len(td), 2)
        self.assertEqual(td[0]["signal_revision"], REVISION)
        self.assertTrue(td[0]["retest"])
        self.assertEqual(td[0]["retest_reason"], RETEST_REASON)
        self.assertEqual(td[0]["retest_batch"], BATCH_ID)
        self.assertEqual(td[1]["trades"], 0)
        self.assertNotIn("signal_revision", td[1])
        self.assertEqual(load_pool().get("champions") or [], [])

        later = datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc)
        again = claim_discovery_batch(
            "omarchy-1", 3, parallel=2, now=later, gate_rules=GATE_RULES, require_gate_rules=True,
        )
        self.assertNotIn("lib.td_cd", again["names"])
        self.assertEqual(again["retest"], ["lib.pierce", "lib.soldiers"])

        second = ingest_discovery_payload({"evaluations": [_fresh("lib.td_cd")]})
        self.assertEqual(second["ingested"], [])
        self.assertEqual(second["skipped"][0]["reason"], "already_tested")
        self.assertEqual(
            sum(1 for row in load_discovery_log() if row.get("strategy") == "lib.td_cd"),
            2,
        )

    def test_corrected_pass_is_auto_seated(self):
        from hedge_fund.trading.champions import load_pool
        from hedge_fund.trading.discovery import load_discovery_log, save_discovery_log
        from hedge_fund.trading.ingest import ingest_discovery_payload

        save_discovery_log([_old("lib.td_cd")])
        body = ingest_discovery_payload({"evaluations": [_pass("lib.td_cd")]})
        self.assertEqual(body["ingested"], ["lib.td_cd"])
        self.assertEqual(body["auto_admitted"], ["lib.td_cd"])
        self.assertEqual(body["admitted"], ["lib.td_cd"])
        self.assertEqual(body["admission"], "auto")
        champs = load_pool().get("champions") or []
        self.assertEqual([row.get("name") for row in champs], ["lib.td_cd"])
        self.assertTrue(champs[0]["qualified"])
        self.assertEqual(champs[0]["engine"], ENGINE_ID)
        logged = [row for row in load_discovery_log() if row.get("strategy") == "lib.td_cd"]
        self.assertEqual(len(logged), 2)
        self.assertEqual(logged[0]["signal_revision"], REVISION)
        self.assertTrue(logged[0]["qualified"])
        self.assertEqual(logged[1]["trades"], 0)
        self.assertNotIn("signal_revision", logged[1])

    def test_random_mode_does_not_lease_the_correction(self):
        from hedge_fund.trading.discovery import save_discovery_log
        from hedge_fund.trading.leases import claim_discovery_batch

        save_discovery_log([_old("lib.td_cd")])
        with patch.dict(os.environ, {"PAPER_DISCOVERY_SOURCE": "random"}):
            with patch("hedge_fund.trading.leases.discovery_universe", return_value=["don_hi_12"]):
                with patch("hedge_fund.trading.leases._mint_unlocked", return_value=([], {"source": "test"})):
                    claimed = claim_discovery_batch("zealous-lion", 2, parallel=2)
        self.assertNotIn("lib.td_cd", claimed["names"])
        self.assertNotIn("lib.td_cd", claimed.get("retest") or [])

    def test_script_reports_the_pending_names(self):
        from hedge_fund.trading.discovery import save_discovery_log
        from scripts.requeue_library_signal import main

        save_discovery_log([_old("lib.pierce")])
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = main([])
        report = json.loads(buf.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["applied"])
        self.assertEqual(report["names"], ["lib.pierce"])
        self.assertEqual(report["rows_deleted"], 0)
        self.assertEqual(report["signal_revision"], REVISION)
