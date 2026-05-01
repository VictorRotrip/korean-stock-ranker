import { NextRequest, NextResponse } from "next/server";
import { getDb, hasDatabase, schema } from "@/db";
import { eq, desc, and, lte, inArray, sql } from "drizzle-orm";

export const runtime = "nodejs";

/**
 * GET /api/ranking-snapshots/[systemId]
 *
 * Returns the latest ranking snapshot for the given system ID.
 * Enriches the raw DB results with stock metadata (name, market, sector).
 *
 * Response shape matches the RankingResult TypeScript type so the
 * results page can consume it directly.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ systemId: string }> },
) {
  const { systemId } = await params;

  if (!hasDatabase()) {
    return NextResponse.json(
      { error: "Database not configured", dataSource: "mock" },
      { status: 503 },
    );
  }

  const db = getDb()!;

  console.log(`[ranking-snapshots] Fetching latest snapshot for system="${systemId}"`);

  // 1. Get the latest snapshot for this system
  const snapshots = await db
    .select()
    .from(schema.rankingSnapshots)
    .where(eq(schema.rankingSnapshots.rankingSystemId, systemId))
    .orderBy(desc(schema.rankingSnapshots.id))
    .limit(1);

  if (snapshots.length === 0) {
    console.log(`[ranking-snapshots] No snapshot found for system="${systemId}"`);
    return NextResponse.json(
      { error: `No ranking snapshot found for system "${systemId}". Run: python run_ranking_snapshot.py --as-of-date 2024-12-30` },
      { status: 404 },
    );
  }

  const snapshot = snapshots[0];
  console.log(`[ranking-snapshots] Found snapshot id=${snapshot.id}, date=${snapshot.date}, universe_size=${snapshot.universeSize}`);

  // 2. Parse the results JSONB
  const rawResults = snapshot.results as Array<{
    ticker: string;
    composite_score: number;
    category_scores: Record<string, number>;
    factor_count: number;
    rank: number;
  }>;

  console.log(`[ranking-snapshots] ${rawResults.length} rows in snapshot`);

  // 3. Fetch stock metadata for all tickers in the snapshot
  const tickers = rawResults.map(r => r.ticker);
  const stockRows = tickers.length > 0
    ? await db
        .select()
        .from(schema.stocks)
        .where(inArray(schema.stocks.ticker, tickers))
    : [];

  const stockMap = new Map<string, typeof stockRows[0]>();
  for (const s of stockRows) {
    stockMap.set(s.ticker, s);
  }

  console.log(`[ranking-snapshots] Stock metadata: ${stockMap.size} stocks found, sectors: ${stockRows.filter(s => s.sector).length}`);

  // 4. Fetch latest prices for market cap (latest price on or before snapshot date)
  const priceMap = new Map<string, typeof schema.dailyPrices.$inferSelect>();

  if (tickers.length > 0) {
    // For each ticker, get the most recent price on or before the snapshot date
    const latestPriceSubquery = db
      .select({
        ticker: schema.dailyPrices.ticker,
        maxDate: sql<string>`max(${schema.dailyPrices.date})`.as("max_date"),
      })
      .from(schema.dailyPrices)
      .where(
        and(
          inArray(schema.dailyPrices.ticker, tickers),
          lte(schema.dailyPrices.date, snapshot.date),
        ),
      )
      .groupBy(schema.dailyPrices.ticker)
      .as("latest_prices");

    const priceRows = await db
      .select()
      .from(schema.dailyPrices)
      .innerJoin(
        latestPriceSubquery,
        and(
          eq(schema.dailyPrices.ticker, latestPriceSubquery.ticker),
          eq(schema.dailyPrices.date, latestPriceSubquery.maxDate),
        ),
      );

    for (const row of priceRows) {
      priceMap.set(row.daily_prices.ticker, row.daily_prices);
    }
  }

  console.log(`[ranking-snapshots] Prices found: ${priceMap.size} tickers`);

  // 5. Fetch factor_snapshots for this date to populate factorScores
  const factorRows = await db
    .select()
    .from(schema.factorSnapshots)
    .where(eq(schema.factorSnapshots.date, snapshot.date));

  // ticker -> factorId -> { rawValue, percentileRank }
  const factorMap = new Map<string, Record<string, { rawValue: number | null; percentileRank: number }>>();
  for (const f of factorRows) {
    if (!factorMap.has(f.ticker)) {
      factorMap.set(f.ticker, {});
    }
    factorMap.get(f.ticker)![f.factorId] = {
      rawValue: f.rawValue,
      percentileRank: f.percentileRank ?? 50,
    };
  }

  // 6. Map to StockRanking shape
  const rankings = rawResults.map(r => {
    const stock = stockMap.get(r.ticker);
    const price = priceMap.get(r.ticker);
    const factors = factorMap.get(r.ticker) ?? {};

    return {
      rank: r.rank,
      ticker: r.ticker,
      name: stock?.name ?? r.ticker,
      market: (stock?.market ?? "KOSPI") as "KOSPI" | "KOSDAQ",
      sector: stock?.sector ?? undefined,
      industry: stock?.industry ?? undefined,
      marketCap: price?.marketCap ?? 0,
      compositeScore: r.composite_score,
      categoryScores: r.category_scores,
      factorScores: factors,
    };
  });

  // 7. Return as RankingResult
  const result = {
    rankingSystemId: systemId,
    date: snapshot.date,
    rankings,
    universeSize: snapshot.universeSize ?? rawResults.length,
    computedAt: snapshot.computedAt?.toISOString() ?? new Date().toISOString(),
    // Extra metadata for the UI
    snapshotId: snapshot.id,
    dataSource: "db" as const,
  };

  return NextResponse.json(result);
}
