"""
Ingest market-cap data (marcap, shares outstanding, trading value) into daily_prices.

Two sources are supported, controlled by --source:

  historical  Read directly from the FinanceData/marcap GitHub dataset
              (https://github.com/FinanceData/marcap). Yearly CSV files are
              downloaded on demand into scripts/python/.cache/marcap/ via
              data_sources.marcap_historical. Point-in-time correct,
              suitable for backtests. Sets daily_prices.source = 'marcap_historical'.

  snapshot    Use fdr.StockListing('KRX'), which is a CURRENT snapshot of
              all KRX stocks. Convenient for current-day validation but NOT
              point-in-time -- a stock that doubled since the as-of-date will
              look artificially expensive. Sets source = 'fdr_listing_snapshot'.

  auto        Try historical first; fall back to snapshot with a loud warning
              if the dataset is unavailable or the requested date is missing.

Modes:

  As-of (single date, optionally falling back to nearest prior trading day):
    python ingest_marcap.py --source historical --as-of-date 2024-12-30 --universe test_200_large

  Range (backfill many dates):
    python ingest_marcap.py --source historical --start-date 2022-01-01 --end-date 2024-12-30 \
      --universe test_200_large --batch-size 25 --resume

  Diagnostics (test source availability, no DB writes):
    python ingest_marcap.py --test-historical-source --as-of-date 2024-12-30
    python ingest_marcap.py --test-source --as-of-date 2024-12-30   # legacy: tests both
"""

import os
import sys
import argparse
import time
from datetime import datetime, timedelta

# Windows console UTF-8 fix (no-op on Mac/Linux)
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

# Source labels written to daily_prices.source. VARCHAR(20).
SOURCE_HISTORICAL = "marcap_historical"
SOURCE_SNAPSHOT = "fdr_listing_snapshot"


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
# Source: historical (FinanceData/marcap GitHub dataset, no PyPI dependency)
# ---------------------------------------------------------------------------

def fetch_historical_marcap(start_date, end_date=None):
    """Fetch historical marcap data for one or more dates from the
    FinanceData/marcap GitHub dataset (cached locally in
    scripts/python/.cache/marcap/).
    """
    from data_sources import marcap_historical

    if end_date is None or end_date == start_date:
        df, _ = marcap_historical.fetch_marcap_date(start_date)
        return df
    return marcap_historical.fetch_marcap_range(start_date, end_date)


def fetch_historical_marcap_as_of(as_of_date, lookback_days=10):
    """Fetch historical marcap for as_of_date, falling back to nearest prior
    trading day if the exact date has no data (weekend, holiday, market close).

    Returns: (df, actual_date) or (None, None).
    """
    from data_sources import marcap_historical
    return marcap_historical.fetch_marcap_date(as_of_date, lookback_days=lookback_days)


# ---------------------------------------------------------------------------
# Source: snapshot (fdr.StockListing)
# ---------------------------------------------------------------------------

