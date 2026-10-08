"""Atom lift from tested names, and mint steering that uses it."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from hedge_fund.trading.atom_lift import (
    EXPLORE_SHARE,
    STRATEGY_LIFT,
    STRATEGY_PENDING,
    STRATEGY_PLAIN,
    AtomEstimate,
    LiftModel,
    ensure_lift_model,
    estimate_lifts,
    explore_count,
    lift_api_payload,
    lift_payload_for_request,
    steer_candidates,
)
from hedge_fund.trading.densify import next_densify_batch, order_seeds


def _est(key: str, lift: float, *, support: int = 10, zero: float = 0.0) -> AtomEstimate:
    return AtomEstimate(
        key=key,
        level="atom",
        sharpe_lift=lift,
        trades_lift=0.0,
        support=support,
        zero_trade_rate=zero,
        source="paired",
        name_count=support,
    )


def _model(atoms: dict[str, AtomEstimate], **kwargs) -> LiftModel:
    families = kwargs.pop("families", {})
    informative = kwargs.pop("informative", True)
    return LiftModel(
        atoms=atoms,
        families=families,
        names_used=kwargs.pop("names_used", 80),
        informative=informative,
        strategy=STRATEGY_LIFT if informative else STRATEGY_PLAIN,
        **kwargs,
    )


def _paired_rows() -> list[dict]:
    rows = []
    for i in range(12):
        parent = f"h1_ema_abv_{20 + i}&mom_18b_gt2pc"
        rows.append({"strategy": parent, "sharpe": 0.10, "trades": 40})
        rows.append({"strategy": parent + "&sma_abv_30", "sharpe": 0.70, "trades": 55})
        rows.append({"strategy": parent + "&rsi_14_>60", "sharpe": -0.40, "trades": 0})
    return rows


def _fifteen_k_seconds() -> float:
    rows = []
    for i in range(7500):
        parent = f"h1_ema_abv_{i % 80}&mom_{(6 + (i % 12) * 6)}b_gt2pc&id_{i}"
        rows.append({"strategy": parent, "sharpe": 0.10, "trades": 40})
        if i % 2 == 0:
            rows.append({"strategy": parent + "&sma_abv_30", "sharpe": 0.60, "trades": 55})
        else:
            rows.append({"strategy": parent + "&rsi_14_>60", "sharpe": -0.30, "trades": 0})
    started = time.perf_counter()
    model = estimate_lifts(rows)
    elapsed = time.perf_counter() - started
    if model.atoms["sma_abv_30"].sharpe_lift <= 0:
        raise AssertionError("sma lift was not positive on the 15k set")
    if model.atoms["rsi_14_>60"].sharpe_lift >= 0:
        raise AssertionError("rsi lift was not negative on the 15k set")
    if model.atoms["sma_abv_30"].support < 5:
        raise AssertionError("sma pair support collapsed")
    return elapsed


class EstimateTests(unittest.TestCase):
    def test_fifteen_k_names_fit_in_two_seconds(self):
        self.assertLess(_fifteen_k_seconds(), 2.0)

    def test_known_good_atom_ranks_first(self):
        model = estimate_lifts(_paired_rows())
        sma = model.atoms["sma_abv_30"]
        rsi = model.atoms["rsi_14_>60"]
        self.assertEqual(sma.source, "paired")
        self.assertGreaterEqual(sma.support, 12)
        self.assertGreater(sma.sharpe_lift, 0)
        self.assertLess(rsi.sharpe_lift, 0)
        self.assertGreater(sma.trades_lift, 0)
        self.assertLess(rsi.trades_lift, 0)
        self.assertEqual(rsi.zero_trade_rate, 1.0)
        self.assertEqual(sma.zero_trade_rate, 0.0)
        ranked = sorted(model.atoms.values(), key=lambda est: -est.sharpe_lift)
        self.assertEqual(ranked[0].key, "sma_abv_30")
        self.assertEqual(ranked[-1].key, "rsi_14_>60")
        self.assertTrue(model.informative)
        self.assertEqual(model.strategy, STRATEGY_LIFT)
        family = model.families["sma_abv|(30,)"]
        self.assertGreaterEqual(family.support, 12)
        self.assertGreater(family.sharpe_lift, 0)
        payload = lift_api_payload(model)
        self.assertEqual(payload["top"][0]["atom"], "sma_abv_30")
        self.assertEqual(payload["bottom"][0]["atom"], "rsi_14_>60")
        self.assertGreaterEqual(payload["top"][0]["support"], 5)
        top_keys = {row["atom"] for row in payload["top"]}
        bottom_keys = {row["atom"] for row in payload["bottom"]}
        self.assertTrue(top_keys.isdisjoint(bottom_keys))

    def test_ridge_fallback_when_pairs_are_scarce(self):
        rows = []
        for i in range(3):
            parent = f"h1_ema_abv_{20 + i}&mom_18b_gt2pc"
            rows.append({"strategy": parent, "sharpe": 0.10, "trades": 40})
            rows.append({"strategy": parent + "&sma_abv_30", "sharpe": 0.90, "trades": 50})
        model = estimate_lifts(rows)
        sma = model.atoms["sma_abv_30"]
        self.assertLess(sma.support, 5)
        self.assertEqual(sma.source, "ridge")
        self.assertGreater(sma.sharpe_lift, 0)
        self.assertFalse(model.informative)

    def test_ops_park_rows_are_not_training(self):
        rows = _paired_rows()
        rows.append({
            "strategy": "h1_ema_abv_50&mom_18b_gt2pc&ema_abv_20",
            "sharpe": 0.0,
            "trades": 0,
            "ops_park": True,
        })
        model = estimate_lifts(rows)
        self.assertNotIn("ema_abv_20", model.atoms)

    def test_parent_match_ignores_atom_order(self):
        rows = []
        for i in range(6):
            parent = f"mom_18b_gt2pc&h1_ema_abv_{20 + i}"
            child = f"sma_abv_30&h1_ema_abv_{20 + i}&mom_18b_gt2pc"
            rows.append({"strategy": parent, "sharpe": 0.0, "trades": 30})
            rows.append({"strategy": child, "sharpe": 0.5, "trades": 40})
        model = estimate_lifts(rows)
        self.assertGreaterEqual(model.atoms["sma_abv_30"].support, 6)
        self.assertGreater(model.atoms["sma_abv_30"].sharpe_lift, 0)


class SteerTests(unittest.TestCase):
    def _goods_and_bads(self):
        goods = [f"g{i}&sma_abv_30" for i in range(15)]
        bads = [f"b{i}&rsi_14_>60" for i in range(40)]
        model = _model({
            "sma_abv_30": _est("sma_abv_30", 0.5, zero=0.0),
            "rsi_14_>60": _est("rsi_14_>60", -0.5, zero=1.0),
        })
        return goods, bads, model

    def test_exploit_prefers_positive_lift_and_explore_share(self):
        goods, bads, model = self._goods_and_bads()
        self.assertEqual(explore_count(20, EXPLORE_SHARE), 5)
        picked = steer_candidates(goods + bads, model, 20, seed=7063)
        self.assertEqual(len(picked), 20)
        self.assertEqual(picked[:15], goods)
        self.assertEqual(len(picked[15:]), 5)
        self.assertTrue(set(picked[15:]).issubset(set(bads)))
        self.assertEqual(steer_candidates(goods + bads, model, 20, seed=7063), picked)
        flat = _model(model.atoms, informative=False)
        self.assertEqual(steer_candidates(goods + bads, flat, 20, seed=7063), (goods + bads)[:20])

    def test_zero_trade_rate_downweights_tied_lift(self):
        model = _model({
            "sma_abv_30": _est("sma_abv_30", 0.2, zero=0.0),
            "ema_abv_30": _est("ema_abv_30", 0.2, zero=1.0),
        })
        picked = steer_candidates(["ema_abv_30", "sma_abv_30"], model, 1, seed=1)
        self.assertEqual(picked, ["sma_abv_30"])

    def test_family_pool_covers_an_untested_threshold(self):
        family = AtomEstimate(
            "mom|(18,)",
            "family",
            0.4,
            0.0,
            10,
            0.0,
            "paired",
            10,
        )
        model = _model(
            {"rsi_14_>60": _est("rsi_14_>60", -0.4, zero=0.8)},
            families={"mom|(18,)": family},
        )
        picked = steer_candidates(
            ["h1_ema_abv_50&rsi_14_>60", "h1_ema_abv_50&mom_18b_gt8pc"],
            model,
            1,
            seed=1,
        )
        self.assertEqual(picked, ["h1_ema_abv_50&mom_18b_gt8pc"])

    def test_refill_keeps_recipe_order_until_lift_is_informative(self):
        from hedge_fund.trading.refill import next_refill_batch

        plain = next_refill_batch(taken_names=[], n=4)
        self.assertEqual(plain[0], "h1_ema_abv_50&mom_18b_gt2pc&sma_abv_30&rsi_14_>45")
        flat = _model({"sma_abv_20": _est("sma_abv_20", 5.0)}, informative=False)
        self.assertEqual(next_refill_batch(taken_names=[], n=4, lift=flat), plain)
        live = _model({
            "sma_abv_20": _est("sma_abv_20", 2.0),
            "sma_abv_30": _est("sma_abv_30", -1.0),
        })
        steered = next_refill_batch(taken_names=[], n=4, lift=live, lift_seed=7063)
        self.assertEqual(len(steered), 4)
        self.assertTrue(all("sma_abv_20" in name for name in steered[:3]))
        self.assertNotEqual(steered[0], plain[0])
        self.assertEqual(
            next_refill_batch(taken_names=[], n=4, lift=live, lift_seed=7063),
            steered,
        )


class SeedAndDensifyTests(unittest.TestCase):
    def test_seeds_prefer_recent_sharpe_over_historical_passes(self):
        high = "h1_ema_abv_50&mom_18b_gt4pc"
        low = "h1_ema_abv_50&mom_18b_gt2pc&sma_abv_30&rsi_14_>50"
        dead = "h1_ema_abv_40&mom_18b_gt2pc"
        index = {high: False, low: True, dead: False}
        metrics = {
            high: {"sharpe": 1.1, "trades": 80},
            low: {"sharpe": 0.05, "trades": 40},
            dead: {"sharpe": -0.2, "trades": 10},
        }
        seeds = order_seeds(index, metrics)
        self.assertEqual(seeds[0], high)
        self.assertIn(low, seeds)
        self.assertNotIn(dead, seeds)
        plain = order_seeds(index, None)
        self.assertEqual(plain, [low])

    def test_densify_without_lift_matches_heuristic_and_with_lift_prefers_it(self):
        seed = "h1_ema_abv_50&mom_18b_gt2pc&sma_abv_30"
        index = {seed: True}
        plain, plain_done = next_densify_batch(taken_names=[], n=4, index=index)
        self.assertFalse(plain_done)
        self.assertEqual(plain[0], "h1_ema_abv_40&mom_18b_gt2pc&sma_abv_30")
        live = _model({
            "sma_abv_25": _est("sma_abv_25", 1.0),
            "h1_ema_abv_40": _est("h1_ema_abv_40", -1.0),
        })
        steered, done = next_densify_batch(
            taken_names=[],
            n=4,
            index=index,
            lift=live,
            lift_seed=7063,
        )
        self.assertFalse(done)
        self.assertIn("sma_abv_25", steered[0])
        self.assertTrue(all("h1_ema_abv_40" not in name for name in steered[:3]))
        self.assertNotEqual(steered[0], plain[0])
        again, _ = next_densify_batch(
            taken_names=[],
            n=4,
            index=index,
            lift=live,
            lift_seed=7063,
        )
        self.assertEqual(again, steered)

    def test_metrics_put_the_higher_sharpe_seed_first(self):
        high = "h1_ema_abv_60&mom_18b_gt2pc"
        low = "h1_ema_abv_50&mom_18b_gt2pc&sma_abv_30&rsi_14_>50"
        index = {low: True, high: False}
        metrics = {
            high: {"sharpe": 2.0, "trades": 100},
            low: {"sharpe": 0.01, "trades": 40},
        }
        steered, _ = next_densify_batch(taken_names=[], n=1, index=index, metrics=metrics)
        plain, _ = next_densify_batch(taken_names=[], n=1, index=index)
        self.assertEqual(steered[0], "h1_ema_abv_50&mom_18b_gt2pc")
        self.assertTrue(plain[0].startswith("h1_ema_abv_40&"))
        self.assertIn("rsi_14_>50", plain[0])


class IndexAndApiTests(unittest.TestCase):
    def test_metrics_survive_log_trim_and_feed_lift(self):
        from hedge_fund.trading.discovery import append_discovery_evaluations, load_discovery_log
        from hedge_fund.trading.tested_index import load_tested_metrics

        kept = "h1_ema_abv_50&mom_18b_gt2pc&sma_abv_30"
        trimmed = "h1_ema_abv_40&mom_18b_gt2pc"
        rows = [
            {"strategy": kept, "qualified": True, "sharpe": 0.8, "trades": 40},
            {"strategy": trimmed, "qualified": False, "sharpe": -0.2, "trades": 4},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                append_discovery_evaluations(rows, cap=1)
                self.assertEqual(load_discovery_log()[0]["strategy"], kept)
                metrics = load_tested_metrics()
                self.assertEqual(metrics[trimmed]["sharpe"], -0.2)
                self.assertEqual(metrics[kept]["trades"], 40)
                model = ensure_lift_model()
                self.assertEqual(model.names_used, 2)
                self.assertEqual(model.strategy, STRATEGY_PLAIN)
                again = ensure_lift_model()
                self.assertEqual(again.fingerprint, model.fingerprint)
                append_discovery_evaluations(
                    [{"strategy": "h1_ema_abv_30&mom_18b_gt2pc", "qualified": False, "sharpe": 0.2, "trades": 10}],
                    cap=1,
                )
                updated = ensure_lift_model()
                self.assertNotEqual(updated.fingerprint, model.fingerprint)
                self.assertEqual(updated.names_used, 3)

    def test_summary_does_not_fit_lift_and_route_serves_cache(self):
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.web.discovery import build_discovery_summary
        from hedge_fund.web.server import Handler

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with patch("hedge_fund.trading.atom_lift.estimate_lifts", side_effect=AssertionError("fit")):
                    summary = build_discovery_summary(lists=False)
                self.assertEqual(summary["refill"]["strategy"], STRATEGY_PENDING)
                (root / "discovery_log.json").write_text(json.dumps(_paired_rows()))
                with patch("hedge_fund.trading.atom_lift.estimate_lifts", side_effect=AssertionError("fit")):
                    pending = lift_payload_for_request()
                self.assertEqual(pending["strategy"], STRATEGY_PENDING)
                self.assertEqual(pending["top"], [])
                ensure_lift_model()
                self.assertEqual(build_discovery_summary(lists=False)["refill"]["strategy"], STRATEGY_LIFT)
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                port = httpd.server_address[1]
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                try:
                    body = json.loads(urlopen(
                        f"http://127.0.0.1:{port}/api/discovery/lift",
                        timeout=10,
                    ).read().decode())
                    with patch(
                        "hedge_fund.trading.leases.discovery_universe",
                        return_value=["don_hi_12"],
                    ):
                        with patch("hedge_fund.trading.atom_lift.estimate_lifts", side_effect=AssertionError("fit")):
                            claim_discovery_batch("linux-1", 1, parallel=1)
                finally:
                    httpd.shutdown()
                    thread.join(timeout=3)
                    httpd.server_close()
        self.assertEqual(body["top"][0]["atom"], "sma_abv_30")
        self.assertEqual(body["bottom"][0]["atom"], "rsi_14_>60")
        self.assertEqual(body["strategy"], STRATEGY_LIFT)
        self.assertEqual(body["explore_share"], EXPLORE_SHARE)
        self.assertTrue(body["paper_only"])
        self.assertGreaterEqual(body["names_used"], 36)
        src = Path(__file__).resolve().parents[1].joinpath("hedge_fund/web/server.py").read_text()
        self.assertIn('route == "/api/discovery/lift"', src)
        self.assertIn("lift_payload_for_request", src)
        self.assertIn("cached_discovery_summary", src)

    def test_claim_path_does_not_fit_and_refresh_is_rate_limited(self):
        import hedge_fund.trading.atom_lift as lift

        root = Path(__file__).resolve().parents[1]
        for rel in (
            "hedge_fund/trading/leases.py",
            "hedge_fund/trading/ingest.py",
            "hedge_fund/trading/refill.py",
        ):
            text = (root / rel).read_text()
            self.assertNotIn("ensure_lift_model", text)
            self.assertNotIn("schedule_lift_refresh", text)
            self.assertNotIn("estimate_lifts", text)
        self.assertGreaterEqual(lift.LIFT_REFRESH_SECONDS, 300.0)

        class _InlineThread:
            def __init__(self, target, name=None, daemon=None):
                self._target = target

            def start(self):
                self._target()

        prev_at = lift._last_refresh_at
        prev_running = lift._refresh_running
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                    with patch.object(lift, "schedule_lift_refresh", side_effect=AssertionError("sched")):
                        with patch.object(lift, "estimate_lifts", side_effect=AssertionError("fit")):
                            model = lift.lift_model_for_mint()
                    self.assertEqual(model.strategy, STRATEGY_PLAIN)
                    self.assertEqual(model.names_used, 0)
                    calls = {"n": 0}

                    def _fake_fit():
                        calls["n"] += 1
                        return LiftModel()

                    lift._refresh_running = False
                    lift._last_refresh_at = time.monotonic()
                    with patch.object(lift.threading, "Thread", _InlineThread):
                        with patch.object(lift, "ensure_lift_model", side_effect=_fake_fit):
                            lift.refresh_lift_cache()
                            self.assertEqual(calls["n"], 0)
                            lift._last_refresh_at = 0.0
                            lift.refresh_lift_cache()
                            lift.refresh_lift_cache()
                            lift.schedule_lift_refresh()
                    self.assertEqual(calls["n"], 1)
        finally:
            lift._last_refresh_at = prev_at
            lift._refresh_running = prev_running


if __name__ == "__main__":
    unittest.main()
