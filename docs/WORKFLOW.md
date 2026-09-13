# Discovery and live workflow

**Paper only.** No real-money broker. OOS **thresholds** are frozen; these
diagrams do not change them.

Maintenance: update these diagrams in the same PR that changes mint/farm/eval/prod topology (refill recipe shape, window count, ingest, farm location, gate meaning).

This is the canonical map of how a name is minted, walked on jensa, gated,
ingested, and traded on the cluster. Read it on GitHub — the Mermaid
blocks render there. PROTOCOL amendments remain the honesty contract;
this file is the living topology.

## Legend

| Piece | Where | What it does |
|---|---|---|
| **Mint** | `hedge_fund/trading/refill.py` on the farm | Bounded DIP/MOM + structure AND recipe, plus densified HTF buyer-regime ANDs (`h1_ema_abv_{15,20,24,30}` / `h4_ema_abv_{12,24,48}` / `h4_sma_abv_{24,50}`). Recipe emits HTF×mom (2-atom and 3-atom, including `MOM_FILTERS_HTF_DENSE`) before HTF×dip — mom-before-dip. When never-tested leftovers run dry, the next handful is appended to `state/discovery_extended.json`. Static `generate_universe()` stays inside the ~40–120 compiled-list band. |
| **Farm** | Alexander's Windows PC (`jensa`) | `scripts/discovery_worker.py` evaluates names against local `state/crypto_history_5m.json` (Binance 5m, **BTC/USDT and ETH/USDT only**). Same fail-once / auto-refill / aggregate-OOS / `rm_v1` / 5m rules as `scripts/tournament_engine.py`. |
| **Eval** | `parse_strategy` → walk-forward → backtest → gate | Name string → AND atoms on native 5m → 8 chronological ~90d windows → `rm_v1` paper backtest → aggregate OOS in `hedge_fund/trading/qualify.py`. |
| **Ingest** | `POST /api/discovery/ingest` | Token-gated. Pass → champion + isolated paper book on k8s. Fail → parked forever (fail-once). Ingest never culls existing champions. |
| **Prod** | k8s cycle sidecar | `DISCOVERY_ON_CYCLE=0` — live trading only (`run_isolated` → collect → report). Do not turn discovery back on in-cluster. UI: `/discovery`. |
| **Farm Start/Stop** | `/discovery` → `POST /api/discovery/farm` | Same ingest token. Worker **idles** (does not exit). Start cannot relaunch a dead process. |
| **Champion pool ops** | `POST /api/champions/retain` and `POST /api/champions/cull_undated` | Same ingest token. Explicit paper-ops exception: drop names from `champions.json` so `live_cycle` stops them. Does not delete trade DBs. `cull_undated` keeps only non-empty `champion_since`. |

