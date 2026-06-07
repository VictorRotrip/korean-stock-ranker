import { Card, CardContent } from "@/components/ui/card";
import { fetchBacktestPayload } from "@/lib/data-service.server";
import BacktestClient from "./BacktestClient";

// Historical-universe payload (~137 snapshots × 3,400 tickers + forward
// returns) is ~50MB — larger than Vercel's 19MB ISR fallback limit. So we
// render dynamically on each request; Vercel's edge cache + the browser's
// in-memory React state still keep slider drags snappy.
export const dynamic = "force-dynamic";
// Generous timeout for the heavy initial data fetch.
export const maxDuration = 60;

// Default weights (Portfolio123-style balanced). Mirror the seeds used by
// run_ranking_snapshot.py for the p123-inspired system so the page lands on
// the same blend the live ranking uses.
const DEFAULT_WEIGHTS: Record<string, number> = {
  "Value": 25,
  "Quality": 20,
  "Growth": 15,
  "Momentum": 25,
  "Low Volatility": 5,
  "Sentiment": 10,
};

export default async function BacktestPage() {
  const payload = await fetchBacktestPayload("p123-inspired", "krx_all_historical", 30);

  if (!payload || payload.snapshots.length === 0) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Rank Performance Backtest</h1>
          <p className="text-muted-foreground mt-1">
            No ranking snapshots found yet.
          </p>
        </div>
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            Run <code className="bg-muted px-1 rounded">python backfill_history.py</code> followed by{" "}
            <code className="bg-muted px-1 rounded">python backtest_forward_returns.py --universe krx_all_current</code>{" "}
            to populate the backtest data, then refresh this page.
          </CardContent>
        </Card>
      </div>
    );
  }

  // Default weights: only include categories that actually appear in the data.
  const defaults: Record<string, number> = {};
  for (const c of payload.categories) {
    defaults[c] = DEFAULT_WEIGHTS[c] ?? 0;
  }

  // Bookkeeping for header
  const usable = payload.snapshots.filter(s => payload.returns[s.date]?.length);
  const firstDate = usable.length > 0 ? usable[0].date : payload.snapshots[0].date;
  const lastDate = usable.length > 0
    ? usable[usable.length - 1].date
    : payload.snapshots[payload.snapshots.length - 1].date;

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Rank Performance Backtest</h1>
          <p className="text-muted-foreground text-sm mt-1">
            {usable.length} rebalances from {firstDate} to {lastDate}
            {" · "}universe <span className="font-mono">{payload.universeName}</span>
            {" · "}30-day hold
          </p>
        </div>
        <div className="text-xs text-muted-foreground">
          {payload.snapshots.length} total snapshots in DB
        </div>
      </div>

      <BacktestClient payload={payload} defaultWeights={defaults} />
    </div>
  );
}
