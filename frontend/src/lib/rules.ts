/**
 * Plain-English reading of a strategy name. A name is building blocks joined
 * with "&" (all must be true on the same closed 5m bar). Mirrors the grammar in
 * hedge_fund/signals/dynamic.py; an unknown block is shown as written.
 */

export type BlockCategory =
  | "trend"
  | "momentum"
  | "dip"
  | "oscillator"
  | "volatility"
  | "structure"
  | "higher_tf"
  | "other";

export interface BlockReading {
  raw: string;
  text: string;
  category: BlockCategory;
}

export const CATEGORY_LABEL: Record<BlockCategory, string> = {
  trend: "Trend",
  momentum: "Momentum",
  dip: "Dip",
  oscillator: "Oscillator",
  volatility: "Volatility",
  structure: "Price structure",
  higher_tf: "Higher timeframe",
  other: "Other",
};

export const CATEGORY_TONE: Record<BlockCategory, string> = {
  trend: "bg-sky-500/15 text-sky-200 ring-sky-400/25",
  momentum: "bg-emerald-500/15 text-emerald-200 ring-emerald-400/25",
  dip: "bg-rose-500/15 text-rose-200 ring-rose-400/25",
  oscillator: "bg-violet-500/15 text-violet-200 ring-violet-400/25",
  volatility: "bg-cyan-500/15 text-cyan-200 ring-cyan-400/25",
  structure: "bg-amber-500/15 text-amber-200 ring-amber-400/25",
  higher_tf: "bg-indigo-500/15 text-indigo-200 ring-indigo-400/25",
  other: "bg-white/10 text-white/70 ring-white/15",
};

/** Building-block families the random search draws from (Discovery explainer). */
export const BLOCK_FAMILIES: { category: BlockCategory; examples: string; plain: string }[] = [
  { category: "trend", examples: "sma_abv_30 · ema_stack · sma_stack", plain: "Price is above a moving average, or averages are stacked in uptrend order." },
  { category: "momentum", examples: "mom_18b_gt2pc", plain: "Price rose more than X% over the last N bars." },
  { category: "dip", examples: "dip_24b_lt3pc", plain: "Price fell more than X% over the last N bars (buy the dip)." },
  { category: "oscillator", examples: "rsi_14_>50 · wt_cross_up_os", plain: "RSI above / below a level, or a WaveTrend cross up from oversold." },
  { category: "volatility", examples: "vol_lowsm_30_90 · bb_lower_20_2", plain: "Volatility is calm, or price touches a Bollinger band." },
  { category: "structure", examples: "don_hi_48 · near_swing_lo_12 · dbl_bot_24", plain: "Breakouts, support near recent swing lows, double bottoms." },
  { category: "higher_tf", examples: "h1_ema_abv_50 · h4_sma_abv_50", plain: "The 1-hour or 4-hour close is above its average (completed bars only)." },
];

