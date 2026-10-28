"""Orphaned leases (lost claim replies) and worker claim cadence."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


T0 = datetime(2026, 10, 28, 15, 6, tzinfo=timezone.utc)


def _names(n: int) -> list[str]:
    return [f"don_hi_{6 + 6 * i}" for i in range(n)]


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class _LeaseEnv(unittest.TestCase):
    universe = _names(24)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        env = {
            "PAPER_STATE": self._tmp.name,
            "DISCOVERY_EVAL_TIMEOUT_SECONDS": "600",
            "DISCOVERY_RANDOM_SHARE": "0",
            "DISCOVERY_PAIRED_SHARE": "0",
        }
        self._patches = [
            patch.dict(os.environ, env),
            patch("hedge_fund.trading.leases.schedule_refill_buffer"),
            patch("hedge_fund.trading.leases.discovery_universe", return_value=list(self.universe)),
            patch("hedge_fund.trading.leases.next_refill_batch", return_value=[]),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def claim(self, wid, n, parallel, at):
        from hedge_fund.trading.leases import claim_discovery_batch

        return claim_discovery_batch(wid, n, parallel=parallel, now=_at(at))["names"]

    def beat(self, wid, status, at, *, evaluating=None, queued=None):
        from hedge_fund.trading.leases import touch_worker_unlocked
        from hedge_fund.trading.store import paper_state_lock

        with paper_state_lock("discovery"):
            return touch_worker_unlocked(
                wid, status, now=_at(at), evaluating=evaluating, queued=queued,
            )

    def held(self, wid, at):
        from hedge_fund.trading.leases import lease_snapshot

        snap = lease_snapshot(_at(at))
        for row in snap["workers"]:
            if row["worker_id"] == wid:
                return set(row["in_flight"])
        return set()


class LostClaimReplyTests(_LeaseEnv):
    def test_old_worker_idle_heartbeat_frees_the_lost_claim(self):
        """16:06 freeze: the reply is lost, every later claim came back empty."""
        lost = self.claim("omarchy-1", 4, 2, 0)
        self.assertEqual(len(lost), 4)
        # Lease room (2 * parallel) is full of names the worker never saw.
        self.assertEqual(self.claim("omarchy-1", 4, 2, 60), [])
        # The old worker's reaction to an empty claim: an idle heartbeat
        # with no evaluating list. It used to renew all four leases.
        dropped = self.beat("omarchy-1", "idle", 60)
        self.assertEqual(set(dropped), set(lost))
        again = self.claim("omarchy-1", 4, 2, 120)
        self.assertEqual(len(again), 4)

    def test_old_worker_heartbeats_never_keep_orphans_past_the_hard_cap(self):
        lost = self.claim("omarchy-1", 4, 2, 0)
        # Busy old worker: running heartbeats every 100s, never idle, never
        # lists the lost names. They are not renewed.
        for t in range(100, 1300, 100):
            self.beat("omarchy-1", "running", t, evaluating=[])
        self.assertEqual(self.held("omarchy-1", 901), set())
        taken = self.claim("jensa-2", 4, 2, 901)
        self.assertEqual(set(taken), set(lost))

    def test_stale_unstarted_leases_do_not_use_lease_room(self):
        self.claim("omarchy-1", 4, 2, 0)
        self.assertEqual(self.claim("omarchy-1", 4, 2, 200), [])
        # No heartbeat at all (web down for the heartbeat too): after
        # UNSTARTED_STALE_SECONDS the orphans stop blocking new claims.
        fresh = self.claim("omarchy-1", 4, 2, 301)
        self.assertEqual(len(fresh), 4)

    def test_new_worker_reporting_its_queue_drops_names_it_does_not_have(self):
        lost = self.claim("omarchy-1", 2, 2, 0)
        got = self.claim("omarchy-1", 2, 2, 30)
        started, waiting = got
        # Inside ORPHAN_GRACE a reply may still be in flight: keep.
        self.assertEqual(
            self.beat("omarchy-1", "running", 100, evaluating=[started], queued=[waiting]),
            [],
        )
        dropped = self.beat("omarchy-1", "running", 200, evaluating=[started], queued=[waiting])
        self.assertEqual(set(dropped), set(lost))
        self.assertEqual(self.held("omarchy-1", 200), {started, waiting})

    def test_ingest_heartbeat_payload_self_heals_without_release(self):
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch

        first = claim_discovery_batch("omarchy-1", 4, parallel=2)["names"]
        self.assertEqual(len(first), 4)
        self.assertEqual(claim_discovery_batch("omarchy-1", 4, parallel=2)["names"], [])
        # Exactly what an old worker posts after an empty claim.
        ingest_discovery_payload({
            "evaluations": [],
            "extended_names": [],
            "source": "windows_worker",
            "paper_only": True,
            "worker_id": "omarchy-1",
            "heartbeat": {"status": "idle", "source": "windows_worker", "worker_id": "omarchy-1"},
        })
        self.assertEqual(len(claim_discovery_batch("omarchy-1", 4, parallel=2)["names"]), 4)

    def test_ingest_passes_queued_through(self):
        from hedge_fund.trading.ingest import ingest_discovery_payload
        from hedge_fund.trading.leases import claim_discovery_batch

        names = claim_discovery_batch("omarchy-1", 2, parallel=1)["names"]
        with patch("hedge_fund.trading.ingest.touch_worker_unlocked") as touch:
            ingest_discovery_payload({
                "evaluations": [],
                "extended_names": [],
                "worker_id": "omarchy-1",
                "heartbeat": {
                    "status": "running",
                    "worker_id": "omarchy-1",
                    "evaluating": [names[0]],
                    "queued": [names[1]],
                },
            })
        self.assertEqual(touch.call_args.kwargs["evaluating"], [names[0]])
        self.assertEqual(touch.call_args.kwargs["queued"], [names[1]])


class HeartbeatRenewalTests(_LeaseEnv):
    def test_idle_heartbeat_does_not_renew_unstarted_leases(self):
        names = self.claim("omarchy-1", 2, 1, 0)
        self.beat("omarchy-1", "idle", 800, evaluating=[])
        self.assertEqual(self.held("omarchy-1", 800), set())
        self.assertEqual(set(self.claim("jensa-2", 2, 1, 801)), set(names))

    def test_running_heartbeat_without_the_name_does_not_renew_it(self):
        names = self.claim("omarchy-1", 2, 1, 0)
        started, other = names
        self.beat("omarchy-1", "running", 100, evaluating=[started])
        self.beat("omarchy-1", "running", 800, evaluating=[started])
        self.assertEqual(self.held("omarchy-1", 899), {started, other})
        # `other` was never listed, so it lapses at its first TTL.
        self.assertEqual(self.held("omarchy-1", 901), {started})

    def test_started_leases_still_renew(self):
        (name,) = self.claim("omarchy-1", 1, 1, 0)
        self.beat("omarchy-1", "running", 100, evaluating=[name])
        self.beat("omarchy-1", "running", 800, evaluating=[name])
        self.assertEqual(self.held("omarchy-1", 1000), {name})
        # Per-name cap from started_at (100 + 1200).
        self.assertEqual(self.held("omarchy-1", 1301), set())

    def test_started_lease_renews_on_old_heartbeat_without_lists(self):
        (name,) = self.claim("omarchy-1", 1, 1, 0)
        self.beat("omarchy-1", "running", 100, evaluating=[name])
        self.beat("omarchy-1", "running", 800)
        self.assertEqual(self.held("omarchy-1", 1000), {name})

    def test_idle_heartbeat_keeps_a_started_lease(self):
        (name,) = self.claim("omarchy-1", 1, 1, 0)
        self.beat("omarchy-1", "running", 100, evaluating=[name])
        self.assertEqual(self.beat("omarchy-1", "idle", 200, evaluating=[]), [])
        self.assertEqual(self.held("omarchy-1", 200), {name})

    def test_queued_name_renews_but_not_past_claimed_at_cap(self):
        (name,) = self.claim("omarchy-1", 1, 1, 0)
        for t in range(100, 1300, 100):
            self.beat("omarchy-1", "running", t, evaluating=[], queued=[name])
        self.assertEqual(self.held("omarchy-1", 1199), {name})
        self.assertEqual(self.held("omarchy-1", 1201), set())

    def test_paused_heartbeat_leaves_leases_alone(self):
        names = self.claim("omarchy-1", 2, 1, 0)
        self.assertEqual(self.beat("omarchy-1", "paused", 100), [])
        self.assertEqual(self.held("omarchy-1", 100), set(names))


class WorkerReleaseTests(unittest.TestCase):
    def _run_main(self, claim_effect, extra=()):
        from scripts import discovery_worker

        events: list[tuple] = []

        def release(base, token, worker_id, *, names=None, status="idle"):
            events.append(("release", worker_id, names))
            return {}

        def claim(*args, **kwargs):
            events.append(("claim",))
            if isinstance(claim_effect, BaseException):
                raise claim_effect
            return claim_effect

        with tempfile.TemporaryDirectory() as tmp:
            env = {"PAPER_STATE": tmp, "PAPER_DISCOVERY_INGEST_TOKEN": "paper-secret-token"}
            with patch.dict(os.environ, env), \
                    patch.object(discovery_worker, "poll_farm_enabled", return_value=True), \
                    patch.object(discovery_worker, "_claim_remote", side_effect=claim), \
                    patch.object(discovery_worker, "_release_remote", side_effect=release), \
                    patch.object(discovery_worker, "_post_ingest", return_value={}), \
                    patch.object(discovery_worker, "run_batch", return_value={"evaluated": 0}), \
                    patch.object(discovery_worker.time, "sleep"):
                rc = discovery_worker.main(
                    ["--once", "--workers", "2", "--worker-id", "omarchy-1", *extra]
                )
        return rc, events

    def test_startup_releases_own_leases_before_the_first_claim(self):
        rc, events = self._run_main({"names": [], "paused": False})
        self.assertEqual(rc, 0)
        self.assertEqual(events[0], ("release", "omarchy-1", None))
        self.assertEqual(events[1], ("claim",))

    def test_claim_5xx_releases_everything_before_backoff(self):
        from scripts.discovery_worker import RemoteHTTPError

        rc, events = self._run_main(RemoteHTTPError("502", code=502))
        self.assertEqual(rc, 1)
        self.assertEqual(events[1], ("claim",))
        self.assertEqual(events[2], ("release", "omarchy-1", None))

    def test_claim_timeout_releases_everything(self):
        rc, events = self._run_main(TimeoutError("timed out"))
        self.assertEqual(rc, 1)
        self.assertEqual(events[2], ("release", "omarchy-1", None))

    def test_claim_4xx_does_not_release_from_the_loop(self):
        from scripts.discovery_worker import RemoteHTTPError

        rc, events = self._run_main(RemoteHTTPError("401", code=401))
        self.assertEqual(rc, 1)
        # Startup release, the claim, then only the exit release.
        self.assertEqual([e[0] for e in events], ["release", "claim", "release"])

    def test_local_plan_does_not_release(self):
        from scripts import discovery_worker

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "crypto_history_5m.json").write_text("{}")
            with patch.dict(os.environ, {"PAPER_STATE": tmp}), \
                    patch.object(discovery_worker, "_release_remote") as release, \
                    patch.object(discovery_worker, "run_batch", return_value={"evaluated": 0}):
                discovery_worker.main(["--once", "--local-plan", "--no-ingest", "--workers", "1"])
        release.assert_not_called()

    def test_claim_failure_backoff_grows_and_honours_retry_after(self):
        from scripts.discovery_worker import (
            CLAIM_FAIL_BACKOFF_MAX_SECONDS,
            RemoteHTTPError,
            _claim_fail_backoff,
        )

        err = RuntimeError("timeout")
        delays = [_claim_fail_backoff(err, k, 60) for k in range(1, 6)]
        self.assertEqual(delays[:4], [60, 120, 240, 480])
        self.assertEqual(delays[4], CLAIM_FAIL_BACKOFF_MAX_SECONDS)
        hinted = RemoteHTTPError("503", code=503, retry_after=17)
        self.assertEqual(_claim_fail_backoff(hinted, 3, 60), 17)

    def test_heartbeat_carries_queued_names(self):
        from scripts.discovery_worker import _post_ingest

        with patch("scripts.discovery_worker._http_json", return_value={}) as http:
            _post_ingest(
                "https://trading.example/api/discovery/ingest",
                "t",
                [],
                [],
                None,
                heartbeat="running",
                worker_id="omarchy-1",
                evaluating=["a"],
                queued=["b", "c"],
            )
            _post_ingest(
                "https://trading.example/api/discovery/ingest",
                "t",
                [],
                [],
                None,
                heartbeat="idle",
                worker_id="omarchy-1",
            )
        first = http.call_args_list[0].kwargs["data"]["heartbeat"]
        second = http.call_args_list[1].kwargs["data"]["heartbeat"]
        self.assertEqual(first["queued"], ["b", "c"])
        self.assertNotIn("queued", second)


class _FakePool:
    def __init__(self, clock, duration):
        self.clock = clock
        self.duration = duration

    def apply_async(self, fn, args):
        name = args[0]
        done_at = self.clock["t"] + self.duration(name)
        ar = MagicMock()
        ar.ready.side_effect = lambda: self.clock["t"] >= done_at
        ar.get.side_effect = lambda timeout=None: {"strategy": name}
        return ar

    def terminate(self):
        pass

    def join(self):
        pass


class _FakeServer:
    """Lease room 2 * parallel; a finished name holds its lease until acked."""

    def __init__(self, clock, *, width, supply, ack_lag):
        self.clock = clock
        self.cap = 2 * width
        self.supply = supply
        self.ack_lag = ack_lag
        self.held: set[str] = set()
        self.acks: list[tuple[float, str]] = []
        self.claims = 0
        self.empty = 0
        self.ingests = 0
        self.seq = 0

    def tick(self):
        now = self.clock["t"]
        due = [a for a in self.acks if a[0] <= now]
        for a in due:
            self.acks.remove(a)
            self.held.discard(a[1])
            self.ingests += 1

    def claim(self, n):
        self.tick()
        self.claims += 1
        room = max(0, self.cap - len(self.held))
        k = min(n, room, self.supply)
        out = []
        for _ in range(k):
            self.seq += 1
            out.append(f"n{self.seq}")
        self.supply -= k
        self.held.update(out)
        if not out:
            self.empty += 1
        return out

    def finish(self, name, _rec):
        self.acks.append((self.clock["t"] + self.ack_lag, name))


def simulate_cadence(run_inflight, *, width=6, total=240, ack_lag=1.0, refill_style="new"):
    """Drive ``_run_inflight`` against a fake prod. Returns counters."""
    clock = {"t": 0.0}
    server = _FakeServer(clock, width=width, supply=total, ack_lag=ack_lag)
    durations = [18.0, 22.0, 26.0, 20.0, 24.0, 21.0, 23.0]
    busy = {"slot_seconds": 0.0}
    inflight_now = {"n": 0}

    def duration(name):
        return durations[int(name[1:]) % len(durations)]

    def sleeper(dt):
        busy["slot_seconds"] += inflight_now["n"] * dt
        clock["t"] += dt
        server.tick()

    def on_inflight(names):
        inflight_now["n"] = len(names)

    def refill(n):
        # run_batch's top-up loop. "old" asked again after any non-empty
        # reply; "new" stops on a short one.
        runnable: list[str] = []
        asks = 0
        while len(runnable) < n and asks < 8:
            asks += 1
            want = n - len(runnable)
            got = server.claim(want)
            if not got:
                break
            runnable.extend(got)
            if refill_style == "new" and len(got) < want:
                break
        return runnable

    first = server.claim(width)
    kwargs = {}
    if refill_style == "new":
        kwargs["unacked"] = lambda: len(server.acks)
    with patch("scripts.discovery_worker._drop_eval_pool"):
        run_inflight(
            first,
            n_workers=width,
            timeout_s=0,
            finish=server.finish,
            pool_factory=lambda: _FakePool(clock, duration),
            trial_for=lambda _n: None,
            sleeper=sleeper,
            clock=lambda: clock["t"],
            on_inflight=on_inflight,
            refill=refill,
            prefetch=width,
            **kwargs,
        )
    while server.acks:
        clock["t"] += 1.0
        server.tick()
    return {
        "claims": server.claims,
        "empty": server.empty,
        "ingests": server.ingests,
        "elapsed": clock["t"],
        "utilization": busy["slot_seconds"] / (width * clock["t"]) if clock["t"] else 0.0,
    }


class ClaimCadenceTests(unittest.TestCase):
    def test_claims_per_ingest_is_bounded_and_slots_stay_full(self):
        from scripts.discovery_worker import _run_inflight

        # The pre-fix worker: 445 claims (240 empty) for 240 ingests here,
        # 29.7 claims/min vs 16 ingests/min, the same ratio prod saw.
        for lag in (0.5, 2.0):
            stats = simulate_cadence(_run_inflight, width=6, total=240, ack_lag=lag)
            self.assertEqual(stats["ingests"], 240)
            self.assertLessEqual(stats["claims"] / stats["ingests"], 0.4)
            self.assertLessEqual(stats["empty"], 3)
            self.assertGreater(stats["utilization"], 0.97)

    def test_no_claim_while_a_full_wave_is_queued(self):
        from scripts.discovery_worker import _run_inflight

        clock = {"t": 0.0}
        asks: list[int] = []

        def refill(n):
            asks.append(n)
            return []

        with patch("scripts.discovery_worker._drop_eval_pool"):
            _run_inflight(
                _names(12),
                n_workers=2,
                timeout_s=0,
                finish=lambda *_: None,
                pool_factory=lambda: _FakePool(clock, lambda _n: 10.0),
                trial_for=lambda _n: None,
                sleeper=lambda dt: clock.__setitem__("t", clock["t"] + dt),
                clock=lambda: clock["t"],
                refill=refill,
                prefetch=2,
            )
        # 12 names, width 2: the queue holds >= 2 until the last wave.
        self.assertLessEqual(len(asks), 2)

    def test_empty_replies_back_off_instead_of_asking_every_completion(self):
        from scripts.discovery_worker import (
            CLAIM_EMPTY_BACKOFF_MAX_SECONDS,
            CLAIM_EMPTY_BACKOFF_SECONDS,
            _run_inflight,
        )

        clock = {"t": 0.0}
        asks: list[float] = []

        def refill(n):
            asks.append(clock["t"])
            return []

        # One long eval holds the batch open for 15 minutes; prod is dry.
        with patch("scripts.discovery_worker._drop_eval_pool"):
            _run_inflight(
                ["long_one"],
                n_workers=6,
                timeout_s=0,
                finish=lambda *_: None,
                pool_factory=lambda: _FakePool(clock, lambda _n: 900.0),
                trial_for=lambda _n: None,
                sleeper=lambda dt: clock.__setitem__("t", clock["t"] + dt),
                clock=lambda: clock["t"],
                refill=refill,
                prefetch=6,
            )
        gaps = [b - a for a, b in zip(asks, asks[1:])]
        self.assertGreaterEqual(gaps[0], CLAIM_EMPTY_BACKOFF_SECONDS - 0.1)
        self.assertTrue(all(g2 >= g1 - 0.1 for g1, g2 in zip(gaps, gaps[1:])))
        self.assertLessEqual(max(gaps), CLAIM_EMPTY_BACKOFF_MAX_SECONDS + 0.1)
        # 900s at the old cadence polled every 0.05s idle tick or every
        # completion; now about 30+60+120+240+240+240.
        self.assertLessEqual(len(asks), 7)

    def test_idle_slot_claims_again_after_backoff(self):
        from scripts.discovery_worker import CLAIM_EMPTY_BACKOFF_SECONDS, _run_inflight

        clock = {"t": 0.0}
        supply = {"open_at": 10.0, "left": 1}
        started: dict[str, float] = {}

        def refill(n):
            if clock["t"] < supply["open_at"] or not supply["left"]:
                return []
            supply["left"] -= 1
            return ["late_name"]

        class Pool(_FakePool):
            def apply_async(self, fn, args):
                started[args[0]] = clock["t"]
                return super().apply_async(fn, args)

        with patch("scripts.discovery_worker._drop_eval_pool"):
            _run_inflight(
                ["long_one"],
                n_workers=2,
                timeout_s=0,
                finish=lambda *_: None,
                pool_factory=lambda: Pool(clock, lambda n: 200.0 if n == "long_one" else 1.0),
                trial_for=lambda _n: None,
                sleeper=lambda dt: clock.__setitem__("t", clock["t"] + dt),
                clock=lambda: clock["t"],
                refill=refill,
                prefetch=0,
            )
        self.assertIn("late_name", started)
        self.assertAlmostEqual(started["late_name"], CLAIM_EMPTY_BACKOFF_SECONDS, delta=0.2)

    def test_short_top_up_reply_is_not_asked_again(self):
        from tests.test_worker_inflight import _Batch

        calls: list[tuple[float, int]] = []

        with tempfile.TemporaryDirectory() as tmp:
            batch = _Batch(Path(tmp), timeout="0", workers=2, names=["first"])

            def claim_more(n):
                calls.append((batch.clock["t"], n))
                return ["extra_one"] if len(calls) == 1 else []

            batch.ready_after = lambda name, attempt: 1
            batch.run(claim_more=claim_more, prefetch=2)
        # Deficit 3, one name back: the same top-up does not ask again.
        self.assertEqual(calls[0], (0.0, 3))
        times = [t for t, _n in calls]
        self.assertEqual(len(times), len(set(times)))

    def test_result_heartbeat_lists_queued_names_in_claim_mode(self):
        from tests.test_worker_inflight import _Batch

        from scripts.discovery_worker import _resolve

        seen: list[dict] = []

        def submit(item):
            # Resolve as the post thread would, but at a known point.
            seen.append({
                "names": [e.get("strategy") for e in item["evals"]],
                "evaluating": item["evaluating"],
                "queued": _resolve(item.get("queued")),
            })
            return False

        with tempfile.TemporaryDirectory() as tmp:
            batch = _Batch(Path(tmp), timeout="30", workers=1, names=["a", "b", "c"])
            batch.ready_after = lambda name, attempt: 1
            with patch("scripts.discovery_worker._release_remote", return_value={}), \
                    patch("scripts.discovery_worker._poster.submit", side_effect=submit):
                batch.run(claim_mode=True)
        rows = [s for s in seen if s["names"]]
        self.assertEqual(rows[0]["names"], ["a"])
        self.assertEqual(rows[0]["evaluating"], ["b"])
        self.assertEqual(rows[0]["queued"], ["c"])
        self.assertEqual(rows[-1]["queued"], [])


if __name__ == "__main__":
    unittest.main()
