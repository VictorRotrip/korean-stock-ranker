// =============================================================================
// Data Service Layer — CLIENT-SAFE
// =============================================================================
// Provides synchronous mock-data accessors that are safe to import from
// client components ("use client") and the ranking engine.
//
// This module does NOT import postgres, drizzle, or any Node-only modules.
//
// For async DB-backed queries, use `@/lib/data-service.server` (server-only).
// =============================================================================

import type {
  Stock,
  DailyPrice,
  FinancialStatement,
  ShortSellingData,
} from "@/types";

import {
  getStocks as mockGetStocks,
  getLatestPrices as mockGetLatestPrices,
  getLatestFinancials as mockGetLatestFinancials,
  getPriorFinancials as mockGetPriorFinancials,
  getStockPriceHistory as mockGetStockPriceHistory,
  getShortSellingData as mockGetShortSellingData,
} from "./mock-data";

// ---------------------------------------------------------------------------
// Synchronous accessors (mock data — safe for client components)
// ---------------------------------------------------------------------------

export function getStocksSync(): Stock[] {
  return mockGetStocks();
}

export function getLatestPricesSync(): Map<string, DailyPrice> {
  return mockGetLatestPrices();
}

export function getLatestFinancialsSync(asOfDate: string): Map<string, FinancialStatement> {
  return mockGetLatestFinancials(asOfDate);
}

export function getPriorFinancialsSync(asOfDate: string): Map<string, FinancialStatement> {
  return mockGetPriorFinancials(asOfDate);
}

export function getStockPriceHistorySync(ticker: string): DailyPrice[] {
  return mockGetStockPriceHistory(ticker);
}

export function getShortSellingDataSync(): ShortSellingData[] {
  return mockGetShortSellingData();
}
