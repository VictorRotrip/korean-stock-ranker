import { NextResponse } from "next/server";
import { getStocksSync as getStocks, getLatestPricesSync as getLatestPrices } from "@/lib/data-service";

/**
 * GET /api/stocks
 * Returns the full stock universe with latest prices.
 */
export async function GET() {
  const stocks = getStocks();
  const prices = getLatestPrices();

  const enriched = stocks.map(s => ({
    ...s,
    price: prices.get(s.ticker)?.close ?? null,
    marketCap: prices.get(s.ticker)?.marketCap ?? null,
    volume: prices.get(s.ticker)?.volume ?? null,
  }));

  return NextResponse.json(enriched);
}
