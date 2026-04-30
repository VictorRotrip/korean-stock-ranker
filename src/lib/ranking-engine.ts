// =============================================================================
// Ranking Engine
// =============================================================================
// Core computation pipeline:
//
// 1. Filter universe  → list of tickers that pass all universe rules
// 2. Compute factors  → for each stock × factor, compute the raw value
// 3. Rank factors     → percentile-rank each factor across the universe
//                        (with optional winsorization, z-score, sector-neutral)
// 4. Aggregate tree   → walk the ranking tree bottom-up, weight-averaging
//                        factor ranks into category scores into composite score
// 5. Sort & output    → sort by composite score descending, assign final ranks
//
// QUANT NOTES:
// - Percentile ranking: rank / (N-1) * 100, where rank 0 = worst.
//   For "lower_is_better" factors, we invert: percentile = 100 - percentile.
// - Missing values: configurable — exclude, assign median, assign worst (0 or 100).
// - Winsorization: clip raw values at the Pth and (100-P)th percentile before ranking.
// - Z-score: standardize to mean=0, std=1 then convert to percentile via normal CDF.
// - Sector-neutral: rank within each sector independently, then map back to 0-100.
// =============================================================================

import type {
  Stock,
  DailyPrice,
  FinancialStatement,
  ShortSellingData,
  UniverseConfig,
  UniverseRule,
  RankingSystem,
  RankingNode,
  RankingOptions,
  RankingResult,
  StockRanking,
  FactorDirection,
  Market,
} from "@/types";

import { FACTOR_REGISTRY, type FactorInput } from "./factors";
import {
  getStocksSync as getStocks,
  getLatestPricesSync as getLatestPrices,
  getLatestFinancialsSync as getLatestFinancials,
  getPriorFinancialsSync as getPriorFinancials,
  getStockPriceHistorySync as getStockPriceHistory,
  getShortSellingDataSync as getShortSellingData,
} from "./data-service";

// ---------------------------------------------------------------------------
// 1. Universe Filtering
// ---------------------------------------------------------------------------

export function filterUniverse(config: UniverseConfig | undefined): Stock[] {
  let stocks = getStocks().filter(s => s.isActive);
  const latestPrices = getLatestPrices();

  if (!config || config.rules.length === 0) return stocks;

  for (const rule of config.rules) {
    switch (rule.type) {
      case "market":
        if (rule.value !== "ALL") {
          const markets = (Array.isArray(rule.value) ? rule.value : [rule.value]) as Market[];
          stocks = stocks.filter(s => markets.includes(s.market));
        }
        break;

      case "minMarketCap": {
        const min = Number(rule.value);
        stocks = stocks.filter(s => {
          const price = latestPrices.get(s.ticker);
          return price && price.marketCap >= min;
        });
        break;
      }

      case "minLiquidity": {
        const min = Number(rule.value);
        stocks = stocks.filter(s => {
          const price = latestPrices.get(s.ticker);
          return price && price.tradingValue >= min;
        });
        break;
      }

      case "excludeFinancials":
        if (rule.value) stocks = stocks.filter(s => !s.isFinancial);
        break;
      case "excludeSpacs":
        if (rule.value) stocks = stocks.filter(s => !s.isSpac);
        break;
      case "excludePreferred":
        if (rule.value) stocks = stocks.filter(s => !s.isPreferred);
        break;
      case "excludeEtfs":
        if (rule.value) stocks = stocks.filter(s => !s.isEtf);
        break;
      case "excludeReits":
        if (rule.value) stocks = stocks.filter(s => !s.isReit);
        break;
      case "excludeHoldings":
        if (rule.value) stocks = stocks.filter(s => !s.isHolding);
        break;

      case "excludeSectors": {
        const sectors = rule.value as string[];
        stocks = stocks.filter(s => !s.sector || !sectors.includes(s.sector));
        break;
      }

      case "maxStocks": {
        const max = Number(rule.value);
        // Sort by market cap descending and take top N
        stocks = stocks
          .map(s => ({ stock: s, mcap: latestPrices.get(s.ticker)?.marketCap ?? 0 }))
          .sort((a, b) => b.mcap - a.mcap)
          .slice(0, max)
          .map(x => x.stock);
        break;
      }
    }
  }

  return stocks;
}

