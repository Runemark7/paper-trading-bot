"""Qualified paper admits of four library batch-2 names under gate v7b.

Alexander, 2026-10-30 09:59 CET: seat lib.ichimoku, lib.rsi14,
lib.tsmom180d and lib.rsi2_10 as paper forward-test champions. Same
qualified path as batch ``lib-rsi2-sma200-v7-20261029``. No champion cap.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from hedge_fund.library.daily import DailyBars
from hedge_fund.library.engine import ENGINE_ID
from hedge_fund.trading.constants import GATE_RULES

from tests.test_gate_v7b_admit import A, B, RSI, SMA100, SMA200, _env, _record, _seed

BATCH1 = "lib-rsi2-sma200-v7-20261029"
BATCH2 = "lib-batch2-four-v7b-20261030"
NEW = ["lib.ichimoku", "lib.rsi14", "lib.tsmom180d", "lib.rsi2_10"]


def _seed_seated(root: Path) -> None:
    """Prod state on 2026-10-30: batch 1 applied, rsi2 and sma200 seated."""
    _seed(root)
    pool = json.loads((root / "champions.json").read_text())
    for name in (RSI, SMA200):
        pool["champions"].append({"name": name, "source": "library_qualification", "qualified": True, "engine": ENGINE_ID})
    (root / "champions.json").write_text(json.dumps(pool))
    (root / "approved_admits.json").write_text(json.dumps({
        "applied": ["rules-v3-admit-20261009", "lib-sma100-forward-test-20261029", BATCH1],
        "batches": [],
        "paper_only": True,
    }))


class Batch2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _seed_seated(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, records):
        from hedge_fund.trading.approved_admit import run_approved_admits

        def evaluate(name, _data, tested_at=None):
            row = dict(records[name])
            if tested_at:
                row["tested_at"] = tested_at
            return row

        bars = DailyBars(
            ts=np.array([0], dtype=np.int64),
            open=np.array([1.0]), high=np.array([1.0]), low=np.array([1.0]), close=np.array([1.0]),
        )
        with _env(self.root), patch("hedge_fund.library.tape.load_library_daily", return_value=bars), patch(
            "hedge_fund.library.evaluate.build_library_data", return_value=object()
        ), patch("hedge_fund.library.evaluate.evaluate_library_record", side_effect=evaluate) as ev:
            out = run_approved_admits()
        return out, ev

    def test_spec(self):
        from hedge_fund.trading.approved_admit import (
            LIBRARY_APPROVED_ADMITS_2,
            LIBRARY_APPROVED_BATCH_2,
            LIBRARY_APPROVED_BATCHES,
        )

        self.assertEqual(LIBRARY_APPROVED_BATCH_2, BATCH2)
        self.assertEqual([r["name"] for r in LIBRARY_APPROVED_ADMITS_2], NEW)
        self.assertEqual([b for b, _ in LIBRARY_APPROVED_BATCHES], [BATCH1, BATCH2])
        for r in LIBRARY_APPROVED_ADMITS_2:
            self.assertEqual(r["approved_by"], "Alexander")
            self.assertEqual(r["approved_at"], "2026-10-30T09:59+01:00")

    def test_four_seat_once_and_nothing_else_moves(self):
        before = json.loads((self.root / "champions.json").read_text())
        before_log = (self.root / "discovery_log.json").read_bytes()
        records = {n: _record(n, warnings=[f"dsr low {n}"]) for n in NEW}
        first, ev1 = self._run(records)
        second, ev2 = self._run(records)
        self.assertEqual(first["batch_id"], BATCH2)
        self.assertEqual(first["admitted"], NEW)
        self.assertIsNone(second)
        self.assertEqual(ev1.call_count, 4)
        self.assertEqual(ev2.call_count, 0)
        pool = json.loads((self.root / "champions.json").read_text())
        names = [c["name"] for c in pool["champions"]]
        self.assertEqual(names, [A, B, SMA100, RSI, SMA200, *NEW])
        self.assertEqual(pool["champions"][:5], before["champions"])
        self.assertEqual((self.root / "discovery_log.json").read_bytes(), before_log)
        for row in pool["champions"][5:]:
            self.assertIs(row["qualified"], True)
            self.assertEqual(row["source"], "library_qualification")
            self.assertEqual(row["provenance"]["admit_batch"], BATCH2)
            self.assertEqual(row["provenance"]["approved_by"], "Alexander")
            self.assertEqual(row["provenance"]["gate_rules"], GATE_RULES)
            self.assertEqual(row["warnings"], [f"dsr low {row['name']}"])
        marker = json.loads((self.root / "approved_admits.json").read_text())
        self.assertEqual(marker["applied"].count(BATCH2), 1)
        self.assertEqual(marker["applied"].count(BATCH1), 1)

    def test_one_unqualified_name_seats_nobody_and_retries(self):
        before = (self.root / "champions.json").read_bytes()
        before_marker = (self.root / "approved_admits.json").read_bytes()
        records = {n: _record(n) for n in NEW}
        records["lib.rsi2_10"] = _record("lib.rsi2_10", qualified=False)
        out, _ = self._run(records)
        self.assertIsNone(out)
        self.assertEqual((self.root / "champions.json").read_bytes(), before)
        self.assertEqual((self.root / "approved_admits.json").read_bytes(), before_marker)


if __name__ == "__main__":
    unittest.main()
