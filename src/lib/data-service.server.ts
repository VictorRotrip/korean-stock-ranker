// =============================================================================
// Data Service Layer — SERVER-ONLY
// =============================================================================
// Async functions that query Supabase Postgres via Drizzle ORM.
// This module is ONLY importable from server components, API routes, and
// server actions. The `server-only` package enforces this at build time.
//
// For client components, use the sync mock-data functions from
// `@/lib/data-service` (which re-exports mock-data.ts).
// =============================================================================

import "server-only";

import { unstable_cache } from "next/cache";
import type {
  Stock,
  DailyPrice,
  FinancialStatement,
  ShortSellingData,
} from "@/types";
import type {
  BacktestPayload,
  BacktestSnapshot,
  BacktestTicker,
  BacktestForwardReturn,
} from "./backtest";
import { getDb, hasDatabase, schema } from "@/db";
import { eq, and, lte, desc, asc, inArray, sql } from "drizzle-orm";

// Re-export mock-data functions for the mock fallback path
import {
  getStocks as mockGetStocks,
  getLatestPrices as mockGetLatestPrices,
  getLatestFinancials as mockGetLatestFinancials,
  getPriorFinancials as mockGetPriorFinancials,
  getStockPriceHistory as mockGetStockPriceHistory,
  getShortSellingData as mockGetShortSellingData,
  getPrices as mockGetPrices,
} from "./mock-data";

// ---------------------------------------------------------------------------
// Detect data source
// ---------------------------------------------------------------------------

export type DataSource = "db" | "mock";

export function getDataSource(): DataSource {
  const explicit = process.env.NEXT_PUBLIC_DATA_SOURCE;
  if (explicit === "mock") return "mock";
  if (explicit === "db" && hasDatabase()) return "db";
  return hasDatabase() ? "db" : "mock";
}

// ---------------------------------------------------------------------------
// Stocks
// ---------------------------------------------------------------------------

export async function fetchStocks(): Promise<Stock[]> {
  if (getDataSource() === "mock") return mockGetStocks();

  const db = getDb()!;
  const rows = await db
    .select()
    .from(schema.stocks)
    .where(eq(schema.stocks.isActive, true));

  return rows.map(mapDbStock);
}

export async function fetchStockByTicker(ticker: string): Promise<Stock | null> {
  if (getDataSource() === "mock") {
    return mockGetStocks().find(s => s.ticker === ticker) ?? null;
  }

  const db = getDb()!;
  const rows = await db
    .select()
    .from(schema.stocks)
    .where(eq(schema.stocks.ticker, ticker))
    .limit(1);

  return rows.length > 0 ? mapDbStock(rows[0]) : null;
}

// ---------------------------------------------------------------------------
// Prices
// ---------------------------------------------------------------------------

export async function fetchLatestPrices(): Promise<Map<string, DailyPrice>> {
  if (getDataSource() === "mock") return mockGetLatestPrices();

  const db = getDb()!;

  const latestDates = db
    .select({
      ticker: schema.dailyPrices.ticker,
      maxDate: sql<string>`max(${schema.dailyPrices.date})`.as("max_date"),
    })
    .from(schema.dailyPrices)
    .groupBy(schema.dailyPrices.ticker)
    .as("latest_dates");

  const rows = await db
    .select()
    .from(schema.dailyPrices)
    .innerJoin(
      latestDates,
      and(
        eq(schema.dailyPrices.ticker, latestDates.ticker),
        eq(schema.dailyPrices.date, latestDates.maxDate),
      ),
    );

  const result = new Map<string, DailyPrice>();
  for (const row of rows) {
    result.set(row.daily_prices.ticker, mapDbPrice(row.daily_prices));
  }
  return result;
}

export async function fetchStockPriceHistory(ticker: string): Promise<DailyPrice[]> {
  if (getDataSource() === "mock") return mockGetStockPriceHistory(ticker);

  const db = getDb()!;
  const rows = await db
    .select()
    .from(schema.dailyPrices)
    .where(eq(schema.dailyPrices.ticker, ticker))
    .orderBy(asc(schema.dailyPrices.date));

  return rows.map(mapDbPrice);
}

