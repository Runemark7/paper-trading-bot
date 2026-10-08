"""Shared 5m tape: one hash for every worker, provenance on ingest."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

from hedge_fund.trading.constants import QUAL_SYMBOLS
from hedge_fund.trading.discovery import append_discovery_evaluations
from hedge_fund.trading.discovery_results import records_by_name
from hedge_fund.trading.live_tape import TAPE_DTYPE, load_array, save_array
from hedge_fund.trading.price_history import (
    DAY_MS,
    clear_manifest_cache,
    floor_utc_day,
    install_tape_blob,
    manifest_for,
    tape_blob,
)


def _bars(start: int, n: int, price: float) -> np.ndarray:
    arr = np.empty(n, dtype=TAPE_DTYPE)
    for i in range(n):
        px = price + i
        arr[i] = (start + i * 300_000, px, px + 1, px - 1, px, 1.0)
    return arr


def _ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    moment = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def _write_tape(root: Path, *, end_shift: int = 0) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for i, symbol in enumerate(QUAL_SYMBOLS):
        save_array(symbol, _bars(1_700_000_000_000 + end_shift, 8, 100.0 + i), root)


def _write_span(root: Path, start_ms: int, end_ms: int) -> None:
    """Inclusive 5m bars from ``start_ms`` through ``end_ms``."""
    root.mkdir(parents=True, exist_ok=True)
    n = (end_ms - start_ms) // 300_000 + 1
    for i, symbol in enumerate(QUAL_SYMBOLS):
        save_array(symbol, _bars(start_ms, n, 100.0 + i), root)


def _append_bar(root: Path, ts: int) -> None:
    for i, symbol in enumerate(QUAL_SYMBOLS):
        prev = load_array(symbol, root)
        extra = _bars(ts, 1, 100.0 + i + len(prev))
        save_array(symbol, np.concatenate([prev, extra]), root)


def _eval(name: str, **extra) -> dict:
    row = {
        "strategy": name,
        "tested_at": "2026-10-08T00:00:00+00:00",
        "qualified": False,
        "sharpe": 0.1,
        "trades": 4,
        "test_pnl": -3.0,
        "bh_oos_pnl": 1.0,
        "sma_stack_oos_pnl": 2.0,
        "fail_reasons": ["oos_sharpe 0.10 < 0.30"],
        "timeframe": "5m",
        "risk_policy": "rm_v1",
    }
    row.update(extra)
    return row


class ManifestTests(unittest.TestCase):
    def tearDown(self):
        clear_manifest_cache()

    def test_hash_is_stable_and_tracks_the_last_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tape(root)
            clear_manifest_cache()
            first = manifest_for(root)
            second = manifest_for(root)
            last = 1_700_000_000_000 + 7 * 300_000
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertEqual(len(first["sha256"]), 64)
            self.assertEqual(first["source"], "live_tape")
            self.assertEqual(first["data_end"], floor_utc_day(last))
            self.assertEqual(first["data_end"] % DAY_MS, 0)
            other = Path(tmp) / "other"
            _write_tape(other, end_shift=300_000)
            shifted = manifest_for(other)
            self.assertEqual(shifted["sha256"], first["sha256"])
            self.assertEqual(shifted["data_end"], first["data_end"])

    def test_hash_ignores_a_bar_inside_the_utc_day_and_changes_when_the_day_rolls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_span(root, _ms(2026, 10, 7, 22, 0), _ms(2026, 10, 8, 1, 0))
            clear_manifest_cache()
            with patch(
                "hedge_fund.trading.price_history.hashlib.sha256",
                wraps=hashlib.sha256,
            ) as hashed:
                first = manifest_for(root)
                built = hashed.call_count
                self.assertGreaterEqual(built, 1)
                self.assertEqual(first["data_end"], _ms(2026, 10, 8))
                symbol = QUAL_SYMBOLS[0]
                self.assertEqual(first["symbols"][symbol]["data_end"], _ms(2026, 10, 8))
                bars = first["symbols"][symbol]["bars"]
                _append_bar(root, _ms(2026, 10, 8, 1, 5))
                second = manifest_for(root)
                self.assertEqual(hashed.call_count, built)
                self.assertEqual(second["sha256"], first["sha256"])
                self.assertEqual(second["data_end"], first["data_end"])
                self.assertEqual(second["symbols"][symbol]["bars"], bars)
                _append_bar(root, _ms(2026, 10, 9, 0, 5))
                third = manifest_for(root)
                self.assertGreater(hashed.call_count, built)
            self.assertNotEqual(third["sha256"], first["sha256"])
            self.assertEqual(third["data_end"], _ms(2026, 10, 9))
            self.assertGreater(third["symbols"][symbol]["bars"], bars)

    def test_repeated_tape_downloads_reuse_the_cached_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_span(root, _ms(2026, 10, 7, 22, 0), _ms(2026, 10, 8, 1, 0))
            clear_manifest_cache()
            with patch(
                "hedge_fund.trading.price_history.np.savez_compressed",
                wraps=np.savez_compressed,
            ) as packed:
                first = tape_blob(root)
                second = tape_blob(root)
            self.assertEqual(packed.call_count, 1)
            self.assertEqual(first, second)
            self.assertGreater(len(first), 0)

    def test_install_roundtrip_matches_the_served_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            dst = Path(tmp) / "dst"
            _write_tape(src)
            clear_manifest_cache()
            blob = tape_blob(src)
            installed = install_tape_blob(blob, dst)
            self.assertEqual(installed["sha256"], manifest_for(src)["sha256"])
            self.assertEqual(installed["data_end"], manifest_for(src)["data_end"])


class ProvenanceTests(unittest.TestCase):
    def test_old_payload_is_unknown_and_stamped_payload_is_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                append_discovery_evaluations([
                    _eval("old_worker"),
                    _eval(
                        "new_worker",
                        worker_id="linux-1",
                        data_end=1_700_000_000_000,
                        data_hash="ab" * 32,
                        provenance="shared",
                    ),
                ])
                rows = records_by_name()
        old = rows["old_worker"]
        self.assertEqual(old["provenance"], "unknown")
        self.assertEqual(old["worker_id"], "unknown")
        self.assertIsNone(old["data_end"])
        self.assertIsNone(old["data_hash"])
        fresh = rows["new_worker"]
        self.assertEqual(fresh["provenance"], "shared")
        self.assertEqual(fresh["worker_id"], "linux-1")
        self.assertEqual(fresh["data_end"], 1_700_000_000_000)
        self.assertEqual(fresh["data_hash"], "ab" * 32)


class SharedHistoryHttpTests(unittest.TestCase):
    def tearDown(self):
        clear_manifest_cache()

    def _start(self):
        from hedge_fund.web.server import Handler

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread, httpd.server_address[1]

    def test_history_requires_the_ingest_token(self):
        httpd, thread, port = self._start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                prod = Path(tmp) / "prod"
                _write_tape(prod)
                clear_manifest_cache()
                env = {
                    "PAPER_STATE": str(prod),
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                }
                with patch.dict(os.environ, env):
                    bare = Request(f"http://127.0.0.1:{port}/api/discovery/history")
                    try:
                        urlopen(bare, timeout=3)
                        self.fail("expected HTTPError")
                    except HTTPError as err:
                        self.assertEqual(err.code, 401)
                    req = Request(
                        f"http://127.0.0.1:{port}/api/discovery/history",
                        headers={"X-Discovery-Token": "paper-secret-token"},
                    )
                    with urlopen(req, timeout=3) as resp:
                        remote = json.loads(resp.read().decode())
                    self.assertEqual(remote["sha256"], manifest_for(prod)["sha256"])
                    tape_req = Request(
                        f"http://127.0.0.1:{port}/api/discovery/history/tape",
                        headers={"X-Discovery-Token": "paper-secret-token"},
                    )
                    with urlopen(tape_req, timeout=3) as resp:
                        blob = resp.read()
                    self.assertEqual(blob, tape_blob(prod))
        finally:
            httpd.shutdown()
            thread.join(timeout=3)
            httpd.server_close()

    def test_worker_refreshes_when_the_local_hash_differs(self):
        from scripts.discovery_worker import ensure_shared_history

        with tempfile.TemporaryDirectory() as tmp:
            prod = Path(tmp) / "prod"
            worker = Path(tmp) / "worker"
            _write_tape(prod)
            _write_tape(worker, end_shift=DAY_MS)
            clear_manifest_cache()
            remote = manifest_for(prod)
            blob = tape_blob(prod)
            with patch.dict(os.environ, {"PAPER_STATE": str(worker)}):
                clear_manifest_cache()
                self.assertNotEqual(manifest_for(worker)["sha256"], remote["sha256"])
                with patch(
                    "scripts.discovery_worker._http_json",
                    return_value=remote,
                ), patch(
                    "scripts.discovery_worker._http_bytes",
                    return_value=blob,
                ):
                    stamped = ensure_shared_history("http://prod", "token")
                self.assertEqual(stamped["provenance"], "shared")
                self.assertEqual(stamped["sha256"], remote["sha256"])
                clear_manifest_cache()
                self.assertEqual(manifest_for(worker)["sha256"], remote["sha256"])

    def test_worker_backs_off_instead_of_exiting_on_persistent_mismatch(self):
        import argparse

        from scripts.discovery_worker import (
            HISTORY_BACKOFF_SECONDS,
            HistoryUnavailable,
            _run_loop,
            ensure_shared_history,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tape(root)
            clear_manifest_cache()
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with patch(
                    "scripts.discovery_worker._http_json",
                    return_value={"sha256": "0" * 64, "data_end": 1},
                ), patch(
                    "scripts.discovery_worker._http_bytes",
                    return_value=b"not-a-tape",
                ), patch(
                    "hedge_fund.trading.price_history.install_tape_blob",
                    return_value={"sha256": "f" * 64, "data_end": 1},
                ):
                    with self.assertRaises(HistoryUnavailable):
                        ensure_shared_history("http://prod", "token")
                with patch(
                    "scripts.discovery_worker._http_json",
                    side_effect=RuntimeError(
                        "GET http://prod/api/discovery/history -> 502: bad gateway"
                    ),
                ):
                    with self.assertRaises(HistoryUnavailable):
                        ensure_shared_history("http://prod", "token")

        sleeps: list[int] = []
        calls = {"n": 0}

        def boom(**_kwargs):
            calls["n"] += 1
            if calls["n"] > 1:
                raise KeyboardInterrupt()
            raise HistoryUnavailable("price history hash mismatch after refresh")

        args = argparse.Namespace(
            once=False,
            no_ingest=True,
            no_bootstrap=True,
            idle_sleep=5,
            pause_sleep=1,
        )
        with patch("scripts.discovery_worker.run_batch", side_effect=boom), patch(
            "scripts.discovery_worker.time.sleep",
            side_effect=lambda seconds: sleeps.append(seconds),
        ):
            with self.assertRaises(KeyboardInterrupt):
                _run_loop(
                    args,
                    workers=1,
                    max_names=1,
                    local_plan=True,
                    worker_id="linux-1",
                    token=None,
                    ingest_url=None,
                    base="http://prod",
                    farm_enabled=True,
                )
        self.assertEqual(sleeps, [HISTORY_BACKOFF_SECONDS])
        self.assertEqual(calls["n"], 2)
        self.assertGreaterEqual(HISTORY_BACKOFF_SECONDS, 60)
