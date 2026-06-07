// =============================================================================
// Backtest computation — pure functions, runs in the browser
// =============================================================================
// Given the user's weight sliders, re-composite each ticker per snapshot,
// bucket into deciles, and chain monthly bucket-mean returns into a
// cumulative-return series. Mirrors Portfolio123's rank-performance test.
//
// All functions are pure: same inputs → same outputs. No fetches, no state.
//
// Type-only: this file is safely importable from client components. The
// matching server-side fetcher in data-service.server.ts imports these
// types from here, not the other way around.
// =============================================================================

/**
 * Compact per-ticker row inside a snapshot. Keeps payload small enough to
 * ship to the browser (≈ 80 KB per date × ~28 dates ≈ 2 MB before gzip).
 */
export interface BacktestTicker {
  ticker: string;
  /** category_scores_simple for this ticker; missing keys = null. */
  cats: Record<string, number | null>;
  passed: boolean;
}

export interface BacktestSnapshot {
  date: string;
  rows: BacktestTicker[];
}

export interface BacktestForwardReturn {
  ticker: string;
  date: string;          // snapshot_date
  ret: number;           // forward total return
  endDate: string | null;
}

export interface BacktestPayload {
  systemId: string;
  universeName: string;
  horizonDays: number;
  categories: string[];   // order of category keys
  snapshots: BacktestSnapshot[];
  /** Per-snapshot forward returns at the chosen horizon. */
  returns: Record<string, BacktestForwardReturn[]>;  // keyed by snapshot date
}

// ---------------------------------------------------------------------------
// Composite scoring with renormalized weights
// ---------------------------------------------------------------------------

/**
 * For one ticker, combine category percentile scores using the weight map.
 *
 * Uses NEUTRAL IMPUTATION for missing categories (null → 50). This matches
 * the snapshot generation methodology (run_ranking_snapshot.py runs with
 * `--missing-category-policy neutral`), so the decile assignment a user
 * sees in the backtest matches what the saved ranking would produce.
 *
 * The previous "renormalize" approach (drop null categories from the
 * denominator) produced systematically different rankings: a stock with
 * only Momentum scored got a 100% Momentum weight, biasing it heavily in
 * the bucketing. Neutral imputation is the standard P123 approach and
 * keeps stocks with partial coverage comparable to fully-covered ones.
 *
 * Returns null only if EVERY category is null AND the stock has no
 * weight signal at all — in practice this should never happen for stocks
 * that passed `passes_minimum` in the snapshot.
 */
export function compositeScore(
  cats: Record<string, number | null>,
  weights: Record<string, number>,
): { score: number | null; coverage: number } {
  const NEUTRAL = 50;
  let weightedSum = 0;
  let totalWeight = 0;
  let activeWeight = 0;
  let sawAnyCategory = false;
  for (const cat of Object.keys(weights)) {
    const w = weights[cat];
    if (w <= 0) continue;
    totalWeight += w;
    const v = cats[cat];
    if (v !== null && v !== undefined) {
      weightedSum += w * v;
      activeWeight += w;
      sawAnyCategory = true;
    } else {
      // Neutral imputation: missing category gets the median percentile
      // (50). The weight still counts toward the denominator so the
      // contribution doesn't get artificially amplified.
      weightedSum += w * NEUTRAL;
    }
  }
  if (totalWeight === 0 || !sawAnyCategory) {
    return { score: null, coverage: 0 };
  }
  return {
    score: weightedSum / totalWeight,
    coverage: activeWeight / totalWeight,
  };
}

// ---------------------------------------------------------------------------
// Bucket assignment
// ---------------------------------------------------------------------------

export interface ScoredTicker {
  ticker: string;
  score: number;
  /** The (winsorised) forward return cached alongside the score so the
   *  bucket-mean computation doesn't have to re-look-up retByTicker. */
  ret?: number;
}

/**
 * Sort tickers by composite descending and assign each to a bucket
 * (1 = highest scoring, nBuckets = lowest). Ties resolved by ticker
 * ordering, which is fine for monthly buckets at this granularity.
 */
