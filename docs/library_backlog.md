# Library backlog (batch 12 and after)

Paper only. BTC only. Daily bars, long or flat, next-open fills, the
pre-registered 20% stop. These are candidates for a later catalog append.
They are not registered, and discovery will not claim them until they are
added to `LIBRARY` the same way batch 11 was.

Batch 11 appended 20 names after the existing 211. It did not change those
211, and it did not requeue them. The random farm stays paused.

Do not add a near-duplicate of a name already in the catalog. Batch 11
skipped, on purpose:

- The regression slope rising. `lib.linreg` is already the sign of the
  20-day least-squares slope. Requiring that slope to also stand above its
  previous value is the rising-line reading this library stopped adding.
- The on-neck line. A white close within 5 percent of the prior five ranges
  of the prior black low does not occur on the 2017-2026 tape, so the
  center would have no round-trip.
- Donchian, turtle, Bollinger and its bandwidth and the squeeze, Keltner,
  the SMA band, MACD and its histogram, the impulse system, RSI and its
  signal line and its failure swing, the slow stochastic, the fast
  stochastic above 50, ConnorsRSI, the double seven, three lower closes,
  the end of a five-day losing streak, three white soldiers, three higher
  lows, the engulfing, the piercing line, the harami, the three inside up,
  the three outside up, the morning star, the hammer, the inverted hammer,
  the three-line strike, the belt hold, the matching low, the meeting
  lines, the separating lines, the stick sandwich, bullish kicking, the
  breakaway, rising three methods, the two-bar reversal, the key reversal,
  the 80-20 bar, the hook, the outside close, Williams %R, the stochastic
  cross, the stochastic pop, the stochastic momentum index, IBS, the close
  location value, the oops reversal, Supertrend, the chandelier, the
  SafeZone stop, the Kase DevStop, the Chande Kroll stop, ADX, the
  directional cross, the holy grail, Aroon and the 70/30 strong-trend
  reading, CCI, Vortex, choppiness, the random walk index, VHF, the
  Gopalakrishnan range index, TRIX and its signal line, TSI and its signal
  line, the Chande oscillator and its signal line, the intraday momentum
  index, the momentum pinball, DeMarker and its signal line, Qstick and its
  signal line, the detrended price oscillator and its signal line, balance
  of power and its signal line, the psychological line and its signal line,
  the trend intensity index, Elder bull power, Elder bear power, the range
  expansion index, TD setup, TD countdown, TD Combo, the awesome oscillator,
  its slope, and its saucer, the accelerator, the alligator, the gator and
  its awakening, VIDYA, FRAMA and its slope, the fractal dimension, MAMA
  and its slope, the zero-lag EMA and its slope, the super smoother and its
  slope, the instantaneous trendline and its slope, the cyber cycle and its
  zero line, the sine wave, the Fisher transform and its zero line, the
  center of gravity, the roofing filter, the band-pass, the homodyne
  dominant-cycle lag, the Laguerre RSI, the Laguerre filter, T3 and its
  slope, ALMA and its slope, the McGinley dynamic, KAMA, the efficiency
  ratio and the signed ratio, the Hull average and the close versus that
  average, Heikin-Ashi, DEMA, the cloud and the tenkan/kijun cross, the
  three-line break, kagi, renko, the point-and-figure double top,
  time-series momentum, the variance ratio, lag-1 autocorrelation, the run
  count, the intermediate window, the regression slope, r-squared, the
  Spearman rank correlation, the Theil-Sen slope, the regression value, the
  standard-error band, the percentile channel, the Donchian midpoint, the
  Fibonacci hold, Woodie's pivot, the floor pivot, the DeMark pivot,
  Camarilla, Garman-Klass, Rogers-Satchell, Yang-Zhang, Parkinson,
  downside deviation, realized skewness, realized kurtosis, the MAX filter,
  Chaikin volatility, the Schaff cross and the Schaff midline, the relative
  vigor index and its zero line, the same-calendar-month sign, the first
  half of the month, expiration week, the weekend, Monday, the turn of the
  month, and the Halloween window. Those are already seated or tested
  names. Batch 11 did not add a neighbour of any of them as a new key.
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
| On-neck line | Nison, *Japanese Candlestick Charting Techniques* (1991) | The 5 percent equal-band of the prior black low never prints on this tape. Zero round-trips. |

## Ready for a later batch

Each line is one rule, with the source and the parameters to pre-register.
Lookbacks stay inside the 2017-08-17 warm-up so the score start can remain
2018-12-01. Neighbours stay one-at-a-time, as in the catalog. None of these
is a parameter neighbour of a name already registered.

