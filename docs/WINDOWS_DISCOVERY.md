# Windows discovery farm (Alexander / `jensa`)

End-to-end mint → farm → gate → live map (Mermaid):
[WORKFLOW.md](WORKFLOW.md).

Paper only. The k8s cycle sidecar (1 CPU / 1.5GiB) no longer runs
walk-forwards. This PC evaluates never-tested names with the **same**
fail-once / auto-refill / aggregate-OOS / `rm_v1` / 5m rules as
`scripts/tournament_engine.py` and POSTs results to prod.
A single empty/neg window is not a veto (beat-B&H and Sharpe ≥ 0.30 stay).
Walk-forward is **8 × ~90 calendar days** of native 5m (~720 days, not
~270). Thresholds are unchanged. Longer tape makes each eval slower on
this PC — that is expected. The worker caches ATR / SMA / EMA / HTF
close series across names on the same window slices (cleared when a new
batch of slices is built). That is a per-eval speedup only — do not skip
bars or shrink windows. Do **not** turn discovery back on in the
k8s cycle sidecar to "make up time."

Machine: i5-6600K / 16GB / GTX 1070. GPU is unused (no CUDA rewrite).
Use 2 workers (safe) or 4 (all cores). Do not keep a kubectl tunnel.

## 1. Disable cluster discovery

Already the default after this change. Confirm the cycle container has:

```
DISCOVERY_ON_CYCLE=0
PAPER_DISCOVERY_MODE=off
```

(`k8s/backend.yaml` sets both.) Live path stays `run_isolated` → collect →
report. To turn tournament back on inside the sidecar (emergency only):
`DISCOVERY_ON_CYCLE=1`.

Create the ingest secret once (on a machine that already has cluster creds;
not needed on this PC afterwards):

```
kubectl -n trading create secret generic paper-discovery-ingest --from-literal=token='long-random-paper-only-token'
```

Roll the backend deploy so the **web** container sees `PAPER_DISCOVERY_INGEST_TOKEN`.
Keep the token off git, Slack, and screenshots.

## 2. Install on the Windows PC

**Preferred — Docker Desktop** (Linux container, same image as prod):

1. Install Docker Desktop and clone this repo.
2. Fetch 5m history into a local folder (PowerShell):

```
cd path\to\paper-trading-bot
$env:PAPER_STATE = "$PWD\state"
python -m pip install -e .
python scripts/fetch_history.py
```

`crypto_history_5m.json` must exist under `state\`. Fetch talks to public
Binance; it does not need the cluster. Default symbols are **BTC/USDT and
ETH/USDT only** (no SOL/XRP — discovery/qual tape is BTC+ETH). Override
with `HIST_SYMBOLS` (comma-separated ccxt symbols) only if you need extra
pairs. Default bars track
`QUAL_WINDOW_BARS * QUAL_N_WINDOWS + slack` (~210k five-minute bars for
8 × 90d). The page cap is 2500 so a multi-year fetch can finish. Re-run
until the file covers ~720 calendar days (or the deep ~5y target), then
every few days so the tape stays current. The `/tmp` mirror is skipped
on Windows.

3. `.env` next to `docker-compose.discovery.yml` (do not commit):

```
PAPER_DISCOVERY_INGEST_TOKEN=the-same-token-as-the-k8s-secret
PAPER_DISCOVERY_INGEST_URL=https://trading.runevibe.se/api/discovery/ingest
PAPER_STATE_DIR=./state
DISCOVERY_WORKERS=2
DISCOVERY_STRUCTURE_LOOKBACK_MAX=96
DISCOVERY_EVAL_TIMEOUT_SECONDS=600
```

`DISCOVERY_STRUCTURE_LOOKBACK_MAX` (default **96**) is the **primary** farm
ops guard: before walk-forward, the worker parses `don_hi` / `don_lo` /
`near_swing_*` / `dbl_bot_*` lookbacks. If any exceeds the cap it immediately
POSTs `qualified=false` with `lookback_too_expensive N>96`, fail-once parks
the name, clears that name from in-flight, and moves on. Leftover
lookback-168 structure names are pathological O(n·k) and burned this PC
for ~2h with no completed evals. This is **ops/throughput, not a gate
softening** — OOS thresholds stay 30 trades / Sharpe ≥ 0.30 / beat B&H /
beat `sma_stack`. Set to `0` to disable. Mint no longer emits structure
`N>96`; leftover already-tested lookback-168/192 names stay parked.
Worker fail-park remains the backstop for any leftover already-queued
name so dry refill can continue.

`DISCOVERY_EVAL_TIMEOUT_SECONDS` (default **600**) is a **coarse backstop
only**. If a cheap-enough name still hangs, the worker fail-parks
`eval_timeout after 600s`, recycles the multiprocessing pool, and
continues. Set to `0` to disable.

4. Start the farm:

```
docker compose -f docker-compose.discovery.yml up --build -d
```

**Alternative — native Python** (if Docker is not available):

```
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -e .
$env:PAPER_STATE = "$PWD\state"
$env:PAPER_DISCOVERY_INGEST_TOKEN = "the-same-token"
$env:PAPER_DISCOVERY_INGEST_URL = "https://trading.runevibe.se/api/discovery/ingest"
python scripts/fetch_history.py
python scripts/discovery_worker.py --workers 2
```

`--once` runs one batch and exits (smoke test). `--no-ingest` evaluates
locally only.

## 3. How results reach prod

No PVC copy and no `kubectl port-forward`.

1. Worker GETs `/api/discovery`, `/api/champions`, `/api/graduated`,
   `/api/discovery/summary` so fail-once and the champion set match prod.
2. Evaluates a small batch (default = `--workers`) against local
   `crypto_history_5m.json`.
3. POSTs each finished evaluation to
   `https://trading.runevibe.se/api/discovery/ingest` with
   `X-Discovery-Token`. Nginx already proxies `/api/`.
