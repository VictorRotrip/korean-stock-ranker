// =============================================================================
// Drizzle ORM Schema for Supabase Postgres
// =============================================================================
// Point-in-time design:
// - financial_statements: periodEnd + filingDate + dataAvailableDate
// - pykrx_fundamentals: KRX-derived valuation proxies (PER, PBR, etc.)
// - daily_prices: OHLCV + market cap from marcap/FinanceData
// - factor_snapshots: precomputed factor values per stock per date
// - ranking_snapshots: reproducible ranking results
// - factor_coverage: metadata about each factor's data availability
//
// Data sources:
// Phase 1: marcap/FinanceData — universe, prices, market cap
// Phase 2: pykrx — PER, PBR, EPS, BPS, DPS, short selling
// Phase 3: DART — full financial statements with filing dates
// =============================================================================

import {
  pgTable,
  text,
  varchar,
  integer,
  bigint,
  doublePrecision,
  boolean,
  date,
  timestamp,
  jsonb,
  primaryKey,
  index,
  uniqueIndex,
} from "drizzle-orm/pg-core";

// ---------------------------------------------------------------------------
// Stocks (universe from marcap / FinanceData)
// ---------------------------------------------------------------------------

export const stocks = pgTable("stocks", {
  ticker: varchar("ticker", { length: 10 }).primaryKey(),
  name: text("name").notNull(),
  nameEn: text("name_en"),
  market: varchar("market", { length: 10 }).notNull(), // KOSPI | KOSDAQ
  sector: text("sector"),
  industry: text("industry"),
  listingDate: date("listing_date"),
  delistingDate: date("delisting_date"),
  isActive: boolean("is_active").default(true).notNull(),
  isSpac: boolean("is_spac").default(false),
  isPreferred: boolean("is_preferred").default(false),
  isEtf: boolean("is_etf").default(false),
  isReit: boolean("is_reit").default(false),
  isFinancial: boolean("is_financial").default(false),
  isHolding: boolean("is_holding").default(false),
  // Data source tracking
  source: varchar("source", { length: 20 }).default("marcap"),
  updatedAt: timestamp("updated_at").defaultNow(),
}, (table) => ({
  marketIdx: index("stocks_market_idx").on(table.market),
  activeIdx: index("stocks_active_idx").on(table.isActive),
}));

// ---------------------------------------------------------------------------
// Daily Prices & Market Data (from marcap / FinanceData)
// ---------------------------------------------------------------------------

export const dailyPrices = pgTable("daily_prices", {
  ticker: varchar("ticker", { length: 10 }).notNull(),
  date: date("date").notNull(),
  open: doublePrecision("open"),
  high: doublePrecision("high"),
  low: doublePrecision("low"),
  close: doublePrecision("close").notNull(),
  volume: bigint("volume", { mode: "number" }),
  tradingValue: bigint("trading_value", { mode: "number" }),
  marketCap: bigint("market_cap", { mode: "number" }),
  sharesOutstanding: bigint("shares_outstanding", { mode: "number" }),
  source: varchar("source", { length: 20 }).default("marcap"),
}, (table) => ({
  pk: primaryKey({ columns: [table.ticker, table.date] }),
  dateIdx: index("daily_prices_date_idx").on(table.date),
  tickerDateIdx: index("daily_prices_ticker_date_idx").on(table.ticker, table.date),
}));

// ---------------------------------------------------------------------------
// pykrx Fundamentals (Phase 2 — KRX-derived valuation proxies)
// ---------------------------------------------------------------------------
// These are convenience values from KRX/Naver via pykrx.
// They are NOT institutional-grade and should be replaced by DART data
// when available. Marked as source="pykrx".

export const pykrxFundamentals = pgTable("pykrx_fundamentals", {
  ticker: varchar("ticker", { length: 10 }).notNull(),
  date: date("date").notNull(),
  per: doublePrecision("per"),              // Price-Earnings Ratio
  pbr: doublePrecision("pbr"),              // Price-Book Ratio
  eps: doublePrecision("eps"),              // Earnings Per Share (KRW)
  bps: doublePrecision("bps"),              // Book Value Per Share (KRW)
  dps: doublePrecision("dps"),              // Dividend Per Share (KRW)
  dividendYield: doublePrecision("dividend_yield"), // %
}, (table) => ({
  pk: primaryKey({ columns: [table.ticker, table.date] }),
  dateIdx: index("pykrx_fund_date_idx").on(table.date),
}));

