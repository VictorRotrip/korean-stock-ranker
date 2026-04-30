import { NextRequest, NextResponse } from "next/server";
import type { RankingSystem } from "@/types";
import { runRanking } from "@/lib/ranking-engine";

/**
 * POST /api/ranking/run
 * Body: { system: RankingSystem, date?: string }
 * Returns: RankingResult
 *
 * This endpoint runs the ranking engine server-side.
 * In the MVP, the client can also run rankings client-side using the same engine code.
 * This API exists for future use when rankings become heavier (real DB queries, etc.).
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const system = body.system as RankingSystem;
    const date = (body.date as string) || "2024-12-20";

    if (!system || !system.tree) {
      return NextResponse.json({ error: "Invalid ranking system" }, { status: 400 });
    }

    const result = runRanking(system, date);

    return NextResponse.json(result);
  } catch (error) {
    console.error("Ranking error:", error);
    return NextResponse.json(
      { error: "Failed to run ranking" },
      { status: 500 }
    );
  }
}