export async function fetchPriceHistoryForTickers(
  tickers: string[],
  startDate?: string,
): Promise<DailyPrice[]> {
  if (getDataSource() === "mock") {
    const all = mockGetPrices();
    return all.filter(
      p => tickers.includes(p.ticker) && (!startDate || p.date >= startDate),
    );
  }

  const db = getDb()!;
  const conditions = [inArray(schema.dailyPrices.ticker, tickers)];
  if (startDate) {
    conditions.push(sql`${schema.dailyPrices.date} >= ${startDate}`);
  }

  const rows = await db
    .select()
    .from(schema.dailyPrices)
    .where(and(...conditions))
    .orderBy(asc(schema.dailyPrices.date));

  return rows.map(mapDbPrice);
}

// ---------------------------------------------------------------------------
// Financial Statements (point-in-time safe)
// ---------------------------------------------------------------------------

export async function fetchLatestFinancials(
  asOfDate: string,
): Promise<Map<string, FinancialStatement>> {
  if (getDataSource() === "mock") return mockGetLatestFinancials(asOfDate);

  const db = getDb()!;

  const rows = await db
    .select()
    .from(schema.financialStatements)
    .where(
      and(
        lte(schema.financialStatements.dataAvailableDate, asOfDate),
        eq(schema.financialStatements.statementType, "annual"),
        eq(schema.financialStatements.consolidatedOrSeparate, "consolidated"),
      ),
    )
    .orderBy(
      desc(schema.financialStatements.periodEnd),
    );

  const result = new Map<string, FinancialStatement>();
  for (const row of rows) {
    if (!result.has(row.ticker)) {
      result.set(row.ticker, mapDbFinancial(row));
    }
  }
  return result;
}

export async function fetchPriorFinancials(
  asOfDate: string,
): Promise<Map<string, FinancialStatement>> {
  if (getDataSource() === "mock") return mockGetPriorFinancials(asOfDate);

  const latest = await fetchLatestFinancials(asOfDate);
  const db = getDb()!;

  const result = new Map<string, FinancialStatement>();

  for (const [ticker, latestFs] of latest) {
    const rows = await db
      .select()
      .from(schema.financialStatements)
      .where(
        and(
          eq(schema.financialStatements.ticker, ticker),
          eq(schema.financialStatements.statementType, "annual"),
          eq(schema.financialStatements.consolidatedOrSeparate, "consolidated"),
          lte(schema.financialStatements.dataAvailableDate, asOfDate),
          sql`${schema.financialStatements.periodEnd} < ${latestFs.periodEnd}`,
        ),
      )
      .orderBy(desc(schema.financialStatements.periodEnd))
      .limit(1);

    if (rows.length > 0) {
      result.set(ticker, mapDbFinancial(rows[0]));
    }
  }

  return result;
}

// ---------------------------------------------------------------------------
// Short Selling
// ---------------------------------------------------------------------------

export async function fetchShortSellingData(): Promise<ShortSellingData[]> {
  if (getDataSource() === "mock") return mockGetShortSellingData();

  const db = getDb()!;
  const rows = await db
    .select()
    .from(schema.shortSelling)
    .orderBy(desc(schema.shortSelling.date));

  return rows.map(mapDbShortSelling);
}

export async function fetchLatestShortSelling(
  asOfDate: string,
): Promise<Map<string, ShortSellingData>> {
  if (getDataSource() === "mock") {
    const all = mockGetShortSellingData();
    const result = new Map<string, ShortSellingData>();
    for (const s of all) {
      if (s.date <= asOfDate) {
        const existing = result.get(s.ticker);
        if (!existing || s.date > existing.date) {
          result.set(s.ticker, s);
        }
      }
    }
    return result;
  }

  const db = getDb()!;
  const latestDates = db
    .select({
      ticker: schema.shortSelling.ticker,
      maxDate: sql<string>`max(${schema.shortSelling.date})`.as("max_date"),
    })
    .from(schema.shortSelling)
    .where(lte(schema.shortSelling.date, asOfDate))
    .groupBy(schema.shortSelling.ticker)
    .as("latest_short");

  const rows = await db
    .select()
    .from(schema.shortSelling)
    .innerJoin(
      latestDates,
      and(
        eq(schema.shortSelling.ticker, latestDates.ticker),
        eq(schema.shortSelling.date, latestDates.maxDate),
      ),
    );

  const result = new Map<string, ShortSellingData>();
  for (const row of rows) {
    result.set(row.short_selling.ticker, mapDbShortSelling(row.short_selling));
  }
  return result;
}

