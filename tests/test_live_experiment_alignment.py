"""Alignment tests: PROTOCOL amendment, dashboard rules, isolated runner.

Locks the 2026-08-30 contract: one paper experiment (TradingLoop cycle),
not a second 0.60 / 2.5% book in run_isolated, and dashboard copy that
matches live constants.
"""
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[1]


class ProtocolAmendmentTests(unittest.TestCase):
    def test_amendment_2026_08_30(self):
        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("can an LLM agent (Hermes) state probabilities", text)
        self.assertNotIn("| _(none yet)_ | |", text)
        self.assertIn("2026-08-30", text)
        self.assertIn("Amendment 2026-08-30", text)
        self.assertIn("graduated paper", text.lower())
        self.assertIn("TradingLoop.run_cycle", text)
        self.assertIn("baseline=None", text)  # historical 2026-08-30 text kept
        self.assertIn("not an LLM", text)
        self.assertIn("Paper only", text)
        # Original sizing/fees language kept for history
        self.assertIn("1 % fixed-fractional risk per trade", text)
        self.assertIn("Taker fee (0.1 %)", text)

    def test_amendment_2026_09_01(self):
        from hedge_fund.trading.constants import (
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
            TRADE_EVALUATION_LIMIT,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-01", text)
        self.assertIn("2026-09-01", text)
        self.assertIn(QUAL_TIMEFRAME, text)
        self.assertIn(RISK_POLICY, text)
        self.assertIn("MAX_ACTIVE_CHAMPIONS = 20", text)
        self.assertIn(str(TRADE_EVALUATION_LIMIT), text)
        self.assertIn(str(MIN_BACKTEST_TRADES), text)
        self.assertIn("0.30", text)
        self.assertIn("same game", text.lower())
        self.assertIn("5m tape was **discovery-only**", text)
        self.assertIn("MIN_BACKTEST_SHARPE = 0.30", text)
        self.assertIn("CYCLE_INTERVAL_SECONDS = 3600", text)

    def test_amendment_2026_09_02(self):
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            QUAL_WINDOW_BARS,
            QUAL_WINDOW_DAYS,
            RISK_POLICY,
            TRADE_EVALUATION_LIMIT,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-02", text)
        self.assertIn("2026-09-02", text)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertIn(QUAL_TIMEFRAME, text)
        self.assertIn("5m", text)
        self.assertIn("CYCLE_INTERVAL_SECONDS = 300", text)
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertIn("07–21 Europe/Stockholm", text)
        self.assertIn("24 × 5m = **2 hours**", text)
        self.assertIn("not 24 × 4h = 4 days", text)
        self.assertIn("dip_24b_lt1pc", text)
        self.assertIn("GRADUATED_PAPER", text)
        self.assertIn("Paper only", text)
        self.assertIn(RISK_POLICY, text)
        self.assertIn("MAX_ACTIVE_CHAMPIONS = 20", text)
        self.assertIn(str(TRADE_EVALUATION_LIMIT), text)
        self.assertIn(str(MIN_BACKTEST_TRADES), text)
        self.assertIn("0.30", text)
        self.assertEqual(QUAL_WINDOW_DAYS, 90)
        self.assertEqual(QUAL_WINDOW_BARS, 25920)
        self.assertIn("25920", text)
        self.assertIn("90 calendar days", text)
        self.assertNotEqual(QUAL_WINDOW_BARS, 2500)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        # 4h remains as historical 09-01 text, but 09-02 names the supersession.
        self.assertIn("5m is the admit tape", text)
        self.assertIn("live and admit are 5m", text)

    def test_amendment_2026_09_03(self):
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
            TRADE_EVALUATION_LIMIT,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-03", text)
        self.assertIn("2026-09-03", text)
        self.assertIn("structure atoms", text.lower())
        self.assertIn("OHLC", text)
        self.assertIn("Paper only", text)
        self.assertIn("don_hi_N", text)
        self.assertIn("near_swing_lo_N", text)
        # Gates unchanged from 2026-09-02.
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        import hedge_fund.trading.constants as constants
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)
        self.assertIn("OOS gates are unchanged", text)
        self.assertIn("arena 20", text)

    def test_amendment_2026_09_03_revokes_live_slot_cap(self):
        import hedge_fund.trading.constants as constants
        from hedge_fund.trading.constants import TRADE_EVALUATION_LIMIT

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("20-slot arena revoked", text)
        self.assertIn("MAX_ACTIVE_CHAMPIONS` is deleted", text)
        self.assertIn("combinatorial bound", text)
        # Historical 2026-09-03 text still names the old sample; the live
        # constant is gone (2026-09-05 drains leftovers instead).
        self.assertIn("DISCOVER_BATCH_SIZE = 30", text)
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        self.assertFalse(hasattr(constants, "DISCOVER_BATCH_SIZE"))
        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)

    def test_amendment_2026_09_05_wavetrend(self):
        from hedge_fund.trading.constants import QUAL_TIMEFRAME, RISK_POLICY, TRADE_EVALUATION_LIMIT
        import hedge_fund.trading.constants as constants

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-05", text)
        self.assertIn("LazyBear", text)
        self.assertIn("wt_cross_up_os", text)
        self.assertIn("HLC3", text)
        self.assertIn("not market cipher", text.lower())
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))

    def test_amendment_2026_09_05_night_window_and_discovery_drain(self):
        import hedge_fund.trading.constants as constants
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("24/7 cycle and leftover-universe discovery", text)
        self.assertIn("Night window revoked", text)
        self.assertIn("Discovery drains leftovers", text)
        self.assertIn("Discovery buckets", text)
        self.assertIn("/api/discovery/summary", text)
        self.assertIn("no 30-name sample", text)
        self.assertIn("Paper only", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("No new strategies", text)
        self.assertIn("CYCLE_INTERVAL_SECONDS = 300", text)
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertFalse(hasattr(constants, "DISCOVER_BATCH_SIZE"))
        self.assertFalse(hasattr(constants, "CYCLE_WINDOW_START_HOUR"))
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        # Historical 09-02 night-window sentence stays; this amendment names the supersession.
        self.assertIn("sidecar still skips outside 07–21 Europe/Stockholm", text)
        self.assertIn("07–21 Europe/Stockholm; that night window is unchanged", text)

    def test_amendment_2026_09_07_budgeted_discovery(self):
        import hedge_fund.trading.constants as constants
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
            DISCOVERY_LOG_CAP,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-07", text)
        self.assertIn("budgeted discovery", text.lower())
        self.assertIn("incremental discovery log", text.lower())
        self.assertIn("DISCOVER_CYCLE_MAX_NAMES", text)
        self.assertIn("DISCOVER_CYCLE_TIME_BUDGET_SECONDS", text)
        self.assertIn("discovery_cursor.json", text)
        self.assertIn("24h", text)
        self.assertIn("DISCOVER_RETEST_COOLDOWN_SECONDS", text)
        self.assertIn("shuffle 30 and ignore the rest", text)
        self.assertIn("Paper only", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("last_tested_at", text)
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 90)
        self.assertEqual(DISCOVERY_LOG_CAP, 10000)
        self.assertLess(DISCOVER_CYCLE_MAX_NAMES, 30)
        self.assertLess(DISCOVER_CYCLE_MAX_NAMES, 4)
        self.assertLess(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, CYCLE_INTERVAL_SECONDS)
        self.assertLess(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 150)
        self.assertFalse(hasattr(constants, "DISCOVER_BATCH_SIZE"))
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        self.assertIn("lighter per-cycle discovery slice", text)
        self.assertIn("DISCOVER_CYCLE_MAX_NAMES` (1)", text)
        self.assertIn("DISCOVER_CYCLE_TIME_BUDGET_SECONDS` (90s)", text)
        self.assertGreater(
            CYCLE_INTERVAL_SECONDS - DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 150
        )

    def test_amendment_2026_09_10_fail_once_never_retest(self):
        import hedge_fund.trading.constants as constants
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
            TRADE_EVALUATION_LIMIT,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-10", text)
        self.assertIn("fail once, never retest", text.lower())
        self.assertIn("parked forever", text.lower())
        self.assertIn("DISCOVER_RETEST_COOLDOWN_SECONDS` is deleted", text)
        self.assertIn("Never-tested leftovers still drain", text)
        self.assertIn("Existing prod fails are parked", text)
        self.assertIn("cheaper per-name discovery eval", text.lower())
        self.assertIn("causal", text.lower())
        self.assertIn("QUAL_WINDOW_BARS", text)
        self.assertIn("Paper only", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("OOS gates are unchanged", text)
        self.assertFalse(hasattr(constants, "DISCOVER_RETEST_COOLDOWN_SECONDS"))
        self.assertFalse(hasattr(constants, "DISCOVER_BATCH_SIZE"))
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 90)
        readme = (REPO / "README.md").read_text()
        self.assertIn("2026-09-10", readme)
        self.assertIn("no 24h", readme.lower())
        web = (REPO / "docs" / "WEB_SERVICE.md").read_text()
        self.assertIn("rejected parked forever", web.lower())

    def test_amendment_2026_09_11_cautious_discovery_bump(self):
        import hedge_fund.trading.constants as constants
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            QUAL_WINDOW_BARS,
            RISK_POLICY,
            TRADE_EVALUATION_LIMIT,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-11", text)
        self.assertIn("cautious per-cycle discovery bump", text.lower())
        self.assertIn("DISCOVER_CYCLE_MAX_NAMES` (2)", text)
        self.assertIn("DISCOVER_CYCLE_TIME_BUDGET_SECONDS` (120s)", text)
        self.assertIn("Not 4 names. Not 150s.", text)
        self.assertIn("Fail-once never-retest", text)
        self.assertIn("Paper only", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("OOS gates are unchanged", text)
        self.assertIn("incremental log", text.lower())
        self.assertIn("rotating cursor", text.lower())
        self.assertFalse(hasattr(constants, "DISCOVER_RETEST_COOLDOWN_SECONDS"))
        self.assertFalse(hasattr(constants, "DISCOVER_BATCH_SIZE"))
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)
        self.assertEqual(QUAL_WINDOW_BARS, 25920)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 90)
        self.assertLess(DISCOVER_CYCLE_MAX_NAMES, 4)
        self.assertLess(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 150)
        self.assertGreater(
            CYCLE_INTERVAL_SECONDS - DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 150
        )
        readme = (REPO / "README.md").read_text()
        self.assertIn("2026-09-11", readme)
        self.assertIn("2 names / ~120s", readme)
        self.assertIn("4 / 150s", readme)

    def test_amendment_2026_09_11_structure_window_leftovers(self):
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
        )
        from hedge_fund.trading.universe import NEW_STRUCTURE_ANDS, generate_universe

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("structure-window leftovers", text.lower())
        self.assertIn("NEW_STRUCTURE_ANDS", text)
        self.assertIn("never-tested names", text.lower())
        self.assertIn("near_duplicate_key", text)
        self.assertIn("don_hi_12", text)
        self.assertIn("near_swing_hi_12", text)
        self.assertIn("dbl_bot_18", text)
        self.assertIn("No WaveTrend", text)
        self.assertIn("No MFI", text)
        self.assertIn("parked 60", text)
        self.assertIn("Paper only", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("OOS gates are unchanged", text)
        self.assertIn("one shot", text.lower())
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 90)
        uni = generate_universe()
        for name in NEW_STRUCTURE_ANDS:
            self.assertIn(name, uni)
        readme = (REPO / "README.md").read_text()
        self.assertIn("NEW_STRUCTURE_ANDS", readme)
        self.assertIn("parked 60", readme)

    def test_amendment_2026_09_11_auto_refill(self):
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
            DISCOVERY_REFILL_BATCH_SIZE,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
        )
        from hedge_fund.trading.universe import UNIVERSE_TARGET_MAX, generate_universe

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("auto-refill never-tested names", text.lower())
        self.assertIn("discovery_extended.json", text)
        self.assertIn("DISCOVERY_REFILL_BATCH_SIZE", text)
        self.assertIn("no pr per batch", text.lower())
        self.assertIn("Pending queue sidecar", text)
        self.assertIn("near_duplicate_key", text)
        self.assertIn("No WaveTrend", text)
        self.assertIn("No MFI", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("Do not retest parked fails", text)
        self.assertIn("OOS gates are unchanged", text)
        self.assertIn("Paper only", text)
        self.assertIn("one shot", text.lower())
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 90)
        self.assertEqual(DISCOVERY_REFILL_BATCH_SIZE, 16)
        self.assertLessEqual(len(generate_universe()), UNIVERSE_TARGET_MAX)
        readme = (REPO / "README.md").read_text()
        self.assertIn("discovery_extended.json", readme)
        self.assertIn("no human PR per batch", readme)
        web = (REPO / "docs" / "WEB_SERVICE.md").read_text()
        self.assertIn("discovery_extended.json", web)

    def test_amendment_2026_09_11_single_name_slice(self):
        import hedge_fund.trading.constants as constants
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
            DISCOVERY_REFILL_BATCH_SIZE,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            QUAL_WINDOW_BARS,
            RISK_POLICY,
            TRADE_EVALUATION_LIMIT,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-11 addendum", text)
        self.assertIn("single-name discovery slice", text.lower())
        self.assertIn("DISCOVER_CYCLE_MAX_NAMES` (1)", text)
        self.assertIn("DISCOVER_CYCLE_TIME_BUDGET_SECONDS` (90s)", text)
        self.assertIn("only one name at a time", text)
        self.assertIn("Not 2 names. Not 4 names. Not 150s.", text)
        self.assertIn("Auto-refill still compares eligible to the live name cap", text)
        self.assertIn("DISCOVERY_REFILL_BATCH_SIZE", text)
        self.assertIn("Fail-once never-retest", text)
        self.assertIn("Paper only", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("OOS gates are unchanged", text)
        self.assertFalse(hasattr(constants, "DISCOVER_RETEST_COOLDOWN_SECONDS"))
        self.assertFalse(hasattr(constants, "DISCOVER_BATCH_SIZE"))
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)
        self.assertEqual(QUAL_WINDOW_BARS, 25920)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 90)
        self.assertEqual(DISCOVERY_REFILL_BATCH_SIZE, 16)
        self.assertLess(DISCOVER_CYCLE_MAX_NAMES, 2)
        self.assertLess(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 150)
        self.assertGreater(
            CYCLE_INTERVAL_SECONDS - DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 150
        )
        readme = (REPO / "README.md").read_text()
        self.assertIn("1 name / ~90s", readme)
        self.assertIn("only one name per cycle", readme)
        web = (REPO / "docs" / "WEB_SERVICE.md").read_text()
        self.assertIn("1 name / ~90s cycle slice", web)

    def test_amendment_2026_09_12_windows_discovery_farm(self):
        import hedge_fund.trading.constants as constants
        from hedge_fund.trading.constants import (
            CYCLE_INTERVAL_SECONDS,
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
            DISCOVERY_REFILL_BATCH_SIZE,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            QUAL_WINDOW_BARS,
            RISK_POLICY,
            TRADE_EVALUATION_LIMIT,
        )
        from hedge_fund.trading.discovery_mode import discovery_on_cycle

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("Amendment 2026-09-12", text)
        self.assertIn("discovery farm on the Windows PC", text)
        self.assertIn("DISCOVERY_ON_CYCLE=0", text)
        self.assertIn("PAPER_DISCOVERY_MODE=off", text)
        self.assertIn("/api/discovery/ingest", text)
        self.assertIn("PAPER_DISCOVERY_INGEST_TOKEN", text)
        self.assertIn("jensa", text)
        self.assertIn("scripts/discovery_worker.py", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("OOS gates are unchanged", text)
        self.assertIn("paper only", text.lower())
        self.assertIn("Still paper", text)
        self.assertIn("no cuda", text.lower())
        self.assertFalse(discovery_on_cycle({}))
        self.assertFalse(discovery_on_cycle({"DISCOVERY_ON_CYCLE": "0"}))
        self.assertTrue(discovery_on_cycle({"DISCOVERY_ON_CYCLE": "1"}))
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(TRADE_EVALUATION_LIMIT, 80)
        self.assertEqual(QUAL_WINDOW_BARS, 25920)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 90)
        self.assertEqual(DISCOVERY_REFILL_BATCH_SIZE, 16)
        self.assertEqual(CYCLE_INTERVAL_SECONDS, 300)
        self.assertFalse(hasattr(constants, "DISCOVER_BATCH_SIZE"))
        self.assertFalse(hasattr(constants, "MAX_ACTIVE_CHAMPIONS"))
        readme = (REPO / "README.md").read_text()
        self.assertIn("2026-09-12", readme)
        self.assertIn("DISCOVERY_ON_CYCLE=0", readme)
        self.assertIn("discovery_worker.py", readme)
        web = (REPO / "docs" / "WEB_SERVICE.md").read_text()
        self.assertIn("/api/discovery/ingest", web)
        self.assertIn("PAPER_DISCOVERY_INGEST_TOKEN", web)
        runbook = (REPO / "docs" / "WINDOWS_DISCOVERY.md").read_text()
        self.assertIn("jensa", runbook)
        self.assertIn("fetch_history.py", runbook)
        self.assertIn("DISCOVERY_ON_CYCLE=0", runbook)
        live = (REPO / "scripts" / "live_cycle.py").read_text()
        self.assertIn("discovery_on_cycle", live)
        self.assertIn("tournament skipped", live)
        self.assertTrue((REPO / "scripts" / "discovery_worker.py").exists())
        self.assertTrue((REPO / "docker-compose.discovery.yml").exists())

    def test_amendment_2026_09_12_structure_recipe_expansion(self):
        from hedge_fund.trading.constants import (
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
            DISCOVERY_REFILL_BATCH_SIZE,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
        )
        from hedge_fund.trading.refill import STRUCTURE_NS, iter_recipe_names
        from hedge_fund.trading.universe import UNIVERSE_TARGET_MAX, generate_universe

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("more structure-AND recipe names", text)
        self.assertIn("iter_recipe_names", text)
        self.assertIn("STRUCTURE_NS", text)
        self.assertIn("LEVEL_TRENDS", text)
        self.assertIn("near_swing_hi_N", text)
        self.assertIn("don_lo_N", text)
        self.assertIn("near-level", text.lower())
        self.assertIn("named candlesticks", text.lower())
        self.assertIn("No named candlesticks", text)
        self.assertIn("engulfing", text.lower())
        self.assertIn("hammer", text.lower())
        self.assertIn("doji", text.lower())
        self.assertIn("farm auto-refill", text.lower())
        self.assertIn("near_duplicate_key", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("Do not retest parked fails", text)
        self.assertIn("OOS gates are unchanged", text)
        self.assertIn("Still paper", text)
        self.assertIn("No WaveTrend", text)
        self.assertIn("No MFI", text)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 90)
        self.assertEqual(DISCOVERY_REFILL_BATCH_SIZE, 16)
        self.assertIn(84, STRUCTURE_NS)
        self.assertIn(96, STRUCTURE_NS)
        self.assertIn("through 96", text)
        self.assertLessEqual(len(list(iter_recipe_names())), 4000)
        self.assertLessEqual(len(generate_universe()), UNIVERSE_TARGET_MAX)
        readme = (REPO / "README.md").read_text()
        self.assertIn("near-level", readme)
        self.assertIn("named candlesticks", readme)
        runbook = (REPO / "docs" / "WINDOWS_DISCOVERY.md").read_text()
        self.assertIn("near-level", runbook)
        self.assertIn("named candlesticks", runbook)

    def test_amendment_2026_09_12_farm_start_stop(self):
        from hedge_fund.trading.discovery_mode import discovery_on_cycle

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("farm Start/Stop from the Discovery page", text)
        self.assertIn("discovery_farm.json", text)
        self.assertIn("/api/discovery/farm", text)
        self.assertIn("authenticated", text.lower())
        self.assertIn("401", text)
        self.assertIn("does not exit", text)
        self.assertIn("Worker not seen", text)
        self.assertIn("DISCOVERY_ON_CYCLE=0", text)
        self.assertIn("Still paper", text)
        self.assertFalse(discovery_on_cycle({}))
        worker = (REPO / "scripts" / "discovery_worker.py").read_text()
        self.assertIn("poll_farm_enabled", worker)
        self.assertIn("consider_pause", worker)
        self.assertIn("farm paused", worker)
        self.assertNotIn("os._exit", worker)
        web = (REPO / "docs" / "WEB_SERVICE.md").read_text()
        self.assertIn("/api/discovery/farm", web)
        self.assertIn("PAPER_DISCOVERY_INGEST_TOKEN", web)
        runbook = (REPO / "docs" / "WINDOWS_DISCOVERY.md").read_text()
        self.assertIn("Pause for gaming", runbook)
        self.assertIn("leave the worker running", runbook.lower())
        self.assertIn("Authentication (required)", runbook)
        self.assertIn("Never commit the token", runbook)
        self.assertIn("X-Paper-Discovery-Token", runbook)
        self.assertIn("Worker not seen", runbook)
        self.assertIn("discovery_worker.py --workers 2", runbook)
        server = (REPO / "hedge_fund" / "web" / "server.py").read_text()
        self.assertIn('route == "/api/discovery/farm"', server)
        self.assertIn("_discovery_ingest_authorized", server)
        self.assertIn("X-Paper-Discovery-Token", server)
        self.assertIn("_discovery_header_tokens", server)

    def test_amendment_2026_09_12_longer_lookbacks_and_leftover_ands(self):
        from hedge_fund.trading.constants import (
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVERY_REFILL_BATCH_SIZE,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
        )
        from hedge_fund.trading.refill import (
            STRUCTURE_NS,
            STRUCTURE_NS_THROUGH_96,
            iter_recipe_names,
            next_refill_batch,
        )
        from hedge_fund.trading.universe import UNIVERSE_TARGET_MAX, generate_universe

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("longer structure lookbacks and leftover AND families", text)
        self.assertIn("108", text)
        self.assertIn("192", text)
        self.assertIn("ema_stack_20_50_100", text)
        self.assertIn("ema_abv_100", text)
        self.assertIn("leftover TREND", text)
        self.assertIn("3-atom extras", text)
        self.assertIn("eligible=0", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("Do not retest parked fails", text)
        self.assertIn("OOS gates are unchanged", text)
        self.assertIn("Still paper", text)
        self.assertIn("No named candlesticks", text)
        self.assertIn("No MFI", text)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVERY_REFILL_BATCH_SIZE, 16)
        self.assertEqual(STRUCTURE_NS_THROUGH_96[-2:], (84, 96))
        self.assertEqual(STRUCTURE_NS[-2:], (180, 192))
        self.assertEqual(len(STRUCTURE_NS), 22)
        names = list(iter_recipe_names())
        self.assertGreater(len(names), 1500)
        self.assertLessEqual(len(names), 4000)
        uni = generate_universe()
        self.assertLessEqual(len(uni), UNIVERSE_TARGET_MAX)
        added = next_refill_batch(taken_names=uni, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        readme = (REPO / "README.md").read_text()
        self.assertIn("192", readme)
        self.assertIn("ema_stack", readme)
        runbook = (REPO / "docs" / "WINDOWS_DISCOVERY.md").read_text()
        self.assertIn("192", runbook)

    def test_amendment_2026_09_12_all_windows_veto_dropped(self):
        from hedge_fund.trading.constants import (
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
        )
        from hedge_fund.trading.qualify import qualification_decision
        from scripts.tournament_engine import qualification_decision as te_decision

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("all-windows OOS veto dropped", text)
        self.assertIn("aggregate OOS only", text)
        self.assertIn("all_windows_nonneg", text)
        self.assertIn("not emit", text)
        self.assertIn("window[i] failed/skipped/neg/empty", text)
        self.assertIn("not all windows non-negative", text)
        self.assertIn("Re-qualify from stored aggregates", text)
        self.assertIn("dbl_bot_120", text)
        self.assertIn("Still paper", text)
        self.assertIn("beat buy-and-hold", text.lower())
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertIs(te_decision, qualification_decision)

        src = (REPO / "scripts" / "tournament_engine.py").read_text()
        self.assertNotIn("not all windows non-negative", src)
        self.assertNotIn("window[{i}]", src)
        ingest = (REPO / "hedge_fund" / "trading" / "ingest.py").read_text()
        self.assertIn("requalify_parked_log", ingest)
        worker = (REPO / "scripts" / "discovery_worker.py").read_text()
        self.assertIn("evaluate_strategy_record", worker)
        self.assertNotIn("def qualification_decision", worker)

        readme = (REPO / "README.md").read_text()
        self.assertIn("all-windows non-negative OOS veto is dropped", readme)
        self.assertIn("beat-B&H stay", readme)
        html = (REPO / "hedge_fund" / "dashboard" / "report.py").read_text()
        self.assertIn("single empty/neg window is logged as a diagnostic", html)
        self.assertNotIn("Every window test PnL", html)

    def test_amendment_2026_09_12_wide_dip_mom_continuation(self):
        from hedge_fund.trading.constants import (
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVERY_REFILL_BATCH_SIZE,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_TIMEFRAME,
            RISK_POLICY,
        )
        from hedge_fund.trading.refill import (
            CONTINUATION_TRENDS,
            DIP_FILTERS_WIDE,
            MOM_FILTERS_WIDE,
            iter_recipe_names,
            next_refill_batch,
        )
        from hedge_fund.trading.universe import UNIVERSE_TARGET_MAX, generate_universe

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("wider dip/mom mint bases and continuation ANDs", text)
        self.assertIn("DIP_FILTERS_WIDE", text)
        self.assertIn("MOM_FILTERS_WIDE", text)
        self.assertIn("continuation ANDs", text)
        self.assertIn("mom_12b_gt2pc", text)
        self.assertIn("dip_12b_lt2pc", text)
        self.assertIn("sma_abv_20", text)
        self.assertIn("ema_abv_20", text)
        self.assertIn("tested_pass", text)
        self.assertIn("How to evaluate after merge", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("Do not retest parked fails", text)
        self.assertIn("OOS gates are unchanged", text)
        self.assertIn("Still paper", text)
        self.assertIn("No named candlesticks", text)
        self.assertIn("No MFI", text)
        self.assertIn("near_duplicate_key", text)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVERY_REFILL_BATCH_SIZE, 16)
        self.assertIn("mom_36b_gt2pc", DIP_FILTERS_WIDE + MOM_FILTERS_WIDE)
        self.assertIn("dip_36b_lt2pc", DIP_FILTERS_WIDE)
        self.assertEqual(CONTINUATION_TRENDS, ("sma_abv_20", "ema_abv_20"))
        names = list(iter_recipe_names())
        self.assertGreater(len(names), 1500)
        self.assertLessEqual(len(names), 4000)
        self.assertIn("mom_36b_gt2pc&don_hi_12", names)
        self.assertIn("dip_12b_lt2pc&near_swing_hi_24", names)
        self.assertIn("sma_abv_20&don_hi_12", names)
        uni = generate_universe()
        self.assertLessEqual(len(uni), UNIVERSE_TARGET_MAX)
        added = next_refill_batch(taken_names=uni, n=DISCOVERY_REFILL_BATCH_SIZE)
        self.assertEqual(len(added), DISCOVERY_REFILL_BATCH_SIZE)
        readme = (REPO / "README.md").read_text()
        self.assertIn("3×3 dip/mom", readme)
        self.assertIn("continuation ANDs", readme)
        runbook = (REPO / "docs" / "WINDOWS_DISCOVERY.md").read_text()
        self.assertIn("wider", runbook)
        self.assertIn("continuation ANDs", runbook)

    def test_amendment_2026_09_12_multi_year_walk_forward(self):
        from hedge_fund.trading.constants import (
            DISCOVER_CYCLE_MAX_NAMES,
            DISCOVER_CYCLE_TIME_BUDGET_SECONDS,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_COVERAGE_DAYS,
            QUAL_N_WINDOWS,
            QUAL_STRIDE,
            QUAL_TIMEFRAME,
            QUAL_WINDOW_BARS,
            QUAL_WINDOW_DAYS,
            RISK_POLICY,
        )

        text = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("multi-year walk-forward calendar coverage", text)
        self.assertIn("QUAL_N_WINDOWS` = 8", text)
        self.assertIn("QUAL_WINDOW_DAYS` = 90", text)
        self.assertIn("QUAL_COVERAGE_DAYS` = 720", text)
        self.assertIn("HIST_FETCH_PAGE_CAP` = 2500", text)
        self.assertIn("window_size * n_windows", text)
        self.assertIn("years", text.lower())
        self.assertIn("thresholds", text.lower())
        self.assertIn("Still paper", text)
        self.assertIn("Do not cull existing champions", text)
        self.assertIn("Do not retest parked fails", text)
        self.assertIn("jensa", text)
        self.assertIn("do not throttle", text.lower())
        self.assertIn("DISCOVERY_ON_CYCLE=0", text)
        self.assertEqual(QUAL_TIMEFRAME, "5m")
        self.assertEqual(RISK_POLICY, "rm_v1")
        self.assertEqual(QUAL_N_WINDOWS, 8)
        self.assertEqual(QUAL_WINDOW_DAYS, 90)
        self.assertEqual(QUAL_WINDOW_BARS, 25920)
        self.assertEqual(QUAL_COVERAGE_DAYS, 720)
        self.assertEqual(QUAL_STRIDE, 1)
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)
        self.assertEqual(DISCOVER_CYCLE_MAX_NAMES, 1)
        self.assertEqual(DISCOVER_CYCLE_TIME_BUDGET_SECONDS, 90)

        fetch = (REPO / "scripts" / "fetch_history.py").read_text()
        self.assertIn("QUAL_WINDOW_BARS * QUAL_N_WINDOWS + 3000", fetch)
        self.assertIn("HIST_FETCH_PAGE_CAP = 2500", fetch)
        self.assertNotIn("pages < 300", fetch)
        fetch5 = (REPO / "scripts" / "fetch_history_5m.py").read_text()
        self.assertIn("pages < 2500", fetch5)

        readme = (REPO / "README.md").read_text()
        self.assertIn("8 × ~90d", readme)
        self.assertIn("~720 days", readme)
        self.assertIn("OOS thresholds unchanged", readme)
        self.assertIn("do not throttle live k8s", readme)
        runbook = (REPO / "docs" / "WINDOWS_DISCOVERY.md").read_text()
        self.assertIn("8 × ~90 calendar days", runbook)
        self.assertIn("~720 days", runbook)
        self.assertIn("page cap is 2500", runbook)
        self.assertIn("Do **not** turn discovery back on", runbook)
        html = (REPO / "hedge_fund" / "dashboard" / "report.py").read_text()
        self.assertIn("8×90d span", html)
        self.assertNotIn("3×90d span", html)

    def test_fetch_history_defaults_btc_eth_only(self):
        from hedge_fund.trading.constants import (
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_N_WINDOWS,
        )

        fetch = (REPO / "scripts" / "fetch_history.py").read_text()
        self.assertIn('_DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT"]', fetch)
        self.assertIn("HIST_SYMBOLS", fetch)
        self.assertNotIn("SOL/USDT", fetch)
        self.assertNotIn("XRP/USDT", fetch)
        self.assertIn("HIST_FETCH_PAGE_CAP = 2500", fetch)
        self.assertIn("QUAL_WINDOW_BARS * QUAL_N_WINDOWS + 3000", fetch)

        fetch5 = (REPO / "scripts" / "fetch_history_5m.py").read_text()
        self.assertIn('_DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT"]', fetch5)
        self.assertIn("HIST_SYMBOLS", fetch5)
        self.assertNotIn("SOL/USDT", fetch5)
        self.assertNotIn("XRP/USDT", fetch5)
        self.assertIn("pages < 2500", fetch5)

        self.assertEqual(QUAL_N_WINDOWS, 8)
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)

        protocol = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("SOL/XRP are not fetched", protocol)
        self.assertIn("HIST_SYMBOLS", protocol)
        runbook = (REPO / "docs" / "WINDOWS_DISCOVERY.md").read_text()
        self.assertIn("BTC/USDT and", runbook)
        self.assertIn("ETH/USDT only", runbook)
        self.assertIn("no SOL/XRP", runbook)
        workflow = (REPO / "docs" / "WORKFLOW.md").read_text()
        self.assertIn("BTC/USDT and ETH/USDT only", workflow)
        self.assertIn("Binance 5m BTC/ETH", workflow)

    def test_amendment_2026_09_13_discovery_log_cap(self):
        from hedge_fund.trading.constants import (
            DISCOVERY_LOG_CAP,
            MIN_BACKTEST_SHARPE,
            MIN_BACKTEST_TRADES,
            QUAL_N_WINDOWS,
        )

        self.assertEqual(DISCOVERY_LOG_CAP, 10000)
        self.assertEqual(QUAL_N_WINDOWS, 8)
        self.assertEqual(MIN_BACKTEST_TRADES, 30)
        self.assertEqual(MIN_BACKTEST_SHARPE, 0.30)

        protocol = (REPO / "PROTOCOL.md").read_text()
        self.assertIn("DISCOVERY_LOG_CAP` = 10000", protocol)
        self.assertIn("was 1000", protocol)
        self.assertIn("Already tested", protocol)
        self.assertIn("Paper only", protocol)
        self.assertIn("OOS thresholds", protocol)
        self.assertIn("QUAL_N_WINDOWS", protocol)
        self.assertIn("fetch symbols unchanged", protocol)