1. **Hampel MAD quiet uptrend.** Hampel, "The Influence Curve and its Role in Robust Estimation", *Journal of the American Statistical Association* (1974). The median absolute deviation of the last 20 log returns, scaled by 1.4826, long while it is below its 100-day median and the close is above the 200-day SMA. The scale and the median length stay fixed. Not the standard deviation inside `lib.vol_trend` and not `lib.ddev`.
2. **Semi-variance share.** Markowitz, *Portfolio Selection* (1959). Downside variance divided by total variance of the last 20 log returns. Long while that share is below its 100-day median and the close is above the 200-day SMA. The median length stays 100. Not `lib.ddev`, which is the level of the downside deviation.
3. **Rescaled-range Hurst.** Hurst, "Long-Term Storage Capacity of Reservoirs", *Transactions of the American Society of Civil Engineers* (1951); the R/S statistic as in Mandelbrot and Wallis, *Water Resources Research* (1969). The Hurst exponent of the last 64 log returns, long while it is above one half and the close is above the 200-day SMA. The one half stays fixed. Not `lib.fdim`.
4. **Volatility ratio.** Schwager, *Schwager on Futures: Technical Analysis* (1996). The 10-day standard deviation of log returns divided by the 100-day standard deviation. Long while the ratio is below 1 and the close is above the 200-day SMA. The 1 and the 100 stay fixed. The short window is the grid. Not `lib.vol_trend`.
5. **STARC upper band.** Manning Stoller. Long while the close is above the n-day SMA plus one Wilder ATR(n). The 1 stays fixed. Not `lib.keltner20` and not `lib.wilder_vol`.
6. **Bollinger percent b.** Bollinger, *Bollinger on Bollinger Bands* (2001). `%b = (close - lower) / (upper - lower)` of the 20-day, 2-deviation bands. Long while `%b` is above 0.8. The 0.8 and the 2 stay fixed. The band length is the grid. Not `lib.bb_break` and not `lib.pctile`.
7. **Corwin-Schultz spread, quiet.** Corwin and Schultz, "A Simple Way to Estimate Bid-Ask Spreads from Daily High and Low Prices", *Journal of Finance* (2012). The high-low spread over 20 days, long while it is below its 100-day median and the close is above the 200-day SMA. No volume. Not `lib.park`.
8. **Roll spread, quiet.** Roll, "A Simple Implicit Measure of the Effective Bid-Ask Spread in an Efficient Market", *Journal of Finance* (1984). Twice the square root of the negative lag-1 autocovariance of price changes over 20 days. A positive autocovariance is a zero spread. Long while the spread is below its 100-day median and the close is above the 200-day SMA. Not `lib.ac1`, which trades the sign of the correlation.
9. **Partial autocorrelation at lag 2.** Box and Jenkins, *Time Series Analysis: Forecasting and Control* (1970). The lag-2 partial autocorrelation of the last 30 log returns, long while it is positive. Lag 1 is removed before the lag-2 coefficient. Not `lib.ac1`.
10. **Fewer turning points.** Kendall, *Time-Series* (1973). A turning point is a close above both neighbours or below both. Long while the last 20 closes contain fewer than 12 turning points and the close is above the close 20 days ago. The 12 stays fixed. Not `lib.runs`.
11. **Overnight drift.** Lou, Polk, and Skouras, "A Tug of War: Overnight Versus Intraday Expected Returns", *Journal of Financial Economics* (2019). Long while the sum of the last 20 close-to-open log returns is positive. Today's open is known before today's close. Not `lib.tsmom30d`.
12. **One-sided CUSUM.** Page, "Continuous Inspection Schemes", *Biometrika* (1954). `S = max(0, S + r - k)` where `k` is the mean of the prior 50 log returns and `r` is today's log return. Long while `S` is above 0. The 50 stays fixed. The allowance is that prior mean, not a future mean.
13. **Advance block.** Nison, *Japanese Candlestick Charting Techniques* (1991). Three white candles whose upper shadows get longer, above the 200-day SMA. The long is the third close. Not `lib.soldiers`.
14. **Tower bottom.** Nison, same book. A long black candle, two smaller bodies, then a long white close through the black midpoint, above the 200-day SMA. The long-body test is the one `lib.belt` already uses. Not `lib.morning` and not `lib.engulf`.
15. **Upper-half close count.** Arms' close location, counted rather than averaged. Long while at least 6 of the last 10 bars closed in the upper half of their own high-low range. The 6 stays fixed. Not `lib.clv` and not `lib.psych`.
16. **True-range imbalance.** Wilder, *New Concepts in Technical Trading Systems* (1978). Over 20 days, the average true range on up closes minus the average true range on down closes. Long while that difference is positive and the close is above the 200-day SMA. Not `lib.qdon` and not `lib.di_cross`.
17. **Parkinson below the close-to-close variance.** Parkinson, *Journal of Business* (1980). Long while the 20-day Parkinson variance is below the 20-day variance of log closes and the close is above the 200-day SMA. Not `lib.park`, which compares Parkinson with its own median.
18. **Wide-range up day.** Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990). Today's high-low range is above the average range of the prior 10 bars, the close is above the open, and the close is above the 200-day SMA. The long is that close. Not `lib.nr7` and not `lib.outclose`.
19. **Three higher highs.** Rhea, *The Dow Theory* (1932). Three consecutive higher highs and a close above the 200-day SMA. The long is that third close. Not `lib.lows3` and not `lib.soldiers`.
20. **Durbin-Watson of the regression residual.** Durbin and Watson, "Testing for Serial Correlation in Least Squares Regression", *Biometrika* (1950). The Durbin-Watson statistic of the last 20 regression residuals, long while it is below 2 and the close is above the 200-day SMA. The 2 stays fixed. Not `lib.ac1` and not `lib.rsquared`.
21. **Open-to-close skewness.** Amaya, Christoffersen, Jacobs, and Vasquez, *Journal of Financial Economics* (2015), applied to the session return rather than the close-to-close return. The skewness of the last 20 open-to-close log returns, long while it is below zero and the close is above the 200-day SMA. The zero stays fixed. Not `lib.skew`.
22. **Window maximum drawdown, quiet.** Grossman and Zhou, "Optimal Investment Strategies for Controlling Drawdowns", *Mathematical Finance* (1993). The worst peak-to-trough decline of the close inside 20 days, long while that drawdown is below its 100-day median and the close is above the 200-day SMA. The median length stays 100. Not `lib.ulcer`, which is the root-mean-square drawdown.

Twenty-two of these can be coded on the current OHLC bars. None of them
is a one-trade-a-year calendar.
