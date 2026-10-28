"""Shared 5m tape: one hash for every worker, provenance on ingest."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
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
                _write_span(prod, _ms(2026, 10, 7, 22, 0), _ms(2026, 10, 8, 1, 0))
                clear_manifest_cache()
                env = {
                    "PAPER_STATE": str(prod),
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                }
                with patch.dict(os.environ, env):
                    from hedge_fund.trading.price_history import (
                        clear_served_tape,
                        publish_shared_tape,
                    )

                    published = publish_shared_tape(prod)
                    self.assertIsNotNone(published)
                    clear_served_tape(prod)
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


class PersistedTapeTests(unittest.TestCase):
    def tearDown(self):
        clear_manifest_cache()

    def _root(self, tmp: str) -> Path:
        root = Path(tmp) / "state"
        _write_span(root, _ms(2026, 10, 7, 22, 0), _ms(2026, 10, 8, 1, 0))
        return root

    def test_cold_start_with_a_file_serves_without_rebuilding(self):
        from hedge_fund.trading.price_history import (
            clear_served_tape,
            publish_shared_tape,
            served_history,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            clear_manifest_cache()
            expected = manifest_for(root)
            publish_shared_tape(root)
            clear_manifest_cache()
            clear_served_tape(root)
            with patch(
                "hedge_fund.trading.price_history.canonical_arrays",
                side_effect=AssertionError("request rebuilt the tape"),
            ):
                started = time.perf_counter()
                manifest, blob = served_history(root)
                elapsed = time.perf_counter() - started
            self.assertEqual(manifest["sha256"], expected["sha256"])
            self.assertEqual(manifest["data_end"], _ms(2026, 10, 8))
            self.assertGreater(len(blob), 0)
            self.assertLess(elapsed, 0.5)

    def test_cold_start_without_a_file_is_503_and_schedules_one_build(self):
        from hedge_fund.web.server import Handler
        from hedge_fund.trading.price_history import clear_served_tape

        calls = {"n": 0}

        def schedule(state_dir=None):
            calls["n"] += 1

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        port = httpd.server_address[1]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                clear_served_tape(root)
                env = {
                    "PAPER_STATE": str(root),
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                }
                with patch.dict(os.environ, env), patch(
                    "hedge_fund.trading.price_history.schedule_tape_build",
                    side_effect=schedule,
                ):
                    req = Request(
                        f"http://127.0.0.1:{port}/api/discovery/history",
                        headers={"X-Discovery-Token": "paper-secret-token"},
                    )
                    started = time.perf_counter()
                    try:
                        urlopen(req, timeout=3)
                        self.fail("expected 503")
                    except HTTPError as err:
                        elapsed = time.perf_counter() - started
                        body = json.loads(err.read().decode())
                        retry = err.headers.get("Retry-After")
                        self.assertEqual(err.code, 503)
                        self.assertEqual(retry, "30")
                        self.assertEqual(body["error"], "tape not ready")
                        self.assertEqual(body["retry_after"], 30)
                        self.assertLess(elapsed, 0.5)
                    tape_req = Request(
                        f"http://127.0.0.1:{port}/api/discovery/history/tape",
                        headers={"X-Discovery-Token": "paper-secret-token"},
                    )
                    try:
                        urlopen(tape_req, timeout=3)
                        self.fail("expected 503")
                    except HTTPError as err2:
                        self.assertEqual(err2.code, 503)
                        self.assertEqual(err2.headers.get("Retry-After"), "30")
                self.assertEqual(calls["n"], 2)
        finally:
            httpd.shutdown()
            thread.join(timeout=3)
            httpd.server_close()

    def test_rollover_keeps_the_previous_manifest_until_the_new_one_is_ready(self):
        from hedge_fund.trading.price_history import (
            _build_pin,
            clear_served_tape,
            publish_shared_tape,
            served_history,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            clear_manifest_cache()
            publish_shared_tape(root)
            clear_served_tape(root)
            current, _blob = served_history(root)
            self.assertEqual(current["data_end"], _ms(2026, 10, 8))
            next_pin = _ms(2026, 10, 9)
            # Prebuild the next day while today is still the active pin.
            _build_pin(root, next_pin, active_pin=_ms(2026, 10, 8))
            with patch(
                "hedge_fund.trading.price_history.canonical_arrays",
                side_effect=AssertionError("rollover rebuilt on the read"),
            ):
                still, _blob = served_history(root)
            self.assertEqual(still["sha256"], current["sha256"])
            self.assertEqual(still["data_end"], _ms(2026, 10, 8))
            # A restart must not prefer that early file over the complete day.
            clear_served_tape(root)
            with patch(
                "hedge_fund.trading.price_history.canonical_arrays",
                side_effect=AssertionError("restart rebuilt the tape"),
            ):
                restarted, _blob = served_history(root)
            self.assertEqual(restarted["data_end"], _ms(2026, 10, 8))
            _append_bar(root, _ms(2026, 10, 9, 0, 0))
            clear_manifest_cache()
            _build_pin(root, next_pin, active_pin=next_pin)
            rolled, _blob = served_history(root)
            self.assertEqual(rolled["data_end"], next_pin)
            self.assertNotEqual(rolled["sha256"], current["sha256"])

    def test_a_torn_or_mismatched_pair_is_not_served(self):
        from hedge_fund.trading.price_history import (
            _blob_file,
            _manifest_file,
            clear_served_tape,
            publish_shared_tape,
            read_tape_cache,
            served_manifest,
            TapeNotReady,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            clear_manifest_cache()
            built = publish_shared_tape(root)
            pin = _ms(2026, 10, 8)
            ok = read_tape_cache(root, pin)
            self.assertIsNotNone(ok)
            self.assertNotIn("_tape_cache", ok[0])
            self.assertEqual(ok[0]["sha256"], built["sha256"])
            blob_path = _blob_file(root, pin)
            data = blob_path.read_bytes()
            # Truncated npz (crash mid-copy): length no longer matches.
            blob_path.write_bytes(data[: len(data) // 2])
            self.assertIsNone(read_tape_cache(root, pin))
            clear_served_tape(root)
            with patch("hedge_fund.trading.price_history.schedule_tape_build") as sched:
                with self.assertRaises(TapeNotReady):
                    served_manifest(root)
            self.assertEqual(sched.call_count, 1)
            # Same length, different bytes: sha256 catches it.
            blob_path.write_bytes(bytes(len(data)))
            self.assertIsNone(read_tape_cache(root, pin))
            # Leftover temp files are never read as a tape.
            (_manifest_file(root, pin).parent / f".{pin}.npz.1.tmp").write_bytes(b"x")
            blob_path.write_bytes(data)
            self.assertIsNotNone(read_tape_cache(root, pin))

    def test_a_day_missing_its_first_bar_is_final_once_the_source_moved_on(self):
        from hedge_fund.trading.price_history import (
            _build_pass,
            _read_manifest_file,
            _tape_final,
            clear_served_tape,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            # No 00:00 bar on Oct 8: data jumps from 23:55 to 00:05.
            _write_span(root, _ms(2026, 10, 7, 22, 0), _ms(2026, 10, 7, 23, 55))
            _append_bar(root, _ms(2026, 10, 8, 0, 5))
            clear_manifest_cache()
            clear_served_tape(root)
            pin = _ms(2026, 10, 8)
            stable: dict = {}
            with patch("hedge_fund.trading.price_history.time.time", return_value=(pin + 3600_000) / 1000):
                _build_pass(root, stable)
            head = _read_manifest_file(root, pin)
            self.assertIsNotNone(head)
            self.assertTrue(_tape_final(head[0], head[1], pin))
            _append_bar(root, _ms(2026, 10, 8, 0, 10))
            with patch(
                "hedge_fund.trading.price_history._launch_pin_build",
                side_effect=AssertionError("rebuilt a final day"),
            ), patch("hedge_fund.trading.price_history.time.time", return_value=(pin + 3700_000) / 1000):
                _build_pass(root, stable)

    def test_old_days_are_pruned(self):
        from hedge_fund.trading.price_history import (
            _build_pin,
            _cached_pin_numbers,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            _write_span(root, _ms(2026, 10, 2, 22, 0), _ms(2026, 10, 8, 1, 0))
            clear_manifest_cache()
            base = _ms(2026, 10, 8)
            for k in range(5):
                _build_pin(root, base - k * DAY_MS, active_pin=base)
            _build_pin(root, base, active_pin=base)
            self.assertEqual(
                _cached_pin_numbers(root),
                [base, base - DAY_MS, base - 2 * DAY_MS],
            )

    def test_tape_endpoint_streams_the_file(self):
        from hedge_fund.web.server import Handler
        from hedge_fund.trading.price_history import (
            clear_served_tape,
            publish_shared_tape,
            read_tape_cache,
        )

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        port = httpd.server_address[1]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = self._root(tmp)
                clear_manifest_cache()
                publish_shared_tape(root)
                clear_served_tape(root)
                _man, expected = read_tape_cache(root, _ms(2026, 10, 8))
                env = {
                    "PAPER_STATE": str(root),
                    "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
                }
                with patch.dict(os.environ, env), patch(
                    "hedge_fund.trading.price_history.schedule_tape_build",
                ):
                    req = Request(
                        f"http://127.0.0.1:{port}/api/discovery/history/tape",
                        headers={"X-Discovery-Token": "paper-secret-token"},
                    )
                    with urlopen(req, timeout=5) as resp:
                        body = resp.read()
                        self.assertEqual(resp.status, 200)
                        self.assertEqual(int(resp.headers["Content-Length"]), len(expected))
                self.assertEqual(body, expected)
        finally:
            httpd.shutdown()
            thread.join(timeout=3)
            httpd.server_close()

    def test_prebuild_window_includes_the_next_pin(self):
        from hedge_fund.trading.price_history import PREBUILD_LEAD_MS, pins_to_build

        pin = _ms(2026, 10, 8)
        noon = pin + 12 * 60 * 60 * 1000
        self.assertEqual(pins_to_build(noon, pin, set()), [pin])
        almost = pin + DAY_MS - 10 * 60 * 1000
        self.assertLessEqual(pin + DAY_MS - almost, PREBUILD_LEAD_MS)
        self.assertEqual(pins_to_build(almost, pin, {pin}), [pin + DAY_MS])
        self.assertEqual(pins_to_build(almost, pin, {pin, pin + DAY_MS}), [])

    def test_worker_keeps_the_local_tape_only_on_a_matching_503_and_honours_retry_after(self):
        import argparse

        from scripts.discovery_worker import (
            HistoryUnavailable,
            RemoteHTTPError,
            _run_loop,
            ensure_shared_history,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_span(root, _ms(2026, 10, 7, 22, 0), _ms(2026, 10, 8, 1, 0))
            clear_manifest_cache()
            local = manifest_for(root)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                with patch(
                    "scripts.discovery_worker._http_json",
                    side_effect=RemoteHTTPError(
                        "GET http://prod/api/discovery/history -> 503: tape not ready",
                        code=503,
                        retry_after=12,
                        body=json.dumps({"error": "tape not ready", "retry_after": 12}),
                    ),
                ):
                    # No fingerprint in the 503: wait, do not evaluate a
                    # tape prod may not accept.
                    with self.assertRaises(HistoryUnavailable) as unnamed:
                        ensure_shared_history("http://prod", "token")
                self.assertEqual(unnamed.exception.retry_after, 12)
                with patch(
                    "scripts.discovery_worker._http_json",
                    side_effect=RemoteHTTPError(
                        "GET http://prod/api/discovery/history -> 503: tape not ready",
                        code=503,
                        retry_after=12,
                        body=json.dumps({"sha256": local["sha256"], "retry_after": 12}),
                    ),
                ) as fetched:
                    kept = ensure_shared_history("http://prod", "token")
                self.assertEqual(fetched.call_count, 1)
                self.assertEqual(kept["sha256"], local["sha256"])
                self.assertEqual(kept["provenance"], "shared")
                with patch(
                    "scripts.discovery_worker._http_json",
                    side_effect=RemoteHTTPError(
                        "GET http://prod/api/discovery/history -> 503: new tape",
                        code=503,
                        retry_after=17,
                        body=json.dumps({"sha256": "ab" * 32, "retry_after": 17}),
                    ),
                ):
                    with self.assertRaises(HistoryUnavailable) as raised:
                        ensure_shared_history("http://prod", "token")
                self.assertEqual(raised.exception.retry_after, 17)

        sleeps: list[int] = []
        calls = {"n": 0}

        def boom(**_kwargs):
            calls["n"] += 1
            if calls["n"] > 1:
                raise KeyboardInterrupt()
            raise HistoryUnavailable("tape not ready", retry_after=17)

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
        self.assertEqual(sleeps, [17])


class SubprocessTapeBuildTests(unittest.TestCase):
    def tearDown(self):
        clear_manifest_cache()

    def test_parent_does_not_load_the_tape_arrays(self):
        from hedge_fund.trading import price_history as history

        calls = {"n": 0}
        real = history._source_array

        def wrapped(*args, **kwargs):
            calls["n"] += 1
            return real(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            _write_span(root, _ms(2026, 10, 7, 22, 0), _ms(2026, 10, 8, 1, 0))
            clear_manifest_cache()
            with patch.object(history, "_source_array", wrapped):
                history._build_pass(root, {})
            self.assertEqual(calls["n"], 0)
            head = history._read_manifest_file(root, _ms(2026, 10, 8))
            self.assertIsNotNone(head)
            self.assertEqual(head[0]["data_end"], _ms(2026, 10, 8))
