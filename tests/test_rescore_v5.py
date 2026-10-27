"""Dry-run-first gate v5 rescore. Does not admit or retire champions."""
from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hedge_fund.trading.constants import DISCOVERY_N_WINDOWS, GATE_RULES, V4_GATE_RULES
from hedge_fund.trading.rescore_v5 import RESCORE_REASON, rescore_v5


def _holdout(*, daily: float = 0.6, bh: float = 0.2) -> dict:
    return {
        "sharpe": 0.4,
        "trades": 8,
        "pnl": 10.0,
        "sma_pnl": 1.0,
        "daily_sharpe": daily,
        "bh_daily_sharpe": bh,
    }


_DEFAULT_BLOCK = object()


def _row(
    name: str,
    *,
    fail_reasons: list[str],
    dsr: float = 0.99,
    daily: float = 0.5,
    qualified: bool = False,
    gate_v4: object = _DEFAULT_BLOCK,
    **extra,
) -> dict:
    if gate_v4 is _DEFAULT_BLOCK:
        block = {
            "dsr": dsr,
            "n_trials": 100,
            "pbo": 0.1,
            "neighbours": 10,
            "neighbours_pass": 8,
            "cost_sharpe": 0.4,
            "cost_daily_sharpe": 0.5,
            "cost_bh_daily_sharpe": 0.1,
            "holdout": _holdout(),
        }
    else:
        block = gate_v4
    row = {
        "strategy": name,
        "qualified": qualified,
        "sharpe": 0.5,
        "trades": 40,
        "test_pnl": 100.0,
        "sma_stack_oos_pnl": 1.0,
        "daily_sharpe": daily,
        "bh_daily_sharpe": 1.2,
        "bh_oos_pnl": 500.0,
        "regimes_tested": DISCOVERY_N_WINDOWS,
        "symbols": ["BTC/USDT"],
        "gate_rules": V4_GATE_RULES,
        "fail_reasons": fail_reasons,
        "gate_v4": block,
    }
    row.update(extra)
    return row


def _fixture() -> list[dict]:
    return [
        _row("alpha", dsr=0.99, daily=0.5, fail_reasons=["daily_sharpe 0.40 <= bh_daily_sharpe 1.20"]),
        _row("beta", dsr=0.99, daily=0.8, fail_reasons=["buy-and-hold daily sharpe missing"]),
        _row(
            "gamma",
            dsr=0.97,
            daily=1.0,
            fail_reasons=[
                "buy-and-hold daily sharpe missing",
                "daily_sharpe 0.20 <= bh_daily_sharpe 0.90",
            ],
        ),
        _row("no_block", fail_reasons=["buy-and-hold daily sharpe missing"], gate_v4=None),
        _row(
            "also_trades",
            fail_reasons=["buy-and-hold daily sharpe missing", "oos_trades 10 < 30"],
        ),
        _row(
            "holdout_loss",
            fail_reasons=["daily_sharpe 0.40 <= bh_daily_sharpe 1.20"],
            gate_v4={
                "dsr": 0.99,
                "n_trials": 100,
                "pbo": 0.1,
                "neighbours": 10,
                "neighbours_pass": 8,
                "cost_sharpe": 0.4,
                "cost_daily_sharpe": 0.5,
                "cost_bh_daily_sharpe": 0.1,
                "holdout": _holdout(daily=0.1, bh=0.4),
            },
        ),
        _row(
            "holdout_only",
            dsr=0.96,
            daily=0.4,
            fail_reasons=["holdout_daily_sharpe 0.10 <= bh 0.40"],
            gate_v4={
                "dsr": 0.96,
                "n_trials": 100,
                "pbo": 0.1,
                "neighbours": 10,
                "neighbours_pass": 8,
                "cost_sharpe": 0.4,
                "cost_daily_sharpe": 0.1,
                "cost_bh_daily_sharpe": 0.8,
                "holdout": _holdout(daily=0.1, bh=0.4),
            },
        ),
        _row(
            "stress_only",
            dsr=0.96,
            daily=0.2,
            fail_reasons=["cost stress sharpe 0.40 / daily 0.10 vs bh 0.80 (2x fee + 5bps)"],
            gate_v4={
                "dsr": 0.96,
                "n_trials": 100,
                "pbo": 0.1,
                "neighbours": 10,
                "neighbours_pass": 8,
                "cost_sharpe": 0.4,
                "cost_daily_sharpe": 0.1,
                "cost_bh_daily_sharpe": 0.8,
                "holdout": _holdout(),
            },
        ),
        _row(
            "stress_floor",
            fail_reasons=["cost stress sharpe 0.10 / daily 0.50 vs bh 0.10 (2x fee + 5bps)"],
            gate_v4={
                "dsr": 0.99,
                "n_trials": 100,
                "pbo": 0.1,
                "neighbours": 10,
                "neighbours_pass": 8,
                "cost_sharpe": 0.1,
                "cost_daily_sharpe": 0.5,
                "cost_bh_daily_sharpe": 0.1,
                "holdout": _holdout(),
            },
        ),
        _row(
            "holdout_mixed",
            fail_reasons=["holdout_daily_sharpe 0.10 <= bh 0.40; holdout_sharpe 0.10 < 0.3"],
            gate_v4={
                "dsr": 0.99,
                "n_trials": 100,
                "pbo": 0.1,
                "neighbours": 10,
                "neighbours_pass": 8,
                "cost_sharpe": 0.4,
                "cost_daily_sharpe": 0.5,
                "cost_bh_daily_sharpe": 0.1,
                "holdout": _holdout(daily=0.1, bh=0.4),
            },
        ),
        _row("already", fail_reasons=[], qualified=True),
        _row(
            "done",
            fail_reasons=[],
            qualified=True,
            gate_rules=GATE_RULES,
            rescore_reason=RESCORE_REASON,
            rescored_at="2026-10-27T21:00:00+00:00",
        ),
    ]