// ---------------------------------------------------------------------------
// 2. Compute Raw Factor Values
// ---------------------------------------------------------------------------

interface RawFactorData {
  /** ticker → factorId → rawValue */
  values: Map<string, Map<string, number | null>>;
  /** List of factor IDs that were computed */
  factorIds: string[];
}

function computeRawFactors(
  stocks: Stock[],
  factorIds: string[],
  asOfDate: string
): RawFactorData {
  const latestPrices = getLatestPrices();
  const latestFinancials = getLatestFinancials(asOfDate);
  const priorFinancials = getPriorFinancials(asOfDate);
  const shortData = getShortSellingData();

  // Index short selling by ticker (latest date)
  const shortByTicker = new Map<string, ShortSellingData>();
  for (const s of shortData) {
    if (s.date <= asOfDate) {
      const existing = shortByTicker.get(s.ticker);
      if (!existing || s.date > existing.date) {
        shortByTicker.set(s.ticker, s);
      }
    }
  }

  const values = new Map<string, Map<string, number | null>>();

  // Get the factor compute functions
  const factors = factorIds
    .map(id => FACTOR_REGISTRY.find(f => f.id === id))
    .filter((f): f is (typeof FACTOR_REGISTRY)[number] => f !== undefined);

  for (const stock of stocks) {
    const price = latestPrices.get(stock.ticker);
    if (!price) continue;

    const input: FactorInput = {
      stock,
      latestPrice: price,
      priceHistory: getStockPriceHistory(stock.ticker),
      financials: latestFinancials.get(stock.ticker) ?? null,
      priorFinancials: priorFinancials.get(stock.ticker) ?? null,
      shortSelling: shortByTicker.get(stock.ticker) ?? null,
    };

    const tickerValues = new Map<string, number | null>();
    for (const factor of factors) {
      try {
        tickerValues.set(factor.id, factor.compute(input));
      } catch {
        tickerValues.set(factor.id, null);
      }
    }
    values.set(stock.ticker, tickerValues);
  }

  return { values, factorIds: factors.map(f => f.id) };
}

// ---------------------------------------------------------------------------
// 3. Percentile Ranking
// ---------------------------------------------------------------------------

/**
 * Winsorize values at the given percentile (e.g., 0.05 clips at 5th and 95th).
 */
function winsorize(values: (number | null)[], pct: number): (number | null)[] {
  const valid = values.filter((v): v is number => v !== null).sort((a, b) => a - b);
  if (valid.length < 5) return values;

  const lowIdx = Math.floor(valid.length * pct);
  const highIdx = Math.floor(valid.length * (1 - pct));
  const low = valid[lowIdx];
  const high = valid[highIdx];

  return values.map(v => {
    if (v === null) return null;
    return Math.max(low, Math.min(high, v));
  });
}

/**
 * Rank an array of values to percentiles (0-100).
 * Handles ties by averaging ranks.
 */
