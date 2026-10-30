"""Gate v6 discovery serves the strategy library and nothing else.

``PAPER_DISCOVERY_SOURCE`` is unset inside these tests. The suite default
(``random``) is what the pre-v6 claim tests rely on; this file checks the
production default.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from hedge_fund.library.catalog import claim_names, is_library_name, library_trial_count
from hedge_fund.library.engine import ENGINE_ID, RISK_POLICY, TIMEFRAME
from hedge_fund.library.evaluate import library_pass_attestation
from hedge_fund.trading.constants import GATE_RULES, V5_GATE_RULES


def _library_env(root: Path) -> dict[str, str]:
    env = {
        "PAPER_STATE": str(root),
        "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
        # Recorded so patch.dict restores the suite default after we delete it.
        "PAPER_DISCOVERY_SOURCE": "random",
    }
    return env


class _LibraryMode(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = patch.dict(os.environ, _library_env(self.root))
        self._env.start()
        os.environ.pop("PAPER_DISCOVERY_SOURCE", None)
        self._sched = patch("hedge_fund.trading.leases.schedule_refill_buffer")
        self._sched.start()

    def tearDown(self):
        self._sched.stop()
        self._env.stop()
        self._tmp.cleanup()


def _row(
    name: str = "lib.sma200",
    *,
    qualified: bool = False,
    n_trials: int | None = None,
    timeframe: str = TIMEFRAME,
    engine: str = ENGINE_ID,
    risk_policy: str = RISK_POLICY,
    gate_rules: str = GATE_RULES,
    gate_v4: dict | None = None,
) -> dict:
    trials = library_trial_count() if n_trials is None else n_trials
    if gate_v4 is None:
        gate_v4 = (
            library_pass_attestation(n_trials=trials)
            if qualified
            else {"passed": False, "rules": GATE_RULES, "n_trials": trials}
        )
    return {
        "strategy": name,
        "tested_at": "2026-10-28T18:00:00+00:00",
        "qualified": qualified,
        "sharpe": 0.71 if qualified else 0.40,
        "daily_sharpe": 0.71 if qualified else 0.40,
        "trades": 40 if qualified else 12,
        "test_pnl": 100.0,
        "train_pnl": None,
        "win_rate_pct": 55.0,
        "fail_reasons": [] if qualified else ["oos_trades 12 < 30"],
        "timeframe": timeframe,
        "risk_policy": risk_policy,
        "engine": engine,
        "gate_rules": gate_rules,
        "gate_v4": gate_v4,
        "symbols": ["BTC/USDT"],
        "library": {
            "key": "sma200",
            "title": "Price vs 200-day SMA",
            "params": {"short": 1, "long": 200},
            "citation": "Brock, Lakonishok & LeBaron (1992)",
            "safety_stop_pct": None,
            "checks": {"oos_trades": {"passed": qualified}},
            "failing_checks": [] if qualified else ["oos_trades"],
            "count_only": not qualified,
            "discovery": {"daily_sharpe": 0.71, "trades": 20, "exposure": 0.62},
            "holdout": {"daily_sharpe": 0.36, "trades": 5, "pnl": 120.0, "bh": {"daily_sharpe": 0.57}},
            "bh": {"daily_sharpe": 0.57, "pnl": 17180.0, "max_drawdown": 0.77},
            "dsr": {"required_annual_sharpe": 1.944, "days_needed": 4000},
        },
    }


class LibraryClaimTests(_LibraryMode):
    def test_default_source_is_library_and_claims_only_canonical_names(self):
        from hedge_fund.trading.discovery_mode import discovery_source, library_mode
        from hedge_fund.trading.leases import claim_discovery_batch, load_lease_state

        self.assertEqual(discovery_source(), "library")
        self.assertTrue(library_mode())
        from hedge_fund.trading.leases import LEASES_PER_LANE

        now = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)
        # The lane cap is still 2 names per core. A larger catalog is
        # claimed in that order, across workers, and is not minted.
        width = 8
        cap = LEASES_PER_LANE * width
        body = claim_discovery_batch("zealous-lion", 64, parallel=width, now=now, gate_rules=GATE_RULES, require_gate_rules=True)
        names = claim_names()
        self.assertGreater(len(names), 15)
        self.assertEqual(body["names"], names[:cap])
        self.assertEqual(body["source"], "library")
        self.assertEqual(body["library"], names[:cap])
        self.assertEqual(body["library_trials"], library_trial_count())
        self.assertEqual(body["refilled"], [])
        self.assertEqual(body["retest"], [])
        self.assertEqual(body["requalify"], [])
        self.assertFalse(body["_schedule_refill"] if "_schedule_refill" in body else False)
        self.assertGreater(library_trial_count(), len(names))
        for name in body["names"]:
            self.assertTrue(is_library_name(name))
            self.assertNotIn("/", name)
            # Canonical spelling: no parameter suffix.
            self.assertEqual(name.count("."), 1)
        leases = load_lease_state()["leases"]
        for name in names[:cap]:
            self.assertTrue(leases[name]["library"])
            self.assertEqual(leases[name]["worker_id"], "zealous-lion")
        again = claim_discovery_batch("zealous-lion", 64, parallel=width, now=now, gate_rules=GATE_RULES, require_gate_rules=True)
        self.assertEqual(again["names"], [])
        got = list(body["names"])
        rest = names[cap:]
        for worker in ("omarchy-1", "omarchy-2", "omarchy-3"):
            if not rest:
                break
            nxt = claim_discovery_batch(worker, 64, parallel=width, now=now, gate_rules=GATE_RULES, require_gate_rules=True)
            self.assertEqual(nxt["names"], rest[:cap])
            self.assertEqual(nxt["source"], "library")
            got.extend(nxt["names"])
            rest = rest[cap:]
        self.assertEqual(got, names)
        self.assertEqual(rest, [])
        leases = load_lease_state()["leases"]
        self.assertEqual(set(leases), set(names))
        for name in names:
            self.assertTrue(leases[name]["library"])

    def test_tested_original_fifteen_leave_batch2_claimable(self):
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import LEASES_PER_LANE, claim_discovery_batch

        original = claim_names()[:15]
        self.assertEqual(original[-1], "lib.macd")
        fresh = [name for name in claim_names() if name not in original]
        self.assertGreaterEqual(len(fresh), 10)
        for name in original:
            ingested = ingest_discovery_payload({"evaluations": [_row(name)]})
            self.assertEqual(ingested["ingested"], [name])
            self.assertEqual(ingested["admitted"], [])
        now = datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc)
        body = claim_discovery_batch(
            "zealous-lion", 64, parallel=8, now=now, gate_rules=GATE_RULES, require_gate_rules=True,
        )
        cap = LEASES_PER_LANE * 8
        self.assertEqual(body["names"], fresh[:cap])
        self.assertTrue(set(body["names"]).isdisjoint(original))
        self.assertEqual(body["refilled"], [])
        self.assertEqual(body["retest"], [])

    def test_stale_v5_worker_gets_no_names(self):
        from hedge_fund.trading.leases import claim_discovery_batch, load_lease_state

        body = claim_discovery_batch(
            "zealous-lion",
            4,
            parallel=2,
            now=datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc),
            gate_rules=V5_GATE_RULES,
            require_gate_rules=True,
        )
        self.assertEqual(body["names"], [])
        self.assertTrue(body["stale_rules"])
        self.assertEqual(body["gate_rules"], GATE_RULES)
        self.assertEqual(body["worker_gate_rules"], V5_GATE_RULES)
        self.assertEqual(load_lease_state()["leases"], {})

    def test_tested_and_book_names_are_not_leased(self):
        from hedge_fund.trading.champions import save_pool
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch

        save_pool({"champions": [{"name": "lib.sma200"}], "synced_until": ""})
        ingested = ingest_discovery_payload({"evaluations": [_row("lib.sma5x150")]})
        self.assertEqual(ingested["ingested"], ["lib.sma5x150"])
        self.assertEqual(ingested["admitted"], [])
        body = claim_discovery_batch(
            "zealous-lion",
            4,
            parallel=4,
            now=datetime(2026, 10, 28, 18, 5, tzinfo=timezone.utc),
            gate_rules=GATE_RULES,
            require_gate_rules=True,
        )
        self.assertNotIn("lib.sma200", body["names"])
        self.assertNotIn("lib.sma5x150", body["names"])
        self.assertEqual(body["names"][0], "lib.sma100")
        self.assertTrue(all(n.startswith("lib.") and n.count(".") == 1 for n in body["names"]))

    def test_refill_buffer_does_not_mint(self):
        from hedge_fund.trading.leases import refill_buffer_unlocked

        with patch("hedge_fund.trading.leases.next_refill_batch", side_effect=AssertionError("mint")):
            body = refill_buffer_unlocked(now=datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(body["skipped"], "library_mode")
        self.assertEqual(body["refilled"], [])

    def test_worker_plan_and_in_cycle_tournament_mint_nothing(self):
        from scripts.discovery_worker import _plan_batch
        from scripts.tournament_engine import discover_and_qualify

        planned, rotated, added = _plan_batch(4)
        self.assertEqual(planned, claim_names()[:4])
        self.assertEqual(rotated, claim_names())
        self.assertEqual(added, [])
        self.assertEqual(discover_and_qualify(max_names=2), ([], []))


class LibraryIngestTests(_LibraryMode):
    def test_valid_row_is_stored_and_not_seated(self):
        from hedge_fund.library.results import library_table_payload
        from hedge_fund.trading.champions import load_pool
        from hedge_fund.trading.discovery import load_discovery_log
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.trials import trial_census
        from hedge_fund.web.discovery import discovery_library_response

        fail = ingest_discovery_payload({"evaluations": [_row("lib.sma200", qualified=False)]})
        passed = ingest_discovery_payload({"evaluations": [_row("lib.golden_cross", qualified=True)]})
        self.assertEqual(fail["ingested"], ["lib.sma200"])
        self.assertEqual(fail["proposed"], [])
        self.assertEqual(fail["admitted"], [])
        self.assertEqual(passed["ingested"], ["lib.golden_cross"])
        self.assertEqual(passed["proposed"], ["lib.golden_cross"])
        self.assertEqual(passed["admitted"], [])
        self.assertEqual(passed["admission"], "manual")
        names = {c.get("name") for c in load_pool().get("champions") or []}
        self.assertNotIn("lib.sma200", names)
        self.assertNotIn("lib.golden_cross", names)
        logged = {row["strategy"] for row in load_discovery_log()}
        self.assertEqual(logged, {"lib.sma200", "lib.golden_cross"})
        census = trial_census()
        self.assertEqual(census["n_backtested"], 0)
        table = library_table_payload()
        by_name = {row["name"]: row for row in table["strategies"]}
        self.assertEqual(by_name["lib.sma200"]["status"], "tested")
        sma = by_name["lib.sma200"]["result"]
        self.assertEqual(sma["daily_sharpe"], 0.40)
        self.assertEqual(sma["failing_checks"], ["oos_trades"])
        self.assertTrue(sma["count_only"])
        self.assertEqual(sma["n_trials"], library_trial_count())
        self.assertEqual(sma["holdout_sharpe"], 0.36)
        self.assertEqual(by_name["lib.golden_cross"]["status"], "tested")
        self.assertTrue(by_name["lib.golden_cross"]["result"]["qualified"])
        self.assertEqual(by_name["lib.sma100"]["status"], "queued")
        self.assertIsNone(by_name["lib.sma100"]["result"])
        self.assertIn("Brock", by_name["lib.sma200"]["citation"]["short"])
        api = discovery_library_response()
        self.assertEqual(api["source"], "library")
        self.assertEqual(api["gate_rules"], GATE_RULES)
        self.assertEqual(api["progress"]["tested"], 2)
        self.assertEqual(api["progress"]["passed"], 1)

    def test_rejects_a_row_that_is_not_this_library(self):
        from hedge_fund.trading.champions import load_pool
        from hedge_fund.trading.discovery import load_discovery_log
        from hedge_fund.trading.ingest import ingest_discovery_payload

        cases = [
            _row("lib.sma200", timeframe="5m"),
            _row("lib.sma200", engine="rm_v1"),
            _row("lib.sma200", risk_policy="rm_v1"),
            _row("lib.sma200", gate_rules=V5_GATE_RULES),
            _row("lib.sma200", n_trials=1),
            _row("lib.sma200", gate_v4=None),
            _row("lib.sma200.1_150"),
            _row("lib.nope"),
            _row("lib.sma200", qualified=True, gate_v4={"passed": True, "rules": GATE_RULES, "n_trials": library_trial_count()}),
        ]
        # gate_v4=None still builds a default inside _row. Force a missing block.
        cases[5] = {k: v for k, v in _row("lib.sma200").items() if k != "gate_v4"}
        body = ingest_discovery_payload({"evaluations": cases})
        self.assertEqual(body["ingested"], [])
        self.assertEqual(body["admitted"], [])
        self.assertEqual(len(body["rejected_invalid"]), len(cases))
        self.assertEqual(load_discovery_log(), [])
        self.assertEqual(load_pool().get("champions"), [])

    def test_force_admit_seats_the_signal_exit_engine(self):
        from hedge_fund.trading.champions import load_pool
        from hedge_fund.trading.ingest import ingest_discovery_payload

        row = _row("lib.sma200", qualified=True)
        body = ingest_discovery_payload({"evaluations": [row], "force_admit": ["lib.sma200"], "source": "owner"})
        self.assertEqual(body["force_admitted"], ["lib.sma200"])
        champs = load_pool()["champions"]
        self.assertEqual(len(champs), 1)
        seated = champs[0]
        self.assertEqual(seated["name"], "lib.sma200")
        self.assertEqual(seated["source"], "library_qualification")
        self.assertEqual(seated["engine"], ENGINE_ID)
        self.assertEqual(seated["timeframe"], TIMEFRAME)
        self.assertEqual(seated["risk_policy"], RISK_POLICY)
        self.assertEqual(seated["library"]["key"], "sma200")
        self.assertEqual(seated["library"]["safety_stop_pct"], None)


class RandomMintStillAvailableTests(unittest.TestCase):
    def test_random_source_still_leases_recipe_names(self):
        from hedge_fund.trading.leases import claim_discovery_batch

        universe = [f"don_hi_{6 + 6 * i}" for i in range(4)]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp), "PAPER_DISCOVERY_SOURCE": "random"}):
                with patch("hedge_fund.trading.leases.schedule_refill_buffer"):
                    with patch("hedge_fund.trading.leases.discovery_universe", return_value=universe):
                        with patch("hedge_fund.trading.leases.next_refill_batch", return_value=[]):
                            body = claim_discovery_batch(
                                "zealous-lion",
                                4,
                                parallel=4,
                                now=datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc),
                                gate_rules=GATE_RULES,
                                require_gate_rules=True,
                            )
        self.assertEqual(set(body["names"]), set(universe))
        self.assertTrue(all(not is_library_name(n) for n in body["names"]))
        self.assertNotIn("source", body)


if __name__ == "__main__":
    unittest.main()
