"""Forward-test kill criteria flag a champion and never retire it."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.forward_test import evaluate_forward_status, registration_for


NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


def _reg(**extra):
    row = registration_for({"backtest_max_dd": 0.20}, "2026-01-01T00:00:00+00:00")
    row.update(extra)
    return row


class ForwardStatusTests(unittest.TestCase):
    def test_missing_registration_is_not_a_flag(self):
        status = evaluate_forward_status({"name": "dip_x", "champion_since": "2026-01-01T00:00:00+00:00"})
        self.assertEqual(status["status"], "not_registered")
        self.assertFalse(status["flagged"])
        self.assertEqual(status["action"], "flag_only")

    def test_pending_until_both_clocks_have_fired(self):
        live = {"closed": 25, "sharpe": -1.0, "max_dd": 0.9, "source": "test"}
        champ = {
            "name": "dip_x",
            "champion_since": (NOW - timedelta(days=10)).isoformat(),
            "forward_test": _reg(),
        }
        with patch("hedge_fund.trading.forward_test._live_metrics", return_value=live):
            early = evaluate_forward_status(champ, now=NOW)
        self.assertEqual(early["status"], "pending")
        self.assertFalse(early["flagged"])

        champ["champion_since"] = (NOW - timedelta(days=120)).isoformat()
        live["closed"] = 5
        with patch("hedge_fund.trading.forward_test._live_metrics", return_value=live):
            few = evaluate_forward_status(champ, now=NOW)
        self.assertEqual(few["status"], "pending")

    def test_flags_negative_sharpe_and_drawdown_without_writing(self):
        champ = {
            "name": "dip_x",
            "champion_since": (NOW - timedelta(days=120)).isoformat(),
            "forward_test": _reg(),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}), patch(
                "hedge_fund.trading.forward_test._live_metrics",
                return_value={"closed": 21, "sharpe": -0.2, "max_dd": 0.10, "source": "test"},
            ):
                neg = evaluate_forward_status(champ, now=NOW)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}), patch(
                "hedge_fund.trading.forward_test._live_metrics",
                return_value={"closed": 21, "sharpe": 0.4, "max_dd": 0.40, "source": "test"},
            ):
                dd = evaluate_forward_status(champ, now=NOW)
            self.assertFalse((root / "retired.json").exists())
            self.assertFalse((root / "champions.json").exists())
        self.assertEqual(neg["status"], "flagged")
        self.assertTrue(any("live_sharpe" in r for r in neg["reasons"]))
        self.assertEqual(neg["action"], "flag_only")
        self.assertEqual(dd["status"], "flagged")
        self.assertTrue(any("live_max_dd" in r for r in dd["reasons"]))

    def test_ok_when_both_bounds_hold(self):
        champ = {
            "name": "dip_x",
            "champion_since": (NOW - timedelta(days=120)).isoformat(),
            "forward_test": _reg(),
        }
        live = {"closed": 21, "sharpe": 0.3, "max_dd": 0.10, "source": "test"}
        with patch("hedge_fund.trading.forward_test._live_metrics", return_value=live):
            status = evaluate_forward_status(champ, now=NOW)
        self.assertEqual(status["status"], "ok")
        self.assertFalse(status["flagged"])

    def test_pool_status_attaches_the_flag_and_does_not_write(self):
        from hedge_fund.trading.champions import pool_status

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({
                "champions": [{
                    "name": "dip_x",
                    "closed": 0,
                    "pnl": 0.0,
                    "wins": 0,
                    "champion_since": "2026-10-09T06:06:03+00:00",
                }],
                "synced_until": "",
            }))
            before = (root / "champions.json").read_bytes()
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                payload = pool_status(read_only=True)
            self.assertEqual((root / "champions.json").read_bytes(), before)
        row = payload["active_champions"][0]
        self.assertEqual(row["forward_status"]["status"], "not_registered")
        self.assertEqual(row["forward_status"]["action"], "flag_only")


if __name__ == "__main__":
    unittest.main()