class IsolatedRunnerTests(unittest.TestCase):
    def test_no_second_hardcoded_book(self):
        src = (REPO / "hedge_fund" / "trading" / "run_isolated.py").read_text()
        self.assertIn("run_cycle", src)
        self.assertNotIn("prob = 0.60", src)
        self.assertNotIn("entry * (1 - 0.025)", src)
        tree = ast.parse(src)
        calls = [
            n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute)
        ]
        self.assertIn("run_cycle", calls)

    def test_main_delegates_to_trading_loop(self):
        from hedge_fund.trading import run_isolated as ri

        fake_loop = MagicMock()
        fake_loop.run_cycle.return_value = []
        fake_broker = MagicMock()
        fake_broker.to_state.return_value = {}
        fake_loop.broker = fake_broker
        fake_risk = MagicMock()
        fake_risk.peak_equity = 10_000.0

        store = MagicMock()
        store.load_account_state.return_value = None

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ri, "CcxtSource") as src_cls, \
                 patch.object(ri, "load_pool", return_value={"champions": [{"name": "sma_stack"}]}), \
                 patch.object(ri, "TradeStore", return_value=store), \
                 patch.object(ri, "CalibrationStore"), \
                 patch.object(ri, "PaperBroker", return_value=fake_broker), \
                 patch.object(ri, "RiskManager", return_value=fake_risk), \
                 patch.object(ri, "TradingLoop", return_value=fake_loop) as loop_cls, \
                 patch("sys.argv", ["run_isolated", "--cycles", "1", "--state", tmp]):
                src = src_cls.return_value
                src.fetch_price.return_value = 100.0
                src.fetch_klines.return_value = [object()]
                ri.main()

            loop_cls.assert_called()
            kwargs = loop_cls.call_args.kwargs
            self.assertEqual(kwargs.get("strategy"), "sma_stack")
            self.assertIsNone(kwargs.get("regime"))
            fake_loop.run_cycle.assert_called_with(ri.SYMBOLS)
            store.save_account_state.assert_called()