**Current walk-forward (after [#43](https://github.com/Runemark7/paper-trading-bot/pull/43)):**
`QUAL_N_WINDOWS` = 8, `QUAL_WINDOW_DAYS` = 90, `QUAL_COVERAGE_DAYS` = 720
(~2y of native 5m). Per-window size stays 90d (honest hold-outs, not one
giant in-sample). Parked 3-window evals stay parked — ingest requalify
needs stored `regimes_tested` = 8. Re-fetch `crypto_history_5m.json` on
jensa so the tape actually covers the span.

**FROZEN OOS gates** (do not edit here to "make names pass"):

- OOS trades ≥ 30
- average OOS Sharpe ≥ 0.30
- beat buy-and-hold on the same test windows after fees
- beat `sma_stack` on those windows
- all-windows non-negative is a **diagnostic only** (not a veto)
- fail-once: a non-qualify parks that name forever

Train PnL is logged and never scored. Score is OOS-only.

Farm install and pause runbook: [WINDOWS_DISCOVERY.md](WINDOWS_DISCOVERY.md).
Honesty contract: [PROTOCOL.md](../PROTOCOL.md).

---

## 1. End-to-end: mint → farm → gate → live

```mermaid
flowchart TB
  recipe["Mint: hedge_fund/trading/refill.py\nHTF densify + mom-before-dip\nthen DIP/MOM + structure"]
  ext["state/discovery_extended.json"]
  recipe --> ext

  subgraph farm [Farm: jensa Windows PC]
    worker["scripts/discovery_worker.py"]
    hist["state/crypto_history_5m.json\nBinance 5m BTC/ETH"]
  end
  ext --> worker
  hist --> worker

  parse["parse_strategy(name) in dynamic.py\nAND atoms on native 5m\n+ causal HTF regime from 5m"]
  wf["Walk-forward 8 x 90d"]
  bt["Backtest rm_v1"]
  gate["Aggregate OOS gate\nhedge_fund/trading/qualify.py"]
  worker --> parse --> wf --> bt --> gate

  frozen["FROZEN: trades at least 30, Sharpe at least 0.30,\nbeat B and H, beat sma_stack,\nall-windows non-neg diagnostic only, fail-once"]
  gate --> frozen

  ingest["POST /api/discovery/ingest"]
  park["Fail: ingest parked forever"]
  frozen -->|"pass"| ingest
  frozen -->|"fail"| park

  subgraph prod [Prod k8s]
    champs["Champions / paper books"]
    live["DISCOVERY_ON_CYCLE=0\nlive trading only"]
    ui["UI /discovery"]
    ctl["Start/Stop token-gated"]
  end
  ingest -->|"admit"| champs
  champs --> live
  ui --> ctl
  ctl -.->|"pause / resume"| worker
```

Caption: Names are minted on the farm, walked on jensa against Binance 5m
BTC/ETH, then POSTed to prod. The cluster never runs walk-forwards.
Start/Stop on `/discovery` is token-gated and only idles the worker.

---

## 2. Inside one strategy name

```mermaid
flowchart LR
  name["Name string\ne.g. mom_36b_gt2pc AND don_hi_12"]
  atoms["Atoms on native 5m\nAND joins"]
  pred["Predicate pred"]
  windows["8 chronological windows\n70/30 train/test each"]
  score["Aggregate OOS score\ntrades, Sharpe, beat B and H, beat sma_stack"]
  decision{"qualify.py"}
  passNode["Pass: ingest admit"]
  failNode["Fail: parked forever"]

  name --> atoms --> pred --> windows --> score --> decision
  decision -->|"all frozen gates hold"| passNode
  decision -->|"any frozen gate misses"| failNode
```

Caption: `&` in the name is AND (every atom true on that 5m bar). Each
window backtests with `rm_v1`. A single empty or negative window is
logged (`all_windows_nonneg`) and does not veto. One fail parks the name.

---

## 3. HTF buyer-regime atoms (shipped v1)

Higher-timeframe buyer-regime is **mint/parser only**. Causal 4h/1h
closes are resampled from the native 5m series; predicates use
**completed** HTF bars only (no lookahead). `daily()` / `h1()` / `m5()`
wrappers stay refused. Topology is unchanged: mint → farm → gate.

Atoms (discoverable, not one oracle): shipped v1 `h4_ema_abv_24`,
`h4_sma_abv_50`, `h1_ema_abv_24`, plus densified periods
`h1_ema_abv_15` / `h1_ema_abv_20` / `h1_ema_abv_30` /
`h4_ema_abv_12` / `h4_ema_abv_48` / `h4_sma_abv_24` (kept distinct
under `near_duplicate_key`). Long-only book: HTF sellers → no new
long (flat), not short. When HTF says buyers and 5m is in a dip,
that is buy-the-dip; disagree → HTF wins (no long). Recipe emits
HTF×mom (and HTF×mom×`don_hi`/`near_swing_lo`) **before** HTF×dip
for both 2-atom and 3-atom passes — mom-before-dip — then leftover
mean-reversion. Short-continuation dense mom (`mom_18b_gt4pc` /
`mom_18b_gt6pc` / `mom_12b_gt6pc`) ANDs onto every HTF tag. OOS
gates are unchanged.

```mermaid
flowchart TB
  shipped["SHIPPED v1 + densify:\nh1_ema_abv_15/20/24/30\nh4_ema_abv_12/24/48\nh4_sma_abv_24/50"]
  order["mom-before-dip:\nHTF x mom then HTF x dip\n2-atom then 3-atom"]
  mintOnly["AND into refill recipe only\nnew names in discovery_extended.json"]
  sameEval["Same 5m walk-forward + rm_v1\n8 x 90d"]
  sameGate["Same frozen OOS gates\nin qualify.py"]

  shipped --> order --> mintOnly --> sameEval --> sameGate
```

Caption: An HTF tag is one more AND atom at mint time. Sharpe / trades /
beat-B&H / beat-`sma_stack` / fail-once / `QUAL_N_WINDOWS` stay as they
are. Still mint → farm → gate.

---

## What this file is not

- Not a request to soften OOS thresholds.
- Not authorization to trade real funds. `GRADUATED_PAPER` is graduated
  paper only.
- Not a second copy of the jensa install steps — use
  [WINDOWS_DISCOVERY.md](WINDOWS_DISCOVERY.md).
