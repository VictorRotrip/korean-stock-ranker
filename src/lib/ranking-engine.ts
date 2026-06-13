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
  getStocks,
  getLatestPrices,
  getLatestFinancials,
  getPriorFinancials,
  getStockPriceHistory,
  getShortSellingData,
} from "./mock-data";

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

// Mirror of `P123_TREE` in scripts/python/run_ranking_snapshot.py. This is
// the tree that the backend uses for the historical composite snapshots
// surfaced on /backtest, so the editor stays in sync with what actually
// drives the live ranking. Keep them aligned: any factor/weight change here
// should be mirrored in Python, and vice versa.
export const DEFAULT_RANKING_SYSTEM: RankingSystem = {
  id: "p123-inspired",
  name: "P123 Inspired Korea Multi-Factor",
  description: "Six-category multi-factor model (value, quality, growth, momentum, low volatility, sentiment) used for the live ranking and /backtest composites.",
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
        weight: 25,
        children: [
          {
            id: "sub-val-earn",
            type: "composite",
            name: "Earnings-Based",
            weight: 35,
            children: [
              { id: "f-ey", type: "factor", name: "Earnings Yield", weight: 50, factorId: "pe_ttm_inv" },
              { id: "f-ebitda-ev", type: "factor", name: "EBITDA/EV", weight: 50, factorId: "ebitda_ev" },
            ],
          },
          {
            id: "sub-val-sales",
            type: "composite",
            name: "Sales-Based",
            weight: 30,
            children: [
              { id: "f-ps", type: "factor", name: "Sales Yield", weight: 40, factorId: "price_sales_ttm_inv" },
              { id: "f-evs", type: "factor", name: "Revenue/EV", weight: 30, factorId: "ev_sales_ttm_inv" },
              { id: "f-gpev", type: "factor", name: "Gross Profit/EV", weight: 30, factorId: "gross_profit_ev" },
            ],
          },
          {
            id: "sub-val-fcf",
            type: "composite",
            name: "FCF-Based",
            weight: 20,
            children: [
              { id: "f-fcfy", type: "factor", name: "FCF Yield", weight: 40, factorId: "fcf_mcap" },
              { id: "f-ocfy", type: "factor", name: "OCF Yield", weight: 30, factorId: "ocf_mcap" },
              { id: "f-ufcf", type: "factor", name: "Unlevered FCF/EV", weight: 30, factorId: "ufcf_ev" },
            ],
          },
          {
            id: "sub-val-asset",
            type: "composite",
            name: "Asset-Based",
            weight: 15,
            children: [
              { id: "f-pb", type: "factor", name: "Book/Market", weight: 50, factorId: "price_book" },
              { id: "f-divy", type: "factor", name: "Dividend Yield", weight: 25, factorId: "dividend_yield" },
              { id: "f-bby", type: "factor", name: "Buyback Yield", weight: 25, factorId: "buyback_yield_yoy" },
            ],
          },
        ],
      },
      {
        id: "cat-quality",
        type: "category",
        name: "Quality",
        weight: 30,
        children: [
          {
            id: "sub-q-margin",
            type: "composite",
            name: "Margins",
            weight: 25,
            children: [
              { id: "f-opmgn", type: "factor", name: "Operating Margin", weight: 60, factorId: "operating_margin_ttm" },
              { id: "f-gpmgn", type: "factor", name: "Gross Margin", weight: 40, factorId: "gross_margin_ttm" },
            ],
          },
          {
            id: "sub-q-roc",
            type: "composite",
            name: "Return on Capital",
            weight: 30,
            children: [
              { id: "f-roe", type: "factor", name: "ROE", weight: 28, factorId: "roe_ttm" },
              { id: "f-roa", type: "factor", name: "ROA", weight: 24, factorId: "roa_ttm" },
              { id: "f-gpa", type: "factor", name: "Gross Profit/Assets", weight: 24, factorId: "gross_profit_assets" },
              { id: "f-fcfa", type: "factor", name: "FCF/Assets", weight: 24, factorId: "fcf_to_assets" },
            ],
          },
          {
            id: "sub-q-bs",
            type: "composite",
            name: "Balance Sheet Strength",
            weight: 10,
            children: [
              { id: "f-cta", type: "factor", name: "Cash/Assets", weight: 100, factorId: "cash_to_assets" },
            ],
          },
          {
            id: "sub-q-turn",
            type: "composite",
            name: "Turnover",
            weight: 10,
            children: [
              { id: "f-at", type: "factor", name: "Asset Turnover", weight: 100, factorId: "asset_turnover_ttm" },
            ],
          },
          {
            id: "sub-q-fin",
            type: "composite",
            name: "Finances",
            weight: 25,
            children: [
              { id: "f-de", type: "factor", name: "Debt/Equity", weight: 50, factorId: "debt_to_equity" },
              { id: "f-ic", type: "factor", name: "Interest Coverage", weight: 50, factorId: "interest_coverage_ttm" },
            ],
          },
        ],
      },
      {
        id: "cat-growth",
        type: "category",
        name: "Growth",
        weight: 15,
        children: [
          {
            id: "sub-g-sales",
            type: "composite",
            name: "Sales Growth",
            weight: 30,
            children: [
              { id: "f-sg", type: "factor", name: "Sales Growth YoY", weight: 100, factorId: "sales_growth_yoy" },
            ],
          },
          {
            id: "sub-g-opinc",
            type: "composite",
            name: "Op Income Growth",
            weight: 25,
            children: [
              { id: "f-oig", type: "factor", name: "Op Income Growth YoY", weight: 100, factorId: "op_income_growth_yoy" },
            ],
          },
          {
            id: "sub-g-eps",
            type: "composite",
            name: "EPS Growth",
            weight: 25,
            children: [
              { id: "f-epsg", type: "factor", name: "EPS Growth YoY", weight: 50, factorId: "eps_growth_yoy" },
              { id: "f-nig", type: "factor", name: "Net Income Growth YoY", weight: 50, factorId: "net_income_growth_yoy" },
            ],
          },
          {
            id: "sub-g-cf",
            type: "composite",
            name: "Cash Flow Growth",
            weight: 20,
            children: [
              { id: "f-ocfg", type: "factor", name: "OCF Growth YoY", weight: 50, factorId: "ocf_growth_yoy" },
              { id: "f-fcfg", type: "factor", name: "FCF Growth YoY", weight: 50, factorId: "fcf_growth_yoy" },
            ],
          },
        ],
      },
      {
        id: "cat-momentum",
        type: "category",
        name: "Momentum",
        weight: 10,
        children: [
          {
            id: "sub-m-price",
            type: "composite",
            name: "Price Changes",
            weight: 30,
            children: [
              { id: "f-pc120", type: "factor", name: "120d Return", weight: 50, factorId: "price_change_120d" },
              { id: "f-pc180", type: "factor", name: "180d Return", weight: 50, factorId: "price_change_180d" },
            ],
          },
          {
            id: "sub-m-tech",
            type: "composite",
            name: "Technical",
            weight: 30,
            children: [
              { id: "f-udr20", type: "factor", name: "UpDown 20d", weight: 20, factorId: "up_down_ratio_20" },
              { id: "f-udr60", type: "factor", name: "UpDown 60d", weight: 30, factorId: "up_down_ratio_60" },
              { id: "f-udr120", type: "factor", name: "UpDown 120d", weight: 25, factorId: "up_down_ratio_120" },
              { id: "f-rsi200", type: "factor", name: "RSI 200", weight: 25, factorId: "rsi_200" },
            ],
          },
          {
            id: "sub-m-qtr",
            type: "composite",
            name: "Quarterly Returns",
            weight: 25,
            children: [
              { id: "f-m3", type: "factor", name: "3M Return", weight: 30, factorId: "momentum_3m" },
              { id: "f-m6", type: "factor", name: "6M Return", weight: 35, factorId: "momentum_6m" },
              { id: "f-m121", type: "factor", name: "12-1M Momentum", weight: 35, factorId: "momentum_12_1" },
            ],
          },
          {
            id: "sub-m-industry",
            type: "composite",
            name: "Industry Momentum",
            weight: 15,
            children: [
              { id: "f-im26", type: "factor", name: "Industry 26W Momentum", weight: 50, factorId: "industry_momentum_26w" },
              { id: "f-im52", type: "factor", name: "Industry 52W Momentum", weight: 50, factorId: "industry_momentum_52w" },
            ],
          },
        ],
      },
      {
        id: "cat-risk",
        type: "category",
        name: "Low Volatility",
        weight: 10,
        children: [
          { id: "f-vol252", type: "factor", name: "252d Volatility", weight: 40, factorId: "volatility_252d" },
          { id: "f-vol60", type: "factor", name: "60d Volatility", weight: 30, factorId: "volatility_60d" },
          { id: "f-mdd", type: "factor", name: "Max Drawdown 252d", weight: 30, factorId: "max_drawdown_252d" },
        ],
      },
      {
        id: "cat-sentiment",
        type: "category",
        name: "Sentiment",
        weight: 10,
        children: [
          { id: "f-insider", type: "factor", name: "Insider Net Buying 90d", weight: 70, factorId: "insider_net_buying_90d" },
          { id: "f-si", type: "factor", name: "Short Interest", weight: 30, factorId: "short_interest_pct" },
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
