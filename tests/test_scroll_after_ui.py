"""Part 1 library table and Part 2 champion list scroll after five rows."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCROLL_TS = REPO / "frontend" / "src" / "components" / "scrollAfter.ts"
SCROLL_TSX = (REPO / "frontend" / "src" / "components" / "ScrollAfter.tsx").read_text()
OVERVIEW = (REPO / "frontend" / "src" / "pages" / "Overview.tsx").read_text()
LIBRARY = (REPO / "frontend" / "src" / "status" / "LibraryTable.tsx").read_text()


def _scroll_mod(*exprs: str) -> list:
    body = (
        "import { VISIBLE_ROWS, scrollportHeight, itemsBelowFold, listNeedsScroll, hiddenBelow } "
        f"from {json.dumps(SCROLL_TS.as_posix())};\n"
        "const out = [];\n"
        + "".join(f"out.push({expr});\n" for expr in exprs)
        + "console.log(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", body],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"scroll harness failed:\n{proc.stderr or proc.stdout}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


class ScrollAfterMathTests(unittest.TestCase):
    def test_window_height_keeps_the_fifth_row_and_the_scrollbar(self):
        visible, height, scrolled, with_bar, five, six, below, hidden_open, hidden_fold = _scroll_mod(
            "VISIBLE_ROWS",
            "scrollportHeight(500, 100, 0)",
            "scrollportHeight(480.2, 100, 40)",
            "scrollportHeight(500, 100, 0, 15)",
            "listNeedsScroll(5)",
            "listNeedsScroll(6)",
            "itemsBelowFold([80, 160, 240.4], 160)",
            "hiddenBelow(28, [10, 20, 30], 999, true)",
            "hiddenBelow(28, [10, 80, 200], 100, false)",
        )
        self.assertEqual(visible, 5)
        self.assertEqual(height, 400)
        self.assertEqual(scrolled, 421)
        self.assertEqual(with_bar, 415)
        self.assertIs(five, False)
        self.assertIs(six, True)
        self.assertEqual(below, 1)
        self.assertEqual(hidden_open, 23)
        self.assertEqual(hidden_fold, 1)


class ScrollAfterUiContractTests(unittest.TestCase):
    def test_scroll_region_is_focusable_and_hints(self):
        self.assertIn("export const VISIBLE_ROWS = 5", SCROLL_TS.read_text())
        self.assertIn('export const SCROLL_ITEM_ATTR = "data-scroll-item"', SCROLL_TS.read_text())
        self.assertIn("overflow-y-auto", SCROLL_TSX)
        self.assertIn("overflow-x-auto", SCROLL_TSX)
        self.assertIn("tabIndex={scrolling ? 0 : undefined}", SCROLL_TSX)
        self.assertIn('role={scrolling ? "region" : undefined}', SCROLL_TSX)
        self.assertIn("aria-label={", SCROLL_TSX)
        self.assertIn("more, scroll", SCROLL_TSX)
        self.assertIn("from-black/45", SCROLL_TSX)
        self.assertIn("ArrowDown", SCROLL_TSX)
        self.assertIn("maxHeight: chrome.maxHeight", SCROLL_TSX)

    def test_forward_test_list_scrolls_inside_the_section(self):
        forward = OVERVIEW.split("The forward test", 1)[1].split("How a strategy becomes", 1)[0]
        self.assertIn('<ScrollAfter className="mt-4" label="Admitted champions" itemCount={active.length}>', forward)
        self.assertIn("<li key={c.name} data-scroll-item>", forward)
        self.assertLess(forward.index("</ScrollAfter>"), forward.index("See champions"))
        self.assertNotIn("<ScrollAfter", OVERVIEW.split("The forward test", 1)[0])
        intro = forward.split("<ScrollAfter", 1)[0]
        self.assertIn("Part 2 · Champions", OVERVIEW)
        self.assertIn("$10,000 paper account", intro)

    def test_library_table_scrolls_with_a_sticky_header(self):
        self.assertLess(LIBRARY.index("<SectionTitle"), LIBRARY.index("<ScrollAfter"))
        self.assertLess(LIBRARY.index("<ScrollAfter"), LIBRARY.index("<table"))
        self.assertLess(LIBRARY.index("</ScrollAfter>"), LIBRARY.index("{data.note}"))
        self.assertIn('label="Strategy library"', LIBRARY)
        self.assertIn("itemCount={data.strategies.length}", LIBRARY)
        self.assertIn("<tr key={row.name} data-scroll-item", LIBRARY)
        self.assertIn("sticky top-0 z-10 bg-[#141b30]", LIBRARY)
        self.assertIn("border-separate border-spacing-0", LIBRARY)
        self.assertNotIn("overflow-x-auto", LIBRARY.split("<ScrollAfter", 1)[0])