class DashboardRulesTests(unittest.TestCase):
    def test_rules_match_live_constants_not_old_blurb(self):
        from hedge_fund.dashboard.report import strategy_rules_section
        from hedge_fund.risk.managed import RISK_FRAC, MAX_OPEN_RISK_FRAC, MAX_DRAWDOWN
        from hedge_fund.trading.loop import ATR_STOP_MULT, ATR_PERIOD, STOP_FLOOR_FRAC
        from hedge_fund.trading.constants import GRADUATED_PAPER, QUAL_TIMEFRAME, TRADE_EVALUATION_LIMIT

        html = strategy_rules_section()
        self.assertNotIn("2.5% below entry (hard)", html)
        self.assertNotIn("Longs only in RISK_ON / NEUTRAL", html)
        self.assertNotIn("LIVE (A/B winner)", html)
        self.assertIn("regime=None", html)
        self.assertIn("graduated paper", html.lower())
        self.assertNotIn("baseline=None", html)
        self.assertIn("buy-and-hold", html.lower())
        self.assertIn(f"{ATR_STOP_MULT:.1f}× ATR({ATR_PERIOD})", html)
        self.assertIn(f"{STOP_FLOOR_FRAC:.1%}", html)
        self.assertIn(f"{RISK_FRAC:.0%}", html)
        self.assertIn(f"{MAX_OPEN_RISK_FRAC:.0%}", html)
        self.assertIn(f"{MAX_DRAWDOWN:.0%}", html)
        self.assertIn(str(TRADE_EVALUATION_LIMIT), html)
        self.assertIn("TradingLoop.run_cycle", html)
        self.assertIn(GRADUATED_PAPER, html)
        self.assertNotIn("READY_FOR_LIVE", html)
        self.assertIn(QUAL_TIMEFRAME, html)
        self.assertNotIn("5m history is not the admit bar", html)
        self.assertIn("Every 300s", html)
        self.assertIn("around the clock", html)
        self.assertNotIn("07–21", html)
        self.assertNotIn("Discover batch 30", html)


