"""Postgres candle store.

``PAPERBOT_DATABASE_URL`` is the libpq/URI connection string. Production
sets ``sslmode=require`` in that string (the host is a private WireGuard
address). This module does not rewrite the URL and does not log it.

The server is shared (``max_connections`` 30). Each process keeps at most
two connections and closes idle ones. Web, cycle, and the updater are
separate processes, so the cap is per process, not cluster-wide.

Readers should call :func:`candle_store_selected`. It stays false until
both BTC/USDT and ETH/USDT cover the canonical tape through a recent
closed bar, so a partial backfill does not replace the file tape.
"""
from __future__ import annotations

import atexit
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import psycopg

from hedge_fund.data.candle_quality import (
    dt_to_ms,
    empty_gap_summary,
    iso_ms,
    last_closed_open_ms,
    ms_to_dt,
    summarize_breaks,
)
from hedge_fund.trading.constants import (
    QUAL_CANONICAL_START_MS,
    QUAL_SYMBOLS,
    QUAL_TIMEFRAME,
    TF_MS_5M,
)

EXCHANGE = "binance"
POOL_MAX_CAP = 2
# Caught up enough that the store, not the file tape, is the source.
# A backfill still walking 2022 does not pass this. After it does, the
# 5-minute updater keeps the tail inside this window.
READY_SLACK_MS = 36 * 60 * 60 * 1000
_MIGRATION_PATH = Path(__file__).resolve().parent / "migrations" / "001_candles.sql"

UPSERT_SQL = """
INSERT INTO candles (
    exchange, symbol, timeframe, open_time, open, high, low, close, volume
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (exchange, symbol, timeframe, open_time) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume
"""

_pools: dict[str, CandlePool] = {}
_pools_lock = threading.Lock()
_migrated: set[str] = set()
_ready = False
_quality: tuple[float, dict] | None = None
_quality_lock = threading.Lock()
_ssl_warned = False
_QUALITY_TTL = 60.0


class PoolTimeout(TimeoutError):
    """Every connection in this process is already checked out."""


class CandlePool:
    """At most ``max_size`` connections (1 or 2). Idle ones are closed."""

    def __init__(
        self,
        dsn: str,
        *,
        max_size: int = 2,
        max_idle: float = 30.0,
        timeout: float = 15.0,
        connect=None,
    ) -> None:
        if max_size < 1 or max_size > POOL_MAX_CAP:
            raise ValueError(f"paperbot pool max_size must be 1 or 2, got {max_size}")
        self._dsn = dsn
        self._max = int(max_size)
        self._max_idle = float(max_idle)
        self._timeout = float(timeout)
        self._connect = connect or self._default_connect
        self._idle: list = []
        self._out = 0
        self._cv = threading.Condition()
        self._closed = False

    def _default_connect(self):
        return psycopg.connect(
            self._dsn,
            connect_timeout=3,
            application_name=os.environ.get("PAPERBOT_DB_APP_NAME", "paperbot")[:64],
        )

    def _prepare(self, conn):
        # Bound a stuck query so one paperbot process cannot sit on the
        # shared server. The timeout lives for the session (committed).
        conn.execute("SET statement_timeout = '60s'")
        conn.execute("SET idle_in_transaction_session_timeout = '60s'")
        conn.commit()
        return conn

    @contextmanager
    def connection(self):
        conn = self._checkout()
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            self._checkin(conn, broken=True)
            raise
        self._checkin(conn, broken=False)

    def _checkout(self):
        deadline = time.monotonic() + self._timeout
        while True:
            spawn = False
            conn = None
            with self._cv:
                if self._closed:
                    raise RuntimeError("candle pool is closed")
                if self._idle:
                    conn = self._idle.pop()
                    self._out += 1
                elif self._out < self._max:
                    self._out += 1
                    spawn = True
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise PoolTimeout("candle store pool exhausted (max 2)")
                    self._cv.wait(timeout=remaining)
                    continue
            if spawn:
                try:
                    return self._prepare(self._connect())
                except Exception:
                    with self._cv:
                        self._out -= 1
                        self._cv.notify()
                    raise
            try:
                conn.execute("SELECT 1")
                conn.rollback()
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                try:
                    return self._prepare(self._connect())
                except Exception:
                    with self._cv:
                        self._out -= 1
                        self._cv.notify()
                    raise

    def _checkin(self, conn, *, broken: bool) -> None:
        with self._cv:
            self._out = max(0, self._out - 1)
            dead = self._closed or broken or bool(getattr(conn, "closed", False))
            if dead:
                try:
                    conn.close()
                except Exception:
                    pass
            else:
                self._idle.append(conn)
                timer = threading.Timer(self._max_idle, self._retire, args=(conn,))
                timer.daemon = True
                timer.start()
            self._cv.notify()

    def _retire(self, conn) -> None:
        with self._cv:
            try:
                self._idle.remove(conn)
            except ValueError:
                return
        try:
            conn.close()
        except Exception:
            pass

    def close(self) -> None:
        with self._cv:
            self._closed = True
            idle = list(self._idle)
            self._idle.clear()
            self._cv.notify_all()
        for conn in idle:
            try:
                conn.close()
            except Exception:
                pass

    def idle_count(self) -> int:
        with self._cv:
            return len(self._idle)

    def in_use(self) -> int:
        with self._cv:
            return self._out


