# Library backlog (batch 4 and after)

Paper only. BTC only. Daily bars, long or flat, next-open fills, the
pre-registered 20% stop. These are candidates for a later catalog append.
They are not registered, and discovery will not claim them until they are
added to `LIBRARY` the same way batch 3 was.

Do not add a near-duplicate of a name already in the catalog. Batch 3
skipped, on purpose:

- Donchian 20/20, turtle 20/10, and turtle 55/20 are already `lib.donchian20`,
  `lib.turtle20`, and `lib.turtle55`. Batch 3 added the symmetric 55-day
  channel (`lib.donchian55`) only.
- Bollinger breakout and mean reversion, and the Keltner breakout, are
  already in the catalog. Batch 3 added the Keltner mean-reversion side.
- MACD signal-line cross and the 12/26 zero line are `lib.macd` and
  `lib.ema12x26`. The 50/200 cross is both `lib.golden_cross` (SMA) and
  `lib.ema50x200`. Another SMA length next to `lib.sma100` or `lib.sma200`
  is not a new rule. The 1% band Brock, Lakonishok, and LeBaron actually
  tested is `lib.sma200_band`.
- A 14-period stochastic oversold dip above the 200-day SMA is the same
  entry as `lib.willr`, because fast %K = Williams %R + 100. The cross
  (`lib.stoch`) is the distinct Lane rule.
- Supertrend, ADX without a price filter, RSI(2), RSI(2) below 10, IBS,
  and Williams %R are already seated or tested names.
- Inverse-volatility position sizing (Moreira and Muir; Barroso and
  Santa-Clara) is not a signal. The book is all-in or flat.
  `lib.vol_trend` is the binary stand-aside, and the citation says so.
- The random farm stays paused. Nothing in this list turns it back on.

## Blocked on the current tape or the trade floor

These need a data or engine change this batch did not make.

| Candidate | Source | Why it waits |
|---|---|---|
| On-balance volume trend | Granville, *Granville's New Key to Stock Market Profits* (1963) | Daily bars have no volume. |
| Chaikin money flow | Marc Chaikin; the oscillator is the standard chart form | Needs volume. |
| Chaikin oscillator (A/D line MACD) | Chaikin | Needs volume. |
| Elder force index | Elder, *Trading for a Living* (1993) | Needs volume. |
| Arms ease of movement | Arms, *Volume Cycles in the Stock Market* | Needs volume. |
| Accumulation / distribution | Larry Williams | Needs volume. |
| Negative and positive volume index | Fosback, *Stock Market Logic* (1976), after Dysart | Needs volume. |
| Klinger volume oscillator | Stephen Klinger | Needs volume. |
| Session VWAP cross | Standard institutional VWAP | Needs volume, and a session VWAP is not a daily close. |
| Coppock curve | Coppock, Barron's, 1962 | Monthly ROC(14)+ROC(11) smoothed by a 10-month WMA. The warm-up runs past the shared score start of 2018-12-01. |
| January effect | Rozeff and Kinney, *Journal of Financial Economics* (1976); Keim, *Journal of Financial Economics* (1983) | The discovery window holds seven Januaries. The floor is 8 out-of-sample round-trips, so a one-trade-a-year rule cannot pass. |
| Opening-range breakout | Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990) | The daily engine has one decision per UTC day, at the prior close. |
| Funding-rate or basis carry | Crypto perpetual funding | Not on the OHLC tape. |
| On-chain value (MVRV, SOPR) | Coin-metrics style valuation | Not on the OHLC tape. |
| Cross-sectional momentum or a pairs book | Jegadeesh and Titman, *Journal of Finance* (1993) | One asset. BTC only. |
| ZigZag / repainting swings | Chart practice | A swing that is rewritten after later bars is look-ahead. Do not add it. |

## Ready for a later batch

Each line is one rule, with the source and the parameters to pre-register.
Lookbacks stay inside the 2017-08-17 warm-up so the score start can remain
2018-12-01. Neighbours stay one-at-a-time, as in the catalog.

