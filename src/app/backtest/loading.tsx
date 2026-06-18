import { Card, CardContent, CardHeader } from "@/components/ui/card";

// Shown instantly by Next.js while the server component fetches the (large)
// backtest payload, so the route never appears to hang on a blank screen.
export default function BacktestLoading() {
  return (
    <div className="space-y-4 animate-pulse">
      <div>
        <div className="h-7 w-72 rounded bg-muted" />
        <div className="mt-2 h-4 w-96 max-w-full rounded bg-muted/70" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-4">
        {/* Left rail: settings/weights skeleton */}
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <div className="h-5 w-24 rounded bg-muted" />
            </CardHeader>
            <CardContent className="space-y-4">
              {Array.from({ length: 7 }).map((_, i) => (
                <div key={i} className="space-y-2">
                  <div className="h-3 w-20 rounded bg-muted/70" />
                  <div className="h-2 w-full rounded bg-muted" />
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        {/* Right: chart + stats skeleton */}
        <div className="space-y-4">
          <Card>
            <CardContent className="p-4">
              <div className="h-[320px] w-full rounded bg-muted/60" />
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4 space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="h-4 w-full rounded bg-muted/50" />
              ))}
            </CardContent>
          </Card>
        </div>
      </div>

      <p className="text-center text-sm text-muted-foreground">
        Loading backtest data…
      </p>
    </div>
  );
}
