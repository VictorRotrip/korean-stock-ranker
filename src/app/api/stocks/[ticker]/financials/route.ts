import { NextRequest, NextResponse } from "next/server";
import { getDb, hasDatabase, schema } from "@/db";
import { and, eq, desc } from "drizzle-orm";

export const runtime = "nodejs";

/**
 * GET /api/stocks/[ticker]/financials
 *
 * Returns the most recent stored financial-statement periods for a stock, so
 * the ranking dropdown can show the actual source numbers behind the factors
 * (cross-checkable against the linked DART filing). Consolidated statements are
 * preferred; falls back to whatever is stored.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ ticker: string }> },
) {
  const { ticker } = await params;

  if (!hasDatabase()) {
    return NextResponse.json({ periods: [] });
  }

  const db = getDb()!;
  const fs = schema.financialStatements;

  const rows = await db
    .select({
      periodEnd: fs.periodEnd,
      statementType: fs.statementType,
      consolidated: fs.consolidatedOrSeparate,
      revenue: fs.revenue,
      operatingIncome: fs.operatingIncome,
      netIncome: fs.netIncome,
      operatingCashFlow: fs.operatingCashFlow,
      freeCashFlow: fs.freeCashFlow,
      totalDebt: fs.totalDebt,
      totalEquity: fs.totalEquity,
      eps: fs.eps,
      sharesOutstanding: fs.sharesOutstanding,
    })
    .from(fs)
    .where(and(
      eq(fs.ticker, ticker),
      eq(fs.consolidatedOrSeparate, "consolidated"),
    ))
    .orderBy(desc(fs.periodEnd))
    .limit(8);

  return NextResponse.json({ periods: rows });
}