// ---------------------------------------------------------------------------
// Financial Statements (Phase 3 — DART, point-in-time)
// ---------------------------------------------------------------------------
// CRITICAL: The ranking engine must only use rows where
// dataAvailableDate <= ranking_date to avoid lookahead bias.

export const financialStatements = pgTable("financial_statements", {
  id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
  ticker: varchar("ticker", { length: 10 }).notNull(),
  // Point-in-time fields
  periodEnd: date("period_end").notNull(),           // fiscal period end (e.g. 2023-12-31)
  filingDate: date("filing_date").notNull(),          // when DART received the report
  dataAvailableDate: date("data_available_date").notNull(), // when we consider it "knowable"
  // Classification
  fiscalYear: integer("fiscal_year").notNull(),
  fiscalQuarter: integer("fiscal_quarter"),           // 1,2,3,4 or null for annual
  statementType: varchar("statement_type", { length: 20 }).notNull(), // annual | Q1 | Q2 | Q3
  consolidatedOrSeparate: varchar("consolidated_or_separate", { length: 15 }).default("consolidated"),
  source: varchar("source", { length: 20 }).default("dart"),
  // Income Statement (KRW)
  revenue: bigint("revenue", { mode: "number" }),
  costOfRevenue: bigint("cost_of_revenue", { mode: "number" }),
  grossProfit: bigint("gross_profit", { mode: "number" }),
  operatingIncome: bigint("operating_income", { mode: "number" }),
  netIncome: bigint("net_income", { mode: "number" }),
  eps: doublePrecision("eps"),
  // Balance Sheet (KRW)
  totalAssets: bigint("total_assets", { mode: "number" }),
  totalLiabilities: bigint("total_liabilities", { mode: "number" }),
  totalEquity: bigint("total_equity", { mode: "number" }),
  bookValuePerShare: doublePrecision("book_value_per_share"),
  currentAssets: bigint("current_assets", { mode: "number" }),
  currentLiabilities: bigint("current_liabilities", { mode: "number" }),
  cash: bigint("cash", { mode: "number" }),
  totalDebt: bigint("total_debt", { mode: "number" }),
  // Cash Flow (KRW)
  operatingCashFlow: bigint("operating_cash_flow", { mode: "number" }),
  capitalExpenditure: bigint("capital_expenditure", { mode: "number" }),
  freeCashFlow: bigint("free_cash_flow", { mode: "number" }),
  dividendsPaid: bigint("dividends_paid", { mode: "number" }),
  // Derived (KRW)
  ebitda: bigint("ebitda", { mode: "number" }),
  interestExpense: bigint("interest_expense", { mode: "number" }),
  depreciation: bigint("depreciation", { mode: "number" }),
  sharesOutstanding: bigint("shares_outstanding", { mode: "number" }),
  // Correction tracking: source filing receipt + date a 정정 (correction) was applied
  receiptNo: varchar("receipt_no", { length: 30 }),
  correctedAt: date("corrected_at"),
}, (table) => ({
  tickerPeriodIdx: uniqueIndex("fs_ticker_period_type_idx")
    .on(table.ticker, table.periodEnd, table.statementType, table.consolidatedOrSeparate),
  filingDateIdx: index("fs_filing_date_idx").on(table.filingDate),
  dataAvailIdx: index("fs_data_avail_idx").on(table.dataAvailableDate),
  tickerFiscalIdx: index("fs_ticker_fiscal_idx").on(table.ticker, table.fiscalYear),
}));

// ---------------------------------------------------------------------------
// Fundamental Snapshots (normalized DART financial data)
// ---------------------------------------------------------------------------
// Normalized view of DART financial data that the factor engine will query.
// Point-in-time safe for ranking operations.