// ---------------------------------------------------------------------------
// Row mappers: DB schema → app types
// ---------------------------------------------------------------------------

function mapDbStock(row: typeof schema.stocks.$inferSelect): Stock {
  return {
    ticker: row.ticker,
    name: row.name,
    nameEn: row.nameEn ?? undefined,
    market: row.market as "KOSPI" | "KOSDAQ",
    sector: row.sector ?? undefined,
    industry: row.industry ?? undefined,
    listingDate: row.listingDate ?? undefined,
    isActive: row.isActive,
    isSpac: row.isSpac ?? false,
    isPreferred: row.isPreferred ?? false,
    isEtf: row.isEtf ?? false,
    isReit: row.isReit ?? false,
    isFinancial: row.isFinancial ?? false,
    isHolding: row.isHolding ?? false,
  };
}

function mapDbPrice(row: typeof schema.dailyPrices.$inferSelect): DailyPrice {
  return {
    ticker: row.ticker,
    date: row.date,
    open: row.open ?? 0,
    high: row.high ?? 0,
    low: row.low ?? 0,
    close: row.close,
    volume: row.volume ?? 0,
    tradingValue: row.tradingValue ?? 0,
    marketCap: row.marketCap ?? 0,
    sharesOutstanding: row.sharesOutstanding ?? 0,
  };
}

function mapDbFinancial(
  row: typeof schema.financialStatements.$inferSelect,
): FinancialStatement {
  return {
    ticker: row.ticker,
    periodEnd: row.periodEnd,
    periodType: row.statementType === "annual" ? "annual" : "quarterly",
    filingDate: row.filingDate,
    revenue: row.revenue,
    costOfRevenue: row.costOfRevenue,
    grossProfit: row.grossProfit,
    operatingIncome: row.operatingIncome,
    netIncome: row.netIncome,
    eps: row.eps,
    totalAssets: row.totalAssets,
    totalLiabilities: row.totalLiabilities,
    totalEquity: row.totalEquity,
    bookValuePerShare: row.bookValuePerShare,
    currentAssets: row.currentAssets,
    currentLiabilities: row.currentLiabilities,
    cash: row.cash,
    shortTermDebt: null,
    longTermDebt: null,
    totalDebt: row.totalDebt,
    operatingCashFlow: row.operatingCashFlow,
    capitalExpenditure: row.capitalExpenditure,
    freeCashFlow: row.freeCashFlow,
    dividendsPaid: row.dividendsPaid,
    ebitda: row.ebitda,
    interestExpense: row.interestExpense,
    depreciation: row.depreciation,
    sharesOutstanding: row.sharesOutstanding,
  };
}

function mapDbShortSelling(
  row: typeof schema.shortSelling.$inferSelect,
): ShortSellingData {
  return {
    ticker: row.ticker,
    date: row.date,
    shortVolume: row.shortVolume ?? 0,
    shortValue: row.shortValue ?? 0,
    shortBalance: row.shortBalance ?? 0,
    shortBalanceValue: row.shortBalanceValue ?? 0,
    shortRatio: row.shortRatio ?? 0,
  };
}

// ---------------------------------------------------------------------------
// Ranking Snapshots
// ---------------------------------------------------------------------------
// Shape stored by scripts/python/run_ranking_snapshot.py:
//   ranking_snapshots.results JSONB = {
//     _meta: { ...snapshot metadata... },
//     rankings: [
//       { ticker, rank, composite_score, category_scores, category_scores_simple,
//         active_categories, imputed_categories, active_category_count,
//         factor_count, active_weight_coverage, composite_weight_used,
//         passes_minimum, failure_reasons, coverage_status: "passed"|"insufficient"|"non_pit_market_cap"
//       }, ...
//     ]
//   }

export type RankingCoverageStatus = "passed" | "insufficient" | "non_pit_market_cap";

export interface RankingResultRow {
  ticker: string;
  rank: number;
  composite_score: number;
  category_scores: Record<string, {
    score: number | null;
    weight: number;
    coverage: number;
    status: string;
  }>;
  category_scores_simple: Record<string, number | null>;
  active_categories: string[];
  imputed_categories: string[];
  active_category_count: number;
  factor_count: number;
  active_weight_coverage: number;
  composite_weight_used: number;
  passes_minimum: boolean;
  failure_reasons: string[];
  coverage_status: RankingCoverageStatus;
}

