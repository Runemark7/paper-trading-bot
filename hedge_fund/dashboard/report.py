"""Static HTML dashboard generator.

Reads the SQLite store and emits a self-contained `report.html` with:
  - Paper equity curve vs buy-and-hold overlay (equal-weight, paper fees
    on the B&H entry; PROTOCOL amendment 2026-09-01).
  - Trade log table: entry/exit, size, stop, stated probability vs outcome.
  - Calibration view: stated vs measured reliability per condition + Brier.
  - Strategy rules imported from TradingLoop / RiskManager / PaperBroker.

No server, no login — just open the HTML. Uses chart.js from a CDN for the
curve (degrades gracefully to a table if offline).
"""

from __future__ import annotations

import html
import json
from collections import defaultdict

from hedge_fund.brokers.paper import SLIPPAGE, TAKER_FEE
from hedge_fund.calibration import compute_calibration
from hedge_fund.risk.managed import (
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    MAX_DRAWDOWN,
    MAX_OPEN_RISK_FRAC,
    RISK_FRAC,
)
from hedge_fund.trading.constants import (
    CYCLE_INTERVAL_SECONDS,
    GRADUATED_PAPER,
    MIN_BACKTEST_SHARPE,
    MIN_BACKTEST_TRADES,
    QUAL_TIMEFRAME,
    RISK_POLICY,
    TRADE_EVALUATION_LIMIT,
)
from hedge_fund.trading.loop import (
    ATR_PERIOD,
    ATR_STOP_MULT,
    CONFIDENCE_REF_PROB,
    MAX_LOTS_PER_SYMBOL,
    STOP_CAP_FRAC,
    STOP_FALLBACK_FRAC,
    STOP_FLOOR_FRAC,
    TAKE_PROFIT_RR,
)
from hedge_fund.trading.store import TradeStore


def _fmt(x, d=2):
    return f"{x:,.{d}f}" if x is not None else "—"


def _pct(x, d=2):
    return f"{x*100:,.{d}f}%" if x is not None else "—"


def build_calibration_records(trades) -> list[dict]:
    """Flatten closed trades into {prob, hit} rows for calibration metrics."""
    records = []
    for t in trades:
        if t["hit"] is None or t["stated_prob"] is None:
            continue
        records.append({"prob": t["stated_prob"], "hit": int(t["hit"])})
    return records


def learning_state_rows(calib: "CalibrationStore") -> list[str]:
    """Build the 'skills learned' rows: each condition's measured proficiency.

    A condition is a 'learned skill' — it accumulates sampled outcomes and its
    calibrated mean / hit-rate is the evidence of what the bot has learned.
    We surface 4 learning levels from sample count:
      < 5 trials   = "learning"  (insufficient evidence)
      5-19 trials  = "developing"
      20-49 trials = "trained"
      >= 50 trials = "established"
    """
    from hedge_fund.calibration import CalibrationStore  # local to avoid import cycle

    snap = calib.snapshot()
    if not snap:
        return ['<tr><td colspan="6" style="text-align:center">No learning state yet — the bot hasn\u2019t closed enough trades to start calibrating conditions.</td></tr>']

    rows = []
    for key, s in sorted(snap.items(), key=lambda kv: -kv[1]["trials"]):
        sym, tf, cond = key.split("|")
        trials = s["trials"]
        mean = s["mean"]
        if trials < 5:
            level, badge = "learning", "learning"
        elif trials < 20:
            level, badge = "developing", "developing"
        elif trials < 50:
            level, badge = "trained", "trained"
        else:
            level, badge = "established", "established"
        rows.append(
            f'<tr><td>{html.escape(sym)}</td>'
            f'<td>{html.escape(cond)}</td>'
            f'<td>{trials}</td>'
            f'<td>{mean:.2f}</td>'
            f'<td><span class="badge badge-{badge}">{level}</span></td></tr>'
        )
    return rows


def per_condition_table(trades) -> str:
    """Group closed trades by condition -> stated vs measured reliability."""
    groups: dict[str, dict] = defaultdict(lambda: {"n": 0, "hits": 0, "probs": []})
    for t in trades:
        if t["hit"] is None:
            continue
        c = t["condition"] or "unknown"
        groups[c]["n"] += 1
        groups[c]["hits"] += t["hit"]
        if t["stated_prob"] is not None:
            groups[c]["probs"].append(t["stated_prob"])

    rows = []
    for cond, g in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
        hit_rate = g["hits"] / g["n"] if g["n"] else 0
        avg_prob = sum(g["probs"]) / len(g["probs"]) if g["probs"] else 0
        rows.append(
            f"<tr><td>{html.escape(cond)}</td><td>{g['n']}</td>"
            f"<td>{avg_prob:.2f}</td><td>{hit_rate:.2%}</td>"
            f"<td>{abs(avg_prob - hit_rate):.2f}</td></tr>"
        )
    return "\n".join(rows)