export const fundamentalSnapshots = pgTable("fundamental_snapshots", {
  id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
  ticker: varchar("ticker", { length: 10 }).notNull(),
  periodEnd: date("period_end").notNull(),
  dataAvailableDate: date("data_available_date").notNull(),
  fiscalYear: integer("fiscal_year").notNull(),
  fiscalQuarter: integer("fiscal_quarter"),  // null = annual
  reportCode: varchar("report_code", { length: 10 }),  // 11011, 11012, etc.
  consolidatedOrSeparate: varchar("consolidated_or_separate", { length: 15 }).default("consolidated"),
  // Normalized financials (KRW)
  revenue: bigint("revenue", { mode: "number" }),
  grossProfit: bigint("gross_profit", { mode: "number" }),
  operatingIncome: bigint("operating_income", { mode: "number" }),
  netIncome: bigint("net_income", { mode: "number" }),
  eps: doublePrecision("eps"),
  totalAssets: bigint("total_assets", { mode: "number" }),
  totalEquity: bigint("total_equity", { mode: "number" }),
  totalLiabilities: bigint("total_liabilities", { mode: "number" }),
  totalDebt: bigint("total_debt", { mode: "number" }),
  cashAndEquivalents: bigint("cash_and_equivalents", { mode: "number" }),
  inventory: bigint("inventory", { mode: "number" }),
  operatingCashFlow: bigint("operating_cash_flow", { mode: "number" }),
  capex: bigint("capex", { mode: "number" }),
  freeCashFlow: bigint("free_cash_flow", { mode: "number" }),
  depreciationAmortization: bigint("depreciation_amortization", { mode: "number" }),
  interestExpense: bigint("interest_expense", { mode: "number" }),
  ebitda: bigint("ebitda", { mode: "number" }),
  sharesOutstanding: bigint("shares_outstanding", { mode: "number" }),
  dividendsPaid: bigint("dividends_paid", { mode: "number" }),
  source: varchar("source", { length: 20 }).default("dart"),
  updatedAt: timestamp("updated_at").defaultNow(),
}, (table) => ({
  tickerPeriodIdx: uniqueIndex("fsnap_ticker_period_idx")
    .on(table.ticker, table.periodEnd, table.fiscalQuarter, table.consolidatedOrSeparate),
  dataAvailIdx: index("fsnap_data_avail_idx").on(table.dataAvailableDate),
  tickerFiscalIdx: index("fsnap_ticker_fiscal_idx").on(table.ticker, table.fiscalYear),
}));

// ---------------------------------------------------------------------------
// Short Selling Data (from pykrx / KRX)
// ---------------------------------------------------------------------------

export const shortSelling = pgTable("short_selling", {
  ticker: varchar("ticker", { length: 10 }).notNull(),
  date: date("date").notNull(),
  shortVolume: bigint("short_volume", { mode: "number" }),
  shortValue: bigint("short_value", { mode: "number" }),
  shortBalance: bigint("short_balance", { mode: "number" }),
  shortBalanceValue: bigint("short_balance_value", { mode: "number" }),
  shortRatio: doublePrecision("short_ratio"),
  source: varchar("source", { length: 20 }).default("pykrx"),
}, (table) => ({
  pk: primaryKey({ columns: [table.ticker, table.date] }),
}));

// ---------------------------------------------------------------------------
// DART Filings (metadata for linking to DART viewer)
// ---------------------------------------------------------------------------

export const dartFilings = pgTable("dart_filings", {
  id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
  ticker: varchar("ticker", { length: 10 }),
  corpCode: varchar("corp_code", { length: 20 }),
  receiptNo: varchar("receipt_no", { length: 30 }),
  reportName: text("report_name"),
  filingDate: date("filing_date"),
  url: text("url"),
});

// ---------------------------------------------------------------------------
// Factor Coverage (metadata about data availability per factor)
// ---------------------------------------------------------------------------