def fetch_fdr_listing():
    """Fetch current KRX listing from FinanceDataReader.

    fdr.StockListing('KRX') returns CURRENT snapshot for all KRX stocks.
    NOT point-in-time. Used as a fallback when historical isn't available
    or for current-day validation.
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

    for col in ["market_cap", "shares_outstanding", "trading_value"]:
        if col in df.columns:
            pos = (df[col] > 0).sum()
            print("  {}: {}/{} positive".format(
                col, pos, len(df)), flush=True)
        else:
            print("  {}: MISSING from listing".format(col), flush=True)

    return df


# ---------------------------------------------------------------------------
# Diagnostic modes
# ---------------------------------------------------------------------------

def run_test_historical_source(date_iso):
    """Test that the historical marcap source works on this machine.

    The historical source is the FinanceData/marcap GitHub dataset, NOT a
    PyPI package. This test downloads the relevant yearly file (if not yet
    cached) and reads it.
    """
    print("", flush=True)
    print("=" * 60, flush=True)
    print("  HISTORICAL MARCAP SOURCE TEST", flush=True)
    print("  Target date: {}".format(date_iso), flush=True)
    print("=" * 60, flush=True)

    # Helper module check
    try:
        from data_sources import marcap_historical
    except Exception as e:
        print("  data_sources.marcap_historical: FAILED to import ({0})".format(e),
              flush=True)
        return False

    cache = marcap_historical.cache_dir()
    print("  Source used:       FinanceData/marcap on GitHub", flush=True)
    print("  Local cache path:  {0}".format(cache), flush=True)
    print("  (cache is gitignored; yearly files are downloaded on demand)",
          flush=True)
    print("", flush=True)

    # Try to fetch the as-of date
    print("  Calling fetch_marcap_date('{}')...".format(date_iso),
          flush=True)
    t0 = time.time()
    df, actual_date = fetch_historical_marcap_as_of(date_iso)
    elapsed = time.time() - t0

    if df is None or len(df) == 0:
        print("  Result:            empty / unavailable for {}".format(date_iso),
              flush=True)
        print("  Possible causes: GitHub unreachable, the date is outside "
              "the dataset's coverage, or the yearly file failed to download.",
              flush=True)
        try:
            files = sorted(os.listdir(cache))
            if files:
                print("  Files currently in cache: {0}".format(", ".join(files)),
                      flush=True)
            else:
                print("  Cache directory is empty.", flush=True)
        except OSError:
            print("  Cache directory does not exist yet.", flush=True)
        return False

    # Locate the actual cached file used (year of the trading date)
    final_path = None
    file_ext = None
    try:
        used_year = int(actual_date[:4])
        for ext in ("parquet", "csv.gz"):
            candidate = os.path.join(cache,
                                     "marcap-{0}.{1}".format(used_year, ext))
            if os.path.exists(candidate):
                final_path = candidate
                file_ext = ext
                break
        if final_path is None:
            # Maybe the clone fallback dir holds it in place
            for sub_ext in ("parquet", "csv.gz"):
                clone_file = os.path.join(
                    cache, "repo", "data",
                    "marcap-{0}.{1}".format(used_year, sub_ext))
                if os.path.exists(clone_file):
                    final_path = clone_file
                    file_ext = sub_ext
                    break
    except (ValueError, IndexError, OSError):
        pass

    if final_path:
        try:
            file_size = os.path.getsize(final_path)
        except OSError:
            file_size = 0
        print("  Final file used:   {0}".format(final_path), flush=True)
        print("  File extension:    {0}".format(file_ext or "?"), flush=True)
        print("  File size:         {0:,} bytes".format(file_size), flush=True)

    # Inspect the full yearly file (not just the as-of-date slice) for
    # min/max date and existence checks.
    try:
        full_df = marcap_historical._load_yearly(used_year, verbose=False)
    except Exception:
        full_df = None

    if full_df is not None and len(full_df) > 0:
        try:
            min_date = full_df["date"].min()
            max_date = full_df["date"].max()
            n_rows_year = len(full_df)
            n_dates = full_df["date"].nunique()
            print("  Yearly file rows:  {0:,}".format(n_rows_year), flush=True)
            print("  Date range:        {0} -> {1} ({2} unique dates)".format(
                min_date, max_date, n_dates), flush=True)
            has_target = (full_df["date"] == date_iso).any()
            print("  Has {0}:    {1}".format(
                date_iso, "YES" if has_target else "NO (using nearest prior trading day)"),
                flush=True)
        except Exception as _e:
            print("  (could not summarize yearly file: {0})".format(_e),
                  flush=True)

    print("  Trading date used: {}".format(actual_date), flush=True)
    print("  Row count (date):  {}".format(len(df)), flush=True)
    print("  Elapsed:           {:.1f}s".format(elapsed), flush=True)
    print("  Columns:           {}".format(list(df.columns)), flush=True)
    print("", flush=True)
    print("  First 5 rows:", flush=True)
    cols_to_show = [c for c in ["ticker", "name", "date", "close",
                                "trading_value", "market_cap",
                                "shares_outstanding"] if c in df.columns]
    print(df[cols_to_show].head().to_string(index=False), flush=True)

    # Samsung check
    print("", flush=True)
    samsung = df[df["ticker"] == "005930"]
    if len(samsung) > 0:
        s = samsung.iloc[0]
        print("  Samsung 005930:    FOUND on {}".format(actual_date), flush=True)
        for fld in ["close", "trading_value", "market_cap",
                    "shares_outstanding"]:
            v = s.get(fld)
            if v is None or pd.isna(v):
                print("    {}: NULL".format(fld), flush=True)
            else:
                try:
                    print("    {}: {:,.0f}".format(fld, float(v)),
                          flush=True)
                except (ValueError, TypeError):
                    print("    {}: {}".format(fld, v), flush=True)
    else:
        print("  Samsung 005930:    NOT FOUND in result", flush=True)

    print("", flush=True)
    print("=" * 60, flush=True)
    print("  HISTORICAL SOURCE TEST: PASS", flush=True)
    print("=" * 60, flush=True)
    return True


def run_diagnostics(date_iso):
    """Legacy combined diagnostic: prints status of both sources."""
    print("", flush=True)
    print("=" * 60, flush=True)
    print("  MARCAP SOURCE DIAGNOSTICS", flush=True)
    print("  Target date: {}".format(date_iso), flush=True)
    print("=" * 60, flush=True)

    print("", flush=True)
    print("--- Source 1 (HISTORICAL): "
          "FinanceData/marcap ---", flush=True)
    run_test_historical_source(date_iso)

    print("", flush=True)
    print("--- Source 2 (SNAPSHOT, current): "
          "fdr.StockListing('KRX') ---", flush=True)
    try:
        import FinanceDataReader as fdr
        print("  FDR imported OK (version: {})".format(
            getattr(fdr, "__version__", "?")), flush=True)
        df = fdr.StockListing("KRX")
        if df is not None and len(df) > 0:
            print("  Result: {} rows (CURRENT SNAPSHOT, NOT POINT-IN-TIME)".format(
                len(df)), flush=True)
        else:
            print("  Result: empty/None", flush=True)
    except Exception as e:
        print("  ERROR: {}".format(e), flush=True)

    print("", flush=True)
    print("=" * 60, flush=True)


# ---------------------------------------------------------------------------
# Upsert: historical (true point-in-time)
# ---------------------------------------------------------------------------

def upsert_historical_marcap_bulk(conn, df, dry_run=False):
    """Bulk-upsert historical marcap rows into daily_prices.

    Each row carries its own date (from the marcap library) so we use
    (ticker, date) as the upsert key.

    NEVER overwrites existing OHLCV with NULL. Existing OHLCV values from
    FDR price ingestion are preserved.
    Source is set to 'marcap_historical' (replacing any previous value)
    to mark this row as PIT-correct.

    Returns: (rows_processed, rows_inserted_or_updated)
    The split between "newly inserted" vs "updated" is reported via the
    xmax pseudo-column trick.
    """
    if df is None or len(df) == 0:
        return 0, 0, 0

    rows = []
    for _, r in df.iterrows():
        ticker = r.get("ticker")
        date_str = r.get("date")
        if not ticker or not date_str:
            continue
        mc = safe_int(r.get("market_cap"))
        sh = safe_int(r.get("shares_outstanding"))
        tv = safe_int(r.get("trading_value"))
        if mc is None and sh is None and tv is None:
            continue
        rows.append((
            ticker, date_str,
            safe_float(r.get("open")),
            safe_float(r.get("high")),
            safe_float(r.get("low")),
            safe_float(r.get("close")),
            safe_int(r.get("volume")),
            tv, mc, sh,
            SOURCE_HISTORICAL,
        ))

    if not rows:
        return 0, 0, 0

    if dry_run:
        return len(rows), 0, 0

    cur = conn.cursor()

    # NOTE on COALESCE behavior:
    #   For OHLCV (open, high, low, close, volume): we PREFER existing values
    #     and only fill in if the existing row had NULL. This avoids the
    #     marcap package's possibly-rounded close clobbering the precise FDR
    #     close that's already there.
    #   For market_cap, shares_outstanding, trading_value: we PREFER the new
    #     historical value, falling back to existing only if the new is NULL.
    #   For source: always overwrite with 'marcap_historical' since this is
    #     the PIT-correct provenance signal that downstream code reads.
    # The source label is interpolated as a SQL string literal because
    # execute_values doesn't cleanly support a tail param shared across
    # all VALUES rows, and SOURCE_HISTORICAL is a known-safe constant.
    query = """
    INSERT INTO daily_prices
        (ticker, date, open, high, low, close, volume,
         trading_value, market_cap, shares_outstanding, source)
    VALUES %s
    ON CONFLICT (ticker, date) DO UPDATE SET
        open               = COALESCE(daily_prices.open, EXCLUDED.open),
        high               = COALESCE(daily_prices.high, EXCLUDED.high),
        low                = COALESCE(daily_prices.low, EXCLUDED.low),
        close              = COALESCE(daily_prices.close, EXCLUDED.close),
        volume             = COALESCE(daily_prices.volume, EXCLUDED.volume),
        trading_value      = COALESCE(EXCLUDED.trading_value, daily_prices.trading_value),
        market_cap         = COALESCE(EXCLUDED.market_cap, daily_prices.market_cap),
        shares_outstanding = COALESCE(EXCLUDED.shares_outstanding, daily_prices.shares_outstanding),
        source             = '""" + SOURCE_HISTORICAL + """'
    RETURNING (xmax = 0) AS inserted
    """
    # execute_values returns rows when fetch=True
    result = execute_values(cur, query, rows, fetch=True)
    conn.commit()
    cur.close()

    inserted = sum(1 for r in result if r[0])
    updated = len(result) - inserted

    return len(rows), inserted, updated


# ---------------------------------------------------------------------------
# Upsert: snapshot (current FDR listing)
# ---------------------------------------------------------------------------

def upsert_snapshot_marcap_as_of(conn, df, as_of_date,
                                 ticker_filter=None,
                                 dry_run=False):
    """Apply a CURRENT FDR listing snapshot to daily_prices for the as-of date.

    NOT point-in-time. Sets source = 'fdr_listing_snapshot' on inserts.
    Existing rows have their marcap fields updated, but source is NOT changed
    if it was already 'marcap_historical' (we don't downgrade PIT-correct rows).
    """
    if df is None or len(df) == 0:
        return 0, 0

    if ticker_filter:
        df = df[df["ticker"].isin(set(ticker_filter))]
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

        cur.execute(
            "SELECT 1 FROM daily_prices WHERE ticker = %s AND date = %s",
            (ticker, as_of_date))
        exact = cur.fetchone()

        if exact:
            cur.execute("""
                UPDATE daily_prices
                SET market_cap         = COALESCE(%s, market_cap),
                    shares_outstanding = COALESCE(%s, shares_outstanding),
                    trading_value      = COALESCE(%s, trading_value),
                    source             = CASE
                        WHEN source = %s THEN source
                        ELSE %s
                    END
                WHERE ticker = %s AND date = %s
            """, (mc, sh, tv,
                  SOURCE_HISTORICAL, SOURCE_SNAPSHOT,
                  ticker, as_of_date))
            updated += 1
        else:
            cur.execute(
                "SELECT date FROM daily_prices "
                "WHERE ticker = %s AND date <= %s "
                "ORDER BY date DESC LIMIT 1",
                (ticker, as_of_date))
            latest = cur.fetchone()

            if latest:
                actual_date = latest[0]
                cur.execute("""
                    UPDATE daily_prices
                    SET market_cap         = COALESCE(%s, market_cap),
                        shares_outstanding = COALESCE(%s, shares_outstanding),
                        trading_value      = COALESCE(%s, trading_value),
                        source             = CASE
                            WHEN source = %s THEN source
                            ELSE %s
                        END
                    WHERE ticker = %s AND date = %s
                """, (mc, sh, tv,
                      SOURCE_HISTORICAL, SOURCE_SNAPSHOT,
                      ticker, actual_date))
                updated += 1
            else:
                close = safe_float(row.get("close"))
                vol = safe_int(row.get("volume"))
                cur.execute("""
                    INSERT INTO daily_prices
                        (ticker, date, close, volume,
                         trading_value, market_cap,
                         shares_outstanding, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, date) DO UPDATE SET
                        market_cap         = COALESCE(EXCLUDED.market_cap, daily_prices.market_cap),
                        shares_outstanding = COALESCE(EXCLUDED.shares_outstanding, daily_prices.shares_outstanding),
                        trading_value      = COALESCE(EXCLUDED.trading_value, daily_prices.trading_value)
                """, (ticker, as_of_date, close, vol,
                      tv, mc, sh, SOURCE_SNAPSHOT))
                inserted += 1

    conn.commit()
    cur.close()
    return updated, inserted


# ---------------------------------------------------------------------------
# Range-mode helpers (resumable backfill)
# ---------------------------------------------------------------------------

def get_resume_dates(conn, ticker_set, requested_dates, source_label):
    """For resume: return the set of dates that DON'T yet have full coverage
    for the given tickers in the requested source.

    A date is considered 'done' if every ticker in ticker_set has a
    daily_prices row on that date with source = source_label and a non-null
    market_cap. Anything less and we re-run that date.
    """
    if not ticker_set or not requested_dates:
        return list(requested_dates)

    cur = conn.cursor()
    cur.execute("""
        SELECT date, COUNT(DISTINCT ticker) AS done
        FROM daily_prices
        WHERE ticker = ANY(%s)
          AND date = ANY(%s)
          AND source = %s
          AND market_cap IS NOT NULL
        GROUP BY date
    """, (list(ticker_set), list(requested_dates), source_label))
    done_counts = {row[0].isoformat(): row[1] for row in cur.fetchall()}
    cur.close()

    target_size = len(ticker_set)
    todo = []
    for d in requested_dates:
        d_iso = d if isinstance(d, str) else d.isoformat()
        if done_counts.get(d_iso, 0) < target_size:
            todo.append(d_iso)
    return todo


def run_historical_range(conn, ticker_filter, start_date, end_date,
                         batch_size=25, resume=False, dry_run=False):
    """Backfill historical marcap for a date range.

    Strategy: fetch the entire range in one marcap call (the package caches
    a CSV bundle locally so this is cheap), filter to ticker_filter, then
    bulk upsert in batches of `batch_size` distinct dates at a time so we
    can checkpoint and resume.

    Returns: dict with keys matched, missing, updated, inserted, dates_done
    """
    print("", flush=True)
    print("  Fetching historical marcap "
          "{0} -> {1}...".format(start_date, end_date), flush=True)
    t0 = time.time()
    df = fetch_historical_marcap(start_date, end_date)
    fetch_elapsed = time.time() - t0

    if df is None or len(df) == 0:
        print("  ERROR: marcap returned no data for the range",
              flush=True)
        return None

    print("  Fetched {0:,} rows in {1:.1f}s ({2} unique tickers, "
          "{3} unique dates)".format(
              len(df), fetch_elapsed,
              df["ticker"].nunique(),
              df["date"].nunique()), flush=True)

    if ticker_filter:
        before = df["ticker"].nunique()
        df = df[df["ticker"].isin(set(ticker_filter))].copy()
        after = df["ticker"].nunique()
        missing = sorted(set(ticker_filter) - set(df["ticker"].unique()))
        print("  Filtered to universe: "
              "{0}/{1} tickers matched (out of {2} in source)".format(
                  after, len(ticker_filter), before),
              flush=True)
        if missing:
            print("    Missing from marcap: {0}{1}".format(
                ", ".join(missing[:10]),
                " ..." if len(missing) > 10 else ""),
                flush=True)
    else:
        missing = []

    if len(df) == 0:
        print("  ERROR: no rows after ticker filter", flush=True)
        return None

    # Group by date and upsert one date at a time so resume can checkpoint
    all_dates = sorted(df["date"].unique())
    target_dates = all_dates

    if resume and ticker_filter:
        target_dates = get_resume_dates(
            conn, ticker_filter, all_dates, SOURCE_HISTORICAL)
        skipped = len(all_dates) - len(target_dates)
        if skipped > 0:
            print("  Resume: skipping {0} dates already complete".format(
                skipped), flush=True)

    total_updated = 0
    total_inserted = 0
    total_processed = 0
    dates_done = 0

    for i, d in enumerate(target_dates, start=1):
        df_d = df[df["date"] == d]
        proc, ins, upd = upsert_historical_marcap_bulk(
            conn, df_d, dry_run=dry_run)
        total_processed += proc
        total_inserted += ins
        total_updated += upd
        dates_done += 1

        if i % batch_size == 0 or i == len(target_dates):
            print("    [{0}/{1}] {2}: +{3} ins, ~{4} upd "
                  "(running totals: {5} ins / {6} upd)".format(
                      i, len(target_dates), d, ins, upd,
                      total_inserted, total_updated),
                  flush=True)

    return {
        "matched": df["ticker"].nunique(),
        "missing": missing,
        "updated": total_updated,
        "inserted": total_inserted,
        "processed": total_processed,
        "dates_done": dates_done,
        "fetch_elapsed": fetch_elapsed,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest KRX market-cap data "
                    "(market_cap, shares_outstanding, trading_value)")

    # Source selection
    parser.add_argument(
        "--source", default="auto",
        choices=["historical", "snapshot", "auto"],
        help=("Data source. historical=PIT-correct (marcap pkg); "
              "snapshot=current fdr.StockListing (NOT PIT); "
              "auto=try historical, fall back to snapshot."))

    # Date arguments
    parser.add_argument(
        "--as-of-date",
        help="Single date YYYY-MM-DD. Falls back to nearest prior trading "
             "day if exact date has no data.")
    parser.add_argument(
        "--start-date",
        help="Range mode: start date YYYY-MM-DD. Requires --end-date.")
    parser.add_argument(
        "--end-date",
        help="Range mode: end date YYYY-MM-DD. Requires --start-date.")

    # Filters
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

    # Range options
    parser.add_argument(
        "--batch-size", type=int, default=25,
        help="Range mode: progress logging granularity (dates per log line). "
             "Default 25.")
    parser.add_argument(
        "--resume", action="store_true",
        help="Range mode: skip dates that already have full coverage.")

    # Misc
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Don't write to DB")
    parser.add_argument(
        "--test-historical-source", action="store_true",
        help="Diagnostic: test only the historical (marcap) source.")
    parser.add_argument(
        "--test-source", action="store_true",
        help="Legacy diagnostic: test both historical and snapshot sources.")

    args = parser.parse_args()

    # ---------- Diagnostic shortcuts ----------
    if args.test_historical_source:
        target = (args.as_of_date
                  or datetime.now().strftime("%Y-%m-%d"))
        ok = run_test_historical_source(target)
        sys.exit(0 if ok else 1)

    if args.test_source:
        target = (args.as_of_date
                  or datetime.now().strftime("%Y-%m-%d"))
        run_diagnostics(target)
        sys.exit(0)

    # ---------- Validate mode (as-of vs range) ----------
    range_mode = bool(args.start_date or args.end_date)
    if range_mode:
        if not (args.start_date and args.end_date):
            print("ERROR: range mode requires both --start-date and --end-date",
                  flush=True)
            sys.exit(1)
        if args.as_of_date:
            print("ERROR: cannot mix --as-of-date with --start-date/--end-date",
                  flush=True)
            sys.exit(1)
    else:
        if not args.as_of_date:
            print("ERROR: provide --as-of-date or --start-date/--end-date",
                  flush=True)
            sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)

    # ---------- Resolve ticker filter ----------
    ticker_filter = None
    if args.tickers:
        ticker_filter = [t.strip() for t in args.tickers.split(",")]
    elif args.universe:
        cur = conn.cursor()
        cur.execute(
            "SELECT ticker FROM universe_memberships "
            "WHERE universe_name = %s ORDER BY ticker",
            (args.universe,))
        ticker_filter = [r[0] for r in cur.fetchall()]
        cur.close()
        if not ticker_filter:
            print("ERROR: Universe '{}' not found or empty".format(
                args.universe), flush=True)
            sys.exit(1)
        print("  Universe '{}': {} tickers".format(
            args.universe, len(ticker_filter)), flush=True)
    elif args.limit or args.market:
        ticker_filter = get_db_tickers(conn, args.limit, args.market)

    log_id = log_start(conn, {
        "source": args.source,
        "as_of_date": args.as_of_date,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "universe": args.universe,
        "tickers": args.tickers,
        "limit": args.limit,
        "market": args.market,
        "dry_run": args.dry_run,
        "resume": args.resume,
    })

    t0 = time.time()

    try:
        # Resolve source: snapshot/historical/auto
        effective_source = args.source

        # ---------- RANGE MODE ----------
        if range_mode:
            print("", flush=True)
            print("KRX Marcap Ingestion (RANGE)", flush=True)
            print("=" * 55, flush=True)
            print("  Source:        {0}".format(args.source), flush=True)
            print("  Range:         {0} -> {1}".format(
                args.start_date, args.end_date), flush=True)
            if ticker_filter:
                print("  Ticker filter: {0} tickers".format(
                    len(ticker_filter)), flush=True)
            print("  Resume:        {0}".format(args.resume), flush=True)
            if args.dry_run:
                print("  DRY RUN:       no writes", flush=True)
            print("=" * 55, flush=True)

            if args.source == "snapshot":
                print("  ERROR: snapshot source does not support range "
                      "mode (it has no historical dates).", flush=True)
                sys.exit(1)

            # historical or auto -> try historical
            result = run_historical_range(
                conn, ticker_filter,
                args.start_date, args.end_date,
                batch_size=args.batch_size,
                resume=args.resume,
                dry_run=args.dry_run)

            if result is None and args.source == "historical":
                print("  ERROR: historical source unavailable.", flush=True)
                print("        Test it with: python ingest_marcap.py "
                      "--test-historical-source --as-of-date {0}".format(
                          args.start_date), flush=True)
                log_finish(conn, log_id, "error",
                           error_message="historical source unavailable")
                sys.exit(1)

            if result is None:
                print("  ERROR: range fetch failed", flush=True)
                sys.exit(1)

            elapsed = time.time() - t0
            print("", flush=True)
            print("SUMMARY (range)", flush=True)
            print("=" * 55, flush=True)
            print("  Source:           {0}".format(SOURCE_HISTORICAL), flush=True)
            print("  Range:            {0} -> {1}".format(
                args.start_date, args.end_date), flush=True)
            print("  Dates processed:  {0}".format(result["dates_done"]), flush=True)
            print("  Tickers matched:  {0}".format(result["matched"]), flush=True)
            print("  Tickers missing:  {0}".format(len(result["missing"])), flush=True)
            print("  Rows inserted:    {0}".format(result["inserted"]), flush=True)
            print("  Rows updated:     {0}".format(result["updated"]), flush=True)
            print("  Rows processed:   {0}".format(result["processed"]), flush=True)
            print("  Elapsed:          {0:.1f}s".format(elapsed), flush=True)
            print("=" * 55, flush=True)

            log_finish(conn, log_id, "success",
                       rows_processed=result["processed"],
                       rows_inserted=result["inserted"],
                       rows_updated=result["updated"])

        # ---------- AS-OF MODE ----------
        else:
            print("", flush=True)
            print("KRX Marcap Ingestion (AS-OF)", flush=True)
            print("=" * 55, flush=True)
            print("  Source:        {0}".format(args.source), flush=True)
            print("  As-of date:    {0}".format(args.as_of_date), flush=True)
            if ticker_filter:
                print("  Ticker filter: {0} tickers".format(
                    len(ticker_filter)), flush=True)
            if args.dry_run:
                print("  DRY RUN:       no writes", flush=True)
            print("=" * 55, flush=True)
            print("", flush=True)

            df = None
            actual_date = None
            chosen_source = None

            if effective_source in ("historical", "auto"):
                df, actual_date = fetch_historical_marcap_as_of(
                    args.as_of_date)
                if df is not None and len(df) > 0:
                    chosen_source = "historical"
                    print("  Trading date used: {0} (from historical source)".format(
                        actual_date), flush=True)
                else:
                    if effective_source == "historical":
                        print("  ERROR: historical source returned no data "
                              "for {0}.".format(args.as_of_date), flush=True)
                        print("        Try: python ingest_marcap.py "
                              "--test-historical-source --as-of-date {0}".format(
                                  args.as_of_date), flush=True)
                        log_finish(conn, log_id, "error",
                                   error_message="historical empty")
                        sys.exit(1)
                    else:
                        print("  WARNING: historical source unavailable, "
                              "falling back to snapshot.", flush=True)
                        print("           SNAPSHOT IS NOT POINT-IN-TIME. "
                              "Backtests will be biased.", flush=True)

            if df is None or len(df) == 0:
                # Snapshot path
                df = fetch_fdr_listing()
                if df is None or len(df) == 0:
                    print("  ERROR: both sources unavailable.", flush=True)
                    log_finish(conn, log_id, "error",
                               error_message="both sources empty")
                    sys.exit(1)
                chosen_source = "snapshot"
                if effective_source == "snapshot":
                    print("  WARNING: --source snapshot is NOT POINT-IN-TIME.",
                          flush=True)
                    print("           Use only for current-day validation.",
                          flush=True)

            # Apply
            if chosen_source == "historical":
                # Filter to universe and bulk upsert
                if ticker_filter:
                    before = df["ticker"].nunique()
                    df = df[df["ticker"].isin(set(ticker_filter))].copy()
                    after = df["ticker"].nunique()
                    missing = sorted(set(ticker_filter) - set(df["ticker"].unique()))
                else:
                    after = df["ticker"].nunique()
                    missing = []
                    before = after

                print("  Source columns: {0}".format(
                    [c for c in df.columns]), flush=True)
                print("  Tickers matched: {0}/{1}".format(
                    after,
                    len(ticker_filter) if ticker_filter else before),
                    flush=True)
                if missing:
                    print("  Tickers missing from source: {0}{1}".format(
                        ", ".join(missing[:10]),
                        " ..." if len(missing) > 10 else ""),
                        flush=True)

                # Samsung sanity
                samsung = df[df["ticker"] == "005930"]
                if len(samsung) > 0:
                    s = samsung.iloc[0]
                    print("  Samsung 005930: mcap={0:,.0f}  "
                          "shares={1:,.0f}".format(
                              s.get("market_cap") or 0,
                              s.get("shares_outstanding") or 0),
                          flush=True)

                processed, inserted, updated = upsert_historical_marcap_bulk(
                    conn, df, dry_run=args.dry_run)

                elapsed = time.time() - t0
                print("", flush=True)
                print("SUMMARY (as-of)", flush=True)
                print("=" * 55, flush=True)
                print("  Source:            {0}".format(SOURCE_HISTORICAL), flush=True)
                print("  Requested date:    {0}".format(args.as_of_date), flush=True)
                print("  Trading date used: {0}".format(actual_date), flush=True)
                print("  Tickers matched:   {0}".format(after), flush=True)
                print("  Tickers missing:   {0}".format(len(missing)), flush=True)
                print("  Rows inserted:     {0}".format(inserted), flush=True)
                print("  Rows updated:      {0}".format(updated), flush=True)
                print("  Rows processed:    {0}".format(processed), flush=True)
                print("  Elapsed:           {0:.1f}s".format(elapsed), flush=True)
                print("=" * 55, flush=True)

                log_finish(conn, log_id, "success",
                           rows_processed=processed,
                           rows_inserted=inserted,
                           rows_updated=updated)

            else:  # snapshot path
                # Samsung sanity
                samsung = df[df["ticker"] == "005930"]
                if len(samsung) > 0:
                    s = samsung.iloc[0]
                    print("  Samsung 005930: mcap={0:,.0f}  "
                          "shares={1:,.0f}".format(
                              s.get("market_cap") or 0,
                              s.get("shares_outstanding") or 0),
                          flush=True)

                upd, ins = upsert_snapshot_marcap_as_of(
                    conn, df, args.as_of_date,
                    ticker_filter, dry_run=args.dry_run)

                elapsed = time.time() - t0
                print("", flush=True)
                print("SUMMARY (as-of, snapshot fallback)", flush=True)
                print("=" * 55, flush=True)
                print("  Source:    {0}".format(SOURCE_SNAPSHOT), flush=True)
                print("  As-of:     {0}".format(args.as_of_date), flush=True)
                print("  Updated:   {0}".format(upd), flush=True)
                print("  Inserted:  {0}".format(ins), flush=True)
                print("  Elapsed:   {0:.1f}s".format(elapsed), flush=True)
                print("  WARNING: NOT POINT-IN-TIME. Marcap is from a "
                      "current FDR snapshot.", flush=True)
                print("           Do not rely on this for backtests.",
                      flush=True)
                print("=" * 55, flush=True)

                log_finish(conn, log_id, "success",
                           rows_processed=upd + ins,
                           rows_inserted=ins,
                           rows_updated=upd)

    except Exception as e:
        log_finish(conn, log_id, "error", error_message=str(e))
        print("ERROR: {}".format(e), flush=True)
        import traceback
        traceback.print_exc()
        raise
    finally:
        conn.close()

    print("", flush=True)
    print("Done!", flush=True)
