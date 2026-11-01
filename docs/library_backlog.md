# Library backlog (batch 6 and after)

Paper only. BTC only. Daily bars, long or flat, next-open fills, the
pre-registered 20% stop. These are candidates for a later catalog append.
They are not registered, and discovery will not claim them until they are
added to `LIBRARY` the same way batch 5 was.

Batch 5 appended 26 names after the existing 82. It did not change those
82, and it did not requeue them. The random farm stays paused.

Do not add a near-duplicate of a name already in the catalog. Batch 5
skipped, on purpose:

- An SMA that is rising. An n-day simple average rises if and only if the
  close is above the close n days ago. That is `lib.tsmom30d` and the
  month-end cousins. It is not a new rule.
- PPO above zero. (EMA 12 − EMA 26) / EMA 26 is positive exactly when EMA 12
  is above EMA 26, which is `lib.ema12x26`.
- The sign of the correlation of price with time. That sign is the sign of
  the least-squares slope, so it is `lib.linreg`.
- The inverse Fisher transform of RSI. It crosses zero when RSI crosses 50,
  which is `lib.rsi14`.
- NR4 as its own key. It is the neighbour n=4 of `lib.nr7`.
- A daily Coppock. The published curve is the monthly one, and that one
  stays blocked. Shortening it so the warm-up fits is not the cited rule.
- Ichimoku chikou, TEMA rising, the turn of the quarter, and Heikin-Ashi.
  Batch 4 skipped those. They are still not keys.
- Yang-Zhang volatility as a second "variance below its median" key next to
  the Garman-Klass candidate below. One OHLC variance filter is enough until
  that one has been tested.
- Donchian, turtle, Bollinger, Keltner, the SMA band, MACD and its histogram
  slope, the impulse system, RSI, ConnorsRSI, the double seven, three lower
  closes, Williams %R, the stochastic cross, the stochastic pop, IBS,
  Qstick, balance of power, Supertrend, ADX, Aroon, CCI, Vortex, choppiness,
  the random walk index, TRIX, TSI, the awesome oscillator, the alligator,
  VIDYA, T3, the McGinley dynamic, KAMA, the Hull average, DEMA, the cloud
  and the tenkan/kijun cross, time-series momentum, the intermediate window,
  the first half of the month, the weekend, Monday, the turn of the month,
  and the Halloween window. Those are already seated or tested names.
  Batch 5 did not add a neighbour of any of them as a new key.
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
| Opening-range breakout | Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990) | The daily engine has one decision per UTC day, at the prior close. NR7, the inside day, and the outside day are the daily forms already registered. |
| Funding-rate or basis carry | Crypto perpetual funding | Not on the OHLC tape. |
| On-chain value (MVRV, SOPR, stock-to-flow) | Coin-metrics style valuation | Not on the OHLC tape. |
| Cross-sectional momentum, pairs, or Antonacci's relative leg | Jegadeesh and Titman, *Journal of Finance* (1993); Antonacci, *Dual Momentum Investing* (2014) | One asset. BTC only. Absolute momentum is already `lib.tsmom12m` / `lib.tsmom12_1` / `lib.mom_mid`. |
| ZigZag / repainting swings | Chart practice | A swing that is rewritten after later bars is look-ahead. Do not add it. |

## Ready for a later batch

Each line is one rule, with the source and the parameters to pre-register.
Lookbacks stay inside the 2017-08-17 warm-up so the score start can remain
2018-12-01. Neighbours stay one-at-a-time, as in the catalog. None of these
is a parameter neighbour of a name already registered.