class RescoreTests(unittest.TestCase):
    def _state(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        champs = {
            "champions": [
                {"name": "kept_a", "closed": 0, "pnl": 0.0, "wins": 0},
                {"name": "kept_b", "closed": 0, "pnl": 0.0, "wins": 0},
            ],
            "synced_until": "",
        }
        (root / "champions.json").write_text(json.dumps(champs))
        return tmp, root

    def test_dry_run_counts_and_does_not_write(self):
        from hedge_fund.trading.discovery import save_discovery_log

        tmp, root = self._state()
        try:
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_discovery_log(_fixture())
                before_log = (root / "discovery_log.json").read_bytes()
                before_champs = (root / "champions.json").read_bytes()
                report = rescore_v5(apply=False, now="2026-10-27T22:10:00+00:00")
                after_log = (root / "discovery_log.json").read_bytes()
                after_champs = (root / "champions.json").read_bytes()
        finally:
            tmp.cleanup()
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["applied"])
        self.assertIsNone(report["rescored_at"])
        self.assertEqual(report["counts"], {
            "relabel": 6,
            "already_rescored": 1,
            "already_qualified": 1,
            "missing_inputs": 1,
            "other_failures": 3,
        })
        ranked = ["beta", "alpha", "holdout_loss", "gamma", "holdout_only", "stress_only"]
        self.assertEqual(report["relabel_names"], ranked)
        self.assertEqual([item["strategy"] for item in report["top"]], ranked)
        self.assertEqual(report["admitted"], [])
        self.assertEqual(report["retired"], [])
        self.assertEqual(report["champions_active"], 2)
        self.assertEqual(before_log, after_log)
        self.assertEqual(before_champs, after_champs)

    def test_apply_is_idempotent_and_does_not_touch_champions(self):
        from hedge_fund.trading.discovery import load_discovery_log, save_discovery_log

        tmp, root = self._state()
        try:
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_discovery_log(_fixture())
                before_champs = (root / "champions.json").read_bytes()
                first = rescore_v5(apply=True, now="2026-10-27T22:10:00+00:00")
                stamped = {
                    row["strategy"]: row
                    for row in load_discovery_log()
                    if row["strategy"] in {"alpha", "beta", "gamma"}
                }
                second = rescore_v5(apply=True, now="2026-10-27T23:00:00+00:00")
                again = {
                    row["strategy"]: row
                    for row in load_discovery_log()
                    if row["strategy"] in {"alpha", "beta", "gamma", "done", "no_block"}
                }
                after_champs = (root / "champions.json").read_bytes()
        finally:
            tmp.cleanup()
        self.assertFalse(first["dry_run"])
        self.assertTrue(first["applied"])
        self.assertEqual(first["rescored_at"], "2026-10-27T22:10:00+00:00")
        self.assertEqual(first["admitted"], [])
        self.assertEqual(first["retired"], [])
        for name in ("alpha", "beta", "gamma"):
            row = stamped[name]
            self.assertTrue(row["qualified"])
            self.assertEqual(row["fail_reasons"], [])
            self.assertEqual(row["gate_rules"], GATE_RULES)
            self.assertEqual(row["rescore_reason"], RESCORE_REASON)
            self.assertEqual(row["rescored_at"], "2026-10-27T22:10:00+00:00")
            self.assertEqual(row["rescore_from_rules"], V4_GATE_RULES)
            self.assertEqual(row["bh_daily_sharpe_role"], "informational")
            self.assertTrue(row["gate_v4"]["passed"])
            self.assertEqual(row["gate_v4"]["cost_bh_role"], "informational")
            self.assertEqual(row["gate_v4"]["holdout"]["bh_role"], "informational")
        self.assertFalse(second["applied"])
        self.assertFalse(second["dry_run"])
        self.assertEqual(second["counts"]["relabel"], 0)
        self.assertEqual(second["counts"]["already_rescored"], 7)
        for name in ("alpha", "beta", "gamma"):
            self.assertEqual(again[name]["rescored_at"], "2026-10-27T22:10:00+00:00")
        self.assertEqual(again["done"]["rescored_at"], "2026-10-27T21:00:00+00:00")
        self.assertIsNone(again["no_block"]["gate_v4"])
        self.assertFalse(again["no_block"]["qualified"])
        self.assertEqual(before_champs, after_champs)

    def test_script_defaults_to_dry_run(self):
        from hedge_fund.trading.discovery import save_discovery_log
        from scripts.rescore_gate_v5 import main

        tmp, root = self._state()
        try:
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_discovery_log(_fixture())
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = main([])
                report = json.loads(buf.getvalue())
        finally:
            tmp.cleanup()
        self.assertEqual(code, 0)
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["applied"])
        self.assertEqual(report["relabel_count"], 6)


