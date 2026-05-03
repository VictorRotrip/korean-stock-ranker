"""
Ingest market-cap data (marcap, shares, trading value) into daily_prices.

Primary source: fdr.StockListing('KRX')
  Returns current KRX listing snapshot with Marcap, Stocks, Amount columns.
  NOTE: This is a current snapshot, not point-in-time historical data.
  Source is tagged 'fdr_listing_snapshot' so downstream code knows.

Fallback: pykrx per-ticker (diagnostic/optional only).

Modes:
  As-of:      python ingest_marcap.py --as-of-date 2024-12-30 --limit 50
  Diagnostic: python ingest_marcap.py --test-source --as-of-date 2024-12-30

Usage:
    python ingest_marcap.py --as-of-date 2024-12-30
    python ingest_marcap.py --as-of-date 2024-12-30 --limit 50
    python ingest_marcap.py --as-of-date 2024-12-30 --tickers 005930,000660
    python ingest_marcap.py --as-of-date 2024-12-30 --market KOSPI
    python ingest_marcap.py --as-of-date 2024-12-30 --dry-run
    python ingest_marcap.py --test-source --as-of-date 2024-12-30
"""

import os
import sys
import argparse
import time
from datetime import datetime, timedelta

# Windows console UTF-8 fix
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.", flush=True)
    sys.exit(1)

SCRIPT_NAME = "ingest_marcap"


# ---------------------------------------------------------------------------
# Ingestion logging
# ---------------------------------------------------------------------------

def log_start(conn, params=None):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ingestion_log (script_name, parameters) "
        "VALUES (%s, %s) RETURNING id",
        (SCRIPT_NAME, Json(params)),
    )
    log_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    return log_id


def log_finish(conn, log_id, status, rows_processed=0,
               rows_inserted=0, rows_updated=0,
               rows_skipped=0, error_message=None):
    cur = conn.cursor()
    cur.execute(
        "UPDATE ingestion_log "
        "SET finished_at = NOW(), status = %s, "
        "rows_processed = %s, rows_inserted = %s, "
        "rows_updated = %s, rows_skipped = %s, "
        "error_message = %s "
        "WHERE id = %s",
        (status, rows_processed, rows_inserted,
         rows_updated, rows_skipped,
         error_message, log_id),
    )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_db_tickers(conn, limit=None, market=None):
    cur = conn.cursor()
    q = "SELECT ticker FROM stocks WHERE is_active = TRUE"
    if market:
        markets = [m.strip() for m in market.split(",")]
        q += " AND market IN ({})".format(
            ",".join("'{}'".format(m) for m in markets))
    q += " ORDER BY ticker"
    if limit:
        q += " LIMIT {}".format(int(limit))
    cur.execute(q)
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    return tickers


def safe_int(val):
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
        v = int(val)
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


def safe_float(val):
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
        v = float(val)
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Primary source: FDR StockListing('KRX')
# ---------------------------------------------------------------------------

def fetch_fdr_listing():
    """Fetch current KRX listing from FinanceDataReader.

    fdr.StockListing('KRX') returns all KRX stocks with columns:
        Code, ISU_CD, Name, Market, Dept, Close, Open, High, Low,
        Volume, Amount, Marcap, Stocks, MarketId

    Returns: DataFrame with standardized columns or None.
    """
    try:
        import FinanceDataReader as fdr
    except ImportError:
        print("  ERROR: FinanceDataReader not installed. "
              "Run: pip install finance-datareader", flush=True)
        return None

    print("  Fetching fdr.StockListing('KRX')...",
          end=" ", flush=True)
    try:
        df = fdr.StockListing("KRX")
    except Exception as e:
        print("FAILED: {}".format(e), flush=True)
        return None

    if df is None or len(df) == 0:
        print("empty result", flush=True)
        return None

    print("{} rows".format(len(df)), flush=True)

    # Normalize column names
    rename = {}
    for col in df.columns:
        lc = str(col).strip()
        if lc == "Code":
            rename[col] = "ticker"
        elif lc == "Name":
            rename[col] = "name"
        elif lc == "Close":
            rename[col] = "close"
        elif lc == "Open":
            rename[col] = "open"
        elif lc == "High":
            rename[col] = "high"
        elif lc == "Low":
            rename[col] = "low"
        elif lc == "Volume":
            rename[col] = "volume"
        elif lc == "Amount":
            rename[col] = "trading_value"
        elif lc == "Marcap":
            rename[col] = "market_cap"
        elif lc == "Stocks":
            rename[col] = "shares_outstanding"
        elif lc == "Market":
            rename[col] = "market_type"
        elif lc == "Dept":
            rename[col] = "dept"
    df = df.rename(columns=rename)

    if "ticker" not in df.columns:
        print("  WARNING: no 'ticker' column after rename. "
              "Raw columns: {}".format(list(df.columns)),
              flush=True)
        return None

    # Verify key columns exist
    for col in ["market_cap", "shares_outstanding",
                "trading_value"]:
        if col in df.columns:
            nonnull = df[col].notna().sum()
            pos = (df[col] > 0).sum()
            print("  {}: {}/{} positive".format(
                col, pos, len(df)), flush=True)
        else:
            print("  {}: MISSING from listing".format(col),
                  flush=True)

    return df


