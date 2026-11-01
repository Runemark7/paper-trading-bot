# Library backlog (batch 10 and after)

Paper only. BTC only. Daily bars, long or flat, next-open fills, the
pre-registered 20% stop. These are candidates for a later catalog append.
They are not registered, and discovery will not claim them until they are
added to `LIBRARY` the same way batch 9 was.

Batch 9 appended 17 names after the existing 174. It did not change those
174, and it did not requeue them. The random farm stays paused.

Do not add a near-duplicate of a name already in the catalog. Batch 9
skipped, on purpose:

- The SMI signal line. `lib.smi` is the zero line. A short average of SMI
  is the same kind of signal cross as `lib.rsi_sig` and `lib.tsi_sig`.
- The Laguerre RSI signal line. `lib.laguerre` is the 0.5 line. A short
  average of that RSI is the same kind of cross.
- VIDYA rising. The smoothing weight is `2 / (n + 1)` times `|CMO| / 100`,
  which stays inside (0, 1). The line then rises on exactly the bars the
  close is above it. That sign is `lib.vidya`. Zero days differ on this tape.
- KAMA rising. The squared smoothing constant stays inside (0, 1) for the
  published 2 and 30. The line rises on exactly the bars the close is above
  it. That sign is `lib.kama`.
- The McGinley slope. The adjustment overshoots only when
  `n * (close / average) ** 4` drops below 1. On this tape that is one bar.
  It is not a second key next to `lib.mcginley`.
- MAMA versus FAMA. The paper's crossover is a second reading of the same
  Hilbert average. `lib.mama` is the close versus MAMA. `lib.mama_up` is
  MAMA rising.
- Donchian, turtle, Bollinger, Keltner, the SMA band, MACD and its histogram,
  the impulse system, RSI and its signal line and its failure swing, the
  slow stochastic, the fast stochastic above 50, ConnorsRSI, the double
  seven, three lower closes, three white soldiers, the engulfing, the
  piercing line, the harami, the three outside up, the morning star, the
  hammer, the inverted hammer, the three-line strike, the belt hold, the
  matching low, the breakaway, rising three methods, the two-bar reversal,
  Williams %R, the stochastic cross, the stochastic pop, the stochastic
  momentum index, IBS, the oops reversal, Supertrend, the chandelier, the
  SafeZone stop, ADX, the directional cross, Aroon and the 70/30
  strong-trend reading, CCI, Vortex, choppiness, the random walk index, VHF,
  TRIX and its signal line, TSI and its signal line, the Chande oscillator
  and its signal line, the intraday momentum index, DeMarker and its signal
  line, Qstick and its signal line, the detrended price oscillator and its
  signal line, balance of power and its signal line, the psychological line
  and its signal line, Elder bull power, Elder bear power, the range
  expansion index, TD setup, TD countdown, TD Combo, the awesome oscillator,
  its slope, and its saucer, the accelerator, the alligator, the gator and
  its awakening, VIDYA, FRAMA and its slope, MAMA and its slope, the
  zero-lag EMA and its slope, the super smoother and its slope, the
  instantaneous trendline and its slope, the cyber cycle and its zero line,
  the sine wave, the Fisher transform and its zero line, the center of
  gravity, the roofing filter, the band-pass, the Laguerre RSI, the
  Laguerre filter, T3 and its slope, ALMA and its slope, the McGinley
  dynamic, KAMA, the efficiency ratio and the signed ratio, the Hull
  average and the close versus that average, DEMA, the cloud and the
  tenkan/kijun cross, the three-line break, kagi, renko, time-series
  momentum, the variance ratio, the intermediate window, the regression
  slope, r-squared, the Spearman rank correlation, the Theil-Sen slope, the
  regression value, the standard-error band, the Fibonacci hold, Woodie's
  pivot, the floor pivot, the DeMark pivot, Camarilla, Garman-Klass, the
  Schaff cross and the Schaff midline, the relative vigor index and its
  zero line, the first half of the month, expiration week, the weekend,
  Monday, the turn of the month, and the Halloween window. Those are
  already seated or tested names. Batch 9 did not add a neighbour of any
  of them as a new key.
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

