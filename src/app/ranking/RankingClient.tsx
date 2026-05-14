"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Search } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn, formatKRW } from "@/lib/utils";

export interface RankingRow {
  rank: number;
  ticker: string;
  name: string;
  market: string;
  sector: string | null;
  marketCap: number | null;
  composite: number;
  categories: Record<string, number | null>;
  coverage: number;       // active_weight_coverage 0-1
  factorCount: number;
  status: "passed" | "insufficient" | "non_pit_market_cap";
}

interface Props {
  rows: RankingRow[];
  categoryOrder: string[];
}

function scoreBadgeClasses(score: number | null): string {
  if (score === null || score === undefined) return "bg-muted text-muted-foreground";
  if (score >= 80) return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
  if (score >= 60) return "bg-lime-500/15 text-lime-700 dark:text-lime-300";
  if (score >= 40) return "bg-amber-500/15 text-amber-700 dark:text-amber-300";
  if (score >= 20) return "bg-orange-500/15 text-orange-700 dark:text-orange-300";
  return "bg-rose-500/15 text-rose-700 dark:text-rose-300";
}

export default function RankingClient({ rows, categoryOrder }: Props) {
  const [search, setSearch] = useState("");
  const [marketFilter, setMarketFilter] = useState<string>("ALL");
  const [statusFilter, setStatusFilter] = useState<string>("passed");
  const [sectorFilter, setSectorFilter] = useState<string>("ALL");

  const sectors = useMemo(() => {
    const s = new Set(rows.map(r => r.sector).filter(Boolean));
    return Array.from(s).sort() as string[];
  }, [rows]);

  const filtered = useMemo(() => {
    return rows.filter(r => {
      if (statusFilter !== "ALL" && r.status !== statusFilter) return false;
      if (marketFilter !== "ALL" && r.market !== marketFilter) return false;
      if (sectorFilter !== "ALL" && r.sector !== sectorFilter) return false;
      if (search) {
        const q = search.toLowerCase();
        if (
          !r.ticker.toLowerCase().includes(q) &&
          !r.name.toLowerCase().includes(q)
        ) return false;
      }
      return true;
    });
  }, [rows, search, marketFilter, statusFilter, sectorFilter]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search ticker or name..."
            className="pl-10"
          />
        </div>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="passed">Passed coverage</SelectItem>
            <SelectItem value="insufficient">Insufficient coverage</SelectItem>
            <SelectItem value="ALL">All</SelectItem>
          </SelectContent>
        </Select>
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
              <SelectItem key={s} value={s}>{s}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <p className="text-sm text-muted-foreground">{filtered.length.toLocaleString()} stocks</p>

      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-3 py-3 text-right text-xs font-medium text-muted-foreground w-12">#</th>
                  <th className="px-3 py-3 text-left text-xs font-medium text-muted-foreground">Ticker</th>
                  <th className="px-3 py-3 text-left text-xs font-medium text-muted-foreground">Name</th>
                  <th className="px-3 py-3 text-left text-xs font-medium text-muted-foreground">Sector</th>
                  <th className="px-3 py-3 text-right text-xs font-medium text-muted-foreground">Mkt Cap</th>
                  <th className="px-3 py-3 text-right text-xs font-medium text-muted-foreground">Composite</th>
                  {categoryOrder.map(c => (
                    <th key={c} className="px-2 py-3 text-right text-xs font-medium text-muted-foreground">{c}</th>
                  ))}
                  <th className="px-2 py-3 text-right text-xs font-medium text-muted-foreground">Cov</th>
                </tr>
              </thead>
              <tbody>
                {filtered.slice(0, 500).map((r) => (
                  <tr key={r.ticker} className="border-b hover:bg-muted/30 transition-colors">
                    <td className="px-3 py-2 text-right font-mono text-xs text-muted-foreground">{r.rank}</td>
                    <td className="px-3 py-2">
                      <Link href={`/stocks/${r.ticker}`} className="font-mono text-primary hover:underline">
                        {r.ticker}
                      </Link>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{r.name}</span>
                        <Badge variant="outline" className="text-[10px]">{r.market}</Badge>
                      </div>
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground max-w-[180px] truncate">{r.sector || "-"}</td>
                    <td className="px-3 py-2 text-right text-xs">{r.marketCap ? formatKRW(r.marketCap) : "-"}</td>
                    <td className="px-3 py-2 text-right">
                      <span className={cn("inline-block px-2 py-0.5 rounded font-mono text-xs font-semibold",
                        scoreBadgeClasses(r.composite))}>
                        {r.composite.toFixed(1)}
                      </span>
                    </td>
                    {categoryOrder.map(c => {
                      const v = r.categories[c];
                      return (
                        <td key={c} className="px-2 py-2 text-right">
                          {v !== null && v !== undefined ? (
                            <span className={cn("inline-block px-1.5 py-0.5 rounded font-mono text-[11px]",
                              scoreBadgeClasses(v))}>
                              {v.toFixed(0)}
                            </span>
                          ) : (
                            <span className="text-xs text-muted-foreground">—</span>
                          )}
                        </td>
                      );
                    })}
                    <td className="px-2 py-2 text-right text-xs text-muted-foreground">
                      {(r.coverage * 100).toFixed(0)}%
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
