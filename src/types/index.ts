// =============================================================================
// Korean Stock Ranker - Core Type Definitions
// =============================================================================
// These types are used throughout the app. They are independent of the database
// schema so the app can run with mock data or real Supabase data.
// =============================================================================

// ---------------------------------------------------------------------------
// Market & Stock
// ---------------------------------------------------------------------------

export type Market = "KOSPI" | "KOSDAQ";

export interface Stock {
  ticker: string;          // e.g. "005930" (Samsung Electronics)
  name: string;            // Korean name
  nameEn?: string;         // English name
  market: Market;
  sector?: string;         // GICS or KRX sector
  industry?: string;       // Sub-industry
  listingDate?: string;    // ISO date
  isActive: boolean;
  /** Flags for universe filtering */
  isSpac?: boolean;
  isPreferred?: boolean;   // 우선주
  isEtf?: boolean;
  isReit?: boolean;
  isFinancial?: boolean;
  isHolding?: boolean;
}

// ---------------------------------------------------------------------------
// Price & Market Data
// ---------------------------------------------------------------------------

export interface DailyPrice {
  ticker: string;
  date: string;            // ISO date
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  tradingValue: number;    // 거래대금 in KRW
  marketCap: number;       // 시가총액 in KRW
  sharesOutstanding: number;
}

// ---------------------------------------------------------------------------
// Financial Statements
// ---------------------------------------------------------------------------

/**
 * Point-in-time financial data.
 * filingDate = when DART published the report (the date it was knowable).
 * periodEnd  = the fiscal period the data covers.
 * We NEVER use data before its filingDate to avoid lookahead bias.
 */
export interface FinancialStatement {
  ticker: string;
  periodEnd: string;       // e.g. "2024-12-31"
  periodType: "annual" | "quarterly";
  filingDate: string;      // When the filing was published on DART
  // Income Statement (단위: KRW)
  revenue: number | null;
  costOfRevenue: number | null;
  grossProfit: number | null;
  operatingIncome: number | null;
  netIncome: number | null;
  eps: number | null;
  // Balance Sheet
  totalAssets: number | null;
  totalLiabilities: number | null;
  totalEquity: number | null;
  bookValuePerShare: number | null;
  currentAssets: number | null;
  currentLiabilities: number | null;
  cash: number | null;
  shortTermDebt: number | null;
  longTermDebt: number | null;
  totalDebt: number | null;
  // Cash Flow Statement
  operatingCashFlow: number | null;
  capitalExpenditure: number | null;
  /** Alias for capitalExpenditure used in factors.ts for brevity. */
  capex?: number | null;
  freeCashFlow: number | null;
  dividendsPaid: number | null;
  // Derived (can be computed but stored for convenience)
  ebitda: number | null;
  interestExpense: number | null;
  depreciation: number | null;
  sharesOutstanding: number | null;
}

// ---------------------------------------------------------------------------
// Short Selling
// ---------------------------------------------------------------------------

export interface ShortSellingData {
  ticker: string;
  date: string;
  shortVolume: number;        // 공매도 거래량
  shortValue: number;         // 공매도 거래대금
  shortBalance: number;       // 공매도 잔고수량
  shortBalanceValue: number;  // 공매도 잔고금액
  shortRatio: number;         // 공매도 비중 (%)
}

// ---------------------------------------------------------------------------
// Universe
// ---------------------------------------------------------------------------

export interface UniverseConfig {
  id: string;
  name: string;
  description?: string;
  rules: UniverseRule[];
}

export interface UniverseRule {
  type:
    | "market"           // KOSPI, KOSDAQ, or both
    | "minMarketCap"     // minimum market cap in KRW
    | "minLiquidity"     // minimum daily trading value
    | "excludeFinancials"
    | "excludeSpacs"
    | "excludePreferred"
    | "excludeEtfs"
    | "excludeReits"
    | "excludeHoldings"
    | "excludeSectors"   // exclude specific sectors
    | "maxStocks";       // cap the universe size by market cap
  value: string | number | boolean | string[];
}

