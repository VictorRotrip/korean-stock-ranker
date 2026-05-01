"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { Search } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getStocks, getLatestPrices } from "@/lib/mock-data";
import { formatKRW } from "@/lib/utils";

export default function UniversePage() {
  const [search, setSearch] = useState("");
  const [marketFilter, setMarketFilter] = useState<string>("ALL");
  const [sectorFilter, setSectorFilter] = useState<string>("ALL");

  const stocks = getStocks();
  const latestPrices = getLatestPrices();

  const sectors = useMemo(() => {
    const s = new Set(stocks.map(st => st.sector).filter(Boolean));
    return Array.from(s).sort();
  }, [stocks]);

  const filtered = useMemo(() => {
    return stocks
      .filter(s => {
        if (marketFilter !== "ALL" && s.market !== marketFilter) return false;
        if (sectorFilter !== "ALL" && s.sector !== sectorFilter) return false;
        if (search) {
          const q = search.toLowerCase();
          return (
            s.ticker.toLowerCase().includes(q) ||
            s.name.toLowerCase().includes(q) ||
            (s.nameEn ?? "").toLowerCase().includes(q)
          );
        }
        return true;
      })
      .map(s => ({
        ...s,
        marketCap: latestPrices.get(s.ticker)?.marketCap ?? 0,
        price: latestPrices.get(s.ticker)?.close ?? 0,
        volume: latestPrices.get(s.ticker)?.volume ?? 0,
      }))
      .sort((a, b) => b.marketCap - a.marketCap);
  }, [stocks, search, marketFilter, sectorFilter, latestPrices]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Stock Universe</h1>
        <p className="text-muted-foreground mt-1">
          {stocks.length} stocks across KOSPI and KOSDAQ (mock data)
        </p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name, ticker, or English name..."
            className="pl-10"
          />
        </div>
        <Select value={marketFilter} onValueChange={setMarketFilter}>
          <SelectTrigger className="w-32">
            <SelectValue placeholder="Market" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All Markets</SelectItem>
            <SelectItem value="KOSPI">KOSPI</SelectItem>
            <SelectItem value="KOSDAQ">KOSDAQ</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sectorFilter} onValueChange={setSectorFilter}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Sector" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All Sectors</SelectItem>
            {sectors.map(s => (
              <SelectItem key={s} value={s!}>{s}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <p className="text-sm text-muted-foreground">{filtered.length} stocks</p>

      {/* Stock table */}
      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Ticker</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Name</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Market</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Sector</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground">Price</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground">Market Cap</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground">Volume</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Flags</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((stock) => (
                  <tr key={stock.ticker} className="border-b hover:bg-muted/30 transition-colors">
                    <td className="px-4 py-3">
                      <Link href={`/stocks/${stock.ticker}`} className="font-mono text-primary hover:underline">
                        {stock.ticker}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <div>
                        <p className="font-medium">{stock.name}</p>
                        {stock.nameEn && <p className="text-xs text-muted-foreground">{stock.nameEn}</p>}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant="outline" className="text-xs">{stock.market}</Badge>
                    </td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">{stock.sector || "-"}</td>
                    <td className="px-4 py-3 text-right font-mono text-xs">
                      {stock.price.toLocaleString("ko-KR")}
                    </td>
                    <td className="px-4 py-3 text-right text-xs">{formatKRW(stock.marketCap)}</td>
                    <td className="px-4 py-3 text-right text-xs">
                      {stock.volume.toLocaleString("ko-KR")}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1">
                        {stock.isFinancial && <Badge variant="secondary" className="text-[10px] px-1">금융</Badge>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
