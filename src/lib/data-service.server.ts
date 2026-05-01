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

import type {
  Stock,
  DailyPrice,
  FinancialStatement,
  ShortSellingData,
} from "@/types";
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
