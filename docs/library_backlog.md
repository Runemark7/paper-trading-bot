# Library backlog (batch 9 and after)

Paper only. BTC only. Daily bars, long or flat, next-open fills, the
pre-registered 20% stop. These are candidates for a later catalog append.
They are not registered, and discovery will not claim them until they are
added to `LIBRARY` the same way batch 8 was.

Batch 8 appended 20 names after the existing 154. It did not change those
154, and it did not requeue them. The random farm stays paused.

Do not add a near-duplicate of a name already in the catalog. Batch 8
skipped, on purpose:

- The projection oscillator above 50. The 50 line of a one-standard-error
  band is the regression value. That sign is `lib.regline`.
- The homing pigeon. The second candle is black, so its body runs from its
  open down to a lower close. On this tape the open is at or below the prior
  close, which is the bottom of the first black body, so the second body
  cannot sit inside the first. The one gap that does put a smaller black
  body inside finishes below the 200-day SMA and before the score start.
  Zero trades. It is not a key.
- The SMI signal line. `lib.smi` is the zero line. A short average of SMI
  is the same kind of signal cross as `lib.rsi_sig` and `lib.tsi_sig`, and
  it can wait until the zero line has been tested. It is listed below as a
  later candidate, not as a key in this batch.
- MAMA versus FAMA. The paper's crossover is a second reading of the same
  Hilbert average. `lib.mama` is the close versus MAMA.
- A 50 percent Fibonacci hold. It is a neighbour of `lib.fibhold`.
- Another gator phase next to `lib.gator` and `lib.gator_wake`.
- Price above DEMA, or TEMA rising. `lib.dema` is DEMA rising. The slope of
  a triple EMA is TRIX.
- Donchian, turtle, Bollinger, Keltner, the SMA band, MACD and its histogram,
  the impulse system, RSI and its signal line and its failure swing, the
  slow stochastic, ConnorsRSI, the double seven, three lower closes, three
  white soldiers, the engulfing, the piercing line, the harami, the three
  outside up, the morning star, the hammer, the inverted hammer, the
  three-line strike, the belt hold, the matching low, rising three methods,
  the two-bar reversal, Williams %R, the stochastic cross, the stochastic
  pop, the stochastic momentum index, IBS, the oops reversal, Supertrend,
  ADX, the directional cross, Aroon and the 70/30 strong-trend reading, CCI,
  Vortex, choppiness, the random walk index, VHF, TRIX and its signal line,
  TSI and its signal line, the Chande oscillator and its signal line, the
  intraday momentum index, DeMarker and its signal line, Qstick and its
  signal line, the detrended price oscillator and its signal line, balance
  of power and its signal line, the psychological line and its signal line,
  the range expansion index, TD setup, TD countdown, TD Combo, the awesome
  oscillator and its saucer, the accelerator, the alligator, the gator and
  its awakening, VIDYA, FRAMA, MAMA, the zero-lag EMA, the super smoother,
  the instantaneous trendline, the cyber cycle and its zero line, the sine
  wave, the Fisher transform and its zero line, the center of gravity, the
  roofing filter, the Laguerre RSI, the Laguerre filter, T3, ALMA, the
  McGinley dynamic, KAMA, the efficiency ratio, the Hull average and the
  close versus that average, DEMA, the cloud and the tenkan/kijun cross,
  the three-line break, kagi, renko, time-series momentum, the variance
  ratio, the intermediate window, the regression slope, r-squared, the
  Spearman rank correlation, the Theil-Sen slope, the regression value, the
  standard-error band, the Fibonacci hold, Woodie's pivot, the floor pivot,
  Camarilla, Garman-Klass, the first half of the month, expiration week,
  the weekend, Monday, the turn of the month, and the Halloween window.
  Those are already seated or tested names. Batch 8 did not add a neighbour
  of any of them as a new key.
- Inverse-volatility position sizing is still not a signal. The book is
  all-in or flat.

`lib.matchlow` is two black candles whose lows match. A cent-exact low
never repeats on this tape, so "the same" is the published candle equal-band:
5 percent of the average high-low range of the prior five bars, applied to
the lows. `lib.belt` is a bullish belt hold. An exact open-at-low never
clears the 200-day SMA here, so a long body is longer than the average body
of the prior ten bars and the lower shadow is shorter than 10 percent of
the average range of those bars. The upper shadow is still no larger than
the body. Do not put the exact-equality tests back. They are the zero-trade
definitions this batch avoided.

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

