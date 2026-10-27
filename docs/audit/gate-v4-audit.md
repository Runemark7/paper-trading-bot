# Gate v4 audit (2026-10-27)

Report only. No champion was retired and no stored pass was rewritten.
Prod numbers below are a point-in-time read of the public API at
2026-10-27 15:47:42 UTC. The farm was still evaluating
(`last_strategy` `h4_sma_abv_200&near_swing_lo_90&sma_stack_20_50_200`,
`last_tested_at` 2026-10-27T15:45:10Z), so the tested count moves.

Sources: `GET /api/discovery/summary?compact=1`,
`GET /api/discovery/results?status=pass&limit=1000` (122 rows, `total` 122),
`GET /api/champions`.

## Headline

| Item | Number |
|---|---|
| Tested index | 18,879 (pass 122, fail 18,757) |
| Active champions | 2, both closed trades 0, paper P&L 0 |
| Passes that carry a per-window return series | 0 |
| Passes with a daily-equity Sharpe stored | 10 (all tag `sltp_cap100_bhdsr_tiled87_20261008`, `regimes_tested` 23) |
| Approximate DSR of the best of those 10, N = 18,879 | **0.020** (`dip_234b_lt8pc&h4_ema_abv_140`, daily Sharpe 0.842) |
| Passes with approximate DSR ≥ 0.95 | **0** (also 0 at N = 122 and at N = 10) |
| PBO of any stored pass | **not computable** (no window matrix) |
| Stored passes that meet gate v4 on the evidence in the API | **0 / 122** |
| Active champions that meet gate v4 | **0 / 2** (not retired; forward status would be `not_registered`) |

The 122 index passes are not "122 dip variants". Ten of them are the
current-tag dip neighbourhood. The other 112 are older admits (93 with
`regimes_tested` 8, 19 with no window count), mostly `h1_* × mom_18b_gt2pc`
stacks, and they have no daily Sharpe at all.

## Fees and slippage

Long-only spot. A strategy enters only when its predicate is true, buys
at `close * (1 + slippage)`, and places the stop below the entry. There
is no short path. Exits are stop, take-profit, or the window end
(`eod`). Signal flip does not close a lot.

| Constant | Value | Where |
|---|---|---|
| Taker fee | 0.001 (0.1% of notional, each fill) | `hedge_fund/brokers/paper.py` `TAKER_FEE` |
| Slippage | 0.0002 (2 bps, worse price each side) | `hedge_fund/brokers/paper.py` `SLIPPAGE` |
| Same two numbers | re-exported | `hedge_fund/risk/rm_v1.py` `FEE_TAKER`, `FEE_SLIPPAGE` |
| Entry cost | `(1 + slippage) * (1 + taker)` | `ENTRY_COST_MULT` in `rm_v1.py`; `backtest()` in `hedge_fund/backtest/strategies.py` |
| Notional cap | 100% of equity, cash never negative | `MAX_NOTIONAL_FRAC` = 1.0, `size_position` |
| Start cash | 10,000 per symbol | `PAPER_START_CASH` |

`backtest()` defaults `taker_fee` / `slippage` to those two constants
(lines that assign `FEE_TAKER if taker_fee is None`). Buy-and-hold uses
the same fee and slippage arguments (`buy_and_hold_daily_equity`). The
model is a constant taker plus a constant adverse price. It is not a
measured Binance fill, not a spread, and not a size-dependent impact.
Gate v4's stress re-run uses taker 0.002 and slippage 0.0007 (2 bps + 5 bps)
on both the strategy and buy-and-hold. That stress is not in the stored rows.

## Trial count

`hedge_fund/trading/trials.py` `trial_census()` reads
`discovery_tested.json` metrics. N for the deflated Sharpe is the count
of names with a finite Sharpe that are not `ops_park`, plus one for the
candidate under test. Ops parks are not return trials. The sample stdev
of those trade-count Sharpes is returned as `trade_sharpe_std` and is
**not** the DSR standard error (different units).

The public summary does not split ops parks out of `tested` = 18,879.
This audit uses 18,879 as N. If some of those rows are ops parks, the
true return-trial count is smaller and the DSR probabilities below are
a bit low. They are not close to 0.95: the best is 0.020, and even
N = 10 (only the ten current-tag passes, pretending the other ~18,869
trials do not exist) leaves the best at about 0.65.

Claim responses now include `trials`. A claim that omits `n_trials`
does not fall back to the worker's local index (that index is often
empty, which would report N = 1 and skip the haircut).

## What the 122 passes actually are

`family_signature` (template with numbers replaced, thresholds kept
exact) on the 122 names:

- 19 templates, 21 threshold groups. Largest group is 13 names of
  `ema_abv_N & h1_ema_abv_N & mom_Nb_gt2pc`.
- 112 names have no `gate_rules` and no `daily_sharpe`. 93 of them
  record `regimes_tested` = 8 (the pre-2026-10-14 window count). They
  are the old h1×mom admit island, not the dip book.