def database_url() -> str | None:
    raw = os.environ.get("PAPERBOT_DATABASE_URL", "").strip()
    return raw or None


def configured() -> bool:
    return database_url() is not None


def pool_max_size() -> int:
    """1 or 2. A larger env value is clamped so a typo cannot exhaust the server."""
    raw = os.environ.get("PAPERBOT_DB_POOL_MAX", "").strip()
    if not raw:
        return POOL_MAX_CAP
    try:
        n = int(raw)
    except ValueError:
        return POOL_MAX_CAP
    if n < 1:
        return 1
    return min(POOL_MAX_CAP, n)


def pool_idle_seconds() -> float:
    raw = os.environ.get("PAPERBOT_DB_POOL_IDLE_SECONDS", "").strip()
    if not raw:
        return 30.0
    try:
        n = float(raw)
    except ValueError:
        return 30.0
    return min(120.0, max(1.0, n))


def migration_sql() -> str:
    return _MIGRATION_PATH.read_text()


def _warn_ssl(dsn: str) -> None:
    global _ssl_warned
    if _ssl_warned or "sslmode=" in dsn:
        return
    lowered = dsn.lower()
    if "localhost" in lowered or "127.0.0.1" in lowered:
        return
    _ssl_warned = True
    print(
        "[candles] PAPERBOT_DATABASE_URL has no sslmode=; production must use sslmode=require",
        flush=True,
    )


def _pool() -> CandlePool:
    dsn = database_url()
    if not dsn:
        raise RuntimeError("PAPERBOT_DATABASE_URL is not set")
    _warn_ssl(dsn)
    with _pools_lock:
        pool = _pools.get(dsn)
        if pool is None:
            pool = CandlePool(dsn, max_size=pool_max_size(), max_idle=pool_idle_seconds())
            _pools[dsn] = pool
        return pool


def close_store() -> None:
    """Close idle connections and forget readiness. Used by tests and process exit."""
    global _ready, _quality
    with _pools_lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.close()
    _migrated.clear()
    _ready = False
    with _quality_lock:
        _quality = None


def clear_ready() -> None:
    """Drop the in-process 'store is the tape' latch after the table is wiped."""
    global _ready
    _ready = False


atexit.register(close_store)


def _check_symbol(symbol: str) -> None:
    if symbol not in QUAL_SYMBOLS:
        raise ValueError(f"candle store is BTC/USDT and ETH/USDT only, got {symbol}")


def migrate() -> None:
    dsn = database_url()
    if not dsn:
        raise RuntimeError("PAPERBOT_DATABASE_URL is not set")
    if dsn in _migrated:
        return
    sql = migration_sql()
    with _pool().connection() as conn:
        conn.execute(sql)
    _migrated.add(dsn)


def _invalidate_quality() -> None:
    global _quality
    with _quality_lock:
        _quality = None


def upsert_ohlcv(symbol: str, rows: list, *, timeframe: str = QUAL_TIMEFRAME) -> int:
    """Insert or replace closed OHLCV rows. Returns the number of rows sent."""
    _check_symbol(symbol)
    if timeframe != QUAL_TIMEFRAME:
        raise ValueError(f"candle store timeframe is {QUAL_TIMEFRAME}, got {timeframe}")
    payload = []
    for row in rows:
        ts = int(row[0])
        vol = float(row[5]) if len(row) > 5 and row[5] is not None else 0.0
        payload.append(
            (
                EXCHANGE,
                symbol,
                timeframe,
                ms_to_dt(ts),
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                vol,
            )
        )
    if not payload:
        return 0
    migrate()
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, payload)
    _invalidate_quality()
    return len(payload)