1. **SMI signal line.** William Blau, *Technical Analysis of Stocks & Commodities* (1993). Long while SMI(10, 3, 3) is above its 9-day EMA. `lib.smi` is the zero line. The 10 and both smooths stay fixed. Not `lib.tsi_sig`.
2. **RVI above zero.** John Ehlers, *Cybernetic Analysis for Stocks and Futures* (2004). Long while the relative vigor index of length 10 is above zero. `lib.rvi` is the index above its signal line. The 10 stays fixed.
3. **Awesome oscillator rising.** Bill Williams, *Trading Chaos* (1995). Long while the awesome oscillator (5, 34) is above its previous value. `lib.awesome` is the zero line. Not `lib.saucer` and not `lib.accel`. The 5 and 34 stay fixed.
4. **Fast stochastic midpoint.** George Lane. Long while fast %K(14) is above 50. `lib.stoch` is %K above %D. `lib.stoch_pop` is the cross of 80. The 50 stays fixed. Not `lib.smi`.
5. **Elder bull power.** Alexander Elder, *Trading for a Living* (1993). Long while the high is above the 13-day EMA and that EMA is rising. `lib.elder_ray` is bear power (the low under a rising EMA). The 13 stays fixed.
6. **DeMark pivot.** Tom DeMark, *The New Science of Technical Analysis* (1994). If the prior close is below the prior open, the pivot is (high + 2 × low + close) / 4. If the prior close is above the prior open, it is (2 × high + low + close) / 4. If they are equal, it is (high + low + 2 × close) / 4. Long while the close is above that prior pivot and the 200-day SMA. Not `lib.floor_pivot` and not `lib.woodie`.
7. **T3 rising.** Tim Tillson. Long while T3(5) is above its previous value. The volume factor stays 0.70. `lib.t3` is the close above T3.
8. **VIDYA rising.** Tushar Chande. Long while VIDYA(20) is above its previous value. `lib.vidya` is the close above VIDYA. Not `lib.cmo`.
9. **ALMA rising.** Arnaud Legoux. Long while ALMA(9, 0.85, 6) is above its previous value. Offset and sigma stay fixed. `lib.alma` is the close above ALMA.
10. **McGinley rising.** John McGinley. Long while the McGinley dynamic of length 20 is above its previous value. `lib.mcginley` is the close above it.
11. **Zero-lag EMA rising.** John Ehlers. Long while the zero-lag EMA of length 20 is above its previous value. `lib.zlema` is the close above it.
12. **FRAMA rising.** John Ehlers. Long while FRAMA(16) is above its previous value. The alpha map stays the published one. `lib.frama` is the close above FRAMA.
13. **Super smoother rising.** John Ehlers. Long while the super smoother of length 10 is above its previous value. `lib.ssmooth` is the close above it. Not `lib.roof`, which is a high-pass passed through the smoother and read against zero.
14. **KAMA rising.** Perry Kaufman, *Smarter Trading* (1995). Long while KAMA(10, 2, 30) is above its previous value. `lib.kama` is the close above KAMA. Not `lib.eratio`, which thresholds the efficiency ratio.
15. **Laguerre RSI signal.** John Ehlers. Long while the Laguerre RSI at gamma 0.50 is above its 4-day SMA. `lib.laguerre` is the 0.5 line. Gamma stays 0.50. Not `lib.lfilter`.
16. **MAMA rising.** John Ehlers. Long while MAMA is above its previous value. The fast limit stays 0.5 and the slow limit stays 0.05. `lib.mama` is the close above MAMA. Not the FAMA cross.
17. **Instantaneous trendline rising.** John Ehlers, *Cybernetic Analysis for Stocks and Futures* (2004). Long while the instantaneous trendline is above its previous value. Alpha stays 0.07. `lib.itrend` is the close above the trendline.
18. **Bullish breakaway.** Steve Nison, *Japanese Candlestick Charting Techniques* (1991). A long black candle, then three smaller bodies whose lows take out that black low (this tape does not print an open below the prior low), then a white close back above the first close, above the 200-day SMA. Not `lib.morning` and not `lib.rising3`. Do not require an opening gap the tape never prints.
19. **Signed efficiency ratio.** Perry Kaufman, *Smarter Trading* (1995). The 10-day net change divided by the path length, long when that signed ratio is above +0.30. The 0.30 stays fixed. `lib.eratio` uses the absolute ratio and also requires the 200-day SMA. This one has no average filter, and a downtrend is not long.
20. **Schaff midline.** Doug Schaff. Long while the Schaff trend cycle of length 10 is above 50. `lib.schaff` is the cross up through 25 and the cross down through 75. The 50 stays fixed. The 23/50 MACD lengths stay fixed.
21. **Bandpass filter.** John Ehlers, *Cycle Analytics for Traders* (2013). A band-pass centered at 30 bars with bandwidth 0.3, long while the filter is above zero. The bandwidth stays fixed. Not `lib.cyber` and not `lib.roof`.
22. **SafeZone stop.** Chuck LeBeau, *Technical Traders Guide to Computer Analysis of the Futures Markets* (1992). The stop is the highest close of the last 20 days minus twice the average range of the last 10 down days (prior close minus the low, on a day that closed down). It only ratchets up. Long while the close is above that stop. The 2 stays fixed. Not `lib.supertrend` and not `lib.chandelier`.

Twenty-two of these can be coded on the current OHLC bars. None of them
is a one-trade-a-year calendar.
