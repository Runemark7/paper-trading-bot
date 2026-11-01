# Library backlog (batch 13 and after)

Paper only. BTC only. Daily bars, long or flat, next-open fills, the
pre-registered 20% stop. These are candidates for a later catalog append.
They are not registered, and discovery will not claim them until they are
added to `LIBRARY` the same way batch 12 was.

Batch 12 appended 20 names after the existing 231. It did not change those
231, and it did not requeue them. The random farm stays paused.

Do not add a near-duplicate of a name already in the catalog. Batch 12
skipped, on purpose:

- The one-sided CUSUM. Page's `S = max(0, S + r - k)`, with `k` the mean of
  the prior 50 log returns, stays above zero after the warm-up on the
  2017-2026 tape. The center's only round-trips are the pre-registered 20%
  stop. It is not a second key.
- The tower bottom. A long black candle, two smaller bodies, and a long
  white close through the black midpoint is another long-black then
  long-white reversal next to `lib.morning` and `lib.engulf`.
- Durbin-Watson on the residuals of the price around a short trend. On
  this tape the statistic stays below 2 (median about 0.81 since
  2018-12-01), so the rule is the close above the 200-day SMA. It agreed
  with `lib.sma200` on 99.2% of scored days.
- The overnight return sum. A 24-hour session has no overnight gap: the
  daily open equals the prior close on about 40% of days, and the mean
  gap is about one basis point. That series is bid-ask noise.
- Donchian, turtle, Bollinger and its bandwidth, percent b, and the squeeze,
  Keltner, the STARC band, the SMA band, MACD and its histogram, the impulse
  system, RSI and its signal line and its failure swing, the slow
  stochastic, the fast stochastic above 50, ConnorsRSI, the double seven,
  three lower closes, the end of a five-day losing streak, three white
  soldiers, the advance block, three higher highs, three higher lows, the
  engulfing, the piercing line, the harami, the three inside up, the three
  outside up, the morning star, the hammer, the inverted hammer, the
  three-line strike, the belt hold, the matching low, the meeting lines,
  the separating lines, the stick sandwich, bullish kicking, the breakaway,
  rising three methods, the two-bar reversal, the key reversal, the 80-20
  bar, the hook, the outside close, the wide-range up day, Williams %R, the
  stochastic cross, the stochastic pop, the stochastic momentum index, IBS,
  the close location value, the upper-half close count, the oops reversal,
  Supertrend, the chandelier, the SafeZone stop, the Kase DevStop, the
  Chande Kroll stop, ADX, the directional cross, the holy grail, Aroon and
  the 70/30 strong-trend reading, CCI, Vortex, choppiness, the random walk
  index, VHF, the Gopalakrishnan range index, TRIX and its signal line, TSI
  and its signal line, the Chande oscillator and its signal line, the
  intraday momentum index, the momentum pinball, DeMarker and its signal
  line, Qstick and its signal line, the detrended price oscillator and its
  signal line, balance of power and its signal line, the psychological line
  and its signal line, the trend intensity index, Elder bull power, Elder
  bear power, the range expansion index, TD setup, TD countdown, TD Combo,
  the awesome oscillator, its slope, and its saucer, the accelerator, the
  alligator, the gator and its awakening, VIDYA, FRAMA and its slope, the
  fractal dimension, MAMA and its slope, the zero-lag EMA and its slope, the
  super smoother and its slope, the instantaneous trendline and its slope,
  the cyber cycle and its zero line, the sine wave, the Fisher transform and
  its zero line, the center of gravity, the roofing filter, the band-pass,
  the homodyne dominant-cycle lag, the Laguerre RSI, the Laguerre filter, T3
  and its slope, ALMA and its slope, the McGinley dynamic, KAMA, the
  efficiency ratio and the signed ratio, the Hull average and the close
  versus that average, Heikin-Ashi, DEMA, the cloud and the tenkan/kijun
  cross, the three-line break, kagi, renko, the point-and-figure double top,
  time-series momentum, the variance ratio, lag-1 autocorrelation, the lag-2
  partial autocorrelation, the run count, the turning-point count, the
  intermediate window, the regression slope, r-squared, the Spearman rank
  correlation, the Theil-Sen slope, the regression value, the standard-error band, the
  percentile channel, the Donchian midpoint, the Fibonacci hold, Woodie's
  pivot, the floor pivot, the DeMark pivot, Camarilla, Garman-Klass,
  Rogers-Satchell, Yang-Zhang, Parkinson and Parkinson against the
  close-to-close variance, the Corwin-Schultz spread, the Roll spread,
  downside deviation, the semi-variance share, the Hampel MAD, the
  short-to-long volatility ratio, the true-range imbalance, realized
  skewness, open-to-close skewness, realized kurtosis, the rescaled-range
  Hurst exponent, the MAX filter, the window maximum drawdown, Chaikin
  volatility, the Schaff cross and the Schaff midline, the relative vigor
  index and its zero line, the leverage correlation, the close channel,
  the same-calendar-month
  sign, the first half of the month, expiration week, the weekend, Monday,
  the turn of the month, and the Halloween window. Those are already seated
  or tested names. Batch 12 did not add a neighbour of any of them as a new
  key.
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
| One-sided CUSUM | Page, "Continuous Inspection Schemes", *Biometrika* (1954) | `S = max(0, S + r - k)` with the prior-mean allowance stays above zero on this tape. The center does not complete three signal round-trips. |
| Durbin-Watson of the price residual | Durbin and Watson, *Biometrika* (1950) | Below 2 on 99% of days since 2018-12-01. The rule is the close above the 200-day SMA (99.2% agreement with `lib.sma200`). |
| Overnight return sum | Lou, Polk, and Skouras, *Journal of Financial Economics* (2019) | The daily open is the prior close on this 24-hour tape. The gap is bid-ask noise. |

