// =============================================================================
// Factor Library for Korean Equities
// =============================================================================
// Each factor is defined with:
// - A unique ID
// - Human-readable name and description
// - Category (value, quality, growth, momentum, risk, short_interest, etc.)
// - Direction (higher_is_better or lower_is_better)
// - A compute function that takes stock data and returns the raw value
//
// QUANT NOTES:
// - All factor computations use point-in-time data to avoid lookahead bias.
// - Financial data is only used if its filingDate <= the ranking date.
// - Price-based factors use the price as of the ranking date.
// - Growth factors compare the latest available annual period to the prior year.
// =============================================================================

import type {
  FactorDefinition,
  FactorCategory,
  FactorDirection,
  FactorCoverage,
  FactorDataStatus,
  Stock,
  DailyPrice,
  FinancialStatement,
  ShortSellingData,
} from "@/types";

// ---------------------------------------------------------------------------
// Factor Computation Context
// ---------------------------------------------------------------------------

/**
 * All the data needed to compute factors for a single stock.
 */
export interface FactorInput {
  stock: Stock;
  latestPrice: DailyPrice;
  priceHistory: DailyPrice[];   // sorted ascending by date
  financials: FinancialStatement | null;
  priorFinancials: FinancialStatement | null;
  shortSelling: ShortSellingData | null;
}

/**
 * A factor definition with its compute function.
 */
export interface ComputeFactor extends FactorDefinition {
  compute: (input: FactorInput) => number | null;
}

// ---------------------------------------------------------------------------
// VALUE Factors
// ---------------------------------------------------------------------------

const earningsYield: ComputeFactor = {
  id: "earnings_yield",
  name: "Earnings Yield (E/P)",
  description: "Net income / market cap. Inverse of P/E. Higher means cheaper.",
  category: "value",
  direction: "higher_is_better",
  formula: "Net Income (TTM) / Market Cap",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials + price",
  compute: ({ financials, latestPrice }) => {
    if (!financials?.netIncome || !latestPrice.marketCap) return null;
    if (latestPrice.marketCap === 0) return null;
    return financials.netIncome / latestPrice.marketCap;
  },
};

const bookToMarket: ComputeFactor = {
  id: "book_to_market",
  name: "Book-to-Market (B/M)",
  description: "Book value of equity / market cap. Higher means cheaper (deep value).",
  category: "value",
  direction: "higher_is_better",
  formula: "Total Equity / Market Cap",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials + price",
  compute: ({ financials, latestPrice }) => {
    if (!financials?.totalEquity || !latestPrice.marketCap) return null;
    if (latestPrice.marketCap === 0) return null;
    return financials.totalEquity / latestPrice.marketCap;
  },
};

const salesYield: ComputeFactor = {
  id: "sales_yield",
  name: "Sales Yield (S/P)",
  description: "Revenue / market cap. Inverse of P/S. Higher means cheaper.",
  category: "value",
  direction: "higher_is_better",
  formula: "Revenue (TTM) / Market Cap",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials + price",
  compute: ({ financials, latestPrice }) => {
    if (!financials?.revenue || !latestPrice.marketCap) return null;
    if (latestPrice.marketCap === 0) return null;
    return financials.revenue / latestPrice.marketCap;
  },
};

const cashFlowYield: ComputeFactor = {
  id: "cf_yield",
  name: "Cash Flow Yield (FCF/P)",
  description: "Free cash flow / market cap. Higher means cheaper.",
  category: "value",
  direction: "higher_is_better",
  formula: "Free Cash Flow / Market Cap",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials + price",
  compute: ({ financials, latestPrice }) => {
    if (!financials?.freeCashFlow || !latestPrice.marketCap) return null;
    if (latestPrice.marketCap === 0) return null;
    return financials.freeCashFlow / latestPrice.marketCap;
  },
};

const evToEbitda: ComputeFactor = {
  id: "ev_ebitda",
  name: "EV/EBITDA (inverted)",
  description: "EBITDA / Enterprise Value. Higher means cheaper. (Inverted so higher is better.)",
  category: "value",
  direction: "higher_is_better",
  formula: "EBITDA / (Market Cap + Total Debt - Cash)",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials + price",
  compute: ({ financials, latestPrice }) => {
    if (!financials?.ebitda || !latestPrice.marketCap) return null;
    const cash = financials.cash ?? 0;
    const debt = financials.totalDebt ?? 0;
    const ev = latestPrice.marketCap + debt - cash;
    if (ev <= 0) return null;
    return financials.ebitda / ev;
  },
};