- 10 names carry `gate_rules` = `sltp_cap100_bhdsr_tiled87_20261008`,
  `regimes_tested` = 23, `data_end` = 1791504000000 (2026-10-09 00:00 UTC).
  Nine are `dip_{204,222,234}b_lt8pc & h4_ema_abv_{120..160}`. One is
  `dip_240b_lt10pc & h4_sma_abv_140`. Lookbacks 204 / 222 / 234 sit
  inside a 15% band. The 8% threshold is shared by the nine; the tenth
  is 10% and uses SMA instead of EMA. This is one parameter
  neighbourhood, which is why a lone-spike check matters. Their
  `data_end` is after the holdout end (2026-10-01), and 23 windows
  include segments that overlap the sealed window, so these Sharpes
  are not discovery-only and are not BTC-only (the v3 book was
  BTC+ETH).

No result row has `windows`, daily returns, skew, or kurtosis. PBO
cannot be computed from this payload. A local Binance re-backtest of
122 names times neighbours was not run: the stored fields already fail
every v4 check that needs a fresh BTC-only, pre-holdout sample, and
the approximate DSR below is enough to show the multiple-testing
problem on the ten rows that have a daily Sharpe.

## Approximate DSR (normality, not a v4 pass)

Method, labeled as an approximation because the return series is absent:

- Per-observation Sharpe ≈ stored annualized `daily_sharpe` / √365.
- Skew 0, raw kurtosis 3 (Gaussian). Real crypto daily returns are
  heavier-tailed, which would push DSR down further, not up.
- `n_obs` = 1,998, the calendar days from `QUAL_OOS_START_MS`
  (2021-04-20) to `data_end` (2026-10-09). The v3 segments tile that
  span. This is the joint-book, holdout-inclusive sample, not the 18
  discovery windows.
- N = 18,879.

Bailey & Lopez de Prado 2014. SR* at this N is about 4.0 estimator
standard deviations, so a per-observation Sharpe of 0.04 does not clear it.

| Strategy | Daily Sharpe | Approx. SR | Approx. DSR |
|---|---:|---:|---:|
| dip_234b_lt8pc&h4_ema_abv_140 | 0.842 | 0.0441 | 0.020 |
| dip_204b_lt8pc&h4_ema_abv_140 | 0.561 | 0.0294 | 0.0035 |
| dip_222b_lt8pc&h4_ema_abv_140 | 0.554 | 0.0290 | 0.0033 |
| dip_222b_lt8pc&h4_ema_abv_130 | 0.542 | 0.0284 | 0.0030 |
| dip_204b_lt8pc&h4_ema_abv_160 | 0.504 | 0.0264 | 0.0023 |
| dip_222b_lt8pc&h4_ema_abv_160 | 0.500 | 0.0262 | 0.0022 |
| dip_222b_lt8pc&h4_ema_abv_150 | 0.500 | 0.0262 | 0.0022 |
| dip_204b_lt8pc&h4_ema_abv_130 | 0.480 | 0.0251 | 0.0019 |
| dip_222b_lt8pc&h4_ema_abv_120 | 0.457 | 0.0239 | 0.0016 |
| dip_240b_lt10pc&h4_sma_abv_140 | 0.438 | 0.0229 | 0.0014 |

The other 112 passes have no daily Sharpe, so this approximation is
not defined for them. `dip_240`'s trade-count Sharpe of 2.76 is a
different statistic (mean/stdev of trade returns × √n) and is not
plugged into the DSR.

## Champions and the forward test

| Champion | Since (UTC) | Closed | Qual on the row | Forward |
|---|---|---:|---|---|
| dip_234b_lt8pc&h4_ema_abv_140 | 2026-10-09 06:06 | 0 | `sharpe_qual` 0.30 only; no `qual` block, no `forward_test` | not registered, not flagged |
| dip_240b_lt10pc&h4_sma_abv_140 | 2026-10-09 07:09 | 0 | daily Sharpe 0.438 vs B&H 0.407, trade Sharpe 2.76, 56 trades, v3 tag | not registered, not flagged |

Neither row has `forward_test`. Gate v4 does not backfill one, because
the backtest max drawdown was not stored. With 0 closed trades they
would be `pending` even after registration (the check waits for both
90 days and 20 closed trades). This change does not retire them.

## Requalify table

"Would pass v4" means the stored row already contains a v4 attestation
measured on BTC-only discovery windows plus one holdout, with DSR, PBO,
neighbours, and cost stress. None do.

| Set | n | Pass v4 on stored evidence | Why |
|---|---:|---:|---|
| Active champions | 2 | 0 | v3 tag or no qual block; no holdout, no DSR/PBO/neighbours/cost; book was BTC+ETH over 23 windows that include the holdout. Left in the pool. |
| Tiled-87 passes (the dip neighbourhood above) | 10 | 0 | Same missing blocks. Approximate DSR ≤ 0.020. PBO undefined. |
| Older index passes (8 windows or unknown) | 112 | 0 | No daily Sharpe, no v4 block, window count is not the sealed 18. |
| All index passes | 122 | 0 | |

A later re-backtest on a BTC-only tape cut at 2025-10-01 could still
qualify a new name. It cannot be inferred from these rows, and this
audit does not invent that result.
