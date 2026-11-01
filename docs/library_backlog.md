# Library backlog (batch 7 and after)

Paper only. BTC only. Daily bars, long or flat, next-open fills, the
pre-registered 20% stop. These are candidates for a later catalog append.
They are not registered, and discovery will not claim them until they are
added to `LIBRARY` the same way batch 6 was.

Batch 6 appended 24 names after the existing 108. It did not change those
108, and it did not requeue them. The random farm stays paused.

Do not add a near-duplicate of a name already in the catalog. Batch 6
skipped, on purpose:

- The Pretty Good Oscillator and the disparity index. Both are positive
  exactly when the close is above an SMA. That sign is already seated.
- The Aroon oscillator. Its sign is Aroon Up versus Aroon Down, which is
  `lib.aroon`.
- The regression endpoint, and the one-day time-series forecast of that
  line. The endpoint is `lib.regline`. The forecast is that value plus one
  slope, so it sits on `lib.regline` and `lib.linreg` rather than a new key.
- Floor-trader R1. It is a higher level of the same (high + low + close) / 3
  pivot as `lib.floor_pivot`.
- A second Camarilla side (L3). H3 is `lib.camarilla`.
- Parkinson, Rogers-Satchell, or Yang-Zhang as another "variance below its
  median" key next to `lib.gk`. One OHLC variance filter is enough until
  that one has been tested.
- The Ehlers decycler. It is another low-pass of the close next to
  `lib.ssmooth`.
- Another single weekday. Monday is `lib.dow`. A different day of the week
  is a neighbour of that rule, not a new key.
- An SMA that is rising, PPO above zero, the inverse Fisher of RSI, NR4,
  a daily Coppock, Ichimoku chikou, TEMA rising, the turn of the quarter,
  and Heikin-Ashi. Earlier batches skipped those. They are still not keys.
- Donchian, turtle, Bollinger, Keltner, the SMA band, MACD and its histogram,
  the impulse system, RSI, the slow stochastic, ConnorsRSI, the double
  seven, three lower closes, three white soldiers, the engulfing, the
  piercing line, the harami, Williams %R, the stochastic cross, the
  stochastic pop, IBS, the oops reversal, Supertrend, ADX, Aroon, CCI,
  Vortex, choppiness, the random walk index, VHF, TRIX and its signal line,
  TSI, DeMarker, the range expansion index, the awesome oscillator, the
  accelerator, the alligator, the gator, VIDYA, FRAMA, the zero-lag EMA,
  the super smoother, the cyber cycle, the Laguerre RSI, the Laguerre
  filter, T3, ALMA, the McGinley dynamic, KAMA, the Hull average, DEMA,
  the cloud and the tenkan/kijun cross, the three-line break, time-series
  momentum, the variance ratio, the intermediate window, the regression
  slope and the regression value, Garman-Klass, the first half of the
  month, expiration week, the weekend, Monday, the turn of the month, and
  the Halloween window. Those are already seated or tested names. Batch 6
  did not add a neighbour of any of them as a new key.
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

