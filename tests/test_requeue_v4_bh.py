"""One-shot requeue of discovery-window buy-and-hold misses."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hedge_fund.trading.constants import (
    GATE_RULES,
    HOLDOUT_END_MS,
    HOLDOUT_START_MS,
    V3_GATE_RULES,
    V4_GATE_RULES,
)
from hedge_fund.trading.requeue_v4_bh import (
    BATCH_ID,
    LEGACY_BH_RULES,
    RETEST_REASON,
    is_requeue_candidate,
    requeue_v4_bh,
)


def _bh(name: str, *, reason: str | None = None) -> dict:
    return {
        "strategy": name,
        "qualified": False,
        "sharpe": 0.41,
        "trades": 48,
        "test_pnl": 120.0,
        "sma_stack_oos_pnl": 40.0,
        "daily_sharpe": 0.22,
        "bh_daily_sharpe": 0.415,
        "regimes_tested": 18,
        "symbols": ["BTC/USDT"],
        "gate_rules": V4_GATE_RULES,
        "tested_at": "2026-10-20T00:00:00+00:00",
        "fail_reasons": [reason or "daily_sharpe 0.22 <= bh_daily_sharpe 0.415"],
        "timeframe": "5m",
        "risk_policy": "rm_v1",
    }


def _decoys() -> list[dict]:
    holdout_full = _bh("holdout_full")
    holdout_full["fail_reasons"] = ["holdout_daily_sharpe 0.10 <= bh 0.40"]
    holdout_full["gate_v4"] = {
        "holdout": {"sharpe": 0.4, "trades": 10, "pnl": 1.0, "sma_pnl": 0.1},
        "dsr": 0.99,
        "pbo": 0.1,
        "n_trials": 10,
        "neighbours": 4,
        "neighbours_pass": 4,
        "cost_sharpe": 0.4,
    }
    later = _bh("later_started")
    later["gate_v4"] = {"dsr": 0.5}
    return [
        {**_bh("other_fail"), "fail_reasons": ["oos_trades 10 < 30"]},
        {**_bh("mixed"), "fail_reasons": [
            "daily_sharpe 0.22 <= bh_daily_sharpe 0.415",
            "oos_trades 10 < 30",
        ]},
        holdout_full,
        {**_bh("holdout_sentence"), "fail_reasons": ["holdout_daily_sharpe 0.10 <= bh 0.40"]},
        {**_bh("v5_tag"), "gate_rules": GATE_RULES},
        {**_bh("already_ok"), "qualified": True, "fail_reasons": []},
        {**_bh("ops"), "ops_park": True},
        {**_bh("cost_floor"), "fail_reasons": [
            "cost stress sharpe 0.10 / daily 0.50 vs bh 0.10 (2x fee + 5bps)",
        ]},
        {**_bh("other_old_tag"), "gate_rules": "sltp_cap100_bhdsr_other"},
        later,
        _bh("on_the_book"),
    ]


def _tagged(name: str, rules: str, reason: str | None = None) -> dict:
    row = _bh(name, reason=reason)
    row["gate_rules"] = rules
    return row


def _prod_like() -> list[dict]:
    """1 gate_v4 + 185 tiled + 156 pre-tiled. The missing-Sharpe sentence is one of the 185."""
    rows = [_bh("v4bh_0000")]
    rows.append(_tagged(
        "tiled_missing",
        V3_GATE_RULES,
        reason="buy-and-hold daily sharpe missing",
    ))
    rows.extend(_tagged(f"tiled_{i:04d}", V3_GATE_RULES) for i in range(184))
    rows.extend(_tagged(f"plain_{i:04d}", LEGACY_BH_RULES) for i in range(156))
    rows.extend(_decoys())
    return rows


class SelectTests(unittest.TestCase):
    def test_prototype_is_eligible_and_decoys_are_not(self):
        self.assertTrue(is_requeue_candidate(_bh("dip_222b_lt8pc&h4_ema_abv_160")))
        self.assertTrue(is_requeue_candidate(
            _bh("missing_only", reason="buy-and-hold daily sharpe missing")
        ))
        self.assertTrue(is_requeue_candidate(_tagged("tiled_ok", V3_GATE_RULES)))
        self.assertTrue(is_requeue_candidate(_tagged("plain_ok", LEGACY_BH_RULES)))
        for row in _decoys():
            if row["strategy"] == "on_the_book":
                self.assertTrue(is_requeue_candidate(row))
                continue
            self.assertFalse(is_requeue_candidate(row), row["strategy"])

    def test_dry_run_counts_342_and_apply_is_idempotent(self):
        from hedge_fund.trading.discovery import save_discovery_log

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [
                    {"name": "kept_a", "closed": 0, "pnl": 0.0, "wins": 0},
                    {"name": "on_the_book", "closed": 0, "pnl": 0.0, "wins": 0},
                ],
                "synced_until": "",
            }))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_discovery_log(_prod_like())
                before = (root / "champions.json").read_bytes()
                holdout = (HOLDOUT_START_MS, HOLDOUT_END_MS)
                dry = requeue_v4_bh(apply=False)
                first = requeue_v4_bh(apply=True, now="2026-10-27T22:40:00+00:00")
                second = requeue_v4_bh(apply=True, now="2026-10-27T23:40:00+00:00")
                again = requeue_v4_bh(apply=False)
                after = (root / "champions.json").read_bytes()
        self.assertEqual(dry["count"], 342)
        self.assertTrue(dry["dry_run"])
        self.assertFalse(dry["applied"])
        self.assertEqual(dry["admitted"], [])
        self.assertEqual(dry["retired"], [])
        self.assertFalse(dry["holdout_unsealed"])
        self.assertNotIn("on_the_book", dry["names"])
        self.assertNotIn("other_fail", dry["names"])
        self.assertNotIn("other_old_tag", dry["names"])
        self.assertIn("v4bh_0000", dry["names"])
        self.assertIn("tiled_missing", dry["names"])
        self.assertIn("plain_0000", dry["names"])
        self.assertEqual(dry["by_rules"], {
            "gate_v4": 1,
            V3_GATE_RULES: 185,
            LEGACY_BH_RULES: 156,
        })
        self.assertEqual(len(dry["names"]), 342)
        self.assertEqual(dry["batch_id"], "bh-only-requeue-20261027")
        self.assertTrue(first["applied"])
        self.assertEqual(first["applied_at"], "2026-10-27T22:40:00+00:00")
        self.assertEqual(first["count"], 342)
        self.assertEqual(first["by_rules"][V3_GATE_RULES], 185)
        self.assertEqual(first["by_rules"][LEGACY_BH_RULES], 156)
        self.assertTrue(second["already_applied"])
        self.assertFalse(second["applied"])
        self.assertEqual(second["applied_at"], "2026-10-27T22:40:00+00:00")
        self.assertEqual(again["names"], first["names"])
        self.assertEqual(before, after)
        self.assertEqual((HOLDOUT_START_MS, HOLDOUT_END_MS), holdout)

    def test_holdout_stays_sealed_under_mint(self):
        from hedge_fund.trading.holdout import HoldoutLeak, read_bars, scoring_purpose

        with scoring_purpose("mint"):
            with self.assertRaises(HoldoutLeak):
                read_bars([[HOLDOUT_START_MS, 1, 1, 1, 1, 1]], purpose="holdout")


class RetestLaneTests(unittest.TestCase):
    def _state(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "champions.json").write_text(json.dumps({
            "champions": [
                {"name": "kept_a", "closed": 0, "pnl": 0.0, "wins": 0},
                {"name": "kept_b", "closed": 0, "pnl": 0.0, "wins": 0},
            ],
            "synced_until": "",
        }))
        return tmp, root

    def test_claim_leases_the_retest_ahead_of_mint_and_fail_once_returns(self):
        from hedge_fund.trading.discovery import load_discovery_log, save_discovery_log
        from hedge_fund.trading.discovery_results import discovery_results_path
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.trading.trials import n_trials_for_retest, trial_census

        name = "dip_222b_lt8pc&h4_ema_abv_160"
        other = "already_failed"
        tmp, root = self._state()
        try:
            with patch.dict(os.environ, {
                "PAPER_STATE": str(root),
                "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
            }):
                save_discovery_log([
                    _bh(name),
                    {**_bh(other), "fail_reasons": ["oos_sharpe 0.10 < 0.30"]},
                ])
                applied = requeue_v4_bh(apply=True, now="2026-10-27T22:40:00+00:00")
                self.assertEqual(applied["names"], [name])
                before = trial_census()["n_backtested"]
                self.assertGreaterEqual(before, 1)
                with patch("hedge_fund.trading.leases.discovery_universe", return_value=["don_hi_12", "don_hi_18"]):
                    with patch("hedge_fund.trading.leases._mint_unlocked", side_effect=AssertionError("mint")):
                        with patch("hedge_fund.trading.leases.schedule_refill_buffer"):
                            claimed = claim_discovery_batch("zealous-lion", 1, parallel=1)
                self.assertEqual(claimed["retest"], [name])
                self.assertEqual(claimed["names"], [name])
                self.assertNotIn(other, claimed["names"])
                fresh = {
                    "strategy": name,
                    "qualified": False,
                    "tested_at": "2026-10-27T23:00:00+00:00",
                    "sharpe": 0.33,
                    "trades": 40,
                    "test_pnl": 8.0,
                    "fail_reasons": ["oos_sharpe 0.33 < 0.30"],
                    "timeframe": "5m",
                    "risk_policy": "rm_v1",
                    "gate_rules": GATE_RULES,
                }
                out = ingest_discovery_payload({"evaluations": [fresh, {**fresh, "strategy": other}]})
                logged = {}
                for row in load_discovery_log():
                    logged.setdefault(row["strategy"], row)
                again = ingest_discovery_payload({"evaluations": [fresh]})
                after = trial_census()
                body = discovery_results_path().read_text()
        finally:
            tmp.cleanup()
        self.assertEqual(out["ingested"], [name])
        self.assertEqual(out["admitted"], [])
        self.assertEqual(out["proposed"], [])
        reasons = {row["strategy"]: row["reason"] for row in out["skipped"]}
        self.assertEqual(reasons[other], "already_tested")
        row = logged[name]
        self.assertTrue(row["retest"])
        self.assertEqual(row["retest_reason"], RETEST_REASON)
        self.assertEqual(row["retest_batch"], BATCH_ID)
        self.assertEqual(row["retest_from_rules"], V4_GATE_RULES)
        self.assertEqual(row["prior_fail_reasons"], ["daily_sharpe 0.22 <= bh_daily_sharpe 0.415"])
        self.assertEqual(again["ingested"], [])
        self.assertEqual(again["skipped"][0]["reason"], "already_tested")
        self.assertEqual(after["n_backtested"], before)
        self.assertEqual(n_trials_for_retest(after), before)
        self.assertEqual(after["n_trials"], before + 1)
        self.assertIn('"retest":true', body.replace(" ", ""))

    def test_worker_uses_the_backtested_count_for_a_retest(self):
        from scripts.discovery_worker import _init_pool, _trials_for

        _init_pool(None, None, None, 18, None, None, None, None, 11, ["re_name"], 10)
        self.assertEqual(_trials_for("re_name"), 10)
        self.assertEqual(_trials_for("fresh_name"), 11)

    def test_script_defaults_to_dry_run(self):
        from hedge_fund.trading.discovery import save_discovery_log
        from scripts.requeue_v4_bh import main

        tmp, root = self._state()
        try:
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_discovery_log([_bh("only_one")])
                buf_out = __import__("io").StringIO()
                with patch("sys.stdout", buf_out):
                    code = main([])
                report = json.loads(buf_out.getvalue())
        finally:
            tmp.cleanup()
        self.assertEqual(code, 0)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["count"], 1)
        self.assertFalse((root / "discovery_bh_requeue.json").exists())
        self.assertFalse((root / "discovery_v4_bh_requeue.json").exists())

    def test_unapplied_v4_file_does_not_block_the_wider_batch(self):
        from hedge_fund.trading.discovery import save_discovery_log
        from hedge_fund.trading.requeue_v4_bh import pending_retest_names

        tmp, root = self._state()
        try:
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                (root / "discovery_v4_bh_requeue.json").write_text(json.dumps({
                    "batch_id": "v4-bh-requeue-20261027",
                    "applied_at": "2026-10-27T22:00:00+00:00",
                    "order": ["someone_else"],
                    "entries": {"someone_else": {"status": "pending"}},
                }))
                save_discovery_log([
                    _bh("only_v4"),
                    _tagged("only_tiled", V3_GATE_RULES),
                ])
                dry = requeue_v4_bh(apply=False)
                applied = requeue_v4_bh(apply=True, now="2026-10-27T23:10:00+00:00")
                pending = pending_retest_names()
                old = json.loads((root / "discovery_v4_bh_requeue.json").read_text())
                new_file = json.loads((root / "discovery_bh_requeue.json").read_text())
        finally:
            tmp.cleanup()
        self.assertFalse(dry["already_applied"])
        self.assertEqual(dry["count"], 2)
        self.assertEqual(sorted(dry["names"]), ["only_tiled", "only_v4"])
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["batch_id"], BATCH_ID)
        self.assertNotEqual(BATCH_ID, "v4-bh-requeue-20261027")
        self.assertEqual(old["applied_at"], "2026-10-27T22:00:00+00:00")
        self.assertEqual(old["order"], ["someone_else"])
        self.assertEqual(new_file["batch_id"], BATCH_ID)
        self.assertEqual(pending, ["only_tiled", "only_v4"])


class RequeueHttpTests(unittest.TestCase):
    def _start(self):
        from hedge_fund.web.server import Handler

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread, httpd.server_address[1]

    def _stop(self, httpd, thread):
        httpd.shutdown()
        thread.join(timeout=3)
        httpd.server_close()

    def test_token_and_boolean_apply(self):
        from hedge_fund.trading.discovery import save_discovery_log

        httpd, thread, port = self._start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                env = {
                    "PAPER_STATE": str(root),
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                }
                with patch.dict(os.environ, env):
                    save_discovery_log([_bh("only_one")])
                    (root / "champions.json").write_text(json.dumps({
                        "champions": [{"name": "kept_a"}, {"name": "kept_b"}],
                        "synced_until": "",
                    }))
                    bad = Request(
                        f"http://127.0.0.1:{port}/api/discovery/requeue",
                        data=b"{}",
                        method="POST",
                        headers={"X-Discovery-Token": "nope", "Content-Type": "application/json"},
                    )
                    try:
                        urlopen(bad, timeout=3)
                        self.fail("expected HTTPError")
                    except HTTPError as err:
                        self.assertEqual(err.code, 401)
                    dry = Request(
                        f"http://127.0.0.1:{port}/api/discovery/requeue",
                        data=b'{"apply": "true"}',
                        method="POST",
                        headers={
                            "Authorization": "Bearer paper-secret-token",
                            "Content-Type": "application/json",
                        },
                    )
                    dry_body = json.loads(urlopen(dry, timeout=3).read().decode())
                    self.assertTrue(dry_body["dry_run"])
                    self.assertFalse(dry_body["applied"])
                    self.assertEqual(dry_body["count"], 1)
                    apply = Request(
                        f"http://127.0.0.1:{port}/api/discovery/requeue",
                        data=b'{"apply": true}',
                        method="POST",
                        headers={
                            "Authorization": "Bearer paper-secret-token",
                            "Content-Type": "application/json",
                        },
                    )
                    apply_body = json.loads(urlopen(apply, timeout=5).read().decode())
                    self.assertTrue(apply_body["applied"])
                    self.assertEqual(apply_body["names"], ["only_one"])
                    self.assertFalse(apply_body["holdout_unsealed"])
                    self.assertEqual(len(json.loads((root / "champions.json").read_text())["champions"]), 2)
        finally:
            self._stop(httpd, thread)


if __name__ == "__main__":
    unittest.main()
