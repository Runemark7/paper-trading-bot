"""Display-only library backtest fields. No tape scoring and no champion write."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.constants import GATE_RULES
from hedge_fund.trading.discovery import latest_eval_per_strategy, save_discovery_log
from hedge_fund.trading.discovery_results import discovery_results_path
from hedge_fund.web.discovery import _eval_row, discovery_results_response

SMA = "lib.sma200"
RSI = "lib.rsi2"
DIP = "dip_204b_lt8pc&h4_ema_abv_150"
NEW_AT = "2026-10-29T12:00:00+00:00"
OLD_AT = "2026-10-28T12:00:00+00:00"
NEIGHBOURS = "neighbours 0/3 = 0% < 70%"
OOS = 102241.97
HOLD = 536.36
HOLD_TRADES = 5
COST_OOS = 93508.82
COST_HOLD = 379.49
WINDOW_SUM = 80000.0


def _core(name: str, tested_at: str) -> dict:
    return {
        "strategy": name,
        "tested_at": tested_at,
        "qualified": True,
        "sharpe": 0.59,
        "trades": 27,
        "test_pnl": WINDOW_SUM,
        "bh_oos_pnl": 81674.0,
        "sma_stack_oos_pnl": 20000.0,
        "fail_reasons": [],
    }


def _sma(*, tested_at: str, oos: float, warnings: list[str]) -> dict:
    row = _core(SMA, tested_at)
    row.update({
        "warnings": warnings,
        "gate_rules": GATE_RULES,
        "oos_net_pnl": oos,
        "gate_v4": {
            "passed": True,
            "rules": GATE_RULES,
            "oos_pnl": oos,
            "cost_oos_pnl": COST_OOS,
            "cost_holdout_pnl": COST_HOLD,
            "neighbours": 3,
            "neighbours_pass": 0,
        },
        "library": {
            "holdout": {"pnl": HOLD, "trades": HOLD_TRADES, "daily_sharpe": 0.4},
        },
    })
    return row


def _dip() -> dict:
    row = _core(DIP, "2026-10-20T00:00:00+00:00")
    row["qualified"] = False
    row["trades"] = 48
    row["test_pnl"] = 12.0
    row["fail_reasons"] = ["oos_sharpe 0.10 < 0.30"]
    row["gate_rules"] = GATE_RULES
    return row


class LibraryBacktestDisplayTests(unittest.TestCase):
    def test_payloads_use_newest_log_row_and_do_not_score_or_write(self):
        newest = _sma(
            tested_at=NEW_AT,
            oos=OOS,
            warnings=[
                "dsr 0.590 < 0.95 (n_trials=64)",
                "pbo 0.30 >= 0.20",
                NEIGHBOURS,
                "oos_pnl 102241.97 <= buy-and-hold 271534.12",
            ],
        )
        older = _sma(tested_at=OLD_AT, oos=1.0, warnings=["dsr 0.100 < 0.95"])
        dip_eval = _dip()
        sma_champ = {
            "name": SMA,
            "source": "library_qualification",
            "qualified": True,
            "closed": 0,
            "pnl": 40.0,
            "wins": 0,
            "champion_since": "2026-10-29T10:05:00+00:00",
        }
        dip_champ = {
            "name": DIP,
            "source": "5m_qualification_filter",
            "closed": 4,
            "pnl": 12.5,
            "wins": 2,
            "champion_since": "2026-10-09T06:06:03+00:00",
        }
        rsi_champ = {
            "name": RSI,
            "source": "library_qualification",
            "qualified": True,
            "closed": 1,
            "pnl": -3.0,
            "wins": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pool = {
                "champions": [dip_champ, sma_champ, rsi_champ],
                "synced_until": "2026-10-28T00:00:00+00:00",
            }
            (root / "champions.json").write_text(json.dumps(pool))
            before = (root / "champions.json").read_bytes()
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                # Trimmed results line: same scored core, no book P&L objects.
                save_discovery_log([_core(SMA, NEW_AT), dip_eval])
                # Newest full row, then an older one. Core matches, so the
                # results file stays the trimmed line.
                save_discovery_log([newest, older, dip_eval])
                blob = json.loads((root / "champions.json").read_text())
                with (
                    patch("hedge_fund.library.evaluate.evaluate_library_record") as ev,
                    patch("hedge_fund.library.evaluate.library_data_from_tape") as tape,
                    patch("hedge_fund.trading.champions.save_pool") as save,
                    patch("hedge_fund.trading.champions.load_pool", return_value=blob),
                ):
                    from hedge_fund.trading.champions import pool_status

                    payload = pool_status(read_only=True)
                    body = discovery_results_response({"limit": "10"})
                ev.assert_not_called()
                tape.assert_not_called()
                save.assert_not_called()
                self.assertEqual((root / "champions.json").read_bytes(), before)
                for champ in blob["champions"]:
                    self.assertNotIn("library_result", champ)
                results_text = discovery_results_path().read_text()
                latest = {row["strategy"]: row for row in latest_eval_per_strategy()}

        self.assertNotIn("102241.97", results_text)
        stored_lines = {
            row["strategy"]: row
            for row in (json.loads(line) for line in results_text.splitlines() if line.strip())
        }
        self.assertIsNone(stored_lines[SMA]["oos_net_pnl"])
        self.assertNotIn("oos_net_pnl", stored_lines[DIP])

        by_name = {row["name"]: row for row in payload["active_champions"]}
        shown = by_name[SMA]["library_result"]
        self.assertEqual(shown["gate_rules"], GATE_RULES)
        self.assertEqual(shown["tested_at"], NEW_AT)
        self.assertEqual(shown["oos_net_pnl"], OOS)
        self.assertEqual(shown["trades"], 27)
        self.assertEqual(shown["holdout_pnl"], HOLD)
        self.assertEqual(shown["holdout_trades"], HOLD_TRADES)
        self.assertEqual(shown["cost_oos_pnl"], COST_OOS)
        self.assertEqual(shown["cost_holdout_pnl"], COST_HOLD)
        self.assertIn(NEIGHBOURS, shown["warnings"])
        self.assertNotEqual(shown["oos_net_pnl"], 1.0)
        self.assertEqual(by_name[SMA]["pnl"], 40.0)
        self.assertEqual(by_name[SMA]["closed"], 0)
        self.assertNotIn("library_result", by_name[DIP])
        self.assertEqual(by_name[DIP]["pnl"], 12.5)
        self.assertEqual(by_name[DIP]["closed"], 4)
        self.assertIsNone(by_name[RSI]["library_result"])

        eval_row = _eval_row(latest[SMA])
        self.assertEqual(eval_row["oos_net_pnl"], OOS)
        self.assertEqual(eval_row["holdout_pnl"], HOLD)
        self.assertEqual(eval_row["holdout_trades"], HOLD_TRADES)
        self.assertEqual(eval_row["cost_oos_pnl"], COST_OOS)
        self.assertEqual(eval_row["cost_holdout_pnl"], COST_HOLD)
        self.assertEqual(eval_row["test_pnl"], WINDOW_SUM)
        dip_row = _eval_row(latest[DIP])
        self.assertNotIn("oos_net_pnl", dip_row)
        self.assertNotIn("holdout_pnl", dip_row)

        api_rows = {row["strategy"]: row for row in body["results"]}
        self.assertEqual(api_rows[SMA]["oos_net_pnl"], OOS)
        self.assertEqual(api_rows[SMA]["holdout_pnl"], HOLD)
        self.assertEqual(api_rows[SMA]["holdout_trades"], HOLD_TRADES)
        self.assertEqual(api_rows[SMA]["cost_oos_pnl"], COST_OOS)
        self.assertEqual(api_rows[SMA]["cost_holdout_pnl"], COST_HOLD)
        self.assertNotIn("oos_net_pnl", api_rows[DIP])
        self.assertNotIn("holdout_pnl", api_rows[DIP])
        self.assertNotIn("cost_oos_pnl", api_rows[DIP])


if __name__ == "__main__":
    unittest.main()