def _backtest_table(rows) -> str:
    """Render one A/B result table (train or held-out)."""
    if not rows:
        return '<tr><td colspan="8">No results.</td></tr>'
    out = []
    for r in rows:
        if r.get("error"):
            out.append(f'<tr><td>{html.escape(r["strategy"])}</td><td colspan="7">'
                       f'ERROR: {html.escape(r["error"])}</td></tr>')
            continue
        cls = "pos" if (r.get("total_pnl") or 0) > 0 else "neg"
        out.append(
            f'<tr><td>{html.escape(r["strategy"])}</td><td>{r["trades"]}</td>'
            f'<td>{r["win_rate"]:.0%}</td>'
            f'<td class="{cls}">{_fmt(r["total_pnl"])}</td>'
            f'<td>{_fmt(r["final_equity"])}</td>'
            f'<td>{r["sharpe"]:.2f}</td><td>{r["max_drawdown"]:.0%}</td>'
            f'<td>{_fmt(r["fees_paid"])}</td></tr>'
        )
    return "\n".join(out)


def strategy_rules_section() -> str:
    """Plain-language explainer of the live paper tournament + risk rules.

    Numbers are imported from TradingLoop / RiskManager / PaperBroker so this
    blurb cannot describe a different experiment than the cycle that runs.
    """
    taker_pct = f"{TAKER_FEE * 100:.1f}%"
    slip_bps = f"{SLIPPAGE * 10_000:.0f}bps"
    return f"""
<h2>Strategies &amp; tests</h2>
<div class="sm">Live experiment (PROTOCOL amendments 2026-08-30, 2026-09-01, and 2026-09-02): an isolated-account
<b>paper</b> tournament of TA rules on BTC/USDT and ETH/USDT, <b>{QUAL_TIMEFRAME}</b> bars.
Discovery qualifies on the same {QUAL_TIMEFRAME} tape and <code>{RISK_POLICY}</code> stop/size as
<code>TradingLoop.run_cycle</code>. 4h history is not the admit bar. Not real money.</div>

<h3 style="margin:14px 0 4px">What is live</h3>
<div class="wrap"><table><thead><tr>
<th>Piece</th><th>What actually runs</th>
</tr></thead><tbody>
<tr><td>Universe</td><td>Explicit ~50-name 5m list (was 3546 combinatorial clones). Lookbacks in names are bar counts (e.g. <code>dip_24b</code> = 24×5m = 2 hours). <code>daily()</code>/<code>h1()</code>/<code>m5()</code> and MFI are not generated. Empty-pool fallback: <code>PAPER_STRATEGY=sma_stack</code>. Each discovery sweep drains remaining untested names (no 30-name sample).</td></tr>
<tr><td>Decision cycle</td><td>Every {CYCLE_INTERVAL_SECONDS}s on {QUAL_TIMEFRAME} closes, around the clock. Heartbeat stays frequent and does not open trades.</td></tr>
<tr><td>Accounts</td><td>One €10k paper book per champion (<code>run_isolated</code>). No homemade live-slot cap; universe size (~40–120) is the combinatorial bound. <code>run.py</code> uses the same cycle on a single account (champion override, else sma_stack).</td></tr>
<tr><td>Stated probability</td><td>Beta-Binomial calibration of a deterministic RSI/score heuristic — not an LLM, not a constant 0.60. Cold-start blends the proposal; after 20 trials the posterior mean dominates.</td></tr>
<tr><td>Admit bar</td><td>OOS/test only. Every window test PnL ≥ 0; ≥ {MIN_BACKTEST_TRADES} OOS trades; OOS Sharpe ≥ {MIN_BACKTEST_SHARPE:.2f}; must beat buy-and-hold and <code>sma_stack</code> after fees. Train PnL does not enter the score.</td></tr>
<tr><td>Graduation</td><td>{TRADE_EVALUATION_LIMIT} closed paper trades. Status <code>{GRADUATED_PAPER}</code> means <b>graduated paper</b> (paper P&amp;L greater than buy-and-hold of the same assets over the same period, after fees), not a real-money go-live.</td></tr>
<tr><td>Buy-and-hold</td><td>Equal-weight long of the same assets, paper taker+slippage on the B&amp;H entry, marked to market each cycle (<code>snapshot_equity</code> baseline). Graduation uses that overlay, or a round-trip reconstruction from the trade tape.</td></tr>
</tbody></table></div>

<h3 style="margin:14px 0 4px">Risk rules (every trade, from code constants)</h3>
<div class="wrap"><table><thead><tr>
<th>Rule</th><th>Value</th>
</tr></thead><tbody>
<tr><td>Position risk / trade</td><td>{RISK_FRAC:.0%} of equity × confidence [{CONFIDENCE_MIN:.1f}×, {CONFIDENCE_MAX:.1f}×] from stated p / {CONFIDENCE_REF_PROB:.2f}</td></tr>
<tr><td>Stop-loss</td><td>{ATR_STOP_MULT:.1f}× ATR({ATR_PERIOD}), floored {STOP_FLOOR_FRAC:.1%} / capped {STOP_CAP_FRAC:.1%} of entry ({STOP_FALLBACK_FRAC:.1%} fallback if ATR unavailable)</td></tr>
<tr><td>Take-profit</td><td>{TAKE_PROFIT_RR:.0f}:1 reward:risk vs stop distance (not a fixed 5%)</td></tr>
<tr><td>Pyramiding</td><td>Max {MAX_LOTS_PER_SYMBOL} lots/symbol; add only if existing lots are in profit</td></tr>
<tr><td>Signal exit</td><td>Close lots when the strategy is no longer long</td></tr>
<tr><td>Max open risk</td><td>{MAX_OPEN_RISK_FRAC:.0%} of equity</td></tr>
<tr><td>Max drawdown</td><td>{MAX_DRAWDOWN:.0%} → hard halt</td></tr>
<tr><td>Regime gate</td><td>Off on the live path (<code>regime=None</code>). <code>/api/regime</code> is display-only.</td></tr>
<tr><td>Fees + slippage</td><td>{taker_pct} taker fee + {slip_bps} slippage per fill</td></tr>
</tbody></table></div>
"""


