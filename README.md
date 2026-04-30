# Korean Stock Ranker

A multi-factor ranking system builder for Korean equities (KOSPI & KOSDAQ), inspired by Portfolio123. Build custom factor trees, assign weights, rank stocks, and inspect factor scores.

## Quick Start

```bash
# Install dependencies
npm install

# Run development server
npm run dev

# Open http://localhost:3000
```

No environment variables are needed for the MVP — it uses mock data for 60 Korean stocks.

## Architecture

### Stack
- **Frontend**: Next.js 15 (App Router), TypeScript, Tailwind CSS, shadcn/ui
- **Database**: Drizzle ORM + Supabase Postgres (mock data for MVP)
- **Ranking Engine**: Pure TypeScript, runs client-side or server-side
- **Data Ingestion**: Python scripts (pykrx, OpenDartReader) — for later milestones
- **Deployment**: Vercel

### Project Structure
```
src/
├── app/                          # Next.js App Router pages
│   ├── page.tsx                  # Dashboard
│   ├── universe/page.tsx         # Stock universe explorer
│   ├── ranking-systems/
│   │   ├── page.tsx              # List of ranking systems
│   │   └── [id]/
│   │       ├── page.tsx          # Ranking system builder (tree editor)
│   │       └── results/page.tsx  # Ranking results table
│   ├── stocks/[ticker]/page.tsx  # Stock detail page
│   └── api/                      # API routes
├── lib/
│   ├── mock-data.ts              # Mock Korean stock data (60 stocks)
│   ├── factors.ts                # Factor library (25 factors)
│   ├── ranking-engine.ts         # Core ranking engine
│   ├── store.ts                  # localStorage persistence
│   └── utils.ts                  # Utility functions
├── db/
│   └── schema.ts                 # Drizzle ORM schema (for Supabase)
├── components/ui/                # shadcn/ui components
└── types/index.ts                # TypeScript type definitions
scripts/
└── python/                       # Data ingestion scripts (Milestone 2+)
```

## Core Concepts

### Ranking Engine Pipeline
1. **Filter Universe** — Apply market, sector, liquidity, and market cap filters
2. **Compute Factors** — Calculate raw factor values for each stock (25 built-in factors)
3. **Rank Factors** — Percentile-rank each factor across the universe (0–100)
4. **Aggregate Tree** — Walk the ranking tree bottom-up with weighted averaging
5. **Sort & Output** — Sort by composite score, assign final ranks

### Factor Library (25 Factors)
| Category | Factors |
|----------|---------|
| Value (6) | Earnings Yield, Book-to-Market, Sales Yield, Cash Flow Yield, EV/EBITDA, Dividend Yield |
| Quality (6) | ROE, ROA, Gross Profitability, Operating Margin, Debt/Equity, Interest Coverage |
| Growth (4) | Revenue Growth, EPS Growth, Operating Income Growth, FCF Growth |
| Momentum (5) | 12-1M Momentum, 6M Momentum, 3M Momentum, 1M Reversal, Distance from 52W High |
| Risk (2) | 60-Day Volatility, Turnover Ratio |
| Short Interest (2) | Short Selling Ratio, Short Balance Ratio |

### Point-in-Time Design
Financial data stores both `periodEnd` (fiscal period) and `filingDate` (when DART published it). The engine only uses data with `filingDate <= rankingDate` to avoid lookahead bias.

## Ranking Options
- **Missing Values**: Exclude, assign median, assign worst, or assign neutral (50)
- **Winsorization**: Clip extreme values at 5th/95th percentile before ranking
- **Sector-Neutral**: Rank within each sector independently
- **Z-Score**: Standardize to mean=0, std=1 (experimental)

## Milestones

| # | Milestone | Status | Description |
|---|-----------|--------|-------------|
| 1 | Mock-Data MVP | **Done** | Interactive ranking builder with 60 mock Korean stocks |
| 2 | Real Universe & Prices | Planned | pykrx / FinanceData for KOSPI+KOSDAQ stocks and daily OHLCV |
| 3 | DART Fundamentals | Planned | OpenDartReader for financial statements with point-in-time dates |
| 4 | Short Selling & Liquidity | Planned | KRX short selling data, trading value filters |
| 5 | Saved Systems & Auth | Planned | Supabase Postgres + Auth for persistent ranking systems |
| 6 | Backtesting | Planned | Historical ranking snapshots, portfolio simulation |
| 7 | Paid Data Integration | Planned | FnGuide estimates, analyst revisions, consensus data |

## Data Ingestion (Milestone 2+)

Python scripts in `scripts/python/` handle data ingestion. They push data into Supabase Postgres. Run them via GitHub Actions cron or locally.

### Required Python packages
```
pykrx
opendartreader
financedataread
psycopg2-binary
pandas
```

### Key scripts (to be implemented)
- `ingest_universe.py` — Load KOSPI/KOSDAQ stock listings
- `ingest_prices.py` — Daily OHLCV and market cap via pykrx
- `ingest_dart.py` — Financial statements from DART with filing dates
- `ingest_short_selling.py` — Short selling data from KRX

## Environment Variables

Copy `.env.example` to `.env.local`:

```env
# Not needed for Milestone 1 (mock data)
DATABASE_URL=postgresql://...
NEXT_PUBLIC_SUPABASE_URL=https://...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
NEXT_PUBLIC_DATA_SOURCE=mock  # "mock" or "db"
```

## Deploy to Vercel

```bash
# Install Vercel CLI
npm i -g vercel

# Deploy
vercel
```

The app works on Vercel with zero configuration for the mock-data MVP. When connecting Supabase, add environment variables in the Vercel dashboard.

## License

Private project.
