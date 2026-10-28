"""GET /api/discovery/insights — gate reject counts and near-misses."""
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

from hedge_fund.trading.constants import GATE_RULES
from hedge_fund.web.discovery_insights import (
    build_insights,
    classify_reason,
    clear_insights_cache,
    discovery_insights_payload,
)


def _row(name, ts, reasons, *, qualified=False, rules=GATE_RULES, dsr=None, sharpe=0.5):
    row = {
        "strategy": name,
        "tested_at": ts,
        "qualified": qualified,
        "fail_reasons": reasons,
        "sharpe": sharpe,
        "trades": 60,
        "gate_rules": rules,
    }
    if dsr is not None:
        row["gate_v4"] = {"dsr": dsr, "pbo": 0.1, "neighbours": 10, "neighbours_pass": 8,
                          "cost_sharpe": 0.4, "holdout": {"sharpe": 0.5, "trades": 12}}
    return row


LOG = [
    _row("a", "2026-10-28T10:00:00+00:00", ["oos_trades 12 < 30", "oos_sharpe 0.10 < 0.3"]),
    _row("b", "2026-10-28T09:00:00+00:00", ["dsr 0.900 < 0.95"], dsr=0.9),
    _row("c", "2026-10-28T08:00:00+00:00", ["pbo 0.300 > 0.2", "cost stress sharpe 0.10 < 0.3 (2x fee + 5bps)"], dsr=0.97),
    _row("d", "2026-10-28T07:00:00+00:00", [], qualified=True, dsr=0.99),
    _row("e", "2026-10-28T06:00:00+00:00", ["oos_pnl 1.00 <= sma_stack 5.00"]),
    _row("old", "2026-09-01T00:00:00+00:00", ["dsr 0.5 < 0.95"], rules="gate_v3_old", dsr=0.5),
    _row("b", "2026-10-01T00:00:00+00:00", ["oos_trades 1 < 30"]),
]


class ClassifyTests(unittest.TestCase):
    def test_reason_strings_map_to_gates(self):
        cases = {
            "oos_trades 12 < 30": "trades",
            "oos_sharpe 0.10 < 0.3": "sharpe",
            "oos_pnl 1.00 <= sma_stack 5.00": "baseline",
            "sma_stack missing": "baseline",
            "holdout_pnl 1.00 <= sma_stack 2.00": "holdout",
            "holdout_trades 3 < 8": "holdout",
            "dsr 0.900 < 0.95": "dsr",
            "pbo 0.300 > 0.2": "pbo",
            "neighbours 3/10 = 30% < 70%": "perturb",
            "no parameter neighbours (lone spike)": "perturb",
            "cost stress sharpe 0.10 < 0.3 (2x fee + 5bps)": "cost",
            "windows 7 != expected 18": "windows",
            "lookback_too_expensive": "ops",
        }
        for reason, gate in cases.items():
            self.assertEqual(classify_reason(reason), gate, reason)


class BuildInsightsTests(unittest.TestCase):
    def test_counts_only_current_gate_newest_per_name(self):
        body = build_insights(LOG)
        self.assertEqual(body["rows_scanned"], 6)
        self.assertEqual(body["current_gate"], {"tested": 5, "cleared_base_floors": 3, "passed": 1})
        counts = {g["gate"]: g["count"] for g in body["gate_failures"]}
        self.assertEqual(counts["trades"], 1)
        self.assertEqual(counts["dsr"], 1)
        self.assertEqual(counts["baseline"], 1)
        self.assertNotIn("holdout", counts)

    def test_near_misses_rank_fewest_robust_fails_first(self):
        body = build_insights(LOG)
        names = [r["strategy"] for r in body["near_misses"]]
        self.assertEqual(names, ["b", "c"])
        self.assertEqual(body["near_misses"][0]["failed_gates"], ["dsr"])
        self.assertAlmostEqual(body["near_misses"][0]["gate"]["dsr"], 0.9)

    def test_recent_is_newest_first(self):
        body = build_insights(LOG)
        self.assertEqual([r["strategy"] for r in body["recent"]][:3], ["a", "b", "c"])


class InsightsRouteTests(unittest.TestCase):
    def test_route_serves_cached_payload(self):
        from hedge_fund.web.server import Handler

        clear_insights_cache()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(json.dumps(LOG))
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                port = httpd.server_address[1]
                with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                    body = json.loads(urlopen(f"http://127.0.0.1:{port}/api/discovery/insights", timeout=10).read())
                    self.assertTrue(body["paper_only"])
                    self.assertEqual(body["current_gate"]["passed"], 1)
                    self.assertIs(discovery_insights_payload(), discovery_insights_payload())
            finally:
                httpd.shutdown()
                httpd.server_close()
                clear_insights_cache()


if __name__ == "__main__":
    unittest.main()
