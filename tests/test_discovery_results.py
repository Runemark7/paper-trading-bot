"""Uncapped discovery results: header counts, seek paging, background backfill."""
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
from urllib.error import HTTPError
from urllib.request import urlopen

from hedge_fund.trading.constants import DISCOVERY_LOG_CAP
from hedge_fund.trading.densify import next_densify_batch, order_seeds
from hedge_fund.trading.discovery import (
    append_discovery_evaluations,
    load_discovery_log,
    save_discovery_log,
)
from hedge_fund.trading.discovery_results import (
    DISCOVERY_GATE_TAG,
    DISCOVERY_RESULTS,
    DISCOVERY_RESULTS_IX,
    backfill_discovery_results,
    peek_result_counts,
    records_by_name,
    write_result_counts_header,
)
from hedge_fund.trading.tested_index import (
    ensure_tested_index,
    load_tested_index,
    load_tested_metrics,
    save_tested_index,
)
from hedge_fund.web.discovery import build_discovery_summary, discovery_results_response


OLD_PASSES = (
    "h1_ema_abv_20&mom_18b_gt2pc",
    "h1_ema_abv_24&mom_18b_gt2pc",
    "h1_ema_abv_30&mom_18b_gt2pc",
    "dip_24b_lt5pc",
)
_SLIM_KEYS = {"sharpe", "trades", "ops_park"}


def _eval(name, *, qualified, tested_at, **extra):
    row = {
        "strategy": name,
        "tested_at": tested_at,
        "qualified": qualified,
        "sharpe": 0.8 if qualified else 0.1,
        "trades": 40 if qualified else 4,
        "test_pnl": 12.0 if qualified else -3.0,
        "bh_oos_pnl": 1.0,
        "sma_stack_oos_pnl": 2.0,
        "fail_reasons": [] if qualified else ["oos_sharpe 0.10 < 0.30"],
        "timeframe": "5m",
        "risk_policy": "rm_v1",
    }
    row.update(extra)
    return row