class BhReasonTests(unittest.TestCase):
    def test_only_buy_and_hold_comparisons_match(self):
        from hedge_fund.trading.rescore_v5 import is_bh_decision_reason

        self.assertTrue(is_bh_decision_reason("buy-and-hold daily sharpe missing"))
        self.assertTrue(is_bh_decision_reason("daily_sharpe 0.40 <= bh_daily_sharpe 1.20"))
        self.assertTrue(is_bh_decision_reason("holdout buy-and-hold daily sharpe missing"))
        self.assertTrue(is_bh_decision_reason("holdout_daily_sharpe 0.10 <= bh 0.40"))
        self.assertTrue(is_bh_decision_reason(
            "cost stress sharpe 0.40 / daily 0.10 vs bh 0.80 (2x fee + 5bps)"
        ))
        self.assertTrue(is_bh_decision_reason(
            "cost stress sharpe 0.30 / daily 0.10 vs bh missing (2x fee + 5bps)"
        ))
        self.assertFalse(is_bh_decision_reason(
            "cost stress sharpe 0.10 / daily 0.50 vs bh 0.10 (2x fee + 5bps)"
        ))
        self.assertFalse(is_bh_decision_reason(
            "holdout_daily_sharpe 0.10 <= bh 0.40; holdout_sharpe 0.10 < 0.3"
        ))
        self.assertFalse(is_bh_decision_reason("oos_trades 10 < 30"))
        self.assertFalse(is_bh_decision_reason("cost stress sharpe 0.10 < 0.3 (2x fee + 5bps)"))


class RescoreHttpTests(unittest.TestCase):
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

    def test_token_and_apply_flag(self):
        from hedge_fund.trading.discovery import load_discovery_log, save_discovery_log

        httpd, thread, port = self._start()
        try:
            tmp = tempfile.TemporaryDirectory()
            root = Path(tmp.name)
            env = {
                "PAPER_STATE": str(root),
                "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
            }
            try:
                with patch.dict(os.environ, env):
                    save_discovery_log(_fixture())
                    (root / "champions.json").write_text(json.dumps({
                        "champions": [{"name": "kept_a"}, {"name": "kept_b"}],
                        "synced_until": "",
                    }))
                    bad = Request(
                        f"http://127.0.0.1:{port}/api/discovery/rescore",
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
                        f"http://127.0.0.1:{port}/api/discovery/rescore",
                        data=b"{}",
                        method="POST",
                        headers={
                            "Authorization": "Bearer paper-secret-token",
                            "Content-Type": "application/json",
                        },
                    )
                    dry_body = json.loads(urlopen(dry, timeout=3).read().decode())
                    self.assertTrue(dry_body["dry_run"])
                    self.assertFalse(dry_body["applied"])
                    self.assertEqual(dry_body["relabel_names"], [
                        "beta", "alpha", "holdout_loss", "gamma", "holdout_only", "stress_only",
                    ])

                    explicit = Request(
                        f"http://127.0.0.1:{port}/api/discovery/rescore",
                        data=b'{"apply": false}',
                        method="POST",
                        headers={
                            "Authorization": "Bearer paper-secret-token",
                            "Content-Type": "application/json",
                        },
                    )
                    explicit_body = json.loads(urlopen(explicit, timeout=3).read().decode())
                    self.assertTrue(explicit_body["dry_run"])

                    apply = Request(
                        f"http://127.0.0.1:{port}/api/discovery/rescore",
                        data=b'{"apply": true}',
                        method="POST",
                        headers={
                            "Authorization": "Bearer paper-secret-token",
                            "Content-Type": "application/json",
                        },
                    )
                    apply_body = json.loads(urlopen(apply, timeout=3).read().decode())
                    self.assertTrue(apply_body["applied"])
                    logged = {row["strategy"]: row for row in load_discovery_log()}
                    self.assertEqual(logged["beta"]["rescore_reason"], RESCORE_REASON)
                    self.assertTrue(logged["beta"]["qualified"])
                    self.assertEqual(len(json.loads((root / "champions.json").read_text())["champions"]), 2)
            finally:
                tmp.cleanup()
        finally:
            self._stop(httpd, thread)
