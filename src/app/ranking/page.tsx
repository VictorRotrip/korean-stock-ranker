import { Card, CardContent } from "@/components/ui/card";
import {
  fetchLatestRankingSnapshot,
  fetchStocks,
  fetchLatestPrices,
} from "@/lib/data-service.server";
import RankingClient, { type RankingRow } from "./RankingClient";

export const revalidate = 1800;  // 30 min — daily snapshot updates land in the morning

const CATEGORY_ORDER = ["Value", "Quality", "Growth", "Momentum", "Low Volatility", "Sentiment"];

export default async function RankingPage() {
  const [snapshot, stocks, priceMap] = await Promise.all([
    fetchLatestRankingSnapshot("p123-inspired", "krx_all_current"),
    fetchStocks(),
    fetchLatestPrices(),
  ]);

  if (!snapshot) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Today&apos;s Ranking</h1>
          <p className="text-muted-foreground mt-1">No ranking snapshot found.</p>
        </div>
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            Run <code className="bg-muted px-1 rounded">python run_ranking_snapshot.py</code> from the pipeline
            to generate a snapshot, then refresh this page.
          </CardContent>
        </Card>
      </div>
    );
  }

  const stockMap = new Map(stocks.map(s => [s.ticker, s]));

  const rows: RankingRow[] = snapshot.rankings.map(r => {
    const stock = stockMap.get(r.ticker);
    const price = priceMap.get(r.ticker);
    return {
      rank: r.rank,
      ticker: r.ticker,
      name: stock?.name ?? r.ticker,
      market: stock?.market ?? "?",
      sector: stock?.sector ?? null,
      marketCap: price?.marketCap ?? null,
      composite: r.composite_score,
      categories: r.category_scores_simple ?? {},
      coverage: r.active_weight_coverage ?? 0,
      factorCount: r.factor_count ?? 0,
      status: r.coverage_status,
    };
  });

  const passedCount = rows.filter(r => r.status === "passed").length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Today&apos;s Ranking</h1>
        <p className="text-muted-foreground mt-1">
          Snapshot id <span className="font-mono">{snapshot.id}</span> · as of {snapshot.date} · universe{" "}
          <span className="font-mono">{snapshot.universeName}</span> ·{" "}
          {passedCount.toLocaleString()} of {snapshot.universeSize?.toLocaleString() ?? rows.length} stocks passed coverage gates
        </p>
      </div>

      <RankingClient rows={rows} categoryOrder={CATEGORY_ORDER} />
    </div>
  );
}
