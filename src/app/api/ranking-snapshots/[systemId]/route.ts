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
 * Handles two snapshot result formats:
 *
 *   1. Legacy: results is a plain array of stock entries.
 *   2. New (post coverage-aware ranking): results is an envelope with
 *        { _meta: {...}, rankings: [...] }
 *      where _meta carries scoring_method, missing_category_policy,
 *      thresholds, globally_unavailable_categories, etc.
 *
 * Response shape matches the RankingResult TypeScript type so the
 * results page can consume it directly. The new fields
 * (activeWeightCoverage, passesMinimum, etc.) are added per stock
 * when present.
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

  // 1. Get the latest snapshot for this system. We constrain to the
  //    `krx_all_current` universe so /ranking-systems Run shows the same
  //    "today's investable" universe as the /ranking dashboard does.
  //    Without this filter, a recent run on `krx_all_historical` (which
  //    the /backtest pipeline produces in batches of 137) would be
  //    returned here, mixing universes and confusing the user.
  const DEFAULT_UNIVERSE = "krx_all_current";
  const snapshots = await db
    .select()
    .from(schema.rankingSnapshots)
    .where(
      and(
        eq(schema.rankingSnapshots.rankingSystemId, systemId),
        eq(schema.rankingSnapshots.universeName, DEFAULT_UNIVERSE),
      ),
    )
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

  // 2. Detect envelope vs legacy format
  type CategoryScoreDetail = {
    score: number | null;
    weight: number;
    coverage: string;
    status: "available" | "missing_imputed" | "missing" | "missing_renormalized" | "globally_unavailable";
  };

  type SnapshotEntry = {
    ticker: string;
    rank: number | null;
    composite_score: number | null;
    category_scores: Record<string, number> | Record<string, CategoryScoreDetail>;
    category_scores_simple?: Record<string, number | null>;
    factor_count?: number;
    active_categories?: string[];
    imputed_categories?: string[];
    active_category_count?: number;
    active_weight_coverage?: number;
    composite_weight_used?: number;
    passes_minimum?: boolean;
    failure_reasons?: string[];
    coverage_status?: "passed" | "insufficient";
  };

  type SnapshotMeta = {
    scoring_method?: string;
    missing_category_policy?: string;
    thresholds?: {
      min_active_weight_coverage?: number;
      min_category_count?: number;
      min_factor_count?: number;
    };
    globally_unavailable_categories?: string[];
    globally_active_categories?: string[];
    category_weights?: Record<string, number>;
    passed_count?: number;
    insufficient_count?: number;
    include_insufficient_coverage?: boolean;
    as_of_date?: string;
    universe_name?: string | null;
    system_id?: string;
  };

  const rawResultsEnvelope = snapshot.results as
    | Array<SnapshotEntry>
    | { _meta?: SnapshotMeta; rankings?: Array<SnapshotEntry> };

  let rawEntries: Array<SnapshotEntry>;
  let meta: SnapshotMeta | null = null;

  if (Array.isArray(rawResultsEnvelope)) {
    rawEntries = rawResultsEnvelope;
  } else if (rawResultsEnvelope && typeof rawResultsEnvelope === "object" && "rankings" in rawResultsEnvelope) {
    rawEntries = rawResultsEnvelope.rankings ?? [];
    meta = rawResultsEnvelope._meta ?? null;
  } else {
    rawEntries = [];
  }

  console.log(`[ranking-snapshots] ${rawEntries.length} rows in snapshot (envelope=${meta !== null})`);

  // 3. Fetch stock metadata for all tickers in the snapshot
  const tickers = rawEntries.map(r => r.ticker);
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

  // 5. Fetch factor_snapshots for this date AND universe to populate factorScores.
  // Percentile ranks depend on the universe used at calculation time, so reading
  // unscoped would mix scores from multiple universes (e.g. test_50 + test_200_large).
  const snapshotUniverse = snapshot.universeName ?? meta?.universe_name ?? null;
  const factorRows = snapshotUniverse
    ? await db
        .select()
        .from(schema.factorSnapshots)
        .where(
          and(
            eq(schema.factorSnapshots.date, snapshot.date),
            eq(schema.factorSnapshots.universeName, snapshotUniverse),
          ),
        )
    : await db
        .select()
        .from(schema.factorSnapshots)
        .where(eq(schema.factorSnapshots.date, snapshot.date));

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

  // 6. Helper: extract a simple {name: score} map from either envelope shape.
  // Envelope shape stores per-category {score, weight, coverage, status}.
  function flattenCategoryScores(entry: SnapshotEntry): Record<string, number> {
    if (entry.category_scores_simple) {
      const out: Record<string, number> = {};
      for (const [k, v] of Object.entries(entry.category_scores_simple)) {
        if (v !== null && typeof v === "number") out[k] = v;
      }
      return out;
    }
    const cs = entry.category_scores;
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(cs)) {
      if (typeof v === "number") {
        out[k] = v;
      } else if (v && typeof v === "object" && typeof (v as CategoryScoreDetail).score === "number") {
        out[k] = (v as CategoryScoreDetail).score as number;
      }
    }
    return out;
  }

  function extractCategoryDetails(entry: SnapshotEntry): Record<string, CategoryScoreDetail> | undefined {
    const cs = entry.category_scores;
    const out: Record<string, CategoryScoreDetail> = {};
    let isDetailed = false;
    for (const [k, v] of Object.entries(cs)) {
      if (v && typeof v === "object" && "status" in (v as object)) {
        isDetailed = true;
        out[k] = v as CategoryScoreDetail;
      }
    }
    return isDetailed ? out : undefined;
  }

  // 7. Map to StockRanking shape (with extra coverage fields when available)
  const rankings = rawEntries.map(r => {
    const stock = stockMap.get(r.ticker);
    const price = priceMap.get(r.ticker);
    const factors = factorMap.get(r.ticker) ?? {};
    const categoryScores = flattenCategoryScores(r);
    const categoryDetails = extractCategoryDetails(r);

    return {
      rank: r.rank,
      ticker: r.ticker,
      name: stock?.name ?? r.ticker,
      nameEn: stock?.nameEn ?? undefined,
      market: (stock?.market ?? "KOSPI") as "KOSPI" | "KOSDAQ",
      sector: stock?.sector ?? undefined,
      industry: stock?.industry ?? undefined,
      marketCap: price?.marketCap ?? 0,
      compositeScore: r.composite_score ?? 0,
      categoryScores,
      categoryDetails,
      factorScores: factors,
      // Coverage metadata (new fields, optional)
      coverageStatus: r.coverage_status,
      passesMinimum: r.passes_minimum,
      activeWeightCoverage: r.active_weight_coverage,
      compositeWeightUsed: r.composite_weight_used,
      activeCategoryCount: r.active_category_count,
      activeCategories: r.active_categories,
      imputedCategories: r.imputed_categories,
      factorCount: r.factor_count,
      failureReasons: r.failure_reasons,
    };
  });

  // 8. Return as RankingResult with metadata for the UI
  const result = {
    rankingSystemId: systemId,
    date: snapshot.date,
    rankings,
    universeSize: snapshot.universeSize ?? rawEntries.length,
    universeName: snapshot.universeName ?? meta?.universe_name ?? null,
    computedAt: snapshot.computedAt?.toISOString() ?? new Date().toISOString(),
    snapshotId: snapshot.id,
    dataSource: "db" as const,
    // Snapshot-level metadata
    scoringMethod: meta?.scoring_method ?? "percentile_rank",
    missingCategoryPolicy: meta?.missing_category_policy ?? null,
    thresholds: meta?.thresholds ?? null,
    globallyUnavailableCategories: meta?.globally_unavailable_categories ?? [],
    globallyActiveCategories: meta?.globally_active_categories ?? [],
    categoryWeights: meta?.category_weights ?? null,
    passedCount: meta?.passed_count ?? rawEntries.filter(r => r.coverage_status !== "insufficient").length,
    insufficientCount: meta?.insufficient_count ?? rawEntries.filter(r => r.coverage_status === "insufficient").length,
    includeInsufficientCoverage: meta?.include_insufficient_coverage ?? false,
  };

  return NextResponse.json(result);
}