class MetricsStoreTests(unittest.TestCase):
    def test_flag_only_save_keeps_slim_metrics_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                save_tested_index(
                    {"keep": True, "park": False},
                    {
                        "keep": {"sharpe": 1.2, "trades": 40, "fail_reasons": ["kept"]},
                        "park": {"sharpe": 0.0, "trades": 0, "ops_park": True},
                    },
                )
                save_tested_index({"keep": False, "park": False})
                metrics = load_tested_metrics()
                self.assertEqual(metrics["keep"], {"sharpe": 1.2, "trades": 40})
                self.assertEqual(metrics["park"], {"sharpe": 0.0, "trades": 0, "ops_park": True})
                self.assertFalse(load_tested_index()["keep"])

    def test_index_only_pass_is_a_partial_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_tested.json").write_text(json.dumps({
                "v": 1,
                "names": {"h1_ema_abv_24&mom_18b_gt2pc": 1, "don_hi_12": 0},
            }))
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("don_hi_12", qualified=False, tested_at="2026-10-08T00:00:00+00:00"),
            ]))
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                backfill_discovery_results()
                ensure_tested_index()
                counts = peek_result_counts()
                metrics = load_tested_metrics()
                rows = records_by_name()
                index = load_tested_index()
                seeds = order_seeds(index, metrics)
            self.assertEqual(counts["unique"], 2)
            self.assertEqual(counts["tested_pass"], 1)
            self.assertEqual(counts["tested_fail"], 1)
            self.assertEqual(set(metrics), {"don_hi_12"})
            self.assertTrue(set(metrics["don_hi_12"]) <= _SLIM_KEYS)
            stub = rows["h1_ema_abv_24&mom_18b_gt2pc"]
            self.assertTrue(stub["qualified"])
            self.assertTrue(stub["partial"])
            self.assertEqual(stub["gate"], "index_only")
            self.assertEqual(
                {name for name, qual in index.items() if qual},
                {name for name, row in rows.items() if row["qualified"]},
            )
            self.assertTrue(index["h1_ema_abv_24&mom_18b_gt2pc"])
            self.assertNotIn("h1_ema_abv_24&mom_18b_gt2pc", seeds)
            self.assertEqual(seeds, [])

    def test_backfill_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("don_hi_12", qualified=True, tested_at="2026-10-08T01:00:00+00:00"),
            ]))
            (root / "discovery_tested.json").write_text(json.dumps({
                "v": 1,
                "names": {"don_hi_12": 1},
                "metrics": {"don_hi_12": {"sharpe": 0.8, "trades": 40}},
            }))
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                backfill_discovery_results()
                index_bytes = (root / "discovery_tested.json").read_bytes()
                results_bytes = (root / DISCOVERY_RESULTS).read_bytes()
                seek_bytes = (root / DISCOVERY_RESULTS_IX).read_bytes()
                backfill_discovery_results()
                self.assertEqual((root / "discovery_tested.json").read_bytes(), index_bytes)
                self.assertEqual((root / DISCOVERY_RESULTS).read_bytes(), results_bytes)
                self.assertEqual((root / DISCOVERY_RESULTS_IX).read_bytes(), seek_bytes)
                row = records_by_name()["don_hi_12"]
                self.assertEqual(row["gate"], "backfill")
                self.assertTrue(set(load_tested_metrics()["don_hi_12"]) <= _SLIM_KEYS)

    def test_backfill_does_not_take_the_discovery_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("don_hi_12", qualified=False, tested_at="2026-10-08T00:00:00+00:00"),
            ]))
            (root / "discovery_tested.json").write_text(json.dumps({
                "v": 1,
                "names": {"don_hi_12": 0},
            }))

            def boom(*_args, **_kwargs):
                raise AssertionError("discovery lock")

            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                with patch("hedge_fund.trading.store.paper_state_lock", boom), \
                     patch("hedge_fund.trading.tested_index.paper_state_lock", boom):
                    backfill_discovery_results()
                counts = peek_result_counts()
            self.assertEqual(counts["unique"], 1)
            self.assertEqual(counts["tested_fail"], 1)

    def test_counts_follow_the_names_map_when_the_line_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_tested.json").write_text(json.dumps({
                "v": 1,
                "names": {"flip_me": 0},
                "metrics": {"flip_me": {"sharpe": 0.4, "trades": 10}},
            }))
            (root / DISCOVERY_RESULTS).write_text(json.dumps({
                "strategy": "flip_me",
                "qualified": True,
                "fail_reasons": [],
                "sharpe": 0.4,
                "trades": 10,
                "tested_at": "2026-10-08T00:00:00+00:00",
                "gate": "backfill",
            }) + "\n")
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                backfill_discovery_results()
                counts = peek_result_counts()
                row = records_by_name()["flip_me"]
                index = load_tested_index()
                seeds = order_seeds(index, load_tested_metrics())
                self.assertEqual(counts["tested_pass"], 0)
                self.assertEqual(counts["tested_fail"], 1)
                self.assertFalse(row["qualified"])
                self.assertEqual(row["fail_reasons"], [])
                self.assertEqual(load_tested_metrics()["flip_me"], {"sharpe": 0.4, "trades": 10})
                self.assertTrue(set(seeds) <= set(index))
                self.assertFalse(index["flip_me"])