// ---------------------------------------------------------------------------
// Factors
// ---------------------------------------------------------------------------

export type FactorDirection = "higher_is_better" | "lower_is_better";

export type FactorCategory =
  | "value"
  | "quality"
  | "growth"
  | "momentum"
  | "risk"
  | "short_interest"
  | "sentiment"
  | "liquidity"
  | "dividend";

export interface FactorDefinition {
  id: string;
  name: string;
  description: string;
  category: FactorCategory;
  direction: FactorDirection;
  /** How to compute this factor from raw stock data */
  formula: string;          // Human-readable formula description
  /** Frequency at which this factor should be refreshed */
  frequency: "daily" | "weekly" | "monthly" | "quarterly";
  /** Whether this factor is available in the MVP with free data */
  availableInMvp: boolean;
  /** Data source required */
  dataSource: string;
  /** Data coverage metadata (populated from factor_coverage table or defaults) */
  coverage?: FactorCoverage;
}

/**
 * Data availability status for a factor.
 * Tracks which data source is active and whether we're using real or mock data.
 */
export type FactorDataStatus = "real" | "proxy" | "mock" | "unavailable";

export interface FactorCoverage {
  /** Which data source is actually providing this factor's data */
  preferredSource: "dart" | "pykrx" | "marcap" | "price" | "estimates" | null;
  /** Fallback source if preferred is unavailable */
  fallbackSource: "dart" | "pykrx" | "marcap" | null;
  /** Current status label */
  dataStatus: FactorDataStatus;
  /** Whether this factor uses mock/synthetic data right now */
  usesMockData: boolean;
  /** Whether this factor is point-in-time safe (no lookahead bias) */
  pointInTimeSafe: boolean;
  /** Fraction of the universe that has valid data (0.0 to 1.0) */
  coverageRatio: number;
}

/**
 * A computed factor value for a single stock on a specific date.
 */
export interface FactorValue {
  ticker: string;
  factorId: string;
  date: string;
  rawValue: number | null;      // The computed raw value
  percentileRank: number | null; // 0-100, after ranking within universe
}

// ---------------------------------------------------------------------------
// Ranking System
// ---------------------------------------------------------------------------

/**
 * A ranking system is a tree of nodes.
 * The root node has type "composite".
 * Category nodes group factors.
 * Factor nodes reference a factor definition.
 *
 * Example tree:
 *   Composite (100%)
 *   ├── Value (40%)
 *   │   ├── Earnings Yield (50%)
 *   │   └── Book-to-Market (50%)
 *   ├── Quality (30%)
 *   │   ├── ROE (60%)
 *   │   └── Gross Profitability (40%)
 *   └── Momentum (30%)
 *       └── 12-1 Month Momentum (100%)
 */
export interface RankingSystem {
  id: string;
  name: string;
  description?: string;
  createdAt: string;
  updatedAt: string;
  /** The root node of the ranking tree */
  tree: RankingNode;
  /** Universe configuration to rank against */
  universeId?: string;
  /** Ranking options */
  options: RankingOptions;
}

export interface RankingOptions {
  /** How to handle missing factor values */
  missingValueHandling: "exclude" | "median" | "worst" | "neutral";
  /** Whether to winsorize extreme values before ranking */
  winsorize: boolean;
  winsorizePercentile?: number;  // e.g. 0.05 for 5th/95th
  /** Use z-score normalization instead of percentile ranking */
  useZScore: boolean;
  /** Rank within sectors (sector-neutral) */
  sectorNeutral: boolean;
  /** Rank within industries (industry-neutral) */
  industryNeutral: boolean;
}

export type RankingNodeType = "composite" | "category" | "factor";

export interface RankingNode {
  id: string;
  type: RankingNodeType;
  name: string;
  /** Weight within its parent (0-100), weights of siblings should sum to 100 */
  weight: number;
  /** For factor nodes: which factor definition to use */
  factorId?: string;
  /** For factor nodes: override the default direction */
  directionOverride?: FactorDirection;
  /** Child nodes (for composite and category nodes) */
  children?: RankingNode[];
}

