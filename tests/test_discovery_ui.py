"""Discovery page + already-tested column filters (source contract + filter logic)."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = (REPO / "frontend" / "src" / "App.tsx").read_text()
DISCOVERY_PAGE = (REPO / "frontend" / "src" / "pages" / "Discovery.tsx").read_text()
DISCOVERY_TSX = (REPO / "frontend" / "src" / "status" / "DiscoveryBuckets.tsx").read_text()
FILTERS_TS = REPO / "frontend" / "src" / "status" / "discoveryFilters.ts"
CHAMPS = (REPO / "frontend" / "src" / "pages" / "Champions.tsx").read_text()
OVERVIEW = (REPO / "frontend" / "src" / "pages" / "Overview.tsx").read_text()

SAMPLE = [
    {
        "strategy": "dip_24b_and_wt_cross",
        "tested_at": "2026-09-11T12:00:00Z",
        "test_pnl": 120.5,
        "sharpe": 1.25,
        "trades": 40,
        "qualified": True,
        "fail_reasons": [],
    },
    {
        "strategy": "mom_48b_sma",
        "tested_at": "2026-09-10T08:30:00Z",
        "test_pnl": -15.0,
        "sharpe": 0.2,
        "trades": 12,
        "qualified": False,
        "fail_reasons": ["oos_trades < 30", "sharpe below floor"],
    },
    {
        "strategy": "dip_12b_only",
        "tested_at": "2026-09-09T00:00:00Z",
        "test_pnl": 5.0,
        "sharpe": 0.81,
        "trades": 31,
        "qualified": False,
        "fail_reasons": ["fail once parked"],
    },
]


def _empty_filters(**overrides) -> dict:
    base = {
        "strategy": "",
        "result": "all",
        "testedAt": "",
        "sharpe": "",
        "trades": "",
        "testPnl": "",
        "failReasons": "",
    }
    base.update(overrides)
    return base


def _filter_names(filters: dict) -> list[str]:
    script = f"""
import {{ filterTestedRows }} from {json.dumps(FILTERS_TS.as_posix())};
const rows = {json.dumps(SAMPLE)};
const filters = {json.dumps(filters)};
const out = filterTestedRows(rows, filters, (iso) => iso || "", (n) => n == null ? "—" : String(n));
console.log(JSON.stringify(out.map((r) => r.strategy)));
"""
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"filter harness failed:\n{proc.stderr or proc.stdout}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


class DiscoveryPageContractTests(unittest.TestCase):
    def test_nav_and_route_own_discovery_page(self):
        self.assertIn('to: "/discovery"', APP)
        self.assertIn('label: "Discovery"', APP)
        self.assertIn('path="/discovery"', APP)
        self.assertIn("grid-cols-6", APP)
        self.assertIn("import Discovery from", APP)
        self.assertIn("<DiscoveryBuckets", DISCOVERY_PAGE)
        self.assertIn("/api/discovery/summary", DISCOVERY_PAGE)

    def test_champions_keeps_teaser_not_full_buckets(self):
        self.assertIn("DiscoveryTeaser", CHAMPS)
        self.assertIn('to="/discovery"', CHAMPS)
        self.assertNotIn("<DiscoveryBuckets", CHAMPS)
        self.assertNotIn("Already tested", CHAMPS)
        self.assertIn("Open Discovery", DISCOVERY_TSX)

    def test_overview_links_to_discovery(self):
        self.assertIn('to="/discovery"', OVERVIEW)

    def test_already_tested_has_per_column_filters(self):
        for label in (
            'label="Name"',
            'label="When"',
            'aria-label="Filter Result"',
            'label="Sharpe"',
            'label="OOS trades"',
            'label="Test P&L"',
            'label="Fail reasons"',
        ):
            self.assertIn(label, DISCOVERY_TSX, label)
        self.assertIn('aria-label={`Filter ${label}`}', DISCOVERY_TSX)
        self.assertIn("filterTestedRows", DISCOVERY_TSX)
        self.assertIn("PhoneCards", DISCOVERY_TSX)
        self.assertIn("DesktopTable", DISCOVERY_TSX)
        src = FILTERS_TS.read_text()
        self.assertIn("export function filterTestedRows", src)
        self.assertIn("export function matchNumeric", src)
        self.assertIn(">=|<=|>|<|=", src)


class DiscoveryFilterLogicTests(unittest.TestCase):
    def test_empty_filters_keep_all(self):
        self.assertEqual(_filter_names(_empty_filters()), [r["strategy"] for r in SAMPLE])

    def test_strategy_and_result_filters(self):
        self.assertEqual(_filter_names(_empty_filters(strategy="dip")), ["dip_24b_and_wt_cross", "dip_12b_only"])
        self.assertEqual(_filter_names(_empty_filters(result="qualified")), ["dip_24b_and_wt_cross"])
        self.assertEqual(
            _filter_names(_empty_filters(result="rejected")),
            ["mom_48b_sma", "dip_12b_only"],
        )

    def test_numeric_and_fail_reason_filters(self):
        self.assertEqual(_filter_names(_empty_filters(sharpe=">1")), ["dip_24b_and_wt_cross"])
        self.assertEqual(_filter_names(_empty_filters(trades=">=30")), ["dip_24b_and_wt_cross", "dip_12b_only"])
        self.assertEqual(_filter_names(_empty_filters(testPnl="<0")), ["mom_48b_sma"])
        self.assertEqual(_filter_names(_empty_filters(failReasons="parked")), ["dip_12b_only"])
        self.assertEqual(_filter_names(_empty_filters(testedAt="2026-09-10")), ["mom_48b_sma"])


if __name__ == "__main__":
    unittest.main()