1. **Stochastic momentum index.** William Blau, *Technical Analysis of Stocks & Commodities* (1993). Double-smoothed distance of the close from the midpoint of the high-low range, divided by the smoothed half-range. Long while SMI(10, 3, 3) is above zero. Not `lib.stoch`, not `lib.stoch_slow`, and not `lib.tsi`.
2. **TSI signal line.** Blau, *Technical Analysis of Stocks & Commodities* (November 1991). Long while TSI(25, 13) is above its 7-day EMA. `lib.tsi` is the zero line. The 25 and 13 stay fixed. Not `lib.trix_sig`, which is a triple EMA of price.
3. **CMO signal line.** Tushar Chande, *The New Technical Trader* (1994). Long while the Chande momentum oscillator of length 14 is above its 9-day SMA. `lib.cmo` is the zero line. Not an RSI.
4. **MESA adaptive moving average.** John Ehlers, *Technical Analysis of Stocks & Commodities* (2001). Close above MAMA. The fast limit stays 0.5 and the slow limit stays 0.05. Not `lib.frama` (fractal dimension), not `lib.kama` (efficiency ratio), and not `lib.vidya`.
5. **Instantaneous trendline.** Ehlers, *Cybernetic Analysis for Stocks and Futures* (2004). Close above the instantaneous trendline. Not `lib.ssmooth` and not `lib.cyber`, which is the cycle above its trigger.
6. **Even Better Sinewave.** Ehlers, same book. The sine wave above the lead sine. A cycle-phase rule, not the one-bar slope of `lib.cyber` and not `lib.fisher`.
7. **Efficiency-ratio filter.** Perry Kaufman, *Smarter Trading* (1995). The 10-day efficiency ratio above 0.30 and the close above the 200-day SMA. The 0.30 stays fixed. `lib.kama` folds the ratio into an average. This one thresholds the ratio.
8. **Regression r-squared.** Achelis, *Technical Analysis from A to Z* (1995). The 20-day r-squared above 0.40 and the slope positive. The 0.40 stays fixed. `lib.linreg` is any positive slope. Not `lib.vhf`.
9. **Standard error band.** Jon Andersen, *Technical Analysis of Stocks & Commodities* (February 1996). Close above the 20-day regression value plus one standard error. The width stays 1. `lib.regline` is the line with no band.
10. **Kagi.** Steve Nison, *Beyond Candlesticks* (1994). A yang turn: the line reverses up by at least 2 percent from the prior swing low, built from closes in order. Not `lib.tlbreak`, which counts lines instead of a price amount.
11. **Renko.** Nison, same book. A white brick of 1 percent of price, built from closes in order. Long on the close that prints the white brick. Not `lib.tlbreak` and not a fixed Donchian window.
12. **Morning star.** Nison, *Japanese Candlestick Charting Techniques* (1991). A long black candle, a small real body, then a white close back through the midpoint of the first body, and the close above the 200-day SMA. Not `lib.pierce` and not `lib.soldiers`.
13. **Hammer.** Nison, same book. The lower shadow is at least twice the body, the body sits in the top third of the range, the prior close is down, and the close is above the 200-day SMA. Not `lib.oops` and not `lib.ibs`.
14. **Rising three methods.** Nison, same book. A long white candle, three small bodies held inside its range, then a white close above the first candle's close, above the 200-day SMA. Not `lib.soldiers` and not `lib.inside_day`.
15. **Three outside up.** Nison, same book. A bullish engulfing followed by a third close above the engulfing close, above the 200-day SMA. `lib.engulf` is the engulfing bar alone.
16. **Two-bar reversal.** Thomas Bulkowski, *Encyclopedia of Candlestick Charts* (2008). The low under yesterday's low and the close above yesterday's high. Not `lib.oops`, which only has to reclaim the low, and not `lib.outside`, which waits for a later break.
17. **TD Combo.** Tom DeMark, *New Market Timing Techniques* (1997). After a buy setup, 13 closes each at or below the close two bars earlier. `lib.td_cd` compares the close with the low two bars earlier. The combo does not. The long is the completion, above the 200-day SMA.
18. **Directional movement cross.** Wilder, *New Concepts in Technical Trading Systems* (1978). Long while +DI is above −DI. `lib.adx14` also requires ADX above 25. This one has no ADX floor.
19. **Woodie pivot.** Woodie's pivot of the prior day, (high + low + 2 × close) / 4. Long when the close is above that pivot and the 200-day SMA. Not `lib.floor_pivot`, which weights the three prices equally, and not `lib.camarilla`.
20. **Fibonacci hold.** Robert Fischer, *Fibonacci Applications and Strategies for Traders* (1993). Close above the 200-day SMA and above the 61.8 percent level of the prior 20-day high-low range. The close has not retraced more than 38.2 percent off that high. Not a moving-average cross.
21. **Awesome oscillator saucer.** Bill Williams, *Trading Chaos* (1995). Two falling bars of the awesome oscillator below zero, then a higher bar that is still below zero, and the close above the 200-day SMA. Not `lib.awesome`, which is the zero line, and not `lib.accel`.
22. **Gator awakening.** Williams, same book. Both absolute gaps were smaller yesterday and are larger today, and the lips are above the jaw. `lib.gator` is the expansion alone, with no prior contraction.

Twenty-two of these can be coded on the current OHLC bars. None of them
is a one-trade-a-year calendar.