const dividendYield: ComputeFactor = {
  id: "dividend_yield",
  name: "Dividend Yield",
  description: "Annual dividends paid / market cap.",
  category: "dividend",
  direction: "higher_is_better",
  formula: "Dividends Paid / Market Cap",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials + price",
  compute: ({ financials, latestPrice }) => {
    if (!financials?.dividendsPaid || !latestPrice.marketCap) return null;
    if (latestPrice.marketCap === 0) return null;
    // dividendsPaid is typically negative in statements; take absolute value
    return Math.abs(financials.dividendsPaid) / latestPrice.marketCap;
  },
};

// ---------------------------------------------------------------------------
// QUALITY Factors
// ---------------------------------------------------------------------------

const returnOnEquity: ComputeFactor = {
  id: "roe",
  name: "Return on Equity (ROE)",
  description: "Net income / total equity. Measures profitability relative to shareholder equity.",
  category: "quality",
  direction: "higher_is_better",
  formula: "Net Income / Total Equity",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials }) => {
    if (!financials?.netIncome || !financials.totalEquity) return null;
    if (financials.totalEquity === 0) return null;
    return financials.netIncome / financials.totalEquity;
  },
};

const returnOnAssets: ComputeFactor = {
  id: "roa",
  name: "Return on Assets (ROA)",
  description: "Net income / total assets.",
  category: "quality",
  direction: "higher_is_better",
  formula: "Net Income / Total Assets",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials }) => {
    if (!financials?.netIncome || !financials.totalAssets) return null;
    if (financials.totalAssets === 0) return null;
    return financials.netIncome / financials.totalAssets;
  },
};

const grossProfitability: ComputeFactor = {
  id: "gross_profitability",
  name: "Gross Profitability (Novy-Marx)",
  description: "Gross profit / total assets. Novy-Marx quality factor.",
  category: "quality",
  direction: "higher_is_better",
  formula: "Gross Profit / Total Assets",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials }) => {
    if (!financials?.grossProfit || !financials.totalAssets) return null;
    if (financials.totalAssets === 0) return null;
    return financials.grossProfit / financials.totalAssets;
  },
};

const operatingMargin: ComputeFactor = {
  id: "operating_margin",
  name: "Operating Margin",
  description: "Operating income / revenue.",
  category: "quality",
  direction: "higher_is_better",
  formula: "Operating Income / Revenue",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials }) => {
    if (!financials?.operatingIncome || !financials.revenue) return null;
    if (financials.revenue === 0) return null;
    return financials.operatingIncome / financials.revenue;
  },
};

const debtToEquity: ComputeFactor = {
  id: "debt_to_equity",
  name: "Debt/Equity",
  description: "Total debt / total equity. Lower is better (less leverage).",
  category: "quality",
  direction: "lower_is_better",
  formula: "Total Debt / Total Equity",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials }) => {
    if (!financials?.totalDebt || !financials.totalEquity) return null;
    if (financials.totalEquity === 0) return null;
    return financials.totalDebt / financials.totalEquity;
  },
};

const interestCoverage: ComputeFactor = {
  id: "interest_coverage",
  name: "Interest Coverage",
  description: "EBITDA / interest expense. Higher means more ability to service debt.",
  category: "quality",
  direction: "higher_is_better",
  formula: "EBITDA / Interest Expense",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials }) => {
    if (!financials?.ebitda || !financials.interestExpense) return null;
    if (financials.interestExpense === 0) return null;
    return financials.ebitda / Math.abs(financials.interestExpense);
  },
};

// ---------------------------------------------------------------------------
// GROWTH Factors
// ---------------------------------------------------------------------------

const revenueGrowth: ComputeFactor = {
  id: "revenue_growth",
  name: "Revenue Growth (YoY)",
  description: "Year-over-year revenue growth.",
  category: "growth",
  direction: "higher_is_better",
  formula: "(Revenue_t - Revenue_t-1) / |Revenue_t-1|",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials, priorFinancials }) => {
    if (!financials?.revenue || !priorFinancials?.revenue) return null;
    if (priorFinancials.revenue === 0) return null;
    return (financials.revenue - priorFinancials.revenue) / Math.abs(priorFinancials.revenue);
  },
};