4. Prod appends `discovery_log.json` and, if the name **qualified**, admits
   it to `champions.json` the same way tournament does. Existing champions
   are never removed. Same-token optional `force_admit` (e.g. `["dbl_bot_120"]`)
   seats an existing log row as a paper champion even when beat-B&H still fails.

Dashboard: [https://trading.runevibe.se](https://trading.runevibe.se) —
Discovery buckets update from the ingested log. A quiet leftover drain
means this PC is off or stuck, not that the cycle sidecar should start
walk-forwards again.

## 4. Pause for gaming (leave the worker running)

Do **not** kill `discovery_worker.py` when you want the CPU/RAM back.
Killing the parent has left orphaned multiprocessing children before.

1. Leave `python scripts/discovery_worker.py --workers 2` running.
2. Open [https://trading.runevibe.se/discovery](https://trading.runevibe.se/discovery).
3. Paste the paper ingest token (same `PAPER_DISCOVERY_INGEST_TOKEN` as
   jensa `.env` / the k8s secret). The UI keeps it in `sessionStorage`
   for this browser tab only. Then click **Stop discovery**. The worker
   polls prod, clears in-flight, and sleeps 10–30s at a time with
   near-zero CPU.
4. **Start discovery** when you are done gaming. The same process
   resumes the next batch. Start cannot relaunch a dead process.

### Authentication (required)

Start / Stop is **not** an open toggle. Anyone who can hit
`trading.runevibe.se` must **not** be able to pause the farm.

`POST /api/discovery/farm` reuses `PAPER_DISCOVERY_INGEST_TOKEN` (already
on the k8s web container and in jensa `.env`). Send it as
`Authorization: Bearer …`, `X-Discovery-Token`, or
`X-Paper-Discovery-Token` — same pattern as `/api/discovery/ingest`.
No token or a wrong token → **401**. Token unset on the server → **503**
(fail-closed).

The frontend does **not** bake the secret into the public JS bundle.
Paste it once on `/discovery`; closing the tab clears `sessionStorage`.
**Never commit the token.** Keep it off git, Slack, and screenshots.

`GET /api/discovery/summary` farm status (Running / Paused / last
heartbeat) stays public. Only the mutate is secret.

If the UI says **Worker not seen**, the process on jensa actually died.
Relaunch it in PowerShell (same env as §2):

```
$env:PAPER_STATE = "$PWD\state"
$env:PAPER_DISCOVERY_INGEST_TOKEN = "the-same-token"
$env:PAPER_DISCOVERY_INGEST_URL = "https://trading.runevibe.se/api/discovery/ingest"
python scripts/discovery_worker.py --workers 2
```

Docker alternative: `docker compose -f docker-compose.discovery.yml up -d`
(only if you already use Compose — native Python is enough).

A batch already running when you click Stop may finish first. After that
the farm stays idle until Start.

## 5. What this worker must not do

- No real-money broker.
- No retest of a name that already has a discovery_log row (fail-once).
- No WaveTrend clones, MFI, named candlesticks, or chart-pattern zoo.
  Auto-refill already walks the expanded Donchian / swing / near-level
  recipe (lookbacks through 96, leftover TREND / ema_stack ANDs, wider
  dip/mom bases and continuation ANDs, plus 1% grind bases and full
  short-MA 3-atoms, plus causal HTF buyer-regime ANDs —
  densified `h1_ema_abv_15` / `h1_ema_abv_18` / `h1_ema_abv_20` /
  `h1_ema_abv_24` / `h1_ema_abv_30` / `h1_ema_abv_36` /
  `h1_sma_abv_20` / `h1_sma_abv_24` / `h1_sma_abv_30` /
  `h4_ema_abv_12` / `h4_ema_abv_20` / `h4_ema_abv_24` /
  `h4_ema_abv_30` / `h4_ema_abv_36` / `h4_ema_abv_48` /
  `h4_sma_abv_20` / `h4_sma_abv_24` / `h4_sma_abv_30` / `h4_sma_abv_50`,
  plus `h1_ema_abv_{12,40,50}` / `h1_sma_abv_{15,36,40}`,
  then unused `h1_ema_abv_{60,70}` / `h1_sma_abv_{12,50,60}` /
  `h4_ema_abv_{15,40,60}` / `h4_sma_abv_{12,15,36,40}`,
  **fresh** winner-shaped 3–5 first (`sma_abv_30` / `ema_abv_30` /
  `rsi_14_>55`, not depth-7 `near_swing` spam),
  then drained `regime&mom&mild_dip` 3-atoms (not structure),
  then 4–7 atom admit-island stacks (depth 4–5 first),
  mom-before-dip, `REGIME_DIP_PRIORITY`
  (`dip_24b_lt5pc` / `dip_24b_lt6pc` / `dip_18b_lt2pc`),
  plus `MOM_FILTERS_HTF_DENSE` / `MOM_FILTERS_HTF_EXPAND` /
  `MOM_FILTERS_HTF_FRESH`).
- No culling champions.
- Do not point `PAPER_STATE` at the cluster PVC.
- Do not raise `DISCOVERY_STRUCTURE_LOOKBACK_MAX` / disable the timeout
  to "give 168 another shot" — that is how this PC hung. Mint no longer
  emits structure `N>96`; leftover already-tested names stay parked.
  Worker fail-park remains the backstop.