function percentileRank(
  values: (number | null)[],
  direction: FactorDirection,
  missingHandling: RankingOptions["missingValueHandling"]
): (number | null)[] {
  // Separate valid and null indices
  const indexed = values.map((v, i) => ({ value: v, index: i }));
  const valid = indexed.filter(x => x.value !== null) as { value: number; index: number }[];

  if (valid.length === 0) return values.map(() => null);

  // Sort ascending by value
  valid.sort((a, b) => a.value - b.value);

  // Assign ranks (handle ties by averaging)
  const ranks = new Map<number, number>();
  let i = 0;
  while (i < valid.length) {
    let j = i;
    while (j < valid.length && valid[j].value === valid[i].value) j++;
    const avgRank = (i + j - 1) / 2;
    for (let k = i; k < j; k++) {
      ranks.set(valid[k].index, avgRank);
    }
    i = j;
  }

  const n = valid.length;
  const result: (number | null)[] = new Array(values.length);

  for (let idx = 0; idx < values.length; idx++) {
    if (values[idx] === null) {
      // Handle missing values
      switch (missingHandling) {
        case "exclude":
          result[idx] = null;
          break;
        case "median":
          result[idx] = 50;
          break;
        case "worst":
          result[idx] = direction === "higher_is_better" ? 0 : 100;
          break;
        case "neutral":
          result[idx] = 50;
          break;
        default:
          result[idx] = null;
      }
    } else {
      const rank = ranks.get(idx)!;
      let pctile = n > 1 ? (rank / (n - 1)) * 100 : 50;

      // For "lower_is_better", invert the percentile so that
      // low raw values get high percentile scores
      if (direction === "lower_is_better") {
        pctile = 100 - pctile;
      }

      result[idx] = Math.round(pctile * 100) / 100;
    }
  }

  return result;
}

/**
 * Sector-neutral ranking: rank within each sector independently,
 * then normalize to 0-100 across the whole universe.
 */
function sectorNeutralRank(
  tickers: string[],
  values: (number | null)[],
  stocks: Stock[],
  direction: FactorDirection,
  missingHandling: RankingOptions["missingValueHandling"]
): (number | null)[] {
  const stockMap = new Map(stocks.map(s => [s.ticker, s]));

  // Group by sector
  const sectors = new Map<string, number[]>(); // sector → indices
  for (let i = 0; i < tickers.length; i++) {
    const sector = stockMap.get(tickers[i])?.sector ?? "Unknown";
    if (!sectors.has(sector)) sectors.set(sector, []);
    sectors.get(sector)!.push(i);
  }

  const result: (number | null)[] = new Array(tickers.length);

  for (const [, indices] of sectors) {
    const sectorValues = indices.map(i => values[i]);
    const sectorRanks = percentileRank(sectorValues, direction, missingHandling);
    for (let j = 0; j < indices.length; j++) {
      result[indices[j]] = sectorRanks[j];
    }
  }

  return result;
}

// ---------------------------------------------------------------------------
// 4. Tree Aggregation
// ---------------------------------------------------------------------------

/**
 * Collect all factor IDs used in a ranking tree (recursive).
 */
export function collectFactorIds(node: RankingNode): string[] {
  if (node.type === "factor" && node.factorId) {
    return [node.factorId];
  }
  if (node.children) {
    return node.children.flatMap(collectFactorIds);
  }
  return [];
}

/**
 * Compute the composite score for a single stock by walking the tree.
 * Returns a score from 0-100.
 */
function computeNodeScore(
  node: RankingNode,
  factorRanks: Map<string, number | null>, // factorId → percentile rank for this stock
): number | null {
  if (node.type === "factor") {
    if (!node.factorId) return null;
    return factorRanks.get(node.factorId) ?? null;
  }

  // Category or Composite node: weighted average of children
  if (!node.children || node.children.length === 0) return null;

  let totalWeight = 0;
  let weightedSum = 0;

  for (const child of node.children) {
    const childScore = computeNodeScore(child, factorRanks);
    if (childScore !== null) {
      weightedSum += childScore * child.weight;
      totalWeight += child.weight;
    }
  }

  if (totalWeight === 0) return null;
  return weightedSum / totalWeight;
}

/**
 * Collect all intermediate node scores for a stock (for the detail breakdown).
 */
