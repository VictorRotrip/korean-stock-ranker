import Link from "next/link";
import { Layers, TrendingUp, List, ArrowRight } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { getStocks, getLatestPrices } from "@/lib/mock-data";
import { getFactorDefinitions, getCategories, CATEGORY_LABELS } from "@/lib/factors";
import { formatKRW } from "@/lib/utils";

export default function DashboardPage() {
  const stocks = getStocks();
  const latestPrices = getLatestPrices();
  const factors = getFactorDefinitions();
  const categories = getCategories();

  const kospiCount = stocks.filter(s => s.market === "KOSPI").length;
  const kosdaqCount = stocks.filter(s => s.market === "KOSDAQ").length;

  // Top 5 by market cap
  const topStocks = stocks
    .map(s => ({ ...s, marketCap: latestPrices.get(s.ticker)?.marketCap ?? 0 }))
    .sort((a, b) => b.marketCap - a.marketCap)
    .slice(0, 5);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
        <p className="text-muted-foreground mt-1">
          Korean equity multi-factor ranking system builder
        </p>
      </div>

      {/* Stats cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Stocks</CardTitle>
            <List className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stocks.length}</div>
            <p className="text-xs text-muted-foreground">
              KOSPI: {kospiCount} / KOSDAQ: {kosdaqCount}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Available Factors</CardTitle>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{factors.length}</div>
            <p className="text-xs text-muted-foreground">
              Across {categories.length} categories
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Factor Categories</CardTitle>
            <Layers className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-1 mt-1">
              {categories.map(cat => (
                <Badge key={cat} variant="secondary" className="text-xs">
                  {CATEGORY_LABELS[cat]}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Data Mode</CardTitle>
          </CardHeader>
          <CardContent>
            <Badge variant="outline" className="text-xs">Mock Data</Badge>
            <p className="text-xs text-muted-foreground mt-2">
              60 stocks, ~1 year of prices, 4 years of financials
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Quick actions + Top stocks */}
      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Quick Start</CardTitle>
            <CardDescription>Build and run a ranking system</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Link href="/ranking-systems">
              <Button className="w-full justify-between">
                View Ranking Systems
                <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
            <Link href="/ranking-systems/default">
              <Button variant="outline" className="w-full justify-between">
                Open Default Multi-Factor System
                <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
            <Link href="/universe">
              <Button variant="outline" className="w-full justify-between">
                Explore Stock Universe
                <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Top Stocks by Market Cap</CardTitle>
            <CardDescription>Largest companies in the universe</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {topStocks.map((stock, i) => (
                <div key={stock.ticker} className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-medium text-muted-foreground w-4">{i + 1}</span>
                    <div>
                      <p className="text-sm font-medium">{stock.name}</p>
                      <p className="text-xs text-muted-foreground">{stock.ticker} · {stock.market}</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-medium">{formatKRW(stock.marketCap)}</p>
                    <p className="text-xs text-muted-foreground">{stock.sector}</p>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
