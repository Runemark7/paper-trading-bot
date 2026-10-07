"""Status and summary read path: last equity row, one open, no exchange fetch.

150 accounts x 10k equity rows must stay under a second and must not
SELECT the full equity_snapshots table. JSON keys stay the ones the UI reads.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[1]

# Dotted keys from a populated /api/status. Account names under
# open_lots_by_account are data, not shape.
STATUS_KEYS = frozenset({
    "paper_only",
    "as_of",
    "running_now",
    "running_now.label",
    "running_now.certainty",
    "running_now.strategy",
    "running_now.strategy.mode",
    "running_now.strategy.active",
    "running_now.strategy.fallback",
    "running_now.strategy.source",
    "running_now.accounts",
    "running_now.accounts.count",
    "running_now.accounts.kind",
    "running_now.accounts.names",
    "running_now.cycle",
    "running_now.cycle.interval_seconds",
    "running_now.cycle.bar_timeframe",
    "running_now.cycle.window",
    "running_now.cycle.last_cycle_at",
    "running_now.cycle.account_saved_at",
    "running_now.cycle.next",
    "running_now.cycle.next.at",
    "running_now.cycle.next.inferred",
    "running_now.cycle.next.overdue",
    "running_now.cycle.next.in_window_now",
    "running_now.cycle.next.note",
    "running_now.live_history",
    "running_now.live_history.paper_only",
    "running_now.live_history.timeframe",
    "running_now.live_history.symbols",
    "running_now.live_history.symbols.BTC/USDT",
    "running_now.live_history.symbols.BTC/USDT.bars",
    "running_now.live_history.symbols.BTC/USDT.first_bar_time",
    "running_now.live_history.symbols.BTC/USDT.last_bar_time",
    "running_now.live_history.symbols.BTC/USDT.short",
    "running_now.live_history.symbols.ETH/USDT",
    "running_now.live_history.symbols.ETH/USDT.bars",
    "running_now.live_history.symbols.ETH/USDT.first_bar_time",
    "running_now.live_history.symbols.ETH/USDT.last_bar_time",
    "running_now.live_history.symbols.ETH/USDT.short",
    "running_now.live_history.target_bars",
    "running_now.live_history.signal_eval_bars",
    "running_now.live_history.minimum_bars",
    "running_now.live_history.store",
    "running_now.positions_open",
    "running_now.open_lots",
    "running_now.open_lots_by_account",
    "running_now.open_lots_unit",
    "running_now.heartbeat",
    "running_now.heartbeat.configured_interval_seconds",
    "running_now.heartbeat.role",
    "running_now.heartbeat.last_pass_at",
    "running_now.heartbeat.last_closed",
    "running_now.heartbeat.accounts_checked",
    "running_now.heartbeat.recent",
    "running_now.heartbeat.certainty",
    "running_now.heartbeat.on",
    "running_now.heartbeat.note",
    "running_now.regime",
    "running_now.regime.gates_live_book",
    "running_now.regime.display_only",
    "running_now.regime.note",
    "in_progress",
    "in_progress.job_runner",
    "in_progress.note",
    "in_progress.pipeline",
    "in_progress.pipeline.job_runner",
    "in_progress.pipeline.phase",
    "in_progress.pipeline.status",
    "in_progress.pipeline.certainty",
    "in_progress.pipeline.running",
    "in_progress.pipeline.stamp_says_in_progress",
    "in_progress.pipeline.stale",
    "in_progress.pipeline.started_at",
    "in_progress.pipeline.finished_at",
    "in_progress.pipeline.at",
    "in_progress.pipeline.source",
    "in_progress.pipeline.note",
    "in_progress.discovery",
    "in_progress.discovery.certainty",
    "in_progress.discovery.running",
    "in_progress.discovery.log_count",
    "in_progress.discovery.unique_tested",
    "in_progress.discovery.last_tested_at",
    "in_progress.discovery.last_strategy",
    "in_progress.discovery.last_qualified",
    "in_progress.discovery.file",
    "in_progress.discovery.file.path",
    "in_progress.discovery.file.mtime",
    "in_progress.discovery.file.exists",
    "in_progress.discovery.note",
    "in_progress.tournament",
    "in_progress.tournament.certainty",
    "in_progress.tournament.running",
    "in_progress.tournament.stamp_says_this_phase",
    "in_progress.tournament.active_count",
    "in_progress.tournament.evaluation_limit",
    "in_progress.tournament.champions_per_cycle",
    "in_progress.tournament.synced_until",
    "in_progress.tournament.file",
    "in_progress.tournament.file.path",
    "in_progress.tournament.file.mtime",
    "in_progress.tournament.file.exists",
    "in_progress.tournament.note",
    "in_progress.replenish",
    "in_progress.replenish.certainty",
    "in_progress.replenish.running",
    "in_progress.replenish.needed",
    "in_progress.replenish.stamp_says_this_phase",
    "in_progress.replenish.note",
    "in_progress.graduation",
    "in_progress.graduation.certainty",
    "in_progress.graduation.running",
    "in_progress.graduation.count",
    "in_progress.graduation.last_name",
    "in_progress.graduation.last_status",
    "in_progress.graduation.last_graduated_at",
    "in_progress.graduation.token",
    "in_progress.graduation.note",
    "in_progress.isolated_cycle",
    "in_progress.isolated_cycle.certainty",
    "in_progress.isolated_cycle.running",
    "in_progress.isolated_cycle.stamp_says_this_phase",
    "in_progress.isolated_cycle.last_cycle_at",
    "in_progress.isolated_cycle.note",
})

SUMMARY_KEYS = frozenset({
    "equity",
    "closed_trades",
    "win_rate",
    "total_pnl",
    "updated",
    "source",
    "account_count",
    "as_of",
    "live_history",
    "live_history.paper_only",
    "live_history.timeframe",
    "live_history.symbols",
    "live_history.symbols.BTC/USDT",
    "live_history.symbols.BTC/USDT.bars",
    "live_history.symbols.BTC/USDT.first_bar_time",
    "live_history.symbols.BTC/USDT.last_bar_time",
    "live_history.symbols.BTC/USDT.short",
    "live_history.symbols.ETH/USDT",
    "live_history.symbols.ETH/USDT.bars",
    "live_history.symbols.ETH/USDT.first_bar_time",
    "live_history.symbols.ETH/USDT.last_bar_time",
    "live_history.symbols.ETH/USDT.short",
    "live_history.target_bars",
    "live_history.signal_eval_bars",
    "live_history.minimum_bars",
    "live_history.store",
})


def _dotted(obj, prefix=""):
    if not isinstance(obj, dict):
        return []
    out = []
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        out.append(path)
        out.extend(_dotted(value, path))
    return out


def _status_shape(payload: dict) -> set[str]:
    return {
        path
        for path in _dotted(payload)
        if not (
            path.startswith("running_now.open_lots_by_account.")
            and path != "running_now.open_lots_by_account"
        )
    }


def _is_full_equity_scan(sql: str) -> bool:
    compact = " ".join(sql.lower().split())
    return (
        "from equity_snapshots" in compact
        and compact.startswith("select")
        and "limit" not in compact
    )


def _clear_caches() -> None:
    from hedge_fund.trading.store import clear_account_read_cache
    from hedge_fund.web.server import clear_summary_cache
    from hedge_fund.web.status import clear_status_cache

    clear_status_cache()
    clear_summary_cache()
    clear_account_read_cache()


class ReadPathTests(unittest.TestCase):
    def setUp(self) -> None:
        _clear_caches()

    def test_last_row_and_list_order_updated_keep_json_shape(self):
        from hedge_fund.trading.store import TradeStore
        from hedge_fund.web.server import build_summary
        from hedge_fund.web.status import build_status

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._account(
                root / "trades_aaa.sqlite",
                rows=[
                    ("2019-01-01T00:00:00+00:00", 1.0),
                    ("2020-01-02T00:00:00+00:00", 10.0),
                ],
                pnl=5.0,
                hit=1,
                saved_at="2020-01-03T00:00:00+00:00",
                lots=1,
            )
            self._account(
                root / "trades_zzz.sqlite",
                rows=[("2020-01-01T00:00:00+00:00", 7.0)],
                pnl=-3.0,
                hit=0,
                saved_at="2019-06-01T00:00:00+00:00",
                lots=0,
            )
            (root / "champions.json").write_text(json.dumps({
                "champions": [
                    {"name": "aaa", "closed": 1, "pnl": 5.0, "wins": 1},
                    {"name": "zzz", "closed": 1, "pnl": -3.0, "wins": 0},
                ],
                "synced_until": "2020-01-02T00:00:00+00:00",
            }))
            (root / "discovery_log.json").write_text(json.dumps([{
                "strategy": "aaa",
                "tested_at": "2020-01-01T00:00:00+00:00",
                "qualified": False,
                "train_pnl": 1,
                "test_pnl": 1,
                "sharpe": 0.1,
                "win_rate_pct": 40,
                "trades": 5,
            }]))
            (root / "graduated.json").write_text(json.dumps([{
                "name": "old",
                "status": "GRADUATED_PAPER",
                "graduated_at": "2019-01-01T00:00:00+00:00",
                "closed_trades": 30,
                "total_pnl": 1,
                "wins": 20,
                "win_rate_pct": 60,
                "trade_history": [],
            }]))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                from hedge_fund.trading.stamps import HEARTBEAT_STAMP, write_json_stamp, write_pipeline_stamp

                write_pipeline_stamp("run_isolated", "finished")
                write_json_stamp(HEARTBEAT_STAMP, {
                    "last_pass_at": "2020-01-04T00:00:00+00:00",
                    "closed": 0,
                    "accounts_checked": 2,
                })
                _clear_caches()
                status = build_status()
                summary = build_summary()

        self.assertEqual(status["running_now"]["cycle"]["last_cycle_at"], "2020-01-02T00:00:00+00:00")
        self.assertEqual(status["running_now"]["cycle"]["account_saved_at"], "2020-01-03T00:00:00+00:00")
        self.assertEqual(status["running_now"]["open_lots"], 1)
        self.assertEqual(status["in_progress"]["tournament"]["champions_per_cycle"], 2)
        self.assertIn("live_history", status["running_now"])
        # updated follows store_dbs() order (last file), not max(ts).
        self.assertEqual(summary["updated"], "2020-01-01T00:00:00+00:00")
        self.assertEqual(summary["equity"], 17.0)
        self.assertEqual(summary["closed_trades"], 2)
        self.assertEqual(summary["total_pnl"], 2.0)
        self.assertEqual(summary["win_rate"], 0.5)
        self.assertEqual(summary["account_count"], 2)
        self.assertIn("live_history", summary)
        self.assertEqual(_status_shape(status), set(STATUS_KEYS))
        self.assertEqual(set(_dotted(summary)), set(SUMMARY_KEYS))

    def test_empty_summary_does_not_create_sqlite(self):
        from hedge_fund.web.server import build_summary

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                _clear_caches()
                summary = build_summary()
            self.assertFalse((Path(tmp) / "trades.sqlite").exists())
        self.assertIsNone(summary["equity"])
        self.assertEqual(summary["closed_trades"], 0)
        self.assertEqual(summary["account_count"], 1)
        self.assertIn("live_history", summary)

    def test_equity_ts_index_is_idempotent_and_history_curve_remains(self):
        from hedge_fund.trading.store import TradeStore, ensure_equity_ts_index

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades_alpha.sqlite"
            raw = sqlite3.connect(path)
            raw.execute(
                "CREATE TABLE equity_snapshots (ts TEXT NOT NULL, equity REAL NOT NULL, "
                "baseline REAL, note TEXT)"
            )
            raw.execute(
                "INSERT INTO equity_snapshots (ts,equity,baseline,note) VALUES (?,?,?,?)",
                ("2020-01-01T00:00:00+00:00", 1.0, None, ""),
            )
            raw.commit()
            raw.close()
            store = TradeStore(path)
            names = [row[1] for row in store.conn.execute("PRAGMA index_list('equity_snapshots')")]
            self.assertIn("idx_eq_ts", names)
            ensure_equity_ts_index(store.conn)
            ensure_equity_ts_index(store.conn)
            store.snapshot_equity(2.0, 1.0, "second")
            hist = store.equity_history()
            self.assertEqual(len(hist), 2)
            self.assertEqual(hist[-1]["equity"], 2.0)
            last = store.last_equity_snapshot()
            self.assertEqual(last["equity"], 2.0)
            store.close()
            ro = TradeStore.open_readonly(path)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    ro.conn.execute(
                        "INSERT INTO equity_snapshots (ts,equity,baseline,note) VALUES ('z',1,1,'')"
                    )
                self.assertEqual(ro.last_equity_snapshot()["equity"], 2.0)
            finally:
                ro.close()

    def test_readonly_open_skips_schema_script(self):
        from hedge_fund.trading.store import TradeStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades_alpha.sqlite"
            store = TradeStore(path)
            store.snapshot_equity(3.0, None, "")
            store.close()
            sqls: list[str] = []
            real = sqlite3.connect

            def wrapped(database, *args, **kwargs):
                conn = real(database, *args, **kwargs)
                conn.set_trace_callback(sqls.append)
                return conn

            with patch("sqlite3.connect", wrapped):
                ro = TradeStore.open_readonly(path)
                self.assertEqual(ro.last_equity_snapshot()["equity"], 3.0)
                ro.close()
        joined = " ".join(sqls).lower()
        self.assertNotIn("create table", joined)
        self.assertNotIn("executescript", joined)
        self.assertTrue(any("limit" in s.lower() for s in sqls))

    def test_single_flight_builds_once(self):
        from hedge_fund.web.ttl_cache import TtlSingleFlight

        cache = TtlSingleFlight(ttl_seconds=5)
        calls = []
        barrier = threading.Barrier(4)

        def builder():
            calls.append(1)
            time.sleep(0.2)
            return {"n": 1, "rows": [1, 2, 3]}

        out = []

        def worker():
            barrier.wait()
            out.append(cache.get("k", builder))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(out), 4)
        self.assertTrue(all(item == {"n": 1, "rows": [1, 2, 3]} for item in out))
        out[0]["rows"].append(9)
        again = cache.get("k", builder)
        self.assertEqual(again["rows"], [1, 2, 3])
        self.assertEqual(len(calls), 1)

    def _account(self, path: Path, rows, pnl: float, hit: int, saved_at: str, lots: int) -> None:
        from hedge_fund.trading.store import TradeStore

        store = TradeStore(path)
        store.conn.executemany(
            "INSERT INTO equity_snapshots (ts,equity,baseline,note) VALUES (?,?,?,?)",
            [(ts, equity, 10_000.0, "") for ts, equity in rows],
        )
        store.conn.execute(
            """INSERT INTO trades (symbol,timeframe,entry_ts,entry_price,exit_ts,exit_price,pnl,hit)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("BTC/USDT", "5m", rows[0][0], 1.0, rows[-1][0], 2.0, pnl, hit),
        )
        store.conn.commit()
        broker_lots = []
        if lots:
            broker_lots.append({
                "lot_id": 1,
                "ticker": "BTC/USDT",
                "quantity": 0.01,
                "entry_price": 100.0,
                "stop_loss": 90.0,
                "entry_fee": 0.1,
                "entry_condition": "test_long",
            })
        store.save_account_state({
            "broker": {"cash": 9_000.0, "lots": broker_lots},
            "saved_at": saved_at,
        })
        store.close()


