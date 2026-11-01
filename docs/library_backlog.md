# Library backlog (batch 11 and after)

Paper only. BTC only. Daily bars, long or flat, next-open fills, the
pre-registered 20% stop. These are candidates for a later catalog append.
They are not registered, and discovery will not claim them until they are
added to `LIBRARY` the same way batch 10 was.

Batch 10 appended 20 names after the existing 191. It did not change those
191, and it did not requeue them. The random farm stays paused.

Do not add a near-duplicate of a name already in the catalog. Batch 10
skipped, on purpose:

- The thrusting line. A white candle that opens under the prior black close
  and closes inside that body, but below its midpoint, is the failed half
  of `lib.pierce`. Only the midpoint threshold differs.
- The Ichimoku cloud twist. Span A above span B is already
  `lib.ichimoku_cloud`. `lib.ichimoku` is the close versus that cloud, and
  `lib.ichimoku_tk` is the tenkan/kijun cross.
- Donchian, turtle, Bollinger, Keltner, the SMA band, MACD and its histogram,
  the impulse system, RSI and its signal line and its failure swing, the
  slow stochastic, the fast stochastic above 50, ConnorsRSI, the double
  seven, three lower closes, three white soldiers, the engulfing, the
  piercing line, the harami, the three inside up, the three outside up, the
  morning star, the hammer, the inverted hammer, the three-line strike, the
  belt hold, the matching low, the meeting lines, the separating lines, the
  stick sandwich, the breakaway, rising three methods, the two-bar reversal,
  the key reversal, the 80-20 bar, the hook, the outside close, Williams %R,
  the stochastic cross, the stochastic pop, the stochastic momentum index,
  IBS, the oops reversal, Supertrend, the chandelier, the SafeZone stop, the
  Kase DevStop, ADX, the directional cross, the holy grail, Aroon and the
  70/30 strong-trend reading, CCI, Vortex, choppiness, the random walk index,
  VHF, the Gopalakrishnan range index, TRIX and its signal line, TSI and its
  signal line, the Chande oscillator and its signal line, the intraday
  momentum index, the momentum pinball, DeMarker and its signal line, Qstick
  and its signal line, the detrended price oscillator and its signal line,
  balance of power and its signal line, the psychological line and its
  signal line, the trend intensity index, Elder bull power, Elder bear power,
  the range expansion index, TD setup, TD countdown, TD Combo, the awesome
  oscillator, its slope, and its saucer, the accelerator, the alligator, the
  gator and its awakening, VIDYA, FRAMA and its slope, MAMA and its slope,
  the zero-lag EMA and its slope, the super smoother and its slope, the
  instantaneous trendline and its slope, the cyber cycle and its zero line,
  the sine wave, the Fisher transform and its zero line, the center of
  gravity, the roofing filter, the band-pass, the homodyne dominant-cycle
  lag, the Laguerre RSI, the Laguerre filter, T3 and its slope, ALMA and its
  slope, the McGinley dynamic, KAMA, the efficiency ratio and the signed
  ratio, the Hull average and the close versus that average, Heikin-Ashi,
  DEMA, the cloud and the tenkan/kijun cross, the three-line break, kagi,
  renko, the point-and-figure double top, time-series momentum, the variance
  ratio, the intermediate window, the regression slope, r-squared, the
  Spearman rank correlation, the Theil-Sen slope, the regression value, the
  standard-error band, the percentile channel, the Fibonacci hold, Woodie's
  pivot, the floor pivot, the DeMark pivot, Camarilla, Garman-Klass,
  Rogers-Satchell, Yang-Zhang, Chaikin volatility, the Schaff cross and the
  Schaff midline, the relative vigor index and its zero line, the first half
  of the month, expiration week, the weekend, Monday, the turn of the month,
  and the Halloween window. Those are already seated or tested names.
  Batch 10 did not add a neighbour of any of them as a new key.
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

