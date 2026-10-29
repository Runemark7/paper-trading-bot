"""Per-name discovery eval is cheaper without changing OOS numbers."""
from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hedge_fund.trading.store import paper_state_lock

from hedge_fund.signals.dynamic import (
    _ema_series,
    _sma_series,
    clear_ema_cache,
    clear_sma_cache,
    ema,
    sma,
)
from hedge_fund.backtest.strategies import _atr_series, atr, clear_atr_cache
from hedge_fund.signals.wavetrend import (
    _cached_wavetrend_series,
    clear_wavetrend_cache,
    wavetrend_at,
    wavetrend_series,
    wt_cross_up_os,
)
from hedge_fund.trading.discovery import append_discovery_evaluation, load_discovery_log
from scripts.tournament_engine import _load_qual_history, _window_slices


def _naive_ema(closes: list[float], period: int, i: int) -> float:
    if i < period - 1 or period <= 0:
        return float("nan")
    k = 2.0 / (period + 1)
    val = sum(closes[:period]) / period
    for idx in range(period, i + 1):
        val = closes[idx] * k + val * (1.0 - k)
    return val


def _ohlcv(n: int, start: float = 100.0, drift: float = 0.0002) -> list[list]:
    rows = []
    px = start
    ts = 1_700_000_000_000
    for i in range(n):
        px *= 1.0 + drift
        rows.append([ts + i * 300_000, px, px * 1.01, px * 0.99, px])
    return rows


class EmaSeriesCacheTests(unittest.TestCase):
    def tearDown(self):
        clear_ema_cache()

    def test_cached_ema_matches_naive_recompute(self):
        closes = [100.0 + i * 0.05 for i in range(400)]
        clear_ema_cache()
        series = _ema_series(closes, 21)
        for i in (20, 21, 50, 200, 399):
            naive = _naive_ema(closes, 21, i)
            self.assertEqual(series[i], naive)
            self.assertEqual(ema(closes, 21, i), naive)


class SmaSeriesCacheTests(unittest.TestCase):
    def tearDown(self):
        clear_sma_cache()

    def test_cached_sma_matches_window_sum(self):
        closes = [100.0 + i * 0.05 for i in range(400)]
        clear_sma_cache()
        series = _sma_series(closes, 25)
        for i in (24, 25, 50, 200, 399):
            naive = sum(closes[i - 24 : i + 1]) / 25
            self.assertEqual(series[i], naive)
            self.assertEqual(sma(closes, 25, i), naive)


class AtrSeriesCacheTests(unittest.TestCase):
    def tearDown(self):
        clear_atr_cache()

    def test_cached_atr_matches_per_bar_window(self):
        closes = [100.0 + i * 0.1 for i in range(80)]
        highs = [c + 0.4 for c in closes]
        lows = [c - 0.3 for c in closes]
        period = 14
        clear_atr_cache()
        series = _atr_series(highs, lows, closes, period)
        for i in (13, 14, 20, 40, 79):
            if i < period:
                self.assertTrue(math.isnan(series[i]))
                self.assertTrue(math.isnan(atr(highs, lows, closes, period, i)))
                continue
            trs = []
            for j in range(i - period + 1, i + 1):
                h, l, pc = highs[j], lows[j], closes[j - 1]
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
            naive = sum(trs) / len(trs)
            self.assertEqual(series[i], naive)
            self.assertEqual(atr(highs, lows, closes, period, i), naive)


class WaveTrendSeriesCacheTests(unittest.TestCase):
    def tearDown(self):
        clear_wavetrend_cache()

    def test_full_series_at_i_matches_prefix_rebuild(self):
        closes = [100.0]
        for j in range(1, 120):
            closes.append(closes[-1] * (1.0 + 0.002 * ((-1) ** j)))
        highs = [c * 1.002 for c in closes]
        lows = [c * 0.998 for c in closes]
        clear_wavetrend_cache()
        cached = _cached_wavetrend_series(highs, lows, closes)
        again = _cached_wavetrend_series(highs, lows, closes)
        self.assertIs(cached[0], again[0])
        for i in (60, 90, 119):
            wt1_p, wt2_p = wavetrend_series(highs[: i + 1], lows[: i + 1], closes[: i + 1])
            if math.isnan(cached[0][i]):
                self.assertTrue(math.isnan(wt1_p[i]))
            else:
                self.assertEqual(cached[0][i], wt1_p[i])
            if math.isnan(cached[1][i]):
                self.assertTrue(math.isnan(wt2_p[i]))
            else:
                self.assertEqual(cached[1][i], wt2_p[i])
            a, b = wavetrend_at(highs, lows, closes, i)
            if math.isnan(a):
                self.assertTrue(math.isnan(wt1_p[i]))
            else:
                self.assertEqual(a, wt1_p[i])
            if math.isnan(b):
                self.assertTrue(math.isnan(wt2_p[i]))
            else:
                self.assertEqual(b, wt2_p[i])
            self.assertEqual(
                wt_cross_up_os(closes, highs, lows, i),
                wt_cross_up_os(closes[: i + 1], highs[: i + 1], lows[: i + 1], i),
            )