def backtest_section() -> str:
    """Build the A/B backtest HTML section from state/abtest.json."""
    from hedge_fund.paths import state_root
    import json as _json
    p = state_root() / "abtest.json"
    if not p.exists():
        return f'<h2>Strategy A/B backtest</h2><div class="sm">No backtest results yet. Run scripts/ab_test.py to generate.</div>'
    try:
        report = _json.loads(p.read_text())
    except (OSError, ValueError):
        return f'<h2>Strategy A/B backtest</h2><div class="sm">Backtest data unreadable.</div>'

    h = ['<h2>Strategy A/B backtest</h2>',
         '<div class="sm">Candidate strategies compared on the same held-out window '
         '(train = pick winner, held-out = verify not overfit). PnL after 0.1% taker fee '
         '+ 2bps slippage, 1% risk per trade, $10k start.</div>']
    for sym, s in report.get("symbols", {}).items():
        h.append(f'<h3 style="margin:14px 0 6px">{html.escape(sym)} — '
                 f'{s["bars"]} bars (train {s["train_bars"]}, held-out {s["hold_bars"]})</h3>')
        for label, key in (("Train", "train"), ("Held-out", "held_out")):
            h.append(f'<div class="sm">{label}</div>')
            h.append('<div class="wrap"><table><thead><tr>'
                     '<th>Strategy</th><th>Trades</th><th>Win%</th><th>PnL</th>'
                     '<th>Final eq</th><th>Sharpe</th><th>Max DD</th><th>Fees</th>'
                     '</tr></thead><tbody>')
            h.append(_backtest_table(s.get(key, [])))
            h.append('</tbody></table></div>')
    return "\n".join(h)


def _live_bar(live: dict | None) -> str:
    """A small LIVE banner showing prices + as-of time when live data present."""
    if not live:
        return ""
    prices = live.get("prices", {})
    chips = "".join(
        f'<span class="chip">{html.escape(s)} <b>${_fmt(p)}</b></span>'
        for s, p in prices.items() if p
    )
    return (
        f'<span class="live-dot">●</span> <b>LIVE</b> — '
        f'{chips} <span class="sm">as of {html.escape(live.get("as_of", ""))}</span>'
    )