function collectNodeScores(
  node: RankingNode,
  factorRanks: Map<string, number | null>,
  scores: Record<string, number> = {}
): Record<string, number> {
  const score = computeNodeScore(node, factorRanks);
  if (score !== null) {
    scores[node.id] = Math.round(score * 100) / 100;
  }

  if (node.children) {
    for (const child of node.children) {
      collectNodeScores(child, factorRanks, scores);
    }
  }

  return scores;
}

// ---------------------------------------------------------------------------
// 5. Full Ranking Pipeline
// ---------------------------------------------------------------------------

export function runRanking(
  system: RankingSystem,
  asOfDate: string = "2024-12-20"
): RankingResult {
  const startTime = Date.now();

  // 1. Filter universe
  const universeConfig: UniverseConfig | undefined = system.universeId
    ? undefined // TODO: load from saved universes
    : undefined;
  const stocks = filterUniverse(universeConfig);
  const tickers = stocks.map(s => s.ticker);

  // 2. Collect which factors the tree uses
  const factorIds = collectFactorIds(system.tree);
  if (factorIds.length === 0) {
    return {
      rankingSystemId: system.id,
      date: asOfDate,
      rankings: [],
      universeSize: stocks.length,
      computedAt: new Date().toISOString(),
    };
  }

  // 3. Compute raw factor values
  const rawData = computeRawFactors(stocks, factorIds, asOfDate);

  // 4. Rank each factor across the universe
  const options = system.options;
  const factorRanksPerStock = new Map<string, Map<string, number | null>>();

  // Initialize per-stock maps
  for (const ticker of tickers) {
    factorRanksPerStock.set(ticker, new Map());
  }

  for (const factorId of rawData.factorIds) {
    const factorDef = FACTOR_REGISTRY.find(f => f.id === factorId);
    if (!factorDef) continue;

    // Find direction (check for overrides in tree)
    const direction = findDirectionOverride(system.tree, factorId) ?? factorDef.direction;

    // Extract raw values in ticker order
    let rawValues = tickers.map(t => rawData.values.get(t)?.get(factorId) ?? null);

    // Optional winsorization
    if (options.winsorize && options.winsorizePercentile) {
      rawValues = winsorize(rawValues, options.winsorizePercentile);
    }

    // Rank
    let ranked: (number | null)[];
    if (options.sectorNeutral) {
      ranked = sectorNeutralRank(tickers, rawValues, stocks, direction, options.missingValueHandling);
    } else {
      ranked = percentileRank(rawValues, direction, options.missingValueHandling);
    }

    // Store
    for (let i = 0; i < tickers.length; i++) {
      factorRanksPerStock.get(tickers[i])!.set(factorId, ranked[i]);
    }
  }

  // 5. Compute composite scores via tree aggregation
  const latestPrices = getLatestPrices();

  const rankings: StockRanking[] = [];

  for (let i = 0; i < stocks.length; i++) {
    const stock = stocks[i];
    const factorRanks = factorRanksPerStock.get(stock.ticker)!;
    const compositeScore = computeNodeScore(system.tree, factorRanks);

    if (compositeScore === null && options.missingValueHandling === "exclude") {
      continue; // skip stocks with no computable score
    }

    // Collect category-level scores
    const allScores = collectNodeScores(system.tree, factorRanks);

    // Collect category scores (only children of root that are categories)
    const categoryScores: Record<string, number> = {};
    if (system.tree.children) {
      for (const child of system.tree.children) {
        if (allScores[child.id] !== undefined) {
          categoryScores[child.name] = allScores[child.id];
        }
      }
    }

    // Collect individual factor scores with raw values
    const factorScores: Record<string, { rawValue: number | null; percentileRank: number }> = {};
    for (const factorId of rawData.factorIds) {
      const rawValue = rawData.values.get(stock.ticker)?.get(factorId) ?? null;
      const pctRank = factorRanks.get(factorId) ?? 50;
      factorScores[factorId] = { rawValue, percentileRank: pctRank };
    }

    const price = latestPrices.get(stock.ticker);

    rankings.push({
      rank: 0, // will be set after sorting
      ticker: stock.ticker,
      name: stock.name,
      market: stock.market,
      sector: stock.sector,
      industry: stock.industry,
      marketCap: price?.marketCap ?? 0,
      compositeScore: Math.round((compositeScore ?? 50) * 100) / 100,
      categoryScores,
      factorScores,
    });
  }

  // 6. Sort by composite score descending and assign ranks
  rankings.sort((a, b) => b.compositeScore - a.compositeScore);
  rankings.forEach((r, idx) => { r.rank = idx + 1; });

  return {
    rankingSystemId: system.id,
    date: asOfDate,
    rankings,
    universeSize: stocks.length,
    computedAt: new Date().toISOString(),
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Find if there's a direction override for a factor in the tree.
 */
function findDirectionOverride(
  node: RankingNode,
  factorId: string
): FactorDirection | undefined {
  if (node.type === "factor" && node.factorId === factorId && node.directionOverride) {
    return node.directionOverride;
  }
  if (node.children) {
    for (const child of node.children) {
      const result = findDirectionOverride(child, factorId);
      if (result) return result;
    }
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// Default Ranking System (for demo)
// ---------------------------------------------------------------------------

export const DEFAULT_RANKING_SYSTEM: RankingSystem = {
  id: "default",
  name: "Multi-Factor Korea",
  description: "Balanced multi-factor ranking combining value, quality, growth, and momentum.",
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  tree: {
    id: "root",
    type: "composite",
    name: "Composite",
    weight: 100,
    children: [
      {
        id: "cat-value",
        type: "category",
        name: "Value",
        weight: 30,
        children: [
          { id: "f-ey", type: "factor", name: "Earnings Yield", weight: 35, factorId: "earnings_yield" },
          { id: "f-bm", type: "factor", name: "Book-to-Market", weight: 25, factorId: "book_to_market" },
          { id: "f-ev", type: "factor", name: "EV/EBITDA (inv)", weight: 25, factorId: "ev_ebitda" },
          { id: "f-cfy", type: "factor", name: "Cash Flow Yield", weight: 15, factorId: "cf_yield" },
        ],
      },
      {
        id: "cat-quality",
        type: "category",
        name: "Quality",
        weight: 25,
        children: [
          { id: "f-roe", type: "factor", name: "ROE", weight: 35, factorId: "roe" },
          { id: "f-gp", type: "factor", name: "Gross Profitability", weight: 30, factorId: "gross_profitability" },
          { id: "f-om", type: "factor", name: "Operating Margin", weight: 20, factorId: "operating_margin" },
          { id: "f-de", type: "factor", name: "Debt/Equity", weight: 15, factorId: "debt_to_equity" },
        ],
      },
      {
        id: "cat-growth",
        type: "category",
        name: "Growth",
        weight: 20,
        children: [
          { id: "f-revg", type: "factor", name: "Revenue Growth", weight: 40, factorId: "revenue_growth" },
          { id: "f-epsg", type: "factor", name: "EPS Growth", weight: 35, factorId: "eps_growth" },
          { id: "f-opg", type: "factor", name: "Op Income Growth", weight: 25, factorId: "op_income_growth" },
        ],
      },
      {
        id: "cat-momentum",
        type: "category",
        name: "Momentum",
        weight: 25,
        children: [
          { id: "f-mom12", type: "factor", name: "12-1M Momentum", weight: 50, factorId: "momentum_12_1" },
          { id: "f-mom6", type: "factor", name: "6M Momentum", weight: 30, factorId: "momentum_6m" },
          { id: "f-52w", type: "factor", name: "Dist from 52W High", weight: 20, factorId: "dist_52w_high" },
        ],
      },
    ],
  },
  options: {
    missingValueHandling: "median",
    winsorize: true,
    winsorizePercentile: 0.05,
    useZScore: false,
    sectorNeutral: false,
    industryNeutral: false,
  },
};