# ---------------------------------------------------------------------------
# Diagnostic mode
# ---------------------------------------------------------------------------

def run_diagnostics(date_iso):
    print("", flush=True)
    print("=" * 60, flush=True)
    print("  MARCAP SOURCE DIAGNOSTICS", flush=True)
    print("  Target date: {}".format(date_iso), flush=True)
    print("=" * 60, flush=True)

    # Source 1: FDR StockListing (PRIMARY)
    print("", flush=True)
    print("--- Source 1 (PRIMARY): "
          "fdr.StockListing('KRX') ---", flush=True)
    try:
        import FinanceDataReader as fdr
        print("  FDR imported OK (version: {})".format(
            getattr(fdr, "__version__", "?")), flush=True)
    except ImportError:
        print("  FDR NOT INSTALLED", flush=True)
        fdr = None

    if fdr:
        try:
            df = fdr.StockListing("KRX")
            if df is not None and len(df) > 0:
                print("  Result: {} rows".format(len(df)),
                      flush=True)
                print("  Columns: {}".format(
                    list(df.columns)), flush=True)
                print("", flush=True)
                print("  First 5 rows:", flush=True)
                print(df.head().to_string(), flush=True)
                print("", flush=True)
                # Samsung check
                code_col = None
                for c in df.columns:
                    if str(c).strip() == "Code":
                        code_col = c
                        break
                if code_col:
                    samsung = df[df[code_col] == "005930"]
                    if len(samsung) > 0:
                        print("  Samsung 005930: FOUND",
                              flush=True)
                        print("  {}".format(
                            samsung.iloc[0].to_dict()),
                            flush=True)
                    else:
                        print("  Samsung 005930: NOT FOUND",
                              flush=True)
                # Marcap check
                for col_name in ["Marcap", "Stocks", "Amount"]:
                    if col_name in df.columns:
                        nn = df[col_name].notna().sum()
                        pos = (df[col_name] > 0).sum()
                        print("  {}: {}/{} positive".format(
                            col_name, pos, len(df)),
                            flush=True)
                    else:
                        print("  {}: MISSING".format(
                            col_name), flush=True)
            else:
                print("  Result: empty/None", flush=True)
        except Exception as e:
            print("  ERROR: {}".format(e), flush=True)

    # Source 2: pykrx (FALLBACK — diagnostic only)
    print("", flush=True)
    print("--- Source 2 (FALLBACK): "
          "pykrx.stock.get_market_cap_by_ticker ---",
          flush=True)
    try:
        from pykrx import stock
        print("  pykrx imported OK", flush=True)
    except ImportError:
        print("  pykrx NOT INSTALLED", flush=True)
        stock = None

    if stock:
        yyyymmdd = date_iso.replace("-", "")
        print("  Calling get_market_cap_by_ticker"
              "('{}')...".format(yyyymmdd), flush=True)
        try:
            df2 = stock.get_market_cap_by_ticker(
                yyyymmdd, market="ALL")
            time.sleep(0.4)
            if df2 is not None and len(df2) > 0:
                print("  Result: {} rows".format(len(df2)),
                      flush=True)
                print("  Columns: {}".format(
                    list(df2.columns)), flush=True)
                print("  Index name: {}".format(
                    df2.index.name), flush=True)
                print("  First 3:", flush=True)
                print(df2.head(3).to_string(), flush=True)
            else:
                print("  Result: empty/None", flush=True)
        except Exception as e:
            print("  ERROR: {}".format(e), flush=True)

    print("", flush=True)
    print("=" * 60, flush=True)
    print("  DIAGNOSTICS COMPLETE", flush=True)
    print("=" * 60, flush=True)


# ---------------------------------------------------------------------------
# Upsert marcap into daily_prices
# ---------------------------------------------------------------------------

