"use client";

import React, { useMemo, useState } from "react";
import { Search, ChevronDown, ChevronRight, ExternalLink, FileText } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn, formatKRW, formatUSD, formatNumber, scoreColor } from "@/lib/utils";
import { getFactorDefinitions } from "@/lib/factors";

interface FactorScore {
  factorId: string;
  rawValue: number | null;
  percentileRank: number;
}

interface FinancialPeriod {
  periodEnd: string;
  statementType: string;
  revenue: number | null;
  operatingIncome: number | null;
  netIncome: number | null;
  operatingCashFlow: number | null;
  freeCashFlow: number | null;
  totalDebt: number | null;
  totalEquity: number | null;
  eps: number | null;
  sharesOutstanding: number | null;
}

export interface CategoryDetail {
  score: number | null;
  weight: number;
  coverage: string | number;   // e.g. "10/11" (factors used / total)
  status: string;
}

export interface RankingRow {
  rank: number;
  ticker: string;
  name: string;
  market: string;
  sector: string | null;
  marketCap: number | null;
  medianTurnover: number | null;   // median daily trading value (KRW), 20d
  composite: number;
  categories: Record<string, number | null>;
  categoryDetails: Record<string, CategoryDetail>;
  activeCategories: string[];
  imputedCategories: string[];
  activeCategoryCount: number | null;
  passesMinimum?: boolean;
  failureReasons: string[];
  coverage: number;       // active_weight_coverage 0-1
  factorCount: number;
  status: "passed" | "insufficient" | "non_pit_market_cap";
  dartUrl: string | null;
  dartFilingDate: string | null;
  correctedAt: string | null;      // date a corrected filing was applied (recent)
  correctedPeriod: string | null;  // which period was restated
}

interface Props {
  rows: RankingRow[];
  categoryOrder: string[];
  asOfDate: string;
  universe: string;
  usdKrwRate: number;
}

function scoreBadgeClasses(score: number | null): string {
  if (score === null || score === undefined) return "bg-muted text-muted-foreground";
  if (score >= 80) return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
  if (score >= 60) return "bg-lime-500/15 text-lime-700 dark:text-lime-300";
  if (score >= 40) return "bg-amber-500/15 text-amber-700 dark:text-amber-300";
  if (score >= 20) return "bg-orange-500/15 text-orange-700 dark:text-orange-300";
  return "bg-rose-500/15 text-rose-700 dark:text-rose-300";
}

function statusLabel(status: string): string | null {
  switch (status) {
    case "available": return null;
    case "missing_imputed": return "imputed";
    case "missing": return "missing";
    case "missing_renormalized": return "renormalized";
    case "globally_unavailable": return "globally N/A";
    default: return status;
  }
}

const FIN_ROWS: { key: keyof FinancialPeriod; label: string; kind: "krw" | "eps" | "shares" }[] = [
  { key: "revenue", label: "Revenue", kind: "krw" },
  { key: "operatingIncome", label: "Operating income", kind: "krw" },
  { key: "netIncome", label: "Net income", kind: "krw" },
  { key: "operatingCashFlow", label: "Operating cash flow", kind: "krw" },
  { key: "freeCashFlow", label: "Free cash flow", kind: "krw" },
  { key: "totalDebt", label: "Total debt", kind: "krw" },
  { key: "totalEquity", label: "Total equity", kind: "krw" },
  { key: "eps", label: "EPS", kind: "eps" },
  { key: "sharesOutstanding", label: "Shares out.", kind: "shares" },
];