1. **DeMarker.** Tom DeMark, *The New Science of Technical Analysis* (1994). DeMax is the part of today's high above yesterday's high, DeMin the part of yesterday's low above today's low, each smoothed. Long while DeMarker(14) is above 0.5. Highs and lows versus the prior bar, not close-to-close RSI (`lib.rsi14`) and not `lib.rei`.
2. **TD countdown.** DeMark, *New Market Timing Techniques* (1997). After a completed 9-bar setup, count 13 closes that satisfy the countdown test. Long when the buy countdown completes above the 200-day SMA. Not `lib.td_setup`, which stops at the setup.
3. **TRIX signal line.** Jack Hutson, *Technical Analysis of Stocks & Commodities* (1983). Long while TRIX is above its 9-day SMA. `lib.trix` is the zero line. The triple-EMA length stays the published 15 at the center.
4. **Slow stochastic.** George Lane, *Technical Analysis of Stocks & Commodities* (1984). Slow %D is an average of fast %D. Long while slow %D is above a further 3-day average of itself. `lib.stoch` is fast %K versus %D. `lib.stoch_pop` is the cross of fast %K through 80.
5. **Zero-lag EMA.** John Ehlers and Ric Way. Close above ZLEMA(20), where the lag is `(n − 1) / 2` and the input is close plus the close-minus-lagged-close. Not `lib.dema`, not `lib.t3`, and not an ordinary EMA.
6. **FRAMA.** John Ehlers, fractal adaptive moving average, *Technical Analysis of Stocks & Commodities*. Close above FRAMA. The adaptive constant is the fractal dimension of the last few closes. Not `lib.kama` (efficiency ratio) and not `lib.vidya` (Chande momentum).
7. **Super smoother.** Ehlers, *Cybernetic Analysis for Stocks and Futures* (2004). Close above the two-pole super smoother of length 10. A Butterworth-style smoother, not an EMA.
8. **Cyber cycle.** Ehlers, same book. The high-pass cycle line above its one-bar trigger. Not `lib.fisher` and not `lib.laguerre`.
9. **Laguerre filter.** Ehlers, same book. Close above the four-pole Laguerre moving average at gamma 0.5. Not the Laguerre RSI (`lib.laguerre`), which is the up-move share of those poles.
10. **Arnaud Legoux moving average.** Arnaud Legoux and Dimitris Kouzis-Loukas (2009). Close above ALMA(9), offset 0.85, sigma 6. A Gaussian window, not `lib.hma`.
11. **Three-line break.** Steve Nison, *Japanese Candlestick Charting Techniques* (1991). Long when a new white line closes beyond the high of the prior three lines. Close only, built in order. Not `lib.outside`.
12. **Bullish engulfing.** Nison, same book. Today's body engulfs the prior down body and the close is above the 200-day SMA. The long is that close. Not `lib.outside`, which waits for a later break of the high, and not `lib.bop`.
13. **Piercing pattern.** Nison, same book. The open is below the prior low and the close finishes above the midpoint of the prior body, with the close above the 200-day SMA.
14. **Three white soldiers.** Nison, same book. Three rising bodies, each opening inside the prior body and closing near its high, above the 200-day SMA. Not `lib.three_dn`, which buys lower closes.
15. **Williams Oops.** Larry Williams, *Long-Term Secrets to Short-Term Trading* (1999). The low takes out yesterday's low and the close finishes back above that low. Not `lib.turtle_soup` (no 20-day extreme) and not `lib.outside`.
16. **Variance ratio.** Lo and MacKinlay, "Stock Market Prices Do Not Follow Random Walks", *Review of Financial Studies* 1(1), 1988. Long when the variance ratio of daily returns at horizon 16 is above 1 and the close is above the 200-day SMA. Not the sign of one return (`lib.tsmom30d`) and not `lib.linreg`.
17. **Vertical horizontal filter.** Adam White, *Technical Analysis of Stocks & Commodities*. VHF(28) above its 100-day median and the close above the 200-day SMA. The median length stays fixed. Not `lib.chop` (the log true-range ratio) and not ADX.
18. **Accelerator oscillator.** Bill Williams, *Trading Chaos* (1995). The awesome oscillator minus its 5-period SMA, long while that difference is above zero. Not `lib.awesome`, which is the zero line of the oscillator itself.
19. **Gator expanding.** Williams, same book. Both the absolute jaw-to-teeth gap and the absolute teeth-to-lips gap are larger than the day before, and the lips are above the jaw. Not `lib.alligator`, which is the alignment alone.
20. **Camarilla pivot.** Nick Stott's equation. H3 of the prior day is close + (high − low) × 1.1 / 4. Long when the close is above that H3 and the 200-day SMA. Not `lib.floor_pivot`, which is (high + low + close) / 3.
21. **Regression value.** Mel Widner, projection bands, *Technical Analysis of Stocks & Commodities* (1995). Long while the close is above the fitted value of the 20-day least-squares line on the current bar. Price can sit above a line whose slope is negative, so this is not `lib.linreg`.
22. **Garman-Klass filter.** Garman and Klass, "On the Estimation of Security Price Volatilities from Historical Data", *Journal of Business* 53(1), 1980. Long while the close is above the 200-day SMA and the 14-day Garman-Klass variance is below its 100-day median. The median length stays fixed. Not `lib.vol_trend` (close-to-close) and not `lib.ulcer`.
23. **Bullish harami.** Nison, *Japanese Candlestick Charting Techniques* (1991). Today's body sits inside the prior body after a down close, and the close is above the 200-day SMA. Not `lib.inside_day`, which uses the full high and low and waits for a later break.
24. **Expiration week.** Stoll and Whaley, "Program Trading and Expiration-Day Effects", *Journal of Finance* (1987). Long for the three UTC sessions ending on the third Friday of the month. Twelve turns a year, so it is not a one-trade-a-year calendar. Not `lib.tom` and not `lib.first_half`.

Twenty-four of these can be coded on the current OHLC bars. Prefer 1–22
before the remaining calendar rule if the next batch should not be all
seasonality. Item 24 is the only calendar rule in this list.