class CapTests(unittest.TestCase):
    def test_over_cap_keeps_exact_pass_fail_and_old_seed(self):
        self.assertGreaterEqual(DISCOVERY_LOG_CAP, 10000)
        fails = [
            _eval(f"fail_{i}", qualified=False, tested_at=f"2026-10-01T00:00:{i % 60:02d}+00:00")
            for i in range(DISCOVERY_LOG_CAP)
        ]
        # Newest first. The cap keeps this prefix and drops the old passes.
        old = [
            _eval(
                name,
                qualified=True,
                tested_at=f"2026-09-01T00:00:0{i}+00:00",
                windows=[{"test_pnl": 1.0, "test_trades": 10, "sharpe": 0.4}],
                regimes_tested=8,
            )
            for i, name in enumerate(OLD_PASSES)
        ]
        recent = _eval(
            "recent_pass",
            qualified=True,
            tested_at="2026-10-08T12:00:00+00:00",
            regimes_tested=23,
            test_pnl=40.0,
            bh_oos_pnl=5.0,
        )
        records = [recent, *fails, *old]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                append_discovery_evaluations(records)
                log = load_discovery_log()
                log_names = {row["strategy"] for row in log}
                self.assertEqual(len(log), DISCOVERY_LOG_CAP)
                counts = peek_result_counts()
                metrics = load_tested_metrics()
                rows = records_by_name()
                summary = build_discovery_summary(lists=False)
                index = load_tested_index()
                seeds = order_seeds(index, metrics, rows)
                taken = set(index)
                batch, _exhausted = next_densify_batch(taken_names=taken, n=8)
                self.assertTrue(batch)
                self.assertTrue(all(name not in taken for name in batch))
        self.assertEqual(seeds, ["recent_pass"])
        for name in OLD_PASSES:
            self.assertNotIn(name, log_names)
            self.assertNotIn(name, seeds)
            self.assertTrue(index[name])
            self.assertTrue(rows[name]["qualified"])
            self.assertEqual(rows[name]["gate"], DISCOVERY_GATE_TAG)
            self.assertIn("windows", rows[name])
            self.assertTrue(set(metrics[name]) <= _SLIM_KEYS)
            self.assertNotIn("windows", metrics[name])
            self.assertNotIn("fail_reasons", metrics[name])
        self.assertTrue(set(seeds) <= set(index))
        self.assertEqual(
            {name for name, qual in index.items() if qual},
            {name for name, row in rows.items() if row["qualified"]},
        )
        self.assertEqual(counts["tested_pass"], 1 + len(OLD_PASSES))
        self.assertEqual(counts["tested_fail"], DISCOVERY_LOG_CAP)
        self.assertEqual(counts["unique"], DISCOVERY_LOG_CAP + 1 + len(OLD_PASSES))
        self.assertEqual(summary["counts"]["tested_pass"], counts["tested_pass"])
        self.assertEqual(summary["counts"]["tested_fail"], counts["tested_fail"])
        self.assertEqual(summary["counts"]["tested"], counts["unique"])
        self.assertEqual(summary["counts"]["unique_tested"], counts["unique"])
        self.assertEqual(summary["counts"]["tested_index"], counts["unique"])
        self.assertEqual(summary["counts"]["log_rows"], DISCOVERY_LOG_CAP)

    def test_results_page_seeks_instead_of_reading_the_file(self):
        fat = "x" * 4000
        rows = [
            _eval(
                f"name_{i:02d}",
                qualified=(i % 5 == 0),
                tested_at=f"2026-10-08T00:{i:02d}:00+00:00",
                windows=[{"blob": fat}],
            )
            for i in range(40)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                append_discovery_evaluations(rows, cap=10)
                body = (root / DISCOVERY_RESULTS).stat().st_size
                self.assertGreater(body, 100_000)
                first = discovery_results_response({"offset": "0", "limit": "10"})
                second = discovery_results_response({"offset": "10", "limit": "10"})
                passes = discovery_results_response({"status": "pass", "limit": "100"})
                read_bytes = {"n": 0}
                real_open = Path.open

                def spy(self, mode="r", *args, **kwargs):
                    fh = real_open(self, mode, *args, **kwargs)
                    if self.name not in (DISCOVERY_RESULTS, DISCOVERY_RESULTS_IX):
                        return fh

                    class Counting:
                        def read(self, n=-1):
                            data = fh.read(n)
                            read_bytes["n"] += len(data)
                            return data

                        def seek(self, *a, **k):
                            return fh.seek(*a, **k)

                        def __enter__(self):
                            return self

                        def __exit__(self, *a):
                            fh.close()
                            return False

                    return Counting()

                with patch.object(Path, "open", spy):
                    again = discovery_results_response({"offset": "30", "limit": "2"})
                self.assertLess(read_bytes["n"], body // 4)
                self.assertGreater(read_bytes["n"], 0)
        self.assertEqual(first["total"], 40)
        self.assertEqual(len(first["results"]), 10)
        self.assertEqual(len(second["results"]), 10)
        self.assertNotEqual(
            [row["strategy"] for row in first["results"]],
            [row["strategy"] for row in second["results"]],
        )
        self.assertTrue(first["results"][0]["tested_at"] >= second["results"][0]["tested_at"])
        self.assertEqual(passes["total"], 8)
        self.assertTrue(all(row["qualified"] for row in passes["results"]))
        self.assertEqual(len(again["results"]), 2)
        self.assertEqual(again["results"][0]["strategy"], "name_09")

    def test_http_results_route_rejects_bad_status(self):
        from hedge_fund.web.server import Handler

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(json.dumps([
                _eval("recent_pass", qualified=True, tested_at="2026-10-08T12:00:00+00:00"),
                _eval("old_fail", qualified=False, tested_at="2026-10-08T11:00:00+00:00"),
            ]))
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                    backfill_discovery_results()
                    body = json.loads(urlopen(
                        f"http://127.0.0.1:{port}/api/discovery/results?limit=1&status=pass",
                        timeout=5,
                    ).read().decode())
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(
                            f"http://127.0.0.1:{port}/api/discovery/results?status=maybe",
                            timeout=5,
                        )
            finally:
                httpd.shutdown()
                thread.join(timeout=3)
                httpd.server_close()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["results"][0]["strategy"], "recent_pass")
        self.assertEqual(raised.exception.code, 400)

    def test_requalify_updates_the_stored_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                append_discovery_evaluations([
                    _eval("flip_me", qualified=True, tested_at="2026-10-08T12:00:00+00:00"),
                ])
                row = dict(load_discovery_log()[0])
                row["qualified"] = False
                row["fail_reasons"] = ["oos_pnl 1.00 <= bh 2.00"]
                row["requalify_source"] = "aggregate_oos_no_window_veto"
                save_discovery_log([row])
                stored = records_by_name()["flip_me"]
                metrics = load_tested_metrics()["flip_me"]
                counts = peek_result_counts()
                index = load_tested_index()
        self.assertFalse(stored["qualified"])
        self.assertEqual(stored["fail_reasons"], ["oos_pnl 1.00 <= bh 2.00"])
        self.assertEqual(stored["gate"], "aggregate_oos_no_window_veto")
        self.assertTrue(set(metrics) <= _SLIM_KEYS)
        self.assertNotIn("fail_reasons", metrics)
        self.assertEqual(counts["tested_pass"], 0)
        self.assertEqual(counts["tested_fail"], 1)
        self.assertFalse(index["flip_me"])

    def test_summary_build_with_15k_names_stays_under_a_second(self):
        n = 15_000
        names = {f"n_{i}": 0 for i in range(n - 1)}
        names["keep"] = 1
        doc = {
            "v": 1,
            "names": names,
            "metrics": {"keep": {"sharpe": 1.0, "trades": 40}},
        }
        from hedge_fund.trading.atom_lift import peek_refill_strategy

        peek_refill_strategy()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_tested.json").write_text(json.dumps(doc, separators=(",", ":")))
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                write_result_counts_header(tested_pass=1, tested_fail=n - 1)
                opened: list[str] = []
                real_open = Path.open

                def spy(self, mode="r", *args, **kwargs):
                    opened.append(self.name)
                    return real_open(self, mode, *args, **kwargs)

                with patch.object(Path, "open", spy):
                    started = time.perf_counter()
                    summary = build_discovery_summary(lists=False)
                    elapsed = time.perf_counter() - started
                    first_opened = list(opened)
                    opened.clear()
                    again = build_discovery_summary(lists=False)
                self.assertLess(elapsed, 1.0)
                self.assertNotIn(DISCOVERY_RESULTS, first_opened)
                self.assertNotIn(DISCOVERY_RESULTS, opened)
                self.assertEqual(summary["counts"]["tested_pass"], 1)
                self.assertEqual(summary["counts"]["tested_fail"], n - 1)
                self.assertEqual(summary["counts"]["unique_tested"], n)
                self.assertEqual(summary["counts"]["tested_index"], n)
                self.assertEqual(again["counts"]["unique_tested"], n)
                self.assertIn("unique strategy names", summary["note"])


if __name__ == "__main__":
    unittest.main()