const epsGrowth: ComputeFactor = {
  id: "eps_growth",
  name: "EPS Growth (YoY)",
  description: "Year-over-year EPS growth.",
  category: "growth",
  direction: "higher_is_better",
  formula: "(EPS_t - EPS_t-1) / |EPS_t-1|",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials, priorFinancials }) => {
    if (!financials?.eps || !priorFinancials?.eps) return null;
    if (priorFinancials.eps === 0) return null;
    return (financials.eps - priorFinancials.eps) / Math.abs(priorFinancials.eps);
  },
};

const operatingIncomeGrowth: ComputeFactor = {
  id: "op_income_growth",
  name: "Operating Income Growth (YoY)",
  description: "Year-over-year operating income growth.",
  category: "growth",
  direction: "higher_is_better",
  formula: "(OpIncome_t - OpIncome_t-1) / |OpIncome_t-1|",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials, priorFinancials }) => {
    if (!financials?.operatingIncome || !priorFinancials?.operatingIncome) return null;
    if (priorFinancials.operatingIncome === 0) return null;
    return (financials.operatingIncome - priorFinancials.operatingIncome) / Math.abs(priorFinancials.operatingIncome);
  },
};

const fcfGrowth: ComputeFactor = {
  id: "fcf_growth",
  name: "Free Cash Flow Growth (YoY)",
  description: "Year-over-year free cash flow growth.",
  category: "growth",
  direction: "higher_is_better",
  formula: "(FCF_t - FCF_t-1) / |FCF_t-1|",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials, priorFinancials }) => {
    if (!financials?.freeCashFlow || !priorFinancials?.freeCashFlow) return null;
    if (priorFinancials.freeCashFlow === 0) return null;
    return (financials.freeCashFlow - priorFinancials.freeCashFlow) / Math.abs(priorFinancials.freeCashFlow);
  },
};

// ---------------------------------------------------------------------------
// MOMENTUM Factors
// ---------------------------------------------------------------------------

/**
 * Helper: compute return over a date range from price history.
 * Returns null if insufficient data.
 */
function computeReturn(priceHistory: DailyPrice[], daysBack: number, skipDays = 0): number | null {
  if (priceHistory.length < daysBack + skipDays) return null;

  const endIdx = priceHistory.length - 1 - skipDays;
  const startIdx = endIdx - daysBack;
  if (startIdx < 0 || endIdx < 0) return null;

  const startPrice = priceHistory[startIdx].close;
  const endPrice = priceHistory[endIdx].close;
  if (startPrice === 0) return null;

  return (endPrice - startPrice) / startPrice;
}

const momentum12m1m: ComputeFactor = {
  id: "momentum_12_1",
  name: "12-1 Month Momentum",
  description: "Return from 12 months ago to 1 month ago (skip most recent month to avoid reversal).",
  category: "momentum",
  direction: "higher_is_better",
  formula: "Price_{t-21} / Price_{t-252} - 1",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => computeReturn(priceHistory, 231, 21),  // ~11 months, skip 1 month
};

const momentum6m: ComputeFactor = {
  id: "momentum_6m",
  name: "6-Month Momentum",
  description: "Return over the last 6 months.",
  category: "momentum",
  direction: "higher_is_better",
  formula: "Price_t / Price_{t-126} - 1",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => computeReturn(priceHistory, 126),
};

const momentum3m: ComputeFactor = {
  id: "momentum_3m",
  name: "3-Month Momentum",
  description: "Return over the last 3 months.",
  category: "momentum",
  direction: "higher_is_better",
  formula: "Price_t / Price_{t-63} - 1",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => computeReturn(priceHistory, 63),
};

const reversal1m: ComputeFactor = {
  id: "reversal_1m",
  name: "1-Month Reversal",
  description: "Negative of 1-month return. Captures short-term mean reversion.",
  category: "momentum",
  direction: "lower_is_better",  // stocks that dropped recently may bounce
  formula: "-(Price_t / Price_{t-21} - 1)",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => computeReturn(priceHistory, 21),
};

const distanceFrom52wHigh: ComputeFactor = {
  id: "dist_52w_high",
  name: "Distance from 52-Week High",
  description: "Current price / 52-week high. Closer to 1.0 = near highs.",
  category: "momentum",
  direction: "higher_is_better",
  formula: "Price_t / max(Price over 252 days)",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory, latestPrice }) => {
    if (priceHistory.length < 20) return null;
    const high52w = Math.max(...priceHistory.slice(-252).map(p => p.high));
    if (high52w === 0) return null;
    return latestPrice.close / high52w;
  },
};

