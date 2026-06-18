import Link from "next/link";
import { Button } from "@/components/ui/button";
import {
  fetchStockByTicker,
  fetchStockPriceHistory,
  fetchLatestFinancialsForTicker,
  fetchLatestPriceForTicker,
  fetchLatestRankingSnapshot,
  fetchFactorSnapshotsForStock,
  fetchLatestPriceDate,
} from "@/lib/data-service.server";
import StockDetailClient from "./StockDetailClient";

interface PageProps {
  params: Promise<{ ticker: string }>;
}

export const revalidate = 3600;

export default async function StockDetailPage({ params }: PageProps) {
  const { ticker } = await params;

  const stock = await fetchStockByTicker(ticker);
  if (!stock) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground mb-4">Stock {ticker} not found</p>
        <Link href="/universe"><Button variant="outline">Back to Universe</Button></Link>
      </div>
    );
  }

  const asOf = (await fetchLatestPriceDate()) ?? new Date().toISOString().slice(0, 10);

  const [priceHistory, latestPrice, financials, snapshot] = await Promise.all([
    fetchStockPriceHistory(ticker),
    fetchLatestPriceForTicker(ticker),
    fetchLatestFinancialsForTicker(ticker, asOf),
    fetchLatestRankingSnapshot("p123-inspired", "krx_all_current"),
  ]);

  // Pull factor snapshots for this ticker on the snapshot's date so we can
  // show universe-relative percentiles, not just raw recomputed values.
  const factorSnapshots = snapshot
    ? await fetchFactorSnapshotsForStock(
        ticker,
        snapshot.date,
        snapshot.universeName ?? "krx_all_current",
      )
    : [];

  return (
    <StockDetailClient
      stock={stock}
      latestPrice={latestPrice}
      priceHistory={priceHistory}
      financials={financials}
      factorSnapshots={factorSnapshots}
      snapshotDate={snapshot?.date ?? null}
    />
  );
}