export const factorCoverage = pgTable("factor_coverage", {
  factorId: varchar("factor_id", { length: 50 }).primaryKey(),
  name: text("name").notNull(),
  category: varchar("category", { length: 30 }).notNull(),
  formula: text("formula"),
  preferredSource: varchar("preferred_source", { length: 20 }),   // "dart" | "pykrx" | "marcap"
  fallbackSource: varchar("fallback_source", { length: 20 }),
  // Coverage status
  isAvailable: boolean("is_available").default(false),
  coverageRatio: doublePrecision("coverage_ratio"),               // 0.0 to 1.0
  usesMockData: boolean("uses_mock_data").default(true),
  pointInTimeSafe: boolean("point_in_time_safe").default(false),
  // Status label: "real" | "proxy" | "mock" | "unavailable"
  dataStatus: varchar("data_status", { length: 15 }).default("mock"),
  // Ranking parameters
  rankDirection: varchar("rank_direction", { length: 20 }),       // "higher_is_better" | "lower_is_better"
  scope: varchar("scope", { length: 20 }),                        // "universe" | "sector" | "industry"
  subcategory: varchar("subcategory", { length: 50 }),
  implementationStatus: varchar("implementation_status", { length: 20 }).default("unavailable"), // "real" | "proxy" | "unavailable" | "mock"
  missingValuePolicy: varchar("missing_value_policy", { length: 30 }).default("exclude_from_factor"),
  lookbackDays: integer("lookback_days"),
  dataSourceDetail: varchar("data_source_detail", { length: 30 }), // "price" | "marcap" | "dart" | "pykrx" | "estimates" | "derived"
  lastUpdated: timestamp("last_updated").defaultNow(),
});

// ---------------------------------------------------------------------------
// Ranking Systems (user-created, stored as JSON tree)
// ---------------------------------------------------------------------------

export const rankingSystems = pgTable("ranking_systems", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  description: text("description"),
  tree: jsonb("tree").notNull(),
  options: jsonb("options").notNull(),
  universeConfig: jsonb("universe_config"),
  userId: text("user_id"),
  createdAt: timestamp("created_at").defaultNow(),
  updatedAt: timestamp("updated_at").defaultNow(),
});

// ---------------------------------------------------------------------------
// Ranking Snapshots (computed results, reproducible)
// ---------------------------------------------------------------------------

export const rankingSnapshots = pgTable("ranking_snapshots", {
  id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
  rankingSystemId: text("ranking_system_id").references(() => rankingSystems.id),
  date: date("date").notNull(),
  results: jsonb("results").notNull(),
  universeSize: integer("universe_size"),
  universeName: text("universe_name"),
  computedAt: timestamp("computed_at").defaultNow(),
}, (table) => ({
  systemDateIdx: index("rs_system_date_idx").on(table.rankingSystemId, table.date),
}));

// ---------------------------------------------------------------------------
// Factor Snapshots (precomputed factor values per stock per date)
// ---------------------------------------------------------------------------

// Factor snapshots are universe-scoped: percentile ranks depend on the
// peer set, so the same (ticker, factor_id, date) can have different
// percentile_ranks under "test_50" and "test_200_large". The
// universe_name column was added by migration 004; the old PK on
// (ticker, factor_id, date) must be dropped manually in Supabase SQL
// Editor (see scripts/sql/004_universe_aware_factor_snapshots.sql).
export const factorSnapshots = pgTable("factor_snapshots", {
  universeName: text("universe_name"),
  ticker: varchar("ticker", { length: 10 }).notNull(),
  factorId: varchar("factor_id", { length: 50 }).notNull(),
  date: date("date").notNull(),
  rawValue: doublePrecision("raw_value"),
  percentileRank: doublePrecision("percentile_rank"),
  source: varchar("source", { length: 20 }),  // "marcap" | "pykrx" | "dart" | "mock"
  scope: varchar("scope", { length: 20 }),    // what scope was used for ranking
  scopeFallback: boolean("scope_fallback").default(false), // true if fell back to universe scope
  missingReason: varchar("missing_reason", { length: 30 }), // null if present, "no_data" | "insufficient_history" | "unavailable"
}, (table) => ({
  pk: primaryKey({ columns: [table.universeName, table.ticker, table.factorId, table.date] }),
  dateIdx: index("factor_snap_date_idx").on(table.date),
  universeDateIdx: index("factor_snapshots_universe_date_idx").on(table.universeName, table.date),
}));

