"use client";

import React, { useState, useEffect, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, ArrowUpDown, Download, ChevronDown, ChevronUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { RankingSystem, RankingResult, StockRanking } from "@/types";
import { getSystemById } from "@/lib/store";
import { runRanking, collectFactorIds } from "@/lib/ranking-engine";
import { getFactorDefinitions } from "@/lib/factors";
import { cn, formatKRW, formatPercent, formatNumber, scoreColor, scoreBg } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Score Cell Component
// ---------------------------------------------------------------------------

function ScoreCell({ score }: { score: number }) {
  return (
    <span className={cn("font-mono text-sm", scoreColor(score))}>
      {score.toFixed(1)}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Expanded Stock Row
// ---------------------------------------------------------------------------

function StockDetailRow({
  stock,
  factorDefs,
}: {
  stock: StockRanking;
  factorDefs: ReturnType<typeof getFactorDefinitions>;
}) {
  return (
    <div className="bg-muted/30 px-4 py-3 border-t space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <p className="text-xs text-muted-foreground">Ticker</p>
          <p className="text-sm font-mono">{stock.ticker}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Market</p>
          <p className="text-sm">{stock.market}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Sector</p>
          <p className="text-sm">{stock.sector || "N/A"}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Market Cap</p>
          <p className="text-sm">{formatKRW(stock.marketCap)}</p>
        </div>
      </div>

      {/* Category breakdown */}
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-2">Category Scores</p>
        <div className="flex flex-wrap gap-2">
          {Object.entries(stock.categoryScores).map(([name, score]) => (
            <div key={name} className={cn("px-3 py-1.5 rounded-md text-xs", scoreBg(score))}>
              <span className="text-muted-foreground">{name}: </span>
              <span className={cn("font-medium", scoreColor(score))}>{score.toFixed(1)}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Individual factor scores */}
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-2">Factor Details</p>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
          {Object.entries(stock.factorScores).map(([factorId, data]) => {
            const def = factorDefs.find(f => f.id === factorId);
            return (
              <div
                key={factorId}
                className="flex items-center justify-between px-3 py-1.5 rounded border bg-card text-xs"
              >
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">{def?.name ?? factorId}</p>
                  <p className="text-muted-foreground">
                    Raw: {data.rawValue !== null ? formatNumber(data.rawValue, 4) : "N/A"}
                  </p>
                </div>
                <div className={cn("ml-2 font-mono font-medium", scoreColor(data.percentileRank))}>
                  {data.percentileRank.toFixed(1)}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="pt-1">
        <Link href={`/stocks/${stock.ticker}`}>
          <Button variant="outline" size="sm" className="text-xs">
            View Full Stock Detail
          </Button>
        </Link>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Results Page
// ---------------------------------------------------------------------------

export default function RankingResultsPage() {
  const params = useParams();
  const id = params.id as string;

  const [system, setSystem] = useState<RankingSystem | null>(null);
  const [result, setResult] = useState<RankingResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [sortField, setSortField] = useState<string>("rank");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const factorDefs = useMemo(() => getFactorDefinitions(), []);

  useEffect(() => {
    const sys = getSystemById(id);
    if (sys) {
      setSystem(sys);
      // Run the ranking engine
      const rankingResult = runRanking(sys);
      setResult(rankingResult);
    }
    setLoading(false);
  }, [id]);

  const toggleRow = (ticker: string) => {
    setExpandedRows(prev => {
      const next = new Set(prev);
      if (next.has(ticker)) next.delete(ticker);
      else next.add(ticker);
      return next;
    });
  };

  const handleSort = (field: string) => {
    if (sortField === field) {
      setSortDir(d => d === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortDir(field === "rank" ? "asc" : "desc");
    }
  };

  const sortedRankings = useMemo(() => {
    if (!result) return [];
    const sorted = [...result.rankings];

    sorted.sort((a, b) => {
      let aVal: number, bVal: number;

      switch (sortField) {
        case "rank":
          aVal = a.rank; bVal = b.rank; break;
        case "composite":
          aVal = a.compositeScore; bVal = b.compositeScore; break;
        case "marketCap":
          aVal = a.marketCap; bVal = b.marketCap; break;
        default:
          // Category sort
          aVal = a.categoryScores[sortField] ?? 0;
          bVal = b.categoryScores[sortField] ?? 0;
      }

      return sortDir === "asc" ? aVal - bVal : bVal - aVal;
    });

    return sorted;
  }, [result, sortField, sortDir]);

  if (loading) {
    return <div className="text-muted-foreground">Computing rankings...</div>;
  }

  if (!system || !result) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground mb-4">Ranking system not found</p>
        <Link href="/ranking-systems">
          <Button variant="outline">Back to Systems</Button>
        </Link>
      </div>
    );
  }

  const categoryNames = system.tree.children?.map(c => c.name) ?? [];

  // Sort header component
  const SortHeader = ({ field, label, className }: { field: string; label: string; className?: string }) => (
    <button
      onClick={() => handleSort(field)}
      className={cn("flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground", className)}
    >
      {label}
      {sortField === field && (
        sortDir === "asc" ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />
      )}
    </button>
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link href={`/ranking-systems/${id}`}>
          <Button variant="ghost" size="icon">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-bold tracking-tight">{system.name}</h1>
          <p className="text-muted-foreground text-sm mt-0.5">
            {result.rankings.length} stocks ranked from {result.universeSize} in universe
            {" | "}Computed: {new Date(result.computedAt).toLocaleString()}
          </p>
        </div>
      </div>

      {/* Summary stats */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Universe Size</p>
            <p className="text-xl font-bold">{result.universeSize}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Factors Used</p>
            <p className="text-xl font-bold">{collectFactorIds(system.tree).length}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Top Score</p>
            <p className="text-xl font-bold text-green-600">
              {result.rankings[0]?.compositeScore.toFixed(1) ?? "N/A"}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Median Score</p>
            <p className="text-xl font-bold">
              {result.rankings.length > 0
                ? result.rankings[Math.floor(result.rankings.length / 2)].compositeScore.toFixed(1)
                : "N/A"}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Results table */}
      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-4 py-3 text-left">
                    <SortHeader field="rank" label="Rank" />
                  </th>
                  <th className="px-4 py-3 text-left">
                    <span className="text-xs font-medium text-muted-foreground">Stock</span>
                  </th>
                  <th className="px-4 py-3 text-left hidden md:table-cell">
                    <span className="text-xs font-medium text-muted-foreground">Market</span>
                  </th>
                  <th className="px-4 py-3 text-left hidden lg:table-cell">
                    <span className="text-xs font-medium text-muted-foreground">Sector</span>
                  </th>
                  <th className="px-4 py-3 text-right">
                    <SortHeader field="marketCap" label="Market Cap" className="justify-end" />
                  </th>
                  <th className="px-4 py-3 text-right">
                    <SortHeader field="composite" label="Composite" className="justify-end" />
                  </th>
                  {categoryNames.map(cat => (
                    <th key={cat} className="px-4 py-3 text-right hidden xl:table-cell">
                      <SortHeader field={cat} label={cat} className="justify-end" />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sortedRankings.map((stock) => (
                  <React.Fragment key={stock.ticker}>
                    <tr
                      className="border-b hover:bg-muted/30 cursor-pointer transition-colors"
                      onClick={() => toggleRow(stock.ticker)}
                    >
                      <td className="px-4 py-3">
                        <span className="font-mono text-xs text-muted-foreground">{stock.rank}</span>
                      </td>
                      <td className="px-4 py-3">
                        <div>
                          <p className="font-medium text-sm">{stock.name}</p>
                          <p className="text-xs text-muted-foreground">{stock.ticker}</p>
                        </div>
                      </td>
                      <td className="px-4 py-3 hidden md:table-cell">
                        <Badge variant="outline" className="text-xs">{stock.market}</Badge>
                      </td>
                      <td className="px-4 py-3 hidden lg:table-cell text-xs text-muted-foreground">
                        {stock.sector || "-"}
                      </td>
                      <td className="px-4 py-3 text-right text-xs">
                        {formatKRW(stock.marketCap)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className={cn("inline-flex items-center px-2 py-0.5 rounded", scoreBg(stock.compositeScore))}>
                          <ScoreCell score={stock.compositeScore} />
                        </div>
                      </td>
                      {categoryNames.map(cat => (
                        <td key={cat} className="px-4 py-3 text-right hidden xl:table-cell">
                          <ScoreCell score={stock.categoryScores[cat] ?? 0} />
                        </td>
                      ))}
                    </tr>
                    {expandedRows.has(stock.ticker) && (
                      <tr>
                        <td colSpan={6 + categoryNames.length}>
                          <StockDetailRow stock={stock} factorDefs={factorDefs} />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
