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
        self.assertIn("`trades_${slug}`", FORMAT_TS)
        self.assertIn("`trades_${strippedSlug}`", FORMAT_TS)


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

    def test_graduated_section_does_not_invent_live_lots(self):
        grad = CHAMPS_TSX[CHAMPS_TSX.index("Graduated paper") :]
        self.assertNotIn("lotsForChampion", grad)
        self.assertNotIn("ChampionLotList", grad)
        self.assertNotIn("qLive", grad)
        self.assertNotIn("ExpandedChampionLots", grad)


if __name__ == "__main__":
    unittest.main()
