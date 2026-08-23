"""Static HTML dashboard generator.

Reads the SQLite store and emits a self-contained `report.html` with:
  - Equity curve (paper P&L) overlaid with the buy-and-hold baseline so
    "am I profitable?" is always answered against a benchmark, not in a vacuum.
  - Trade log table: entry/exit, size, stop, stated probability vs outcome.
  - Calibration view: stated vs measured reliability per condition + Brier.

No server, no login — just open the HTML. Uses chart.js from a CDN for the
curve (degrades gracefully to a table if offline).
"""

from __future__ import annotations

import html
import json
from collections import defaultdict

from hedge_fund.calibration import compute_calibration
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


def backtest_section() -> str:
    """Build the A/B backtest HTML section from state/abtest.json."""
    from pathlib import Path
    import json as _json
    p = Path(__file__).resolve().parent.parent.parent / "state" / "abtest.json"
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


def generate_dashboard(store: TradeStore, out_path: str, calib_path: str | None = None) -> str:
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
    trade_rows = []
    for t in closed:
        trade_rows.append(
            f"<tr><td>{html.escape(t['symbol'])}</td>"
            f"<td>{html.escape(t['condition'] or '')}</td>"
            f"<td>{_fmt(t['stated_prob'])}</td>"
            f"<td>{t['entry_price']:,.0f}</td>"
            f"<td>{_fmt(t['exit_price'])}</td>"
            f"<td>{html.escape(t['exit_reason'] or '')}</td>"
            f"<td class=\"{'pos' if (t['pnl'] or 0)>0 else 'neg'}\">{_fmt(t['pnl'])}</td>"
            f"<td>{_pct(t['pnl_pct'])}</td>"
            f"<td>{'✓' if t['hit'] else '✗'}</td></tr>"
        )
    # open positions (exit_ts is null) shown as still-in-trade
    open_t = [t for t in trades if not t["exit_ts"]]
    for t in open_t:
        trade_rows.append(
            f"<tr><td>{html.escape(t['symbol'])}</td>"
            f"<td>{html.escape(t['condition'] or '')}</td>"
            f"<td>{_fmt(t['stated_prob'])}</td>"
            f"<td>{t['entry_price']:,.0f}</td>"
            f"<td>—</td>"
            f"<td>open</td>"
            f"<td class=\"neg\">—</td><td>—</td><td>…</td></tr>"
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
</style></head><body>
<h1>paper-trading-bot <span class="sm">— self-learning probabilities on BTC/ETH (paper)</span></h1>

<div class="cards">
  <div class="card"><div class="k">Equity</div><div class="v">{_fmt(eq[-1]['equity']) if eq else '—'}</div></div>
  <div class="card"><div class="k">Closed trades</div><div class="v">{stats['closed'] or 0}</div></div>
  <div class="card"><div class="k">Win rate</div><div class="v">{(stats['hits']/stats['closed']) if stats['closed'] else 0:.0%}</div></div>
  <div class="card"><div class="k">Total P&amp;L</div><div class="v {'pos' if (stats['total_pnl'] or 0)>0 else 'neg'}">{_fmt(stats['total_pnl'])}</div></div>
  <div class="card"><div class="k">Brier score</div><div class="v">{cal.brier:.3f}</div></div>
  <div class="card"><div class="k">Calibrated samples</div><div class="v">{cal.n}</div></div>
</div>

<h2>Equity curve vs buy-and-hold baseline</h2>
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

{backtest_section()}

<h2>Trade log</h2>
<div class="wrap"><table><thead><tr>
<th>Symbol</th><th>Condition</th><th>Stated p</th><th>Entry</th><th>Exit</th>
<th>Reason</th><th>P&amp;L</th><th>P&amp;L %</th><th>Hit</th>
</tr></thead><tbody>
{trade_rows and ''.join(trade_rows) or '<tr><td colspan=9>No closed trades yet.</td></tr>'}
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
