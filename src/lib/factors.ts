// =============================================================================
// Factor Library for Korean Equities — P123-Inspired Multi-Factor Framework
// =============================================================================
// Each factor is defined with:
// - A unique ID (matches Python factor_definitions.py)
// - Human-readable name and description
// - Category (value, quality, growth, momentum, risk, liquidity, sentiment, etc.)
// - Direction (higher_is_better or lower_is_better)
// - A compute function that takes stock data and returns the raw value
//
// QUANT NOTES:
// - All factor computations use point-in-time data to avoid lookahead bias.
// - Financial data is only used if its filingDate <= the ranking date.
// - Price-based factors use the price as of the ranking date.
// - Growth factors compare the latest available annual period to the prior year.
// - Factor IDs must match the Python factor_definitions.py for ranking consistency.
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

const peTtmInv: ComputeFactor = {
  id: "pe_ttm_inv",
  name: "P/E TTM (inverted)",
  description: "Earnings yield = Net Income TTM / Market Cap. Higher means cheaper.",
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

const priceBook: ComputeFactor = {
  id: "price_book",
  name: "Price / Book",
  description: "Book-to-Market = Total Equity / Market Cap. Higher means cheaper.",
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

const priceSalesTtmInv: ComputeFactor = {
  id: "price_sales_ttm_inv",
  name: "Price/Sales TTM (inverted)",
  description: "Sales yield = Revenue TTM / Market Cap. Higher means cheaper.",
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

const fcfMcap: ComputeFactor = {
  id: "fcf_mcap",
  name: "FCF / Market Cap",
  description: "Free cash flow yield. Higher means cheaper.",
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

const ebitdaEv: ComputeFactor = {
  id: "ebitda_ev",
  name: "EBITDA / EV",
  description: "EBITDA divided by Enterprise Value. Higher means cheaper.",
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
  category: "value",
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

const evSalesTtmInv: ComputeFactor = {
  id: "ev_sales_ttm_inv",
  name: "EV/Sales TTM (inverted)",
  description: "Revenue / Enterprise Value. Higher means cheaper.",
  category: "value",
  direction: "higher_is_better",
  formula: "Revenue / (Market Cap + Total Debt - Cash)",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials + price",
  compute: ({ financials, latestPrice }) => {
    if (!financials?.revenue || !latestPrice.marketCap) return null;
    const cash = financials.cash ?? 0;
    const debt = financials.totalDebt ?? 0;
    const ev = latestPrice.marketCap + debt - cash;
    if (ev <= 0) return null;
    return financials.revenue / ev;
  },
};

const grossProfitEv: ComputeFactor = {
  id: "gross_profit_ev",
  name: "Gross Profit / EV",
  description: "Gross Profit divided by Enterprise Value. Higher is better.",
  category: "value",
  direction: "higher_is_better",
  formula: "Gross Profit / (Market Cap + Total Debt - Cash)",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials + price",
  compute: ({ financials, latestPrice }) => {
    if (!financials?.grossProfit || !latestPrice.marketCap) return null;
    const cash = financials.cash ?? 0;
    const debt = financials.totalDebt ?? 0;
    const ev = latestPrice.marketCap + debt - cash;
    if (ev <= 0) return null;
    return financials.grossProfit / ev;
  },
};

const ocfMcap: ComputeFactor = {
  id: "ocf_mcap",
  name: "Operating CF / Market Cap",
  description: "Operating cash flow yield.",
  category: "value",
  direction: "higher_is_better",
  formula: "Operating Cash Flow / Market Cap",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials + price",
  compute: ({ financials, latestPrice }) => {
    if (!financials?.operatingCashFlow || !latestPrice.marketCap) return null;
    if (latestPrice.marketCap === 0) return null;
    return financials.operatingCashFlow / latestPrice.marketCap;
  },
};

const ufcfEv: ComputeFactor = {
  id: "ufcf_ev",
  name: "Unlevered FCF / EV",
  description: "(OCF - Capex + 0.8 * Interest) / EV.",
  category: "value",
  direction: "higher_is_better",
  formula: "(Operating CF - CapEx + 0.8 * Interest Expense) / EV",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials + price",
  compute: ({ financials, latestPrice }) => {
    if (!latestPrice.marketCap) return null;
    const ocf = financials?.operatingCashFlow ?? 0;
    const capex = financials?.capex ?? 0;
    const interest = financials?.interestExpense ?? 0;
    const cash = financials?.cash ?? 0;
    const debt = financials?.totalDebt ?? 0;
    const ufcf = ocf - capex + 0.8 * Math.abs(interest);
    const ev = latestPrice.marketCap + debt - cash;
    if (ev <= 0) return null;
    return ufcf / ev;
  },
};

// ---------------------------------------------------------------------------
// QUALITY Factors
// ---------------------------------------------------------------------------

const roeTtm: ComputeFactor = {
  id: "roe_ttm",
  name: "ROE TTM",
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

const roaTtm: ComputeFactor = {
  id: "roa_ttm",
  name: "ROA TTM",
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

const grossProfitAssets: ComputeFactor = {
  id: "gross_profit_assets",
  name: "Gross Profit / Assets",
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

const operatingMarginTtm: ComputeFactor = {
  id: "operating_margin_ttm",
  name: "Operating Margin TTM",
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

const grossMarginTtm: ComputeFactor = {
  id: "gross_margin_ttm",
  name: "Gross Margin TTM",
  description: "Gross profit / revenue.",
  category: "quality",
  direction: "higher_is_better",
  formula: "Gross Profit / Revenue",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials }) => {
    if (!financials?.grossProfit || !financials.revenue) return null;
    if (financials.revenue === 0) return null;
    return financials.grossProfit / financials.revenue;
  },
};

const assetTurnoverTtm: ComputeFactor = {
  id: "asset_turnover_ttm",
  name: "Asset Turnover TTM",
  description: "Revenue / total assets.",
  category: "quality",
  direction: "higher_is_better",
  formula: "Revenue / Total Assets",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials }) => {
    if (!financials?.revenue || !financials.totalAssets) return null;
    if (financials.totalAssets === 0) return null;
    return financials.revenue / financials.totalAssets;
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

const interestCoverageTtm: ComputeFactor = {
  id: "interest_coverage_ttm",
  name: "Interest Coverage TTM",
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

const salesGrowthYoy: ComputeFactor = {
  id: "sales_growth_yoy",
  name: "Sales Growth YoY",
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

const epsGrowthYoy: ComputeFactor = {
  id: "eps_growth_yoy",
  name: "EPS Growth YoY",
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

const opIncomeGrowthYoy: ComputeFactor = {
  id: "op_income_growth_yoy",
  name: "Op Income Growth YoY",
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

const netIncomeGrowthYoy: ComputeFactor = {
  id: "net_income_growth_yoy",
  name: "Net Income Growth YoY",
  description: "Year-over-year net income growth.",
  category: "growth",
  direction: "higher_is_better",
  formula: "(NetIncome_t - NetIncome_t-1) / |NetIncome_t-1|",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials, priorFinancials }) => {
    if (!financials?.netIncome || !priorFinancials?.netIncome) return null;
    if (priorFinancials.netIncome === 0) return null;
    return (financials.netIncome - priorFinancials.netIncome) / Math.abs(priorFinancials.netIncome);
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

const volatility60d: ComputeFactor = {
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

const volatility252d: ComputeFactor = {
  id: "volatility_252d",
  name: "252-Day Volatility",
  description: "Annualized standard deviation of daily returns over 252 days. Lower is less risky.",
  category: "risk",
  direction: "lower_is_better",
  formula: "std(daily returns, 252d) * sqrt(252)",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => {
    const recent = priceHistory.slice(-260);
    if (recent.length < 200) return null;

    const returns: number[] = [];
    for (let i = 1; i < recent.length; i++) {
      if (recent[i - 1].close === 0) continue;
      returns.push((recent[i].close - recent[i - 1].close) / recent[i - 1].close);
    }

    if (returns.length < 150) return null;

    const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
    const variance = returns.reduce((a, b) => a + (b - mean) ** 2, 0) / (returns.length - 1);
    return Math.sqrt(variance) * Math.sqrt(252); // annualize
  },
};

const maxDrawdown252d: ComputeFactor = {
  id: "max_drawdown_252d",
  name: "Max Drawdown 252d",
  description: "Maximum drawdown over 252 trading days. Lower is better (less risky).",
  category: "risk",
  direction: "lower_is_better",
  formula: "min(cumulative return) over 252 days",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => {
    const recent = priceHistory.slice(-260);
    if (recent.length < 200) return null;

    let peak = recent[0].close;
    let maxDD = 0;
    for (let i = 1; i < recent.length; i++) {
      if (recent[i].close > peak) {
        peak = recent[i].close;
      } else {
        const dd = (peak - recent[i].close) / peak;
        if (dd > maxDD) {
          maxDD = dd;
        }
      }
    }
    return -maxDD; // negative so that lower (more negative) is worse risk
  },
};

const shareTurnover65d: ComputeFactor = {
  id: "share_turnover_65d",
  name: "Share Turnover 65d",
  description: "Median daily volume / shares outstanding over 65 days. Proxy for liquidity.",
  category: "liquidity",
  direction: "higher_is_better",
  formula: "median(Volume, 65d) / Shares Outstanding",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory, latestPrice }) => {
    const recent = priceHistory.slice(-70);
    if (recent.length < 20) return null;
    if (!latestPrice.sharesOutstanding || latestPrice.sharesOutstanding === 0) return null;

    const volumes = recent.map(p => p.volume).sort((a, b) => a - b);
    const medianVolume = volumes[Math.floor(volumes.length / 2)];
    return medianVolume / latestPrice.sharesOutstanding;
  },
};

// ---------------------------------------------------------------------------
// SHORT INTEREST Factors
// ---------------------------------------------------------------------------

const shortInterestPct: ComputeFactor = {
  id: "short_interest_pct",
  name: "Short Interest % Shares",
  description: "Short balance as % of shares outstanding. Lower = less bearish sentiment.",
  category: "sentiment",
  direction: "lower_is_better",
  formula: "Short Balance / Shares Outstanding",
  frequency: "daily",
  availableInMvp: false,
  dataSource: "KRX short selling data",
  compute: ({ shortSelling, latestPrice }) => {
    if (!shortSelling?.shortBalance) return null;
    if (!latestPrice.sharesOutstanding || latestPrice.sharesOutstanding === 0) return null;
    return shortSelling.shortBalance / latestPrice.sharesOutstanding;
  },
};

// Price change factors
const priceChange120d: ComputeFactor = {
  id: "price_change_120d",
  name: "Price Change 120d",
  description: "Close(0) / Close(120) - 1. 120-day price return.",
  category: "momentum",
  direction: "higher_is_better",
  formula: "(Price_t - Price_t-120) / Price_t-120",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => {
    if (priceHistory.length < 130) return null;
    const startIdx = priceHistory.length - 130;
    const startPrice = priceHistory[startIdx].close;
    const endPrice = priceHistory[priceHistory.length - 1].close;
    if (startPrice === 0) return null;
    return (endPrice - startPrice) / startPrice;
  },
};

const priceChange180d: ComputeFactor = {
  id: "price_change_180d",
  name: "Price Change 180d",
  description: "Close(0) / Close(180) - 1. 180-day price return.",
  category: "momentum",
  direction: "higher_is_better",
  formula: "(Price_t - Price_t-180) / Price_t-180",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => {
    if (priceHistory.length < 190) return null;
    const startIdx = priceHistory.length - 190;
    const startPrice = priceHistory[startIdx].close;
    const endPrice = priceHistory[priceHistory.length - 1].close;
    if (startPrice === 0) return null;
    return (endPrice - startPrice) / startPrice;
  },
};

// Technical factors
const upDownRatio20: ComputeFactor = {
  id: "up_down_ratio_20",
  name: "Up/Down Ratio 20d",
  description: "Ratio of up days to down days over 20 trading days.",
  category: "momentum",
  direction: "higher_is_better",
  formula: "Count(Close_i > Close_i-1) / Count(Close_i < Close_i-1)",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => {
    const recent = priceHistory.slice(-25);
    if (recent.length < 20) return null;
    let upDays = 0;
    let downDays = 0;
    for (let i = 1; i < recent.length; i++) {
      if (recent[i].close > recent[i - 1].close) upDays++;
      else if (recent[i].close < recent[i - 1].close) downDays++;
    }
    if (downDays === 0) return upDays > 0 ? 2.0 : 1.0;
    return upDays / downDays;
  },
};

const upDownRatio60: ComputeFactor = {
  id: "up_down_ratio_60",
  name: "Up/Down Ratio 60d",
  description: "Ratio of up days to down days over 60 trading days.",
  category: "momentum",
  direction: "higher_is_better",
  formula: "Count(Close_i > Close_i-1) / Count(Close_i < Close_i-1)",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => {
    const recent = priceHistory.slice(-65);
    if (recent.length < 60) return null;
    let upDays = 0;
    let downDays = 0;
    for (let i = 1; i < recent.length; i++) {
      if (recent[i].close > recent[i - 1].close) upDays++;
      else if (recent[i].close < recent[i - 1].close) downDays++;
    }
    if (downDays === 0) return upDays > 0 ? 2.0 : 1.0;
    return upDays / downDays;
  },
};

const upDownRatio120: ComputeFactor = {
  id: "up_down_ratio_120",
  name: "Up/Down Ratio 120d",
  description: "Ratio of up days to down days over 120 trading days.",
  category: "momentum",
  direction: "higher_is_better",
  formula: "Count(Close_i > Close_i-1) / Count(Close_i < Close_i-1)",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => {
    const recent = priceHistory.slice(-130);
    if (recent.length < 120) return null;
    let upDays = 0;
    let downDays = 0;
    for (let i = 1; i < recent.length; i++) {
      if (recent[i].close > recent[i - 1].close) upDays++;
      else if (recent[i].close < recent[i - 1].close) downDays++;
    }
    if (downDays === 0) return upDays > 0 ? 2.0 : 1.0;
    return upDays / downDays;
  },
};

const rsi200: ComputeFactor = {
  id: "rsi_200",
  name: "RSI 200",
  description: "Relative Strength Index over 200 days. Scales to 0-100.",
  category: "momentum",
  direction: "higher_is_better",
  formula: "100 - (100 / (1 + (avg gain / avg loss)))",
  frequency: "daily",
  availableInMvp: true,
  dataSource: "Daily prices",
  compute: ({ priceHistory }) => {
    const recent = priceHistory.slice(-210);
    if (recent.length < 200) return null;

    let gains = 0;
    let losses = 0;
    for (let i = 1; i < recent.length; i++) {
      const change = recent[i].close - recent[i - 1].close;
      if (change > 0) gains += change;
      else if (change < 0) losses += Math.abs(change);
    }

    const avgGain = gains / 200;
    const avgLoss = losses / 200;
    if (avgLoss === 0) return avgGain > 0 ? 100 : 50;

    const rs = avgGain / avgLoss;
    return 100 - (100 / (1 + rs));
  },
};

// ---------------------------------------------------------------------------
// Additional factors used by the live P123 ranking (server-driven)
// ---------------------------------------------------------------------------
// These factors are computed in the Python pipeline and stored in
// factor_snapshots, but the client-side `compute` functions below are
// simplified or null-returning placeholders. The real values come from the
// DB via the data service. Keeping the definitions here lets the editor
// render proper names/descriptions for the P123 tree.

const buybackYieldYoy: ComputeFactor = {
  id: "buyback_yield_yoy",
  name: "Buyback Yield",
  description: "YoY change in shares outstanding (negative = buyback, positive = dilution). Sibling of dividend yield.",
  category: "value",
  direction: "higher_is_better",
  formula: "(prior_shares - current_shares) / current_shares",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: () => null,
};

const fcfToAssets: ComputeFactor = {
  id: "fcf_to_assets",
  name: "FCF / Assets",
  description: "Free Cash Flow (TTM) / Total Assets. Capital-efficiency proxy less susceptible to accruals than ROA.",
  category: "quality",
  direction: "higher_is_better",
  formula: "Free Cash Flow / Total Assets",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: ({ financials }) => {
    if (!financials?.freeCashFlow || !financials.totalAssets) return null;
    if (financials.totalAssets === 0) return null;
    return financials.freeCashFlow / financials.totalAssets;
  },
};

const cashToAssets: ComputeFactor = {
  id: "cash_to_assets",
  name: "Cash / Assets",
  description: "Cash & equivalents / Total Assets. Balance-sheet liquidity; high = optionality & downturn cushion.",
  category: "quality",
  direction: "higher_is_better",
  formula: "Cash & Equivalents / Total Assets",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: () => null,
};

const ocfGrowthYoy: ComputeFactor = {
  id: "ocf_growth_yoy",
  name: "OCF Growth YoY",
  description: "Year-over-year growth in operating cash flow.",
  category: "growth",
  direction: "higher_is_better",
  formula: "(OCF_current - OCF_prior) / |OCF_prior|",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: () => null,
};

const fcfGrowthYoy: ComputeFactor = {
  id: "fcf_growth_yoy",
  name: "FCF Growth YoY",
  description: "Year-over-year growth in free cash flow.",
  category: "growth",
  direction: "higher_is_better",
  formula: "(FCF_current - FCF_prior) / |FCF_prior|",
  frequency: "quarterly",
  availableInMvp: true,
  dataSource: "DART financials",
  compute: () => null,
};

const industryMomentum26w: ComputeFactor = {
  id: "industry_momentum_26w",
  name: "Industry 26W Momentum",
  description: "26-week return of the stock's industry (KSIC). Captures sector-level reversion/momentum.",
  category: "momentum",
  direction: "higher_is_better",
  formula: "Industry index level today / 26W ago - 1",
  frequency: "weekly",
  availableInMvp: true,
  dataSource: "Industry classifications + daily prices",
  compute: () => null,
};

const industryMomentum52w: ComputeFactor = {
  id: "industry_momentum_52w",
  name: "Industry 52W Momentum",
  description: "52-week return of the stock's industry (KSIC).",
  category: "momentum",
  direction: "higher_is_better",
  formula: "Industry index level today / 52W ago - 1",
  frequency: "weekly",
  availableInMvp: true,
  dataSource: "Industry classifications + daily prices",
  compute: () => null,
};

const insiderNetBuying90d: ComputeFactor = {
  id: "insider_net_buying_90d",
  name: "Insider Net Buying 90d",
  description: "Net insider share purchases over trailing 90 days, as a fraction of shares outstanding. Higher = more bullish insider sentiment.",
  category: "sentiment",
  direction: "higher_is_better",
  formula: "(buy_shares - sell_shares) / shares_outstanding (trailing 90d)",
  frequency: "weekly",
  availableInMvp: true,
  dataSource: "DART insider filings (임원·주요주주 보고)",
  compute: () => null,
};

// ---------------------------------------------------------------------------
// Factor Registry
// ---------------------------------------------------------------------------

export const FACTOR_REGISTRY: ComputeFactor[] = [
  // === VALUE ===
  peTtmInv,
  ebitdaEv,
  priceSalesTtmInv,
  evSalesTtmInv,
  grossProfitEv,
  fcfMcap,
  ocfMcap,
  ufcfEv,
  priceBook,
  dividendYield,
  buybackYieldYoy,
  // === QUALITY ===
  roeTtm,
  roaTtm,
  grossProfitAssets,
  fcfToAssets,
  cashToAssets,
  operatingMarginTtm,
  grossMarginTtm,
  assetTurnoverTtm,
  debtToEquity,
  interestCoverageTtm,
  // === GROWTH ===
  salesGrowthYoy,
  opIncomeGrowthYoy,
  epsGrowthYoy,
  netIncomeGrowthYoy,
  ocfGrowthYoy,
  fcfGrowthYoy,
  // === MOMENTUM ===
  priceChange120d,
  priceChange180d,
  upDownRatio20,
  upDownRatio60,
  upDownRatio120,
  rsi200,
  momentum12m1m,
  momentum6m,
  momentum3m,
  reversal1m,
  distanceFrom52wHigh,
  industryMomentum26w,
  industryMomentum52w,
  // === RISK ===
  volatility60d,
  volatility252d,
  maxDrawdown252d,
  // === LIQUIDITY ===
  shareTurnover65d,
  // === SENTIMENT ===
  shortInterestPct,
  insiderNetBuying90d,
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
  // === VALUE (earnings-based) ===
  pe_ttm_inv:         { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  ebitda_ev:          { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === VALUE (sales-based) ===
  price_sales_ttm_inv:{ preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  ev_sales_ttm_inv:   { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  gross_profit_ev:    { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === VALUE (fcf-based) ===
  fcf_mcap:           { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  ocf_mcap:           { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  ufcf_ev:            { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === VALUE (asset-based) ===
  price_book:         { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  dividend_yield:     { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === QUALITY (margins) ===
  operating_margin_ttm: { preferredSource: "dart", fallbackSource: null,  dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  gross_margin_ttm:   { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === QUALITY (return on capital) ===
  roe_ttm:            { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  roa_ttm:            { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  gross_profit_assets:{ preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === QUALITY (turnover) ===
  asset_turnover_ttm: { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === QUALITY (finances) ===
  debt_to_equity:     { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  interest_coverage_ttm: { preferredSource: "dart", fallbackSource: null, dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === GROWTH ===
  sales_growth_yoy:   { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  op_income_growth_yoy: { preferredSource: "dart", fallbackSource: null,  dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  eps_growth_yoy:     { preferredSource: "dart", fallbackSource: null,    dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  net_income_growth_yoy: { preferredSource: "dart", fallbackSource: null, dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === MOMENTUM (price changes) ===
  price_change_120d:  { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  price_change_180d:  { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === MOMENTUM (technical) ===
  up_down_ratio_20:   { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  up_down_ratio_60:   { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  up_down_ratio_120:  { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  rsi_200:            { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === MOMENTUM (quarterly returns) ===
  momentum_12_1:      { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  momentum_6m:        { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  momentum_3m:        { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  reversal_1m:        { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  dist_52w_high:      { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === RISK ===
  volatility_252d:    { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  volatility_60d:     { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  max_drawdown_252d:  { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === LIQUIDITY ===
  share_turnover_65d: { preferredSource: "price", fallbackSource: null,   dataStatus: "mock", usesMockData: true, pointInTimeSafe: true,  coverageRatio: 0 },
  // === SENTIMENT ===
  short_interest_pct: { preferredSource: "estimates", fallbackSource: null, dataStatus: "mock", usesMockData: true, pointInTimeSafe: false, coverageRatio: 0 },
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

// ---------------------------------------------------------------------------
// P123-Inspired Ranking System (for client-side use)
// ---------------------------------------------------------------------------

export const P123_INSPIRED_RANKING_SYSTEM = {
  id: "p123-inspired",
  name: "P123 Inspired Korea Multi-Factor",
  tree: {
    id: "root",
    type: "composite" as const,
    name: "P123 Inspired Korea Multi-Factor",
    weight: 100,
    children: [
      // === Core: Value — 25% ===
      {
        id: "cat-value", type: "category" as const, name: "Value", weight: 25,
        children: [
          {
            id: "sub-val-earn", type: "composite" as const, name: "Earnings-Based", weight: 35,
            children: [
              { id: "f-ey", type: "factor" as const, name: "Earnings Yield", weight: 50, factorId: "pe_ttm_inv" },
              { id: "f-ebitda-ev", type: "factor" as const, name: "EBITDA/EV", weight: 50, factorId: "ebitda_ev" },
            ] as any[],
          },
          {
            id: "sub-val-sales", type: "composite" as const, name: "Sales-Based", weight: 30,
            children: [
              { id: "f-ps", type: "factor" as const, name: "Sales Yield", weight: 40, factorId: "price_sales_ttm_inv" },
              { id: "f-evs", type: "factor" as const, name: "Revenue/EV", weight: 30, factorId: "ev_sales_ttm_inv" },
              { id: "f-gpev", type: "factor" as const, name: "Gross Profit/EV", weight: 30, factorId: "gross_profit_ev" },
            ] as any[],
          },
          {
            id: "sub-val-fcf", type: "composite" as const, name: "FCF-Based", weight: 20,
            children: [
              { id: "f-fcfy", type: "factor" as const, name: "FCF Yield", weight: 40, factorId: "fcf_mcap" },
              { id: "f-ocfy", type: "factor" as const, name: "OCF Yield", weight: 30, factorId: "ocf_mcap" },
              { id: "f-ufcf", type: "factor" as const, name: "Unlevered FCF/EV", weight: 30, factorId: "ufcf_ev" },
            ] as any[],
          },
          {
            id: "sub-val-asset", type: "composite" as const, name: "Asset-Based", weight: 15,
            children: [
              { id: "f-pb", type: "factor" as const, name: "Book/Market", weight: 60, factorId: "price_book" },
              { id: "f-divy", type: "factor" as const, name: "Dividend Yield", weight: 40, factorId: "dividend_yield" },
            ] as any[],
          },
        ] as any[],
      },
      // === Core: Quality — 30% ===
      {
        id: "cat-quality", type: "category" as const, name: "Quality", weight: 30,
        children: [
          {
            id: "sub-q-margin", type: "composite" as const, name: "Margins", weight: 25,
            children: [
              { id: "f-opmgn", type: "factor" as const, name: "Operating Margin", weight: 60, factorId: "operating_margin_ttm" },
              { id: "f-gpmgn", type: "factor" as const, name: "Gross Margin", weight: 40, factorId: "gross_margin_ttm" },
            ] as any[],
          },
          {
            id: "sub-q-roc", type: "composite" as const, name: "Return on Capital", weight: 35,
            children: [
              { id: "f-roe", type: "factor" as const, name: "ROE", weight: 35, factorId: "roe_ttm" },
              { id: "f-roa", type: "factor" as const, name: "ROA", weight: 30, factorId: "roa_ttm" },
              { id: "f-gpa", type: "factor" as const, name: "Gross Profit/Assets", weight: 35, factorId: "gross_profit_assets" },
            ] as any[],
          },
          {
            id: "sub-q-turn", type: "composite" as const, name: "Turnover", weight: 15,
            children: [
              { id: "f-at", type: "factor" as const, name: "Asset Turnover", weight: 100, factorId: "asset_turnover_ttm" },
            ] as any[],
          },
          {
            id: "sub-q-fin", type: "composite" as const, name: "Finances", weight: 25,
            children: [
              { id: "f-de", type: "factor" as const, name: "Debt/Equity", weight: 50, factorId: "debt_to_equity" },
              { id: "f-ic", type: "factor" as const, name: "Interest Coverage", weight: 50, factorId: "interest_coverage_ttm" },
            ] as any[],
          },
        ] as any[],
      },
      // === Core: Growth — 15% ===
      {
        id: "cat-growth", type: "category" as const, name: "Growth", weight: 15,
        children: [
          {
            id: "sub-g-sales", type: "composite" as const, name: "Sales Growth", weight: 35,
            children: [
              { id: "f-sg", type: "factor" as const, name: "Sales Growth YoY", weight: 100, factorId: "sales_growth_yoy" },
            ] as any[],
          },
          {
            id: "sub-g-opinc", type: "composite" as const, name: "Op Income Growth", weight: 30,
            children: [
              { id: "f-oig", type: "factor" as const, name: "Op Income Growth YoY", weight: 100, factorId: "op_income_growth_yoy" },
            ] as any[],
          },
          {
            id: "sub-g-eps", type: "composite" as const, name: "EPS Growth", weight: 35,
            children: [
              { id: "f-epsg", type: "factor" as const, name: "EPS Growth YoY", weight: 50, factorId: "eps_growth_yoy" },
              { id: "f-nig", type: "factor" as const, name: "Net Income Growth YoY", weight: 50, factorId: "net_income_growth_yoy" },
            ] as any[],
          },
        ] as any[],
      },
      // === Core: Momentum — 10% ===
      {
        id: "cat-momentum", type: "category" as const, name: "Momentum", weight: 10,
        children: [
          {
            id: "sub-m-price", type: "composite" as const, name: "Price Changes", weight: 35,
            children: [
              { id: "f-pc120", type: "factor" as const, name: "120d Return", weight: 50, factorId: "price_change_120d" },
              { id: "f-pc180", type: "factor" as const, name: "180d Return", weight: 50, factorId: "price_change_180d" },
            ] as any[],
          },
          {
            id: "sub-m-tech", type: "composite" as const, name: "Technical", weight: 35,
            children: [
              { id: "f-udr20", type: "factor" as const, name: "UpDown 20d", weight: 20, factorId: "up_down_ratio_20" },
              { id: "f-udr60", type: "factor" as const, name: "UpDown 60d", weight: 30, factorId: "up_down_ratio_60" },
              { id: "f-udr120", type: "factor" as const, name: "UpDown 120d", weight: 25, factorId: "up_down_ratio_120" },
              { id: "f-rsi200", type: "factor" as const, name: "RSI 200", weight: 25, factorId: "rsi_200" },
            ] as any[],
          },
          {
            id: "sub-m-qtr", type: "composite" as const, name: "Quarterly Returns", weight: 30,
            children: [
              { id: "f-m3", type: "factor" as const, name: "3M Return", weight: 30, factorId: "momentum_3m" },
              { id: "f-m6", type: "factor" as const, name: "6M Return", weight: 35, factorId: "momentum_6m" },
              { id: "f-m121", type: "factor" as const, name: "12-1M Momentum", weight: 35, factorId: "momentum_12_1" },
            ] as any[],
          },
        ] as any[],
      },
      // === Core: Low Volatility — 10% ===
      {
        id: "cat-risk", type: "category" as const, name: "Low Volatility", weight: 10,
        children: [
          { id: "f-vol252", type: "factor" as const, name: "252d Volatility", weight: 40, factorId: "volatility_252d" },
          { id: "f-vol60", type: "factor" as const, name: "60d Volatility", weight: 30, factorId: "volatility_60d" },
          { id: "f-mdd", type: "factor" as const, name: "Max Drawdown 252d", weight: 30, factorId: "max_drawdown_252d" },
        ] as any[],
      },
      // === Core: Sentiment — 10% ===
      {
        id: "cat-sentiment", type: "category" as const, name: "Sentiment", weight: 10,
        children: [
          { id: "f-si", type: "factor" as const, name: "Short Interest", weight: 100, factorId: "short_interest_pct" },
        ] as any[],
      },
    ] as any[],
  },
};

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
