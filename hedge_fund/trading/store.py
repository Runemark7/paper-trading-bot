"""SQLite trade/decision store — the honest audit trail.

The PROTOCOL.md contract requires every decision (approved, held, or
rejected) and every completed trade to be recorded durably. This module is
that store. The dashboard reads from it; the decision loop writes to it.

Schema
======
decisions  — one row per cycle per symbol: what the loop saw, proposed, decided.
trades     — completed trades: entry->exit, size, fees, P&L, stated probability.
equity_snapshots — equity over time, for the growth curve.

Outcomes are entered only when a position closes (horizon known), which is
what prevents lookahead: a recorded probability is always paired with the
outcome that resolved it, in the same row.
"""

from __future__ import annotations

import fcntl
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SQLITE_BUSY_TIMEOUT_MS = 30_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_sqlite(path: str | Path) -> sqlite3.Connection:
    """Open SQLite with WAL + busy timeout so sidecar/heartbeat/web can share a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def sqlite_write_lock(db_path: Path):
    """Exclusive file lock around a SQLite writer. Stops split-brain commits."""
    lock_path = Path(str(db_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


class TradeStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = connect_sqlite(self.path)
        self._init_schema()

    def _init_schema(self) -> None:
        c = self.conn.cursor()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                condition TEXT,
                proposed_prob REAL,
                calibrated_prob REAL,
                direction TEXT,
                action TEXT NOT NULL,      -- ENTER/HOLD/CLOSE_*/REJECTED
                reason TEXT,
                equity REAL,
                size REAL,
                entry REAL,
                raw_signal TEXT
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                condition TEXT,
                stated_prob REAL,
                entry_ts TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_ts TEXT,
                exit_price REAL,
                size REAL,
                exit_reason TEXT,          -- take_profit / stop_loss / manual
                entry_fee REAL,
                exit_fee REAL,
                pnl REAL,
                pnl_pct REAL,
                hit INTEGER,               -- 1 = prediction resolved, 0 = not
                lot_id INTEGER             -- identifies which broker lot this is
            );
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                ts TEXT NOT NULL,
                equity REAL NOT NULL,
                baseline REAL,
                note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_dec_ts ON decisions(ts);
            CREATE INDEX IF NOT EXISTS idx_tr_entry ON trades(entry_ts);
            CREATE INDEX IF NOT EXISTS idx_eq_ts ON equity_snapshots(ts);
            """
        )
        self.conn.commit()
        # migration: add lot_id column if the table predates it
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(trades)").fetchall()]
        if "lot_id" not in cols:
            self.conn.execute("ALTER TABLE trades ADD COLUMN lot_id INTEGER")
            self.conn.commit()

    # -- writes ----------------------------------------------------------
    def record_decision(
        self,
        symbol: str,
        timeframe: str,
        condition,
        proposed_prob,
        calibrated_prob,
        direction,
        action,
        reason,
        equity,
        size=0.0,
        entry=0.0,
        raw_signal=None,
    ) -> int:
        with sqlite_write_lock(self.path):
            cur = self.conn.execute(
                """INSERT INTO decisions
                   (ts,symbol,timeframe,condition,proposed_prob,calibrated_prob,
                    direction,action,reason,equity,size,entry,raw_signal)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (_now(), symbol, timeframe, condition, proposed_prob,
                 calibrated_prob, direction, action, reason, equity, size, entry,
                 json.dumps(raw_signal) if raw_signal else None),
            )
            self.conn.commit()
        return cur.lastrowid

    def open_trade(
        self,
        symbol: str,
        timeframe: str,
        condition: str,
        stated_prob: float,
        entry_price: float,
        size: float,
        entry_fee: float,
        lot_id: int | None = None,
    ) -> int:
        with sqlite_write_lock(self.path):
            cur = self.conn.execute(
                """INSERT INTO trades (symbol,timeframe,condition,stated_prob,
                   entry_ts,entry_price,size,entry_fee,lot_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (symbol, timeframe, condition, stated_prob, _now(),
                 entry_price, size, entry_fee, lot_id),
            )
            self.conn.commit()
        return cur.lastrowid

    def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        exit_reason: str,
        exit_fee: float,
        pnl: float,
        pnl_pct: float,
        hit: int,
    ) -> None:
        with sqlite_write_lock(self.path):
            self.conn.execute(
                """UPDATE trades SET exit_ts=?, exit_price=?, exit_reason=?,
                   exit_fee=?, pnl=?, pnl_pct=?, hit=?
                   WHERE id=?""",
                (_now(), exit_price, exit_reason, exit_fee, pnl, pnl_pct, hit, trade_id),
            )
            self.conn.commit()

    def snapshot_equity(self, equity: float, baseline: float | None, note: str = "") -> None:
        with sqlite_write_lock(self.path):
            self.conn.execute(
                "INSERT INTO equity_snapshots (ts,equity,baseline,note) VALUES (?,?,?,?)",
                (_now(), equity, baseline, note),
            )
            self.conn.commit()

    # -- reads ------------------------------------------------------------
    def decisions(self, limit: int = 500) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def trades(self, closed_only: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM trades"
        if closed_only:
            q += " WHERE exit_ts IS NOT NULL"
        return self.conn.execute(q + " ORDER BY id").fetchall()

    def all_trades(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM trades ORDER BY id").fetchall()

    def equity_history(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM equity_snapshots ORDER BY ts"
        ).fetchall()

    def open_trade_ids(self) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT id, symbol, entry_price, size, entry_fee, lot_id FROM trades WHERE exit_ts IS NULL"
            ).fetchall()
        ]

    def stats(self) -> dict:
        row = self.conn.execute(
            """SELECT COUNT(*) AS closed,
                      SUM(CASE WHEN hit=1 THEN 1 ELSE 0 END) AS hits,
                      SUM(pnl) AS total_pnl
               FROM trades WHERE exit_ts IS NOT NULL"""
        ).fetchone()
        return dict(row)

    # -- broker/account state persistence ------------------------------------
    def save_account_state(self, state: dict) -> None:
        """Persist broker + risk state so the account survives across runs."""
        with sqlite_write_lock(self.path):
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS account_state (
                       k TEXT PRIMARY KEY, v TEXT
                   )"""
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO account_state (k, v) VALUES ('broker', ?)",
                (json.dumps(state),),
            )
            self.conn.commit()

    def load_account_state(self) -> dict | None:
        try:
            row = self.conn.execute(
                "SELECT v FROM account_state WHERE k='broker'"
            ).fetchone()
        except sqlite3.OperationalError:
            return None  # table not created yet
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except (OSError, ValueError):
            return None
