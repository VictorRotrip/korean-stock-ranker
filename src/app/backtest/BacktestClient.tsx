"use client";

import { useMemo, useState, useTransition } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
  ReferenceLine,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { RotateCcw, TrendingUp, TrendingDown } from "lucide-react";
import { cn, formatPercent } from "@/lib/utils";
import { runBacktest, type BacktestPayload } from "@/lib/backtest";

interface Props {
  payload: BacktestPayload;
  defaultWeights: Record<string, number>;
}

// Bucket colours — green at top, red at bottom, gentle interpolation.
function bucketColor(b: number, n: number, isMarket = false): string {
  if (isMarket) return "#6b7280"; // slate-500
  // Top = emerald, bottom = rose
  const t = (b - 1) / Math.max(1, n - 1);  // 0 = top, 1 = bottom
  // Linearly interp between two HSL endpoints (emerald 150° → red 0°)
  const hue = 150 - 150 * t;
  const sat = 65;
  const light = 45;
  return `hsl(${hue}, ${sat}%, ${light}%)`;
}

export default function BacktestClient({ payload, defaultWeights }: Props) {
  const [weights, setWeights] = useState<Record<string, number>>(defaultWeights);
  const [nBuckets, setNBuckets] = useState<number>(10);
  const [universeFilter, setUniverseFilter] = useState<"passed" | "all">("passed");
  const [logScale, setLogScale] = useState<boolean>(false);
  const [showAllBuckets, setShowAllBuckets] = useState<boolean>(true);
  const [, startTransition] = useTransition();

  const handleSlider = (cat: string, value: number) => {
    startTransition(() => {
      setWeights(w => ({ ...w, [cat]: value }));
    });
  };

  const handleReset = () => {
    setWeights({ ...defaultWeights });
  };

  // Normalize for the display "x of total" — backtest math renormalizes too,
  // so the absolute scale of the sliders is just a UX choice.
  const totalWeight = useMemo(
    () => Object.values(weights).reduce((a, b) => a + b, 0),
    [weights],
  );

  // Run the backtest. This memoizes on weights + nBuckets + filter so it
  // only recomputes when something actually changes.
  const result = useMemo(
    () => runBacktest({ payload, weights, nBuckets, universeFilter }),
    [payload, weights, nBuckets, universeFilter],
  );

  // Chart data: align cum series with dates.
  const cumChartData = useMemo(() => {
    const data: Array<Record<string, number | string>> = [];
    for (let i = 0; i < result.dates.length; i++) {
      const row: Record<string, number | string> = { date: result.dates[i] };
      for (let b = 0; b < nBuckets; b++) {
        row[`b${b + 1}`] = result.cumByBucket[b][i] ?? 1;
      }
      row.market = result.cumMarket[i] ?? 1;
      data.push(row);
    }
    return data;
  }, [result, nBuckets]);

  // Bar chart: annualized CAGR per bucket.
  const cagrBarData = useMemo(() => {
    return result.stats.map((s, i) => ({
      label: s.label,
      bucket: i + 1,
      cagr: s.cagr * 100,
    }));
  }, [result]);

  // The visible bucket lines on the cumulative chart.
  // When showAllBuckets is off, we only show D1 and Dn (top vs bottom).
  const visibleBuckets = useMemo(() => {
    if (showAllBuckets) {
      return Array.from({ length: nBuckets }, (_, i) => i + 1);
    }
    return [1, nBuckets];
  }, [showAllBuckets, nBuckets]);

  // Top-minus-bottom spread per period for the spread chart.
  const spreadChartData = useMemo(() => {
    let cum = 1;
    return result.periods.map((p) => {
      const top = p.bucketReturns[0];
      const bot = p.bucketReturns[nBuckets - 1];
      cum = cum * (1 + (top - bot));
      return { date: p.date, period: (top - bot) * 100, cum };
    });
  }, [result, nBuckets]);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-4">
      {/* ---------------- Left rail: controls ---------------- */}
      <div className="space-y-4">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Settings</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Buckets</label>
              <Select value={String(nBuckets)} onValueChange={(v) => setNBuckets(parseInt(v))}>
                <SelectTrigger className="mt-1 h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="5">Quintiles (5)</SelectItem>
                  <SelectItem value="10">Deciles (10)</SelectItem>
                  <SelectItem value="20">Vigintiles (20)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Universe filter</label>
              <Select value={universeFilter} onValueChange={(v) => setUniverseFilter(v as "passed" | "all")}>
                <SelectTrigger className="mt-1 h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="passed">Passed coverage gates</SelectItem>
                  <SelectItem value="all">All ranked stocks</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center justify-between pt-1">
              <label htmlFor="log-scale" className="text-xs font-medium text-muted-foreground">
                Log scale
              </label>
              <Switch id="log-scale" checked={logScale} onCheckedChange={setLogScale} />
            </div>
            <div className="flex items-center justify-between">
              <label htmlFor="show-all" className="text-xs font-medium text-muted-foreground">
                Show all buckets
              </label>
              <Switch id="show-all" checked={showAllBuckets} onCheckedChange={setShowAllBuckets} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3 flex flex-row items-center justify-between">
            <CardTitle className="text-base">Weights</CardTitle>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={handleReset} title="Reset to defaults">
              <RotateCcw className="h-3.5 w-3.5" />
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {payload.categories.map((cat) => {
              const v = weights[cat] ?? 0;
              const pct = totalWeight > 0 ? (v / totalWeight) * 100 : 0;
              return (
                <div key={cat}>
                  <div className="flex items-baseline justify-between mb-1.5">
                    <span className="text-sm font-medium">{cat}</span>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {v}
                      <span className="ml-1 text-[10px]">({pct.toFixed(0)}%)</span>
                    </span>
                  </div>
                  <Slider
                    value={[v]}
                    onValueChange={(vals) => handleSlider(cat, vals[0])}
                    min={0}
                    max={50}
                    step={1}
                  />
                </div>
              );
            })}
            <div className="pt-2 border-t text-xs text-muted-foreground">
              Total slider points: <span className="tabular-nums font-mono">{totalWeight}</span>
              <p className="mt-1 text-[11px] leading-snug">
                Weights are renormalized per ticker: a stock missing a category just doesn&apos;t get that category counted (no zero-fill).
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ---------------- Main: charts + stats ---------------- */}
      <div className="space-y-4">
        {/* Cumulative chart */}
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-baseline justify-between flex-wrap gap-2">
              <div>
                <CardTitle className="text-base">Cumulative return by bucket</CardTitle>
                <CardDescription className="text-xs">
                  Equal-weight, monthly rebalance, growth of $1
                </CardDescription>
              </div>
              <div className="text-xs text-muted-foreground tabular-nums">
                {result.periods.length} rebalances · {result.periods.reduce((s, p) => s + p.nTickers, 0).toLocaleString()} total positions
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {result.periods.length === 0 ? (
              <p className="text-sm text-muted-foreground py-8 text-center">
                No periods with both ranking and forward-return data. Adjust weights or universe filter.
              </p>
            ) : (
              <div className="h-[380px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={cumChartData} margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 11 }}
                      interval="preserveStartEnd"
                      minTickGap={40}
                    />
                    <YAxis
                      tick={{ fontSize: 11 }}
                      scale={logScale ? "log" : "auto"}
                      domain={logScale ? [0.5, "auto"] : ["auto", "auto"]}
                      tickFormatter={(v) => (v as number).toFixed(2)}
                    />
                    <Tooltip
                      contentStyle={{ fontSize: 12, borderRadius: 6 }}
                      formatter={(value: number) => value.toFixed(3)}
                    />
                    <Legend wrapperStyle={{ fontSize: 11 }} iconType="line" />
                    <ReferenceLine y={1} stroke="#94a3b8" strokeDasharray="2 4" />
                    {visibleBuckets.map((b) => (
                      <Line
                        key={`b${b}`}
                        type="monotone"
                        dataKey={`b${b}`}
                        name={b === 1 ? `${result.stats[b - 1].label} (top)` : b === nBuckets ? `${result.stats[b - 1].label} (bottom)` : result.stats[b - 1].label}
                        stroke={bucketColor(b, nBuckets)}
                        strokeWidth={b === 1 || b === nBuckets ? 2.2 : 1.4}
                        dot={false}
                        isAnimationActive={false}
                      />
                    ))}
                    <Line
                      type="monotone"
                      dataKey="market"
                      name="Universe avg"
                      stroke={bucketColor(0, nBuckets, true)}
                      strokeWidth={1.4}
                      strokeDasharray="4 3"
                      dot={false}
                      isAnimationActive={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        {/* CAGR-by-bucket bar chart */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Annualized return by bucket</CardTitle>
              <CardDescription className="text-xs">
                Monotonic = the ranking is sorting; flat or inverted = it isn&apos;t.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-[240px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={cagrBarData} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                    <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                    <YAxis
                      tick={{ fontSize: 11 }}
                      tickFormatter={(v) => `${(v as number).toFixed(0)}%`}
                    />
                    <Tooltip
                      contentStyle={{ fontSize: 12, borderRadius: 6 }}
                      formatter={(value: number) => `${value.toFixed(2)}%`}
                    />
                    <ReferenceLine y={0} stroke="#94a3b8" />
                    <ReferenceLine
                      y={result.marketStat.cagr * 100}
                      stroke="#94a3b8"
                      strokeDasharray="4 3"
                      label={{ value: "Univ", fontSize: 10, fill: "#64748b", position: "insideTopRight" }}
                    />
                    <Bar dataKey="cagr">
                      {cagrBarData.map((d) => (
                        <Cell key={d.bucket} fill={bucketColor(d.bucket, nBuckets)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>

          {/* Top-minus-bottom spread */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Top − Bottom spread</CardTitle>
              <CardDescription className="text-xs">
                Cumulative spread of best bucket minus worst bucket (long top, short bottom).
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-[240px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={spreadChartData} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" minTickGap={40} />
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => (v as number).toFixed(2)} />
                    <Tooltip
                      contentStyle={{ fontSize: 12, borderRadius: 6 }}
                      formatter={(value: number, name: string) =>
                        name === "cum" ? value.toFixed(3) : `${value.toFixed(2)}%`
                      }
                    />
                    <ReferenceLine y={1} stroke="#94a3b8" strokeDasharray="2 4" />
                    <Line
                      type="monotone"
                      dataKey="cum"
                      name="Cumulative"
                      stroke="#0ea5e9"
                      strokeWidth={2}
                      dot={false}
                      isAnimationActive={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Stats table */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Bucket statistics</CardTitle>
            <CardDescription className="text-xs">
              Monthly returns annualized (×12). Sharpe assumes rf = 0.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto -mx-6">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Bucket</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-muted-foreground">Avg / mo</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-muted-foreground">CAGR</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-muted-foreground">Total</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-muted-foreground">Vol (ann.)</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-muted-foreground">Sharpe</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-muted-foreground">Hit %</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-muted-foreground">Max DD</th>
                  </tr>
                </thead>
                <tbody>
                  {result.stats.map((s, i) => (
                    <tr
                      key={s.label}
                      className={cn(
                        "border-b hover:bg-muted/30 transition-colors",
                        (i === 0 || i === result.stats.length - 1) && "font-medium",
                      )}
                    >
                      <td className="px-3 py-2">
                        <span className="inline-flex items-center gap-2">
                          <span
                            className="inline-block h-3 w-3 rounded-sm"
                            style={{ backgroundColor: bucketColor(i + 1, nBuckets) }}
                          />
                          {s.label}
                          {i === 0 && <TrendingUp className="h-3 w-3 text-emerald-600" />}
                          {i === result.stats.length - 1 && <TrendingDown className="h-3 w-3 text-rose-600" />}
                        </span>
                      </td>
                      <td className={cn("px-3 py-2 text-right tabular-nums", s.meanRet < 0 && "text-rose-600")}>
                        {formatPercent(s.meanRet, 2)}
                      </td>
                      <td className={cn("px-3 py-2 text-right tabular-nums", s.cagr < 0 && "text-rose-600")}>
                        {formatPercent(s.cagr, 1)}
                      </td>
                      <td className={cn("px-3 py-2 text-right tabular-nums", s.cumReturn < 0 && "text-rose-600")}>
                        {formatPercent(s.cumReturn, 1)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">{formatPercent(s.vol, 1)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{s.sharpe.toFixed(2)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{(s.hitRate * 100).toFixed(0)}%</td>
                      <td className="px-3 py-2 text-right tabular-nums text-rose-600/80">
                        {formatPercent(s.maxDrawdown, 1)}
                      </td>
                    </tr>
                  ))}
                  <tr className="border-b text-muted-foreground italic">
                    <td className="px-3 py-2">{result.marketStat.label}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatPercent(result.marketStat.meanRet, 2)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatPercent(result.marketStat.cagr, 1)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatPercent(result.marketStat.cumReturn, 1)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatPercent(result.marketStat.vol, 1)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{result.marketStat.sharpe.toFixed(2)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{(result.marketStat.hitRate * 100).toFixed(0)}%</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatPercent(result.marketStat.maxDrawdown, 1)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
