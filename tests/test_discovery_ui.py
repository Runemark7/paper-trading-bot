"""Discovery page + already-tested column filters and sort (source contract + logic)."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = (REPO / "frontend" / "src" / "App.tsx").read_text()
DISCOVERY_PAGE = (REPO / "frontend" / "src" / "pages" / "Discovery.tsx").read_text()
DISCOVERY_TSX = (REPO / "frontend" / "src" / "status" / "DiscoveryBuckets.tsx").read_text()
FARM_TSX = (REPO / "frontend" / "src" / "status" / "FarmControl.tsx").read_text()
CLIENT_TS = (REPO / "frontend" / "src" / "api" / "client.ts").read_text()
TYPES_TS = (REPO / "frontend" / "src" / "api" / "types.ts").read_text()
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
        self.assertIn("<FarmControl", DISCOVERY_PAGE)
        self.assertIn("/api/discovery/summary", DISCOVERY_PAGE)

    def test_champions_keeps_teaser_not_full_buckets(self):
        self.assertIn("DiscoveryTeaser", CHAMPS)
        self.assertIn('to="/discovery"', DISCOVERY_TSX)
        self.assertNotIn("<DiscoveryBuckets", CHAMPS)
        self.assertNotIn("Already tested", CHAMPS)
        self.assertIn("Open Discovery", DISCOVERY_TSX)
        self.assertIn("farmStatusLabel", DISCOVERY_TSX)
        self.assertIn("Farm:", DISCOVERY_TSX)

    def test_farm_control_start_stop_and_unseen_copy(self):
        self.assertIn("Stop discovery", FARM_TSX)
        self.assertIn("Start discovery", FARM_TSX)
        self.assertIn("Running", FARM_TSX)
        self.assertIn("Paused", FARM_TSX)
        self.assertIn("Worker idle", FARM_TSX)
        self.assertIn("Worker not seen", FARM_TSX)
        self.assertIn("sessionStorage", FARM_TSX)
        self.assertIn("paper_discovery_farm_token", FARM_TSX)
        self.assertIn('type="password"', FARM_TSX)
        self.assertIn("setDiscoveryFarm", FARM_TSX)
        self.assertIn("/api/discovery/farm", CLIENT_TS)
        self.assertIn("X-Discovery-Token", CLIENT_TS)
        self.assertIn("export interface DiscoveryFarm", TYPES_TS)
        self.assertIn("worker_unseen", TYPES_TS)
        self.assertIn("Start will not relaunch", FARM_TSX)
        self.assertIn("Leave the worker running", FARM_TSX)

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
        self.assertIn("applyTestedRows", DISCOVERY_TSX)
        self.assertIn("PhoneCards", DISCOVERY_TSX)
        self.assertIn("DesktopTable", DISCOVERY_TSX)
        src = FILTERS_TS.read_text()
        self.assertIn("export function filterTestedRows", src)
        self.assertIn("export function matchNumeric", src)
        self.assertIn(">=|<=|>|<|=", src)

    def test_already_tested_has_sort_controls(self):
        self.assertIn('aria-label="Sort Already tested"', DISCOVERY_TSX)
        self.assertIn('aria-label="Toggle sort order"', DISCOVERY_TSX)
        self.assertIn("Reset sort", DISCOVERY_TSX)
        self.assertIn("cycleTestedSort", DISCOVERY_TSX)
        self.assertIn("SortTh", DISCOVERY_TSX)
        self.assertIn("aria-sort", DISCOVERY_TSX)
        self.assertIn('aria-label={`Sort by ${label}`}', DISCOVERY_TSX)
        self.assertIn("highest / lowest", DISCOVERY_TSX)
        src = FILTERS_TS.read_text()
        self.assertIn("export function sortTestedRows", src)
        self.assertIn("export function cycleTestedSort", src)
        self.assertIn("export function applyTestedRows", src)
        self.assertIn('key: "testPnl"', src)
        self.assertIn('key: "testedAt"', src)


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


def _apply_names(filters: dict, sort: dict) -> list[str]:
    script = f"""
