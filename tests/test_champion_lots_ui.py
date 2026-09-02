"""Champions accordion: live lots under the tapped card, joined like open_lots.py."""
from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FORMAT_TS = (REPO / "frontend" / "src" / "status" / "format.ts").read_text()
CHAMPS_TSX = (REPO / "frontend" / "src" / "pages" / "Champions.tsx").read_text()
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
        self.assertIn("name.replace(/[/:]/g, \"_\")", FORMAT_TS)
        self.assertIn("function paperAccountKeys", FORMAT_TS)
        self.assertIn("function accountsMatch", FORMAT_TS)
        self.assertIn("function lotsForChampion", FORMAT_TS)
        self.assertIn("function openLotsByPair", FORMAT_TS)
        self.assertIn("function pairOf", FORMAT_TS)
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


class ChampionsAccordionUiTests(unittest.TestCase):
    def test_joins_live_rows_behind_an_accordion_not_always_open(self):
        self.assertIn('queryKey: ["live"]', CHAMPS_TSX)
        self.assertIn("api.live", CHAMPS_TSX)
        self.assertIn("lotsForChampion", CHAMPS_TSX)
        self.assertIn("expandedChampion", CHAMPS_TSX)
        self.assertIn("setExpandedChampion(isOpen ? null : c.name)", CHAMPS_TSX)
        self.assertIn("{isOpen &&", CHAMPS_TSX)
        self.assertIn("aria-expanded", CHAMPS_TSX)
        self.assertIn("min-h-11", CHAMPS_TSX)
        self.assertIn("no open lots", CHAMPS_TSX)
        self.assertIn('label="Condition" span', CHAMPS_TSX)
        self.assertIn("accountsMatch(name, c.name)", CHAMPS_TSX)
        self.assertIn("ChampionTape", CHAMPS_TSX)
        self.assertIn("championName={c.name}", CHAMPS_TSX)

    def test_collapsed_card_shows_btc_eth_lot_chips(self):
        collapsed = CHAMPS_TSX.split("{isOpen &&")[0]
        self.assertIn("openLotsByPair", collapsed)
        self.assertIn("PairLotChips", collapsed)
        self.assertIn("flex flex-wrap", collapsed)
        self.assertIn("shrink-0", collapsed)
        self.assertIn("BTC {btc}", collapsed)
        self.assertIn("ETH {eth}", collapsed)
        self.assertIn("split.btc", collapsed)
        self.assertIn("split.eth", collapsed)
        self.assertIn("split.total", collapsed)
        self.assertNotIn("open_lots_btc", collapsed)
        self.assertNotIn("open_lots_eth", collapsed)

    def test_graduated_section_does_not_invent_live_lots(self):
        grad = CHAMPS_TSX[CHAMPS_TSX.index("Graduated paper") :]
        self.assertNotIn("lotsForChampion", grad)
        self.assertNotIn("ChampionLotList", grad)
        self.assertNotIn("qLive", grad)
        self.assertNotIn("ExpandedChampionLots", grad)
        self.assertNotIn("ChampionTape", grad)
        self.assertNotIn("PairLotChips", grad)
        self.assertNotIn("openLotsByPair", grad)


if __name__ == "__main__":
    unittest.main()