class HistoryTrimTests(unittest.TestCase):
    def test_load_keeps_only_qual_symbols_and_last_bars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "BTC/USDT": _ohlcv(40, start=100.0),
                "ETH/USDT": _ohlcv(40, start=10.0),
                "SOL/USDT": _ohlcv(40, start=50.0),
            }
            (root / "crypto_history_5m.json").write_text(json.dumps(payload))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                data = _load_qual_history(keep_bars=10)
        self.assertEqual(set(data), {"BTC/USDT", "ETH/USDT"})
        self.assertEqual(len(data["BTC/USDT"]), 10)
        self.assertEqual(data["BTC/USDT"][0], payload["BTC/USDT"][-10])
        self.assertEqual(data["BTC/USDT"][-1], payload["BTC/USDT"][-1])

    def test_trimmed_history_same_window_ohlc_as_full(self):
        full = {
            "BTC/USDT": _ohlcv(90, start=100.0),
            "ETH/USDT": _ohlcv(90, start=10.0),
        }
        keep = 30
        trimmed = {s: rows[-keep:] for s, rows in full.items()}
        full_w = _window_slices(full, window_size=10, n_windows=3, stride=1, warmup_bars=0)
        trim_w = _window_slices(trimmed, window_size=10, n_windows=3, stride=1, warmup_bars=0)
        self.assertEqual(len(full_w), 3)
        for a, b in zip(full_w, trim_w):
            self.assertEqual(set(a), set(b))
            for sym in a:
                c1, h1, l1, warm1, cut1 = a[sym]
                c2, h2, l2, warm2, cut2 = b[sym]
                self.assertEqual(warm1, 0)
                self.assertEqual(warm2, 0)
                self.assertEqual(cut1, cut2)
                self.assertEqual(c1, c2)
                self.assertEqual(h1, h2)
                self.assertEqual(l1, l2)

    def test_default_keep_includes_warmup_prefix(self):
        from hedge_fund.trading.constants import QUAL_N_WINDOWS, QUAL_WARMUP_BARS

        window_bars = 10
        warmup = 7
        keep = window_bars * QUAL_N_WINDOWS + warmup
        extra = 25
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "BTC/USDT": _ohlcv(keep + extra, start=100.0),
                "ETH/USDT": _ohlcv(keep + extra, start=10.0),
                "SOL/USDT": _ohlcv(keep + extra, start=50.0),
            }
            (root / "crypto_history_5m.json").write_text(json.dumps(payload))
            with (
                patch.dict(os.environ, {"PAPER_STATE": str(root)}),
                patch("scripts.tournament_engine.QUAL_WINDOW_BARS", window_bars),
                patch("scripts.tournament_engine.QUAL_N_WINDOWS", QUAL_N_WINDOWS),
                patch("scripts.tournament_engine.QUAL_WARMUP_BARS", warmup),
            ):
                data = _load_qual_history()
        self.assertEqual(set(data), {"BTC/USDT", "ETH/USDT"})
        self.assertEqual(len(data["BTC/USDT"]), keep)
        self.assertEqual(data["BTC/USDT"][0], payload["BTC/USDT"][-keep])
        self.assertEqual(data["BTC/USDT"][-1], payload["BTC/USDT"][-1])
        self.assertEqual(QUAL_WARMUP_BARS, 14 * 24 * 12)

    def test_qual_windows_are_chronological_full_size_holdouts(self):
        from hedge_fund.trading.constants import QUAL_N_WINDOWS

        window_size = 10
        n_windows = QUAL_N_WINDOWS
        data = {
            "BTC/USDT": _ohlcv(window_size * n_windows, start=100.0),
            "ETH/USDT": _ohlcv(window_size * n_windows, start=10.0),
        }
        slices = _window_slices(
            data, window_size=window_size, n_windows=n_windows, stride=1, warmup_bars=0,
        )
        self.assertEqual(len(slices), n_windows)
        prev_last = None
        for i, sl in enumerate(slices):
            closes, _h, _l, warmup, cut = sl["BTC/USDT"]
            self.assertEqual(warmup, 0)
            self.assertEqual(len(closes), window_size)
            self.assertEqual(cut, int(window_size * 0.70))
            if prev_last is not None:
                self.assertGreater(closes[0], prev_last)
            prev_last = closes[-1]
        # Last slice is the most recent tape; first slice is the oldest hold-out.
        self.assertEqual(slices[-1]["BTC/USDT"][0][-1], data["BTC/USDT"][-1][4])
        self.assertEqual(slices[0]["BTC/USDT"][0][0], data["BTC/USDT"][0][4])

    def test_warmup_prefix_from_prior_bars_first_window_partial(self):
        window_size = 10
        n_windows = 3
        pad = 8
        extra = 5
        data = {
            "BTC/USDT": _ohlcv(window_size * n_windows + extra, start=100.0),
            "ETH/USDT": _ohlcv(window_size * n_windows + extra, start=10.0),
        }
        slices = _window_slices(
            data, window_size=window_size, n_windows=n_windows, stride=1, warmup_bars=pad,
        )
        self.assertEqual(len(slices), 3)
        w0 = slices[0]["BTC/USDT"]
        closes0, _h, _l, warm0, cut0 = w0
        self.assertEqual(warm0, extra)  # only 5 bars exist before window 0
        self.assertEqual(len(closes0) - warm0, window_size)
        self.assertEqual(cut0, warm0 + int(window_size * 0.70))
        self.assertEqual(closes0[warm0], data["BTC/USDT"][extra][4])
        w1 = slices[1]["BTC/USDT"]
        closes1, _h1, _l1, warm1, cut1 = w1
        self.assertEqual(warm1, pad)
        self.assertEqual(len(closes1) - warm1, window_size)
        # Warm-up is earlier tape, not future bars.
        self.assertLess(closes1[0], closes1[warm1])
        scored_start1 = extra + window_size
        self.assertEqual(closes1[0], data["BTC/USDT"][scored_start1 - pad][4])
        self.assertEqual(closes1[warm1], data["BTC/USDT"][scored_start1][4])
        self.assertEqual(closes1[-1], data["BTC/USDT"][scored_start1 + window_size - 1][4])


