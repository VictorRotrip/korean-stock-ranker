"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { Search } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { formatKRW } from "@/lib/utils";
import { displayName, translateIndustry } from "@/lib/i18n";
import type { Stock } from "@/types";

export interface EnrichedStock extends Stock {
  marketCap: number;
  price: number;
  volume: number;
}

interface Props {
  stocks: EnrichedStock[];
}

export default function UniverseClient({ stocks }: Props) {
  const [search, setSearch] = useState("");
  const [marketFilter, setMarketFilter] = useState<string>("ALL");
  const [sectorFilter, setSectorFilter] = useState<string>("ALL");

  const sectors = useMemo(() => {
    const s = new Set(stocks.map(st => st.sector).filter(Boolean));
    return (Array.from(s) as string[]).sort((a, b) =>
      (translateIndustry(a) ?? "").localeCompare(translateIndustry(b) ?? ""),
    );
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
      .sort((a, b) => b.marketCap - a.marketCap);
  }, [stocks, search, marketFilter, sectorFilter]);

  return (
    <div className="space-y-4">
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
          <SelectTrigger className="w-56">
            <SelectValue placeholder="Sector" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All Sectors</SelectItem>
            {sectors.map(s => (
              <SelectItem key={s} value={s}>{translateIndustry(s)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <p className="text-sm text-muted-foreground">{filtered.length.toLocaleString()} stocks</p>

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
                {filtered.slice(0, 500).map((stock) => (
                  <tr key={stock.ticker} className="border-b hover:bg-muted/30 transition-colors">
                    <td className="px-4 py-3">
                      <Link href={`/stocks/${stock.ticker}`} className="font-mono text-primary hover:underline">
                        {stock.ticker}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <div>
                        <p className="font-medium">{displayName(stock)}</p>
                        {stock.nameEn && stock.nameEn.trim() && (
                          <p className="text-xs text-muted-foreground">{stock.name}</p>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant="outline" className="text-xs">{stock.market}</Badge>
                    </td>
                    <td
                      className="px-4 py-3 text-xs text-muted-foreground min-w-[16rem]"
                      title={translateIndustry(stock.sector) ?? undefined}
                    >
                      {translateIndustry(stock.sector) || "-"}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-xs">
                      {stock.price.toLocaleString("ko-KR")}
                    </td>
                    <td className="px-4 py-3 text-right text-xs">{formatKRW(stock.marketCap)}</td>
                    <td className="px-4 py-3 text-right text-xs">
                      {stock.volume.toLocaleString("ko-KR")}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1">
                        {stock.isFinancial && <Badge variant="secondary" className="text-[10px] px-1">Financial</Badge>}
                        {stock.isHolding && <Badge variant="secondary" className="text-[10px] px-1">Holding</Badge>}
                        {stock.isReit && <Badge variant="secondary" className="text-[10px] px-1">REIT</Badge>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filtered.length > 500 && (
              <div className="px-4 py-3 text-xs text-muted-foreground border-t">
                Showing first 500 of {filtered.length.toLocaleString()} — refine filters to narrow further.
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
