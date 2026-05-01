"use client";

import React, { useState, useEffect, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, ArrowUpDown, Download, ChevronDown, ChevronUp, Database, FileText } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { RankingSystem, RankingResult, StockRanking } from "@/types";
import { getSystemById } from "@/lib/store";
import { runRanking, collectFactorIds, DEFAULT_RANKING_SYSTEM } from "@/lib/ranking-engine";
import { getFactorDefinitions } from "@/lib/factors";
import { cn, formatKRW, formatPercent, formatNumber, scoreColor, scoreBg } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Data source detection (client-safe — uses NEXT_PUBLIC_ env var)
// ---------------------------------------------------------------------------

function getClientDataSource(): "db" | "mock" {
  const explicit = process.env.NEXT_PUBLIC_DATA_SOURCE;
  if (explicit === "db") return "db";
  if (explicit === "mock") return "mock";
  return "mock"; // default to mock on the client
}

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
      {stock.factorScores && Object.keys(stock.factorScores).length > 0 && (
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
      )}

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
// DB snapshot metadata type (extra fields from API response)
// ---------------------------------------------------------------------------

interface DbRankingResult extends RankingResult {
  snapshotId?: number;
  dataSource?: "db" | "mock";
}

// ---------------------------------------------------------------------------
// Results Page
// ---------------------------------------------------------------------------

export default function RankingResultsPage() {
  const params = useParams();
  const id = params.id as string;

  const [system, setSystem] = useState<RankingSystem | null>(null);
  const [result, setResult] = useState<DbRankingResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dataSource, setDataSource] = useState<"db" | "mock">("mock");
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [sortField, setSortField] = useState<string>("rank");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const factorDefs = useMemo(() => getFactorDefinitions(), []);

  useEffect(() => {
    const source = getClientDataSource();
    setDataSource(source);

    async function loadResults() {
      try {
        if (source === "db") {
          // DB mode: fetch the pre-computed snapshot from the API
          console.log(`[results] Data source: db, fetching snapshot for system="${id}"`);

          const res = await fetch(`/api/ranking-snapshots/${id}`);
          const data = await res.json();

          if (!res.ok) {
            console.error(`[results] API error:`, data);
            setError(data.error || "Failed to load ranking snapshot from database.");
            setLoading(false);
            return;
          }

          console.log(`[results] DB snapshot loaded: id=${data.snapshotId}, ${data.rankings?.length} stocks`);

          // For DB mode, use the default system definition for tree/category info
          // (the system config comes from localStorage OR the default)
          const sys = getSystemById(id) ?? (id === "default" ? DEFAULT_RANKING_SYSTEM : null);
          setSystem(sys);
          setResult(data as DbRankingResult);
        } else {
          // Mock mode: run ranking engine client-side
          console.log(`[results] Data source: mock, running ranking engine for system="${id}"`);
          const sys = getSystemById(id);
          if (sys) {
            setSystem(sys);
            const rankingResult = runRanking(sys);
            setResult({ ...rankingResult, dataSource: "mock" });
          } else {
            setError("Ranking system not found in localStorage.");
          }
        }
      } catch (err) {
        console.error("[results] Error loading results:", err);
        setError(String(err));
      }
      setLoading(false);
    }

    loadResults();
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
    return <div className="text-muted-foreground">
      {dataSource === "db" ? "Loading ranking snapshot from database..." : "Computing rankings..."}
    </div>;
  }

  if (error) {
    return (
      <div className="text-center py-12 space-y-4">
        <Badge variant="destructive" className="text-sm">
          <Database className="h-3.5 w-3.5 mr-1.5" />
          Data source: Database
        </Badge>
        <p className="text-destructive font-medium">{error}</p>
        <p className="text-muted-foreground text-sm max-w-md mx-auto">
          No DB ranking snapshot found. Run{" "}
          <code className="bg-muted px-1.5 py-0.5 rounded text-xs">python smoke_test.py</code>{" "}
          or{" "}
          <code className="bg-muted px-1.5 py-0.5 rounded text-xs">python run_ranking_snapshot.py</code>{" "}
          to generate one.
        </p>
        <Link href="/ranking-systems">
          <Button variant="outline">Back to Systems</Button>
        </Link>
      </div>
    );
  }

  if (!result) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground mb-4">Ranking system not found</p>
        <Link href="/ranking-systems">
          <Button variant="outline">Back to Systems</Button>
        </Link>
      </div>
    );
  }

  // Derive category names from the result data (works for both mock and DB)
  const categoryNames = system?.tree.children?.map(c => c.name)
    ?? (result.rankings.length > 0
      ? Object.keys(result.rankings[0].categoryScores)
      : []);

  const dbResult = result as DbRankingResult;

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
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight">{system?.name ?? id}</h1>
            <Badge
              variant={dataSource === "db" ? "default" : "secondary"}
              className="text-xs"
            >
              {dataSource === "db" ? (
                <><Database className="h-3 w-3 mr-1" />Database</>
              ) : (
                <><FileText className="h-3 w-3 mr-1" />Mock</>
              )}
            </Badge>
          </div>
          <p className="text-muted-foreground text-sm mt-0.5">
            {result.rankings.length} stocks ranked from {result.universeSize} in universe
            {" | "}Date: {result.date}
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
            <p className="text-xs text-muted-foreground">
              {dataSource === "db" ? "Snapshot ID" : "Factors Used"}
            </p>
            <p className="text-xl font-bold">
              {dataSource === "db"
                ? (dbResult.snapshotId ?? "—")
                : (system ? collectFactorIds(system.tree).length : "—")}
            </p>
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

      {/* DB snapshot metadata */}
      {dataSource === "db" && dbResult.snapshotId && (
        <Card>
          <CardContent className="pt-4 pb-3">
            <div className="flex flex-wrap gap-6 text-sm">
              <div>
                <span className="text-muted-foreground">Snapshot ID: </span>
                <span className="font-mono font-medium">{dbResult.snapshotId}</span>
              </div>
              <div>
                <span className="text-muted-foreground">As-of date: </span>
                <span className="font-mono font-medium">{result.date}</span>
              </div>
              <div>
                <span className="text-muted-foreground">Stock count: </span>
                <span className="font-mono font-medium">{result.rankings.length}</span>
              </div>
              <div>
                <span className="text-muted-foreground">Created: </span>
                <span className="font-mono font-medium">{new Date(result.computedAt).toLocaleString()}</span>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

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
