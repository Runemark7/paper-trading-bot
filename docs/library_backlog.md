# Library backlog (batch 8 and after)

Paper only. BTC only. Daily bars, long or flat, next-open fills, the
pre-registered 20% stop. These are candidates for a later catalog append.
They are not registered, and discovery will not claim them until they are
added to `LIBRARY` the same way batch 7 was.

Batch 7 appended 22 names after the existing 132. It also corrected
`lib.td_cd`, `lib.pierce`, and `lib.soldiers` in place. Those three keys
stay. A library claim leases them again while the newest discovery-log row
lacks `signal_revision`. The old rows stay. It did not change the other
129 names, and it did not requeue them. The random farm stays paused.

Do not add a near-duplicate of a name already in the catalog. Batch 7
skipped, on purpose:

- The SMI signal line. `lib.smi` is the zero line. A short average of SMI
  is the same kind of signal cross as `lib.tsi_sig` and `lib.cmo_sig`, and
  it can wait until the zero line has been tested.
- MAMA versus FAMA. The paper's crossover is a second reading of the same
  Hilbert average. `lib.mama` is the close versus MAMA.
- The 2013 Even Better Sinewave as a single normalized line. `lib.ebsw` is
  the 2004 sine-versus-lead rule. The later one-line oscillator is not a
  second key.
- A 50 percent Fibonacci hold. It is a neighbour of `lib.fibhold`, whose
  center is the 61.8 percent level.
- Woodie R1 or R2. They are other levels of `lib.woodie`.
- An ADX-rising filter on top of `lib.di_cross`. `lib.adx14` already
  requires +DI above −DI and ADX above 25.
- The awesome-oscillator twin peaks. `lib.saucer` is the three-bar pattern
  below zero. A second pattern on the same oscillator is not a new key
  until that one has been tested.
- Another gator phase (eating, sated) next to `lib.gator` and
  `lib.gator_wake`.
- A doji morning star. It is a tighter `lib.morning`.
- Price above DEMA, or TEMA rising. `lib.dema` is DEMA rising. The slope of
  a triple EMA is TRIX.
- Donchian, turtle, Bollinger, Keltner, the SMA band, MACD and its histogram,
  the impulse system, RSI, the slow stochastic, ConnorsRSI, the double
  seven, three lower closes, three white soldiers, the engulfing, the
  piercing line, the harami, the three outside up, the morning star, the
  hammer, rising three methods, the two-bar reversal, Williams %R, the
  stochastic cross, the stochastic pop, the stochastic momentum index, IBS,
  the oops reversal, Supertrend, ADX, the directional cross, Aroon, CCI,
  Vortex, choppiness, the random walk index, VHF, TRIX and its signal line,
  TSI and its signal line, the Chande oscillator and its signal line,
  DeMarker, the range expansion index, TD setup, TD countdown, TD Combo,
  the awesome oscillator and its saucer, the accelerator, the alligator,
  the gator and its awakening, VIDYA, FRAMA, MAMA, the zero-lag EMA, the
  super smoother, the instantaneous trendline, the cyber cycle, the sine
  wave, the Laguerre RSI, the Laguerre filter, T3, ALMA, the McGinley
  dynamic, KAMA, the efficiency ratio, the Hull average, DEMA, the cloud
  and the tenkan/kijun cross, the three-line break, kagi, renko, time-series
  momentum, the variance ratio, the intermediate window, the regression
  slope, r-squared, the regression value, the standard-error band, the
  Fibonacci hold, Woodie's pivot, the floor pivot, Camarilla, Garman-Klass,
  the first half of the month, expiration week, the weekend, Monday, the
  turn of the month, and the Halloween window. Those are already seated or
  tested names. Batch 7 did not add a neighbour of any of them as a new key.
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
| Opening-range breakout | Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990) | The daily engine has one decision per UTC day, at the prior close. NR7, the inside day, and the outside day are the daily bar forms already registered. |
| Funding-rate or basis carry | Crypto perpetual funding | Not on the OHLC tape. |
| On-chain value (MVRV, SOPR, stock-to-flow) | Coin-metrics style valuation | Not on the OHLC tape. |
| Cross-sectional momentum, pairs, or Antonacci's relative leg | Jegadeesh and Titman, *Journal of Finance* (1993); Antonacci, *Dual Momentum Investing* (2014) | One asset. BTC only. Absolute momentum is already `lib.tsmom12m` / `lib.tsmom12_1` / `lib.mom_mid`. |
| ZigZag / repainting swings | Chart practice | A swing that is rewritten after later bars is look-ahead. Do not add it. |

## Ready for a later batch

Each line is one rule, with the source and the parameters to pre-register.
Lookbacks stay inside the 2017-08-17 warm-up so the score start can remain
2018-12-01. Neighbours stay one-at-a-time, as in the catalog. None of these
is a parameter neighbour of a name already registered.

