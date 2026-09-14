"""Full strategy names stay readable — wrap, title, copy; never hash or slice."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UI = (REPO / "frontend" / "src" / "components" / "ui.tsx").read_text()
CHAMPS = (REPO / "frontend" / "src" / "pages" / "Champions.tsx").read_text()
DETAIL = (REPO / "frontend" / "src" / "pages" / "ChampionDetail.tsx").read_text()
DISCOVERY = (REPO / "frontend" / "src" / "status" / "DiscoveryBuckets.tsx").read_text()
CHART = (REPO / "frontend" / "src" / "pages" / "Chart.tsx").read_text()
OVERVIEW = (REPO / "frontend" / "src" / "pages" / "Overview.tsx").read_text()
POSITIONS = (REPO / "frontend" / "src" / "pages" / "Positions.tsx").read_text()
FRONTEND_SRC = list((REPO / "frontend" / "src").rglob("*.ts")) + list(
    (REPO / "frontend" / "src").rglob("*.tsx")
)


class FullStrategyNameUiTests(unittest.TestCase):
    def test_mononame_wraps_and_keeps_title_not_ellipsis(self):
        start = UI.index("export function MonoName")
        end = UI.index("export function NameChip")
        body = UI[start:end]
        self.assertIn("title={title}", body)
        self.assertIn("break-all", body)
        self.assertIn("[overflow-wrap:anywhere]", body)
        self.assertIn("whitespace-normal", body)
        self.assertIn("select-text", body)
        self.assertNotIn("truncate", body)
        self.assertNotIn("line-clamp", body)
        self.assertNotIn("overflow-hidden", body)
        self.assertNotIn("ellipsis", body)

    def test_namechip_and_copyable_expose_the_full_string(self):
        self.assertIn("export function NameChip", UI)
        self.assertIn("title={name}", UI)
        self.assertIn("export function CopyableName", UI)
        self.assertIn("navigator.clipboard.writeText(name)", UI)
        self.assertIn('aria-label={`Copy strategy name ${name}`}', UI)
        self.assertIn("select-all", UI)

    def test_champion_list_wraps_name_and_detail_is_copyable(self):
        list_body = CHAMPS.split("Graduated paper")[0]
        self.assertIn('MonoName className="block w-full', list_body)
        self.assertIn("{c.name}", list_body)
        self.assertIn("championDetailPath(c.name)", list_body)
        self.assertNotIn("slice(0, 40)", list_body)
        self.assertIn("CopyableName", DETAIL)
        self.assertIn("<CopyableName name={name}", DETAIL)
        self.assertNotIn("slice(0, 40)", DETAIL)

    def test_discovery_tested_and_queued_keep_full_names(self):
        self.assertIn("NameChip", DISCOVERY)
        self.assertIn("<NameChip key={name} name={name}", DISCOVERY)
        self.assertIn('MonoName className="block w-full text-xs font-medium text-white">{d.strategy ?? "—"}', DISCOVERY)
        self.assertIn("title={d.strategy ?? undefined}", DISCOVERY)
        self.assertNotIn("slice(0, 40)", DISCOVERY)

    def test_chart_picker_shows_full_name_outside_the_select(self):
        self.assertIn("title={champion || undefined}", CHART)
        self.assertIn("<MonoName className=\"block text-xs text-white/70\">{champion}</MonoName>", CHART)
        self.assertIn("championDetailPath(champion)", CHART)
        self.assertNotIn("title={champion ? `${champion} · 5m`", CHART)

    def test_overview_and_positions_account_cells_use_mononame(self):
        self.assertIn("<MonoName className=\"block text-sm\">{n}</MonoName>", OVERVIEW)
        self.assertIn("championDetailPath(n)", OVERVIEW)
        self.assertIn("<MonoName className=\"text-xs\">{accountLabel(p.account)}</MonoName>", OVERVIEW)
        self.assertIn("<MonoName className=\"text-xs\">{accountLabel(lot.account)}</MonoName>", POSITIONS)

    def test_frontend_does_not_hash_or_slice_strategy_names(self):
        slice_name = re.compile(r"\.(name|strategy)\s*\.slice\s*\(")
        for path in FRONTEND_SRC:
            text = path.read_text()
            self.assertIsNone(slice_name.search(text), path)
            self.assertNotIn("digest().hexdigest()", text)
            rel = path.relative_to(REPO)
            if rel.as_posix() == "frontend/src/status/format.ts":
                continue
            self.assertNotIn("sha256", text.lower(), path)


if __name__ == "__main__":
    unittest.main()