def symbol_bounds(symbol: str) -> tuple[int, int, int] | None:
    """``(min_open_ms, max_open_ms, bars)`` or None when the symbol has no rows."""
    _check_symbol(symbol)
    migrate()
    with _pool().connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)::bigint, MIN(open_time), MAX(open_time)
            FROM candles
            WHERE exchange = %s AND symbol = %s AND timeframe = %s
            """,
            (EXCHANGE, symbol, QUAL_TIMEFRAME),
        ).fetchone()
    if row is None or int(row[0]) == 0 or row[1] is None or row[2] is None:
        return None
    return dt_to_ms(row[1]), dt_to_ms(row[2]), int(row[0])


def latest_open_ms(symbol: str) -> int | None:
    _check_symbol(symbol)
    migrate()
    with _pool().connection() as conn:
        row = conn.execute(
            """
            SELECT MAX(open_time)
            FROM candles
            WHERE exchange = %s AND symbol = %s AND timeframe = %s
            """,
            (EXCHANGE, symbol, QUAL_TIMEFRAME),
        ).fetchone()
    if row is None or row[0] is None:
        return None
    return dt_to_ms(row[0])


def gap_edges(symbol: str) -> list[tuple[int, int]]:
    """``(prev_open_ms, next_open_ms)`` for steps that are not exactly 5m."""
    _check_symbol(symbol)
    migrate()
    with _pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT prev, open_time
            FROM (
                SELECT open_time,
                       LAG(open_time) OVER (ORDER BY open_time) AS prev
                FROM candles
                WHERE exchange = %s AND symbol = %s AND timeframe = %s
            ) s
            WHERE prev IS NOT NULL
              AND open_time - prev <> interval '5 minutes'
            """,
            (EXCHANGE, symbol, QUAL_TIMEFRAME),
        ).fetchall()
    return [(dt_to_ms(prev), dt_to_ms(nxt)) for prev, nxt in rows]


def _symbol_quality(symbol: str) -> dict:
    bounds = symbol_bounds(symbol)
    if bounds is None:
        return {
            "bars": 0,
            "first_bar_time": None,
            "last_bar_time": None,
            "last_candle_time": None,
            **empty_gap_summary(),
        }
    min_ms, max_ms, n = bounds
    summary = summarize_breaks(gap_edges(symbol))
    return {
        "bars": n,
        "first_bar_time": iso_ms(min_ms),
        "last_bar_time": iso_ms(max_ms),
        "last_candle_time": iso_ms(max_ms),
        **summary,
    }


def quality_summary(*, force: bool = False) -> dict:
    """Gap / monotonic snapshot for both symbols. Cached for a minute."""
    global _quality
    if not configured():
        return {
            "gap_count": None,
            "known_gap_count": None,
            "duplicates": None,
            "monotonic": None,
            "last_candle_time": None,
            "symbols": {},
        }
    now = time.monotonic()
    with _quality_lock:
        if not force and _quality is not None and (now - _quality[0]) < _QUALITY_TTL:
            return _quality[1]
    symbols = {sym: _symbol_quality(sym) for sym in QUAL_SYMBOLS}
    lasts = [
        symbols[sym]["last_candle_time"]
        for sym in QUAL_SYMBOLS
        if symbols[sym]["last_candle_time"]
    ]
    payload = {
        "gap_count": sum(int(symbols[sym]["gap_count"]) for sym in QUAL_SYMBOLS),
        "known_gap_count": sum(int(symbols[sym]["known_gap_count"]) for sym in QUAL_SYMBOLS),
        "duplicates": sum(int(symbols[sym]["duplicates"]) for sym in QUAL_SYMBOLS),
        "monotonic": all(bool(symbols[sym]["monotonic"]) for sym in QUAL_SYMBOLS),
        "last_candle_time": min(lasts) if lasts else None,
        "symbols": symbols,
    }
    with _quality_lock:
        _quality = (time.monotonic(), payload)
    return payload


def _coverage_ok() -> bool:
    last = last_closed_open_ms(int(time.time() * 1000))
    for sym in QUAL_SYMBOLS:
        bounds = symbol_bounds(sym)
        if bounds is None:
            return False
        min_ms, max_ms, _n = bounds
        if min_ms > QUAL_CANONICAL_START_MS + TF_MS_5M:
            return False
        if max_ms < last - READY_SLACK_MS:
            return False
    return True


