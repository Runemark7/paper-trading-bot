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
| GET    | `/api/discovery/summary` | Last-known unique-tested / in-flight (2 names / ~120s cycle slice) / leftover-untested; rejected parked forever; newest last_tested_at; stuck/stale copy |
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

Everything except `POST /run` is read-only. `POST /run` runs the same paper
trading cycle (no real orders) and regenerates the dashboard.

## Notes

- No authentication. Only expose over the network you trust, or put it behind
  a VPN / auth proxy. All data is paper-trading state.
- The cron job (`paperbot-live-cycle`) keeps `state/` fresh; the service reads
  the current files on each request, so it always reflects the latest cycle.
