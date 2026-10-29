"""BTC daily bars for library discovery, written by the cycle and sent by the web.

The canonical 5m tape is cut at 2021-01-26 so its hash stays put. Library
names need completed UTC days from 2017-08-17 (about 3.4k rows). The cycle
sidecar resamples that series into ``library_daily_BTC_USDT.npz`` when the
completed day changes. The web process only stats that file and sends the
bytes. It does not resample the 5m tape, and a request does not rebuild a
missing file.

Workers download it with the ingest token. ``ETag`` is the file mtime and
size. ``If-None-Match`` or ``If-Modified-Since`` answers 304 without a body.
"""
from __future__ import annotations

import json
import os
from email.utils import formatdate, parsedate_to_datetime
from io import BytesIO
from pathlib import Path

import numpy as np

from hedge_fund.library.daily import DailyBars
from hedge_fund.trading.constants import LIBRARY_TAPE_START_MS, LIBRARY_TAPE_START_SLACK_MS

DAILY_CACHE_NAME = "library_daily_BTC_USDT.npz"
LIBRARY_DAILY_RETRY_AFTER = 60

# (resolved path) -> (mtime_ns, size, bytes). A request never resamples.
_SERVED: dict[str, tuple[int, int, bytes]] = {}
_file_reads = 0


class LibraryDailyNotReady(RuntimeError):
    """No warmed daily file. The request must not build one."""

    def __init__(
        self,
        message: str = "library daily tape not ready",
        *,
        retry_after: int = LIBRARY_DAILY_RETRY_AFTER,
    ) -> None:
        self.retry_after = int(retry_after)
        super().__init__(message)


def file_read_count() -> int:
    """How many times the web path has read the npz bytes. Tests use this."""
    return _file_reads


def reset_file_reads() -> None:
    global _file_reads
    _file_reads = 0


def clear_served_daily() -> None:
    """Drop the in-memory copy. The file stays. A new mtime is a miss anyway."""
    _SERVED.clear()


def _meta_text(raw) -> str:
    value = np.asarray(raw)
    if value.shape == ():
        item = value.item()
    elif value.size:
        item = value.reshape(-1)[0]
    else:
        return ""
    if isinstance(item, bytes):
        return item.decode("utf-8")
    return str(item)


def _read_meta(packed) -> dict:
    if "meta" not in packed.files:
        return {}
    try:
        data = json.loads(_meta_text(packed["meta"]))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def parse_daily_npz(blob: bytes) -> tuple[DailyBars, dict] | None:
    """Daily bars stored by :func:`write_daily_npz`. No 5m resample."""
    if not blob:
        return None
    try:
        with np.load(BytesIO(blob), allow_pickle=False) as packed:
            ts = np.asarray(packed["ts"], dtype=np.int64).copy()
            open_ = np.asarray(packed["open"], dtype=np.float64).copy()
            high = np.asarray(packed["high"], dtype=np.float64).copy()
            low = np.asarray(packed["low"], dtype=np.float64).copy()
            close = np.asarray(packed["close"], dtype=np.float64).copy()
            meta = _read_meta(packed)
    except Exception:
        return None
    n = int(ts.shape[0])
    if open_.shape[0] != n or high.shape[0] != n or low.shape[0] != n or close.shape[0] != n:
        return None
    return DailyBars(ts=ts, open=open_, high=high, low=low, close=close), meta


def parse_daily_file(path: Path) -> tuple[DailyBars, dict] | None:
    try:
        blob = Path(path).read_bytes()
    except OSError:
        return None
    return parse_daily_npz(blob)