1. **Parkinson quiet uptrend.** Parkinson, "The Extreme Value Method for Estimating the Variance of the Rate of Return", *Journal of Business* (1980). One day is `ln(H/L)^2 / (4 ln 2)`. Long while the 20-day mean is below its 100-day median and the close is above the 200-day SMA. The median length stays 100. Not `lib.gk` (that subtracts an open-close term), not `lib.rsvol`, and not `lib.yz`.
2. **Realized skewness.** Amaya, Christoffersen, Jacobs, and Vasquez, "Does realized skewness predict the cross-section of equity returns?", *Journal of Financial Economics* (2015). The skewness of the last 20 daily log returns, long while it is below zero and the close is above the 200-day SMA. The zero stays fixed. Not a variance median.
3. **Realized kurtosis.** The same paper measures the fourth moment. Long while the excess kurtosis of the last 20 daily log returns is below its 100-day median and the close is above the 200-day SMA. The median length stays 100. Not the skewness rule and not `lib.yz`.
4. **Lag-1 autocorrelation.** Lo and MacKinlay, "Stock Market Prices Do Not Follow Random Walks", *Review of Financial Studies* (1988). Long while the autocorrelation of the last 20 daily log returns at lag 1 is positive. Not `lib.vratio`, which is a ratio of variances, and not `lib.tsmom30d`, which is the sign of the total return.
5. **MAX-effect filter.** Bali, Cakici, and Whitelaw, "Maxing out: Stocks as lotteries and the cross-section of expected returns", *Journal of Financial Economics* (2011). Long while the largest one-day simple return of the prior 21 sessions is below its 100-day median and the close is above the 200-day SMA. Today is outside the 21-day window. The median length stays 100. Not `lib.rev1m`.
6. **Downside-deviation quiet uptrend.** Sortino and van der Meer, "Downside Risk", *Journal of Portfolio Management* (1991). The root mean square of the negative daily log returns over 20 days, zeros included for the up days. Long while that deviation is below its 100-day median and the close is above the 200-day SMA. The median length stays 100. Not total variance.
7. **Fractal-dimension trend.** Ehlers, "FRAMA", *Technical Analysis of Stocks & Commodities*. The fractal dimension of the last 16 bars, long while it is below 1.4 and the close is above the 200-day SMA. The 1.4 stays fixed. Not `lib.frama`, which folds the dimension into an average.
8. **Chande Kroll stop.** Chande and Kroll, *The New Technical Trader* (1994). The preliminary long stop is the lowest low of 10 days plus 1 ATR(10). The stop used is the lowest of that preliminary stop over the last 9 days. Long while the close is above it. The 1 and the 9 stay fixed. The ATR length is the grid. Not `lib.chandelier`, not `lib.kase`, and not `lib.safezone`.
9. **Completed-week rising swing.** Rhea, *The Dow Theory* (1932). A UTC week ends on Sunday. Long while the just-completed week's high and low are both above the prior completed week's high and low. The decision uses only finished weeks. Not `lib.tsmom1w` and not a rolling five-day Donchian.
10. **Inside-week break.** A completed UTC week whose high and low sit inside the prior completed week, then long on a later daily close above that inside week's high, above the 200-day SMA. The long is the break close. Not `lib.inside_day`.
11. **Prior-week high break.** Long while the daily close is above the high of the prior completed UTC week and above the 200-day SMA. The week is calendar-aligned, not a rolling five-day window. Not `lib.donchian20`.
12. **Donchian midline.** The midpoint of the prior 20-day high and low. Long while the close is above that midpoint. Today is outside the window. Not the channel break (`lib.donchian20`) and not the 61.8 percent hold (`lib.fibhold`).
13. **Fewer runs, up window.** Mood, "The Distribution Theory of Runs", *Annals of Mathematical Statistics* (1940). A run is a streak of up or down closes. Long while the number of runs in the last 20 closes is below 10 and the close is above the close 20 days ago. The 10 stays fixed. Not `lib.eratio`, which is the net change over the path length.
14. **Same-calendar-month seasonality.** Heston and Sadka, "Seasonality in the Cross-Section of Expected Stock Returns", *Journal of Financial Economics* (2008). Long on each day of a UTC month when that same month last year had a positive close-to-close return. Not `lib.halloween` and not `lib.tom`. A month is not one trade a year.
15. **Close-location trend.** Arms' close location value, `(2 * close - high - low) / (high - low)`, averaged over 10 days. Long while that average is above zero. The zero stays fixed. Not `lib.ibs`, which buys a low IBS and sells a high one.
16. **On-neck line.** Nison, *Japanese Candlestick Charting Techniques* (1991). A white candle closes at the prior black candle's low, within the same 5 percent equal-band `lib.matchlow` uses, above the 200-day SMA. Not `lib.pierce` and not `lib.meet`.
17. **Bullish kicking.** Nison, same book. A black marubozu followed by a white marubozu. Use the long-body and very-short-shadow ratios already used by `lib.belt`, because an exact open-at-extreme does not clear this tape. The long is the white close, above the 200-day SMA. Not `lib.belt` and not `lib.separate`.
18. **Regression slope rising.** The 20-day least-squares slope is positive and above its previous value. Not `lib.linreg`, which is the sign alone, and not `lib.rsquared`.
19. **End of a five-day losing streak.** Jegadeesh, "Evidence of Predictable Behavior of Security Returns", *Journal of Finance* (1990), the daily form of the short-horizon reversal. Five lower closes, then a higher close, above the 200-day SMA. The 5 stays fixed. The long is that higher close. Not `lib.three_dn`, which holds during the decline, and not `lib.rev1m`, which is the 21-day sign.
20. **ATR-quiet Donchian break.** Wilder ATR(14) below its 100-day median and the close above the prior 20-day high. The 14, the 100, and the break window stay as the center; the ATR length is the grid. Not `lib.bb_squeeze`, which is a Bollinger band inside a Keltner band, and not `lib.donchian20` alone.
21. **Three rising lows.** Rhea, *The Dow Theory*. Three consecutive higher lows and a close above the 200-day SMA. The long is that third close. Not `lib.fractal` and not `lib.soldiers`.
22. **Bollinger bandwidth expansion.** Bollinger, *Bollinger on Bollinger Bands*. The bandwidth is `(upper - lower) / middle` of the 20-day, 2-standard-deviation bands. Long while that bandwidth's 10-day rate of change is above zero and the close is above the 200-day SMA. The 2 and the 10 stay fixed. The band length is the grid. Not `lib.cv`, which is the raw high-low range, and not `lib.bb_squeeze`.

Twenty-two of these can be coded on the current OHLC bars. None of them
is a one-trade-a-year calendar.