def upsert_marcap_as_of(conn, df, as_of_date,
                        ticker_filter=None,
                        dry_run=False):
    """Upsert marcap data into daily_prices for the as-of date.

    Strategy:
      1. For each ticker, find the daily_prices row on as_of_date.
      2. If no exact-date row exists, find the latest row
         on or before as_of_date.
      3. UPDATE that row with market_cap, shares_outstanding,
         trading_value from the FDR listing snapshot.
      4. If no row exists at all, INSERT a new row on as_of_date.

    Returns: (updated_count, inserted_count)
    """
    if df is None or len(df) == 0:
        return 0, 0

    # Filter to target tickers
    if ticker_filter:
        ticker_set = set(ticker_filter)
        df = df[df["ticker"].isin(ticker_set)]

    if len(df) == 0:
        return 0, 0

    if dry_run:
        return len(df), 0

    cur = conn.cursor()
    updated = 0
    inserted = 0

    for _, row in df.iterrows():
        ticker = row.get("ticker")
        if not ticker:
            continue

        mc = safe_int(row.get("market_cap"))
        sh = safe_int(row.get("shares_outstanding"))
        tv = safe_int(row.get("trading_value"))

        if mc is None and sh is None and tv is None:
            continue

        # Try exact date first
        cur.execute(
            "SELECT 1 FROM daily_prices "
            "WHERE ticker = %s AND date = %s",
            (ticker, as_of_date))
        exact = cur.fetchone()

        if exact:
            # Update the exact-date row
            cur.execute(
                "UPDATE daily_prices "
                "SET market_cap = COALESCE(%s, market_cap), "
                "    shares_outstanding = COALESCE("
                "        %s, shares_outstanding), "
                "    trading_value = COALESCE("
                "        %s, trading_value), "
                "    source = COALESCE(source, '') || '+marcap' "
                "WHERE ticker = %s AND date = %s",
                (mc, sh, tv, ticker, as_of_date))
            updated += 1
        else:
            # Find latest row on or before as_of_date
            cur.execute(
                "SELECT date FROM daily_prices "
                "WHERE ticker = %s AND date <= %s "
                "ORDER BY date DESC LIMIT 1",
                (ticker, as_of_date))
            latest = cur.fetchone()

            if latest:
                actual_date = latest[0]
                cur.execute(
                    "UPDATE daily_prices "
                    "SET market_cap = COALESCE("
                    "    %s, market_cap), "
                    "    shares_outstanding = COALESCE("
                    "        %s, shares_outstanding), "
                    "    trading_value = COALESCE("
                    "        %s, trading_value), "
                    "    source = COALESCE(source, '') "
                    "        || '+marcap' "
                    "WHERE ticker = %s AND date = %s",
                    (mc, sh, tv, ticker, actual_date))
                updated += 1
            else:
                # No price row exists — insert a new row
                close = safe_float(row.get("close"))
                vol = safe_int(row.get("volume"))
                cur.execute(
                    "INSERT INTO daily_prices "
                    "(ticker, date, close, volume, "
                    " trading_value, market_cap, "
                    " shares_outstanding, source) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (ticker, date) DO UPDATE SET "
                    "market_cap = COALESCE("
                    "  EXCLUDED.market_cap, "
                    "  daily_prices.market_cap), "
                    "shares_outstanding = COALESCE("
                    "  EXCLUDED.shares_outstanding, "
                    "  daily_prices.shares_outstanding), "
                    "trading_value = COALESCE("
                    "  EXCLUDED.trading_value, "
                    "  daily_prices.trading_value)",
                    (ticker, as_of_date, close, vol,
                     tv, mc, sh, "fdr_listing_snapshot"))
                inserted += 1

    conn.commit()
    cur.close()
    return updated, inserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest KRX market-cap data "
                    "(marcap, shares, trading value)")
    parser.add_argument(
        "--as-of-date",
        help="Target date YYYY-MM-DD. Marcap data is applied "
             "to daily_prices rows on or near this date.")
    parser.add_argument(
        "--tickers",
        help="Comma-separated tickers to filter")
    parser.add_argument(
        "--universe",
        help="Use named universe from universe_memberships table")
    parser.add_argument(
        "--limit", type=int,
        help="Max tickers from DB")
    parser.add_argument(
        "--market",
        help="Filter: KOSPI,KOSDAQ")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Don't write to DB")
    parser.add_argument(
        "--test-source", action="store_true",
        help="Diagnostic mode: test each data source "
             "and print results")
    args = parser.parse_args()

    # Diagnostic mode
    if args.test_source:
        target = (args.as_of_date
                  or datetime.now().strftime("%Y-%m-%d"))
        run_diagnostics(target)
        sys.exit(0)

    if not args.as_of_date:
        print("ERROR: provide --as-of-date", flush=True)
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)

    # Resolve ticker filter
    ticker_filter = None
    if args.tickers:
        ticker_filter = [
            t.strip() for t in args.tickers.split(",")]
    elif args.universe:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM universe_memberships WHERE universe_name = %s ORDER BY ticker", (args.universe,))
        ticker_filter = [r[0] for r in cur.fetchall()]
        cur.close()
        if not ticker_filter:
            print("ERROR: Universe '{}' not found or empty".format(args.universe), flush=True)
            sys.exit(1)
        print("  Universe '{}': {} tickers".format(args.universe, len(ticker_filter)), flush=True)
    elif args.limit or args.market:
        ticker_filter = get_db_tickers(
            conn, args.limit, args.market)

    log_id = log_start(conn, {
        "as_of_date": args.as_of_date,
        "tickers": args.tickers,
        "limit": args.limit,
        "market": args.market,
        "dry_run": args.dry_run,
    })

    t0 = time.time()

    try:
        print("", flush=True)
        print("KRX Marcap Ingestion", flush=True)
        print("=" * 55, flush=True)
        print("  As-of date:   {}".format(
            args.as_of_date), flush=True)
        print("  Source:        fdr.StockListing('KRX') "
              "[current snapshot]", flush=True)
        if ticker_filter:
            print("  Ticker filter: {} tickers".format(
                len(ticker_filter)), flush=True)
        if args.dry_run:
            print("  DRY RUN:       no writes", flush=True)
        print("=" * 55, flush=True)
        print("", flush=True)

        # Fetch FDR listing
        df = fetch_fdr_listing()

        if df is None or len(df) == 0:
            print("", flush=True)
            print("  ERROR: fdr.StockListing('KRX') "
                  "returned no data.", flush=True)
            print("  Run: python ingest_marcap.py "
                  "--test-source to diagnose", flush=True)
            log_finish(conn, log_id, "error",
                       error_message="FDR listing empty")
            sys.exit(1)

        # Verify marcap columns
        has_mc = ("market_cap" in df.columns
                  and df["market_cap"].notna().any()
                  and (df["market_cap"] > 0).any())
        if not has_mc:
            print("", flush=True)
            print("  ERROR: FDR listing has no market_cap "
                  "data.", flush=True)
            print("  Columns: {}".format(
                list(df.columns)), flush=True)
            log_finish(conn, log_id, "error",
                       error_message="No marcap in listing")
            sys.exit(1)

        # Samsung sanity check
        samsung = df[df["ticker"] == "005930"]
        if len(samsung) > 0:
            s = samsung.iloc[0]
            print("  Samsung 005930: mcap={:,.0f}  "
                  "shares={:,.0f}".format(
                      s.get("market_cap", 0),
                      s.get("shares_outstanding", 0)),
                  flush=True)
        else:
            print("  Samsung 005930: not in listing",
                  flush=True)

        # Filter and upsert
        print("", flush=True)
        upd, ins = upsert_marcap_as_of(
            conn, df, args.as_of_date,
            ticker_filter, dry_run=args.dry_run)

        if ticker_filter:
            matched = df[
                df["ticker"].isin(set(ticker_filter))]
            print("  Matched {}/{} target tickers "
                  "in listing".format(
                      len(matched), len(ticker_filter)),
                  flush=True)
            # Show which target tickers are missing
            missing = set(ticker_filter) - set(
                matched["ticker"].tolist())
            if missing:
                print("  Missing tickers: {}".format(
                    ", ".join(sorted(missing)[:10])),
                    flush=True)

        action = "Would update" if args.dry_run else "Updated"
        print("  {} {} existing rows, "
              "inserted {} new rows".format(
                  action, upd, ins), flush=True)

        # Summary
        elapsed = time.time() - t0
        print("", flush=True)
        print("SUMMARY", flush=True)
        print("=" * 55, flush=True)
        print("  Source:     fdr_listing_snapshot", flush=True)
        print("  As-of:     {}".format(
            args.as_of_date), flush=True)
        print("  Updated:   {}".format(upd), flush=True)
        print("  Inserted:  {}".format(ins), flush=True)
        print("  Elapsed:   {:.1f}s".format(elapsed),
              flush=True)
        print("  NOTE: Marcap is from a current FDR snapshot,",
              flush=True)
        print("        not point-in-time {} data.".format(
            args.as_of_date), flush=True)
        print("=" * 55, flush=True)

        log_finish(conn, log_id, "success",
                   rows_processed=upd + ins,
                   rows_inserted=ins,
                   rows_updated=upd)

    except Exception as e:
        log_finish(conn, log_id, "error",
                   error_message=str(e))
        print("ERROR: {}".format(e), flush=True)
        import traceback
        traceback.print_exc()
        raise
    finally:
        conn.close()

    print("", flush=True)
    print("Done!", flush=True)
