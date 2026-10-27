"""Postgres candle store: migration, upserts, gaps, and the file fallback."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from hedge_fund.data.candle_quality import (
    TF_MS,
    known_outage_reason,
    last_closed_open_ms,
    ms_to_dt,
    summarize_breaks,
)
from hedge_fund.trading.constants import QUAL_CANONICAL_START_MS, QUAL_SYMBOLS

REPO = Path(__file__).resolve().parents[1]
DSN = os.environ.get("PAPERBOT_TEST_DATABASE_URL", "").strip()


def _utc(y, m, d, hh=0, mm=0, ss=0) -> int:
    moment = datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def _bar(ts: int, close: float = 1.0) -> list:
    return [ts, close, close + 1, close - 1, close, 2.0]


class QualityTests(unittest.TestCase):
    def test_last_closed_drops_the_forming_bar(self):
        # 10:07 UTC sits inside the 10:05 candle. Last closed open is 10:00.
        now = _utc(2024, 6, 1, 10, 7)
        self.assertEqual(last_closed_open_ms(now), _utc(2024, 6, 1, 10, 0))
        # Exactly 10:10 opens a new candle. 10:05 just closed.
        boundary = _utc(2024, 6, 1, 10, 10)
        self.assertEqual(last_closed_open_ms(boundary), _utc(2024, 6, 1, 10, 5))

    def test_known_maintenance_is_not_an_unexpected_gap(self):
        # 2021-02-11 03:30–05:00 UTC is on the published maintenance list.
        before = _utc(2021, 2, 11, 3, 25)
        after = _utc(2021, 2, 11, 5, 0)
        reason = known_outage_reason(before, after)
        self.assertIsNotNone(reason)
        summary = summarize_breaks([(before, after)])
        self.assertEqual(summary["gap_count"], 0)
        self.assertEqual(summary["known_gap_count"], 1)
        self.assertTrue(summary["monotonic"])

        hole = (_utc(2024, 1, 1, 0, 0), _utc(2024, 1, 1, 0, 15))
        self.assertIsNone(known_outage_reason(*hole))
        unexpected = summarize_breaks([hole])
        self.assertEqual(unexpected["gap_count"], 1)
        self.assertEqual(unexpected["gaps"][0]["missing_bars"], 2)
        self.assertGreater(unexpected["gaps"][0]["missing_bars"], 0)

    def test_duplicate_timestamp_is_not_monotonic(self):
        ts = _utc(2024, 1, 1)
        summary = summarize_breaks([(ts, ts)])
        self.assertFalse(summary["monotonic"])
        self.assertEqual(summary["duplicates"], 1)
        self.assertEqual(summary["gap_count"], 0)

    def test_pool_max_is_clamped_at_two(self):
        from hedge_fund.data.candle_store import POOL_MAX_CAP, pool_max_size

        self.assertEqual(POOL_MAX_CAP, 2)
        with patch.dict(os.environ, {"PAPERBOT_DB_POOL_MAX": "20"}):
            self.assertEqual(pool_max_size(), 2)
        with patch.dict(os.environ, {"PAPERBOT_DB_POOL_MAX": "1"}):
            self.assertEqual(pool_max_size(), 1)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PAPERBOT_DB_POOL_MAX", None)
            self.assertEqual(pool_max_size(), 2)

    def test_pool_closes_idle_and_rejects_a_third_checkout(self):
        from hedge_fund.data.candle_store import CandlePool, PoolTimeout

        class Fake:
            def __init__(self):
                self.closed = False

            def execute(self, *_a, **_k):
                if self.closed:
                    raise RuntimeError("closed")
                return self

            def commit(self):
                return None

            def rollback(self):
                return None

            def close(self):
                self.closed = True

        made: list[Fake] = []

        def connect():
            conn = Fake()
            made.append(conn)
            return conn

        pool = CandlePool("unused", max_size=2, max_idle=0.05, timeout=0.2, connect=connect)
        first = pool.connection()
        second = pool.connection()
        a = first.__enter__()
        b = second.__enter__()
        self.assertIsNot(a, b)
        held = []

        def third():
            try:
                with pool.connection():
                    held.append("got")
            except PoolTimeout:
                held.append("timeout")

        thread = threading.Thread(target=third)
        thread.start()
        thread.join(1.0)
        self.assertEqual(held, ["timeout"])
        first.__exit__(None, None, None)
        second.__exit__(None, None, None)
        self.assertEqual(pool.in_use(), 0)
        time.sleep(0.2)
        self.assertEqual(pool.idle_count(), 0)
        self.assertTrue(all(conn.closed for conn in made))
        pool.close()


@unittest.skipUnless(DSN, "PAPERBOT_TEST_DATABASE_URL not set")
class PostgresCandleTests(unittest.TestCase):
    def setUp(self):
        from hedge_fund.data import candle_store

        self._env = patch.dict(os.environ, {"PAPERBOT_DATABASE_URL": DSN})
        self._env.start()
        candle_store.close_store()
        candle_store.migrate()
        with candle_store._pool().connection() as conn:
            conn.execute("TRUNCATE candles")
        candle_store.close_store()

    def tearDown(self):
        from hedge_fund.data import candle_store

        candle_store.close_store()
        self._env.stop()

    def test_migration_is_idempotent_and_matches_the_sql_file(self):
        from hedge_fund.data.candle_store import migrate, migration_sql

        sql = migration_sql()
        self.assertIn("CREATE TABLE IF NOT EXISTS candles", sql)
        self.assertIn("PRIMARY KEY (exchange, symbol, timeframe, open_time)", sql)
        self.assertEqual(sql, (REPO / "hedge_fund/data/migrations/001_candles.sql").read_text())
        migrate()
        migrate()
        from hedge_fund.data.candle_store import _pool

        with _pool().connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_name = 'candles'
                """
            ).fetchone()
        self.assertEqual(int(row[0]), 1)

    def test_upsert_replaces_and_does_not_duplicate(self):
        from hedge_fund.data.candle_store import load_tape, symbol_bounds, upsert_ohlcv

        ts = _utc(2024, 5, 1, 0, 0)
        upsert_ohlcv("BTC/USDT", [_bar(ts, close=10)])
        upsert_ohlcv("BTC/USDT", [_bar(ts, close=11)])
        bounds = symbol_bounds("BTC/USDT")
        self.assertIsNotNone(bounds)
        assert bounds is not None
        self.assertEqual(bounds[2], 1)
        arr = load_tape("BTC/USDT")
        self.assertEqual(len(arr), 1)
        self.assertEqual(float(arr[0]["close"]), 11.0)
        self.assertEqual(int(arr[0]["ts"]), ts)

    def test_incremental_update_writes_only_closed_bars_and_resumes(self):
        from hedge_fund.data.candle_store import symbol_bounds
        from hedge_fund.data.candle_sync import backfill, update_incremental

        start = _utc(2024, 3, 2, 0, 0)
        end = start + 4 * TF_MS
        now = end + TF_MS + 60_000  # one minute into the next, still-forming bar
        series = [_bar(start + i * TF_MS, close=float(i + 1)) for i in range(6)]
        calls: list[int] = []

        def fetch(symbol, since, limit):
            calls.append(int(since))
            return [row for row in series if row[0] >= since][:limit]

        first = backfill(
            fetch=fetch,
            symbols=("BTC/USDT", "ETH/USDT"),
            start_ms=start,
            now_ms=now,
            pause_s=0.0,
            retry_pause=0.0,
        )
        for sym in ("BTC/USDT", "ETH/USDT"):
            self.assertEqual(symbol_bounds(sym)[2], 5)
            self.assertEqual(symbol_bounds(sym)[1], end)
        self.assertEqual(first["quality"]["gap_count"], 0)
        self.assertTrue(first["quality"]["monotonic"])

        calls.clear()
        second = update_incremental(
            fetch=fetch,
            symbols=("BTC/USDT", "ETH/USDT"),
            now_ms=now,
            pause_s=0.0,
            retry_pause=0.0,
        )
        self.assertEqual(calls, [])
        for sym in ("BTC/USDT", "ETH/USDT"):
            self.assertEqual(symbol_bounds(sym)[2], 5)
            self.assertTrue(second["symbols"][sym]["up_to_date"])

    def test_update_skips_the_forming_candle(self):
        from hedge_fund.data.candle_store import symbol_bounds, upsert_ohlcv
        from hedge_fund.data.candle_sync import update_incremental

        start = _utc(2024, 3, 4, 0, 0)
        end = start + 2 * TF_MS
        now = end + TF_MS + 60_000
        series = [_bar(start + i * TF_MS, close=float(i + 1)) for i in range(4)]
        calls: list[int] = []

        def fetch(symbol, since, limit):
            calls.append(int(since))
            return [row for row in series if row[0] >= since][:limit]

        for sym in ("BTC/USDT", "ETH/USDT"):
            upsert_ohlcv(sym, [_bar(start, 1)])
        update_incremental(
            fetch=fetch,
            symbols=("BTC/USDT", "ETH/USDT"),
            now_ms=now,
            pause_s=0.0,
            retry_pause=0.0,
        )
        self.assertEqual(calls[0], start + TF_MS)
        for sym in ("BTC/USDT", "ETH/USDT"):
            bounds = symbol_bounds(sym)
            self.assertEqual(bounds[2], 3)
            self.assertEqual(bounds[1], end)

    def test_backfill_resumes_and_fills_a_hole(self):
        from hedge_fund.data.candle_store import symbol_bounds, upsert_ohlcv
        from hedge_fund.data.candle_sync import backfill

        start = _utc(2024, 3, 3, 0, 0)
        end = start + 6 * TF_MS
        now = end + TF_MS + 10_000
        # Frontier in the middle, plus a hole behind it, and the final bar.
        for sym in ("BTC/USDT", "ETH/USDT"):
            upsert_ohlcv(sym, [_bar(start, 1), _bar(start + 3 * TF_MS, 2), _bar(end, 3)])

        def fetch(symbol, since, limit):
            rows = []
            ts = int(since)
            while len(rows) < limit and ts <= end + TF_MS:
                rows.append(_bar(ts, 5))
                ts += TF_MS
            return rows

        backfill(
            fetch=fetch,
            symbols=("BTC/USDT", "ETH/USDT"),
            start_ms=start,
            now_ms=now,
            pause_s=0.0,
            retry_pause=0.0,
        )
        for sym in ("BTC/USDT", "ETH/USDT"):
            bounds = symbol_bounds(sym)
            self.assertEqual(bounds[0], start)
            self.assertEqual(bounds[1], end)
            self.assertEqual(bounds[2], 7)

    def test_known_and_unexpected_gaps_are_reported(self):
        from hedge_fund.data.candle_store import quality_summary, upsert_ohlcv

        before = _utc(2021, 2, 11, 3, 25)
        after = _utc(2021, 2, 11, 5, 0)
        plain_a = _utc(2024, 1, 2, 0, 0)
        plain_b = _utc(2024, 1, 2, 0, 15)
        for sym in QUAL_SYMBOLS:
            upsert_ohlcv(sym, [_bar(before, 1), _bar(after, 2), _bar(plain_a, 3), _bar(plain_b, 4)])
        quality = quality_summary(force=True)
        self.assertGreaterEqual(quality["known_gap_count"], 2)
        self.assertGreaterEqual(quality["gap_count"], 2)
        self.assertTrue(quality["monotonic"])
        self.assertEqual(quality["duplicates"], 0)
        self.assertEqual(quality["last_candle_time"][:4], "2024")
        btc_reasons = [gap["known"] for gap in quality["symbols"]["BTC/USDT"]["gaps"]]
        self.assertIn(True, btc_reasons)
        self.assertIn(False, btc_reasons)

    def test_store_is_not_selected_until_it_covers_the_canonical_tape(self):
        from hedge_fund.data.candle_store import candle_store_selected, upsert_ohlcv

        now = int(time.time() * 1000)
        last = last_closed_open_ms(now)
        for sym in QUAL_SYMBOLS:
            upsert_ohlcv(sym, [_bar(last, 1)])
        self.assertFalse(candle_store_selected())

        for sym in QUAL_SYMBOLS:
            upsert_ohlcv(sym, [_bar(QUAL_CANONICAL_START_MS, 2), _bar(last, 3)])
        self.assertTrue(candle_store_selected())

    def test_shared_history_and_live_slice_read_the_store_when_ready(self):
        from hedge_fund.data.candle_store import upsert_ohlcv
        from hedge_fund.trading.live_tape import TAPE_DTYPE, live_history_health, load_cycle_klines, save_array
        from hedge_fund.trading.price_history import clear_manifest_cache, manifest_for
        import numpy as np

        now = int(time.time() * 1000)
        last = last_closed_open_ms(now)
        day = 86_400_000
        kept = (last - (last % day)) - day + 12 * 60 * 60 * 1000
        self.assertEqual(kept % TF_MS, 0)
        for i, sym in enumerate(QUAL_SYMBOLS):
            upsert_ohlcv(
                sym,
                [
                    _bar(QUAL_CANONICAL_START_MS, 1 + i),
                    _bar(kept, 42 + i),
                    _bar(last, 7 + i),
                ],
            )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i, sym in enumerate(QUAL_SYMBOLS):
                arr = np.empty(1, dtype=TAPE_DTYPE)
                arr[0] = (kept, 999, 999, 999, 999, 1)
                save_array(sym, arr, root)
            clear_manifest_cache()
            manifest = manifest_for(root)
            self.assertEqual(manifest["source"], "candles")
            from hedge_fund.trading.price_history import canonical_arrays

            arrays, source, _pin = canonical_arrays(root)
            self.assertEqual(source, "candles")
            closes = [float(row["close"]) for row in arrays["BTC/USDT"]]
            self.assertIn(42.0, closes)
            self.assertNotIn(999.0, closes)

            with patch("hedge_fund.data.candle_sync.update_incremental", return_value={}) as upd:
                klines = load_cycle_klines(source=object(), state_dir=root, refresh=True)
            upd.assert_called_once()
            self.assertEqual(klines["BTC/USDT"][-1].close, 7.0)
            health = live_history_health(root)
        self.assertEqual(health["store"], "candles")
        self.assertEqual(health["symbols"]["BTC/USDT"]["last_candle_time"][:4], str(datetime.fromtimestamp(last / 1000, tz=timezone.utc).year))
        self.assertGreaterEqual(health["gap_count"], 1)
        self.assertIn("gap_count", health)
        self.assertTrue(health["candle_store"]["configured"])
        self.assertTrue(health["candle_store"]["ready"])
        clear_manifest_cache()

    def test_empty_store_keeps_the_file_tape(self):
        from hedge_fund.trading.live_tape import TAPE_DTYPE, live_history_health, signal_candles, save_array
        from hedge_fund.trading.price_history import clear_manifest_cache, manifest_for
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for sym in QUAL_SYMBOLS:
                arr = np.empty(2, dtype=TAPE_DTYPE)
                arr[0] = (1_700_000_000_000, 1, 1, 1, 3, 1)
                arr[1] = (1_700_000_000_000 + TF_MS, 1, 1, 1, 4, 1)
                save_array(sym, arr, root)
            clear_manifest_cache()
            self.assertEqual(manifest_for(root)["source"], "live_tape")
            bars = signal_candles("BTC/USDT", state_dir=root)
            self.assertEqual(bars[-1].close, 4.0)
            health = live_history_health(root)
        self.assertEqual(health["store"], "live_tape")
        self.assertFalse(health["candle_store"]["ready"])
        self.assertTrue(health["candle_store"]["configured"])
        self.assertEqual(health["symbols"]["BTC/USDT"]["gap_count"], 0)
        self.assertTrue(health["symbols"]["BTC/USDT"]["monotonic"])
        clear_manifest_cache()

    def test_health_stays_ok_when_the_snapshot_fails(self):
        from hedge_fund.web.server import health_payload

        with patch("hedge_fund.data.candle_store.health_snapshot", side_effect=RuntimeError("db down")):
            body = health_payload()
        self.assertTrue(body["ok"])
        self.assertIsNone(body["candles"]["gap_count"])
        self.assertIn("db down", body["candles"]["error"])

    def test_retry_then_success(self):
        from hedge_fund.data.candle_sync import fetch_with_retry

        calls = {"n": 0}

        def fetch(symbol, since, limit):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("429 too many requests")
            return [_bar(int(since), 1)]

        rows = fetch_with_retry(fetch, "BTC/USDT", 0, 10, retry_pause=0.0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(calls["n"], 2)


class ManifestWiringTests(unittest.TestCase):
    def test_workers_do_not_read_the_database_url(self):
        text = (REPO / "scripts" / "discovery_worker.py").read_text()
        self.assertNotIn("PAPERBOT_DATABASE_URL", text)
        self.assertNotIn("paperbot-db", text)

    def test_claim_mode_starts_without_the_history_file(self):
        from scripts import discovery_worker

        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "PAPER_STATE": tmp,
                "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token",
            }
            with patch.dict(os.environ, env, clear=False):
                with patch.object(discovery_worker, "_run_loop", return_value=0) as loop:
                    rc = discovery_worker.main(["--once", "--workers", "1", "--worker-id", "unit"])
        self.assertEqual(rc, 0)
        loop.assert_called_once()

    def test_local_plan_still_requires_the_history_file(self):
        from scripts import discovery_worker

        with tempfile.TemporaryDirectory() as tmp:
            env = {"PAPER_STATE": tmp}
            with patch.dict(os.environ, env, clear=False):
                rc = discovery_worker.main(["--local-plan", "--no-ingest", "--once", "--workers", "1"])
        self.assertEqual(rc, 2)

    def test_k8s_wires_the_secret_and_does_not_commit_a_url(self):
        cron = yaml.safe_load((REPO / "k8s" / "candle-updater.yaml").read_text())
        self.assertEqual(cron["metadata"]["namespace"], "trading")
        self.assertEqual(cron["spec"]["schedule"], "*/5 * * * *")
        self.assertEqual(cron["spec"]["concurrencyPolicy"], "Forbid")
        container = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["command"], ["python", "-m", "hedge_fund.data.candle_update"])
        url = next(item for item in container["env"] if item["name"] == "PAPERBOT_DATABASE_URL")
        self.assertEqual(url["valueFrom"]["secretKeyRef"]["name"], "paperbot-db")
        self.assertEqual(url["valueFrom"]["secretKeyRef"]["key"], "DATABASE_URL")
        pool = next(item for item in container["env"] if item["name"] == "PAPERBOT_DB_POOL_MAX")
        self.assertEqual(pool["value"], "1")

        backend = list(yaml.safe_load_all((REPO / "k8s" / "backend.yaml").read_text()))
        deploy = next(doc for doc in backend if doc["kind"] == "Deployment")
        containers = {c["name"]: c for c in deploy["spec"]["template"]["spec"]["containers"]}
        for name in ("web", "cycle"):
            env = {item["name"]: item for item in containers[name]["env"]}
            ref = env["PAPERBOT_DATABASE_URL"]["valueFrom"]["secretKeyRef"]
            self.assertEqual(ref["name"], "paperbot-db")
            self.assertEqual(ref["key"], "DATABASE_URL")
        self.assertNotIn("PAPERBOT_DATABASE_URL", {item["name"] for item in containers["heartbeat"]["env"]})
        web_pool = next(item for item in containers["web"]["env"] if item["name"] == "PAPERBOT_DB_POOL_MAX")
        self.assertEqual(web_pool["value"], "2")

        frontend = (REPO / "k8s" / "frontend.yaml").read_text()
        self.assertNotIn("paperbot-db", frontend)
        self.assertNotIn("DATABASE_URL", frontend)
        kustom = (REPO / "k8s" / "kustomization.yaml").read_text()
        self.assertIn("candle-updater.yaml", kustom)

        blob = "\n".join(
            path.read_text()
            for path in (REPO / "k8s").glob("*.yaml")
        )
        self.assertNotIn("sslmode=require", blob)
        self.assertNotIn("postgres://", blob)
        self.assertNotIn("postgresql://", blob)

    def test_dt_roundtrip_is_exact_for_5m_boundaries(self):
        from hedge_fund.data.candle_quality import dt_to_ms

        ts = QUAL_CANONICAL_START_MS
        self.assertEqual(dt_to_ms(ms_to_dt(ts)), ts)


if __name__ == "__main__":
    unittest.main()