class AppendOnlyStateTests(unittest.TestCase):
    def test_ingest_does_not_rewrite_base_files_and_ignores_a_partial_line(self):
        from hedge_fund.trading.constants import GATE_RULES
        from hedge_fund.trading.discovery import discovery_log_delta_path
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.tested_index import (
            load_tested_index,
            tested_journal_path,
        )

        row = {
            "qualified": False,
            "sharpe": 0.2,
            "trades": 10,
            "test_pnl": 1.0,
            "train_pnl": 1.0,
            "win_rate_pct": 40.0,
            "fail_reasons": ["oos_sharpe 0.20 < 0.30"],
            "timeframe": "5m",
            "risk_policy": "rm_v1",
            "gate_rules": GATE_RULES,
            "tested_at": "2026-10-20T00:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_log = json.dumps([dict(row, strategy="old")], separators=(",", ":"))
            base_index = json.dumps({
                "v": 1,
                "paper_only": True,
                "names": {"old": 0},
                "metrics": {"old": {"sharpe": 0.2, "trades": 10}},
            }, separators=(",", ":"))
            (root / "discovery_log.json").write_text(base_log)
            (root / "discovery_tested.json").write_text(base_index)
            (root / "discovery_farm.json").write_text(json.dumps({"enabled": True}))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                ev = dict(row, strategy="fresh_name", tested_at="2026-10-28T05:00:00+00:00")
                body = ingest_discovery_payload({
                    "evaluations": [ev],
                    "heartbeat": {"status": "running", "worker_id": "w"},
                })
                self.assertEqual(body["ingested"], ["fresh_name"])
                self.assertEqual((root / "discovery_log.json").read_text(), base_log)
                self.assertEqual((root / "discovery_tested.json").read_text(), base_index)
                self.assertIn("fresh_name", discovery_log_delta_path().read_text())
                self.assertIn("fresh_name", tested_journal_path().read_text())
                self.assertFalse(load_tested_index()["fresh_name"])
                again = ingest_discovery_payload({"evaluations": [ev]})
                self.assertEqual(again["ingested"], [])
                self.assertEqual(again["skipped"][0]["reason"], "already_tested")
                # Crash mid-append: a partial last line must not hide the good one.
                with tested_journal_path().open("a") as fh:
                    fh.write('{"name":"broken"')
                self.assertNotIn("broken", load_tested_index())
                self.assertIn("fresh_name", load_tested_index())


class CompactDiscoveryLogTests(unittest.TestCase):
    def test_append_writes_compact_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                append_discovery_evaluation({
                    "strategy": "sma_stack",
                    "qualified": False,
                    "tested_at": "2026-09-10T00:00:00+00:00",
                })
                from hedge_fund.trading.discovery import discovery_log_delta_path

                raw = discovery_log_delta_path().read_text()
                self.assertNotIn("\n  ", raw)
                self.assertFalse((root / "discovery_log.json").exists())
                log = load_discovery_log()
        self.assertEqual(log[0]["strategy"], "sma_stack")
        self.assertFalse(log[0]["qualified"])

    def test_large_log_appends_a_line_and_stays_readable(self):
        import time

        from hedge_fund.trading.discovery import (
            DELTA_COMPACT_LINES,
            discovery_log_delta_path,
        )

        seed = {
            "strategy": "seed",
            "qualified": False,
            "sharpe": 0.1,
            "trades": 4,
            "fail_reasons": ["x" * 40],
        }
        rows = [{**seed, "strategy": f"s{i:04d}"} for i in range(5000)]
        blob = json.dumps(rows, separators=(",", ":"))
        self.assertGreater(len(blob), 256 * 1024)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(blob)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                before = (root / "discovery_log.json").read_bytes()
                load_discovery_log()
                started = time.perf_counter()
                append_discovery_evaluation({
                    **seed,
                    "strategy": "new_name",
                    "tested_at": "2026-10-28T00:00:00+00:00",
                })
                delta_s = time.perf_counter() - started
                self.assertEqual((root / "discovery_log.json").read_bytes(), before)
                delta = discovery_log_delta_path().read_text()
                self.assertIn("new_name", delta)
                self.assertEqual(load_discovery_log()[0]["strategy"], "new_name")
                rewrite_started = time.perf_counter()
                loaded = json.loads(before)
                (root / "full_rewrite.json").write_text(
                    json.dumps([{"strategy": "z"}] + loaded, separators=(",", ":"))
                )
                rewrite_s = time.perf_counter() - rewrite_started
        print(
            f"TIMING log_delta_append={delta_s * 1000:.1f}ms "
            f"full_rewrite={rewrite_s * 1000:.1f}ms bytes={len(blob)} "
            f"compact_every={DELTA_COMPACT_LINES}"
        )
        # The append must stay cheaper than rewriting the base array.
        self.assertLess(delta_s, rewrite_s)

    def test_census_is_reused_until_the_index_changes(self):
        from hedge_fund.trading.tested_index import save_tested_index
        from hedge_fund.trading.trials import trial_census

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                save_tested_index(
                    {"a": False, "b": False},
                    {"a": {"sharpe": 0.2, "trades": 10}, "b": {"sharpe": 0.4, "trades": 12}},
                )
                with patch(
                    "hedge_fund.trading.trials.load_tested_metrics",
                    wraps=__import__(
                        "hedge_fund.trading.tested_index",
                        fromlist=["load_tested_metrics"],
                    ).load_tested_metrics,
                ) as load:
                    first = trial_census()
                    second = trial_census()
        self.assertEqual(first["n_backtested"], 2)
        self.assertEqual(second, first)
        self.assertEqual(load.call_count, 1)

    def test_later_heartbeat_skips_the_display_log(self):
        from hedge_fund.trading.ingest import ingest_discovery_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                first = ingest_discovery_payload({
                    "evaluations": [{
                        "strategy": "sma_stack",
                        "qualified": False,
                        "sharpe": 0.1,
                        "trades": 4,
                        "test_pnl": -1.0,
                        "train_pnl": 0.0,
                        "win_rate_pct": 0.0,
                        "fail_reasons": ["oos_sharpe 0.10 < 0.30"],
                        "timeframe": "5m",
                        "risk_policy": "rm_v1",
                        "gate_rules": "gate_v7b_library_profit_20261029",
                        "tested_at": "2026-10-28T00:00:00+00:00",
                    }],
                })
                with patch(
                    "hedge_fund.trading.ingest.load_discovery_log",
                    side_effect=AssertionError("log"),
                ):
                    second = ingest_discovery_payload({
                        "evaluations": [],
                        "heartbeat": {"status": "running", "worker_id": "zealous-lion"},
                    })
        self.assertEqual(first["ingested"], ["sma_stack"])
        self.assertIn("timing_ms", first)
        self.assertEqual(second["ingested"], [])
        self.assertIn("heartbeat", second["timing_ms"])
        self.assertNotIn("park_scan", second["timing_ms"])


class ClaimIngestHotPathTests(unittest.TestCase):
    def test_retest_claim_and_ingest_stay_under_a_quarter_second(self):
        """Prod-shaped files: a warm claim/ingest must not rescan the world."""
        import time

        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch
        from hedge_fund.trading.requeue_v4_bh import BATCH_ID, STATE_NAME

        row = {
            "strategy": "s",
            "qualified": False,
            "sharpe": 0.22,
            "trades": 40,
            "test_pnl": 3.0,
            "fail_reasons": ["daily_sharpe 0.22 <= bh_daily_sharpe 0.40"],
            "timeframe": "5m",
            "risk_policy": "rm_v1",
            "gate_rules": "sltp_cap100_bhdsr_tiled87_20261008",
            "tested_at": "2026-10-20T00:00:00+00:00",
            "daily_sharpe": 0.22,
            "bh_daily_sharpe": 0.4,
            "regimes_tested": 18,
            "symbols": ["BTC/USDT"],
            "sma_stack_oos_pnl": 1.0,
        }
        rows = [{**row, "strategy": f"n{i:05d}"} for i in range(4000)]
        metrics = {f"n{i:05d}": {"sharpe": 0.2, "trades": 30} for i in range(8000)}
        order = [f"n{i:05d}" for i in range(200)]
        fresh = {
            "strategy": "fresh_0",
            "qualified": False,
            "sharpe": 0.11,
            "trades": 8,
            "test_pnl": -1.0,
            "train_pnl": 0.0,
            "win_rate_pct": 40.0,
            "fail_reasons": ["oos_sharpe 0.11 < 0.30"],
            "timeframe": "5m",
            "risk_policy": "rm_v1",
            "gate_rules": "gate_v7b_library_profit_20261029",
            "tested_at": "2026-10-28T01:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(json.dumps(rows, separators=(",", ":")))
            (root / "discovery_tested.json").write_text(json.dumps({
                "v": 1,
                "paper_only": True,
                "names": {name: 0 for name in metrics},
                "metrics": metrics,
            }, separators=(",", ":")))
            (root / "champions.json").write_text(json.dumps({"champions": [], "synced_until": ""}))
            (root / STATE_NAME).write_text(json.dumps({
                "batch_id": BATCH_ID,
                "applied_at": "2026-10-28T00:00:00+00:00",
                "order": order,
                "entries": {
                    name: {
                        "status": "pending",
                        "prior_gate_rules": row["gate_rules"],
                        "prior_fail_reasons": row["fail_reasons"],
                    }
                    for name in order
                },
            }))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                ingest_discovery_payload({"evaluations": [fresh]})
                ingest_ms = []
                for i in range(3):
                    ev = {**fresh, "strategy": f"fresh_{i+1}", "tested_at": f"2026-10-28T01:00:0{i+1}+00:00"}
                    t0 = time.perf_counter()
                    ingest_discovery_payload({
                        "evaluations": [ev],
                        "heartbeat": {"status": "running", "worker_id": "zealous-lion"},
                    })
                    ingest_ms.append((time.perf_counter() - t0) * 1000)
                claim_ms = []
                last = None
                # 2*parallel is the lease cap, so two claims of 2 fill it.
                for _ in range(2):
                    t0 = time.perf_counter()
                    body = claim_discovery_batch("zealous-lion", 2, parallel=2)
                    claim_ms.append((time.perf_counter() - t0) * 1000)
                    self.assertEqual(len(body["names"]), 2)
                    last = body
                from hedge_fund.trading.handler_timing import handler_frame

                with handler_frame():
                    timed = ingest_discovery_payload({
                        "evaluations": [],
                        "heartbeat": {"status": "running", "worker_id": "zealous-lion"},
                    })
        self.assertIsNotNone(last)
        for key in ("lock_wait", "io", "work", "lock_hold"):
            self.assertIn(key, last["timing_ms"])
            self.assertIn(key, timed["timing_ms"])
        print(
            f"TIMING warm_ingest_ms={['%.1f' % v for v in ingest_ms]} "
            f"warm_claim_ms={['%.1f' % v for v in claim_ms]} "
            f"claim_steps={last['timing_ms']} ingest_steps={timed['timing_ms']}"
        )
        self.assertLess(sorted(ingest_ms)[1], 250)
        self.assertLess(sorted(claim_ms)[1], 250)

    def test_prod_sized_claim_does_not_rescan_after_ingest(self):
        """19.8k tested names: a later claim must not re-canonicalise the book."""
        import time

        from hedge_fund.trading.constants import GATE_RULES
        from hedge_fund.trading.handler_timing import handler_frame
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch

        n_tested, n_log, n_ext = 12000, 6000, 10000
        tested = [
            f"sma_abv_{20 + (i % 30) * 5}&mom_{(i % 15) * 6 + 12}b_gt2pc&n{i}"
            for i in range(n_tested)
        ]
        row = {
            "qualified": False,
            "sharpe": 0.2,
            "trades": 40,
            "test_pnl": 1.0,
            "train_pnl": 1.0,
            "win_rate_pct": 40.0,
            "fail_reasons": ["oos_sharpe 0.20 < 0.30"],
            "timeframe": "5m",
            "risk_policy": "rm_v1",
            "gate_rules": GATE_RULES,
            "tested_at": "2026-10-20T00:00:00+00:00",
        }
        rows = []
        for name in tested[:n_log]:
            item = dict(row)
            item["strategy"] = name
            rows.append(item)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(json.dumps(rows, separators=(",", ":")))
            (root / "discovery_tested.json").write_text(json.dumps({
                "v": 1,
                "paper_only": True,
                "names": {name: 0 for name in tested},
                "metrics": {name: {"sharpe": 0.2, "trades": 30} for name in tested},
            }, separators=(",", ":")))
            (root / "discovery_extended.json").write_text(json.dumps({
                "names": tested[:n_ext],
                "batches_emitted": 3,
            }, separators=(",", ":")))
            (root / "champions.json").write_text(json.dumps({
                "champions": [{"name": tested[0]}],
                "synced_until": "",
            }))
            (root / "discovery_farm.json").write_text(json.dumps({"enabled": True}))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                claim_discovery_batch("zealous-lion", 2, parallel=2, gate_rules=GATE_RULES, require_gate_rules=True)
                first = dict(row)
                first["strategy"] = "fresh_warm&sma_abv_50&mom_18b_gt2pc"
                first["tested_at"] = "2026-10-28T04:00:00+00:00"
                ingest_discovery_payload({
                    "evaluations": [first],
                    "heartbeat": {"status": "running", "worker_id": "zealous-lion"},
                })
                fresh = dict(row)
                fresh["strategy"] = "fresh_after&sma_abv_50&mom_24b_gt2pc"
                fresh["tested_at"] = "2026-10-28T04:01:00+00:00"
                with handler_frame():
                    t0 = time.perf_counter()
                    ing = ingest_discovery_payload({
                        "evaluations": [fresh],
                        "heartbeat": {"status": "running", "worker_id": "zealous-lion"},
                    })
                    ingest_s = time.perf_counter() - t0
                self.assertEqual(ing["ingested"], [fresh["strategy"]])
                again = ingest_discovery_payload({
                    "evaluations": [fresh],
                    "heartbeat": {"status": "running", "worker_id": "zealous-lion"},
                })
                self.assertEqual(again["ingested"], [])
                self.assertEqual(again["skipped"][0]["reason"], "already_tested")
                t0 = time.perf_counter()
                body = claim_discovery_batch(
                    "zealous-lion", 2, parallel=2, gate_rules=GATE_RULES, require_gate_rules=True,
                )
                claim_s = time.perf_counter() - t0
        timing = body["timing_ms"]
        self.assertLess(timing["lock_hold"], 100)
        self.assertLess(timing["work"], 200)
        self.assertNotIn(fresh["strategy"], body["names"])
        self.assertLess(ingest_s, 1.0)
        self.assertLess(claim_s, 0.2)
        ing_timing = ing["timing_ms"]
        self.assertLess(ing_timing["lock_hold"], 100)
        self.assertLess(ing_timing["work"], 200)


    def test_claim_caches_follow_writes_by_other_processes(self):
        """A write the cache did not make (densify, rescore in another
        container) followed by an ingest must not leave the cached queue or
        tested set stamped with the new fingerprint but the old contents."""
        from hedge_fund.trading import discovery as disc
        from hedge_fund.trading import leases
        from hedge_fund.trading.constants import GATE_RULES
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch

        row = {
            "qualified": False, "sharpe": 0.2, "trades": 40, "test_pnl": 1.0,
            "train_pnl": 1.0, "win_rate_pct": 40.0,
            "fail_reasons": ["oos_sharpe 0.20 < 0.30"], "timeframe": "5m",
            "risk_policy": "rm_v1", "gate_rules": GATE_RULES,
            "tested_at": "2026-10-20T00:00:00+00:00",
        }
        tested = [f"sma_abv_{20 + i * 5}&mom_{12 + i * 6}b_gt2pc" for i in range(6)]
        rows = [dict(row, strategy=n) for n in tested[:3]]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text(json.dumps(rows))
            (root / "discovery_tested.json").write_text(json.dumps({
                "v": 1, "paper_only": True, "names": {n: 0 for n in tested}, "metrics": {},
            }))
            (root / "discovery_extended.json").write_text(json.dumps({"names": [], "batches_emitted": 1}))
            (root / "discovery_farm.json").write_text(json.dumps({"enabled": True}))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                claim_discovery_batch("w", 1, parallel=1, gate_rules=GATE_RULES, require_gate_rules=True)
                disc.cached_tested_names()
                self.assertIsNotNone(leases._QUEUE["fp"])
                # Another container densifies: a raw write this process never saw.
                densified = "ema_x_9_21&rsi14_lt35&vol_gt1p5"
                (root / "discovery_extended.json").write_text(
                    json.dumps({"names": [densified], "batches_emitted": 2})
                )
                # Another container rewrites the log with a row this process never cached.
                other = dict(row, strategy="other_proc&sma_abv_70&mom_42b_gt2pc")
                log_now = json.loads((root / "discovery_log.json").read_text())
                (root / "discovery_log.json").write_text(json.dumps([other] + log_now))
                fresh = dict(row, strategy="fresh_after&sma_abv_50&mom_24b_gt2pc",
                             tested_at="2026-10-28T04:01:00+00:00")
                ing = ingest_discovery_payload({
                    "evaluations": [fresh],
                    "heartbeat": {"status": "running", "worker_id": "w"},
                })
                self.assertEqual(ing["ingested"], [fresh["strategy"]])
                warm_view = leases._queue_view({"leases": {}})
                warm_names = set(disc.cached_tested_names())
                for key in list(leases._QUEUE):
                    leases._QUEUE[key] = None
                disc._tested_names_cache["fp"] = None
                disc._tested_names_cache["names"] = None
                cold_view = leases._queue_view({"leases": {}})
                cold_names = set(disc.cached_tested_names())
        self.assertIn(other["strategy"], cold_names)
        self.assertEqual(warm_names, cold_names)
        self.assertEqual(warm_view["claimable"], cold_view["claimable"])
        self.assertEqual(warm_view["blocked"], cold_view["blocked"])
        self.assertEqual(warm_view["universe"], cold_view["universe"])
        self.assertIn(densified, cold_view["universe"])

    def test_near_duplicate_of_leased_name_matches_uncached_queue(self):
        """Cached queue with a lease matches a cold rebuild (permutations collapse upstream)."""
        from hedge_fund.trading import leases
        from hedge_fund.trading.mint_quality import canonical_name

        p1 = "ema_x_9_21&rsi14_lt35&vol_gt1p5"
        p2 = "rsi14_lt35&ema_x_9_21&vol_gt1p5"
        self.assertEqual(canonical_name(p1), canonical_name(p2))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "discovery_log.json").write_text("[]")
            (root / "discovery_extended.json").write_text(json.dumps({"names": [p1, p2], "batches_emitted": 1}))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}):
                for key in list(leases._QUEUE):
                    leases._QUEUE[key] = None
                free = leases._queue_view({"leases": {}})["claimable"]
                self.assertEqual([n for n in free if n in (p1, p2)].__len__(), 1)
                first = next(n for n in free if n in (p1, p2))
                other = p2 if first == p1 else p1
                warm = leases._queue_view({"leases": {first: {}}})["claimable"]
                for key in list(leases._QUEUE):
                    leases._QUEUE[key] = None
                cold = leases._queue_view({"leases": {first: {}}})["claimable"]
        self.assertNotIn(other, cold)  # same as origin/main: permutations collapse upstream
        self.assertEqual(warm, cold)