def store_is_ready() -> bool:
    """True once both symbols cover the canonical start through a recent bar.

    The true result is latched for the process so a later blip does not
    flip the shared-tape hash back to the file copy. :func:`clear_ready`
    drops the latch when a read finds the table empty.
    """
    global _ready
    if not configured():
        return False
    if _ready:
        return True
    try:
        ok = _coverage_ok()
    except Exception as exc:
        print(f"[candles] readiness check failed: {exc}", flush=True)
        return False
    if ok:
        _ready = True
    return ok


def candle_store_selected() -> bool:
    """Prod should read the store instead of the file tape."""
    if not configured() or not store_is_ready():
        return False
    try:
        for sym in QUAL_SYMBOLS:
            if latest_open_ms(sym) is None:
                clear_ready()
                return False
        return True
    except Exception as exc:
        # The latch already said the store was the tape. Keep selecting it
        # so the caller surfaces the read error instead of hashing the npy.
        print(f"[candles] latest-bar check failed: {exc}", flush=True)
        return True


# One symbol of the canonical tape is ~605k bars. A chunk is small enough
# that the driver does not hold the whole result as Python rows, and large
# enough that the round-trip count stays in the dozens.
TAPE_LOAD_CHUNK = 50_000
# This read is the only statement allowed to outlive the session 60s cap.
# SET LOCAL dies with the transaction, so the pooled connection goes back
# to 60s. A throttled web process needs the extra room; other queries do not.
TAPE_STATEMENT_TIMEOUT = "10min"


def _tape_where(
    symbol: str,
    *,
    since_ms: int | None,
    until_ms: int | None,
) -> tuple[str, list]:
    clauses = "exchange = %s AND symbol = %s AND timeframe = %s"
    params: list = [EXCHANGE, symbol, QUAL_TIMEFRAME]
    if since_ms is not None:
        clauses += " AND open_time >= %s"
        params.append(ms_to_dt(int(since_ms)))
    if until_ms is not None:
        clauses += " AND open_time <= %s"
        params.append(ms_to_dt(int(until_ms)))
    return clauses, params


def _tape_select_sql(where: str) -> str:
    """Epoch ms and float8 in SQL, so the client never sees timestamps or decimals."""
    return f"""
        SELECT (EXTRACT(EPOCH FROM open_time) * 1000)::bigint,
               open::float8,
               high::float8,
               low::float8,
               close::float8,
               COALESCE(volume, 0)::float8
        FROM candles
        WHERE {where}
        ORDER BY open_time ASC
    """


def write_tape_chunk(arr: np.ndarray, offset: int, rows) -> int:
    """Copy one driver batch into ``arr`` at ``offset``. Returns the new offset.

    ``rows`` is a short chunk (see ``TAPE_LOAD_CHUNK``), not the whole tape.
    The caller drops it before fetching the next chunk.
    """
    n = len(rows)
    if n <= 0:
        return offset
    end = offset + n
    if end > len(arr):
        raise ValueError(f"tape chunk exceeds allocation ({end} > {len(arr)})")
    # One C-level conversion per chunk instead of a Python store per row.
    arr[offset:end] = np.array([tuple(row) for row in rows], dtype=arr.dtype)
    return end


def fill_tape_from_chunks(count: int, chunks, *, dtype) -> np.ndarray:
    """Preallocate ``count`` rows and fill them from successive chunks."""
    arr = np.empty(max(0, int(count)), dtype=dtype)
    offset = 0
    for rows in chunks:
        n = len(rows)
        if n <= 0:
            continue
        if offset + n > len(arr):
            grown = np.empty(offset + n, dtype=dtype)
            if offset:
                grown[:offset] = arr[:offset]
            arr = grown
        offset = write_tape_chunk(arr, offset, rows)
    if offset != len(arr):
        arr = arr[:offset]
    return arr


