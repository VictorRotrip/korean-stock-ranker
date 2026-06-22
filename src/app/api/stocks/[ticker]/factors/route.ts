import { NextRequest, NextResponse } from "next/server";
import { getDb, hasDatabase, schema } from "@/db";
import { and, eq } from "drizzle-orm";

export const runtime = "nodejs";

/**
 * GET /api/stocks/[ticker]/factors?date=YYYY-MM-DD&universe=krx_all_current
 *
 * Returns the per-factor raw values and universe-relative percentile ranks for
 * one stock on a given snapshot date + universe. Used by the Today's Ranking
 * dropdown to show the same "Factor Details" breakdown as the run-ranking
 * results page, loaded on demand so the ranking page payload stays small.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ ticker: string }> },
) {
  const { ticker } = await params;
  const { searchParams } = new URL(request.url);
  const date = searchParams.get("date");
  const universe = searchParams.get("universe");

  if (!hasDatabase()) {
    return NextResponse.json({ factors: [] });
  }
  if (!date || !universe) {
    return NextResponse.json({ error: "date and universe are required" }, { status: 400 });
  }

  const db = getDb()!;
  const rows = await db
    .select({
      factorId: schema.factorSnapshots.factorId,
      rawValue: schema.factorSnapshots.rawValue,
      percentileRank: schema.factorSnapshots.percentileRank,
    })
    .from(schema.factorSnapshots)
    .where(and(
      eq(schema.factorSnapshots.ticker, ticker),
      eq(schema.factorSnapshots.date, date),
      eq(schema.factorSnapshots.universeName, universe),
    ));

  return NextResponse.json({
    factors: rows.map(r => ({
      factorId: r.factorId,
      rawValue: r.rawValue,
      percentileRank: r.percentileRank ?? 50,
    })),
  });
}