class SharedCycleTests(unittest.TestCase):
    def test_run_cycle_uses_atr_stop_and_calibration_not_fixed_book(self):
        from hedge_fund.backtest.strategies import atr as atr_fn
        from hedge_fund.brokers.paper import PaperBroker, TAKER_FEE
        from hedge_fund.calibration import CalibrationStore
        from hedge_fund.data.binance import Candle
        from hedge_fund.risk.managed import RiskManager
        from hedge_fund.signals.momentum import Features, Signal
        from hedge_fund.trading.loop import (
            ATR_PERIOD,
            ATR_STOP_MULT,
            STOP_CAP_FRAC,
            STOP_FLOOR_FRAC,
            TradingLoop,
        )
        from hedge_fund.trading.store import TradeStore

        entry = 100_000.0
        candles = []
        for i in range(40):
            c = 90_000.0 + i * 200
            candles.append(Candle(ts=i, open=c, high=c + 800, low=c - 800, close=c, volume=1.0))
        candles.append(Candle(ts=40, open=entry, high=entry + 800, low=entry - 800,
                              close=entry, volume=1.0))

        class Src:
            def fetch_price(self, symbol):
                return entry

            def fetch_klines(self, symbol, timeframe="5m", limit=300, since=None):
                return candles

        long_sig = Signal(
            symbol="BTC/USDT", timeframe="5m", condition="sma_stack_long",
            features=Features(rsi=55.0, price=entry), direction="long", raw_score=0.5,
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = TradeStore(Path(tmp) / "trades.sqlite")
            calib = CalibrationStore(Path(tmp) / "calibration.json")
            broker = PaperBroker(cash=10_000.0)
            self.assertEqual(broker.taker_fee, TAKER_FEE)
            loop = TradingLoop(Src(), broker, RiskManager(), calib, store=store,
                               strategy="sma_stack", regime=None)
            with patch("hedge_fund.trading.loop.compute_signal", return_value=long_sig):
                results = loop.run_cycle(["BTC/USDT"])

        enters = [r for r in results if r.action == "ENTER"]
        self.assertTrue(enters, f"expected ENTER, got {[(r.action, r.reason) for r in results]}")
        entered = enters[0]
        # Not the isolated-runner constant 0.60; heuristic proposal is blended in.
        self.assertNotAlmostEqual(entered.probability, 0.60, places=4)
        self.assertGreater(entered.probability, 0.5)
        self.assertLessEqual(entered.probability, 0.8)

        stop = broker.lots[0].stop_loss
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        closes = [c.close for c in candles]
        a = atr_fn(highs, lows, closes, ATR_PERIOD)
        expected_dist = max(entry * STOP_FLOOR_FRAC, min(entry * STOP_CAP_FRAC, ATR_STOP_MULT * a))
        self.assertAlmostEqual(stop, entry - expected_dist, places=4)
        # Distinct from the old isolated 2.5% book.
        self.assertNotAlmostEqual(stop, entry * (1 - 0.025), places=2)

        snaps = store.equity_history()
        self.assertTrue(snaps)
        self.assertIsNotNone(snaps[-1]["baseline"])
        self.assertGreater(snaps[-1]["baseline"], 0)


if __name__ == "__main__":
    unittest.main()
