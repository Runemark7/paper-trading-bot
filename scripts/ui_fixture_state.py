"""Write a sample PAPER_STATE for running the dashboard locally.

Paper-only demo data: two champions (one with trades, one just admitted with
none), a discovery log with passes, near-misses and plain fails, a farm
heartbeat, and worker leases. Nothing here touches a real account.

    .venv/bin/python scripts/ui_fixture_state.py /tmp/paper_fixture
    PAPER_STATE=/tmp/paper_fixture .venv/bin/python -m hedge_fund.web.server
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from hedge_fund.trading.constants import GATE_RULES  # noqa: E402
from hedge_fund.trading.store import TradeStore  # noqa: E402

FIXTURE_MARKER = ".ui_fixture_state"
CHAMPION_A = "h1_ema_abv_50&mom_18b_gt2pc&sma_abv_30&rsi_14_>45"
CHAMPION_B = "h4_ema_abv_20&mom_18b_gt2pc&sma_abv_30&rsi_14_>50"

ATOMS = [
    "sma_abv_30", "sma_abv_40", "ema_abv_30", "ema_abv_40", "rsi_14_>45", "rsi_14_>50",
    "rsi_14_>55", "rsi_14_>60", "mom_18b_gt2pc", "mom_12b_gt4pc", "mom_18b_gt6pc",
    "dip_24b_lt3pc", "dip_12b_lt2pc", "h1_ema_abv_24", "h1_ema_abv_50", "h4_ema_abv_20",
    "h4_sma_abv_50", "don_hi_48", "don_lo_24", "near_swing_lo_12", "dbl_bot_24",
    "wt_cross_up_os", "sma_stack", "ema_stack_7_25_50", "vol_lowsm_30_90",
]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def gate_block(rng: random.Random, *, passed: bool, fail: set[str]) -> dict:
    n_nb = rng.choice([6, 8, 10, 12])
    nb_pass = n_nb if "perturb" not in fail else rng.randint(1, int(n_nb * 0.6))
    return {
        "passed": passed,
        "rules": GATE_RULES,
        "holdout_pass": "holdout" not in fail,
        "dsr_pass": "dsr" not in fail,
        "pbo_pass": "pbo" not in fail,
        "perturb_pass": "perturb" not in fail,
        "cost_pass": "cost" not in fail,
        "dsr": round(rng.uniform(0.96, 0.995) if "dsr" not in fail else rng.uniform(0.6, 0.94), 4),
        "pbo": round(rng.uniform(0.02, 0.18) if "pbo" not in fail else rng.uniform(0.22, 0.6), 4),
        "neighbours": n_nb,
        "neighbours_pass": nb_pass,
        "cost_sharpe": round(rng.uniform(0.35, 0.9) if "cost" not in fail else rng.uniform(0.0, 0.28), 4),
        "holdout": {
            "sharpe": round(rng.uniform(0.35, 1.2) if "holdout" not in fail else rng.uniform(-0.4, 0.28), 4),
            "trades": rng.randint(10, 40),
        },
        "n_trials": 18000,
    }


def reasons_for(block: dict) -> list[str]:
    out = []
    if not block["holdout_pass"]:
        out.append(f"holdout_sharpe {block['holdout']['sharpe']:.2f} < 0.3")
    if not block["dsr_pass"]:
        out.append(f"dsr {block['dsr']:.3f} < 0.95")
    if not block["pbo_pass"]:
        out.append(f"pbo {block['pbo']:.3f} > 0.2")
    if not block["perturb_pass"]:
        n, p = block["neighbours"], block["neighbours_pass"]
        out.append(f"neighbours {p}/{n} = {p / n:.0%} < 70%")
    if not block["cost_pass"]:
        out.append(f"cost stress sharpe {block['cost_sharpe']:.2f} < 0.3 (2x fee + 5bps)")
    return out


def discovery_rows(rng: random.Random, now: datetime, n: int) -> list[dict]:
    rows = []
    seen = {CHAMPION_A, CHAMPION_B}
    for i in range(n):
        k = rng.randint(2, 7)
        name = "&".join(sorted(rng.sample(ATOMS, k)))
        if name in seen:
            continue
        seen.add(name)
        tested = now - timedelta(minutes=4 * i + rng.randint(0, 3))
        sharpe = round(rng.gauss(0.15, 0.35), 3)
        trades = rng.randint(4, 220)
        sma = round(rng.uniform(-200, 600), 2)
        pnl = round(sharpe * rng.uniform(600, 1800), 2)
        row = {
            "strategy": name,
            "tested_at": iso(tested),
            "sharpe": sharpe,
            "trades": trades,
            "test_pnl": pnl,
            "daily_sharpe": round(sharpe * 0.8, 3),
            "bh_daily_sharpe": 0.62,
            "bh_oos_pnl": 5120.0,
            "bh_role": "informational",
            "sma_stack_oos_pnl": sma,
            "gate_rules": GATE_RULES,
            "timeframe": "5m",
            "risk_policy": "rm_v1",
        }
        reasons = []
        if trades < 30:
            reasons.append(f"oos_trades {trades} < 30")
        if sharpe < 0.30:
            reasons.append(f"oos_sharpe {sharpe:.2f} < 0.3")
        if pnl <= sma:
            reasons.append(f"oos_pnl {pnl:.2f} <= sma_stack {sma:.2f}")
        if not reasons:
            fail = set(rng.sample(["holdout", "dsr", "pbo", "perturb", "cost"], rng.choice([0, 1, 1, 1, 2, 3])))
            block = gate_block(rng, passed=not fail, fail=fail)
            row["gate_v4"] = block
            reasons = reasons_for(block)
        row["fail_reasons"] = reasons
        row["qualified"] = not reasons
        rows.append(row)
    return rows


def write_trades(root: Path, name: str, trades: list[dict], open_lots: list[dict], equity: float, now: datetime) -> None:
    slug = name.replace("/", "_").replace(":", "_")
    db = root / f"trades_{slug}.sqlite"
    st = TradeStore(db)
    for t in trades:
        tid = st.open_trade("BTC/USDT", "5m", name, 0.5, t["entry"], t["size"], entry_fee=t["entry"] * t["size"] * 0.001)
        st.conn.execute("UPDATE trades SET entry_ts=? WHERE id=?", (iso(t["entry_ts"]), tid))
        if t.get("exit") is not None:
            exit_fee = t["exit"] * t["size"] * 0.001
            entry_fee = t["entry"] * t["size"] * 0.001
            pnl = (t["exit"] - t["entry"]) * t["size"] - exit_fee - entry_fee
            st.close_trade(tid, t["exit"], t["reason"], exit_fee, pnl, (t["exit"] - t["entry"]) / t["entry"], 1 if pnl > 0 else 0)
            st.conn.execute("UPDATE trades SET exit_ts=? WHERE id=?", (iso(t["exit_ts"]), tid))
        st.conn.commit()
    lots = []
    for i, lot in enumerate(open_lots, start=1):
        tid = st.open_trade("BTC/USDT", "5m", name, 0.5, lot["entry"], lot["size"], entry_fee=lot["entry"] * lot["size"] * 0.001, lot_id=i)
        st.conn.execute("UPDATE trades SET entry_ts=? WHERE id=?", (iso(lot["entry_ts"]), tid))
        st.conn.commit()
        lots.append({
            "lot_id": i, "ticker": "BTC/USDT", "quantity": lot["size"], "entry_price": lot["entry"],
            "stop_loss": lot["stop"], "entry_fee": lot["entry"] * lot["size"] * 0.001, "entry_condition": name,
        })
    cash = equity - sum(lot["entry"] * lot["size"] for lot in open_lots)
    st.save_account_state({"broker": {"cash": cash, "lots": lots}})
    st.snapshot_equity(equity, baseline=10_000.0)
    st.close()


def build(root: Path, seed: int = 7) -> None:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    marker = root / FIXTURE_MARKER
    if root.exists() and any(root.iterdir()):
        if not marker.exists():
            # Never wipe a real PAPER_STATE: only a dir this script wrote is replaced.
            raise SystemExit(f"refusing to overwrite {root}: not empty and not a fixture dir (no {FIXTURE_MARKER})")
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text("sample paper state written by scripts/ui_fixture_state.py\n")

    a_since = now - timedelta(days=9, hours=3)
    b_since = now - timedelta(hours=20)
    a_trades = [
        {"entry_ts": a_since + timedelta(days=1, hours=2), "entry": 61250.0, "size": 0.0412,
         "exit_ts": a_since + timedelta(days=1, hours=9), "exit": 63080.0, "reason": "take_profit"},
        {"entry_ts": a_since + timedelta(days=3, hours=5), "entry": 62940.0, "size": 0.0398,
         "exit_ts": a_since + timedelta(days=3, hours=11), "exit": 61990.0, "reason": "stop_loss"},
        {"entry_ts": a_since + timedelta(days=5, hours=1), "entry": 63410.0, "size": 0.0405,
         "exit_ts": a_since + timedelta(days=6, hours=4), "exit": 65320.0, "reason": "take_profit"},
    ]
    a_open = [{"entry_ts": now - timedelta(hours=6), "entry": 66120.0, "size": 0.0389, "stop": 65130.0}]
    closed_pnl = sum(
        (t["exit"] - t["entry"]) * t["size"] - (t["exit"] + t["entry"]) * t["size"] * 0.001 for t in a_trades
    )
    write_trades(root, CHAMPION_A, a_trades, a_open, 10_000.0 + closed_pnl - 66120.0 * 0.0389 * 0.001, now)
    write_trades(root, CHAMPION_B, [], [], 10_000.0, now)

    log = discovery_rows(rng, now, 420)
    for name, since in ((CHAMPION_A, a_since), (CHAMPION_B, b_since)):
        block = gate_block(rng, passed=True, fail=set())
        log.append({
            "strategy": name, "tested_at": iso(since - timedelta(hours=2)), "qualified": True,
            "sharpe": 0.71, "trades": 64, "test_pnl": 1840.2, "daily_sharpe": 0.88,
            "bh_daily_sharpe": 0.62, "bh_oos_pnl": 5120.0, "sma_stack_oos_pnl": 410.0,
            "gate_rules": GATE_RULES, "gate_v4": block, "fail_reasons": [],
        })
    log.sort(key=lambda r: r["tested_at"], reverse=True)
    (root / "discovery_log.json").write_text(json.dumps(log))

    champs = []
    for name, since, closed, pnl, wins in (
        (CHAMPION_A, a_since, 3, round(closed_pnl, 2), 2),
        (CHAMPION_B, b_since, 0, 0.0, 0),
    ):
        row = next(r for r in log if r["strategy"] == name)
        g = row["gate_v4"]
        champs.append({
            "name": name, "closed": closed, "pnl": pnl, "wins": wins,
            "admitted_at": iso(since), "champion_since": iso(since),
            "source": "5m_qualification_filter", "timeframe": "5m", "risk_policy": "rm_v1",
            "sharpe_qual": row["sharpe"],
            "gate_v4": {k: g.get(k) for k in ("passed", "rules", "dsr", "pbo", "n_trials", "neighbours", "neighbours_pass", "cost_sharpe")},
        })
    (root / "champions.json").write_text(json.dumps({"champions": champs, "synced_until": iso(now - timedelta(minutes=3))}))
    (root / "graduated.json").write_text("[]")

    (root / "heartbeat.json").write_text(json.dumps({
        "last_pass_at": iso(now - timedelta(seconds=40)), "closed": 0, "accounts_checked": 2,
        "interval_seconds": 60, "source": "hedge_fund.trading.heartbeat",
    }))
    (root / "pipeline_stamp.json").write_text(json.dumps({
        "phase": "run_isolated", "status": "finished", "at": iso(now - timedelta(minutes=2)),
        "started_at": iso(now - timedelta(minutes=2, seconds=20)), "finished_at": iso(now - timedelta(minutes=2)),
        "source": "live_cycle.py",
    }))
    (root / "discovery_farm.json").write_text(json.dumps({
        "enabled": True, "updated_at": iso(now - timedelta(days=1)),
        "heartbeat_at": iso(now - timedelta(seconds=25)), "heartbeat_status": "running",
    }))
    untested = ["&".join(sorted(rng.sample(ATOMS, rng.randint(2, 7)))) for _ in range(6)]
    exp = iso(now + timedelta(minutes=20))
    leases = {n: {"worker_id": "win-pc-01" if i % 2 else "linux-box", "claimed_at": iso(now - timedelta(minutes=3)), "expires_at": exp}
              for i, n in enumerate(untested[:4])}
    (root / "discovery_leases.json").write_text(json.dumps({
        "leases": leases,
        "workers": {
            "win-pc-01": {"last_seen": iso(now - timedelta(seconds=20)), "status": "running"},
            "linux-box": {"last_seen": iso(now - timedelta(seconds=50)), "status": "running"},
            "old-laptop": {"last_seen": iso(now - timedelta(hours=7)), "status": "idle"},
        },
    }))
    (root / "discovery_extended.json").write_text(json.dumps(untested))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("state_dir")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    build(Path(args.state_dir), seed=args.seed)
    print(f"wrote sample paper state to {args.state_dir}")


if __name__ == "__main__":
    main()
