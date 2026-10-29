"""Warmed BTC daily bars for library workers.

The web sends the cycle's npz. It does not resample the 5m tape.
A worker that cannot load a series from 2017-08-17 does not claim
``lib.*`` names. ``lib.sma200`` on the recorded daily bars matches the
prod cycle: DSR 0.590, PBO 0.30.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

from hedge_fund.library.daily import DAY_MS, DailyBars, empty_daily
from hedge_fund.library.daily_cache import (
    cache_path,
    clear_served_daily,
    daily_is_warm,
    etag_matches,
    file_read_count,
    not_modified,
    parse_daily_npz,
    publish_library_daily,
    reset_file_reads,
    write_daily_npz,
)
from hedge_fund.trading.constants import LIBRARY_TAPE_START_MS, QUAL_CANONICAL_START_MS

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "btc_library_daily_2017.npz"
TOKEN = "paper-daily-token"


def _request(url: str, headers: dict | None = None):
    req = Request(url, headers=headers or {})
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers, resp.read()
    except HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def _bars(start_ms: int, n: int, price: float = 100.0) -> DailyBars:
    ts = int(start_ms) + np.arange(n, dtype=np.int64) * DAY_MS
    px = np.full(n, price, dtype=np.float64)
    return DailyBars(ts=ts, open=px.copy(), high=px + 1, low=px - 1, close=px + 0.5)


def _install(root: Path, bars: DailyBars, *, tape_start: int = LIBRARY_TAPE_START_MS) -> None:
    write_daily_npz(
        cache_path(root),
        bars,
        {"tape_start": tape_start, "source": "test", "last_ts": int(bars.ts[-1])},
    )


class _Server:
    def __init__(self) -> None:
        from hedge_fund.web.server import Handler

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def close(self) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=3)
        self.httpd.server_close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"


class ValidatorTests(unittest.TestCase):
    def test_if_none_match_wins_over_if_modified_since(self):
        self.assertTrue(etag_matches('"abc"', '"abc"'))
        self.assertTrue(etag_matches('W/"abc"', '"abc"'))
        self.assertTrue(etag_matches("*", '"abc"'))
        self.assertFalse(etag_matches('"nope"', '"abc"'))
        headers = {
            "If-None-Match": '"nope"',
            "If-Modified-Since": "Thu, 01 Jan 2099 00:00:00 GMT",
        }
        self.assertFalse(not_modified(headers, etag='"abc"', mtime=1_700_000_000))
        self.assertFalse(not_modified({"If-Modified-Since": "not-a-date"}, etag='"abc"', mtime=1))


class LibraryDailyEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_served_daily()
        reset_file_reads()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.server = _Server()

    def tearDown(self) -> None:
        self.server.close()
        self.tmp.cleanup()
        clear_served_daily()
        reset_file_reads()

    def _env(self, token: str | None = TOKEN):
        env = {"PAPER_STATE": str(self.root)}
        if token is None:
            env["PAPER_DISCOVERY_INGEST_TOKEN"] = ""
        else:
            env["PAPER_DISCOVERY_INGEST_TOKEN"] = token
        return patch.dict(os.environ, env)

    def test_requires_the_ingest_token_and_does_not_echo_it(self):
        _install(self.root, _bars(LIBRARY_TAPE_START_MS, 12))
        url = self.server.url("/api/discovery/library/daily")
        with self._env():
            code, _headers, body = _request(url)
            self.assertEqual(code, 401)
            self.assertNotIn(TOKEN.encode(), body)
            code, _headers, body = _request(url, {"X-Discovery-Token": "wrong-token"})
            self.assertEqual(code, 401)
            self.assertNotIn(TOKEN.encode(), body)
            self.assertNotIn(b"wrong-token", body)
            code, headers, body = _request(url, {"Authorization": f"Bearer {TOKEN}"})
            self.assertEqual(code, 200)
            self.assertIn("ETag", headers)
            self.assertIn("Last-Modified", headers)
            self.assertGreater(len(body), 0)
        with self._env(token=None):
            code, _headers, body = _request(url, {"X-Discovery-Token": TOKEN})
            self.assertEqual(code, 503)
            self.assertNotIn(TOKEN.encode(), body)

    def test_missing_or_short_tape_is_a_fast_503(self):
        url = self.server.url("/api/discovery/library/daily")
        with self._env(), patch(
            "hedge_fund.library.daily.daily_from_tape",
            side_effect=AssertionError("resampled a missing tape"),
        ), patch(
            "hedge_fund.library.tape.load_library_tape",
            side_effect=AssertionError("loaded the 5m tape"),
        ):
            code, headers, body = _request(url, {"X-Discovery-Token": TOKEN})
            self.assertEqual(code, 503)
            self.assertEqual(headers.get("Retry-After"), "60")
            self.assertIn(b"not ready", body)
            self.assertEqual(file_read_count(), 0)
            _install(self.root, _bars(QUAL_CANONICAL_START_MS, 30))
            clear_served_daily()
            code, _headers, body = _request(url, {"X-Discovery-Token": TOKEN})
            self.assertEqual(code, 503)
            self.assertIn(b"2017-08-17", body)
            self.assertEqual(file_read_count(), 1)

    def test_response_is_cached_and_does_not_resample(self):
        bars = _bars(LIBRARY_TAPE_START_MS, 40)
        _install(self.root, bars)
        url = self.server.url("/api/discovery/library/daily")
        calls = {"live": 0}

        def _live(_state):
            calls["live"] += 1
            raise AssertionError("web request rebuilt daily bars")

        with self._env(), patch(
            "hedge_fund.library.daily.daily_from_tape",
            side_effect=AssertionError("resampled on the request"),
        ), patch(
            "hedge_fund.library.tape.load_library_tape",
            side_effect=AssertionError("read the 5m tape"),
        ), patch("hedge_fund.library.live.live_daily_bars", side_effect=_live):
            code, headers, body = _request(url, {"X-Discovery-Token": TOKEN})
            self.assertEqual(code, 200)
            self.assertEqual(file_read_count(), 1)
            etag = headers["ETag"]
            modified = headers["Last-Modified"]
            self.assertTrue(etag.startswith('"'))
            code, _headers, again = _request(
                url,
                {"X-Discovery-Token": TOKEN, "If-None-Match": etag},
            )
            self.assertEqual(code, 304)
            self.assertEqual(again, b"")
            self.assertEqual(file_read_count(), 1)
            code, _headers, third = _request(url, {"X-Discovery-Token": TOKEN})
            self.assertEqual(code, 200)
            self.assertEqual(third, body)
            self.assertEqual(file_read_count(), 1)
            code, _headers, _ims = _request(
                url,
                {"X-Discovery-Token": TOKEN, "If-Modified-Since": modified},
            )
            self.assertEqual(code, 304)
            code, _headers, fresh = _request(
                url,
                {
                    "X-Discovery-Token": TOKEN,
                    "If-None-Match": '"not-the-etag"',
                    "If-Modified-Since": modified,
                },
            )
            self.assertEqual(code, 200)
            self.assertEqual(fresh, body)
            self.assertEqual(file_read_count(), 1)
            self.assertEqual(calls["live"], 0)
        parsed_bars, meta = parse_daily_npz(body)
        self.assertIsNotNone(parsed_bars)
        assert parsed_bars is not None
        self.assertTrue(daily_is_warm(parsed_bars, meta))
        self.assertEqual(int(parsed_bars.ts[0]), LIBRARY_TAPE_START_MS)
        self.assertTrue(np.array_equal(parsed_bars.close, bars.close))

    def test_cycle_publishes_the_file_the_web_sends(self):
        bars = _bars(LIBRARY_TAPE_START_MS, 15)

        def fake_live(state):
            write_daily_npz(
                cache_path(state),
                bars,
                {"tape_start": LIBRARY_TAPE_START_MS, "source": "live_tape"},
            )
            return bars

        with patch("hedge_fund.library.live.live_daily_bars", side_effect=fake_live) as live:
            info = publish_library_daily(self.root)
        live.assert_called_once()
        self.assertTrue(info["ok"])
        self.assertEqual(info["bars"], 15)
        self.assertEqual(info["first_ts"], LIBRARY_TAPE_START_MS)
        url = self.server.url("/api/discovery/library/daily")
        with self._env(), patch(
            "hedge_fund.library.daily.daily_from_tape",
            side_effect=AssertionError("web resampled the cycle file"),
        ):
            code, _headers, body = _request(url, {"X-Discovery-Token": TOKEN})
        self.assertEqual(code, 200)
        from hedge_fund.library.daily_cache import parse_daily_npz

        got, _meta = parse_daily_npz(body)
        assert got is not None
        self.assertTrue(np.array_equal(got.close, bars.close))


class LibraryDailyWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        from scripts.discovery_worker import _reset_eval_session

        _reset_eval_session()
        clear_served_daily()
        reset_file_reads()

    def tearDown(self) -> None:
        from scripts.discovery_worker import _init_pool, _reset_eval_session

        _reset_eval_session()
        _init_pool(None, None, None, 0, None, None, None, None, None, (), None, None)

    def test_worker_sma200_matches_the_prod_cycle(self):
        from hedge_fund.library.daily_cache import parse_daily_npz
        from hedge_fund.library.evaluate import build_library_data, evaluate_library_record
        from scripts.discovery_worker import _eval_name, _init_pool, ensure_library_daily

        self.assertTrue(FIXTURE.is_file())
        blob = FIXTURE.read_bytes()
        direct_bars, _meta = parse_daily_npz(blob)
        assert direct_bars is not None
        self.assertEqual(len(direct_bars), 3360)
        self.assertEqual(int(direct_bars.ts[0]), LIBRARY_TAPE_START_MS)
        direct = evaluate_library_record(
            "lib.sma200",
            build_library_data(direct_bars),
            tested_at="2026-10-29T00:00:00+00:00",
        )
        self.assertEqual(f"{direct['gate_v4']['dsr']:.3f}", "0.590")
        self.assertEqual(f"{direct['gate_v4']['pbo']:.2f}", "0.30")
        self.assertEqual(direct["trades"], 27)
        self.assertEqual(direct["library"]["blocks"]["score_start"], "2018-12-01")
        self.assertIn("dsr 0.590 < 0.95 (n_trials=64)", direct["warnings"])
        self.assertIn("pbo 0.300 > 0.2", direct["warnings"])
        self.assertFalse(any(str(r).startswith(("dsr ", "pbo ")) for r in direct["fail_reasons"]))

        prod = Path(tempfile.mkdtemp())
        worker = Path(tempfile.mkdtemp())
        (cache_path(prod)).write_bytes(blob)
        server = _Server()
        token = "sma200-ingest-token"
        try:
            with patch("hedge_fund.web.server.state_root", return_value=prod), patch(
                "scripts.discovery_worker.state_root", return_value=worker
            ), patch.dict(os.environ, {"PAPER_DISCOVERY_INGEST_TOKEN": token}):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    ensure_library_daily(server.url(""), token)
                    self.assertEqual(file_read_count(), 1)
                    ensure_library_daily(server.url(""), token)
                    self.assertEqual(file_read_count(), 1)
            text = buf.getvalue()
            self.assertNotIn(token, text)
            sidecar = worker / "library_daily_BTC_USDT.npz.http.json"
            self.assertNotIn(token, sidecar.read_text(encoding="utf-8"))
            saved = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertTrue(saved.get("etag"))
            from scripts.discovery_worker import _SESSION

            ready = _SESSION["library_ready"]
            _init_pool(None, None, None, 0, None, None, None, None, None, (), None, ready)
            worker_row = _eval_name("lib.sma200")
        finally:
            server.close()
        assert worker_row is not None
        self.assertEqual(worker_row["gate_v4"]["dsr"], direct["gate_v4"]["dsr"])
        self.assertEqual(worker_row["gate_v4"]["pbo"], direct["gate_v4"]["pbo"])
        self.assertEqual(worker_row["trades"], direct["trades"])
        self.assertEqual(worker_row["library"]["failing_checks"], direct["library"]["failing_checks"])
        self.assertEqual(worker_row["warnings"], direct["warnings"])
        self.assertEqual(worker_row["qualified"], direct["qualified"])
        self.assertEqual(f"{worker_row['gate_v4']['dsr']:.3f}", "0.590")
        self.assertEqual(f"{worker_row['gate_v4']['pbo']:.2f}", "0.30")

    def test_unavailable_tape_does_not_claim(self):
        from scripts.discovery_worker import (
            HISTORY_BACKOFF_SECONDS,
            LibraryTapeUnavailable,
            _run_loop,
        )

        token = "do-not-log-this-token"
        claims: list = []
        releases: list = []
        sleeps: list = []

        def claim(*_a, **_k):
            claims.append(1)
            return {"names": ["lib.sma200"]}

        def release(*_a, **_k):
            releases.append(1)

        def sleeper(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 3:
                raise KeyboardInterrupt()

        args = argparse.Namespace(once=False, no_ingest=True, no_bootstrap=True, idle_sleep=5, pause_sleep=10)
        with patch(
            "scripts.discovery_worker.ensure_library_daily",
            side_effect=LibraryTapeUnavailable(
                f"upstream echoed {token}",
                retry_after=HISTORY_BACKOFF_SECONDS,
            ),
        ), patch("scripts.discovery_worker._claim_remote", side_effect=claim), patch(
            "scripts.discovery_worker._release_remote_safe", side_effect=release
        ), patch("scripts.discovery_worker.time.sleep", side_effect=sleeper), patch.dict(
            os.environ, {"PAPER_DISCOVERY_SOURCE": ""}
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                with self.assertRaises(KeyboardInterrupt):
                    _run_loop(
                        args,
                        workers=1,
                        max_names=1,
                        local_plan=False,
                        worker_id="zealous-lion",
                        token=token,
                        ingest_url="http://prod/api/discovery/ingest",
                        base="http://prod",
                        farm_enabled=True,
                    )
        self.assertEqual(claims, [])
        self.assertEqual(releases, [])
        self.assertEqual(sleeps, [HISTORY_BACKOFF_SECONDS] * 3)
        self.assertGreaterEqual(HISTORY_BACKOFF_SECONDS, 60)
        logged = buf.getvalue()
        self.assertNotIn(token, logged)
        self.assertIn("[redacted]", logged)
        self.assertIn("not claiming lib.* names", logged)

        args.once = True
        with patch(
            "scripts.discovery_worker.ensure_library_daily",
            side_effect=LibraryTapeUnavailable("tape not ready", retry_after=60),
        ), patch("scripts.discovery_worker._claim_remote", side_effect=claim), patch.dict(
            os.environ, {"PAPER_DISCOVERY_SOURCE": ""}
        ):
            rc = _run_loop(
                args,
                workers=1,
                max_names=1,
                local_plan=False,
                worker_id="zealous-lion",
                token=token,
                ingest_url=None,
                base="http://prod",
                farm_enabled=True,
            )
        self.assertEqual(rc, 1)
        self.assertEqual(claims, [])

    def test_ready_tape_still_claims_and_random_mode_skips_the_daily_fetch(self):
        from scripts.discovery_worker import _library_data, _run_loop

        claims: list = []

        def claim(*_a, **_k):
            claims.append(1)
            return {"names": []}

        args = argparse.Namespace(once=True, no_ingest=True, no_bootstrap=True, idle_sleep=5, pause_sleep=10)
        with patch("scripts.discovery_worker.ensure_library_daily", return_value=object()) as fetched, patch(
            "scripts.discovery_worker._claim_remote", side_effect=claim
        ), patch("scripts.discovery_worker._post_ingest", return_value=None), patch.dict(
            os.environ, {"PAPER_DISCOVERY_SOURCE": ""}
        ):
            rc = _run_loop(
                args,
                workers=1,
                max_names=1,
                local_plan=False,
                worker_id="zealous-lion",
                token="token",
                ingest_url=None,
                base="http://prod",
                farm_enabled=True,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(claims, [1])
        fetched.assert_called_once()

        claims.clear()
        with patch(
            "scripts.discovery_worker.ensure_library_daily",
            side_effect=AssertionError("random mode fetched daily bars"),
        ), patch("scripts.discovery_worker._claim_remote", side_effect=claim), patch(
            "scripts.discovery_worker._post_ingest", return_value=None
        ), patch.dict(os.environ, {"PAPER_DISCOVERY_SOURCE": "random"}):
            rc = _run_loop(
                args,
                workers=1,
                max_names=1,
                local_plan=False,
                worker_id="zealous-lion",
                token="token",
                ingest_url=None,
                base="http://prod",
                farm_enabled=True,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(claims, [1])

        rows = [[1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 1.0]]
        with patch.dict(os.environ, {"PAPER_DISCOVERY_SOURCE": "random"}), patch(
            "hedge_fund.library.tape.load_library_daily", return_value=empty_daily()
        ), patch(
            "hedge_fund.library.evaluate.library_data_from_rows", return_value="from-rows"
        ) as from_rows:
            self.assertEqual(_library_data({"BTC/USDT": rows}), "from-rows")
        from_rows.assert_called_once()
        with patch.dict(os.environ, {"PAPER_DISCOVERY_SOURCE": ""}), patch(
            "hedge_fund.library.tape.load_library_daily", return_value=empty_daily()
        ), patch(
            "hedge_fund.library.evaluate.library_data_from_rows",
            side_effect=AssertionError("library mode scored the 5m cut"),
        ):
            self.assertIsNone(_library_data({"BTC/USDT": rows}))


if __name__ == "__main__":
    unittest.main()
