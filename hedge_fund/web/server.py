"""PaperBot web service — serve the dashboard + JSON status over HTTP.

A small, dependency-free HTTP server (stdlib only) that exposes:

  GET /                 -> dashboard HTML (state/report.html)
  GET /api/summary      -> JSON: equity, closed trades, win rate, P&L, Brier
  GET /api/learning     -> JSON: per-condition learning state (skills acquired)
  GET /api/regime       -> JSON: current regime zone/score
  GET /api/trades       -> JSON: recent closed trades
  GET /api/status       -> running-now vs in-progress (last-known stamps)
  GET /api/discovery/summary -> tested / in-flight / leftover-untested; farm Start/Stop status
                                (?compact=1 omits those lists; counts/farm/stuck stay)
  POST /api/discovery/ingest -> Windows worker: append evals + admit / force_admit (shared secret)
  POST /api/discovery/farm -> Start/Stop Windows farm (same ingest token)
  POST /api/champions/retain -> keep-list filter of champions.json (same ingest token)
  POST /api/champions/cull_undated -> drop missing champion_since (same ingest token)
  POST /run             -> trigger a live decision cycle, then regenerate
  GET /health           -> liveness probe

Purpose: a durable little service you can port-forward to and open the
dashboard (and its data) from any device. Everything is read-only except
POST /run, the token-gated discovery ingest / farm Start/Stop routes, and
the token-gated champion retain / cull_undated routes.

Run:
    .venv/bin/python -m hedge_fund.web.server [--port 8787] [--host 0.0.0.0]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from hedge_fund.paths import state_root
from hedge_fund.regime.gate import RegimeGate
from hedge_fund.trading.discovery_mode import ingest_token, tokens_match
from hedge_fund.trading.open_lots import open_lots_snapshot, paper_book_dbs
from hedge_fund.trading.store import TradeStore
from hedge_fund.web.live import live_preview, live_prices

_INGEST_MAX_BYTES = 1_000_000

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_LOCK = threading.Lock()


def _state_dir() -> Path:
    return state_root()


def _dashboard_html() -> Path:
    return _state_dir() / "report.html"


def _trades_db() -> Path:
    return _state_dir() / "trades.sqlite"


def _calib_json() -> Path:
    return _state_dir() / "calibration.json"


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def per_strategy_dbs() -> list[Path]:
    """All isolated per-strategy account DBs in STATE_ROOT."""
    return [Path(p) for p in paper_book_dbs() if Path(p).name.startswith("trades_")]


def store_dbs() -> list[str]:
    """The DBs to read for aggregate stats/trades: same set as /api/live."""
    return paper_book_dbs() or [str(_trades_db())]


def build_summary() -> dict:
    dbs = store_dbs()
    total_closed = 0
    total_hits = 0
    total_pnl = 0.0
    last_equity = None
    last_ts = None
    for db in dbs:
        try:
            st = TradeStore(db)
            stats = st.stats()
            total_closed += stats["closed"] or 0
            total_hits += stats["hits"] or 0
            eh = st.equity_history()
            if eh:
                last_equity = (last_equity or 0.0) + eh[-1]["equity"]
                last_ts = eh[-1]["ts"] or last_ts
            if stats["total_pnl"]:
                total_pnl += stats["total_pnl"]
        except Exception:
            continue
    return {
        "equity": last_equity,
        "closed_trades": total_closed,
        "win_rate": round((total_hits / total_closed), 3) if total_closed else None,
        "total_pnl": total_pnl if total_closed else None,
        "updated": last_ts,
        "source": "per-strategy paper accounts" if per_strategy_dbs() else "legacy trades.sqlite",
        "account_count": len(dbs),
    }


def parse_route(path: str) -> tuple[str, dict[str, str]]:
    """Split request path into route + last-wins query values."""
    parsed = urlparse(path)
    route = parsed.path.rstrip("/") or "/"
    qs = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    return route, qs


def build_trades(symbol: str | None = None, limit: int = 60) -> list[dict]:
    """Closed paper trades across the same DBs as /api/live.

    Optional ``symbol`` (BTC/USDT or ETH/USDT) filters before the tail slice.
    ``lot_id`` is included so pyramids stay individually identifiable.
    """
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 60
    n = max(1, min(n, 500))
    want = None
    if symbol:
        from hedge_fund.web.candles import normalize_symbol

        want = normalize_symbol(symbol)
        if want is None:
            return []
    rows: list[dict] = []
    for db in store_dbs():
        try:
            st = TradeStore(db)
            for t in st.trades(closed_only=True):
                d = dict(t)
                if want and d.get("symbol") != want:
                    continue
                d["account"] = Path(db).stem
                rows.append(d)
        except Exception:
            continue
    rows.sort(key=lambda r: (r.get("exit_ts") or r.get("entry_ts") or "", r.get("id") or 0))
    return rows[-n:]


def build_learning() -> dict:
    out = {}
    calib_files = sorted(_state_dir().glob("calibration_*.json"))
    for f in calib_files:
        calib = _read_json(f)
        if not calib:
            continue
        for key, v in calib.items():
            parts = key.split("|")
            if len(parts) < 3:
                continue
            sym, tf, cond = parts[0], parts[1], "|".join(parts[2:])
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
            out[f"{sym}|{tf}|{cond}"] = {"symbol": sym, "timeframe": tf, "condition": cond,
                                          "trials": trials, "calibrated_prob": round(mean, 3),
                                          "level": level, "account": f.stem}
    return out


def build_regime() -> dict:
    gate = RegimeGate(state_dir=_state_dir(), cache_hours=24)
    try:
        return {"zone": gate.zone(), "score": gate.score(), "allowed": gate.allowed_to_trade()}
    except Exception as exc:
        return {"error": str(exc)}


def trigger_run() -> dict:
    """Run one live cycle via the existing entrypoint; regenerate dashboard."""
    with RUN_LOCK:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "hedge_fund.trading.run_isolated", "--cycles", "1"],
                capture_output=True, text=True, timeout=300, cwd=str(REPO_ROOT),
                env={**os.environ, "PAPER_STATE": str(_state_dir())},
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timed out"}
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "output": proc.stdout[-1500:] + proc.stderr[-500:],
        }


_LIVE_LOCK = threading.Lock()


def live_report(fresh_prices: bool = True) -> None:
    """Regenerate the dashboard HTML with live prices/P&L injected.

    Called on each GET / so the dashboard reflects current market value,
    not just the last cron snapshot. Position sizing/decisions are untouched —
    this only re-prices the open positions live for display.
    """
    from hedge_fund.dashboard.report import generate_dashboard

    with _LIVE_LOCK:
        try:
            live = live_preview(str(_trades_db()))
            st = TradeStore(str(_trades_db()))
            generate_dashboard(st, str(_dashboard_html()),
                               calib_path=str(_calib_json()), live=live)
        except Exception as exc:  # noqa: BLE001
            print(f"[live_report] failed: {exc}")


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
        route, qs = parse_route(self.path)
        if route in ("/health", "/healthz"):
            self._send_json({"ok": True, "ts": time.time()})
        elif route == "/dashboard" or route == "/":
            live_report()  # re-price open positions live before serving
            self._send_html(_dashboard_html())
        elif route == "/api/summary":
            live_report()  # ensure fresh
            self._send_json(build_summary())
        elif route == "/api/learning":
            self._send_json(build_learning())
        elif route == "/api/regime":
            self._send_json(build_regime())
        elif route == "/api/live":
            # aggregate live preview across all per-strategy accounts
            try:
                from hedge_fund.web.live import live_preview
                from hedge_fund.web.lot_health import fetch_signal_closes

                dbs = store_dbs()
                lots = open_lots_snapshot()
                try:
                    closes = fetch_signal_closes()
                except Exception:
                    # Lots still render; signal stays unknown. Never stall → 502.
                    closes = {}
                merged = {
                    "live_equity": 0.0,
                    "cash": 0.0,
                    "positions": [],
                    "lots": [],
                    "accounts": len(dbs),
                    "as_of": None,
                    "open_lots": lots["open_lots"],
                    "open_lots_by_account": lots["by_account"],
                    "open_lots_unit": "open_lots",
                }
                for db in dbs:
                    try:
                        lp = live_preview(db, closes_by_symbol=closes)
                        merged["live_equity"] += lp.get("live_equity", 0.0) or 0.0
                        merged["cash"] += lp.get("cash", 0.0) or 0.0
                        merged["as_of"] = lp.get("as_of") or merged["as_of"]
                        acct = Path(db).stem
                        for p in lp.get("positions", []):
                            p["account"] = acct
                            for lot in p.get("lots") or []:
                                lot["account"] = acct
                                merged["lots"].append(lot)
                            merged["positions"].append(p)
                    except Exception:
                        continue
                self._send_json(merged)
            except Exception as exc:
                # JSON 503 — never a bare nginx 502.
                self._send_json(
                    {
                        "error": str(exc),
                        "lots": [],
                        "positions": [],
                        "open_lots": 0,
                        "paper_only": True,
                    },
                    503,
                )
        elif route == "/api/trades":
            try:
                lim = int(qs["limit"]) if qs.get("limit") else 60
            except (TypeError, ValueError):
                lim = 60
            self._send_json(build_trades(symbol=qs.get("symbol"), limit=lim))
        elif route == "/api/candles":
            try:
                from hedge_fund.web.candles import (
                    CandleFetchError,
                    CandleRequestError,
                    candles_payload,
                )

                self._send_json(
                    candles_payload(
                        qs.get("symbol"),
                        qs.get("timeframe") or "5m",
                        qs.get("limit"),
                        since=qs.get("since"),
                    )
                )
            except CandleRequestError as exc:
                self._send_json({"error": str(exc)}, 400)
            except CandleFetchError as exc:
                self._send_json({"error": str(exc), "paper_only": True}, exc.status)
            except Exception as exc:
                # Never a bare nginx 502 — JSON 503 so the chart can show why.
                self._send_json({"error": str(exc), "paper_only": True}, 503)
        elif route == "/api/champions":
            # Read-only: last-known champions.json + open-lots snapshot.
            # collect_live_results mutates the pool / may graduate — that stays
            # on live_cycle.py (and heartbeat after closes), not a browser GET.
            try:
                from hedge_fund.trading.champions import pool_status

                self._send_json(pool_status(read_only=True))
            except Exception as exc:
                self._send_json({"error": str(exc), "champions": [], "count": 0}, 500)
        elif route == "/api/graduated":
            try:
                from hedge_fund.trading.champions import load_graduated
                self._send_json(load_graduated())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/discovery/summary":
            try:
                from hedge_fund.web.discovery import build_discovery_summary, compact_query

                self._send_json(build_discovery_summary(lists=not compact_query(qs.get("compact"))))
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/discovery":
            try:
                log_path = _state_dir() / "discovery_log.json"
                if log_path.exists():
                    self._send_json(json.loads(log_path.read_text()))
                else:
                    self._send_json([])
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/status":
            try:
                from hedge_fund.web.status import build_status
                self._send_json(build_status())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        else:
            self._send_json({"error": "unknown route"}, 404)

    def do_HEAD(self):
        self.do_GET()

    def _read_json_body(self, max_bytes: int = _INGEST_MAX_BYTES):
        raw_len = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw_len)
        except ValueError:
            return None, "invalid Content-Length"
        if length < 0 or length > max_bytes:
            return None, "payload too large"
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            return None, "invalid json"
        if not isinstance(data, dict):
            return None, "payload must be an object"
        return data, None

    def _discovery_header_tokens(self) -> list[str]:
        """Same secrets as ingest: Bearer, X-Discovery-Token, X-Paper-Discovery-Token."""
        found: list[str] = []
        for key in ("X-Discovery-Token", "X-Paper-Discovery-Token"):
            val = (self.headers.get(key) or "").strip()
            if val:
                found.append(val)
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            val = auth[7:].strip()
            if val:
                found.append(val)
        return found

    def _discovery_ingest_authorized(self) -> tuple[bool, str | None, int]:
        expected = ingest_token()
        if not expected:
            return False, "ingest disabled — PAPER_DISCOVERY_INGEST_TOKEN is not set", 503
        if not any(tokens_match(got, expected) for got in self._discovery_header_tokens()):
            return False, "unauthorized", 401
        return True, None, 200

    def do_POST(self):
        route, _qs = parse_route(self.path)
        if route == "/run":
            self._send_json(trigger_run())
        elif route == "/api/discovery/ingest":
            ok, err, code = self._discovery_ingest_authorized()
            if not ok:
                self._send_json({"ok": False, "error": err, "paper_only": True}, code)
                return
            payload, err = self._read_json_body()
            if err:
                self._send_json({"ok": False, "error": err, "paper_only": True}, 400)
                return
            try:
                from hedge_fund.trading.ingest import ingest_discovery_payload

                self._send_json(ingest_discovery_payload(payload))
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc), "paper_only": True}, 400)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc), "paper_only": True}, 500)
        elif route == "/api/discovery/farm":
            ok, err, code = self._discovery_ingest_authorized()
            if not ok:
                self._send_json({"ok": False, "error": err, "paper_only": True}, code)
                return
            payload, err = self._read_json_body()
            if err:
                self._send_json({"ok": False, "error": err, "paper_only": True}, 400)
                return
            if "enabled" not in payload:
                self._send_json(
                    {"ok": False, "error": "enabled (bool) is required", "paper_only": True},
                    400,
                )
                return
            try:
                from hedge_fund.trading.farm import set_farm_enabled

                farm = set_farm_enabled(bool(payload.get("enabled")))
                self._send_json({"ok": True, "paper_only": True, "farm": farm})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc), "paper_only": True}, 500)
        elif route in ("/api/champions/retain", "/api/champions/cull_undated"):
            ok, err, code = self._discovery_ingest_authorized()
            if not ok:
                self._send_json({"ok": False, "error": err, "paper_only": True}, code)
                return
            payload, err = self._read_json_body()
            if err:
                self._send_json({"ok": False, "error": err, "paper_only": True}, 400)
                return
            try:
                from hedge_fund.trading.champions import (
                    cull_undated_champions,
                    retain_champions,
                )

                if route == "/api/champions/retain":
                    keep = payload.get("keep")
                    if not isinstance(keep, list):
                        self._send_json(
                            {
                                "ok": False,
                                "error": "keep must be a list of names",
                                "paper_only": True,
                            },
                            400,
                        )
                        return
                    names = {
                        n.strip() for n in keep if isinstance(n, str) and n.strip()
                    }
                    self._send_json(retain_champions(names))
                else:
                    self._send_json(cull_undated_champions())
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc), "paper_only": True}, 500)
        else:
            self._send_json({"error": "unknown route"}, 404)


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