// ---------------------------------------------------------------------------
// Ingestion Log (track script runs)
// ---------------------------------------------------------------------------

export const ingestionLog = pgTable("ingestion_log", {
  id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
  scriptName: varchar("script_name", { length: 100 }).notNull(),
  startedAt: timestamp("started_at").defaultNow(),
  finishedAt: timestamp("finished_at"),
  status: varchar("status", { length: 20 }).default("running"),   // running | success | error
  rowsProcessed: integer("rows_processed").default(0),
  rowsInserted: integer("rows_inserted").default(0),
  rowsUpdated: integer("rows_updated").default(0),
  rowsSkipped: integer("rows_skipped").default(0),
  errorMessage: text("error_message"),
  parameters: jsonb("parameters"),  // store CLI args for reproducibility
});

// ---------------------------------------------------------------------------
// Ingestion Errors (detailed error tracking)
// ---------------------------------------------------------------------------

export const ingestionErrors = pgTable("ingestion_errors", {
  id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
  scriptName: varchar("script_name", { length: 100 }).notNull(),
  ticker: varchar("ticker", { length: 10 }),
  errorType: varchar("error_type", { length: 50 }),  // "timeout" | "api_error" | "parse_error" | etc.
  errorMessage: text("error_message"),
  parameters: jsonb("parameters"),
  createdAt: timestamp("created_at").defaultNow(),
});

// ---------------------------------------------------------------------------
// Universe Memberships (named universes — written by ingest_universe.py)
// ---------------------------------------------------------------------------
// Tracks which tickers belong to a named universe (e.g. "krx_all_current",
// "test_50"). Used by the Python pipeline to scope ranking runs, and by the
// webapp to know which universe a snapshot was computed against.

export const universeMemberships = pgTable("universe_memberships", {
  universeName: text("universe_name").notNull(),
  ticker: varchar("ticker", { length: 10 }).notNull(),
}, (table) => ({
  pk: primaryKey({ columns: [table.universeName, table.ticker] }),
  universeIdx: index("universe_memberships_universe_idx").on(table.universeName),
}));

// ---------------------------------------------------------------------------
// Backtest Forward Returns (pre-computed by backtest_forward_returns.py)
// ---------------------------------------------------------------------------
// One row per (ticker, snapshot_date, horizon_days) with the forward total
// return. Joined with factor_snapshots in the /backtest UI so we can
// recompute composites with user-tweaked weights and bucket into deciles
// without shipping all the price data to the client.

export const backtestForwardReturns = pgTable("backtest_forward_returns", {
  ticker: varchar("ticker", { length: 10 }).notNull(),
  snapshotDate: date("snapshot_date").notNull(),
  horizonDays: integer("horizon_days").notNull(),  // 21 (1m), 63 (3m), 126 (6m), 252 (12m)
  forwardReturn: doublePrecision("forward_return"),  // (price_end / price_start) - 1
  startClose: doublePrecision("start_close"),
  endClose: doublePrecision("end_close"),
  endDate: date("end_date"),
  computedAt: timestamp("computed_at").defaultNow(),
}, (table) => ({
  pk: primaryKey({ columns: [table.ticker, table.snapshotDate, table.horizonDays] }),
  dateHorizonIdx: index("bfr_date_horizon_idx").on(table.snapshotDate, table.horizonDays),
}));

// ---------------------------------------------------------------------------
// FX rates (USD/KRW etc.) — for showing approximate USD figures in the UI
// ---------------------------------------------------------------------------

export const fxRates = pgTable("fx_rates", {
  pair: varchar("pair", { length: 12 }).notNull(),   // e.g. "USD/KRW"
  date: date("date").notNull(),
  rate: doublePrecision("rate").notNull(),
  source: varchar("source", { length: 30 }),
  updatedAt: timestamp("updated_at").defaultNow(),
}, (table) => ({
  pk: primaryKey({ columns: [table.pair, table.date] }),
}));
