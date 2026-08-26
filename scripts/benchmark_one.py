import sys, time, json
sys.path.insert(0, '/opt/data/paper-trading-bot')
import hedge_fund.backtest.strategies as bs
from hedge_fund.backtest.stride import downsample

data = json.load(open('/opt/data/paper-trading-bot/state/crypto_history_1h.json'))
btc_full = data['BTC/USDT']
closes_full = [r[4] for r in btc_full]
highs_full = [r[2] for r in btc_full]
lows_full = [r[3] for r in btc_full]

btc_stride = downsample(btc_full, 6)
closes_stride = [r[4] for r in btc_stride]
highs_stride = [r[2] for r in btc_stride]
lows_stride = [r[3] for r in btc_stride]

# Measure stride=6 (1 symbol)
t0 = time.time()
res_stride = bs.backtest(closes_stride, highs_stride, lows_stride, 'sma_stack')
t_stride = time.time() - t0

# Measure full 70k (1 symbol)
t0 = time.time()
res_full = bs.backtest(closes_full, highs_full, lows_full, 'sma_stack')
t_full = time.time() - t0

print(f"Stride 6 (~11.8k bars, 8 yrs): {t_stride*1000:.1f} ms per symbol")
print(f"  -> 4 symbols (BTC, ETH, SOL, XRP) across Train + Held-out: {t_stride * 4 * 2:.3f} s total per strategy")
print(f"Full 1h (70.9k bars, 8 yrs): {t_full*1000:.1f} ms per symbol")
print(f"  -> 4 symbols across Train + Held-out: {t_full * 4 * 2:.3f} s total per strategy")
