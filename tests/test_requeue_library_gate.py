"""Requeue canonical lib.* names whose newest log row is an older gate tag."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from hedge_fund.library.catalog import claim_names, library_trial_count
from hedge_fund.library.engine import ENGINE_ID, RISK_POLICY, TIMEFRAME
from hedge_fund.library.evaluate import library_pass_attestation
from hedge_fund.trading.constants import GATE_RULES, V6_WARMUP_GATE_RULES, V7_GATE_RULES
from hedge_fund.trading.requeue_library_gate import (
    BATCH_ID,
    RETEST_REASON,
    STATE_NAME,
    requeue_library_gate,
)

SMA100 = "lib.sma100"
SMA200 = "lib.sma200"
RSI = "lib.rsi2"
MACD = "lib.macd"
FIVE = "dip_204b_lt8pc&h4_ema_abv_150"
OTHER = "h1_ema_abv_50&mom_18b_gt2pc&sma_abv_30&rsi_14_>45"


def _old(name: str, rules: str | None = V6_WARMUP_GATE_RULES, *, fail: str = "dsr 0.500 < 0.95") -> dict:
    row = {
        "strategy": name,
        "qualified": False,
        "tested_at": "2026-10-29T08:00:00+00:00",
        "sharpe": 0.4,
        "daily_sharpe": 0.4,
        "trades": 20,
        "test_pnl": 10.0,
        "fail_reasons": [fail],
        "timeframe": TIMEFRAME,
        "risk_policy": RISK_POLICY,
        "engine": ENGINE_ID,
    }
    if rules is not None:
        row["gate_rules"] = rules
    return row


def _current(name: str) -> dict:
    row = _old(name, GATE_RULES)
    row["tested_at"] = "2026-10-29T11:30:00+00:00"
    row["qualified"] = True
    row["fail_reasons"] = []
    return row


def _fresh(name: str, *, qualified: bool = True) -> dict:
    trials = library_trial_count()
    gate = (
        library_pass_attestation(n_trials=trials)
        if qualified
        else {
            "passed": False,
            "rules": GATE_RULES,
            "n_trials": trials,
            "oos_pnl_pass": False,
            "trades_pass": True,
            "holdout_pass": False,
            "cost_pass": False,
        }
    )
    return {
        "strategy": name,
        "tested_at": "2026-10-29T12:00:00+00:00",
        "qualified": qualified,
        "sharpe": 0.59,
        "daily_sharpe": 0.59,
        "trades": 27,
        "test_pnl": 102241.97,
        "win_rate_pct": 40.0,
        "fail_reasons": [] if qualified else ["oos_pnl -1.00 <= 0"],
        "warnings": ["neighbours 0/3 = 0% < 70%", "dsr 0.590 < 0.95 (n_trials=64)"],
        "timeframe": TIMEFRAME,
        "risk_policy": RISK_POLICY,
        "engine": ENGINE_ID,
        "gate_rules": GATE_RULES,
        "gate_v4": gate,
        "symbols": ["BTC/USDT"],
        "library": {
            "key": name.split(".", 1)[1],
            "title": name,
            "params": {"n": 1},
            "citation": "test",
            "safety_stop_pct": 20.0,
        },
    }


def _five(name: str = FIVE) -> dict:
    return {
        "strategy": name,
        "qualified": False,
        "tested_at": "2026-10-20T00:00:00+00:00",
        "sharpe": 0.41,
        "daily_sharpe": 0.22,
        "trades": 48,
        "test_pnl": 12.0,
        "fail_reasons": ["oos_sharpe 0.10 < 0.30"],
        "gate_rules": V7_GATE_RULES,
        "timeframe": "5m",
        "risk_policy": "rm_v1",
    }


class SelectTests(unittest.TestCase):
    def _root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        sma100 = {
            "name": SMA100,
            "source": "forward_test_unproven",
            "admission": "forward_test_unproven",
            "qualified": False,
            "closed": 0,
            "pnl": 0.0,
            "wins": 0,
            "champion_since": "2026-10-29T08:30:00+00:00",
        }
        sma200 = {
            "name": SMA200,
            "source": "library_qualification",
            "qualified": True,
            "closed": 0,
            "pnl": 0.0,
            "wins": 0,
            "champion_since": "2026-10-29T10:05:00+00:00",
            "provenance": {"approved_by": "Alexander"},
        }
        (root / "champions.json").write_text(json.dumps({
            "champions": [
                {"name": FIVE, "source": "5m_qualification_filter", "closed": 4, "pnl": 12.5, "wins": 2},
                {"name": OTHER, "source": "5m_qualification_filter", "closed": 1, "pnl": -3.0, "wins": 0},
                sma100,
                sma200,
            ],
            "synced_until": "2026-10-28T00:00:00+00:00",
        }))
        (root / "retired.json").write_text(json.dumps({
            "retired": {MACD: {"name": MACD, "role": "retired"}},
            "applied": [],
            "batches": [],
        }))
        (root / "trades_lib.sma100.sqlite").write_bytes(b"sma100-account")
        (root / "trades_lib.sma200.sqlite").write_bytes(b"sma200-account")
        return tmp, root

    def test_older_canonical_names_only_and_apply_is_idempotent(self):
        from hedge_fund.library.catalog import claim_names as names
        from hedge_fund.trading.discovery import save_discovery_log

        tmp, root = self._root()
        try:
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                rows = [_old(name, V6_WARMUP_GATE_RULES) for name in names()]
                rows[names().index(RSI)] = _current(RSI)
                rows.append(_old(RSI, V6_WARMUP_GATE_RULES))
                rows.append(_old("lib.sma100.90", V7_GATE_RULES))
                rows.append(_five())
                missing = _old("lib.ema20x50")
                del missing["gate_rules"]
                rows[names().index("lib.ema20x50")] = missing
                save_discovery_log(rows)
                before = (root / "champions.json").read_bytes()
                retired = (root / "retired.json").read_bytes()
                account = (root / "trades_lib.sma100.sqlite").read_bytes()
                dry = requeue_library_gate(apply=False)
                self.assertFalse((root / STATE_NAME).exists())
                first = requeue_library_gate(apply=True, now="2026-10-29T11:05:00+00:00")
                second = requeue_library_gate(apply=True, now="2026-10-29T12:05:00+00:00")
                again = requeue_library_gate(apply=False)
                after = (root / "champions.json").read_bytes()
                retired_after = (root / "retired.json").read_bytes()
                account_after = (root / "trades_lib.sma100.sqlite").read_bytes()
        finally:
            tmp.cleanup()
        self.assertTrue(dry["dry_run"])
        self.assertFalse(dry["applied"])
        self.assertEqual(dry["admitted"], [])
        self.assertEqual(dry["retired"], [])
        self.assertTrue(dry["seats_unchanged"])
        self.assertEqual(dry["gate_to"], GATE_RULES)
        self.assertNotIn(RSI, dry["names"])
        self.assertNotIn("lib.sma100.90", dry["names"])
        self.assertNotIn(FIVE, dry["names"])
        self.assertIn(SMA100, dry["names"])
        self.assertIn(SMA200, dry["names"])
        self.assertIn(MACD, dry["names"])
        self.assertIn("lib.ema20x50", dry["names"])
        self.assertEqual(dry["count"], len(names()) - 1)
        self.assertEqual(dry["names"], [n for n in names() if n != RSI])
        self.assertEqual(dry["seated"], [SMA200, SMA100])
        self.assertEqual(dry["by_rules"][V6_WARMUP_GATE_RULES], len(names()) - 2)
        self.assertEqual(dry["by_rules"]["missing"], 1)
        self.assertNotIn(GATE_RULES, dry["by_rules"])
        self.assertTrue(first["applied"])
        self.assertEqual(first["applied_at"], "2026-10-29T11:05:00+00:00")
        self.assertEqual(first["names"], dry["names"])
        self.assertTrue(second["already_applied"])
        self.assertFalse(second["applied"])
        self.assertEqual(second["applied_at"], first["applied_at"])
        self.assertEqual(again["names"], first["names"])
        self.assertEqual(again["pending"], first["count"])
        self.assertEqual(before, after)
        self.assertEqual(retired, retired_after)
        self.assertEqual(account, account_after)
        self.assertEqual(account_after, b"sma100-account")

    def test_claim_and_ingest_keep_the_seat_and_propose_the_pass(self):
        from hedge_fund.trading.discovery import load_discovery_log, save_discovery_log
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.trading.trials import trial_census

        tmp, root = self._root()
        try:
            env = {
                "PAPER_STATE": str(root),
                "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
                "PAPER_DISCOVERY_SOURCE": "random",
            }
            with patch.dict(os.environ, env):
                os.environ.pop("PAPER_DISCOVERY_SOURCE", None)
                save_discovery_log([
                    _old(SMA100, V6_WARMUP_GATE_RULES),
                    _old(SMA200, V7_GATE_RULES, fail="neighbours 0/3 = 0% < 70%"),
                    _old(MACD, V6_WARMUP_GATE_RULES),
                    _current(RSI),
                    _five(),
                    _five("dip_other_tested"),
                ])
                before_champs = (root / "champions.json").read_bytes()
                before_retired = (root / "retired.json").read_bytes()
                before_accounts = (
                    (root / "trades_lib.sma100.sqlite").read_bytes(),
                    (root / "trades_lib.sma200.sqlite").read_bytes(),
                )
                blocked = claim_discovery_batch(
                    "zealous-lion",
                    4,
                    parallel=4,
                    now=datetime(2026, 10, 29, 11, 0, tzinfo=timezone.utc),
                    gate_rules=GATE_RULES,
                    require_gate_rules=True,
                )
                applied = requeue_library_gate(apply=True, now="2026-10-29T11:05:00+00:00")
                census_before = trial_census()["n_backtested"]
                with patch("hedge_fund.trading.leases.schedule_refill_buffer"):
                    claimed = claim_discovery_batch(
                        "zealous-lion",
                        4,
                        parallel=4,
                        now=datetime(2026, 10, 29, 11, 6, tzinfo=timezone.utc),
                        gate_rules=GATE_RULES,
                        require_gate_rules=True,
                    )
                fresh_sma = _fresh(SMA100, qualified=True)
                fresh_rsi_seat = _fresh(SMA200, qualified=True)
                posted_five = {**_five(), "gate_rules": GATE_RULES, "tested_at": "2026-10-29T12:00:00+00:00"}
                posted_other = {**_five("dip_other_tested"), "gate_rules": GATE_RULES, "tested_at": "2026-10-29T12:00:00+00:00"}
                out = ingest_discovery_payload({
                    "evaluations": [fresh_sma, fresh_rsi_seat, _fresh(MACD, qualified=False), posted_five, posted_other],
                })
                logged = load_discovery_log()
                again = ingest_discovery_payload({"evaluations": [_fresh(SMA100)]})
                logged_again = load_discovery_log()
                after_claim = claim_discovery_batch(
                    "zealous-lion",
                    4,
                    parallel=4,
                    now=datetime(2026, 10, 29, 11, 10, tzinfo=timezone.utc),
                    gate_rules=GATE_RULES,
                    require_gate_rules=True,
                )
                (root / STATE_NAME).unlink()
                wiped = requeue_library_gate(apply=False)
                census_after = trial_census()["n_backtested"]
                after_champs = (root / "champions.json").read_bytes()
                after_retired = (root / "retired.json").read_bytes()
                after_accounts = (
                    (root / "trades_lib.sma100.sqlite").read_bytes(),
                    (root / "trades_lib.sma200.sqlite").read_bytes(),
                )
                champs = json.loads(after_champs)
                five_rows = [row for row in logged if row.get("strategy") == FIVE]
                other_rows = [row for row in logged if row.get("strategy") == "dip_other_tested"]
        finally:
            tmp.cleanup()
        self.assertNotIn(SMA100, blocked["names"])
        self.assertNotIn(SMA200, blocked["names"])
        self.assertEqual(applied["names"], [SMA200, SMA100, MACD])
        self.assertEqual(applied["seated"], [SMA200, SMA100])
        self.assertEqual(claimed["retest"], [SMA200, SMA100, MACD])
        self.assertEqual(claimed["names"][:3], [SMA200, SMA100, MACD])
        self.assertNotIn(RSI, claimed["retest"])
        self.assertNotIn(FIVE, claimed["names"])
        self.assertEqual(out["admitted"], [])
        self.assertEqual(out["proposed"], [SMA100, SMA200])
        self.assertEqual(
            {row["strategy"]: row["reason"] for row in out["skipped"]},
            {FIVE: "already_pooled_or_graduated", "dip_other_tested": "already_tested"},
        )
        by_name: dict[str, list[dict]] = {}
        for row in logged:
            by_name.setdefault(row["strategy"], []).append(row)
        self.assertEqual(len(by_name[SMA100]), 2)
        newest = by_name[SMA100][0]
        self.assertEqual(newest["gate_rules"], GATE_RULES)
        self.assertTrue(newest["qualified"])
        self.assertTrue(newest["retest"])
        self.assertEqual(newest["retest_reason"], RETEST_REASON)
        self.assertEqual(newest["retest_batch"], BATCH_ID)
        self.assertEqual(newest["retest_from_rules"], V6_WARMUP_GATE_RULES)
        self.assertEqual(newest["prior_fail_reasons"], ["dsr 0.500 < 0.95"])
        self.assertTrue(newest["retest_keeps_seat"])
        self.assertIn("neighbours 0/3 = 0% < 70%", newest["warnings"])
        self.assertEqual(by_name[SMA100][1]["gate_rules"], V6_WARMUP_GATE_RULES)
        self.assertTrue(by_name[SMA200][0]["retest_keeps_seat"])
        self.assertFalse(by_name[MACD][0]["qualified"])
        self.assertFalse(by_name[MACD][0]["retest_keeps_seat"])
        self.assertEqual(len(five_rows), 1)
        self.assertEqual(five_rows[0]["gate_rules"], V7_GATE_RULES)
        self.assertEqual(five_rows[0]["fail_reasons"], ["oos_sharpe 0.10 < 0.30"])
        self.assertEqual(len(other_rows), 1)
        self.assertEqual(other_rows[0]["gate_rules"], V7_GATE_RULES)
        self.assertNotIn("retest", other_rows[0])
        self.assertEqual(again["ingested"], [])
        self.assertEqual(again["admitted"], [])
        self.assertEqual(again["skipped"][0]["reason"], "already_pooled_or_graduated")
        self.assertEqual(sum(1 for row in logged_again if row.get("strategy") == SMA100), 2)
        self.assertNotIn(SMA100, after_claim["names"])
        self.assertNotIn(SMA200, after_claim["names"])
        self.assertNotIn(MACD, after_claim["names"])
        self.assertNotIn(SMA100, wiped["names"])
        self.assertNotIn(SMA200, wiped["names"])
        self.assertNotIn(MACD, wiped["names"])
        self.assertEqual(census_after, census_before)
        self.assertEqual(after_champs, before_champs)
        self.assertEqual(after_retired, before_retired)
        self.assertEqual(after_accounts, before_accounts)
        seated = {row["name"]: row for row in champs["champions"]}
        self.assertEqual(list(seated), [FIVE, OTHER, SMA100, SMA200])
        self.assertEqual(seated[SMA100]["admission"], "forward_test_unproven")
        self.assertIs(seated[SMA100]["qualified"], False)
        self.assertIs(seated[SMA200]["qualified"], True)
        self.assertEqual(seated[SMA200]["provenance"]["approved_by"], "Alexander")
        self.assertEqual(seated[FIVE]["pnl"], 12.5)
        self.assertEqual(champs["synced_until"], "2026-10-28T00:00:00+00:00")

    def test_random_mode_does_not_lease_the_library_queue(self):
        from hedge_fund.trading.discovery import save_discovery_log
        from hedge_fund.trading.leases import claim_discovery_batch

        tmp, root = self._root()
        try:
            with patch.dict(os.environ, {
                "PAPER_STATE": str(root),
                "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
                "PAPER_DISCOVERY_SOURCE": "random",
            }):
                save_discovery_log([_old(SMA100), _five()])
                requeue_library_gate(apply=True, now="2026-10-29T11:05:00+00:00")
                with patch("hedge_fund.trading.leases.discovery_universe", return_value=["don_hi_12"]):
                    with patch("hedge_fund.trading.leases._mint_unlocked", return_value=([], {"source": "test"})):
                        with patch("hedge_fund.trading.leases.schedule_refill_buffer"):
                            claimed = claim_discovery_batch("zealous-lion", 2, parallel=2)
        finally:
            tmp.cleanup()
        self.assertNotIn(SMA100, claimed["names"])
        self.assertNotIn(SMA100, claimed["retest"])
        self.assertNotIn(SMA100, claimed.get("library") or [])

    def test_script_defaults_to_dry_run(self):
        from hedge_fund.trading.discovery import save_discovery_log
        from scripts.requeue_library_gate import main

        tmp, root = self._root()
        try:
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_discovery_log([_old(SMA100)])
                buf = __import__("io").StringIO()
                with patch("sys.stdout", buf):
                    code = main([])
                report = json.loads(buf.getvalue())
        finally:
            tmp.cleanup()
        self.assertEqual(code, 0)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["names"], [SMA100])
        self.assertFalse((root / STATE_NAME).exists())
