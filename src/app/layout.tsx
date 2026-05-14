import type { Metadata } from "next";
import Link from "next/link";
import { BarChart3, Layers, List, Database, Trophy, LineChart } from "lucide-react";
import { getDataSource } from "@/lib/data-service.server";
import "./globals.css";

export const metadata: Metadata = {
  title: "Korean Stock Ranker",
  description: "Multi-factor ranking system builder for KOSPI and KOSDAQ equities",
};

const navItems = [
  { href: "/", label: "Dashboard", icon: BarChart3 },
  { href: "/ranking", label: "Today's Ranking", icon: Trophy },
  { href: "/backtest", label: "Backtest", icon: LineChart },
  { href: "/ranking-systems", label: "Ranking Systems", icon: Layers },
  { href: "/universe", label: "Universe", icon: List },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const dataSource = getDataSource();
  return (
    <html lang="ko" suppressHydrationWarning>
      <body className="min-h-screen bg-background font-sans antialiased">
        <div className="flex min-h-screen">
          {/* Sidebar */}
          <aside className="hidden md:flex w-60 flex-col border-r bg-card">
            <div className="flex h-14 items-center border-b px-4">
              <Link href="/" className="flex items-center gap-2 font-semibold">
                <BarChart3 className="h-5 w-5 text-primary" />
                <span className="text-sm">KR Stock Ranker</span>
              </Link>
            </div>
            <nav className="flex-1 space-y-1 p-3">
              {navItems.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="flex items-center gap-3 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
                >
                  <item.icon className="h-4 w-4" />
                  {item.label}
                </Link>
              ))}
            </nav>
            <div className="border-t p-3">
              <div className="flex items-center gap-2 rounded-md bg-muted px-3 py-2">
                <Database className="h-3 w-3 text-muted-foreground" />
                <span className="text-xs text-muted-foreground">
                  {dataSource === "db" ? "Live (Supabase)" : "Mock Data Mode"}
                </span>
              </div>
            </div>
          </aside>

          {/* Main content */}
          <main className="flex-1 overflow-auto">
            {/* Mobile header */}
            <header className="md:hidden flex h-14 items-center border-b px-4 gap-4">
              <Link href="/" className="flex items-center gap-2 font-semibold">
                <BarChart3 className="h-5 w-5 text-primary" />
                <span className="text-sm">KR Stock Ranker</span>
              </Link>
              <nav className="flex gap-2 ml-auto">
                {navItems.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent"
                  >
                    <item.icon className="h-3 w-3" />
                    {item.label}
                  </Link>
                ))}
              </nav>
            </header>

            <div className="p-6 max-w-[1400px] mx-auto">
              {children}
            </div>
          </main>
        </div>
      </body>
    </html>
  );
}