function finCell(v: number | null, kind: "krw" | "eps" | "shares"): string {
  if (v === null || v === undefined) return "—";
  if (kind === "krw") return formatKRW(v);
  if (kind === "shares") return v.toLocaleString("en-US");
  return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function RankingDetail({ row, categoryOrder, factorData, factorDefs, financials, usdKrwRate }: {
  row: RankingRow;
  categoryOrder: string[];
  factorData: FactorScore[] | "loading" | undefined;
  factorDefs: ReturnType<typeof getFactorDefinitions>;
  financials: FinancialPeriod[] | "loading" | undefined;
  usdKrwRate: number;
}) {
  const finPeriods = Array.isArray(financials) ? financials.slice(0, 5) : [];
  return (
    <div className="bg-muted/30 px-4 py-3 border-t space-y-3">
      {/* Identity */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <p className="text-xs text-muted-foreground">Ticker</p>
          <p className="text-sm font-mono">{row.ticker}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Market</p>
          <p className="text-sm">{row.market}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Sector</p>
          <p className="text-sm">{row.sector || "N/A"}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Market Cap</p>
          <p className="text-sm">{row.marketCap ? formatKRW(row.marketCap) : "—"}</p>
        </div>
      </div>

      {/* Corrected-filing notice */}
      {row.correctedAt && (
        <div className="rounded border border-amber-500/40 bg-amber-50 dark:bg-amber-900/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
          A corrected (정정) filing was applied on <span className="font-medium">{row.correctedAt}</span>
          {row.correctedPeriod ? <> for period <span className="font-medium">{row.correctedPeriod}</span></> : null}.
          The numbers below were refreshed from the restated report — open the DART link to see the latest filing.
        </div>
      )}

      {/* Liquidity */}
      <div className="rounded border bg-card px-3 py-2">
        <p className="text-xs text-muted-foreground">Median daily trading value (20d) — liquidity</p>
        <p className="text-sm font-mono font-semibold">
          {row.medianTurnover
            ? `${formatKRW(row.medianTurnover)}  (≈ ${formatUSD(row.medianTurnover, usdKrwRate)})`
            : "—"}
        </p>
      </div>

      {/* Coverage summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-2 border-t">
        <div>
          <p className="text-xs text-muted-foreground">Active Weight</p>
          <p className="text-sm font-mono">{(row.coverage * 100).toFixed(0)}%</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Active Categories</p>
          <p className="text-sm font-mono">
            {row.activeCategoryCount ?? "—"}
            {row.activeCategories.length > 0 && (
              <span className="text-xs text-muted-foreground ml-1">
                ({row.activeCategories.join(", ")})
              </span>
            )}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Factors Available</p>
          <p className="text-sm font-mono">{row.factorCount}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Status</p>
          <p className="text-sm">
            {row.status === "passed"
              ? "Passed coverage"
              : row.status === "non_pit_market_cap"
                ? "Non-PIT market cap"
                : "Insufficient coverage"}
          </p>
          {row.imputedCategories.length > 0 && (
            <p className="text-xs text-muted-foreground mt-0.5">
              imputed: {row.imputedCategories.join(", ")}
            </p>
          )}
        </div>
      </div>

      {/* Failure reasons */}
      {row.failureReasons.length > 0 && (
        <div className="rounded border border-amber-500/30 bg-amber-50 dark:bg-amber-900/10 px-3 py-2">
          <p className="text-xs font-medium text-amber-800 dark:text-amber-400 mb-1">
            Coverage notes:
          </p>
          <ul className="text-xs text-amber-700 dark:text-amber-300 list-disc ml-4 space-y-0.5">
            {row.failureReasons.map((reason, i) => <li key={i}>{reason}</li>)}
          </ul>
        </div>
      )}

      {/* Category breakdown */}
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-2">Category Scores</p>
        <div className="flex flex-wrap gap-2">
          {categoryOrder.map(name => {
            const detail = row.categoryDetails[name];
            const score = detail?.score ?? row.categories[name] ?? null;
            const sLabel = detail ? statusLabel(detail.status) : null;
            return (
              <div
                key={name}
                className={cn("px-3 py-1.5 rounded-md text-xs flex items-center gap-1.5",
                  scoreBadgeClasses(score))}
              >
                <span className="opacity-70">{name}:</span>
                <span className="font-medium">{score !== null ? score.toFixed(1) : "—"}</span>
                {sLabel && <span className="text-[9px] opacity-70">({sLabel})</span>}
                {detail && (
                  <span className="text-[10px] opacity-70">{detail.coverage}</span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Source financials (loaded on demand) */}
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-2">
          Source financials <span className="font-normal">— stored values behind the factors (consolidated). Cross-check against the DART report below.</span>
        </p>
        {financials === undefined || financials === "loading" ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : finPeriods.length === 0 ? (
          <p className="text-xs text-muted-foreground">No stored financials for this stock.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="text-xs border rounded">
              <thead>
                <tr className="bg-card">
                  <th className="px-2 py-1 text-left font-medium text-muted-foreground">Metric</th>
                  {finPeriods.map((p, i) => (
                    <th key={i} className="px-2 py-1 text-right font-medium whitespace-nowrap">
                      {p.periodEnd}<span className="text-muted-foreground"> · {p.statementType}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {FIN_ROWS.map(fr => (
                  <tr key={fr.key} className="border-t">
                    <td className="px-2 py-1 text-muted-foreground whitespace-nowrap">{fr.label}</td>
                    {finPeriods.map((p, i) => (
                      <td key={i} className="px-2 py-1 text-right font-mono whitespace-nowrap">
                        {finCell(p[fr.key] as number | null, fr.kind)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Factor details (loaded on demand) */}
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-2">
          Factor Details <span className="font-normal">— each factor&apos;s formula (computed from the source financials above), this stock&apos;s value, and its percentile rank vs peers (0–100, higher = better).</span>
        </p>
        {factorData === undefined || factorData === "loading" ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : factorData.length === 0 ? (
          <p className="text-xs text-muted-foreground">No factor detail available for this stock.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
            {factorData.map(d => {
              const def = factorDefs.find(f => f.id === d.factorId);
              return (
                <div key={d.factorId}
                     className="px-3 py-1.5 rounded border bg-card text-xs">
                  <div className="flex items-start justify-between gap-2">
                    <p className="font-medium">{def?.name ?? d.factorId}</p>
                    <div className={cn("font-mono font-medium shrink-0", scoreColor(d.percentileRank))}>
                      {d.percentileRank.toFixed(1)}
                    </div>
                  </div>
                  {def?.description && (
                    <p className="text-muted-foreground mt-0.5">{def.description}</p>
                  )}
                  <p className="text-muted-foreground mt-0.5">
                    Value from this stock&apos;s data: {d.rawValue !== null ? formatNumber(d.rawValue, 4) : "N/A"}
                  </p>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Source filing link */}
      <div className="pt-1 flex flex-wrap items-center gap-2">
        {row.dartUrl ? (
          <a href={row.dartUrl} target="_blank" rel="noopener noreferrer"
             className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted transition-colors">
            <FileText className="h-3.5 w-3.5" />
            View source report on DART
            {row.dartFilingDate && (
              <span className="text-muted-foreground">· filed {row.dartFilingDate}</span>
            )}
            <ExternalLink className="h-3 w-3 opacity-60" />
          </a>
        ) : (
          <span className="text-xs text-muted-foreground">No DART filing on record for this ticker.</span>
        )}
        <a href={`https://finance.naver.com/item/main.naver?code=${row.ticker}`}
           target="_blank" rel="noopener noreferrer"
           className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted transition-colors">
          Naver Finance
          <ExternalLink className="h-3 w-3 opacity-60" />
        </a>
      </div>
    </div>
  );
}

export default function RankingClient({ rows, categoryOrder, asOfDate, universe, usdKrwRate }: Props) {
  const [search, setSearch] = useState("");
  const [marketFilter, setMarketFilter] = useState<string>("ALL");
  const [statusFilter, setStatusFilter] = useState<string>("passed");
  const [sectorFilter, setSectorFilter] = useState<string>("ALL");
  const [liquidityFilter, setLiquidityFilter] = useState<string>("ALL");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [factorCache, setFactorCache] = useState<Record<string, FactorScore[] | "loading">>({});
  const [finCache, setFinCache] = useState<Record<string, FinancialPeriod[] | "loading">>({});

  const factorDefs = useMemo(() => getFactorDefinitions(), []);

  const loadFactors = (ticker: string) => {
    if (factorCache[ticker]) return;   // already loading or loaded
    setFactorCache(prev => ({ ...prev, [ticker]: "loading" }));
    fetch(`/api/stocks/${ticker}/factors?date=${asOfDate}&universe=${encodeURIComponent(universe)}`)
      .then(res => res.json())
      .then((data: { factors?: FactorScore[] }) => {
        setFactorCache(prev => ({ ...prev, [ticker]: data.factors ?? [] }));
      })
      .catch(() => {
        setFactorCache(prev => ({ ...prev, [ticker]: [] }));
      });
  };

  const loadFinancials = (ticker: string) => {
    if (finCache[ticker]) return;
    setFinCache(prev => ({ ...prev, [ticker]: "loading" }));
    fetch(`/api/stocks/${ticker}/financials`)
      .then(res => res.json())
      .then((data: { periods?: FinancialPeriod[] }) => {
        setFinCache(prev => ({ ...prev, [ticker]: data.periods ?? [] }));
      })
      .catch(() => {
        setFinCache(prev => ({ ...prev, [ticker]: [] }));
      });
  };

  const toggleRow = (ticker: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(ticker)) {
        next.delete(ticker);
      } else {
        next.add(ticker);
        loadFactors(ticker);
        loadFinancials(ticker);
      }
      return next;
    });
  };

  const sectors = useMemo(() => {
    const s = new Set(rows.map(r => r.sector).filter(Boolean));
    return Array.from(s).sort() as string[];
  }, [rows]);

  const filtered = useMemo(() => {
    const minLiquidity = liquidityFilter === "ALL" ? 0 : Number(liquidityFilter);
    return rows.filter(r => {
      if (statusFilter !== "ALL" && r.status !== statusFilter) return false;
      if (marketFilter !== "ALL" && r.market !== marketFilter) return false;
      if (sectorFilter !== "ALL" && r.sector !== sectorFilter) return false;
      if (minLiquidity > 0) {
        // Hide names below the liquidity floor — and those with no turnover
        // data, since we can't confirm they're tradable.
        if (r.medianTurnover === null || r.medianTurnover < minLiquidity) return false;
      }
      if (search) {
        const q = search.toLowerCase();
        if (
          !r.ticker.toLowerCase().includes(q) &&
          !r.name.toLowerCase().includes(q)
        ) return false;
      }
      return true;
    });
  }, [rows, search, marketFilter, statusFilter, sectorFilter, liquidityFilter]);

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
        <Select value={liquidityFilter} onValueChange={setLiquidityFilter}>
          <SelectTrigger className="w-48">
            <SelectValue placeholder="Min liquidity" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">Any liquidity</SelectItem>
            <SelectItem value="100000000">≥ ₩1억 / day</SelectItem>
            <SelectItem value="500000000">≥ ₩5억 / day</SelectItem>
            <SelectItem value="1000000000">≥ ₩10억 / day</SelectItem>
            <SelectItem value="5000000000">≥ ₩50억 / day</SelectItem>
            <SelectItem value="10000000000">≥ ₩100억 / day</SelectItem>
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
                  <th className="px-3 py-3 text-right text-xs font-medium text-muted-foreground" title="Median daily trading value over the last 20 trading days">Liq 20d</th>
                  <th className="px-3 py-3 text-right text-xs font-medium text-muted-foreground">Composite</th>
                  {categoryOrder.map(c => (
                    <th key={c} className="px-2 py-3 text-right text-xs font-medium text-muted-foreground">{c}</th>
                  ))}
                  <th className="px-2 py-3 text-right text-xs font-medium text-muted-foreground">Cov</th>
                </tr>
              </thead>
              <tbody>
                {filtered.slice(0, 500).map((r) => {
                  const isOpen = expanded.has(r.ticker);
                  return (
                  <React.Fragment key={r.ticker}>
                  <tr
                    className="border-b hover:bg-muted/30 transition-colors cursor-pointer"
                    onClick={() => toggleRow(r.ticker)}
                  >
                    <td className="px-3 py-2 text-right font-mono text-xs text-muted-foreground">{r.rank}</td>
                    <td className="px-3 py-2">
                      <span className="inline-flex items-center gap-1 font-mono text-primary">
                        {isOpen
                          ? <ChevronDown className="h-3.5 w-3.5" />
                          : <ChevronRight className="h-3.5 w-3.5" />}
                        {r.ticker}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{r.name}</span>
                        <Badge variant="outline" className="text-[10px]">{r.market}</Badge>
                        {r.correctedAt && (
                          <Badge
                            variant="outline"
                            className="text-[10px] border-amber-500/50 text-amber-700 dark:text-amber-400"
                            title={`A corrected filing was applied on ${r.correctedAt}${r.correctedPeriod ? ` (period ${r.correctedPeriod})` : ""}. Numbers were refreshed from the restated report.`}
                          >
                            corrected
                          </Badge>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground whitespace-nowrap" title={r.sector || undefined}>{r.sector || "-"}</td>
                    <td className="px-3 py-2 text-right text-xs">{r.marketCap ? formatKRW(r.marketCap) : "-"}</td>
                    <td className="px-3 py-2 text-right text-xs">
                      {r.medianTurnover ? (
                        <div className="leading-tight">
                          <div>{formatKRW(r.medianTurnover)}</div>
                          <div className="text-[10px] text-muted-foreground">≈ {formatUSD(r.medianTurnover, usdKrwRate)}</div>
                        </div>
                      ) : "-"}
                    </td>
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
                  {isOpen && (
                    <tr>
                      <td colSpan={8 + categoryOrder.length} className="p-0">
                        <RankingDetail
                          row={r}
                          categoryOrder={categoryOrder}
                          factorData={factorCache[r.ticker]}
                          factorDefs={factorDefs}
                          financials={finCache[r.ticker]}
                          usdKrwRate={usdKrwRate}
                        />
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                  );
                })}
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
