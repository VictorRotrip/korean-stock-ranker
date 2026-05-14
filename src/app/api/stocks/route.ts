import { NextResponse } from "next/server";
import { fetchStocks, fetchLatestPrices } from "@/lib/data-service.server";

export const runtime = "nodejs";

/**
 * GET /api/stocks
 * Returns the full stock universe with latest prices.
 */
export async function GET() {
  const [stocks, prices] = await Promise.all([
    fetchStocks(),
    fetchLatestPrices(),
  ]);

  const enriched = stocks.map(s => ({
    ...s,
    price: prices.get(s.ticker)?.close ?? null,
    marketCap: prices.get(s.ticker)?.marketCap ?? null,
    volume: prices.get(s.ticker)?.volume ?? null,
  }));

  return NextResponse.json(enriched);
}