// ---------------------------------------------------------------------------
// RISK Factors
// ---------------------------------------------------------------------------

const volatility: ComputeFactor = {
  id: "volatility_60d",
  name: "60-Day Volatility",
  description: "Annualized standard deviation of daily returns over 60 days. Lower is less risky.",
  category: "risk",
  direction: "lower_is_better",
  formula: "std(daily returns, 60d) * sqrt(252)",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => {
    const recent = priceHistory.slice(-61);
    if (recent.length < 30) return null;

    const returns: number[] = [];
    for (let i = 1; i < recent.length; i++) {
      if (recent[i - 1].close === 0) continue;
      returns.push((recent[i].close - recent[i - 1].close) / recent[i - 1].close);
    }

    if (returns.length < 20) return null;

    const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
    const variance = returns.reduce((a, b) => a + (b - mean) ** 2, 0) / (returns.length - 1);
    return Math.sqrt(variance) * Math.sqrt(252); // annualize
  },
};

const turnoverRatio: ComputeFactor = {
  id: "turnover_ratio",
  name: "Turnover Ratio (30d avg)",
  description: "Average daily volume / shares outstanding over 30 days. Proxy for liquidity.",
  category: "liquidity",
  direction: "higher_is_better",
  formula: "avg(Volume, 30d) / Shares Outstanding",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory, latestPrice }) => {
    const recent = priceHistory.slice(-30);
    if (recent.length < 10) return null;
    if (!latestPrice.sharesOutstanding || latestPrice.sharesOutstanding === 0) return null;

    const avgVolume = recent.reduce((a, b) => a + b.volume, 0) / recent.length;
    return avgVolume / latestPrice.sharesOutstanding;
  },
};

// ---------------------------------------------------------------------------
// SHORT INTEREST Factors
// ---------------------------------------------------------------------------

const shortSellingRatio: ComputeFactor = {
  id: "short_ratio",
  name: "Short Selling Ratio",
  description: "Short volume as % of total volume. Higher = more bearish sentiment.",
  category: "short_interest",
  direction: "lower_is_better",
  formula: "Short Volume / Total Volume",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "KRX short selling data",
  compute: ({ shortSelling }) => {
    if (!shortSelling) return null;
    return shortSelling.shortRatio / 100; // convert from percentage
  },
};

const shortBalanceRatio: ComputeFactor = {
  id: "short_balance_ratio",
  name: "Short Balance Ratio",
  description: "Outstanding short balance / shares. Higher = more shorts outstanding.",
  category: "short_interest",
  direction: "lower_is_better",
  formula: "Short Balance / Shares Outstanding",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "KRX short selling data",
  compute: ({ shortSelling, latestPrice }) => {
    if (!shortSelling?.shortBalance) return null;
    if (!latestPrice.sharesOutstanding || latestPrice.sharesOutstanding === 0) return null;
    return shortSelling.shortBalance / latestPrice.sharesOutstanding;
  },
};

// ---------------------------------------------------------------------------
// Factor Registry
// ---------------------------------------------------------------------------

export const FACTOR_REGISTRY: ComputeFactor[] = [
  // Value
  earningsYield,
  bookToMarket,
  salesYield,
  cashFlowYield,
  evToEbitda,
  dividendYield,
  // Quality
  returnOnEquity,
  returnOnAssets,
  grossProfitability,
  operatingMargin,
  debtToEquity,
  interestCoverage,
  // Growth
  revenueGrowth,
  epsGrowth,
  operatingIncomeGrowth,
  fcfGrowth,
  // Momentum
  momentum12m1m,
  momentum6m,
  momentum3m,
  reversal1m,
  distanceFrom52wHigh,
  // Risk
  volatility,
  turnoverRatio,
  // Short Interest
  shortSellingRatio,
  shortBalanceRatio,
];

/**
 * Get all factor definitions (without compute functions) for the UI.
 */
export function getFactorDefinitions(): FactorDefinition[] {
  return FACTOR_REGISTRY.map(({ compute, ...def }) => def);
}

/**
 * Get a factor by ID.
 */
export function getFactorById(id: string): ComputeFactor | undefined {
  return FACTOR_REGISTRY.find(f => f.id === id);
}

/**
 * Get all factors in a category.
 */