// ---------------------------------------------------------------------------
// Ranking Results
// ---------------------------------------------------------------------------

export interface RankingResult {
  /** Which ranking system produced this */
  rankingSystemId: string;
  /** The date this ranking was run for */
  date: string;
  /** Ranked list of stocks with scores */
  rankings: StockRanking[];
  /** Metadata */
  universeSize: number;
  computedAt: string;
  // ----- Snapshot-level coverage / scoring metadata (DB mode only) -----
  universeName?: string | null;
  scoringMethod?: string;            // e.g. "percentile_rank"
  missingCategoryPolicy?: string;    // "neutral" | "exclude" | "renormalize"
  thresholds?: {
    min_active_weight_coverage?: number;
    min_category_count?: number;
    min_factor_count?: number;
  } | null;
  globallyUnavailableCategories?: string[];
  globallyActiveCategories?: string[];
  categoryWeights?: Record<string, number> | null;
  passedCount?: number;
  insufficientCount?: number;
  includeInsufficientCoverage?: boolean;
}

export type CategoryStatus =
  | "available"
  | "missing_imputed"
  | "missing"
  | "missing_renormalized"
  | "globally_unavailable";

export interface CategoryScoreDetail {
  score: number | null;
  weight: number;
  coverage: string;        // e.g. "9/12"
  status: CategoryStatus;
}

export interface StockRanking {
  rank: number | null;
  ticker: string;
  name: string;
  nameEn?: string;
  market: Market;
  sector?: string;
  industry?: string;
  marketCap: number;
  /** Final composite score (0-100). May be null if no composite computable. */
  compositeScore: number;
  /** Scores per category node (legacy simple shape: {name: score}) */
  categoryScores: Record<string, number>;
  /** Per-category detail with status flags (status: available/imputed/missing/etc) */
  categoryDetails?: Record<string, CategoryScoreDetail>;
  /** Scores per individual factor */
  factorScores: Record<string, {
    rawValue: number | null;
    percentileRank: number;
  }>;
  // ----- Coverage metadata (post-fix snapshots only) -----
  /** "passed" = passed minimum coverage; "insufficient" = failed and excluded from main ranking */
  coverageStatus?: "passed" | "insufficient";
  passesMinimum?: boolean;
  /** Real-data weight / globally-active total weight, in [0, 1] */
  activeWeightCoverage?: number;
  /** Includes neutral-imputed weight, in [0, 1] */
  compositeWeightUsed?: number;
  activeCategoryCount?: number;
  activeCategories?: string[];
  imputedCategories?: string[];
  factorCount?: number;
  failureReasons?: string[];
  /** Direct link to the latest DART source report (the filing the financials came from) */
  dartUrl?: string | null;
  dartFilingDate?: string | null;
  /** Median daily trading value (KRW) over the last 20 trading days — liquidity gauge */
  medianTurnover?: number | null;
}

// ---------------------------------------------------------------------------
// Backtest (Milestone 6 - stubbed types)
// ---------------------------------------------------------------------------

export interface BacktestConfig {
  id: string;
  name: string;
  rankingSystemId: string;
  startDate: string;
  endDate: string;
  rebalanceFrequency: "weekly" | "monthly" | "quarterly";
  topN: number;              // Buy top N stocks
  transactionCostBps: number; // e.g. 30 = 0.30%
  slippageBps: number;
  benchmark: "KOSPI" | "KOSDAQ" | "KOSPI200";
}

export interface BacktestResult {
  configId: string;
  totalReturn: number;
  annualizedReturn: number;
  sharpeRatio: number;
  maxDrawdown: number;
  benchmarkReturn: number;
  equityCurve: { date: string; value: number; benchmark: number }[];
  holdings: BacktestHolding[][];
}

export interface BacktestHolding {
  date: string;
  ticker: string;
  name: string;
  weight: number;
  entryPrice: number;
  currentPrice: number;
  returnPct: number;
}