def write_daily_npz(path: Path, bars: DailyBars, meta: dict) -> None:
    """Atomic replace. A reader sees the previous file or the new one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.npz")
    np.savez(
        tmp,
        ts=np.asarray(bars.ts, dtype=np.int64),
        open=np.asarray(bars.open, dtype=np.float64),
        high=np.asarray(bars.high, dtype=np.float64),
        low=np.asarray(bars.low, dtype=np.float64),
        close=np.asarray(bars.close, dtype=np.float64),
        meta=np.asarray(json.dumps(meta)),
    )
    os.replace(tmp, path)
    try:
        _SERVED.pop(str(path.resolve()), None)
    except OSError:
        pass


def daily_is_warm(bars: DailyBars, meta: dict | None = None) -> bool:
    """True when the first day reaches the Binance BTCUSDT listing."""
    if len(bars) == 0:
        return False
    if int(bars.ts[0]) > int(LIBRARY_TAPE_START_MS) + int(LIBRARY_TAPE_START_SLACK_MS):
        return False
    if isinstance(meta, dict) and meta.get("tape_start") is not None:
        try:
            if int(meta["tape_start"]) != int(LIBRARY_TAPE_START_MS):
                return False
        except (TypeError, ValueError):
            return False
    return True


def cache_path(state: Path) -> Path:
    return Path(state) / DAILY_CACHE_NAME


def served_library_daily(state: Path) -> tuple[bytes, os.stat_result]:
    """Npz bytes and the stat used for validators. Does not resample.

    A repeated request with the same mtime and size does not read the file
    again. A short tape, or a missing file, raises :class:`LibraryDailyNotReady`.
    """
    global _file_reads
    path = cache_path(state)
    try:
        key = str(path.resolve())
        st = path.stat()
    except OSError as exc:
        raise LibraryDailyNotReady() from exc
    if st.st_size <= 0:
        raise LibraryDailyNotReady()
    hit = _SERVED.get(key)
    if hit is not None and hit[0] == int(st.st_mtime_ns) and hit[1] == int(st.st_size):
        return hit[2], st
    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise LibraryDailyNotReady() from exc
    _file_reads += 1
    parsed = parse_daily_npz(blob)
    if parsed is None:
        raise LibraryDailyNotReady("library daily tape is unreadable")
    bars, meta = parsed
    if not daily_is_warm(bars, meta):
        raise LibraryDailyNotReady("library daily tape does not start at 2017-08-17")
    _SERVED[key] = (int(st.st_mtime_ns), int(st.st_size), blob)
    return blob, st


def publish_library_daily(state: Path | None = None) -> dict:
    """Refresh the daily file from the BTC tape. Cycle sidecar only.

    :func:`live_daily_bars` resamples when the completed day moved or the
    file does not start in 2017. This process is the cycle, not the web
    request. A short tape is reported and is not what the web will send.
    """
    from hedge_fund.library.live import live_daily_bars
    from hedge_fund.paths import state_root

    root = state_root() if state is None else Path(state)
    bars = live_daily_bars(root)
    clear_served_daily()
    if not daily_is_warm(bars):
        return {
            "ok": False,
            "paper_only": True,
            "reason": "not_warm",
            "bars": int(len(bars)),
        }
    path = cache_path(root)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {
        "ok": True,
        "paper_only": True,
        "bars": int(len(bars)),
        "first_ts": int(bars.ts[0]),
        "last_ts": None if bars.last_ts is None else int(bars.last_ts),
        "bytes": int(size),
    }


def etag_for(st: os.stat_result) -> str:
    """Strong validator from mtime and size. No read of the 5m tape."""
    return f'"{int(st.st_mtime_ns):x}-{int(st.st_size):x}"'


def last_modified_http(mtime: float) -> str:
    return formatdate(float(mtime), usegmt=True)


def _etag_tokens(header: str) -> list[str]:
    text = header.strip()
    if text == "*":
        return ["*"]
    out: list[str] = []
    for part in text.split(","):
        token = part.strip()
        if token.upper().startswith("W/"):
            token = token[2:].strip()
        if token:
            out.append(token)
    return out


def etag_matches(header: str, etag: str) -> bool:
    tokens = _etag_tokens(header)
    return "*" in tokens or etag in tokens


def not_modified(headers, *, etag: str, mtime: float) -> bool:
    """RFC 9110: ``If-None-Match`` wins when both validators are present."""
    if headers is None:
        return False
    inm = headers.get("If-None-Match")
    if inm:
        return etag_matches(str(inm), etag)
    ims = headers.get("If-Modified-Since")
    if not ims:
        return False
    try:
        parsed = parsedate_to_datetime(str(ims).strip())
    except (TypeError, ValueError, OverflowError):
        return False
    if parsed is None:
        return False
    return int(mtime) <= int(parsed.timestamp())
