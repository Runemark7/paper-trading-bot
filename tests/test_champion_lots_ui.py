"""Champions accordion: live lots under the tapped card, joined like open_lots.py."""
from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FORMAT_TS = (REPO / "frontend" / "src" / "status" / "format.ts").read_text()
CHAMPS_TSX = (REPO / "frontend" / "src" / "pages" / "Champions.tsx").read_text()
DETAIL_TSX = (REPO / "frontend" / "src" / "pages" / "ChampionDetail.tsx").read_text()
LOTS_TSX = (REPO / "frontend" / "src" / "status" / "ChampionLots.tsx").read_text()
PATH_TS = (REPO / "frontend" / "src" / "status" / "championPath.ts").read_text()
FILTERS_TS = REPO / "frontend" / "src" / "status" / "championFilters.ts"
TABLE_CONTROLS = (REPO / "frontend" / "src" / "components" / "tableControls.tsx").read_text()
APP_TSX = (REPO / "frontend" / "src" / "App.tsx").read_text()
DISCOVERY_TSX = (REPO / "frontend" / "src" / "status" / "DiscoveryBuckets.tsx").read_text()
OPEN_LOTS_PY = (REPO / "hedge_fund" / "trading" / "open_lots.py").read_text()


def account_slug(name: str) -> str:
    return name.replace("/", "_").replace(":", "_")


def paper_account_keys(name: str) -> set[str]:
    """Mirror frontend paperAccountKeys / open_lots.py name · slug · trades_* stem."""
    stripped = name[len("trades_") :] if name.startswith("trades_") else name
    slug = account_slug(name)
    stripped_slug = account_slug(stripped)
    return {
        name,
        slug,
        stripped,
        stripped_slug,
        f"trades_{slug}",
        f"trades_{stripped_slug}",
    }


def accounts_match(account: str | None, champion_name: str) -> bool:
    if not account:
        return False
    return bool(paper_account_keys(account) & paper_account_keys(champion_name))


def lots_for_champion(positions: list[dict], champion_name: str) -> list[dict]:
    return [p for p in positions if accounts_match(p.get("account"), champion_name)]


class AccountJoinTests(unittest.TestCase):
    def test_name_slug_and_sqlite_stem_match(self):
        self.assertTrue(accounts_match("trades_sma_stack", "sma_stack"))
        self.assertTrue(accounts_match("sma_stack", "sma_stack"))
        self.assertTrue(accounts_match("trades_foo_bar", "foo/bar"))
        self.assertTrue(accounts_match("foo_bar", "foo/bar"))
        self.assertTrue(accounts_match("trades_a_b", "a:b"))

    def test_lot_does_not_land_under_the_wrong_champion(self):
        self.assertFalse(accounts_match("trades_dip_6b_lt1pc", "dip_6b_lt3pc"))
        self.assertFalse(accounts_match("trades_sma_stack", "sma_stack_9_28_51"))
        self.assertFalse(accounts_match("trades_bravo", "alpha"))
        self.assertFalse(accounts_match(None, "alpha"))

    def test_btc_and_eth_rows_stay_on_that_account(self):
        positions = [
            {"account": "trades_pair", "symbol": "BTC/USDT", "lot_count": 1},
            {"account": "trades_pair", "symbol": "ETH/USDT", "lot_count": 1},
            {"account": "trades_solo", "symbol": "BTC/USDT", "lot_count": 1},
        ]
        pair = lots_for_champion(positions, "pair")
        self.assertEqual([p["symbol"] for p in pair], ["BTC/USDT", "ETH/USDT"])
        self.assertEqual(sum(p["lot_count"] for p in pair), 2)
        solo = lots_for_champion(positions, "solo")
        self.assertEqual([p["symbol"] for p in solo], ["BTC/USDT"])
        self.assertEqual(lots_for_champion(positions, "missing"), [])

    def test_frontend_helper_uses_the_same_slug_as_open_lots_py(self):
        self.assertIn('name.replace("/", "_").replace(":", "_")', OPEN_LOTS_PY)
        self.assertIn('(name ?? "").replace(/[/:]/g, "_")', FORMAT_TS)
        self.assertIn("function paperAccountKeys", FORMAT_TS)
        self.assertIn("function accountsMatch", FORMAT_TS)
        self.assertIn("function lotsForChampion", FORMAT_TS)
        self.assertIn("function openLotsByPair", FORMAT_TS)
        self.assertIn("function pairOf", FORMAT_TS)
        self.assertIn("function openLotsForChampion", FORMAT_TS)
        self.assertIn("`trades_${slug}`", FORMAT_TS)
        self.assertIn("`trades_${strippedSlug}`", FORMAT_TS)


