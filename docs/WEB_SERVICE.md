# PaperBot Web Service

A small, dependency-free HTTP service (Python stdlib only) exposing the
dashboard and its data so you can port-forward and view it from any device.

## Start

```bash
# from repo root, with the venv
.venv/bin/python -m hedge_fund.web.server --port 8787
```

Binds to `0.0.0.0:8787` by default so it's reachable over the LAN / port
forward. To restrict, pass `--host 127.0.0.1`.

## Routes

| Method | Route          | Description                                        |
|--------|----------------|----------------------------------------------------|
| GET    | `/`            | Dashboard HTML (equity vs baseline, trade log, calibration, learning) |
| GET    | `/api/summary` | Equity, closed trades, win rate, total P&L, last update |
| GET    | `/api/learning`| Per-condition learning state: trials, calibrated probability, level |
| GET    | `/api/regime`  | Current crypto regime zone / score / allowed flag  |
| GET    | `/api/discovery/summary` | Last-known unique-tested / in-flight (1 name / ~90s cycle slice if `DISCOVERY_ON_CYCLE=1`; otherwise Windows worker) / leftover-untested; farm Start/Stop status + heartbeat; rejected parked forever; empty eligible auto-refills `discovery_extended.json`; newest last_tested_at; stuck/stale copy |
| GET    | `/api/discovery` | Raw `discovery_log.json` (newest-first) |
| POST   | `/api/discovery/ingest` | Windows worker: append evals + admit qualified names. Requires `PAPER_DISCOVERY_INGEST_TOKEN` (`X-Discovery-Token` or `Authorization: Bearer`). Fail-closed if unset. Fail-once. Does not cull champions. Optional `heartbeat` updates farm liveness. |
| POST   | `/api/discovery/farm` | Start (`enabled=true`) / Stop (`enabled=false`) the Windows farm. **Authenticated** — same `PAPER_DISCOVERY_INGEST_TOKEN` as ingest (`Authorization: Bearer`, `X-Discovery-Token`, or `X-Paper-Discovery-Token`). No/wrong token → 401. Unset token → 503. Not an open toggle. Durable `state/discovery_farm.json` flag — does not kill the worker. |
| GET    | `/api/trades`  | Recent closed trades                              |
| POST   | `/run`         | Trigger one live paper cycle, then regenerate      |
| GET    | `/health`      | Liveness probe                                     |

## Port-forwarding

Forward whichever public port to this machine's port `8787`. Example:

```bash
# on a device on your network: open <this-machine-ip>:8787
# or via a reverse tunnel (e.g. cloudflared, ngrok):
cloudflared tunnel --url http://127.0.0.1:8787
```

Everything except `POST /run`, `POST /api/discovery/ingest`, and
`POST /api/discovery/farm` is read-only.
`POST /run` is **not** proxied on the public host (`trading.runevibe.se`).
`POST /api/discovery/ingest` and `POST /api/discovery/farm` are proxied
under `/api/` and require the paper-only shared secret. A visitor who
can load `trading.runevibe.se` cannot pause the farm without that token.
Ingest appends evaluations and may admit new champions; farm only flips
the pause flag. Neither culls existing champions. GET
`/api/discovery/summary` farm status stays public.

## Notes

- GET routes have no authentication. Only expose over the network you trust,
  or put it behind a VPN / auth proxy. All data is paper-trading state.
- Ingest and farm Start/Stop are fail-closed: if
  `PAPER_DISCOVERY_INGEST_TOKEN` is unset, those POSTs return 503. Wrong
  or missing token → 401. Set the k8s secret `paper-discovery-ingest` on
  the web container. Never put the token in the frontend build. See
  [WINDOWS_DISCOVERY.md](WINDOWS_DISCOVERY.md).
- Cluster `live_cycle` skips tournament by default (`DISCOVERY_ON_CYCLE=0`).
  The cycle sidecar keeps `state/` fresh for live paper trading; the
  Windows worker keeps `discovery_log.json` / new admits fresh.
