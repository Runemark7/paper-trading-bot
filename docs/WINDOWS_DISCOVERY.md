# Windows discovery farm (Alexander / `jensa`)

Paper only. The k8s cycle sidecar (1 CPU / 1.5GiB) no longer runs
walk-forwards. This PC evaluates never-tested names with the **same**
fail-once / auto-refill / OOS / `rm_v1` / 5m rules as
`scripts/tournament_engine.py` and POSTs results to prod.

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
Binance; it does not need the cluster. Re-run every few days so the tape
stays current. The `/tmp` mirror is skipped on Windows.

3. `.env` next to `docker-compose.discovery.yml` (do not commit):

```
PAPER_DISCOVERY_INGEST_TOKEN=the-same-token-as-the-k8s-secret
PAPER_DISCOVERY_INGEST_URL=https://trading.runevibe.se/api/discovery/ingest
PAPER_STATE_DIR=./state
DISCOVERY_WORKERS=2
```

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
   are never removed.

Dashboard: [https://trading.runevibe.se](https://trading.runevibe.se) —
Discovery buckets update from the ingested log. A quiet leftover drain
means this PC is off or stuck, not that the cycle sidecar should start
walk-forwards again.

## 4. What this worker must not do

- No real-money broker.
- No retest of a name that already has a discovery_log row (fail-once).
- No WaveTrend clones, MFI, named candlesticks, or chart-pattern zoo.
  Auto-refill already walks the expanded Donchian / swing / near-level recipe.
- No culling champions.
- Do not point `PAPER_STATE` at the cluster PVC.
