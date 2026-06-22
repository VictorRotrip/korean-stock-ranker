"use client";

import React, { useState, useEffect, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, ChevronDown, ChevronUp, Database, FileText, AlertTriangle, CheckCircle2, ExternalLink } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { RankingSystem, RankingResult, StockRanking, CategoryScoreDetail } from "@/types";
import { getSystemById } from "@/lib/store";
import { runRanking, collectFactorIds, DEFAULT_RANKING_SYSTEM } from "@/lib/ranking-engine";
import { getFactorDefinitions } from "@/lib/factors";
import { displayName, translateIndustry } from "@/lib/i18n";
import { cn, formatKRW, formatUSD, formatNumber, scoreColor, scoreBg, USD_KRW_RATE } from "@/lib/utils";

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
// Coverage badges
// ---------------------------------------------------------------------------

function CoverageBadge({ stock }: { stock: StockRanking }) {
  if (stock.passesMinimum === undefined) return null;
  if (stock.passesMinimum) {
    return (
      <Badge variant="outline" className="text-[10px] gap-1 border-green-500/50 text-green-700 dark:text-green-400">
        <CheckCircle2 className="h-3 w-3" />
        PASS
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-[10px] gap-1 border-amber-500/50 text-amber-700 dark:text-amber-500">
      <AlertTriangle className="h-3 w-3" />
      Insufficient
    </Badge>
  );
}

function CategoryStatusBadge({ status }: { status: CategoryScoreDetail["status"] }) {
  if (status === "available") return null;
  const labelMap: Record<CategoryScoreDetail["status"], { label: string; cls: string }> = {
    available: { label: "", cls: "" },
    missing_imputed: { label: "imputed", cls: "border-blue-500/50 text-blue-700 dark:text-blue-400" },
    missing: { label: "missing", cls: "border-amber-500/50 text-amber-700" },
    missing_renormalized: { label: "renormalized", cls: "border-amber-500/50 text-amber-700" },
    globally_unavailable: { label: "globally N/A", cls: "border-muted text-muted-foreground" },
  };
  const { label, cls } = labelMap[status];
  return (
    <Badge variant="outline" className={cn("text-[9px] px-1 h-4", cls)}>
      {label}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Expanded Stock Row
// ---------------------------------------------------------------------------

function StockDetailRow({
  stock,
  factorDefs,
  usdKrwRate,
}: {
  stock: StockRanking;
  factorDefs: ReturnType<typeof getFactorDefinitions>;
  usdKrwRate: number;
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
          <p className="text-sm">{translateIndustry(stock.sector) || "N/A"}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Market Cap</p>
          <p className="text-sm">{formatKRW(stock.marketCap)}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Median daily value (20d)</p>
          <p className="text-sm font-medium">
            {stock.medianTurnover
              ? `${formatKRW(stock.medianTurnover)}  (≈ ${formatUSD(stock.medianTurnover, usdKrwRate)})`
              : "—"}
          </p>
        </div>
      </div>

      {/* Coverage summary */}
      {stock.passesMinimum !== undefined && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-2 border-t">
          <div>
            <p className="text-xs text-muted-foreground">Active Weight</p>
            <p className="text-sm font-mono">
              {stock.activeWeightCoverage !== undefined
                ? `${(stock.activeWeightCoverage * 100).toFixed(0)}%`
                : "—"}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Active Categories</p>
            <p className="text-sm font-mono">
              {stock.activeCategoryCount ?? "—"}
              {stock.activeCategories && stock.activeCategories.length > 0 && (
                <span className="text-xs text-muted-foreground ml-1">
                  ({stock.activeCategories.join(", ")})
                </span>
              )}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Factors Available</p>
            <p className="text-sm font-mono">{stock.factorCount ?? "—"}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Coverage Status</p>
            <CoverageBadge stock={stock} />
            {stock.imputedCategories && stock.imputedCategories.length > 0 && (
              <p className="text-xs text-muted-foreground mt-0.5">
                imputed: {stock.imputedCategories.join(", ")}
              </p>
            )}
          </div>
        </div>
      )}

      {/* Failure reasons */}
      {stock.passesMinimum === false && stock.failureReasons && stock.failureReasons.length > 0 && (
        <div className="rounded border border-amber-500/30 bg-amber-50 dark:bg-amber-900/10 px-3 py-2">
          <p className="text-xs font-medium text-amber-800 dark:text-amber-400 mb-1">
            Failed coverage requirements:
          </p>
          <ul className="text-xs text-amber-700 dark:text-amber-300 list-disc ml-4 space-y-0.5">
            {stock.failureReasons.map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Category breakdown with status */}
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-2">Category Scores</p>
        <div className="flex flex-wrap gap-2">
          {Object.entries(stock.categoryScores).map(([name, score]) => {
            const detail = stock.categoryDetails?.[name];
            const status = detail?.status ?? "available";
            return (
              <div
                key={name}
                className={cn(
                  "px-3 py-1.5 rounded-md text-xs flex items-center gap-1.5",
                  scoreBg(score)
                )}
              >
                <span className="text-muted-foreground">{name}:</span>
                <span className={cn("font-medium", scoreColor(score))}>
                  {score.toFixed(1)}
                </span>
                {detail && <CategoryStatusBadge status={status} />}
                {detail && (
                  <span className="text-[10px] text-muted-foreground">
                    {detail.coverage}
                  </span>
                )}
              </div>
            );
          })}
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

      {/* Source data links */}
      <div className="pt-1 flex flex-wrap items-center gap-2">
        {stock.dartUrl ? (
          <a href={stock.dartUrl} target="_blank" rel="noopener noreferrer"
             className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted transition-colors">
            <FileText className="h-3.5 w-3.5" />
            View source report on DART
            {stock.dartFilingDate && (
              <span className="text-muted-foreground">· filed {stock.dartFilingDate}</span>
            )}
            <ExternalLink className="h-3 w-3 opacity-60" />
          </a>
        ) : (
          <span className="text-xs text-muted-foreground">No DART filing on record for this ticker.</span>
        )}
        <a href={`https://finance.naver.com/item/main.naver?code=${stock.ticker}`}
           target="_blank" rel="noopener noreferrer"
           className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted transition-colors">
          Naver Finance
          <ExternalLink className="h-3 w-3 opacity-60" />
        </a>
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
  usdKrwRate?: number | null;
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
  const [showInsufficient, setShowInsufficient] = useState(false);

  const factorDefs = useMemo(() => getFactorDefinitions(), []);

  useEffect(() => {
    const source = getClientDataSource();
    setDataSource(source);

    async function loadResults() {
      try {
        if (source === "db") {
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

          const sys = getSystemById(id) ?? (id === "default" ? DEFAULT_RANKING_SYSTEM : null);
          setSystem(sys);
          setResult(data as DbRankingResult);
        } else {
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

  // Split into main and insufficient lists; main = passes minimum coverage
  const { mainRankings, insufficientRankings } = useMemo(() => {
    if (!result) return { mainRankings: [], insufficientRankings: [] };
    const main: StockRanking[] = [];
    const insuff: StockRanking[] = [];
    for (const s of result.rankings) {
      if (s.coverageStatus === "insufficient" || s.passesMinimum === false) {
        insuff.push(s);
      } else {
        main.push(s);
      }
    }
    return { mainRankings: main, insufficientRankings: insuff };
  }, [result]);

  const sortedRankings = useMemo(() => {
    const list = showInsufficient ? insufficientRankings : mainRankings;
    const sorted = [...list];

    sorted.sort((a, b) => {
      let aVal: number, bVal: number;

      switch (sortField) {
        case "rank":
          aVal = a.rank ?? 9999; bVal = b.rank ?? 9999; break;
        case "composite":
          aVal = a.compositeScore; bVal = b.compositeScore; break;
        case "marketCap":
          aVal = a.marketCap; bVal = b.marketCap; break;
        case "coverage":
          aVal = a.activeWeightCoverage ?? 0; bVal = b.activeWeightCoverage ?? 0; break;
        default:
          aVal = a.categoryScores[sortField] ?? 0;
          bVal = b.categoryScores[sortField] ?? 0;
      }

      return sortDir === "asc" ? aVal - bVal : bVal - aVal;
    });

    return sorted;
  }, [mainRankings, insufficientRankings, showInsufficient, sortField, sortDir]);

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

  const categoryNames = system?.tree.children?.map(c => c.name)
    ?? (result.rankings.length > 0
      ? Object.keys(result.rankings[0].categoryScores)
      : []);

  const dbResult = result as DbRankingResult;

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
            {dbResult.scoringMethod && (
              <Badge variant="outline" className="text-xs">
                {dbResult.scoringMethod}
              </Badge>
            )}
          </div>
          <p className="text-muted-foreground text-sm mt-0.5">
            {result.rankings.length} stocks{" "}
            {dbResult.universeName && <>from universe <span className="font-mono">{dbResult.universeName}</span></>}
            {" | "}As of: {result.date}
            {" | "}Computed: {new Date(result.computedAt).toLocaleString()}
          </p>
        </div>
      </div>

      {/* Snapshot config metadata */}
      {dataSource === "db" && (dbResult.missingCategoryPolicy || (dbResult.globallyUnavailableCategories && dbResult.globallyUnavailableCategories.length > 0)) && (
        <Card>
          <CardContent className="pt-4 pb-3 space-y-3">
            <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
              <div>
                <span className="text-muted-foreground">Snapshot ID: </span>
                <span className="font-mono font-medium">{dbResult.snapshotId ?? "—"}</span>
              </div>
              <div>
                <span className="text-muted-foreground">As-of date: </span>
                <span className="font-mono font-medium">{result.date}</span>
              </div>
              {dbResult.universeName && (
                <div>
                  <span className="text-muted-foreground">Universe: </span>
                  <span className="font-mono font-medium">{dbResult.universeName}</span>
                </div>
              )}
              {dbResult.missingCategoryPolicy && (
                <div>
                  <span className="text-muted-foreground">Missing-category policy: </span>
                  <span className="font-mono font-medium">{dbResult.missingCategoryPolicy}</span>
                </div>
              )}
              {dbResult.thresholds && (
                <div>
                  <span className="text-muted-foreground">Thresholds: </span>
                  <span className="font-mono text-xs">
                    weight≥{((dbResult.thresholds.min_active_weight_coverage ?? 0) * 100).toFixed(0)}%, cats≥{dbResult.thresholds.min_category_count}, factors≥{dbResult.thresholds.min_factor_count}
                  </span>
                </div>
              )}
            </div>
            {dbResult.globallyUnavailableCategories && dbResult.globallyUnavailableCategories.length > 0 && (
              <div className="text-xs flex flex-wrap items-center gap-2">
                <span className="text-amber-700 dark:text-amber-400">
                  Globally unavailable (excluded from composite):
                </span>
                {dbResult.globallyUnavailableCategories.map(cat => (
                  <Badge key={cat} variant="outline" className="border-amber-500/50 text-amber-700">
                    {cat}
                  </Badge>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

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
            <p className="text-xs text-muted-foreground">Passed Coverage</p>
            <p className="text-xl font-bold text-green-600">
              {dbResult.passedCount ?? mainRankings.length}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Insufficient Coverage</p>
            <p className="text-xl font-bold text-amber-600">
              {dbResult.insufficientCount ?? insufficientRankings.length}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Top Score</p>
            <p className="text-xl font-bold text-green-600">
              {mainRankings[0]?.compositeScore?.toFixed(1) ?? "N/A"}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Tab toggle: main vs insufficient */}
      <div className="flex items-center gap-2 border-b">
        <button
          onClick={() => setShowInsufficient(false)}
          className={cn(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
            !showInsufficient
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          )}
        >
          Main Ranking ({mainRankings.length})
        </button>
        <button
          onClick={() => setShowInsufficient(true)}
          className={cn(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
            showInsufficient
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          )}
        >
          Insufficient Coverage ({insufficientRankings.length})
        </button>
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
                  <th className="px-4 py-3 text-right hidden md:table-cell">
                    <SortHeader field="coverage" label="Coverage" className="justify-end" />
                  </th>
                  <th className="px-4 py-3 text-center hidden lg:table-cell">
                    <span className="text-xs font-medium text-muted-foreground">Status</span>
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
                        <span className="font-mono text-xs text-muted-foreground">
                          {stock.rank ?? "—"}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div>
                          <p className="font-medium text-sm">{displayName(stock)}</p>
                          <p className="text-xs text-muted-foreground">{stock.ticker}</p>
                        </div>
                      </td>
                      <td className="px-4 py-3 hidden md:table-cell">
                        <Badge variant="outline" className="text-xs">{stock.market}</Badge>
                      </td>
                      <td className="px-4 py-3 hidden lg:table-cell text-xs text-muted-foreground">
                        {translateIndustry(stock.sector) || "-"}
                      </td>
                      <td className="px-4 py-3 text-right text-xs">
                        {formatKRW(stock.marketCap)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className={cn("inline-flex items-center px-2 py-0.5 rounded", scoreBg(stock.compositeScore))}>
                          <ScoreCell score={stock.compositeScore} />
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right hidden md:table-cell font-mono text-xs">
                        {stock.activeWeightCoverage !== undefined
                          ? `${(stock.activeWeightCoverage * 100).toFixed(0)}%`
                          : "—"}
                        {stock.imputedCategories && stock.imputedCategories.length > 0 && (
                          <span className="text-blue-500 ml-1" title={`Imputed: ${stock.imputedCategories.join(", ")}`}>
                            *
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-center hidden lg:table-cell">
                        <CoverageBadge stock={stock} />
                      </td>
                      {categoryNames.map(cat => {
                        const score = stock.categoryScores[cat];
                        const detail = stock.categoryDetails?.[cat];
                        return (
                          <td key={cat} className="px-4 py-3 text-right hidden xl:table-cell">
                            {score !== undefined ? (
                              <div className="flex items-center justify-end gap-1">
                                <ScoreCell score={score} />
                                {detail && detail.status !== "available" && (
                                  <span
                                    className="text-[9px] text-muted-foreground"
                                    title={detail.status}
                                  >
                                    {detail.status === "missing_imputed" ? "i" : detail.status === "globally_unavailable" ? "g" : "—"}
                                  </span>
                                )}
                              </div>
                            ) : (
                              <span className="text-muted-foreground text-xs">—</span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                    {expandedRows.has(stock.ticker) && (
                      <tr>
                        <td colSpan={8 + categoryNames.length}>
                          <StockDetailRow stock={stock} factorDefs={factorDefs} usdKrwRate={dbResult.usdKrwRate ?? USD_KRW_RATE} />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
          {sortedRankings.length === 0 && (
            <div className="px-4 py-8 text-center text-muted-foreground text-sm">
              {showInsufficient ? "No stocks failed coverage requirements." : "No stocks in the main ranking."}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Helper text */}
      {!showInsufficient && mainRankings.some(s => s.imputedCategories && s.imputedCategories.length > 0) && (
        <p className="text-xs text-muted-foreground">
          <span className="text-blue-500 font-mono">*</span> indicates stock had one or more
          missing categories that were neutral-imputed (50). Click a row for details.
        </p>
      )}
      {!showInsufficient && dbResult.globallyUnavailableCategories && dbResult.globallyUnavailableCategories.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Categories <span className="font-mono">{dbResult.globallyUnavailableCategories.join(", ")}</span>{" "}
          have no data anywhere in the universe; their weight is excluded from the composite for all stocks.
        </p>
      )}
      {dataSource === "db" && (
        <p className="text-xs text-muted-foreground">
          Note: market cap shown is from a current FDR snapshot, not point-in-time {result.date} historical data.
          See <code>TODO_POINT_IN_TIME_MARCAP.md</code>.
        </p>
      )}
    </div>
  );
}