1. **Holy Grail.** Connors and Raschke, *Street Smarts* (1995). ADX(14) is above 30 and below its previous value, the low tags the 20-day EMA, and the close is above that EMA and above the 200-day SMA. The 14 and the 30 stay fixed. The EMA length is the grid. Not `lib.adx14` and not `lib.elder_ray`.
2. **80-20 bar.** Connors and Raschke, same book. The open is in the bottom fifth of the high-low range and the close is in the top fifth, above the 200-day SMA. The fifths stay fixed. Not `lib.ibs` and not `lib.hammer`. The long is that close.
3. **Bullish key reversal.** Achelis, *Technical Analysis from A to Z*. A lower low than yesterday and a close above yesterday's close, after a down close, above the 200-day SMA. Not `lib.twobar`, which needs a close above yesterday's high.
4. **Three inside up.** Nison, *Japanese Candlestick Charting Techniques* (1991). A bullish harami, then a close above the first candle's high, above the 200-day SMA. Not `lib.harami` and not `lib.three_out`.
5. **Thrusting line.** Nison, same book. A white candle opens below the prior black close and closes inside that black body but below its midpoint, above the 200-day SMA. Not `lib.pierce`, which closes through the midpoint.
6. **Bullish meeting lines.** Nison, same book. A black candle, then a white candle whose close differs from that black close by at most 5 percent of the average high-low range of the prior five bars, above the 200-day SMA. A cent-exact close does not repeat. Not `lib.pierce`.
7. **Bullish separating lines.** Nison, same book. A black candle, then a white candle whose open differs from that black open by at most the same 5 percent band and which closes up, above the 200-day SMA. Not `lib.belt`.
8. **Point-and-figure double top.** Cohen, *How to Use the Three-Point Reversal Method of Point & Figure Stock Market Trading*; the percent box is the form that travels with bitcoin. A box is 1 percent. A column reverses after 3 boxes. Long when a column of rising boxes exceeds the prior rising column. Not `lib.renko` and not `lib.kagi`. The 1 and the 3 stay fixed.
9. **Heikin-Ashi white candle.** Valcu, "Using The Heikin-Ashi Technique", *Technical Analysis of Stocks & Commodities* (February 2004). The HA close is (open + high + low + close) / 4. The HA open is the average of the prior HA open and HA close, seeded with the first bar. Long while the HA close is above the HA open. Not a one-day white candle and not `lib.qstick`.
10. **Kase DevStop.** Kase, *Trading with the Odds* (1996). The stop is the highest close of the last 20 days minus twice the standard deviation of the true range over those days. It ratchets up while long and starts again from the raw stop when flat. The 2 stays fixed. Not `lib.safezone` and not `lib.chandelier`.
11. **Yang-Zhang quiet uptrend.** Yang and Zhang, "Drift-Independent Volatility Estimation Based on High, Low, Open, and Close Prices", *Journal of Business* (2000). Long while the 20-day Yang-Zhang variance is below its 100-day median and the close is above the 200-day SMA. The median length stays 100. Not `lib.gk`.
12. **Rogers-Satchell quiet uptrend.** Rogers and Satchell, "Estimating Variance from High, Low and Closing Prices", *Annals of Applied Probability* (1991). The daily term is `ln(H/O) * (ln(H/O) - ln(C/O)) + ln(L/O) * (ln(L/O) - ln(C/O))`. Long while the 20-day mean is below its 100-day median and the close is above the 200-day SMA. Not Yang-Zhang and not `lib.gk`.
13. **Trend intensity index.** M. H. Pee, *Technical Analysis of Stocks & Commodities* (2002). The percent of the last 20 closes that sit above the 20-day SMA, long above 50. The 50 stays fixed. Not `lib.psych`, which counts up-closes, and not the close above the SMA by itself.
14. **Chaikin volatility expansion.** Chaikin; Achelis records it apart from the money-flow oscillator. The 10-day EMA of the high-low range, then that average's 10-day rate of change. Long while the rate of change is above zero and the close is above the 200-day SMA. No volume. Not `lib.mass_index`.
15. **Ichimoku cloud twist.** Hosoda's cloud. Long while senkou span A is above senkou span B. The 9, 26, and 52 stay fixed. Not `lib.ichimoku`, which is the close versus the cloud, and not `lib.ichimoku_tk`.
16. **Dominant-cycle momentum.** Ehlers, *Cybernetic Analysis for Stocks and Futures* (2004). Long when the close is above the close one homodyne period ago. The period is the one already computed for MAMA, clamped from 6 to 50. Not a fixed `lib.tsmom30d` window. The fast and slow limits stay 0.5 and 0.05.
17. **Bullish hook.** Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990). The low takes out yesterday's low and the close finishes in the top quarter of today's range, above the 200-day SMA. The quarter stays fixed. Not `lib.hammer` and not `lib.twobar`.
18. **Bullish outside close.** Crabel, same book. The high takes out yesterday's high, the low takes out yesterday's low, and the close finishes in the top half, above the 200-day SMA. The long is that close. `lib.outside` waits for a later break of the outside day's high.
19. **Stick sandwich.** Nison, *Japanese Candlestick Charting Techniques* (1991). Two black candles with closes inside the 5 percent equal-band, and a white candle between them, above the 200-day SMA. Not `lib.matchlow`, which matches the lows.
20. **Momentum pinball.** Connors and Raschke, *Street Smarts* (1995). The 3-day RSI of the one-day percent change closes below 30, and the close is above the 200-day SMA. The 3 and the 30 stay fixed. Not `lib.rsi2` and not `lib.rmi`. The long is that close.
21. **Gopalakrishnan range index.** Jayanthi Gopalakrishnan, *Technical Analysis of Stocks & Commodities*. `ln(highest high - lowest low) / ln(n)` over 20 bars, long while that index is below its previous value and the close is above the 200-day SMA. Not `lib.chop` and not `lib.vhf`.
22. **Percentile channel.** Achelis records a percentile rank apart from a moving average. Long while the close is above the 80th percentile of the prior 20 closes. Today is outside the window. The 80 stays fixed. Not a Donchian high (`lib.donchian20` buys the highest high) and not an SMA.

Twenty-two of these can be coded on the current OHLC bars. None of them
is a one-trade-a-year calendar.