export interface RankingSnapshot {
  id: number;
  rankingSystemId: string | null;
  date: string;
  universeName: string | null;
  universeSize: number | null;
  computedAt: string | null;
  meta: Record<string, unknown>;
  rankings: RankingResultRow[];
}

/**
 * Fetch the most recent ranking snapshot for a given system + universe.
 * If `systemId` is omitted, returns the latest snapshot across all systems.
 */
export async function fetchLatestRankingSnapshot(
  systemId?: string,
  universeName?: string,
): Promise<RankingSnapshot | null> {
  if (getDataSource() === "mock") return null;

  const db = getDb()!;
  const conditions = [];
  if (systemId) {
    conditions.push(eq(schema.rankingSnapshots.rankingSystemId, systemId));
  }
  if (universeName) {
    conditions.push(eq(schema.rankingSnapshots.universeName, universeName));
  }

  const rows = await db
    .select()
    .from(schema.rankingSnapshots)
    .where(conditions.length > 0 ? and(...conditions) : undefined)
    .orderBy(desc(schema.rankingSnapshots.date), desc(schema.rankingSnapshots.id))
    .limit(1);

  if (rows.length === 0) return null;
  const row = rows[0];
  const results = row.results as { _meta?: Record<string, unknown>; rankings?: RankingResultRow[] };

  return {
    id: row.id,
    rankingSystemId: row.rankingSystemId ?? null,
    date: row.date,
    universeName: row.universeName ?? null,
    universeSize: row.universeSize ?? null,
    computedAt: row.computedAt ? row.computedAt.toISOString() : null,
    meta: (results?._meta as Record<string, unknown>) ?? {},
    rankings: results?.rankings ?? [],
  };
}

/**
 * Fetch one stock's factor snapshot row for a given (date, universe).
 * Used by the stock detail page to show universe-relative percentile ranks.
 */
export async function fetchFactorSnapshotsForStock(
  ticker: string,
  date: string,
  universeName: string,
): Promise<Array<{
  factorId: string;
  rawValue: number | null;
  percentileRank: number | null;
  source: string | null;
  scope: string | null;
}>> {
  if (getDataSource() === "mock") return [];

  const db = getDb()!;
  const rows = await db
    .select({
      factorId: schema.factorSnapshots.factorId,
      rawValue: schema.factorSnapshots.rawValue,
      percentileRank: schema.factorSnapshots.percentileRank,
      source: schema.factorSnapshots.source,
      scope: schema.factorSnapshots.scope,
    })
    .from(schema.factorSnapshots)
    .where(and(
      eq(schema.factorSnapshots.ticker, ticker),
      eq(schema.factorSnapshots.date, date),
      eq(schema.factorSnapshots.universeName, universeName),
    ));

  return rows.map(r => ({
    factorId: r.factorId,
    rawValue: r.rawValue,
    percentileRank: r.percentileRank,
    source: r.source,
    scope: r.scope,
  }));
}

/**
 * Returns the most recent trading date in daily_prices. Used as a default
 * "as of" date by pages that need to anchor a PIT query to "now".
 */
export async function fetchLatestPriceDate(): Promise<string | null> {
  if (getDataSource() === "mock") return null;
  const db = getDb()!;
  const rows = await db
    .select({ maxDate: sql<string>`max(${schema.dailyPrices.date})`.as("max_date") })
    .from(schema.dailyPrices);
  return rows[0]?.maxDate ?? null;
}

// ---------------------------------------------------------------------------
// Backtest data (one shot — loads all snapshots + returns for the client to
// re-aggregate with sliders without round-trips)
// ---------------------------------------------------------------------------
// Types live in @/lib/backtest so client components can import them safely.

/**
 * Fetch every ranking_snapshot for (system, universe) and the matching
 * forward returns at the selected horizon. Used to power the /backtest
 * page client-side: the user moves weight sliders, we re-aggregate the
 * composite score in the browser, bucket, and chain bucket returns into a
 * cumulative line — no server round-trip per slider tick.
 */
