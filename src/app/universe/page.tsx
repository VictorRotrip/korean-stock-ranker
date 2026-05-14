import { fetchStocks, fetchLatestPrices, getDataSource } from "@/lib/data-service.server";
import UniverseClient, { type EnrichedStock } from "./UniverseClient";

export const revalidate = 3600;

// Server Component: fetch stocks + latest prices, hand off to the client UI.
export default async function UniversePage() {
  const dataSource = getDataSource();
  const [stocks, priceMap] = await Promise.all([
    fetchStocks(),
    fetchLatestPrices(),
  ]);

  const enriched: EnrichedStock[] = stocks
    .filter(s => !s.isPreferred && !s.isEtf && !s.isSpac)
    .map(s => {
      const p = priceMap.get(s.ticker);
      return {
        ...s,
        marketCap: p?.marketCap ?? 0,
        price: p?.close ?? 0,
        volume: p?.volume ?? 0,
      };
    });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Stock Universe</h1>
        <p className="text-muted-foreground mt-1">
          {stocks.length.toLocaleString()} active stocks across KOSPI and KOSDAQ
          {dataSource === "mock" && " (mock data)"}
        </p>
      </div>

      <UniverseClient stocks={enriched} />
    </div>
  );
}
