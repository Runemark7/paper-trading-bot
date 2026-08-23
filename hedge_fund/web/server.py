"""PaperBot web service — serve the dashboard + JSON status over HTTP.

A small, dependency-free HTTP server (stdlib only) that exposes:

  GET /                 -> dashboard HTML (state/report.html)
  GET /api/summary      -> JSON: equity, closed trades, win rate, P&L, Brier
  GET /api/learning     -> JSON: per-condition learning state (skills acquired)
  GET /api/regime       -> JSON: current regime zone/score
  GET /api/trades       -> JSON: recent closed trades
  POST /run             -> trigger a live decision cycle, then regenerate
  GET /health           -> liveness probe

Purpose: a durable little service you can port-forward to and open the
dashboard (and its data) from any device. Everything is read-only except
POST /run, which is a manual trigger of the same paper-trading cycle.

Run:
    .venv/bin/python -m hedge_fund.web.server [--port 8787] [--host 0.0.0.0]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hedge_fund.regime.gate import RegimeGate
from hedge_fund.trading.store import TradeStore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_DIR = REPO_ROOT / "state"
DASHBOARD_HTML = STATE_DIR / "report.html"
TRADES_DB = STATE_DIR / "trades.sqlite"
CALIB_JSON = STATE_DIR / "calibration.json"

RUN_LOCK = threading.Lock()
CORE_DB = str(TRADES_DB)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def build_summary() -> dict:
    st = TradeStore(CORE_DB)
    stats = st.stats()
    return {
        "equity": st.equity_history()[-1]["equity"] if st.equity_history() else None,
        "closed_trades": stats["closed"] or 0,
        "win_rate": round((stats["hits"] / stats["closed"]), 3) if stats["closed"] else None,
        "total_pnl": stats["total_pnl"],
        "updated": st.equity_history()[-1]["ts"] if st.equity_history() else None,
    }


def build_learning() -> dict:
    calib = _read_json(CALIB_JSON)
    if not calib:
        return {}
    out = {}
    for key, v in calib.items():
        sym, tf, cond = key.split("|")
        trials = int(v["alpha"] + v["beta"] - 2)
        mean = v["alpha"] / (v["alpha"] + v["beta"])
        if trials < 5:
            level = "learning"
        elif trials < 20:
            level = "developing"
        elif trials < 50:
            level = "trained"
        else:
            level = "established"
        out[key] = {"symbol": sym, "timeframe": tf, "condition": cond,
                    "trials": trials, "calibrated_prob": round(mean, 3), "level": level}
    return out


def build_regime() -> dict:
    gate = RegimeGate(state_dir=STATE_DIR, cache_hours=24)
    try:
        return {"zone": gate.zone(), "score": gate.score(), "allowed": gate.allowed_to_trade()}
    except Exception as exc:
        return {"error": str(exc)}


def trigger_run() -> dict:
    """Run one live cycle via the existing entrypoint; regenerate dashboard."""
    with RUN_LOCK:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "hedge_fund.trading.run", "--cycles", "1"],
                capture_output=True, text=True, timeout=300, cwd=str(REPO_ROOT),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timed out"}
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "output": proc.stdout[-1500:] + proc.stderr[-500:],
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quieter

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, path, code=200):
        try:
            data = Path(path).read_bytes()
        except OSError:
            self.send_error(404, "not found")
            return
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = self.path.split("?")[0].rstrip("/")
        if not route:
            route = "/"
        if route == "/health":
            self._send_json({"ok": True, "ts": time.time()})
        elif route == "/dashboard" or route == "/":
            self._send_html(DASHBOARD_HTML)
        elif route == "/api/summary":
            self._send_json(build_summary())
        elif route == "/api/learning":
            self._send_json(build_learning())
        elif route == "/api/regime":
            self._send_json(build_regime())
        elif route == "/api/trades":
            st = TradeStore(CORE_DB)
            self._send_json([dict(t) for t in st.trades(closed_only=True)[-50:]])
        else:
            self._send_json({"error": "unknown route"}, 404)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        route = self.path.split("?")[0].rstrip("/")
        if route == "/run":
            self._send_json(trigger_run())
        else:
            self._send_json({"error": "unknown route"}, 404)


CORE_DB = str(CORE_DB) if (CORE_DB := TRADES_DB) else TRADES_DB  # resolve for module use


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"paperbot dashboard service on http://{args.host}:{args.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()