import {{ applyTestedRows }} from {json.dumps(FILTERS_TS.as_posix())};
const rows = {json.dumps(SAMPLE)};
const filters = {json.dumps(filters)};
const sort = {json.dumps(sort)};
const out = applyTestedRows(rows, filters, sort, (iso) => iso || "", (n) => n == null ? "—" : String(n));
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
        raise AssertionError(f"sort harness failed:\n{proc.stderr or proc.stdout}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _cycle(current: dict, clicked: str) -> dict:
    script = f"""
import {{ cycleTestedSort }} from {json.dumps(FILTERS_TS.as_posix())};
console.log(JSON.stringify(cycleTestedSort({json.dumps(current)}, {json.dumps(clicked)})));
"""
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"cycle harness failed:\n{proc.stderr or proc.stdout}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


class DiscoverySortLogicTests(unittest.TestCase):
    def test_numeric_and_date_sort(self):
        empty = _empty_filters()
        self.assertEqual(
            _apply_names(empty, {"key": "testPnl", "dir": "desc"}),
            ["dip_24b_and_wt_cross", "dip_12b_only", "mom_48b_sma"],
        )
        self.assertEqual(
            _apply_names(empty, {"key": "testPnl", "dir": "asc"}),
            ["mom_48b_sma", "dip_12b_only", "dip_24b_and_wt_cross"],
        )
        self.assertEqual(
            _apply_names(empty, {"key": "sharpe", "dir": "desc"}),
            ["dip_24b_and_wt_cross", "dip_12b_only", "mom_48b_sma"],
        )
        self.assertEqual(
            _apply_names(empty, {"key": "trades", "dir": "asc"}),
            ["mom_48b_sma", "dip_12b_only", "dip_24b_and_wt_cross"],
        )
        self.assertEqual(
            _apply_names(empty, {"key": "testedAt", "dir": "desc"}),
            ["dip_24b_and_wt_cross", "mom_48b_sma", "dip_12b_only"],
        )
        self.assertEqual(
            _apply_names(empty, {"key": "testedAt", "dir": "asc"}),
            ["dip_12b_only", "mom_48b_sma", "dip_24b_and_wt_cross"],
        )

    def test_name_and_result_sort(self):
        empty = _empty_filters()
        self.assertEqual(
            _apply_names(empty, {"key": "strategy", "dir": "asc"}),
            ["dip_12b_only", "dip_24b_and_wt_cross", "mom_48b_sma"],
        )
        self.assertEqual(
            _apply_names(empty, {"key": "strategy", "dir": "desc"}),
            ["mom_48b_sma", "dip_24b_and_wt_cross", "dip_12b_only"],
        )
        # Qualified first; rejected tie-break newest tested_at.
        self.assertEqual(
            _apply_names(empty, {"key": "result", "dir": "desc"}),
            ["dip_24b_and_wt_cross", "mom_48b_sma", "dip_12b_only"],
        )
        self.assertEqual(
            _apply_names(empty, {"key": "result", "dir": "asc"}),
            ["mom_48b_sma", "dip_12b_only", "dip_24b_and_wt_cross"],
        )

    def test_filter_then_sort(self):
        names = _apply_names(_empty_filters(strategy="dip"), {"key": "testPnl", "dir": "asc"})
        self.assertEqual(names, ["dip_12b_only", "dip_24b_and_wt_cross"])

    def test_nulls_sort_last(self):
        rows = SAMPLE + [
            {
                "strategy": "gap_nulls",
                "tested_at": "not-a-date",
                "test_pnl": None,
                "sharpe": None,
                "trades": None,
                "qualified": False,
                "fail_reasons": [],
            }
        ]
        script = f"""
import {{ sortTestedRows }} from {json.dumps(FILTERS_TS.as_posix())};
const rows = {json.dumps(rows)};
const out = sortTestedRows(rows, {{ key: "testPnl", dir: "desc" }});
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
            raise AssertionError(f"null sort harness failed:\n{proc.stderr or proc.stdout}")
        self.assertEqual(
            json.loads(proc.stdout.strip().splitlines()[-1]),
            ["dip_24b_and_wt_cross", "dip_12b_only", "mom_48b_sma", "gap_nulls"],
        )

    def test_header_cycle_none_desc_asc_none(self):
        default = {"key": "testedAt", "dir": "desc"}
        self.assertEqual(_cycle(default, "testPnl"), {"key": "testPnl", "dir": "desc"})
        self.assertEqual(_cycle({"key": "testPnl", "dir": "desc"}, "testPnl"), {"key": "testPnl", "dir": "asc"})
        self.assertEqual(_cycle({"key": "testPnl", "dir": "asc"}, "testPnl"), default)
        self.assertEqual(_cycle({"key": "testPnl", "dir": "desc"}, "sharpe"), {"key": "sharpe", "dir": "desc"})
        self.assertEqual(_cycle(default, "strategy"), {"key": "strategy", "dir": "asc"})
        self.assertEqual(_cycle({"key": "strategy", "dir": "asc"}, "strategy"), {"key": "strategy", "dir": "desc"})
        self.assertEqual(_cycle({"key": "strategy", "dir": "desc"}, "strategy"), default)


if __name__ == "__main__":
    unittest.main()
