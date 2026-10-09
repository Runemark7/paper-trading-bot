"""One champion per near-identical family; twins parked, never deleted.

Alexander 2026-10-09 08:04. Family = same template (atoms with numbers
as N, h1/h4 kept), equal thresholds, lookbacks/periods within 20% of the
winner. Best = OOS daily Sharpe on the current GATE_RULES, then gate
Sharpe, then P&L, then the earlier admit.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.constants import (
    GATE_RULES,
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    QUAL_N_WINDOWS,
)
from hedge_fund.trading.store import TradeStore

NOW = datetime(2026, 10, 9, 6, 30, tzinfo=timezone.utc)
A130 = "dip_204b_lt8pc&h4_ema_abv_130"
A140 = "dip_204b_lt8pc&h4_ema_abv_140"
A150 = "dip_204b_lt8pc&h4_ema_abv_150"
B150 = "dip_222b_lt8pc&h4_ema_abv_150"
B160 = "dip_222b_lt8pc&h4_ema_abv_160"
OTHER = "near_swing_lo_54&sma_stack_20_50_100"


def _m(dsr: float, sharpe: float = 0.4, pnl: float = 1500.0, rules: str = GATE_RULES) -> dict:
    return {
        "gate_rules": rules,
        "daily_sharpe": dsr,
        "bh_daily_sharpe": 0.407,
        "sharpe": sharpe,
        "test_pnl": pnl,
        "trades": 100,
    }


def _champ(name: str, metrics: dict | None, admitted: str = "2026-10-09T04:00:00+00:00", **extra) -> dict:
    row = {
        "name": name,
        "closed": 0,
        "pnl": 0.0,
        "wins": 0,
        "admitted_at": admitted,
        "champion_since": admitted,
        "source": "5m_qualification_filter",
    }
    if metrics is not None:
        row["qual"] = metrics
    row.update(extra)
    return row


def _seed(root: Path, champions: list[dict], retired: dict | None = None) -> None:
    (root / "champions.json").write_text(json.dumps({"champions": champions, "synced_until": ""}))
    (root / "graduated.json").write_text(json.dumps([]))
    (root / "retired.json").write_text(json.dumps({"retired": retired or {}, "applied": [], "batches": []}))


def _eval(name: str, dsr: float, sharpe: float = 0.4, pnl: float = 1500.0) -> dict:
    return {
        "strategy": name,
        "qualified": True,
        "tested_at": "2026-10-09T06:20:00+00:00",
        "fail_reasons": [],
        "sharpe": sharpe,
        "trades": 100,
        "test_pnl": pnl,
        "daily_sharpe": dsr,
        "bh_daily_sharpe": 0.407,
        "gate_rules": GATE_RULES,
        "regimes_tested": 23,
    }


class _StateCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = patch.dict(os.environ, {"PAPER_STATE": str(self.root)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def pool(self) -> list[str]:
        return [c["name"] for c in json.loads((self.root / "champions.json").read_text())["champions"]]

    def retired(self) -> dict:
        return json.loads((self.root / "retired.json").read_text())["retired"]


class SignatureTests(unittest.TestCase):
    def test_gate_is_frozen(self):
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(QUAL_N_WINDOWS, 23)

    def test_same_family(self):
        from hedge_fund.trading.families import family_signature, same_family

        self.assertEqual(family_signature(A140)[0], "dip_Nb_ltNpc&h4_ema_abv_N")
        for a, b in ((A130, A150), (A140, B160), (A150, B150), (A140, "dip_234b_lt8pc&h4_ema_abv_140")):
            self.assertTrue(same_family(a, b), msg=(a, b))
        # threshold differs, timeframe differs, ema vs sma, period too far, other template
        for a, b in (
            (A140, "dip_204b_lt10pc&h4_ema_abv_140"),
            (A140, "dip_204b_lt8pc&h1_ema_abv_140"),
            (A140, "dip_204b_lt8pc&h4_sma_abv_140"),
            ("dip_204b_lt8pc&h4_ema_abv_120", "dip_204b_lt8pc&h4_ema_abv_180"),
            ("dip_204b_lt8pc&h4_ema_abv_150", "dip_456b_lt8pc&h4_ema_abv_150"),
            (A140, OTHER),
        ):
            self.assertFalse(same_family(a, b), msg=(a, b))
        # Atom order does not matter.
        self.assertTrue(same_family(A140, "h4_ema_abv_140&dip_204b_lt8pc"))


class EnforceTests(_StateCase):
    def _enforce(self, **kw):
        from hedge_fund.trading.families import enforce_champion_families_unlocked

        kw.setdefault("open_lots", lambda name: 0)
        return enforce_champion_families_unlocked(NOW, **kw)

    def test_best_daily_sharpe_wins_twins_parked_with_pointer(self):
        _seed(self.root, [
            _champ(B150, _m(0.500, 0.42, 1790.53), "2026-10-09T03:43:40+00:00"),
            _champ(B160, _m(0.500, 0.42, 1790.53), "2026-10-09T04:03:30+00:00"),
            _champ(A150, None, "2026-10-09T05:50:53+00:00", source="requalify_approved_admit",
                   provenance=dict(_m(0.504, 0.40, 1765.7), approved_by="Alexander")),
            _champ(A130, _m(0.480, 0.40, 1635.21)),
            _champ(A140, _m(0.561, 0.41, 1925.85)),
            _champ(OTHER, _m(0.609, -0.07, 2741)),
        ])
        out = self._enforce()
        self.assertEqual(sorted(self.pool()), sorted([A140, OTHER]))
        self.assertEqual(set(out["parked"]), {A130, A150, B150, B160})
        retired = self.retired()
        for twin in (A130, A150, B150, B160):
            row = retired[twin]
            self.assertEqual(row["role"], "parked_twin")
            self.assertEqual(row["family_winner"], A140)
            self.assertIn(A140, row["reason"])
            self.assertEqual(row["record"]["name"], twin)  # archived, not deleted
        self.assertEqual(retired[A150]["record"]["provenance"]["approved_by"], "Alexander")
        # Idempotent.
        self.assertIsNone(self._enforce())

    def test_tie_breaks_gate_sharpe_then_pnl_then_earlier_admit(self):
        from hedge_fund.trading.families import plan_families

        rows = [
            _champ(B160, _m(0.5, 0.42, 1790.53), "2026-10-09T04:03:30+00:00"),
            _champ(B150, _m(0.5, 0.42, 1790.53), "2026-10-09T03:43:40+00:00"),
        ]
        winners, twins, _u, _m2 = plan_families(rows, lambda n: None)
        self.assertEqual(winners, [B150])
        rows = [_champ(A130, _m(0.5, 0.45, 100.0)), _champ(A140, _m(0.5, 0.41, 9000.0))]
        self.assertEqual(plan_families(rows, lambda n: None)[0], [A130])
        rows = [_champ(A130, _m(0.5, 0.41, 100.0)), _champ(A140, _m(0.5, 0.41, 9000.0))]
        self.assertEqual(plan_families(rows, lambda n: None)[0], [A140])

    def test_old_rules_or_missing_metrics_never_park(self):
        _seed(self.root, [
            _champ(A140, _m(0.9, rules="old_rules")),
            _champ(A150, None),
            _champ(A130, _m(0.48)),
        ])
        self.assertIsNone(self._enforce(lookup=lambda n: None))
        self.assertEqual(len(self.pool()), 3)

    def test_results_lookup_fills_rows_without_qual(self):
        _seed(self.root, [_champ(A130, None), _champ(A140, None)])
        lookup = {A130: _m(0.48), A140: _m(0.561)}
        out = self._enforce(lookup=lookup.get)
        self.assertEqual(out["parked"], {A130: A140})

    def test_twin_with_open_lot_is_deferred(self):
        _seed(self.root, [_champ(A130, _m(0.48)), _champ(A140, _m(0.561))])
        out = self._enforce(open_lots=lambda n: 1 if n == A130 else 0)
        self.assertEqual(out["deferred"], {A130: A140})
        self.assertEqual(sorted(self.pool()), sorted([A130, A140]))
        self.assertNotIn(A130, self.retired())
        out = self._enforce()
        self.assertEqual(out["parked"], {A130: A140})

    def test_open_lot_reader_counts_lots(self):
        from hedge_fund.trading.families import _open_lots

        TradeStore(self.root / "trades_x.sqlite").save_account_state({"broker": {"cash": 1.0, "lots": [{"q": 1}]}})
        self.assertEqual(_open_lots("x"), 1)
        self.assertEqual(_open_lots("missing"), 0)


class IngestTests(_StateCase):
    def _ingest(self, *evals):
        from hedge_fund.trading.ingest import ingest_discovery_payload

        with patch("hedge_fund.trading.families._open_lots", lambda n: 0):
            return ingest_discovery_payload({"evaluations": list(evals)})

    def test_new_worse_pass_in_family_is_parked(self):
        _seed(self.root, [_champ(A140, _m(0.561))])
        out = self._ingest(_eval(A130, 0.48))
        self.assertEqual(out["admitted"], [A130])
        self.assertEqual(out["family_parked"], {A130: A140})
        self.assertEqual(self.pool(), [A140])
        self.assertEqual(self.retired()[A130]["family_winner"], A140)

    def test_new_better_pass_replaces_winner_and_repoints_twins(self):
        better = "dip_234b_lt8pc&h4_ema_abv_140"
        _seed(self.root, [_champ(A140, _m(0.561))], retired={
            A130: {"name": A130, "role": "parked_twin", "family_winner": A140, "reason": "x", "record": {}},
        })
        out = self._ingest(_eval(better, 0.842, sharpe=0.30, pnl=2905.26))
        self.assertEqual(out["family_parked"], {A140: better})
        self.assertEqual(self.pool(), [better])
        retired = self.retired()
        self.assertEqual(retired[A140]["family_winner"], better)
        self.assertEqual(retired[A130]["family_winner"], better)
        # Stored ranking fields on the new champion row.
        row = json.loads((self.root / "champions.json").read_text())["champions"][0]
        self.assertEqual(row["qual"]["daily_sharpe"], 0.842)

    def test_unrelated_pass_is_untouched(self):
        _seed(self.root, [_champ(A140, _m(0.561))])
        out = self._ingest(_eval(OTHER, 0.609, sharpe=0.31))
        self.assertEqual(out["family_parked"], {})
        self.assertEqual(sorted(self.pool()), sorted([A140, OTHER]))

    def test_parked_twin_blocked_while_winner_active(self):
        from hedge_fund.trading.families import admit_blocked_names

        _seed(self.root, [_champ(A140, _m(0.561))], retired={
            A130: {"name": A130, "role": "parked_twin", "family_winner": A140, "reason": "x", "record": {}},
        })
        self.assertIn(A130, admit_blocked_names())
        out = self._ingest(_eval(A130, 0.48))
        self.assertEqual(out["admitted"], [])
        self.assertEqual(out["skipped"][0]["reason"], "already_pooled_or_graduated")

    def test_parked_twin_reconsiderable_after_winner_retired(self):
        from hedge_fund.trading.families import admit_blocked_names, reconsiderable_twins

        _seed(self.root, [], retired={
            A140: {"name": A140, "role": "champion", "reason": "failed requalify", "record": {}},
            A130: {"name": A130, "role": "parked_twin", "family_winner": A140, "reason": "x", "record": {}},
            A150: {"name": A150, "role": "parked_twin", "family_winner": B150, "reason": "x", "record": {}},
            B150: {"name": B150, "role": "parked_twin", "family_winner": A140, "reason": "x", "record": {}},
        })
        self.assertEqual(reconsiderable_twins(), {A130, B150})
        blocked = admit_blocked_names()
        self.assertIn(A140, blocked)
        self.assertIn(A150, blocked)  # its winner is itself parked, not retired
        self.assertNotIn(A130, blocked)
        # An explicit force_admit of a reconsiderable twin seats it and unparks it.
        (self.root / "discovery_log.json").write_text(json.dumps([_eval(A130, 0.48)]))
        from hedge_fund.trading.ingest import ingest_discovery_payload

        with patch("hedge_fund.trading.families._open_lots", lambda n: 0):
            out = ingest_discovery_payload({"evaluations": [], "force_admit": [A130]})
        self.assertEqual(out["force_admitted"], [A130])
        self.assertIn(A130, self.pool())
        self.assertNotIn(A130, self.retired())


class PayloadTests(_StateCase):
    def test_pool_status_lists_parked(self):
        from hedge_fund.trading.champions import pool_status
        from hedge_fund.trading.retire import retired_payload

        _seed(self.root, [_champ(A140, _m(0.561))], retired={
            A130: {"name": A130, "role": "parked_twin", "family_winner": A140,
                   "reason": f"near-identical family twin of {A140}", "retired_at": "t",
                   "metrics": _m(0.48), "record": {}},
            "old": {"name": "old", "role": "champion", "reason": "failed", "record": {}},
        })
        out = pool_status(read_only=True)
        self.assertEqual(out["active_count"], 1)
        self.assertEqual(out["parked_count"], 1)
        self.assertEqual(out["parked"][0]["family_winner"], A140)
        self.assertEqual(out["parked"][0]["daily_sharpe"], 0.48)
        self.assertEqual(out["retired_count"], 2)
        rows = {r["name"]: r for r in retired_payload()["retired"]}
        self.assertEqual(rows[A130]["family_winner"], A140)

    def test_startup_runs_enforcement_after_approved_admits(self):
        from hedge_fund.trading import approved_admit

        _seed(self.root, [_champ(A130, _m(0.48)), _champ(A140, _m(0.561))])
        with patch.object(approved_admit, "ensure_approved_admits_unlocked", lambda now: None), \
                patch("hedge_fund.trading.families._open_lots", lambda n: 0):
            approved_admit.run_approved_admits()
        self.assertEqual(self.pool(), [A140])
        self.assertEqual(self.retired()[A130]["role"], "parked_twin")


if __name__ == "__main__":
    unittest.main()
