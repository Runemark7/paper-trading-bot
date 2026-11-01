# Library backlog (batch 5 and after)

Paper only. BTC only. Daily bars, long or flat, next-open fills, the
pre-registered 20% stop. These are candidates for a later catalog append.
They are not registered, and discovery will not claim them until they are
added to `LIBRARY` the same way batch 4 was.

Batch 4 appended 26 names after the existing 56. It did not change those
56, and it did not requeue them. The random farm stays paused.

Do not add a near-duplicate of a name already in the catalog. Batch 4
skipped, on purpose:

- Ichimoku chikou (close versus the close 26 days ago). That is a 26-day
  sign. `lib.tsmom30d` is the 30-day sign and its neighbours are 21 and 42,
  so 26 sits inside a rule that has already been tested. `lib.ichimoku_tk`
  is the tenkan/kijun cross, which is the distinct Hosoda rule.
- TEMA(20) rising. The sign of a triple EMA is TRIX. `lib.trix` is that
  slope at length 15, with neighbours 12 and 18. Length 20 is the same
  rule. `lib.dema` is the double average, which is not TRIX.
- Turn of the quarter. The last day and the first three days, kept only at
  quarter-ends, is a subset of `lib.tom`. That window is already tested
  every month.
- Heikin-Ashi trend. A synthetic up-candle has no published test. It is a
  one-day smoothed trend next to the EMA, DEMA, and Hull rules already in
  the catalog.
- Donchian 20/55, turtle 20/55, Bollinger breakout and mean reversion,
  Keltner breakout and mean reversion, the 1% SMA band, MACD, the 12/26
  and 50/200 crosses, RSI(2), RSI(14), ConnorsRSI, Williams %R, IBS,
  Supertrend, ADX, Aroon, CCI, stochastic, StochRSI, the Ultimate
  Oscillator, Vortex, the chandelier, the 52-week high, time-series
  momentum at 1/3/6/12 months and the 1/3/12 vote, Monday-only, the turn
  of the month, and the Halloween window. Those are already seated or
  tested names. Batch 4 did not add a neighbour of any of them as a new key.
- Inverse-volatility position sizing is still not a signal. The book is
  all-in or flat.

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
| Market facilitation index | Bill Williams, *Trading Chaos* (1995) | Needs volume. |
| Coppock curve | Coppock, Barron's, 1962 | Monthly ROC(14)+ROC(11) smoothed by a 10-month WMA. The warm-up runs past the shared score start of 2018-12-01. |
| 200-week moving average | The long bitcoin average used by many practitioners | 200 weeks is about 1400 days. The tape is not warm by 2018-12-01. |
| January effect | Rozeff and Kinney, *Journal of Financial Economics* (1976); Keim, *Journal of Financial Economics* (1983) | The discovery window holds seven Januaries. The floor is 8 out-of-sample round-trips, so a one-trade-a-year rule cannot pass. |
| Santa Claus rally | Yale Hirsch, *Stock Trader's Almanac* | The last five sessions of December plus the first two of January. Same trade-floor problem as the January effect. |
| Opening-range breakout | Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990) | The daily engine has one decision per UTC day, at the prior close. NR7 and the inside day are the daily forms already registered. |
| Funding-rate or basis carry | Crypto perpetual funding | Not on the OHLC tape. |
| On-chain value (MVRV, SOPR, stock-to-flow) | Coin-metrics style valuation | Not on the OHLC tape. |
| Cross-sectional momentum, pairs, or Antonacci's relative leg | Jegadeesh and Titman, *Journal of Finance* (1993); Antonacci, *Dual Momentum Investing* (2014) | One asset. BTC only. Absolute momentum is already `lib.tsmom12m` / `lib.tsmom12_1`. |
| ZigZag / repainting swings | Chart practice | A swing that is rewritten after later bars is look-ahead. Do not add it. |

## Ready for a later batch

Each line is one rule, with the source and the parameters to pre-register.
Lookbacks stay inside the 2017-08-17 warm-up so the score start can remain
2018-12-01. Neighbours stay one-at-a-time, as in the catalog. None of these
is a parameter neighbour of a name already registered.