class SummaryDoesNotFetchTests(unittest.TestCase):
    def test_api_summary_does_not_call_live_report(self):
        from hedge_fund.web.server import Handler

        called = []

        def slow(*_a, **_k):
            called.append(1)
            time.sleep(5)

        with tempfile.TemporaryDirectory() as tmp:
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict(os.environ, {"PAPER_STATE": str(tmp)}):
                    with patch("hedge_fund.web.server.live_report", side_effect=slow):
                        with patch("hedge_fund.web.live.live_prices", side_effect=AssertionError("prices")):
                            _clear_caches()
                            t0 = time.monotonic()
                            raw = urlopen(f"http://127.0.0.1:{port}/api/summary", timeout=2)
                            elapsed = time.monotonic() - t0
                            body = json.loads(raw.read().decode())
                self.assertEqual(raw.status, 200)
                self.assertLess(elapsed, 1.5)
                self.assertEqual(called, [])
                self.assertIn("live_history", body)
                self.assertEqual(body["closed_trades"], 0)
                self.assertIn("account_count", body)
            finally:
                httpd.shutdown()
                thread.join(timeout=3)
                httpd.server_close()

    def test_dashboard_route_still_calls_live_report(self):
        src = (REPO / "hedge_fund" / "web" / "server.py").read_text()
        self.assertIn('route == "/dashboard" or route == "/"', src)
        self.assertIn("live_report()  # re-price open positions live before serving", src)
        self.assertIn("start_report_refresh()", src)
        summary_at = src.index('elif route == "/api/summary"')
        learning_at = src.index('elif route == "/api/learning"')
        summary_block = src[summary_at:learning_at]
        self.assertNotIn("live_report()", summary_block)
        self.assertIn("build_summary()", summary_block)


