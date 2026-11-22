# Library backlog (batch 14 and after)

Paper only. BTC only. Daily bars, long or flat, next-open fills, the
pre-registered 20% stop. These are candidates for a later catalog append.
They are not registered, and discovery will not claim them until they are
added to `LIBRARY` the same way batch 13 was.

Batch 13 appended 20 names after the existing 251. It did not change those
251, and it did not requeue them. The random farm stays paused.

Do not add a near-duplicate of a name already in the catalog. Batches 12 and
13 skipped, on purpose:

- Gap and go, the intraday drift sum, the session semi-variance, the failed
  break of the prior two lows, and the NR4 break. See the table below.

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
  the tower bottom, sign entropy, the absolute-return autocorrelation, the
  Hodges-Lehmann location, the return IQR, the prior-range width, two inside
  days, the Yang-Zhang excess variance, Rogers-Satchell against Parkinson,
  the volatility of the Parkinson terms, the prior week's midpoint, median
  runs, the reset CUSUM of squares, the prior month's high, the shadow
  imbalance, the Cox-Stuart sign test, the bipower jump share, the Williams
  volatility breakout, the smash day, the Raschke 3/10 oscillator, the
  same-calendar-month
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
| Gap and go | Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990) | The open has to clear yesterday's high. On this 24-hour tape it never does after the 2018-12-01 score start, so the center is never on and has no round-trips. |
| Intraday drift (sum of open-to-close returns) | Lou, Polk, and Skouras, *Journal of Financial Economics* (2019) | The sign of the 20-day sum is the signal of `lib.cmo` on every scored day (identical on-days), because the open is the prior close or within a basis point of it. |
| Session semi-variance, quiet | Markowitz, *Portfolio Selection* (1959) | The open-to-close downside RMS against its 100-day median is the signal of `lib.ddev` on every scored day (identical on-days). |
| Failed prior-low break | Crabel, same book as the two inside days | A low under the prior two lows with a higher close sits inside `lib.keyrev`: 98.3% agreement and 69% overlap of on-days at the center. |
| NR4 break (setup held until broken) | Crabel, same book | Contains the registered `lib.nr7` neighbour at n = 4: all 143 of its on-days, 98.3% agreement. Not a second key. |

## Ready for a later batch

Empty. Batch 13 took all twenty slots. Five listed candidates were dropped
(gap and go, the intraday drift sum, the session semi-variance, the failed
prior-low break, and the NR4 break). Five cited rules replaced them: the
Cox-Stuart sign test, the bipower jump share, Williams' volatility breakout,
Williams' smash day, and the Raschke 3/10 oscillator. A later batch needs
new cited, daily-OHLC, non-duplicate
rules; check each against every registered center and grid point before it
is pre-registered. Lookbacks stay inside the 2017-08-17 warm-up so the score
start can remain 2018-12-01.
