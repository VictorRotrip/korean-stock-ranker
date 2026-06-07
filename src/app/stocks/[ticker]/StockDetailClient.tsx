"use client";

import { useMemo } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { FACTOR_REGISTRY, type FactorInput } from "@/lib/factors";
import { formatKRW, formatNumber } from "@/lib/utils";
import { displayName, translateIndustry } from "@/lib/i18n";
import type { Stock, DailyPrice, FinancialStatement } from "@/types";

interface FactorSnapshotRow {
  factorId: string;
  rawValue: number | null;
  percentileRank: number | null;
  source: string | null;
  scope: string | null;
}

interface Props {
  stock: Stock;
  latestPrice: DailyPrice | null;
  priceHistory: DailyPrice[];
  financials: FinancialStatement | null;
  factorSnapshots: FactorSnapshotRow[];
  snapshotDate: string | null;
}

function PriceChart({ data }: { data: { date: string; close: number }[] }) {
  if (data.length < 2) return <div className="text-muted-foreground text-sm">Insufficient data</div>;

  const width = 700;
  const height = 200;
  const padding = { top: 10, right: 10, bottom: 30, left: 60 };

  const prices = data.map(d => d.close);
  const minP = Math.min(...prices) * 0.98;
  const maxP = Math.max(...prices) * 1.02;

  const xScale = (i: number) => padding.left + (i / (data.length - 1)) * (width - padding.left - padding.right);
  const yScale = (v: number) => padding.top + (1 - (v - minP) / (maxP - minP)) * (height - padding.top - padding.bottom);

  const pathD = data.map((d, i) => `${i === 0 ? "M" : "L"} ${xScale(i).toFixed(1)} ${yScale(d.close).toFixed(1)}`).join(" ");

  const firstPrice = data[0].close;
  const lastPrice = data[data.length - 1].close;
  const isUp = lastPrice >= firstPrice;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ maxHeight: 200 }}>
      {[0, 0.25, 0.5, 0.75, 1].map(pct => {
        const val = minP + pct * (maxP - minP);
        return (
          <g key={pct}>
            <line x1={padding.left} y1={yScale(val)} x2={width - padding.right} y2={yScale(val)} stroke="currentColor" strokeOpacity="0.1" strokeDasharray="2,2" />
            <text x={padding.left - 5} y={yScale(val) + 4} textAnchor="end" className="text-[10px] fill-muted-foreground">
              {(val / 1000).toFixed(0)}K
            </text>
          </g>
        );
      })}
      {[0, Math.floor(data.length / 2), data.length - 1].map(i => (
        <text key={i} x={xScale(i)} y={height - 5} textAnchor="middle" className="text-[10px] fill-muted-foreground">
          {data[i].date.substring(5)}
        </text>
      ))}
      <path d={pathD} fill="none" stroke={isUp ? "hsl(142, 71%, 45%)" : "hsl(0, 84%, 60%)"} strokeWidth={1.5} />
    </svg>
  );
}