1. **Elder Impulse.** Elder, *Trading for a Living* (1993). Long when the 13-day EMA is rising and the MACD histogram is rising. Not `lib.elder_ray` (bear power) and not `lib.macd` (the signal-line cross).
2. **MACD histogram slope.** Appel. Long while the 12/26/9 histogram is above its previous value. `lib.macd` is the signal cross. `lib.ema12x26` is the zero line. The histogram's own slope is the third reading.
3. **True Strength Index.** William Blau, *Technical Analysis of Stocks & Commodities* (1991). Double-smoothed momentum, long while TSI(25, 13) is above zero. Not TRIX (a triple EMA of price) and not `lib.cmo` (a raw sum).
4. **Awesome Oscillator.** Bill Williams, *Trading Chaos* (1995). SMA(5) of the median price minus SMA(34), long while that difference is above zero. Not `lib.fractal`.
5. **Alligator alignment.** Williams, same book. Long when the lips (5, displaced 3) are above the teeth (8, displaced 5) and the teeth are above the jaw (13, displaced 8). The displacement uses only older bars. Not the fractal break.
6. **Balance of Power.** Igor Livshin, *Technical Analysis of Stocks & Commodities*. SMA of (close − open) / (high − low), long above zero. Not `lib.ibs` (one day, no open) and not `lib.qstick` (the body is not divided by the range).
7. **Psychological line.** The percent of up closes over 12 days, long above 50. Achelis, *Technical Analysis from A to Z*, records the standard form. Not an RSI.
8. **Choppiness Index.** E. W. Dreiss, *Technical Analysis of Stocks & Commodities* (1992). Long when CI(14) is below 38.2 and the close is above the 200-day SMA. A low reading means the path is trending. Not ADX.
9. **Random Walk Index.** Michael Poulos, *Technical Analysis of Stocks & Commodities*. Long when the RWI of the highs is above 1 and above the RWI of the lows, length 14. Not ADX and not `lib.vortex`.
10. **Accumulative Swing Index.** Wilder, *New Concepts in Technical Trading Systems* (1978). Long while the ASI is above its previous value. The limit move in his formula can be a fixed fraction of price (the book was written for futures). Not `lib.wilder_vol`, which is a band around yesterday's close.
11. **Darvas box.** Nicolas Darvas, *How I Made $2,000,000 in the Stock Market* (1960). After a new high, a box is the range of the next three days that do not take that high out. Long on a close above the box top. Not a fixed Donchian window.
12. **Ichimoku cloud color.** Hosoda, *Ichimoku Kinko Hyo* (1969). Long while senkou span A is above senkou span B, using the displaced cloud already computed for `lib.ichimoku`. Not price-versus-cloud and not `lib.ichimoku_tk`.
13. **TD setup.** Tom DeMark, *New Market Timing Techniques* (1997). Long after nine consecutive closes each above the close four days earlier, while the close is above the 200-day SMA; flat when the setup fails. The countdown is a later piece and is not this rule.
14. **Range Expansion Index.** DeMark, same book. REI compares each close with the high and low of the bars two and three days back. Long while REI(5) is above zero. Not RSI.
15. **Linear regression slope.** The least-squares slope of the last 20 closes, long while it is positive. Achelis records the standard form. Not a moving-average cross.
16. **Intermediate momentum.** Novy-Marx, "Is Momentum Really Momentum?", *Journal of Financial Economics* (2012). Long when the close 126 days ago is above the close 252 days ago (the return from 12 months ago to 6 months ago). `lib.tsmom12_1` skips only the latest month. This one drops the whole recent half-year.
17. **First half of the month.** Ariel, "A Monthly Effect in Stock Returns", *Journal of Financial Economics* (1987). Long when tomorrow is calendar day 1 through 15; flat on the 16th through month-end. Not `lib.tom`, which is four days.
18. **Laguerre RSI.** Ehlers, *Cybernetic Analysis for Stocks and Futures* (2004), gamma 0.5. Long while the Laguerre RSI is above 0.5. Not `lib.fisher` and not Wilder RSI.
19. **VIDYA.** Chande, the variable index dynamic average. Long while the close is above VIDYA, with the Chande momentum oscillator as the adaptive constant. Not `lib.kama`, which uses the efficiency ratio.
20. **Tillson T3.** Tim Tillson, "Smoothing Techniques for More Accurate Signals", *Technical Analysis of Stocks & Commodities* (1998). Long while the close is above T3(5) with the volume factor at 0.7. Not `lib.dema` and not `lib.hma`.
21. **Relative Momentum Index.** Roger Altman, *Technical Analysis of Stocks & Commodities* (1993). RSI built on an 5-day momentum instead of a one-day change, length 14, long above 50. Not `lib.rsi14` and not `lib.cmo`.
22. **Connors double seven.** Connors and Alvarez, *Short Term Trading Strategies That Work* (2008). Close above the 200-day SMA, long after a 7-day closing low, flat after a 7-day closing high. Not `lib.rsi2` and not `lib.rsi2_cum`.
23. **McGinley Dynamic.** John McGinley, *Technical Analysis of Stocks & Commodities*. Long while the close is above the dynamic average of length 20. The adjustment uses price relative to the average. Not an EMA and not `lib.kama`.
24. **Stochastic pop.** Jake Bernstein. Long after fast %K(14) crosses above 80; flat after it crosses back below 80. Not `lib.stoch` (the %K/%D cross) and not `lib.willr` (the oversold side).
25. **Outside day.** Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990). A day whose high is above the prior high and whose low is below the prior low, then long on a later close above that day's high. Not `lib.inside_day`.
26. **Three lower closes.** Connors. Three consecutive lower closes while the close stays above the 200-day SMA; flat on a close above the 5-day SMA. Not `lib.turtle_soup`, which needs a 20-day low to fail.

Twenty-six of these can be coded on the current OHLC bars. Prefer 1–16
before the remaining calendar rule if the next batch should not be all
seasonality. Item 17 is the only calendar rule in this list.
