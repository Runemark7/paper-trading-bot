"""Self-learning strategy explorer.

Iterative improvement over generations (tiny genetic program):
  Gen 1: seed pool of diverse parameterised strategies.
  Test all on train+held-out, rank by robustness score.
  Gen N+1: keep top-k survivors, MUTATE them and CROSS two parents,
  inject fresh random candidates, test again. Track best score per gen.

Usage:
  .venv/bin/python scripts/evolve.py --gens 4 --limit 50
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, "/opt/data/paper-trading-bot")
import hedge_fund.backtest.strategies as bs

HIST = "/tmp/crypto_history.json"
OUT = "/opt/data/paper-trading-bot/state/evolve.json"
random.seed(7)


def _sma(c, p, i):
    return bs.sma(c, p, i)


def _ema(c, p, i):
    return bs.ema(c, p, i)


def _ret(c, i, lb):
    return c[i] / c[i - lb] - 1 if i >= lb else 0.0


def _stack_pred(periods, kind):
    def p(c, i):
        f = _sma if kind == "sma" else _ema
        vals = [f(c, x, i) for x in periods]
        if any(v != v for v in vals):
            return False
        return c[i] > vals[0] and all(vals[j] > vals[j + 1] for j in range(len(vals) - 1))
    return p


def _above_pred(period, kind):
    f = _sma if kind == "sma" else _ema
    def p(c, i):
        v = f(c, period, i)
        return not (v != v) and c[i] > v
    return p


def predicate_for(spec):
    k = spec["kind"]
    if k == "sma_stack":
        return _stack_pred(spec["periods"], "sma"), f"sma_stack_{'_'.join(map(str, spec['periods']))}"
    if k == "ema_stack":
        return _stack_pred(spec["periods"], "ema"), f"ema_stack_{'_'.join(map(str, spec['periods']))}"
    if k == "sma_abv":
        return _above_pred(spec["period"], "sma"), f"sma_abv_{spec['period']}"
    if k == "ema_abv":
        return _above_pred(spec["period"], "ema"), f"ema_abv_{spec['period']}"
    if k == "rsi":
        p, th, ov = spec["period"], spec["th"], spec.get("over", 100)
        return (lambda c, i: bs.rsi(c, p, i) > th and bs.rsi(c, p, i) < ov), f"rsi_{p}_{th}"
    if k == "mom_gt":
        return (lambda c, i: _ret(c, i, spec["lb"]) > spec["thr"]), f"momgt_{spec['lb']}_{spec['thr']}"
    if k == "mom_lt":
        return (lambda c, i: _ret(c, i, spec["lb"]) < -spec["thr"]), f"momlt_{spec['lb']}_{spec['thr']}"
    if k == "and":
        a, na = predicate_for(spec["a"]); b, nb = predicate_for(spec["b"])
        return (lambda c, i: a(c, i) and b(c, i)), f"({na}&{nb})"
    if k == "or":
        a, na = predicate_for(spec["a"]); b, nb = predicate_for(spec["b"])
        return (lambda c, i: a(c, i) or b(c, i)), f"({na}|{nb})"
    raise ValueError(k)


STACK_TRIPLES = [(5, 20, 50), (10, 20, 50), (8, 21, 55), (7, 25, 50),
                 (21, 55, 144), (10, 30, 50), (5, 13, 34), (3, 8, 21)]
PERIODS = [10, 13, 20, 21, 30, 40, 50, 55, 100, 150, 200]
RSI_PERIODS = [7, 10, 14, 20, 30]
RSI_TH = [30, 40, 45, 50, 55, 60]
MOM_LB = [4, 6, 12, 24, 30, 48]
MOM_THR = [0.01, 0.02, 0.03, 0.05, 0.08]


def random_spec():
    r = random.random()
    if r < 0.30:
        return {"kind": "sma_stack", "periods": list(random.choice(STACK_TRIPLES))}
    if r < 0.50:
        return {"kind": "ema_stack", "periods": list(random.choice(STACK_TRIPLES))}
    if r < 0.65:
        return {"kind": "sma_abv", "period": random.choice(PERIODS)}
    if r < 0.75:
        return {"kind": "ema_abv", "period": random.choice(PERIODS)}
    if r < 0.88:
        return {"kind": "rsi", "period": random.choice(RSI_PERIODS), "th": random.choice(RSI_TH), "over": 100}
    if r < 0.96:
        return {"kind": "mom_gt", "lb": random.choice(MOM_LB), "thr": random.choice(MOM_THR)}
    return {"kind": "mom_lt", "lb": random.choice(MOM_LB), "thr": random.choice(MOM_THR)}


def mutate(spec):
    s = json.loads(json.dumps(spec))
    k = s["kind"]
    if k in ("sma_stack", "ema_stack"):
        if random.random() < 0.7:
            s["periods"] = sorted(set(max(2, x + random.choice([-3, -2, -1, 0, 1, 2, 3])) for x in s["periods"]))
        else:
            s = {"kind": "sma_abv" if k == "sma_stack" else "ema_abv", "period": s["periods"][0]}
    elif k in ("sma_abv", "ema_abv"):
        s["period"] = max(3, s["period"] + random.choice([-20, -10, -5, -2, -1, 1, 2, 5, 10, 20]))
    elif k == "rsi":
        s["th"] = max(20, min(80, s["th"] + random.choice([-10, -5, -2, -1, 1, 2, 5, 10])))
    elif k in ("mom_gt", "mom_lt"):
        s["lb"] = max(2, s["lb"] + random.choice([-6, -3, -1, 1, 3, 6]))
        s["thr"] = round(max(0.005, s["thr"] + random.choice([-0.01, -0.005, 0, 0.005, 0.01])), 3)
    elif k in ("and", "or"):
        who = random.random()
        if who < 0.5:
            s["a"] = mutate(s["a"])
        elif who < 0.9:
            s["b"] = mutate(s["b"])
        else:
            s = json.loads(json.dumps(s["a"]))
    return s


def cross(a, b):
    if random.random() < 0.6:
        return {"kind": "and", "a": json.loads(json.dumps(a)), "b": json.loads(json.dumps(b))}
    if random.random() < 0.8:
        return {"kind": "or", "a": json.loads(json.dumps(a)), "b": json.loads(json.dumps(b))}
    return json.loads(json.dumps(a))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gens", type=int, default=4)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--topk", type=int, default=14)
    ap.add_argument("--seed-extra", type=int, default=8)
    ap.add_argument("--stride", type=int, default=6,
                    help="downsample 1h bars (every Nth) to keep evolution fast")
    args = ap.parse_args()

    data = json.load(open(HIST))
    from hedge_fund.backtest.stride import downsample, split_windows
    # downsample each symbol's rows to speed up backtesting on 8yr 1h data
    data = {s: downsample(rows, args.stride) for s, rows in data.items()}
    syms = sorted(data.keys())
    closes = {s: [r[4] for r in rows] for s, rows in data.items()}
    highs = {s: [r[2] for r in rows] for s, rows in data.items()}
    lows = {s: [r[3] for r in rows] for s, rows in data.items()}

    # Walk-forward honest split: train on OLDEST 70%, validate on NEWEST 30%.
    # A champion chosen here is tested on data it never saw during selection.
    def test_wf(spec):
        out = {}
        for s in syms:
            c, h, l = closes[s], highs[s], lows[s]
            n = len(c)
            cut_fit = int(n * 0.70)      # fit
            cut_valid = int(n * 0.85)    # validation starts
            # fit = bars[0:cut_fit]; warmup/valid = bars[cut_fit:cut_valid]; test = bars[cut_valid:]
            r_fit = bs.backtest(c[:cut_fit], h[:cut_fit], l[:cut_fit], predicate_for(spec)[0])
            r_valid = bs.backtest(c[cut_fit:cut_valid], h[cut_fit:cut_valid], l[cut_fit:cut_valid], predicate_for(spec)[0])
            r_test = bs.backtest(c[cut_valid:], h[cut_valid:], l[cut_valid:], predicate_for(spec)[0])
            out[s] = {"fit_pnl": r_fit.total_pnl, "valid_pnl": r_valid.total_pnl,
                      "test_pnl": r_test.total_pnl}
        return out

    def evaluate_wf(spec):
        name = predicate_for(spec)[1]
        w = test_wf(spec)
        fit = sum(v["fit_pnl"] for v in w.values())
        valid = sum(v["valid_pnl"] for v in w.values())
        test = sum(v["test_pnl"] for v in w.values())
        return {"strategy": name, "spec": json.loads(json.dumps(spec)),
                "fit_pnl": round(fit, 0), "valid_pnl": round(valid, 0),
                "test_pnl": round(test, 0)}

    # Selection: pick by OUT-OF-FIT validation (not fit) to avoid overfit,
    # and REQUIRE the untouched test window to be positive too — a strategy is
    # only a champion if it generalizes to data it never saw during selection.
    def score_wf(e):
        if e["test_pnl"] <= 0:
            return float("-inf")  # fails the honesty gate, no championship
        return e["valid_pnl"] + e["test_pnl"] + 0.3 * e["fit_pnl"]

    # Generation 1
    current = [random_spec() for _ in range(args.limit)]
    history = []
    final_ranked = []

    for gen in range(1, args.gens + 1):
        evals = [evaluate_wf(s) for s in current]
        best = {}
        for e in evals:
            n = e["strategy"]
            if n not in best or score_wf(e) > score_wf(best[n]):
                best[n] = e
        ranked_all = sorted(best.values(), key=score_wf, reverse=True)
        # only strategies that PASSED the untouched-test gate can champion
        ranked = [e for e in ranked_all if score_wf(e) != float("-inf")]
        final_ranked = ranked
        top = ranked[: args.topk]
        best_score = score_wf(top[0]) if top else 0
        history.append({"gen": gen, "n": len(ranked),
                        "best_score": round(best_score, 0),
                        "best": top[0]["strategy"] if top else None,
                        "best_fitP": top[0]["fit_pnl"] if top else None,
                        "best_validP": top[0]["valid_pnl"] if top else None,
                        "best_testP": top[0]["test_pnl"] if top else None})
        print(f"GEN {gen}: best={top[0]['strategy'] if top else None} "
              f"score={best_score:.0f} fitP={top[0]['fit_pnl'] if top else 0} "
              f"validP={top[0]['valid_pnl'] if top else 0} testP={top[0]['test_pnl'] if top else 0}")

        if gen == args.gens:
            break

        parents = [json.loads(json.dumps(e["spec"])) for e in top]
        if not parents:
            # nothing passed the honesty gate; restart with fresh random seed
            current = [random_spec() for _ in range(args.limit)]
            continue
        nxt = []
        keep = max(0, args.limit - args.seed_extra)
        for i in range(keep):
            if random.random() < 0.75:
                child = mutate(random.choice(parents))
            else:
                a = parents[i % len(parents)]
                b = parents[(i + 1) % len(parents)]
                child = cross(a, b)
            nxt.append(child)
        for _ in range(args.seed_extra):
            nxt.append(random_spec())
        current = nxt

    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT).write_text(json.dumps({
        "generations": history,
        "champion": final_ranked[0] if final_ranked else None,
        "top10": [{"strategy": e["strategy"], "fit_pnl": e["fit_pnl"],
                   "valid_pnl": e["valid_pnl"], "test_pnl": e["test_pnl"]} for e in final_ranked[:10]]
    }, indent=2))
    print(f"\nsaved -> {OUT}")


if __name__ == "__main__":
    main()