def generate_dashboard(store: TradeStore, out_path: str, calib_path: str | None = None,
                       live: dict | None = None) -> str:
    trades = store.all_trades()
    closed = [t for t in trades if t["exit_ts"]]
    eq = store.equity_history()

    stats = store.stats()
    records = build_calibration_records(closed)
    cal = compute_calibration(records)

    # learning state rows, if a calibration store path was provided
    learning_rows = []
    if calib_path:
        from hedge_fund.calibration import CalibrationStore
        cstore = CalibrationStore(calib_path)
        learning_rows = learning_state_rows(cstore)

    # equity curve as JSON for chart.js
    eq_pts = [
        {"t": e["ts"], "equity": round(e["equity"], 2),
         "baseline": round(e["baseline"], 2) if e["baseline"] else None}
        for e in eq
    ]

    # trade rows: closed trades first, then open positions (never-empty view)
    def _short_ts(ts):
        if not ts:
            return "—"
        # ISO "2026-08-23T13:08:19+00:00" -> "2026-08-23 13:08"
        return ts[:16].replace("T", " ")

    trade_rows = []
    for t in closed:
        trade_rows.append(
            f"<tr><td>{_short_ts(t['entry_ts'])}</td>"
            f"<td>{_short_ts(t['exit_ts'])}</td>"
            f"<td>{html.escape(t['symbol'])}</td>"
            f"<td>{html.escape(t['condition'] or '')}</td>"
            f"<td>{_fmt(t['stated_prob'])}</td>"
            f"<td>{t['entry_price']:,.0f}</td>"
            f"<td>{_fmt(t['exit_price'])}</td>"
            f"<td>{html.escape(t['exit_reason'] or '')}</td>"
            f"<td class=\"{'pos' if (t['pnl'] or 0)>0 else 'neg'}\">{_fmt(t['pnl'])}</td>"
            f"<td>{_pct(t['pnl_pct'])}</td>"
            f"<td>{'✓' if t['hit'] else '✗'}</td></tr>"
        )
    # open positions (exit_ts is null) shown as still-in-trade; if `live`
    # data is provided, show live current price + unrealized P&L.
    open_t = [t for t in trades if not t["exit_ts"]]
    live_pos = {p["symbol"]: p for p in (live or {}).get("positions", [])}
    for t in open_t:
        lp = live_pos.get(t["symbol"])
        if lp and lp.get("current") is not None:
            cls = "pos" if (lp.get("unrealized_pnl") or 0) > 0 else "neg"
            cell = (f"<td>{_fmt(lp['current'])}</td><td>open</td>"
                    f'<td class="{cls}">{_fmt(lp["unrealized_pnl"])}</td>'
                    f'<td>{_pct(lp["unrealized_pct"])}</td><td>live</td>')
        else:
            cell = ("<td>—</td><td>open</td>"
                    "<td class=\"neg\">—</td><td>—</td><td>…</td>")
        trade_rows.append(
            f"<tr><td>{_short_ts(t['entry_ts'])}</td>"
            f"<td>—</td>"
            f"<td>{html.escape(t['symbol'])}</td>"
            f"<td>{html.escape(t['condition'] or '')}</td>"
            f"<td>{_fmt(t['stated_prob'])}</td>"
            f"<td>{t['entry_price']:,.0f}</td>"
            f"{cell}</tr>"
        )

    html_doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>paper-trading-bot — dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px;
         color: #222; background: #fafafa; }}
  h1 {{ font-size: 22px; }} h2 {{ font-size: 17px; margin-top: 28px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 16px 0; }}
  .card {{ background: #fff; border: 1px solid #e3e3e3; border-radius: 8px;
          padding: 14px 18px; min-width: 140px; }}
  .card .k {{ font-size: 12px; color: #777; text-transform: uppercase; letter-spacing: .04em; }}
  .card .v {{ font-size: 22px; font-weight: 600; margin-top: 4px; }}
  .pos {{ color: #1a7f37; }} .neg {{ color: #cf222e; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; font-size: 13px; }}
  th, td {{ border: 1px solid #e3e3e3; padding: 6px 10px; text-align: left; white-space: nowrap; }}
  th {{ background: #f4f4f4; position: sticky; top: 0; }}
  .wrap {{ overflow-x: auto; }}
  #curve {{ max-height: 340px; }}
  .sm {{ color: #888; font-size: 13px; }}
  .badge {{ display:inline-block; padding: 2px 9px; border-radius: 10px;
            font-size: 11px; font-weight: 600; text-transform: uppercase; }}
  .badge-learning {{ background:#fff3cd; color:#7a5c00; }}
  .badge-developing {{ background:#cfe2ff; color:#084298; }}
  .badge-trained {{ background:#d1e7dd; color:#0f5132; }}
  .badge-established {{ background:#198754; color:#fff; }}
  .livebar {{ margin: 8px 0 14px; font-size: 13px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
  .live-dot {{ color:#198754; font-size:14px; }}
  .chip {{ background:#eef7ee; border:1px solid #cde8cd; border-radius:12px;
           padding:2px 10px; }}
  .chip b {{ font-weight:600; }}
</style></head><body>
<h1>paper-trading-bot <span class="sm">— paper tournament on BTC/ETH (not real money)</span></h1>
<div class="livebar">{_live_bar(live)}</div>

<div class="cards">
  <div class="card"><div class="k">{'Live equity' if live else 'Equity'}</div><div class="v {'pos' if (live and (live.get('live_equity') or 0)>=9990) else ''}">{_fmt((live or {}).get('live_equity') or (eq[-1]['equity'] if eq else 0))}</div></div>
  <div class="card"><div class="k">Closed trades</div><div class="v">{stats['closed'] or 0}</div></div>
  <div class="card"><div class="k">Win rate</div><div class="v">{(stats['hits']/stats['closed']) if stats['closed'] else 0:.0%}</div></div>
  <div class="card"><div class="k">Total P&amp;L</div><div class="v {'pos' if (stats['total_pnl'] or 0)>0 else 'neg'}">{_fmt(stats['total_pnl'])}</div></div>
  <div class="card"><div class="k">Brier score</div><div class="v">{cal.brier:.3f}</div></div>
  <div class="card"><div class="k">Calibrated samples</div><div class="v">{cal.n}</div></div>
</div>

<h2>Equity curve vs buy-and-hold baseline</h2>
<div class="sm">PROTOCOL §3: &ldquo;profitable&rdquo; means vs buy-and-hold of the same assets over the same period, after fees. The live cycle stores an equal-weight B&amp;H overlay on each snapshot (amendment 2026-09-01). Paper equity is the solid line; B&amp;H is dashed.</div>
<div class="wrap"><canvas id="curve" height="120"></canvas></div>

<h2>Calibration by condition</h2>
<div class="sm">Stated probability vs measured reliability per signal condition.
The gap (|stated − measured|) is the calibration error.|</div>
<div class="wrap"><table><thead><tr>
<th>Condition</th><th>N</th><th>Stated prob</th><th>Measured</th><th>|gap|</th>
</tr></thead><tbody>
{per_condition_table(closed) or '<tr><td colspan=5>No closed trades yet.</td></tr>'}
</tbody></table></div>

<h2>Learning / Skills acquired</h2>
<div class="sm">Each signal condition is a skill the bot is learning: it accumulates
sampled outcomes, and its calibrated probability shows measured proficiency.
More trials = stronger evidence (learning → developing → trained → established).</div>
<div class="wrap"><table><thead><tr>
<th>Symbol</th><th>Condition</th><th>Trials</th><th>Calibrated p</th><th>Level</th>
</tr></thead><tbody>
{''.join(learning_rows) if learning_rows else '<tr><td colspan=5>No learning state yet.</td></tr>'}
</tbody></table></div>

{strategy_rules_section()}

{backtest_section()}

<h2>Trade log</h2>
<div class="wrap"><table><thead><tr>
<th>Entry date</th><th>Exit date</th><th>Symbol</th><th>Condition</th><th>Stated p</th>
<th>Entry</th><th>Exit</th><th>Reason</th><th>P&amp;L</th><th>P&amp;L %</th><th>Hit</th>
</tr></thead><tbody>
{trade_rows and ''.join(trade_rows) or '<tr><td colspan=11>No trades yet.</td></tr>'}
</tbody></table></div>

<script>
const eq = {json.dumps(eq_pts)};
if (typeof Chart !== 'undefined' && eq.length) {{
  const labels = eq.map(p => p.t);
  new Chart(document.getElementById('curve'), {{
    type: 'line',
    data: {{ labels,
      datasets: [
        {{ label: 'Paper equity', data: eq.map(p => p.equity),
           borderColor: '#1a7f37', fill: false, tension: .15 }},
        {{ label: 'Buy & hold baseline', data: eq.map(p => p.baseline),
           borderColor: '#cf222e', borderDash: [4,4], fill: false }}
      ]}},
    options: {{ responsive: true, maintainAspectRatio: false,
      scales: {{ y: {{ beginAtZero: false }} }} }}
  }});
}}
</script>
</body></html>"""

    from pathlib import Path
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html_doc)
    return out_path