1. **Ichimoku tenkan/kijun cross.** Hosoda, *Ichimoku Kinko Hyo* (1969). Long while the 9-period conversion line is above the 26-period base line. `lib.ichimoku` is price versus the cloud, and the catalog says this cross is not required.
2. **Ichimoku chikou confirmation.** Same source. Long only when the close is also above the close 26 days ago. Still not the cloud rule.
3. **Guppy multiple moving average.** Daryl Guppy, *Trend Trading* / *Market Trading Tactics*. Long when the short group (3, 5, 8, 10, 12, 15) sits entirely above the long group (30, 35, 40, 45, 50, 60).
4. **Elder Ray.** Elder, *Trading for a Living* (1993). Long when the 13-day EMA is rising and low minus that EMA (bear power) is negative but rising. Bull power is high minus the EMA.
5. **DEMA(20) rising.** Patrick Mulloy, *Technical Analysis of Stocks & Commodities* (1994), "Smoothing Data with Faster Moving Averages". Double EMA, not a single EMA cross.
6. **TEMA(20) rising.** Mulloy, same article. Triple EMA, which is not TRIX (TRIX is the rate of change of a triple EMA; `lib.trix` is that slope at length 15).
7. **Chande momentum oscillator.** Chande and Kroll, *The New Technical Trader* (1994). Long while CMO(20) is above zero. Sum of gains minus sum of losses, over the sum of both. Not RSI.
8. **Qstick(8).** Chande, *Technical Analysis of Stocks & Commodities*. SMA of close minus open. Long when that average is above zero. Uses the open, which IBS does not average.
9. **Know Sure Thing.** Martin Pring. Weighted sum of four smoothed rates of change (10, 15, 20, 30). Long when KST is above its 9-period SMA. The lookbacks fit under the score start.
10. **Schaff trend cycle.** Doug Schaff. Stochastic of the MACD line, cycled twice, default 10 / 23 / 50. Long when the result crosses above 25. Not `lib.macd`.
11. **Fisher transform.** John Ehlers, *Cybernetic Analysis for Stocks and Futures* (2004). Transform of a normalized 10-day price location. Long when the Fisher value is above its prior value.
12. **Relative vigor index.** Ehlers. Close-open over high-low, smoothed, versus its signal line. Long on the cross. Not IBS, which is a one-day level with a 200-day filter.
13. **Detrended price oscillator.** A close minus a displaced SMA (20, displaced by 11). Long when the oscillator crosses above zero.
14. **Wilder volatility breakout.** Wilder, *New Concepts in Technical Trading Systems* (1978), the volatility system. Long after the close exceeds the prior close plus 3 times ATR(14); flat after it falls below the prior close minus that band. Not a Keltner band around an EMA.
15. **Bollinger squeeze.** John Bollinger, *Bollinger on Bollinger Bands* (2001), and the later TTM form associated with John Carter. Long after the 20-day, 2-sigma band, having been inside the Keltner channel, closes above the upper band. `lib.bb_break` has no squeeze.
16. **Turtle soup.** Raschke and Connors, *Street Smarts* (1996). Fade a 20-day high: short is not available, so the long rule is the mirror, a failed 20-day low that closes back above it, in the direction of the 200-day SMA. Not `lib.turtle20`, which buys the break.
17. **Cumulative RSI(2).** Connors and Alvarez, *Short Term Trading Strategies That Work* (2008). The sum of RSI(2) over two days below 35, close above the 200-day SMA, exit above the 5-day SMA. Not a single-day `lib.rsi2` cutoff and not ConnorsRSI.
18. **Short-term reversal.** Jegadeesh, "Evidence of Predictable Behavior of Security Returns", *Journal of Finance* 45(3), 1990. Long when the prior month's return is negative and the close is above the 200-day SMA; flat when the prior month's return is positive. The opposite sign of time-series momentum.
19. **12-month momentum, skipping the last month.** Novy-Marx, "Is Momentum Really Momentum?", *Journal of Financial Economics* 103(3), 2012. Long when the close 21 days ago is above the close 252 days before that. `lib.tsmom12m` does not skip the recent month.
20. **Brock-Lakonishok-LeBaron 150-day breakout, 10-day hold.** Brock, Lakonishok, and LeBaron, *Journal of Finance* (1992). They tested trading-range breakouts at 50, 150, and 200 days. The catalog has the 50-day hold (`lib.bll_trb50`) only.
21. **Crabel NR7.** Toby Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990). The narrowest range of 7 days, then long on a close above that day's high, while the close is above the 200-day SMA. The daily engine can express the close break; it cannot express his intraday opening range.
22. **Inside-day breakout.** Crabel, same book. Long on a close above the high of an inside day (high below the prior high and low above the prior low).
23. **Floor-trader pivot.** Classic pivot (high + low + close) / 3 of the prior day. Long when the close is above that pivot and above the 200-day SMA.
24. **Williams fractal.** Bill Williams, *Trading Chaos* (1995). A five-bar fractal high, then long on a close above it. Not a Donchian window.
25. **Mass-index reversal bulge.** Donald Dorsey, *Technical Analysis of Stocks & Commodities* (1992). A 25/9 mass index bulge above 27 then back below 26.5, taken only in the direction of the 200-day SMA.
26. **Relative volatility index.** Dorsey, "The Relative Volatility Index", *Technical Analysis of Stocks & Commodities* (1993). RSI of the standard deviation of highs versus lows, length 14. Long above 50.
27. **Ulcer index filter.** Peter Martin and Byron McCann, *The Investor's Guide to Fidelity Funds* (1989). Long only when price is above the 200-day SMA and the 14-day ulcer index is below its 100-day median. A drawdown filter, not `lib.vol_trend`'s return volatility.
28. **Weekend flat.** French, "Stock Returns and the Weekend Effect", *Journal of Financial Economics* 8(1), 1980. Flat on the UTC Saturday and Sunday sessions, long the other days. `lib.dow` is long on Monday only, which is the bitcoin result in Aharon and Qadan (2019), not this equity weekend rule.
29. **Turn of the quarter.** A narrower calendar than `lib.tom`: the last day of March, June, September, and December plus the first three days of the next month. The turn-of-month literature (McConnell and Xu, *Financial Analysts Journal*, 2008) separates quarter-ends; this rule keeps only those four turns.
30. **Heikin-Ashi trend.** The modified close (open + high + low + close) / 4 above the modified open. Long while the synthetic candle is up. Practitioner chart rule; cite it as practitioner, not as a journal test.

Twenty-four of these can be coded on the current OHLC bars. Prefer 1–20
before the calendar variants if the queue is the constraint, so the next
batch is not all seasonality.