export function getFactorsByCategory(category: FactorCategory): ComputeFactor[] {
  return FACTOR_REGISTRY.filter(f => f.category === category);
}

/**
 * Get all unique categories.
 */
export function getCategories(): FactorCategory[] {
  const cats = new Set(FACTOR_REGISTRY.map(f => f.category));
  return Array.from(cats);
}

/**
 * Pretty label for a category.
 */
export const CATEGORY_LABELS: Record<FactorCategory, string> = {
  value: "Value",
  quality: "Quality",
  growth: "Growth",
  momentum: "Momentum",
  risk: "Risk",
  short_interest: "Short Interest",
  sentiment: "Sentiment",
  liquidity: "Liquidity",
  dividend: "Dividend",
};

// ---------------------------------------------------------------------------
// Default Factor Coverage (mock mode — all factors use synthetic data)
// ---------------------------------------------------------------------------
// These defaults reflect the current state when running on mock data.
// When real data is ingested, the factor_coverage DB table overrides these.

const DEFAULT_COVERAGE: Record<string, FactorCoverage> = {
  // Value — will come from DART (Phase 3), proxy from pykrx (Phase 2)
  earnings_yield:     { preferredSource: "dart", fallbackSource: "pykrx", dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  book_to_market:     { preferredSource: "dart", fallbackSource: "pykrx", dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  sales_yield:        { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  cf_yield:           { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  ev_ebitda:          { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  dividend_yield:     { preferredSource: "pykrx", fallbackSource: null,  dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  // Quality — DART only
  roe:                { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  roa:                { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  gross_profitability:{ preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  operating_margin:   { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  debt_to_equity:     { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  interest_coverage:  { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  // Growth — DART only
  revenue_growth:     { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  eps_growth:         { preferredSource: "dart", fallbackSource: "pykrx", dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  op_income_growth:   { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  fcf_growth:         { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  // Momentum — marcap/price data (available in Phase 1)
  momentum_12_1:      { preferredSource: "marcap", fallbackSource: null,  dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  momentum_6m:        { preferredSource: "marcap", fallbackSource: null,  dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  momentum_3m:        { preferredSource: "marcap", fallbackSource: null,  dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  reversal_1m:        { preferredSource: "marcap", fallbackSource: null,  dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  dist_52w_high:      { preferredSource: "marcap", fallbackSource: null,  dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // Risk & Liquidity — marcap/price data
  volatility_60d:     { preferredSource: "marcap", fallbackSource: null,  dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  turnover_ratio:     { preferredSource: "marcap", fallbackSource: null,  dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // Short Interest — pykrx (Phase 2)
  short_ratio:        { preferredSource: "pykrx", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
  short_balance_ratio:{ preferredSource: "pykrx", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
};

/**
 * Get the default coverage for a factor (used when no DB is connected).
 */
export function getDefaultCoverage(factorId: string): FactorCoverage {
  return DEFAULT_COVERAGE[factorId] ?? {
    preferredSource: null,
    fallbackSource: null,
    dataStatus: "unavailable" as FactorDataStatus,
    usesMockData: true,
    pointInTimeSafe: false,
    coverageRatio: 0,
  };
}

/**
 * Get factor definitions enriched with coverage metadata.
 * In mock mode, uses DEFAULT_COVERAGE. When DB is connected,
 * the server should fetch from the factor_coverage table.
 */
export function getFactorDefinitionsWithCoverage(): (FactorDefinition & { coverage: FactorCoverage })[] {
  return FACTOR_REGISTRY.map(({ compute, ...def }) => ({
    ...def,
    coverage: getDefaultCoverage(def.id),
  }));
}

/**
 * UI badge color for data status.
 */
export function dataStatusColor(status: FactorDataStatus): string {
  switch (status) {
    case "real": return "text-green-600 bg-green-50 border-green-200";
    case "proxy": return "text-amber-600 bg-amber-50 border-amber-200";
    case "mock": return "text-blue-600 bg-blue-50 border-blue-200";
    case "unavailable": return "text-gray-400 bg-gray-50 border-gray-200";
  }
}

/**
 * UI label for data status.
 */
export function dataStatusLabel(status: FactorDataStatus): string {
  switch (status) {
    case "real": return "Real Data";
    case "proxy": return "Proxy (KRX)";
    case "mock": return "Mock Data";
    case "unavailable": return "Unavailable";
  }
}
