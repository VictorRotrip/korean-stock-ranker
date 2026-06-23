import { NextRequest, NextResponse } from "next/server";
import { getDb, hasDatabase, schema } from "@/db";
import { and, eq, sql } from "drizzle-orm";
import type { RankingSystem } from "@/types";
import { computeNodeScore, collectFactorIds } from "@/lib/ranking-engine";

export const runtime = "nodejs";

/**
 * POST /api/ranking/run-live
 * Body: { system: RankingSystem, universe?: string }
 *
 * Ranks a (possibly custom, user-built) ranking system against the LIVE factor
 * data, computed on the server with the same tree-composition the engine uses.
 * Unlike /api/ranking-snapshots/[id] this needs no pre-computed snapshot, so any
 * custom system from the builder can be run against today's data.
 */
export async function POST(request: NextRequest) {
  if (!hasDatabase()) {
    return NextResponse.json({ error: "Database not configured" }, { status: 503 });
  }

  let body: { system?: RankingSystem; universe?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }
  const system = body.system;
  const universe = body.universe || "krx_all_current";
  if (!system || !system.tree) {
    return NextResponse.json({ error: "Invalid ranking system" }, { status: 400 });
  }

  const db = getDb()!;

  // Latest date with factor data for this universe.
  const dateRows = await db.execute(sql`
    SELECT max(date)::text AS d FROM factor_snapshots WHERE universe_name = ${universe}
  `);
  const asOf = (dateRows as unknown as Array<{ d: string | null }>)[0]?.d;
  if (!asOf) {
    return NextResponse.json({ error: `No factor data for universe ${universe}` }, { status: 404 });
  }

  // Per-ticker factor percentiles (+ raw + explain) for the tree composition.
  const factorRows = await db
    .select({
      ticker: schema.factorSnapshots.ticker,
      factorId: schema.factorSnapshots.factorId,
      rawValue: schema.factorSnapshots.rawValue,
      percentileRank: schema.factorSnapshots.percentileRank,
      explain: schema.factorSnapshots.explain,
    })
    .from(schema.factorSnapshots)
    .where(and(
      eq(schema.factorSnapshots.date, asOf),
      eq(schema.factorSnapshots.universeName, universe),
    ));

  const byTicker = new Map<string, Map<string, { raw: number | null; pct: number; explain: string | null }>>();
  for (const r of factorRows) {
    let m = byTicker.get(r.ticker);
    if (!m) { m = new Map(); byTicker.set(r.ticker, m); }
    m.set(r.factorId, {
      raw: r.rawValue,
      pct: r.percentileRank ?? 50,
      explain: r.explain ?? null,
    });
  }

  // Stock metadata + market cap.
  const tickers = [...byTicker.keys()];
  const stockRows = tickers.length
    ? await db.select().from(schema.stocks).where(sql`${schema.stocks.ticker} = ANY(${tickers})`)
    : [];
  const stockMap = new Map(stockRows.map(s => [s.ticker, s]));

  const priceRows = tickers.length ? await db.execute(sql`
    SELECT DISTINCT ON (ticker) ticker, market_cap
    FROM daily_prices
    WHERE ticker = ANY(${tickers}) AND market_cap IS NOT NULL AND date <= ${asOf}
    ORDER BY ticker, date DESC
  `) : [];
  const mcapMap = new Map<string, number>();
  for (const p of priceRows as unknown as Array<{ ticker: string; market_cap: number }>) {
    mcapMap.set(p.ticker, Number(p.market_cap));
  }

  const systemFactorIds = collectFactorIds(system.tree);
  const categories = system.tree.children ?? [];
  const minFactors = system.options && (system.options as { minFactors?: number }).minFactors
    ? (system.options as { minFactors?: number }).minFactors as number
    : 5;

  const rankings = tickers.map(ticker => {
    const ranks = new Map<string, number | null>();
    const fs: Record<string, { rawValue: number | null; percentileRank: number; explain?: string | null }> = {};
    const tf = byTicker.get(ticker)!;
    let present = 0;
    for (const fid of systemFactorIds) {
      const cell = tf.get(fid);
      ranks.set(fid, cell ? cell.pct : null);
      if (cell) {
        present += 1;
        fs[fid] = { rawValue: cell.raw, percentileRank: cell.pct, explain: cell.explain };
      }
    }
    const composite = computeNodeScore(system.tree, ranks);
    const categoryScores: Record<string, number> = {};
    for (const c of categories) {
      const cs = computeNodeScore(c, ranks);
      if (cs !== null) categoryScores[c.name] = Math.round(cs * 100) / 100;
    }
    const stock = stockMap.get(ticker);
    const passes = composite !== null && present >= minFactors;
    return {
      rank: null as number | null,
      ticker,
      name: stock?.name ?? ticker,
      nameEn: stock?.nameEn ?? undefined,
      market: (stock?.market ?? "KOSPI") as "KOSPI" | "KOSDAQ",
      sector: stock?.sector ?? undefined,
      industry: stock?.industry ?? undefined,
      marketCap: mcapMap.get(ticker) ?? 0,
      compositeScore: composite ?? 0,
      categoryScores,
      factorScores: fs,
      factorCount: present,
      coverageStatus: (passes ? "passed" : "insufficient") as "passed" | "insufficient",
      passesMinimum: passes,
    };
  });

  const main = rankings.filter(r => r.passesMinimum).sort((a, b) => b.compositeScore - a.compositeScore);
  main.forEach((r, i) => { r.rank = i + 1; });
  const insufficient = rankings.filter(r => !r.passesMinimum);
  const ordered = [...main, ...insufficient];

  return NextResponse.json({
    rankingSystemId: system.id,
    date: asOf,
    rankings: ordered,
    universeSize: tickers.length,
    universeName: universe,
    computedAt: new Date().toISOString(),
    dataSource: "db" as const,
    scoringMethod: "percentile_rank",
    passedCount: main.length,
    insufficientCount: insufficient.length,
    isLiveCustom: true,
  });
}