## Ready for a later batch

Each line is one rule, with the source and the parameters to pre-register.
Lookbacks stay inside the 2017-08-17 warm-up so the score start can remain
2018-12-01. Neighbours stay one-at-a-time, as in the catalog. None of these
is a parameter neighbour of a name already registered.

1. **Tower bottom.** Nison, *Japanese Candlestick Charting Techniques* (1991). A long black candle, two smaller bodies, then a long white close through the black candle's midpoint, above the 200-day SMA. The long-body test is the one `lib.belt` already uses. Not `lib.morning` and not `lib.engulf`. The long is the white close.
2. **Sign entropy, quiet.** Shannon, "A Mathematical Theory of Communication", *Bell System Technical Journal* (1948). The binary entropy of up-closes versus down-closes over the last 20 closes, long while it is below its 100-day median and the close is above the close 20 days ago. A tie is a down step. The median length stays 100. Not `lib.turns` and not `lib.runs`.
3. **Absolute-return autocorrelation.** Ding, Granger, and Engle, "A long memory property of stock market returns and a new model", *Journal of Empirical Finance* (1993). The lag-1 autocorrelation of absolute log returns over 20 days, long while it is above its 100-day median and the close is above the 200-day SMA. The median length stays 100. Not `lib.ac1` and not `lib.volr`.
4. **Hodges-Lehmann location.** Hodges and Lehmann, "Estimates of Location Based on Rank Tests", *Annals of Mathematical Statistics* (1963). The median of the pairwise averages of the last 20 log returns, long while it is positive. The zero stays fixed. Not `lib.tsmom30d`, which is the sign of the total return.
5. **Interquartile range, quiet.** Tukey, *Exploratory Data Analysis* (1977). The interquartile range of the last 20 log returns, long while it is below its 100-day median and the close is above the 200-day SMA. The median length stays 100. Not `lib.hampel`.
6. **Donchian width, quiet.** Donchian; the width is the prior high minus the prior low. Long while that 20-day width is below its 100-day median and the close is above the midpoint of the same prior range. Today is outside the window. The median length stays 100. Not `lib.dmid` and not `lib.qdon`.
7. **Two inside days.** Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990). Two consecutive inside bars, then a later close above the second bar's high, above the 200-day SMA. A close through the second bar's low drops the setup. The long is that break close. Not `lib.inside_day`.
8. **Gap and go.** Crabel, same book. The open is above yesterday's high and the close is above the open, above the 200-day SMA. The long is that close. Not `lib.wide`. The open has to clear yesterday's high, so a one-basis-point open gap is not enough.
9. **Overnight variance gap, quiet.** Yang and Zhang, "Drift-Independent Volatility Estimation Based on High, Low, Open, and Close Prices", *Journal of Business* (2000). The 20-day Yang-Zhang variance minus the 20-day close-to-close variance, long while that difference is below its 100-day median and the close is above the 200-day SMA. The median length stays 100. Not `lib.yz`.
10. **Rogers-Satchell below Parkinson.** Rogers and Satchell, "Estimating Variance from High, Low and Closing Prices", *Annals of Applied Probability* (1991), against Parkinson, *Journal of Business* (1980). Long while the 20-day Rogers-Satchell variance is below the 20-day Parkinson variance and the close is above the 200-day SMA. Not `lib.parkcc` and not `lib.rsvol`.
11. **Volatility of volatility, quiet.** Engle, "Autoregressive Conditional Heteroscedasticity with Estimates of the Variance of United Kingdom Inflation", *Econometrica* (1982). The standard deviation of the last 20 daily Parkinson terms, long while it is below its 100-day median and the close is above the 200-day SMA. The median length stays 100. Not `lib.park`.
12. **Intraday drift.** Lou, Polk, and Skouras, "A tug of war: Overnight versus intraday expected returns", *Journal of Financial Economics* (2019). Long while the sum of the last 20 open-to-close log returns is positive. Today's open and close are both known at the close. This is the session move, not the open gap.
13. **Prior week's midpoint.** Rhea, *The Dow Theory* (1932). Long while the close is above the midpoint of the prior completed Monday-Sunday week and above the 200-day SMA. A week that has not ended is not the reference. Not `lib.wkhigh` and not `lib.dmid`.
14. **NR4 break.** Crabel, same book as the two inside days. A bar whose high-low range is the narrowest of the last four, then a later close above that bar's high, above the 200-day SMA. A close through that bar's low drops the setup. The long is the break close. Not `lib.nr7`.
15. **Median runs.** Swed and Eisenhart, "Tables for Testing Randomness of Grouping in a Sequence of Alternatives", *Annals of Mathematical Statistics* (1943). Runs of closes above and below the median of the last 20 closes. Long while that count is below 10 and the close is above the median. The 10 stays fixed. Not `lib.runs`, which counts up and down steps.
16. **CUSUM of squares, reset.** Brown, Durbin, and Evans, "Techniques for Testing the Constancy of Regression Relationships over Time", *Journal of the Royal Statistical Society* (1975). The cumulative sum of squared demeaned log returns, reset to zero on the day it exceeds the mean of the prior 50 squared shocks. Long while it is at or below that mean and the close is above the 200-day SMA. The reset is what the level CUSUM lacked. The 50 stays fixed.
17. **Prior month's high.** A completed UTC month. Long while the close is above the high of the prior completed month and above the 200-day SMA. The month that is still in progress is not the reference. Not `lib.faber10m` and not a one-trade-a-year calendar: the long lasts as long as the close holds that high.
18. **Session semi-variance, quiet.** Markowitz, *Portfolio Selection* (1959), applied to the open-to-close log return. The root mean square of the negative session returns over 20 days, long while it is below its 100-day median and the close is above the 200-day SMA. The median length stays 100. Not `lib.svar` and not `lib.ocskew`.
19. **Lower-shadow imbalance.** Nison's shadows, counted as a ratio rather than a single hammer. The average over 10 days of (lower shadow - upper shadow) / range, long while it is positive and the close is above the 200-day SMA. A zero range contributes 0. The zero stays fixed. Not `lib.hammer` and not `lib.uhalf`.
20. **Failed prior-low break.** Crabel's reversal, apart from the hook. The low takes out the low of the prior two bars and the close finishes above yesterday's close, above the 200-day SMA. The long is that close. Not `lib.hook` and not `lib.oops`.

Twenty of these can be coded on the current OHLC bars. None of them
is a one-trade-a-year calendar.