async function fetchBacktestPayloadUncached(
  systemId: string,
  universeName: string,
  horizonDays: number = 30,
): Promise<BacktestPayload | null> {
  if (getDataSource() === "mock") return null;

  const db = getDb()!;

  // 1. All ranking snapshots for (system, universe), oldest → newest.
  const snapRows = await db
    .select({
      date: schema.rankingSnapshots.date,
      results: schema.rankingSnapshots.results,
    })
    .from(schema.rankingSnapshots)
    .where(and(
      eq(schema.rankingSnapshots.rankingSystemId, systemId),
      eq(schema.rankingSnapshots.universeName, universeName),
    ))
    .orderBy(asc(schema.rankingSnapshots.date));

  if (snapRows.length === 0) return null;

  const snapshots: BacktestSnapshot[] = [];
  const categorySet = new Set<string>();

  // Payload-size trimming: drop tickers with no usable category scores
  // (saves ~30% on the historical universe where many delisted/inactive
  // names had only NULLs at a given rebalance date). Round category
  // scores to 1 decimal — they're percentile ranks, not precise
  // measurements.
  const round1 = (v: number | null): number | null =>
    v == null ? null : Math.round(v * 10) / 10;

  for (const row of snapRows) {
    const r = row.results as {
      rankings?: Array<{
        ticker: string;
        category_scores_simple?: Record<string, number | null>;
        passes_minimum?: boolean;
      }>;
    };
    const rankings = r?.rankings ?? [];
    const rows: BacktestTicker[] = [];
    for (const rk of rankings) {
      const raw = rk.category_scores_simple ?? {};
      const cats: Record<string, number | null> = {};
      let anyNonNull = false;
      for (const k of Object.keys(raw)) {
        categorySet.add(k);
        const v = round1(raw[k]);
        cats[k] = v;
        if (v !== null) anyNonNull = true;
      }
      // Skip rows with no usable category data — they contribute nothing
      // to the composite anyway and bloat the payload.
      if (!anyNonNull) continue;
      rows.push({
        ticker: rk.ticker,
        cats,
        passed: rk.passes_minimum ?? false,
      });
    }
    snapshots.push({ date: row.date, rows });
  }

  // 2. Forward returns for those snapshot dates at the chosen horizon.
  const dates = snapshots.map(s => s.date);
  const retRows = await db
    .select({
      ticker: schema.backtestForwardReturns.ticker,
      snapshotDate: schema.backtestForwardReturns.snapshotDate,
      forwardReturn: schema.backtestForwardReturns.forwardReturn,
    })
    .from(schema.backtestForwardReturns)
    .where(and(
      inArray(schema.backtestForwardReturns.snapshotDate, dates),
      eq(schema.backtestForwardReturns.horizonDays, horizonDays),
    ));

  const returnsByDate: Record<string, BacktestForwardReturn[]> = {};
  for (const r of retRows) {
    if (r.forwardReturn === null) continue;
    const key = r.snapshotDate;
    if (!returnsByDate[key]) returnsByDate[key] = [];
    returnsByDate[key].push({
      ticker: r.ticker,
      date: r.snapshotDate,
      ret: Math.round(r.forwardReturn * 10000) / 10000,  // 4 decimal places
      endDate: null,                                      // omitted (unused in client)
    });
  }

  // Stable category order: known canonical ordering first, then any leftover.
  const CANONICAL = ["Value", "Quality", "Growth", "Momentum", "Low Volatility", "Sentiment"];
  const ordered: string[] = [];
  for (const c of CANONICAL) if (categorySet.has(c)) ordered.push(c);
  for (const c of categorySet) if (!ordered.includes(c)) ordered.push(c);

  return {
    systemId,
    universeName,
    horizonDays,
    categories: ordered,
    snapshots,
    returns: returnsByDate,
  };
}

/**
 * Server-cached wrapper around the heavy Supabase fetch. The page is rendered
 * dynamically (no ISR) so every request hits the server, but this cache means
 * only the first request per hour actually queries Supabase — subsequent
 * requests reuse the in-memory result on the same Vercel function instance.
 * The cache tag `backtest-payload` lets us bust it on demand if needed.
 */
export const fetchBacktestPayload = unstable_cache(
  fetchBacktestPayloadUncached,
  ["backtest-payload-v1"],
  {
    revalidate: 3600,
    tags: ["backtest-payload"],
  },
);