def load_tape(
    symbol: str,
    *,
    since_ms: int | None = None,
    until_ms: int | None = None,
) -> np.ndarray:
    """Ascending tape array for one symbol. Empty when the symbol has no rows.

    The SELECT casts time and prices in SQL. A server-side cursor fetches
    about 50k rows at a time into a preallocated array, then drops the
    chunk. ``statement_timeout`` is raised for this transaction only.
    """
    from hedge_fund.trading.live_tape import TAPE_DTYPE

    _check_symbol(symbol)
    migrate()
    where, params = _tape_where(symbol, since_ms=since_ms, until_ms=until_ms)
    count_sql = f"SELECT COUNT(*)::bigint FROM candles WHERE {where}"
    select_sql = _tape_select_sql(where)
    with _pool().connection() as conn:
        conn.execute(f"SET LOCAL statement_timeout = '{TAPE_STATEMENT_TIMEOUT}'")
        # The client converts each chunk between fetches; under CPU throttling
        # that gap must not trip the session's 60s idle-in-transaction cap.
        conn.execute(
            f"SET LOCAL idle_in_transaction_session_timeout = '{TAPE_STATEMENT_TIMEOUT}'"
        )
        counted = conn.execute(count_sql, params).fetchone()
        total = 0 if counted is None or counted[0] is None else int(counted[0])
        if total <= 0:
            return np.empty(0, dtype=TAPE_DTYPE)
        arr = np.empty(total, dtype=TAPE_DTYPE)
        offset = 0
        # Named cursor: the server holds the result; the client sees one chunk.
        with conn.cursor(name=f"paperbot_tape_{symbol.replace('/', '_')}") as cur:
            cur.itersize = TAPE_LOAD_CHUNK
            cur.execute(select_sql, params)
            while offset < total:
                rows = cur.fetchmany(TAPE_LOAD_CHUNK)
                if not rows:
                    break
                if offset + len(rows) > len(arr):
                    grown = np.empty(offset + len(rows), dtype=TAPE_DTYPE)
                    grown[:offset] = arr[:offset]
                    arr = grown
                offset = write_tape_chunk(arr, offset, rows)
                del rows
        if offset != len(arr):
            arr = arr[:offset]
    return arr


def tail_candles(symbol: str, n: int) -> list:
    """Newest ``n`` closed candles, oldest first. The live signal slice."""
    from hedge_fund.data.binance import Candle

    _check_symbol(symbol)
    if n <= 0:
        return []
    migrate()
    with _pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE exchange = %s AND symbol = %s AND timeframe = %s
            ORDER BY open_time DESC
            LIMIT %s
            """,
            (EXCHANGE, symbol, QUAL_TIMEFRAME, int(n)),
        ).fetchall()
    rows.reverse()
    return [
        Candle(
            ts=dt_to_ms(opened),
            open=float(o),
            high=float(h),
            low=float(low),
            close=float(c),
            volume=float(vol or 0.0),
        )
        for opened, o, h, low, c, vol in rows
    ]


def cached_health_snapshot() -> dict:
    """Last quality numbers already in memory. Does not open the database.

    ``/health`` calls this. A probe must not take the candle pool or wait
    on a tape rebuild.
    """
    if not configured():
        return {
            "configured": False,
            "ready": False,
            "last_candle_time": None,
            "gap_count": None,
            "known_gap_count": None,
            "monotonic": None,
            "duplicates": None,
        }
    snap = _quality[1] if _quality is not None else None
    if not isinstance(snap, dict):
        return {
            "configured": True,
            "ready": bool(_ready),
            "last_candle_time": None,
            "gap_count": None,
            "known_gap_count": None,
            "monotonic": None,
            "duplicates": None,
        }
    return {
        "configured": True,
        "ready": bool(_ready),
        "last_candle_time": snap.get("last_candle_time"),
        "gap_count": snap.get("gap_count"),
        "known_gap_count": snap.get("known_gap_count"),
        "monotonic": snap.get("monotonic"),
        "duplicates": snap.get("duplicates"),
    }


def health_snapshot() -> dict:
    """Last candle time and gap counts. Never raises for a missing URL.

    Database errors are returned in ``error`` so a caller can stay ok.
    The liveness probe uses :func:`cached_health_snapshot` instead.
    """
    if not configured():
        return {
            "configured": False,
            "ready": False,
            "last_candle_time": None,
            "gap_count": None,
            "known_gap_count": None,
            "monotonic": None,
            "duplicates": None,
        }
    try:
        quality = quality_summary()
        return {
            "configured": True,
            "ready": store_is_ready(),
            "last_candle_time": quality["last_candle_time"],
            "gap_count": quality["gap_count"],
            "known_gap_count": quality["known_gap_count"],
            "monotonic": quality["monotonic"],
            "duplicates": quality["duplicates"],
        }
    except Exception as exc:
        return {
            "configured": True,
            "ready": False,
            "last_candle_time": None,
            "gap_count": None,
            "known_gap_count": None,
            "monotonic": None,
            "duplicates": None,
            "error": str(exc),
        }