1. **RSI signal line.** Wilder, *New Concepts in Technical Trading Systems* (1978). Long while RSI(14) is above its 9-day SMA. `lib.rsi14` is the 50 line. The 14 stays fixed. Not `lib.rsi2`.
2. **DeMarker signal line.** DeMark, *The New Science of Technical Analysis* (1994). Long while DeMarker(14) is above its 9-day SMA. `lib.demarker` is the 0.5 line. The 14 stays fixed.
3. **Qstick signal line.** Chande and Kroll, *The New Technical Trader* (1994). Long while Qstick(8) is above its 5-day SMA. `lib.qstick` is the zero line. The 8 stays fixed.
4. **DPO signal line.** Achelis, *Technical Analysis from A to Z* (1995). Long while the detrended price oscillator of length 20 is above its 9-day SMA. `lib.dpo` is the zero line. The 20 stays fixed.
5. **Balance of power signal line.** Igor Livshin. Long while balance of power of length 14 is above its 9-day SMA. `lib.bop` is the zero line. The 14 stays fixed.
6. **Psychological line signal.** Achelis. Long while the 12-day up-close percent is above its 5-day SMA. `lib.psych` is the 50 level. The 12 stays fixed. Not an RSI.
7. **Fisher above zero.** Ehlers, *Cybernetic Analysis for Stocks and Futures* (2004). Long while the Fisher transform of length 10 is above zero. `lib.fisher` is the one-bar slope. The zero line is the other reading.
8. **Cyber cycle zero line.** Ehlers, same book. Long while the cyber cycle is above zero. `lib.cyber` is the cycle above its previous value. Alpha stays 0.07. Not `lib.ebsw`.
9. **Center of gravity rising.** Ehlers, *Technical Analysis of Stocks & Commodities*. The center of gravity of the last 10 closes, long while it is above its previous value. Not an SMA slope and not `lib.linreg`.
10. **Roofing filter.** Ehlers, *Cycle Analytics for Traders* (2013). A high-pass of length 48, then the super smoother of length 10, long while that filter is above zero. Not `lib.cyber` and not `lib.ssmooth` alone. The 10 stays fixed.
11. **Spearman rank correlation.** The rank correlation of the last 10 closes with time, long when it is positive. Achelis records the rank correlation as a separate trend statistic from least squares. Not `lib.linreg` and not `lib.rsquared`.
12. **Theil-Sen slope.** Theil, "A rank-invariant method of linear and polynomial regression analysis" (1950). The median of the pairwise slopes of the last 10 closes, long when it is positive. Not `lib.linreg`.
13. **Projection oscillator.** Mel Widner, *Technical Analysis of Stocks & Commodities* (1995). Where the close sits between the regression value minus one standard error and plus one standard error, on a 0 to 100 scale, long above 50. `lib.seband` is the cross of the upper band. `lib.regline` is the line with no band. The width stays 1.
14. **Price above the Hull average.** Hull (2005). Long while the close is above the Hull average of length 16. `lib.hma` is the average rising, not price versus the average.
15. **Homing pigeon.** Nison, *Japanese Candlestick Charting Techniques* (1991). Two black candles, the second body inside the first, and the close above the 200-day SMA. `lib.harami` does not require the second candle to be black.
16. **Matching low.** Nison, same book. Two black candles with the same low, above the 200-day SMA. Not `lib.hammer` and not `lib.oops`.
17. **Inverted hammer.** Nison, same book. The upper shadow is at least twice the body, the body sits in the bottom third of the range, the prior close is down, and the close is above the 200-day SMA. Not `lib.hammer`, which is the lower shadow.
18. **Bullish three-line strike.** Nison, same book. Three black candles, then a white candle that opens at or below the third close and closes above the first open, above the 200-day SMA. Not `lib.engulf` and not `lib.three_out`.
19. **Bullish belt hold.** Nison, same book. A long white candle that opens at its low and closes near its high, after a down close, above the 200-day SMA. Not `lib.soldiers`.
20. **Aroon strong trend.** Achelis, *Technical Analysis from A to Z*. Aroon Up above 70 and Aroon Down below 30, length 25. `lib.aroon` is Up versus Down with no 70/30 floor. The 70 and 30 stay fixed.
21. **Intraday momentum index.** Chande, *The New Technical Trader* (1994). An RSI built on the close-versus-open change instead of the close-to-close change, length 14, long above 50. Not `lib.rsi14` and not `lib.bop`.
22. **RSI failure swing.** Wilder, *New Concepts in Technical Trading Systems* (1978). RSI(14) falls below 30, rises back above 30, holds a higher RSI low, then breaks the intervening RSI high, with the close above the 200-day SMA. Not `lib.rsi14` and not `lib.rsi2`.

Twenty-two of these can be coded on the current OHLC bars. None of them
is a one-trade-a-year calendar.
