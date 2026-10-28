"""Chunked tape load: SQL shape, and peak RSS on a prod-sized book."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import numpy as np

from hedge_fund.data.candle_store import TAPE_LOAD_CHUNK, TAPE_STATEMENT_TIMEOUT
from hedge_fund.trading.live_tape import TAPE_DTYPE

# Prod candle book is about this many 5m bars per symbol (BTC and ETH).
PROD_BARS = 605_000


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Cursor:
    def __init__(self, rows, chunk):
        self._rows = rows
        self._chunk = chunk
        self._i = 0
        self.fetches: list[int] = []
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchmany(self, n):
        self.fetches.append(n)
        batch = self._rows[self._i : self._i + n]
        self._i += n
        return batch


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.statements: list[str] = []
        self.cursor_obj: _Cursor | None = None

    def execute(self, sql, params=None):
        self.statements.append(sql)
        if "COUNT" in sql:
            return _Result((len(self.rows),))
        return _Result(None)

    def cursor(self, name=None):
        self.cursor_obj = _Cursor(self.rows, TAPE_LOAD_CHUNK)
        self.cursor_name = name
        return self.cursor_obj


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


class LoadTapeShapeTests(unittest.TestCase):
    def test_chunks_are_cast_in_sql_and_timeout_is_local(self):
        from hedge_fund.data import candle_store

        rows = [
            (1_700_000_000_000 + i * 300_000, 1.0, 2.0, 0.5, 1.5, 3.0)
            for i in range(120)
        ]
        conn = _Conn(rows)
        with patch.object(candle_store, "migrate", lambda: None), \
                patch.object(candle_store, "_pool", lambda: _Pool(conn)), \
                patch.object(candle_store, "TAPE_LOAD_CHUNK", 40):
            # The cursor was built with the module constant at import of the
            # test helper. load_tape reads the patched constant.
            arr = candle_store.load_tape("BTC/USDT")
        self.assertEqual(len(arr), 120)
        self.assertEqual(int(arr[0]["ts"]), rows[0][0])
        self.assertEqual(float(arr[-1]["close"]), 1.5)
        joined = "\n".join(conn.statements)
        self.assertIn("SET LOCAL statement_timeout", joined)
        self.assertIn(TAPE_STATEMENT_TIMEOUT, joined)
        assert conn.cursor_obj is not None
        self.assertIn("::bigint", conn.cursor_obj.sql)
        self.assertIn("::float8", conn.cursor_obj.sql)
        self.assertNotIn("open_time,", conn.cursor_obj.sql)
        self.assertEqual(conn.cursor_obj.fetches, [40, 40, 40])
        self.assertTrue(str(conn.cursor_name).startswith("paperbot_tape_"))


class ProdSizedMemoryTests(unittest.TestCase):
    def test_both_symbols_stay_under_150mb(self):
        script = textwrap.dedent(
            f"""
            import json, time
            from datetime import datetime, timezone
            from decimal import Decimal

            N = {PROD_BARS}
            CHUNK = {TAPE_LOAD_CHUNK}

            def hwm_kb():
                for line in open("/proc/self/status"):
                    if line.startswith("VmHWM:"):
                        return int(line.split()[1])
                return 0

            def legacy():
                rows = []
                for sym in range(2):
                    block = []
                    for i in range(N):
                        block.append((
                            datetime.fromtimestamp(1_600_000_000 + i * 300, tz=timezone.utc),
                            Decimal(1), Decimal(2), Decimal("0.5"), Decimal(3), Decimal(4),
                        ))
                    rows.append(block)
                return len(rows)

            def chunked():
                import gc
                from hedge_fund.data.candle_store import fill_tape_from_chunks
                from hedge_fund.trading.live_tape import TAPE_DTYPE

                gc.collect()
                base = rss_kb()

                def chunks():
                    i = 0
                    while i < N:
                        m = min(CHUNK, N - i)
                        yield [
                            (1_600_000_000_000 + (i + j) * 300_000, 1.0, 2.0, 0.5, 1.5, 3.0)
                            for j in range(m)
                        ]
                        i += m

                arrays = []
                for _sym in range(2):
                    arrays.append(fill_tape_from_chunks(N, chunks(), dtype=TAPE_DTYPE))
                return sum(len(arr) for arr in arrays), base

            def rss_kb():
                for line in open("/proc/self/status"):
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
                return 0

            which = __import__("sys").argv[1]
            base = rss_kb()
            t0 = time.perf_counter()
            if which == "legacy":
                n = legacy()
                growth = hwm_kb() - base
            else:
                n, base = chunked()
                growth = hwm_kb() - base
            elapsed = time.perf_counter() - t0
            print(json.dumps({{"mode": which, "n": n, "seconds": elapsed, "hwm_kb": hwm_kb(), "growth_kb": growth}}))
            """
        )
        def run(mode: str) -> dict:
            proc = subprocess.run(
                [sys.executable, "-c", script, mode],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
            line = proc.stdout.strip().splitlines()[-1]
            return json.loads(line)

        before = run("legacy")
        after = run("chunked")
        print(
            f"TAPE_RSS before_hwm_mb={before['hwm_kb'] / 1024:.1f} "
            f"before_s={before['seconds']:.2f} "
            f"after_hwm_mb={after['hwm_kb'] / 1024:.1f} "
            f"after_s={after['seconds']:.2f}"
        )
        self.assertEqual(after["n"], PROD_BARS * 2)
        self.assertLess(after["growth_kb"] / 1024, 150.0)
        self.assertGreater(before["hwm_kb"], after["hwm_kb"])
