"""5m gap checks against documented Binance maintenance windows.

Binance does not emit a kline while spot trading is suspended
(binance/binance-public-data#169, staff reply 2022-08-02). The windows
below are the maintenance list posted on that issue, treated as UTC
(Binance maintenance announcements and the kline dataset are UTC).
A missing 5m bar outside these windows is an unexpected gap and is counted.
Later outages are not guessed: they show up as unexpected gaps until the
list is extended.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hedge_fund.trading.constants import TF_MS_5M as TF_MS

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
GAP_REPORT_LIMIT = 20
_SOURCE = "binance-public-data#169"

# (start, end) of a trading suspension. end is exclusive.
_RAW_OUTAGES: tuple[tuple[str, str], ...] = (
    ("2017-09-06 15:45:00", "2017-09-06 23:00:00"),
    ("2017-12-18 12:15:00", "2017-12-18 13:30:00"),
    ("2018-01-04 03:00:00", "2018-01-04 05:00:00"),
    ("2018-02-08 00:15:00", "2018-02-09 09:58:14.800"),
    ("2018-02-10 05:58:14.800", "2018-02-10 06:15:00"),
    ("2018-02-11 04:00:00", "2018-02-11 04:30:00"),
    ("2018-06-26 01:45:00", "2018-06-26 12:00:00"),
    ("2018-06-27 12:45:00", "2018-06-27 14:45:00"),
    ("2018-07-04 00:15:00", "2018-07-04 08:00:00"),
    ("2018-10-19 05:45:00", "2018-10-19 09:30:00"),
    ("2018-11-14 01:45:00", "2018-11-14 09:00:00"),
    ("2019-03-12 01:45:00", "2019-03-12 08:00:00"),
    ("2019-05-15 02:45:00", "2019-05-15 13:00:00"),
    ("2019-06-07 21:00:00", "2019-06-07 22:15:00"),
    ("2019-08-15 01:45:00", "2019-08-15 10:00:00"),
    ("2019-11-13 01:45:00", "2019-11-13 04:15:00"),
    ("2019-11-25 01:45:00", "2019-11-25 04:00:00"),
    ("2020-02-09 01:45:00", "2020-02-09 03:00:00"),
    ("2020-02-19 11:30:00", "2020-02-19 17:30:00"),
    ("2020-03-04 09:15:00", "2020-03-04 11:30:00"),
    ("2020-04-25 01:45:00", "2020-04-25 04:30:00"),
    ("2020-06-28 01:45:00", "2020-06-28 05:30:00"),
    ("2020-11-30 05:45:00", "2020-11-30 07:00:00"),
    ("2020-12-21 14:00:00", "2020-12-21 18:00:00"),
    ("2020-12-25 01:45:00", "2020-12-25 03:00:00"),
    ("2021-02-11 03:30:00", "2021-02-11 05:00:00"),
    ("2021-03-06 01:45:00", "2021-03-06 03:30:00"),
    ("2021-04-20 01:45:00", "2021-04-20 04:30:00"),
    ("2021-04-25 04:00:00", "2021-04-25 08:45:00"),
    ("2021-08-13 01:45:00", "2021-08-13 06:30:00"),
    ("2021-09-29 06:45:00", "2021-09-29 09:00:00"),
)


def ms_to_dt(ms: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=int(ms))


def dt_to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt.astimezone(timezone.utc) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def iso_ms(ts: int) -> str:
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def _parse_utc(text: str) -> int:
    return dt_to_ms(datetime.fromisoformat(text).replace(tzinfo=timezone.utc))


def _window_label(start_ms: int, end_ms: int) -> str:
    return f"binance maintenance {iso_ms(start_ms)} to {iso_ms(end_ms)} ({_SOURCE})"


KNOWN_BINANCE_OUTAGES: tuple[tuple[int, int, str], ...] = tuple(
    (start, end, _window_label(start, end))
    for start, end in ((_parse_utc(a), _parse_utc(b)) for a, b in _RAW_OUTAGES)
)


def last_closed_open_ms(now_ms: int, tf_ms: int = TF_MS) -> int:
    """Open time of the newest 5m candle that has already closed.

    The candle that contains ``now_ms`` is still forming. A timestamp that
    lands exactly on a boundary is the open of the new forming candle, so
    the previous one is the last closed bar.
    """
    now_ms = int(now_ms)
    tf_ms = int(tf_ms)
    if tf_ms <= 0:
        raise ValueError("tf_ms must be positive")
    bucket = now_ms - (now_ms % tf_ms)
    return bucket - tf_ms


def missing_bars(delta_ms: int, tf_ms: int = TF_MS) -> int:
    if delta_ms <= tf_ms:
        return 0
    return max(1, int(delta_ms // tf_ms) - 1)


def _overlaps_outage(open_ms: int, start_ms: int, end_ms: int, tf_ms: int = TF_MS) -> bool:
    """True when the bucket ``[open, open+tf)`` overlaps ``[start, end)``."""
    return open_ms < end_ms and open_ms + tf_ms > start_ms


def known_outage_reason(prev_open_ms: int, next_open_ms: int) -> str | None:
    """Reason string when every missing open between the two bars is a known suspension.

    A gap wider than the maintenance window stays unexpected so a real hole
    is not hidden behind a short outage.
    """
    first = int(prev_open_ms) + TF_MS
    last = int(next_open_ms) - TF_MS
    if last < first:
        last = first
    for start, end, reason in KNOWN_BINANCE_OUTAGES:
        if _overlaps_outage(first, start, end) and _overlaps_outage(last, start, end):
            return reason
    return None


def empty_gap_summary() -> dict:
    return {
        "gap_count": 0,
        "known_gap_count": 0,
        "duplicates": 0,
        "monotonic": True,
        "gaps": [],
    }


def summarize_breaks(pairs: list[tuple[int, int]]) -> dict:
    """Classify timestamp steps that are not exactly one 5m bar.

    ``pairs`` are ``(prev_open_ms, next_open_ms)``. Equal timestamps are
    duplicates. Decreasing timestamps are non-monotonic. A step larger than
    5m is a gap, known only when a documented outage covers the whole hole.
    """
    gaps: list[dict] = []
    duplicates = 0
    monotonic = True
    unexpected = 0
    known = 0
    for prev, nxt in pairs:
        delta = int(nxt) - int(prev)
        if delta <= 0:
            monotonic = False
            if delta == 0:
                duplicates += 1
            continue
        if delta == TF_MS:
            continue
        reason = known_outage_reason(int(prev), int(nxt))
        if reason is None:
            unexpected += 1
        else:
            known += 1
        if len(gaps) < GAP_REPORT_LIMIT:
            gaps.append(
                {
                    "after": iso_ms(int(prev)),
                    "before": iso_ms(int(nxt)),
                    "missing_bars": missing_bars(delta),
                    "known": reason is not None,
                    "reason": reason,
                }
            )
    return {
        "gap_count": unexpected,
        "known_gap_count": known,
        "duplicates": duplicates,
        "monotonic": monotonic,
        "gaps": gaps,
    }


def summarize_timestamp_array(ts) -> dict:
    """Gap summary for a sorted open-time array. Only irregular steps are walked."""
    import numpy as np

    values = np.asarray(ts, dtype=np.int64)
    if int(values.shape[0]) < 2:
        return empty_gap_summary()
    deltas = np.diff(values)
    bad = np.flatnonzero(deltas != TF_MS)
    pairs = [(int(values[i]), int(values[i + 1])) for i in bad]
    return summarize_breaks(pairs)