class ManyAccountBenchmarkTests(unittest.TestCase):
    def test_150_accounts_10k_rows_skip_full_history_and_finish_under_1s(self):
        from hedge_fund.web.server import build_summary
        from hedge_fund.web.status import build_status

        n_accounts = 150
        n_rows = 10_000
        rows = [(f"{i:08d}", 10_000.0 + (i % 1000), 10_000.0, "") for i in range(n_rows)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in range(n_accounts):
                db = root / f"trades_acct{n:03d}.sqlite"
                conn = sqlite3.connect(db)
                conn.executescript(
                    """
                    CREATE TABLE equity_snapshots (
                        ts TEXT NOT NULL, equity REAL NOT NULL, baseline REAL, note TEXT
                    );
                    CREATE INDEX idx_eq_ts ON equity_snapshots(ts);
                    CREATE TABLE trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                        entry_ts TEXT NOT NULL, entry_price REAL NOT NULL,
                        exit_ts TEXT, exit_price REAL, pnl REAL, hit INTEGER
                    );
                    CREATE TABLE account_state (k TEXT PRIMARY KEY, v TEXT);
                    """
                )
                conn.executemany(
                    "INSERT INTO equity_snapshots (ts,equity,baseline,note) VALUES (?,?,?,?)",
                    rows,
                )
                conn.execute(
                    """INSERT INTO trades (symbol,timeframe,entry_ts,entry_price,exit_ts,exit_price,pnl,hit)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    ("BTC/USDT", "5m", "00000000", 1.0, "00000001", 2.0, 1.5, 1),
                )
                conn.commit()
                conn.close()

            sqls: list[str] = []
            uris: list[str] = []
            real = sqlite3.connect

            def wrapped(database, *args, **kwargs):
                if kwargs.get("uri") or (isinstance(database, str) and str(database).startswith("file:")):
                    uris.append(str(database))
                conn = real(database, *args, **kwargs)
                conn.set_trace_callback(sqls.append)
                return conn

            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                _clear_caches()
                with patch("sqlite3.connect", wrapped):
                    t0 = time.perf_counter()
                    status = build_status()
                    status_s = time.perf_counter() - t0
                    t1 = time.perf_counter()
                    summary = build_summary()
                    summary_s = time.perf_counter() - t1
                t2 = time.perf_counter()
                with patch("sqlite3.connect", side_effect=AssertionError("cache miss opened sqlite")):
                    cached_status = build_status()
                    cached_summary = build_summary()
                cached_s = time.perf_counter() - t2

        self.assertLess(status_s, 1.0, f"build_status took {status_s:.3f}s")
        self.assertLess(summary_s, 1.0, f"build_summary took {summary_s:.3f}s")
        self.assertLess(cached_s, 0.05, f"cached pair took {cached_s:.3f}s")
        # Status opens each DB once. Summary reuses the mtime cache.
        self.assertEqual(len(uris), n_accounts)
        self.assertTrue(all("mode=ro" in uri and "immutable=1" not in uri for uri in uris))
        scans = [sql for sql in sqls if _is_full_equity_scan(sql)]
        self.assertEqual(scans, [])
        self.assertFalse(any("create table" in sql.lower() for sql in sqls))
        self.assertTrue(any("order by ts desc limit 1" in " ".join(sql.lower().split()) for sql in sqls))
        self.assertEqual(status["running_now"]["accounts"]["count"], n_accounts)
        self.assertEqual(status["running_now"]["cycle"]["last_cycle_at"], "00009999")
        self.assertEqual(status["in_progress"]["tournament"]["champions_per_cycle"], 1)
        self.assertIn("live_history", status["running_now"])
        # Last row equity is 10000 + (9999 % 1000) = 10999, not the sum of history.
        self.assertEqual(summary["equity"], n_accounts * 10_999.0)
        self.assertEqual(summary["closed_trades"], n_accounts)
        self.assertEqual(summary["total_pnl"], n_accounts * 1.5)
        self.assertEqual(summary["win_rate"], 1.0)
        self.assertEqual(summary["updated"], "00009999")
        self.assertEqual(summary["account_count"], n_accounts)
        self.assertIn("live_history", summary)
        self.assertEqual(cached_status["running_now"]["cycle"]["last_cycle_at"], "00009999")
        self.assertEqual(cached_summary["equity"], summary["equity"])


class StaleWhileRevalidateTests(unittest.TestCase):
    def setUp(self) -> None:
        _clear_caches()

    def test_request_stays_fast_while_rebuild_is_slow(self):
        from hedge_fund.web.status import build_status, refresh_status

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                _clear_caches()
                original = build_status()
                started = threading.Event()
                release = threading.Event()

                def slow():
                    started.set()
                    self.assertTrue(release.wait(5), "slow rebuild was not released")
                    fresh = dict(original)
                    fresh["as_of"] = "2099-01-01T00:00:00+00:00"
                    return fresh

                with patch("hedge_fund.web.status._build_status_uncached", side_effect=slow):
                    worker = threading.Thread(target=refresh_status, daemon=True)
                    worker.start()
                    self.assertTrue(started.wait(2), "rebuild did not start")
                    t0 = time.perf_counter()
                    served = build_status()
                    elapsed = time.perf_counter() - t0
                    release.set()
                    worker.join(timeout=3)
                    after = build_status()
        self.assertLess(elapsed, 0.2, f"request waited {elapsed:.3f}s on the rebuild")
        self.assertEqual(served["as_of"], original["as_of"])
        self.assertEqual(served["running_now"]["cycle"]["last_cycle_at"], original["running_now"]["cycle"]["last_cycle_at"])
        self.assertIn("live_history", served["running_now"])
        self.assertIn("champions_per_cycle", served["in_progress"]["tournament"])
        self.assertEqual(after["as_of"], "2099-01-01T00:00:00+00:00")

    def test_rebuild_error_keeps_previous_payload(self):
        import io
        from contextlib import redirect_stderr

        from hedge_fund.web.status import build_status, refresh_status

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                _clear_caches()
                original = build_status()
                buf = io.StringIO()
                with patch(
                    "hedge_fund.web.status._build_status_uncached",
                    side_effect=RuntimeError("disk gone"),
                ):
                    with redirect_stderr(buf):
                        refresh_status()
                    served = build_status()
        log = buf.getvalue()
        self.assertIn("disk gone", log)
        self.assertIn(original["as_of"], log)
        self.assertIn("age=", log)
        self.assertEqual(served["as_of"], original["as_of"])
        self.assertEqual(served["paper_only"], True)
        self.assertIn("running_now", served)
        self.assertIn("in_progress", served)
        self.assertIn("live_history", served["running_now"])

    def test_unchanged_account_is_not_reopened(self):
        from hedge_fund.trading.store import TradeStore, clear_account_read_cache, read_paper_account

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades_alpha.sqlite"
            store = TradeStore(path)
            store.snapshot_equity(4.0, None, "")
            store.close()
            clear_account_read_cache()
            self.assertEqual(read_paper_account(path)["snapshot"]["equity"], 4.0)
            real = sqlite3.connect
            calls = []

            def wrapped(database, *args, **kwargs):
                calls.append(database)
                return real(database, *args, **kwargs)

            with patch("sqlite3.connect", wrapped):
                again = read_paper_account(path)
            self.assertEqual(calls, [])
            self.assertEqual(again["snapshot"]["equity"], 4.0)
            os.utime(path, None)
            with patch("sqlite3.connect", wrapped):
                store = TradeStore(path)
                store.snapshot_equity(9.0, None, "")
                store.close()
                calls.clear()
                updated = read_paper_account(path)
            self.assertEqual(updated["snapshot"]["equity"], 9.0)
            self.assertTrue(calls)


class FrontendPollTests(unittest.TestCase):
    def test_status_poll_is_30s_and_skips_while_in_flight(self):
        bar = (REPO / "frontend" / "src" / "status" / "StatusBar.tsx").read_text()
        champs = (REPO / "frontend" / "src" / "pages" / "Champions.tsx").read_text()
        poll = (REPO / "frontend" / "src" / "api" / "poll.ts").read_text()
        main = (REPO / "frontend" / "src" / "main.tsx").read_text()
        self.assertIn("pollInterval(STATUS_POLL_MS)", bar)
        self.assertIn("pollInterval(STATUS_POLL_MS)", champs)
        self.assertNotIn("15_000", bar)
        self.assertNotIn("refetchInterval: 15_000", champs)
        self.assertIn("STATUS_POLL_MS = 30_000", poll)
        self.assertIn('fetchStatus === "fetching"', poll)
        self.assertIn("pollInterval(30_000)", main)
        server = (REPO / "hedge_fund" / "web" / "server.py").read_text()
        self.assertIn("start_read_refresh()", server)
        self.assertIn("READ_REBUILD_SECONDS = 10.0", (REPO / "hedge_fund" / "web" / "ttl_cache.py").read_text())


if __name__ == "__main__":
    unittest.main()
