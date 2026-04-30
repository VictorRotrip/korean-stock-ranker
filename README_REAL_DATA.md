# Real Data Setup Guide

This guide walks you through switching from mock data to a live Supabase Postgres database with real Korean market data.

## Prerequisites

- **Node.js 18+** and **Python 3.10+**
- A free [Supabase](https://supabase.com/) project (or any Postgres 15+ instance)
- Optional: [DART API key](https://opendart.fss.or.kr/) for Phase 3 financial statements

## 1. Supabase Setup

Create a Supabase project, then grab the connection string from **Settings → Database → Connection string → URI**.

It looks like:
```
postgresql://postgres:[PASSWORD]@db.xxxx.supabase.co:5432/postgres
```

> **Use the "Session mode" (port 5432) connection string**, not the pooler. The Python scripts use long-lived connections that work best with direct connections.

## 2. Environment Variables

Copy the example and fill in your values:

```bash
cp .env.example .env.local
```

Edit `.env.local`:

```env
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@db.YOUR_PROJECT.supabase.co:5432/postgres

# Switch the web app to use real data (optional — defaults to auto-detect)
NEXT_PUBLIC_DATA_SOURCE=db

# Phase 3 (optional): DART financial statements
DART_API_KEY=your-dart-api-key
```

**Security notes:**
- `DATABASE_URL` is server-side only — never prefix with `NEXT_PUBLIC_`
- `DART_API_KEY` is also server-side only
- `SUPABASE_SERVICE_ROLE_KEY` is **not used** by the ingestion scripts — they connect via `DATABASE_URL` directly
- `.env.local` is already in `.gitignore`

## 3. Create Tables

Run the migration SQL against your Supabase database. You can do this in the **Supabase SQL Editor** (Dashboard → SQL Editor → New query):

```sql
-- Paste the contents of scripts/sql/001_create_tables.sql and run
```

Or via `psql`:

```bash
psql "$DATABASE_URL" -f scripts/sql/001_create_tables.sql
```

This creates 11 tables: `stocks`, `daily_prices`, `pykrx_fundamentals`, `financial_statements`, `short_selling`, `dart_filings`, `factor_coverage`, `ranking_systems`, `ranking_snapshots`, `factor_snapshots`, `ingestion_log`.

## 4. Install Python Dependencies

```bash
cd scripts/python
pip install -r requirements.txt
```

Required packages: `pykrx`, `opendartreader`, `financedatareader`, `psycopg2-binary`, `pandas`, `python-dotenv`.

## 5. Smoke Test (Recommended First Step)

**Always run the smoke test before a full ingestion.** It validates the entire pipeline with 5 large-cap tickers (Samsung Electronics, SK Hynix, Naver, LG Chem, Hyundai Motor):

```bash
cd scripts/python
python smoke_test.py --as-of-date 2024-12-31
```

This takes ~2–5 minutes and will:
1. Check `DATABASE_URL` and DB connection
2. Verify all 11 tables exist
3. Ingest universe, prices, fundamentals, short selling for 5 tickers
4. Calculate factor values and percentile ranks
5. Create a ranking snapshot
6. Print a pass/fail summary

If DART_API_KEY is not set, it skips DART ingestion (that's fine for the smoke test).

You can also run it without ingestion to just verify existing data:

```bash
python smoke_test.py --skip-ingestion --as-of-date 2024-12-31
```

## 6. Full Ingestion (Step by Step)

After the smoke test passes, run ingestion in phases. Start with a small date range, then expand.

### Phase 1: Universe + Prices

```bash
# 1. Stock universe (all KOSPI + KOSDAQ, ~2500 stocks)
python ingest_universe.py

# 2. Prices — start with 3 months, expand later
python ingest_prices.py --start-date 2024-10-01 --end-date 2024-12-31

# Full price history (WARNING: takes hours)
# python ingest_prices.py --full
```

### Phase 2: Fundamentals + Short Selling

```bash
# 3. pykrx fundamentals (PER, PBR, EPS, BPS, DPS)
python ingest_pykrx_fundamentals.py --start-date 2024-10-01 --end-date 2024-12-31

# 4. Short selling data
python ingest_short_selling.py --start-date 2024-10-01 --end-date 2024-12-31
```

> Note: Korean short selling was banned Nov 2023 – Mar 2025 for most stocks. Data for that period will be empty.

### Phase 3: DART Financial Statements (Optional)

Requires `DART_API_KEY`:

```bash
# 5. DART financials — latest year for all tickers in DB
python ingest_dart.py

# Or specific tickers
python ingest_dart.py --tickers 005930,000660 --year 2023

# Full history (WARNING: slow, API rate limited)
# python ingest_dart.py --full
```

### Phase 4: Calculate Factors + Ranking

```bash
# 6. Calculate all factor values and percentile ranks
python calculate_factors.py --as-of-date 2024-12-31

# 7. Run the ranking engine and store a snapshot
python run_ranking_snapshot.py --as-of-date 2024-12-31
```

## CLI Reference

All scripts share a consistent set of arguments:

| Argument | Description | Scripts |
|---|---|---|
| `--tickers` | Comma-separated ticker codes | All |
| `--start-date` | Start date (YYYY-MM-DD) | prices, fundamentals, short selling |
| `--end-date` | End date (YYYY-MM-DD) | prices, fundamentals, short selling |
| `--date` | Single date (YYYY-MM-DD) | fundamentals, short selling |
| `--limit` | Max tickers to process | All |
| `--full` | Load full history (slow!) | prices, fundamentals, short selling, dart |
| `--as-of-date` | Point-in-time date for factors/ranking | calculate_factors, run_ranking_snapshot |
| `--system-id` | Ranking system ID (default: "default") | run_ranking_snapshot |

## Switching Data Sources

The web app auto-detects its data source:

- If `DATABASE_URL` is set → uses Supabase
- Otherwise → uses built-in mock data

You can force it explicitly:

```env
NEXT_PUBLIC_DATA_SOURCE=mock   # always use mock data
NEXT_PUBLIC_DATA_SOURCE=db     # always use real data (requires DATABASE_URL)
```

## Monitoring

Every script run is logged to the `ingestion_log` table:

```sql
SELECT script_name, status, rows_processed, rows_inserted,
       started_at, finished_at, error_message
FROM ingestion_log
ORDER BY id DESC LIMIT 20;
```

Check factor coverage status:

```sql
SELECT factor_id, data_status, is_available, uses_mock_data, coverage_ratio
FROM factor_coverage
ORDER BY factor_id;
```

## Troubleshooting

### "DATABASE_URL not set"
Copy `.env.example` to `.env.local` and add your Supabase connection string. Make sure the file is in the project root (same directory as `package.json`).

### "relation X does not exist"
Run the migration first: `psql "$DATABASE_URL" -f scripts/sql/001_create_tables.sql`

### "No factor snapshots found for DATE"
You need to run `calculate_factors.py` before `run_ranking_snapshot.py`. Factor calculation needs price + financial data, so make sure ingestion ran first.

### "connection refused" or "timeout"
Check that your Supabase project is not paused (free tier pauses after 1 week of inactivity). Go to the Supabase dashboard and unpause it.

### pykrx returns empty data
pykrx pulls from KRX/Naver and sometimes returns empty DataFrames for weekends, holidays, or when the service is temporarily down. Retry after a few minutes. Also check that your date is a valid trading day.

### DART API rate limiting
The free DART API tier allows ~1000 requests/day. If you hit the limit, wait until midnight KST and retry, or spread the ingestion across multiple days.

### Stale mock data showing in the app
Make sure you set `NEXT_PUBLIC_DATA_SOURCE=db` in `.env.local`, then restart the dev server (`npm run dev`). Next.js caches environment variables at startup.

## Safety Design

All ingestion scripts follow these safety patterns:

- **Upsert-only**: Every INSERT uses `ON CONFLICT ... DO UPDATE` — running a script twice never creates duplicates
- **Ingestion logging**: Every run is tracked in `ingestion_log` with start/finish times, row counts, and error messages
- **Point-in-time correctness**: DART financial data uses `data_available_date` (filing date + 1 day) to prevent lookahead bias
- **Fail-safe on missing env**: Scripts exit immediately with a clear message if `DATABASE_URL` is missing
- **No destructive operations**: Scripts never DROP, TRUNCATE, or DELETE — they only INSERT/UPDATE