def pair_of(symbol: str | None) -> str | None:
    if not symbol:
        return None
    s = symbol.upper()
    if s.startswith("BTC"):
        return "BTC"
    if s.startswith("ETH"):
        return "ETH"
    return None


def lot_count(p: dict) -> int:
    nested = p.get("lots") or []
    if nested:
        return len(nested)
    return int(p.get("lot_count") or 1)


def open_lots_by_pair(live: dict | None, champion_name: str) -> dict:
    """Mirror frontend openLotsByPair: lots[] first, else position lot_count."""
    split = {"btc": 0, "eth": 0, "total": 0}
    if not live or not champion_name:
        return split
    lots = [l for l in live.get("lots") or [] if accounts_match(l.get("account"), champion_name)]
    if lots:
        rows = lots
        counts = True
    else:
        rows = lots_for_champion(live.get("positions") or [], champion_name)
        counts = False
    for row in rows:
        n = 1 if counts else lot_count(row)
        pair = pair_of(row.get("symbol"))
        if pair == "BTC":
            split["btc"] += n
        elif pair == "ETH":
            split["eth"] += n
    split["total"] = split["btc"] + split["eth"]
    return split


class PairLotSplitTests(unittest.TestCase):
    def test_two_btc_plus_one_eth_is_btc_2_eth_1_total_3(self):
        live = {
            "lots": [
                {"account": "trades_pair", "symbol": "BTC/USDT", "lot_id": 1},
                {"account": "trades_pair", "symbol": "BTC/USDT", "lot_id": 2},
                {"account": "trades_pair", "symbol": "ETH/USDT", "lot_id": 3},
                {"account": "trades_solo", "symbol": "BTC/USDT", "lot_id": 9},
            ]
        }
        pair = open_lots_by_pair(live, "pair")
        self.assertEqual(pair, {"btc": 2, "eth": 1, "total": 3})
        solo = open_lots_by_pair(live, "solo")
        self.assertEqual(solo, {"btc": 1, "eth": 0, "total": 1})
        missing = open_lots_by_pair(live, "missing")
        self.assertEqual(missing, {"btc": 0, "eth": 0, "total": 0})

    def test_two_btc_lots_are_two_not_one_symbol_row(self):
        live = {
            "lots": [
                {"account": "trades_pyr", "symbol": "BTC/USDT", "lot_id": 1},
                {"account": "trades_pyr", "symbol": "BTC/USDT", "lot_id": 2},
            ],
            "positions": [
                {"account": "trades_pyr", "symbol": "BTC/USDT", "lot_count": 2},
            ],
        }
        self.assertEqual(open_lots_by_pair(live, "pyr"), {"btc": 2, "eth": 0, "total": 2})

    def test_position_lot_count_fallback_does_not_collapse_pyramid(self):
        live = {
            "positions": [
                {"account": "trades_pyr", "symbol": "BTC/USDT", "lot_count": 2},
                {"account": "trades_pyr", "symbol": "ETH/USDT", "lot_count": 1},
            ]
        }
        self.assertEqual(open_lots_by_pair(live, "pyr"), {"btc": 2, "eth": 1, "total": 3})

    def test_nested_position_lots_count_when_flat_list_missing(self):
        live = {
            "positions": [
                {
                    "account": "trades_pair",
                    "symbol": "BTC/USDT",
                    "lot_count": 1,
                    "lots": [
                        {"symbol": "BTC/USDT", "lot_id": 1},
                        {"symbol": "BTC/USDT", "lot_id": 2},
                    ],
                },
            ]
        }
        self.assertEqual(open_lots_by_pair(live, "pair"), {"btc": 2, "eth": 0, "total": 2})

    def test_name_slug_join_matches_open_lots_py(self):
        live = {
            "lots": [
                {"account": "trades_foo_bar", "symbol": "ETH/USDT", "lot_id": 1},
            ]
        }
        self.assertEqual(open_lots_by_pair(live, "foo/bar")["eth"], 1)
        self.assertEqual(open_lots_by_pair(live, "foo/bar")["btc"], 0)

    def test_zero_on_a_side_stays_readable(self):
        live = {
            "lots": [{"account": "trades_alpha", "symbol": "BTC/USDT", "lot_id": 1}],
        }
        split = open_lots_by_pair(live, "alpha")
        self.assertEqual(split["eth"], 0)
        self.assertEqual(split["btc"], 1)
        empty = open_lots_by_pair({"lots": [], "positions": []}, "alpha")
        self.assertEqual(empty, {"btc": 0, "eth": 0, "total": 0})

    def test_live_preview_lots_split_like_the_card(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from hedge_fund.trading.store import TradeStore
        from hedge_fund.web.live import live_preview

        def _lot(ticker: str, lot_id: int) -> dict:
            return {
                "lot_id": lot_id,
                "ticker": ticker,
                "quantity": 0.01,
                "entry_price": 100.0 if ticker.startswith("BTC") else 3_000.0,
                "stop_loss": 95.0 if ticker.startswith("BTC") else 2_850.0,
                "entry_fee": 0.1,
                "entry_condition": "test_long",
            }

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "trades_pair.sqlite"
            TradeStore(db).save_account_state({
                "broker": {
                    "cash": 8_000.0,
                    "lots": [
                        _lot("BTC/USDT", 1),
                        _lot("BTC/USDT", 2),
                        _lot("ETH/USDT", 3),
                    ],
                },
            })
            with patch(
                "hedge_fund.web.live.live_prices",
                return_value={"BTC/USDT": 101.0, "ETH/USDT": 3_010.0},
            ):
                prev = live_preview(str(db))
        acct = "trades_pair"
        for p in prev["positions"]:
            p["account"] = acct
            for lot in p.get("lots") or []:
                lot["account"] = acct
        for lot in prev["lots"]:
            lot["account"] = acct
        self.assertEqual(len(prev["lots"]), 3)
        self.assertEqual(open_lots_by_pair(prev, "pair"), {"btc": 2, "eth": 1, "total": 3})
        self.assertEqual(open_lots_by_pair(prev, "pair")["total"], prev["open_lots"])


class ChampionsListUiTests(unittest.TestCase):
    def test_list_uses_discovery_table_and_navigates_to_detail(self):
        self.assertIn('queryKey: ["live"]', CHAMPS_TSX)
        self.assertIn("api.live", CHAMPS_TSX)
        self.assertIn("openLotsForChampion", CHAMPS_TSX)
        self.assertIn("showing last-known", CHAMPS_TSX)
        self.assertIn("Could not load /api/champions", CHAMPS_TSX)
        self.assertIn("Could not load /api/graduated", CHAMPS_TSX)
        self.assertIn("Could not load /api/discovery/summary", DISCOVERY_TSX)
        self.assertIn("DiscoveryTeaser", CHAMPS_TSX)
        self.assertIn('queryKey: ["discovery-summary", "compact"]', DISCOVERY_TSX)
        self.assertIn("fetchDiscoverySummary({ compact: true })", DISCOVERY_TSX)
        self.assertIn("PageErrorBoundary", APP_TSX)
        self.assertIn('label="Champions"', APP_TSX)
        self.assertIn('to="/discovery"', DISCOVERY_TSX)
        self.assertNotIn("<DiscoveryBuckets", CHAMPS_TSX)
        self.assertNotIn("Already tested", CHAMPS_TSX)
        self.assertNotIn("Discovery log (", CHAMPS_TSX)
        self.assertNotIn("slice(0, 40)", CHAMPS_TSX)
        self.assertNotIn("slice(0, 40)", DISCOVERY_TSX)
        self.assertNotIn("expandedChampion", CHAMPS_TSX)
        self.assertNotIn("aria-expanded", CHAMPS_TSX)
        self.assertIn("PhoneCards", CHAMPS_TSX)
        self.assertIn("DesktopTable", CHAMPS_TSX)
        self.assertIn("applyChampionRows", CHAMPS_TSX)
        self.assertIn("cycleChampionSort", CHAMPS_TSX)
        self.assertIn('aria-label="Sort Champions"', CHAMPS_TSX)
        self.assertIn("Clear filters", CHAMPS_TSX)
        self.assertIn("championDetailPath(c.name)", CHAMPS_TSX)
        self.assertIn("min-h-11", CHAMPS_TSX)
        self.assertIn("accountsMatch(name, c.name)", CHAMPS_TSX)
        self.assertNotIn("ChampionTape", CHAMPS_TSX)
        self.assertIn('path="/champions/*"', APP_TSX)
        self.assertIn("import ChampionDetail", APP_TSX)
        self.assertIn("encodeURIComponent(name)", PATH_TS)
        self.assertIn("decodeURIComponent(raw)", PATH_TS)

    def test_list_row_shows_btc_eth_lot_chips(self):
        list_body = CHAMPS_TSX.split("Graduated paper")[0]
        self.assertIn("openLotsByPair", list_body)
        self.assertIn("PairLotChips", list_body)
        self.assertIn("flex flex-wrap", list_body)
        self.assertIn("BTC {btc}", LOTS_TSX)
        self.assertIn("ETH {eth}", LOTS_TSX)
        self.assertIn("c.btc", list_body)
        self.assertIn("c.eth", list_body)
        self.assertIn("LotHealthSummaryChips", list_body)
        self.assertIn("ChampionSinceChip", list_body)
        self.assertIn('label="Champion since"', list_body)
        self.assertNotIn("open_lots_btc", list_body)
        self.assertNotIn("open_lots_eth", list_body)

    def test_detail_page_shows_summary_lots_tape_and_closed(self):
        self.assertIn("← Champions", DETAIL_TSX)
        self.assertIn('to="/champions"', DETAIL_TSX)
        self.assertIn("CopyableName", DETAIL_TSX)
        self.assertIn("name={name}", DETAIL_TSX)
        self.assertIn("Started {fmtChampionSince(champ?.champion_since)}", DETAIL_TSX)
        self.assertIn("ChampionOpenLotsCard", DETAIL_TSX)
        self.assertIn("ChampionTape", DETAIL_TSX)
        self.assertIn("championName={name}", DETAIL_TSX)
        self.assertIn("closedTradesForChampion", DETAIL_TSX)
        self.assertIn("no open lots", LOTS_TSX)
        self.assertIn('label="Condition" span', LOTS_TSX)
        self.assertIn("Strategy", DETAIL_TSX)

    def test_graduated_section_does_not_invent_live_lots(self):
        grad = CHAMPS_TSX[CHAMPS_TSX.index("Graduated paper") :]
        self.assertNotIn("lotsForChampion", grad)
        self.assertNotIn("ChampionLotList", grad)
        self.assertNotIn("qLive", grad)
        self.assertNotIn("ChampionOpenLots", grad)
        self.assertNotIn("ChampionTape", grad)
        self.assertNotIn("PairLotChips", grad)
        self.assertNotIn("openLotsByPair", grad)
        self.assertNotIn("LotHealthSummaryChips", grad)
        self.assertNotIn("openLotsForChampion", grad)


class DiscoveryBucketsUiTests(unittest.TestCase):
    def test_three_honest_blocks_not_a_scrap_log(self):
        self.assertIn("Being tested", DISCOVERY_TSX)
        self.assertIn("Already tested", DISCOVERY_TSX)
        self.assertIn("Not tested yet", DISCOVERY_TSX)
        self.assertIn("idle — last eval", DISCOVERY_TSX)
        self.assertIn("discovery stuck / cycle overdue", DISCOVERY_TSX)
        self.assertIn("unique tested", DISCOVERY_TSX)
        self.assertNotIn("idle — last sweep", DISCOVERY_TSX)
        self.assertIn("Show more", DISCOVERY_TSX)
        self.assertIn("qualified", DISCOVERY_TSX)
        self.assertIn("rejected", DISCOVERY_TSX)
        self.assertIn("parked forever", DISCOVERY_TSX)
        self.assertIn("Already tested · rejected", DISCOVERY_TSX)
        self.assertNotIn("retest queue", DISCOVERY_TSX)
        self.assertNotIn("24h retest cooldown", DISCOVERY_TSX)
        self.assertNotIn("including retests", DISCOVERY_TSX)
        self.assertIn("/api/discovery/summary", DISCOVERY_TSX)
        self.assertIn("fetchDiscoverySummary", DISCOVERY_TSX)
        self.assertIn("OOS trades", DISCOVERY_TSX)
        self.assertIn("applyTestedRows", DISCOVERY_TSX)
        self.assertIn('aria-label={`Filter ${label}`}', TABLE_CONTROLS)
        self.assertIn('aria-label="Filter Result"', DISCOVERY_TSX)
        self.assertIn("Clear filters", DISCOVERY_TSX)
        self.assertIn("Fail reasons", DISCOVERY_TSX)
        self.assertIn("No already-tested rows match these column filters.", DISCOVERY_TSX)
        self.assertNotIn("Discovery log (", DISCOVERY_TSX)
        self.assertNotIn("Train P&L", DISCOVERY_TSX)


SAMPLE_CHAMPS = [
    {
        "name": "dip_24b_and_wt_cross",
        "closed": 12,
        "pnl": 140.5,
        "wins": 8,
        "open_lots": 3,
        "champion_since": "2026-09-01T12:00:00Z",
        "btc": 2,
        "eth": 1,
        "openLots": 3,
        "liveLotsKnown": True,
        "lots": [],
    },
    {
        "name": "mom_48b_sma & twin",
        "closed": 4,
        "pnl": -20.0,
        "wins": 1,
        "open_lots": 1,
        "champion_since": "2026-08-15T08:00:00Z",
        "btc": 0,
        "eth": 1,
        "openLots": 1,
        "liveLotsKnown": True,
        "lots": [],
    },
    {
        "name": "sma_abv_20",
        "closed": 40,
        "pnl": 15.0,
        "wins": 22,
        "open_lots": 0,
        "champion_since": None,
        "btc": 0,
        "eth": 0,
        "openLots": 0,
        "liveLotsKnown": True,
        "lots": [],
    },
]


def _empty_champ_filters(**overrides) -> dict:
    base = {
        "name": "",
        "since": "",
        "btc": "",
        "eth": "",
        "openLots": "",
        "closed": "",
        "wins": "",
        "pnl": "",
    }
    base.update(overrides)
    return base


def _champ_names(filters: dict, sort: dict | None = None) -> list[str]:
    import json
    import subprocess

    sort = sort or {"key": "pnl", "dir": "desc"}
    script = f"""
import {{ applyChampionRows }} from {json.dumps(FILTERS_TS.as_posix())};
const rows = {json.dumps(SAMPLE_CHAMPS)};
const filters = {json.dumps(filters)};
const sort = {json.dumps(sort)};
const out = applyChampionRows(rows, filters, sort, (iso) => iso || "", (n) => n == null ? "—" : String(n));
console.log(JSON.stringify(out.map((r) => r.name)));
"""
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"champion filter harness failed:\n{proc.stderr or proc.stdout}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


class ChampionFilterLogicTests(unittest.TestCase):
    def test_name_ampersand_and_numeric_filters(self):
        self.assertEqual(
            _champ_names(_empty_champ_filters(name="&")),
            ["mom_48b_sma & twin"],
        )
        self.assertEqual(
            _champ_names(_empty_champ_filters(btc=">=1")),
            ["dip_24b_and_wt_cross"],
        )
        self.assertEqual(
            _champ_names(_empty_champ_filters(eth=">=1")),
            ["dip_24b_and_wt_cross", "mom_48b_sma & twin"],
        )
        self.assertEqual(
            _champ_names(_empty_champ_filters(pnl="<0")),
            ["mom_48b_sma & twin"],
        )

    def test_default_sort_is_highest_pnl(self):
        self.assertEqual(
            _champ_names(_empty_champ_filters()),
            ["dip_24b_and_wt_cross", "sma_abv_20", "mom_48b_sma & twin"],
        )

    def test_path_encodes_ampersand_and_slash(self):
        import json
        import subprocess

        path_ts = REPO / "frontend" / "src" / "status" / "championPath.ts"
        script = f"""
import {{ championDetailPath, decodeChampionName }} from {json.dumps(path_ts.as_posix())};
const names = ["mom_48b_sma & twin", "foo/bar", "a:b"];
console.log(JSON.stringify(names.map((n) => [championDetailPath(n), decodeChampionName(championDetailPath(n).slice("/champions/".length))])));
"""
        proc = subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(f"path harness failed:\n{proc.stderr or proc.stdout}")
        pairs = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(pairs[0][0], "/champions/mom_48b_sma%20%26%20twin")
        self.assertEqual(pairs[0][1], "mom_48b_sma & twin")
        self.assertEqual(pairs[1][0], "/champions/foo%2Fbar")
        self.assertEqual(pairs[1][1], "foo/bar")
        self.assertEqual(pairs[2][1], "a:b")


if __name__ == "__main__":
    unittest.main()