export function bucketize(
  scored: ScoredTicker[],
  nBuckets: number,
): Map<string, number> {
  const sorted = scored.slice().sort((a, b) => b.score - a.score);
  const out = new Map<string, number>();
  const n = sorted.length;
  if (n === 0) return out;
  // Even chunks; remainder spills into the top bucket (matters for small n).
  for (let i = 0; i < n; i++) {
    const bucket = Math.min(
      nBuckets,
      Math.floor((i * nBuckets) / n) + 1,
    );
    out.set(sorted[i].ticker, bucket);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Run the backtest
// ---------------------------------------------------------------------------

export interface BucketPeriodReturn {
  date: string;          // snapshot date (rebalance)
  bucketReturns: number[]; // index 0 = bucket 1 (top), length = nBuckets
  marketReturn: number;  // equal-weight mean across all tickers used
  nTickers: number;
}

export interface BacktestResult {
  /** Per-rebalance bucket and market returns. */
  periods: BucketPeriodReturn[];
  /** Cumulative growth-of-1 per bucket. Each array has length = periods.length + 1.
   *  cumByBucket[b][0] = 1 (start), cumByBucket[b][k+1] = cum × (1 + bucketReturns[k]). */
  cumByBucket: number[][];
  /** Cumulative growth-of-1 for equal-weight benchmark. */
  cumMarket: number[];
  /** Per-bucket summary statistics. */
  stats: BucketStat[];
  marketStat: BucketStat;
  /** Whether each period had returns data for every bucket (else partial). */
  fullCoverage: boolean[];
  /** Dates aligned with cum arrays — length = periods.length + 1. First is the
   *  snapshot date (entry); subsequent entries correspond to the END of each
   *  hold period. */
  dates: string[];
}

export interface BucketStat {
  label: string;
  /** Avg arithmetic period return. */
  meanRet: number;
  /** Total cumulative return (final - 1). */
  cumReturn: number;
  /** Annualized geometric return. */
  cagr: number;
  /** Annualized volatility of period returns. */
  vol: number;
  /** Sharpe (rf assumed 0). */
  sharpe: number;
  /** % of periods with positive return. */
  hitRate: number;
  /** Worst single-period drawdown (max peak-to-trough on cum series). */
  maxDrawdown: number;
}

interface RunArgs {
  payload: BacktestPayload;
  weights: Record<string, number>;
  nBuckets: number;
  /** "passed" (default) restricts to passes_minimum=true, "all" includes everyone with at least one category score. */
  universeFilter?: "passed" | "all";
  /** How many periods per year for annualization (default = 12 for monthly). */
  periodsPerYear?: number;
}

export function runBacktest({
  payload,
  weights,
  nBuckets,
  universeFilter = "passed",
  periodsPerYear = 12,
}: RunArgs): BacktestResult {
  const periods: BucketPeriodReturn[] = [];

  for (const snap of payload.snapshots) {
    const rets = payload.returns[snap.date] ?? [];
    if (rets.length === 0) continue;  // no forward data yet for this date
    const retByTicker = new Map<string, number>();
    for (const r of rets) retByTicker.set(r.ticker, r.ret);

    // Score every ticker that has a return AND passes filter.
    const scored: ScoredTicker[] = [];
    for (const row of snap.rows) {
      if (universeFilter === "passed" && !row.passed) continue;
      const ret = retByTicker.get(row.ticker);
      if (ret === undefined) continue;
      // Backstop only — splits are already adjusted in
      // backtest_forward_returns.py via the shares-outstanding-based
      // detector. We keep a much looser winsorisation here as
      // defence-in-depth (in case the split detector misses something
      // because shares_outstanding is null for a given date). Bounds:
      // -95% (max realistic loss without delisting) to +500% (top of
      // realistic biotech/M&A catalyst territory).
      const winsorisedRet = Math.max(-0.95, Math.min(5.0, ret));
      const { score } = compositeScore(row.cats, weights);
      if (score === null) continue;
      scored.push({ ticker: row.ticker, score, ret: winsorisedRet });
    }

    if (scored.length < nBuckets) continue;  // not enough names to bucket

    const bucketMap = bucketize(scored, nBuckets);

    // Average return per bucket
    const sumByBucket = new Array<number>(nBuckets).fill(0);
    const countByBucket = new Array<number>(nBuckets).fill(0);
    let marketSum = 0;
    let marketCount = 0;
    for (const t of scored) {
      const b = bucketMap.get(t.ticker);
      if (b === undefined) continue;
      // Use the winsorised return cached on the scored ticker rather than
      // the raw retByTicker. This way both bucket means AND the universe
      // benchmark line use the same outlier handling.
      const r = t.ret ?? retByTicker.get(t.ticker)!;
      sumByBucket[b - 1] += r;
      countByBucket[b - 1] += 1;
      marketSum += r;
      marketCount += 1;
    }
    const bucketReturns = sumByBucket.map((s, i) =>
      countByBucket[i] > 0 ? s / countByBucket[i] : 0,
    );

    periods.push({
      date: snap.date,
      bucketReturns,
      marketReturn: marketCount > 0 ? marketSum / marketCount : 0,
      nTickers: scored.length,
    });
  }

  // Cumulative chains
  const cumByBucket: number[][] = Array.from({ length: nBuckets }, () => [1]);
  const cumMarket: number[] = [1];
  for (const p of periods) {
    for (let b = 0; b < nBuckets; b++) {
      const prev = cumByBucket[b][cumByBucket[b].length - 1];
      cumByBucket[b].push(prev * (1 + p.bucketReturns[b]));
    }
    cumMarket.push(cumMarket[cumMarket.length - 1] * (1 + p.marketReturn));
  }

  // Stats
  const stats: BucketStat[] = [];
  for (let b = 0; b < nBuckets; b++) {
    stats.push(makeStat(
      bucketLabel(b + 1, nBuckets),
      periods.map(p => p.bucketReturns[b]),
      cumByBucket[b],
      periodsPerYear,
    ));
  }
  const marketStat = makeStat(
    "Universe",
    periods.map(p => p.marketReturn),
    cumMarket,
    periodsPerYear,
  );

  // Dates aligned with cumulative arrays (length = periods.length + 1).
  // cum[i] is the value at the START of period (i+1), i.e. at periods[i].date
  // for i = 0..N-1. cum[N] is the value at the END of the last period, which
  // we approximate by shifting the final snapshot forward by ~30 days
  // (monthly rebalance assumption).
  const dates: string[] = [];
  if (periods.length > 0) {
    for (let i = 0; i < periods.length; i++) {
      dates.push(periods[i].date);
    }
    // Approximate end-of-last-period as snapshot + 30 calendar days.
    const last = new Date(periods[periods.length - 1].date);
    last.setDate(last.getDate() + 30);
    dates.push(last.toISOString().slice(0, 10));
  }

  const fullCoverage = periods.map(p => p.bucketReturns.every(r => r !== 0));

  return {
    periods,
    cumByBucket,
    cumMarket,
    stats,
    marketStat,
    fullCoverage,
    dates,
  };
}

function bucketLabel(b: number, n: number): string {
  if (n === 10) return `D${b}`;
  if (n === 5) return `Q${b}`;
  return `B${b}`;
}

function makeStat(
  label: string,
  rets: number[],
  cum: number[],
  periodsPerYear: number,
): BucketStat {
  if (rets.length === 0) {
    return { label, meanRet: 0, cumReturn: 0, cagr: 0, vol: 0, sharpe: 0, hitRate: 0, maxDrawdown: 0 };
  }
  const meanRet = rets.reduce((a, b) => a + b, 0) / rets.length;
  const variance = rets.reduce((a, b) => a + (b - meanRet) ** 2, 0) / Math.max(1, rets.length - 1);
  const periodVol = Math.sqrt(variance);
  const vol = periodVol * Math.sqrt(periodsPerYear);
  const finalVal = cum[cum.length - 1];
  const cumReturn = finalVal - 1;
  const years = rets.length / periodsPerYear;
  const cagr = years > 0 ? Math.pow(finalVal, 1 / years) - 1 : 0;
  const sharpe = vol > 0 ? (meanRet * periodsPerYear) / vol : 0;
  const hitRate = rets.filter(r => r > 0).length / rets.length;
  // Max drawdown on cumulative path
  let peak = cum[0];
  let maxDd = 0;
  for (const v of cum) {
    if (v > peak) peak = v;
    const dd = (v - peak) / peak;
    if (dd < maxDd) maxDd = dd;
  }
  return { label, meanRet, cumReturn, cagr, vol, sharpe, hitRate, maxDrawdown: maxDd };
}