if __name__ == "__main__":
    unittest.main()


class IngestRaceTests(unittest.TestCase):
    """The fail-once check and the append run under separate locks. A second
    ingest of the same name that lands in between must not add a second row."""

    def _row(self, name: str, stamp: str) -> dict:
        return {
            "strategy": name,
            "qualified": False,
            "sharpe": 0.11,
            "trades": 8,
            "test_pnl": -1.0,
            "train_pnl": 0.0,
            "win_rate_pct": 40.0,
            "fail_reasons": ["oos_sharpe 0.11 < 0.30"],
            "timeframe": "5m",
            "risk_policy": "rm_v1",
            "gate_rules": "gate_v7b_library_profit_20261029",
            "tested_at": stamp,
        }

    def test_concurrent_duplicate_ingest_writes_one_row(self):
        import hedge_fund.trading.ingest as ingest_mod
        from hedge_fund.trading.ingest import ingest_discovery_payload

        original = ingest_mod.append_discovery_evaluation
        payload = {"evaluations": [self._row("race_a", "2026-10-28T02:00:00+00:00")]}
        state = {"raced": False, "inner": None}

        def racing_append(rec, **kwargs):
            # Before the outer ingest appends, a retried post of the same
            # name runs end to end (it passed the same stale check).
            if not state["raced"]:
                state["raced"] = True
                state["inner"] = ingest_discovery_payload(payload)
            return original(rec, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "champions.json").write_text(json.dumps({"champions": [], "synced_until": ""}))
            with patch.dict(os.environ, {"PAPER_STATE": str(root)}), patch.object(
                ingest_mod, "append_discovery_evaluation", racing_append
            ):
                outer = ingest_discovery_payload(payload)
                rows = [r for r in load_discovery_log() if r.get("strategy") == "race_a"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(state["inner"]["ingested"], ["race_a"])
        self.assertEqual(outer["ingested"], [])
        self.assertEqual(outer["skipped"], [{"strategy": "race_a", "reason": "already_tested"}])


def _journal_row(name: str, *, qualified: bool = False, sharpe: float = 0.1, at: str = "2026-10-28T05:00:00+00:00") -> dict:
    return {
        "strategy": name,
        "qualified": qualified,
        "sharpe": sharpe,
        "trades": 9,
        "tested_at": at,
    }


def _cold_caches() -> None:
    """Forget every in-process view so the next read comes from disk."""
    from hedge_fund.trading import discovery, tested_index

    tested_index._derived["key"] = None
    tested_index._doc_cache["key"] = None
    tested_index._doc_cache["data"] = None
    tested_index._covers = None
    discovery._log_cache["key"] = None
    discovery._log_cache["rows"] = None
    discovery._tested_names_cache["fp"] = None
    discovery._tested_names_cache["names"] = None


class AppendOnlyCrashAndRaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = patch.dict(os.environ, {"PAPER_STATE": str(self.root)})
        self._env.start()
        # Compaction is driven explicitly in these tests.
        self._sched = patch("hedge_fund.trading.discovery.schedule_state_compact", lambda: None)
        self._sched.start()
        _cold_caches()

    def tearDown(self):
        self._sched.stop()
        self._env.stop()
        _cold_caches()
        self._tmp.cleanup()

    def test_row_appended_after_a_partial_line_is_kept(self):
        from hedge_fund.trading.discovery import (
            append_discovery_evaluations,
            discovery_log_delta_path,
            load_discovery_log,
        )
        from hedge_fund.trading.tested_index import load_tested_index, tested_journal_path

        append_discovery_evaluations([_journal_row("first")])
        with discovery_log_delta_path().open("a") as fh:
            fh.write('{"strategy":"torn"')
        with tested_journal_path().open("a") as fh:
            fh.write('{"name":"torn"')
        append_discovery_evaluations([_journal_row("second", at="2026-10-28T05:01:00+00:00")])
        _cold_caches()
        names = [r["strategy"] for r in load_discovery_log()]
        self.assertEqual(names, ["second", "first"])
        index = load_tested_index()
        self.assertIn("second", index)
        self.assertIn("first", index)
        self.assertNotIn("torn", index)

    def test_bulk_index_edit_survives_crash_before_journal_drop(self):
        from hedge_fund.trading.discovery import append_discovery_evaluations
        from hedge_fund.trading.store import paper_state_lock
        from hedge_fund.trading.tested_index import (
            load_index_and_metrics,
            load_tested_index,
            save_tested_index,
            tested_journal_path,
        )

        append_discovery_evaluations([_journal_row("flip")])
        self.assertTrue(tested_journal_path().exists())
        real_unlink = Path.unlink

        def crash_unlink(self_path, *a, **k):
            if self_path.name == "discovery_tested.jsonl":
                raise OSError("simulated crash before the journal drop")
            return real_unlink(self_path, *a, **k)

        with paper_state_lock("discovery"):
            index, metrics = load_index_and_metrics()
            index["flip"] = True
            index["added"] = False  # different name set -> base replace
            with patch.object(Path, "unlink", crash_unlink):
                save_tested_index(index, metrics, cleaned=True)
        self.assertTrue(tested_journal_path().exists())
        _cold_caches()
        index = load_tested_index()
        self.assertTrue(index["flip"])
        self.assertIn("added", index)
        # The stale journal is replaced by the next append, not extended.
        append_discovery_evaluations([_journal_row("later", at="2026-10-28T06:00:00+00:00")])
        _cold_caches()
        index = load_tested_index()
        self.assertTrue(index["flip"])
        self.assertIn("later", index)

    def test_bulk_log_edit_survives_crash_before_delta_drop(self):
        from hedge_fund.trading.discovery import (
            append_discovery_evaluations,
            discovery_log_path,
            load_discovery_log,
        )

        append_discovery_evaluations([_journal_row("row")])
        edited = [dict(_journal_row("row"), qualified=True)]
        # save_discovery_log installed the edited base, then the process died.
        discovery_log_path().write_text(json.dumps(edited))
        _cold_caches()
        log = load_discovery_log()
        self.assertEqual(len(log), 1)
        self.assertTrue(log[0]["qualified"])

    def test_compaction_keeps_rows_appended_during_encode(self):
        import hedge_fund.trading.discovery as disc
        from hedge_fund.trading.tested_index import load_tested_index, tested_journal_path

        for i in range(6):
            disc.append_discovery_evaluations(
                [_journal_row(f"n{i}", at=f"2026-10-28T05:0{i}:00+00:00")]
            )
        real_json = disc.json
        fired = []

        class RacingJson:
            def __getattr__(self, attr):
                return getattr(real_json, attr)

            def dumps(self, obj, *a, **k):
                if isinstance(obj, list) and not fired:
                    fired.append(1)
                    disc.append_discovery_evaluations(
                        [_journal_row("raced", at="2026-10-28T05:30:00+00:00")]
                    )
                return real_json.dumps(obj, *a, **k)

        with patch.object(disc, "LOG_REWRITE_MAX_BYTES", 16), patch.object(disc, "json", RacingJson()):
            disc.compact_discovery_state()
        self.assertTrue(fired)
        _cold_caches()
        names = [r["strategy"] for r in disc.load_discovery_log()]
        self.assertIn("raced", names)
        self.assertIn("raced", load_tested_index())
        with patch.object(disc, "LOG_REWRITE_MAX_BYTES", 16):
            disc.compact_discovery_state()
        self.assertFalse(disc.discovery_log_delta_path().exists())
        self.assertFalse(tested_journal_path().exists())
        _cold_caches()
        names = [r["strategy"] for r in disc.load_discovery_log()]
        self.assertEqual(sorted(names), sorted([f"n{i}" for i in range(6)] + ["raced"]))
        self.assertEqual(len(load_tested_index()), 7)

    def test_in_place_index_patch_sees_another_process_line(self):
        from hedge_fund.trading.discovery import append_discovery_evaluations
        from hedge_fund.trading.tested_index import tested_journal_path, tested_name_set

        append_discovery_evaluations([_journal_row("a")])
        self.assertIn("a", tested_name_set())  # warm merged view
        # Another container appends a line without touching this process.
        with tested_journal_path().open("a") as fh:
            fh.write(json.dumps({"name": "other", "q": 0}) + "\n")
        append_discovery_evaluations([_journal_row("b", at="2026-10-28T05:02:00+00:00")])
        names = tested_name_set()
        self.assertIn("other", names)
        self.assertIn("b", names)


    def test_same_size_same_mtime_base_swap_is_seen(self):
        from hedge_fund.trading.discovery import append_discovery_evaluations
        from hedge_fund.trading.tested_index import (
            tested_index_document,
            tested_index_path,
            tested_name_set,
            write_tested_document,
        )

        with paper_state_lock("discovery"):
            write_tested_document(tested_index_document({"aaaa": False}, {}))
        self.assertEqual(tested_name_set(), {"aaaa"})
        path = tested_index_path()
        st = path.stat()
        # Another process installs a same-size base within one mtime tick.
        doc = json.loads(path.read_text())
        doc["jgen"] = "f" * 32
        doc["names"] = {"bbbb": 0}
        tmp = path.with_name("other.tmp")
        tmp.write_text(json.dumps(doc, separators=(",", ":")))
        os.replace(tmp, path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(path.stat().st_size, st.st_size)
        self.assertEqual(tested_name_set(), {"bbbb"})
        append_discovery_evaluations([_journal_row("c")])
        self.assertEqual(tested_name_set(), {"bbbb", "c"})


class AppendOnlyMultiProcessTests(unittest.TestCase):
    def test_concurrent_writers_with_compaction_lose_nothing(self):
        import subprocess
        import sys

        script = (
            "import sys, threading\n"
            "import hedge_fund.trading.discovery as d\n"
            "d.LOG_REWRITE_MAX_BYTES = 1500\n"
            "w = sys.argv[1]\n"
            "for i in range(40):\n"
            "    d.append_discovery_evaluations([{'strategy': f'{w}_{i}', 'qualified': False,"
            " 'sharpe': 0.1, 'trades': 3, 'tested_at': f'2026-10-28T05:{i:02d}:00+00:00'}])\n"
            "for t in threading.enumerate():\n"
            "    if t.name == 'discovery-compact':\n"
            "        t.join()\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, PAPER_STATE=tmp)
            procs = [
                subprocess.Popen([sys.executable, "-c", script, f"w{k}"], env=env)
                for k in range(4)
            ]
            for p in procs:
                self.assertEqual(p.wait(timeout=240), 0)
            with patch.dict(os.environ, {"PAPER_STATE": tmp}):
                _cold_caches()
                from hedge_fund.trading.discovery import compact_discovery_state, load_discovery_log
                from hedge_fund.trading.tested_index import load_tested_index

                expected = {f"w{k}_{i}" for k in range(4) for i in range(40)}
                names = [r["strategy"] for r in load_discovery_log()]
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(set(names), expected)
                self.assertEqual(set(load_tested_index()), expected)
                compact_discovery_state()
                _cold_caches()
                self.assertEqual(set(load_tested_index()), expected)
                self.assertEqual({r["strategy"] for r in load_discovery_log()}, expected)
                _cold_caches()