function duration(bars: number, minutesPerBar = 5): string {
  const minutes = bars * minutesPerBar;
  if (minutes < 60) return `${minutes} min`;
  const hours = minutes / 60;
  if (hours < 48) return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`;
  const days = hours / 24;
  return `${Number.isInteger(days) ? days : days.toFixed(1)} days`;
}

function pct(value: string): string {
  const n = Number(value);
  return n >= 1 ? `${n}%` : `${(n * 100).toFixed(1)}%`;
}

export function describeBlock(raw: string): BlockReading {
  const a = raw.trim();
  let m: RegExpMatchArray | null;

  if ((m = a.match(/^(h4|h1)_(ema|sma)_abv_(\d+)$/))) {
    const tf = m[1] === "h4" ? "4-hour" : "1-hour";
    const kind = m[2] === "ema" ? "exponential" : "simple";
    return { raw, category: "higher_tf", text: `${tf} close above its ${m[3]}-bar ${kind} average` };
  }
  if ((m = a.match(/^sma_stack(?:_([\d_]+))?$/))) {
    const p = m[1] ? m[1].split("_").join(" > ") : "7 > 25 > 50";
    return { raw, category: "trend", text: `Uptrend stack: price above simple averages ordered ${p} bars` };
  }
  if ((m = a.match(/^ema_stack(?:_([\d_]+))?$/))) {
    const p = m[1] ? m[1].split("_").join(" > ") : "7 > 25 > 50";
    return { raw, category: "trend", text: `Uptrend stack: price above exponential averages ordered ${p} bars` };
  }
  if ((m = a.match(/^sma(\d+)_rsi(\d+)$/))) {
    return { raw, category: "trend", text: `Price above ${m[1]}-bar average and RSI(14) above ${m[2]}` };
  }
  if ((m = a.match(/^sma(\d+)_mom(\d+)_(\d+)$/))) {
    return { raw, category: "momentum", text: `Price above ${m[1]}-bar average and up more than ${m[3]}% over ${m[2]} bars` };
  }
  if ((m = a.match(/^sma(?:_abv|_above)?_?(\d+)(?:_abv|_above)?$/))) {
    return { raw, category: "trend", text: `Price above its ${m[1]}-bar simple average (${duration(Number(m[1]))})` };
  }
  if ((m = a.match(/^ema(?:_abv|_above)?_?(\d+)(?:_abv|_above)?$/))) {
    return { raw, category: "trend", text: `Price above its ${m[1]}-bar exponential average (${duration(Number(m[1]))})` };
  }
  if ((m = a.match(/^rsi_(\d+)_>_?(\d+)(?:_<_?(\d+))?$/))) {
    return {
      raw,
      category: "oscillator",
      text: m[3] ? `RSI(${m[1]}) between ${m[2]} and ${m[3]}` : `RSI(${m[1]}) above ${m[2]} (buyers in control)`,
    };
  }
  if ((m = a.match(/^rsi_(\d+)_<_?(\d+)$/))) {
    return { raw, category: "oscillator", text: `RSI(${m[1]}) below ${m[2]}` };
  }
  if ((m = a.match(/^rsi_(\d+)_(\d+)$/))) {
    return { raw, category: "oscillator", text: `RSI(${m[1]}) above ${m[2]}` };
  }
  if (a === "rsi_momentum") {
    return { raw, category: "oscillator", text: "Price above 20-bar EMA and RSI(14) above 50" };
  }
  if (a === "robust_open") {
    return { raw, category: "trend", text: "Uptrend stack (7 > 25 > 50) and RSI(20) above 55" };
  }
  if ((m = a.match(/^mom_(\d+)b?_gt_?(\d+)(?:pc)?$/))) {
    return { raw, category: "momentum", text: `Price up more than ${pct(m[2])} over the last ${m[1]} bars (${duration(Number(m[1]))})` };
  }
  if ((m = a.match(/^dip_(\d+)b?_lt_?(\d+)(?:pc)?$/))) {
    return { raw, category: "dip", text: `Price down more than ${pct(m[2])} over the last ${m[1]} bars (${duration(Number(m[1]))})` };
  }
  if ((m = a.match(/^vol_lowsm_(\d+)_(\d+)$/))) {
    return { raw, category: "volatility", text: `Calm market: ${m[1]}-bar volatility below ${m[2]}-bar volatility` };
  }
  if ((m = a.match(/^bb_(lower|upper)_(\d+)_?(\d*)$/))) {
    const sd = m[3] || "2";
    return {
      raw,
      category: "volatility",
      text: m[1] === "lower" ? `Price at or below the lower Bollinger band (${m[2]} bars, ${sd}σ)` : `Price at or above the upper Bollinger band (${m[2]} bars, ${sd}σ)`,
    };
  }
  if ((m = a.match(/^don_(hi|lo)_(\d+)$/))) {
    return {
      raw,
      category: "structure",
      text: m[1] === "hi" ? `Breakout: close above the prior ${m[2]}-bar high` : `Support: price near the prior ${m[2]}-bar low`,
    };
  }
  if ((m = a.match(/^near_swing_(hi|lo)_(\d+)$/))) {
    return {
      raw,
      category: "structure",
      text: m[1] === "lo" ? `Price near a confirmed swing low (support, ${m[2]}-bar pivot)` : `Price near a confirmed swing high (${m[2]}-bar pivot)`,
    };
  }
  if ((m = a.match(/^dbl_(bot|top)_(\d+)$/))) {
    return {
      raw,
      category: "structure",
      text: m[1] === "bot" ? `Double bottom held and price recovered (${m[2]}-bar pivots)` : `Double top (${m[2]}-bar pivots)`,
    };
  }
  if (a === "wt_cross_up_os") {
    return { raw, category: "oscillator", text: "WaveTrend crosses up from oversold (green dot)" };
  }
  if (a === "wt_below_os") {
    return { raw, category: "oscillator", text: "WaveTrend below the oversold line" };
  }
  if (a === "wt_cross_down_ob") {
    return { raw, category: "oscillator", text: "WaveTrend crosses down from overbought" };
  }
  return { raw, category: "other", text: a };
}

/** Blocks of one strategy name. "&" means all must hold; "|" is shown as an any-of block. */
export function strategyBlocks(name: string | null | undefined): BlockReading[] {
  if (!name) return [];
  return name
    .split("&")
    .map((part) => part.replace(/^\(|\)$/g, "").trim())
    .filter(Boolean)
    .map((part) => {
      if (part.includes("|")) {
        const alts = part.split("|").map((p) => describeBlock(p.trim()).text);
        return { raw: part, category: "other" as BlockCategory, text: `Any of: ${alts.join(" / ")}` };
      }
      return describeBlock(part);
    });
}

/** Frozen rm_v1 exit / sizing policy, in words (hedge_fund/risk/rm_v1.py). */
export const EXIT_RULES = [
  "Stop-loss 2× ATR(14) below entry, kept between 1.5% and 4%",
  "Take-profit at 2× the stop distance (2:1 reward-to-risk)",
  "Exits only on stop or take-profit, never on a signal flip",
];

export const SIZING_RULES = [
  "Risk 1% of the account per lot, up to 3 lots per symbol",
  "Open notional capped at 100% of equity; BTC/USDT only",
  "Fees: 0.1% taker each side plus 2 bps slippage",
];