export default function StockDetailClient({
  stock,
  latestPrice,
  priceHistory,
  financials,
  factorSnapshots,
  snapshotDate,
}: Props) {
  // Build a lookup from factor_id -> { rawValue, percentileRank } so we can
  // show universe-relative percentile ranks alongside the raw computation.
  const snapshotByFactor = useMemo(() => {
    const m = new Map<string, FactorSnapshotRow>();
    for (const r of factorSnapshots) m.set(r.factorId, r);
    return m;
  }, [factorSnapshots]);

  // Trust the snapshot fully. If a factor isn't in the snapshot at all
  // (no row for this ticker/factor), mark it as "not computed" rather than
  // silently recomputing via the TS library — that double-source led to
  // confusing 0.0000 displays when the snapshot rawValue was null.
  const factorValues = useMemo(() => {
    return FACTOR_REGISTRY.map(f => {
      const stored = snapshotByFactor.get(f.id);
      return {
        id: f.id,
        name: f.name,
        category: f.category,
        direction: f.direction,
        rawValue: stored ? stored.rawValue : null,
        percentileRank: stored ? stored.percentileRank : null,
        source: stored?.source ?? null,
        inSnapshot: !!stored,
      };
    });
  }, [snapshotByFactor]);

  const chartData = priceHistory.slice(-120).map(p => ({ date: p.date, close: p.close }));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link href="/universe">
          <Button variant="ghost" size="icon"><ArrowLeft className="h-4 w-4" /></Button>
        </Link>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{displayName(stock)}</h1>
            <Badge variant="outline">{stock.market}</Badge>
          </div>
          <p className="text-muted-foreground text-sm">
            {stock.ticker}
            {/* Show the Korean name as the secondary label when the English name is the headline */}
            {stock.nameEn && stock.nameEn.trim() ? ` · ${stock.name}` : ""}
            {translateIndustry(stock.sector) ? ` · ${translateIndustry(stock.sector)}` : ""}
          </p>
        </div>
        {latestPrice && (
          <div className="text-right">
            <p className="text-2xl font-bold">{latestPrice.close.toLocaleString("ko-KR")} KRW</p>
            <p className="text-sm text-muted-foreground">Market Cap: {formatKRW(latestPrice.marketCap)}</p>
          </div>
        )}
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="financials">Financials</TabsTrigger>
          <TabsTrigger value="factors">
            Factor Scores
            {snapshotDate && (
              <span className="text-[10px] text-muted-foreground ml-2">@{snapshotDate}</span>
            )}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Price History (last 6 months)</CardTitle>
            </CardHeader>
            <CardContent>
              <PriceChart data={chartData} />
            </CardContent>
          </Card>

          <div className="grid gap-4 md:grid-cols-3">
            <Card>
              <CardContent className="pt-4">
                <p className="text-xs text-muted-foreground">Volume</p>
                <p className="text-lg font-bold">{latestPrice?.volume?.toLocaleString("ko-KR") ?? "N/A"}</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                <p className="text-xs text-muted-foreground">Shares Outstanding</p>
                <p className="text-lg font-bold">{latestPrice?.sharesOutstanding?.toLocaleString("ko-KR") ?? "N/A"}</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                <p className="text-xs text-muted-foreground">Sector / Industry</p>
                <p className="text-sm font-medium">{translateIndustry(stock.sector) ?? "-"}</p>
                <p className="text-xs text-muted-foreground">{translateIndustry(stock.industry) ?? "-"}</p>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="financials">
          {financials ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  Financial Statement — Period Ending {financials.periodEnd}
                </CardTitle>
                <p className="text-xs text-muted-foreground">Filed: {financials.filingDate}</p>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                  {([
                    ["Revenue", financials.revenue],
                    ["Gross Profit", financials.grossProfit],
                    ["Operating Income", financials.operatingIncome],
                    ["Net Income", financials.netIncome],
                    ["EBITDA", financials.ebitda],
                    ["Total Assets", financials.totalAssets],
                    ["Total Equity", financials.totalEquity],
                    ["Total Debt", financials.totalDebt],
                    ["Cash", financials.cash],
                    ["Operating CF", financials.operatingCashFlow],
                    ["CapEx", financials.capitalExpenditure],
                    ["Free Cash Flow", financials.freeCashFlow],
                    ["EPS", financials.eps],
                    ["BV/Share", financials.bookValuePerShare],
                    ["Dividends Paid", financials.dividendsPaid],
                  ] as [string, number | null][]).map(([label, val]) => (
                    <div key={label}>
                      <p className="text-xs text-muted-foreground">{label}</p>
                      <p className="text-sm font-medium">
                        {val !== null && val !== undefined ? (Math.abs(val) >= 1e8 ? formatKRW(val) : formatNumber(val)) : "N/A"}
                      </p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ) : (
            <Card><CardContent className="py-8 text-center text-muted-foreground">No financial data available</CardContent></Card>
          )}
        </TabsContent>

        <TabsContent value="factors">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Factor Values</CardTitle>
              <p className="text-xs text-muted-foreground">
                Raw value + universe-relative percentile (where available). Higher percentile = better relative to universe.
              </p>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {factorValues.map(f => (
                  <div key={f.id} className="flex items-center justify-between px-3 py-2 rounded border">
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">{f.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {f.category}{f.source ? ` · ${f.source}` : ""}
                      </p>
                    </div>
                    <div className="text-right ml-3 shrink-0">
                      <p className="font-mono text-sm">
                        {f.rawValue !== null && f.rawValue !== undefined ? formatNumber(f.rawValue, 4) : "N/A"}
                      </p>
                      {f.percentileRank !== null && f.percentileRank !== undefined && (
                        <p className="text-xs text-muted-foreground">
                          pct {f.percentileRank.toFixed(0)}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
