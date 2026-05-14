"""
Targeted refresh: re-fetch DART filings to fill NULL interest_expense.

Why this exists
---------------
The original ingest_dart.py ACCOUNT_MAP only mapped Korean 이자비용
(literal "interest cost") to interest_expense. Many Korean K-IFRS
filers report 금융비용 (finance costs — broader bucket) or one of its
variants instead, so interest_expense ended up at only 492/2500
(20%) annual coverage in financial_statements, and interest_coverage_ttm
correspondingly low (198/2500 = 7.9%).

The ACCOUNT_PRIORITY map added to ingest_dart.py now accepts those
broader concepts as fallbacks. But adding mappings doesn't update
the existing rows in financial_statements — those were parsed before
the new mapping existed. To update them, we'd have to re-run the full
DART ingest, which is hours.

This script does a focused re-fetch: ONLY the (ticker, year, report)
filings that currently have NULL interest_expense get re-fetched.
Skips filings that already have a value. Updates just the
interest_expense column on the matched row. Doesn't touch other
fields.

After this script runs:
  1. Re-run normalize_dart_financials.py so fundamental_snapshots
     picks up the new interest_expense values.
  2. Re-run calculate_factors + run_ranking_snapshot.

Usage
-----
    python refresh_dart_interest_expense.py --dry-run
    python refresh_dart_interest_expense.py
    python refresh_dart_interest_expense.py --tickers 005930,000660
    python refresh_dart_interest_expense.py --years 2024,2025 --rate-limit 1.0
    python refresh_dart_interest_expense.py --limit 100 --dry-run
"""

import os
import sys
import argparse
import time
from datetime import datetime

import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv

# Re-use the existing DART machinery, including the now-expanded
# ACCOUNT_MAP / ACCOUNT_PRIORITY.
from ingest_dart import (
    fetch_financials,
    download_corp_codes,
    REPORT_TYPES,
)

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "refresh_dart_interest_expense"


# ---------------------------------------------------------------------------
# Logging
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
        (status, rows_processed, rows_inserted, rows_updated,
         rows_skipped, error_message, log_id),
    )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Target discovery
# ---------------------------------------------------------------------------

def find_targets(conn, tickers_filter=None, years_filter=None, limit=None):
    """Return DEDUPED list of (ticker, fiscal_year, report_code,
    consolidated_or_separate) for filings with NULL interest_expense.

    A filing (ticker + year + report_code + consolidation flavor) can
    have multiple rows in financial_statements when DART filings get
    amended (정정공시). We only need to fetch DART once per unique
    filing; the UPDATE pass uses a WHERE clause that will hit all
    matching rows (so amendments get filled with the same value)."""
    cur = conn.cursor()
    sql = (
        "SELECT DISTINCT ticker, fiscal_year, "
        "CASE statement_type "
        "  WHEN 'annual'    THEN '11011' "
        "  WHEN 'quarterly' THEN "
        "    CASE fiscal_quarter "
        "      WHEN 1 THEN '11013' "
        "      WHEN 2 THEN '11012' "
        "      WHEN 3 THEN '11014' "
        "      ELSE '11011' END "
        "  ELSE '11011' END AS report_code, "
        "consolidated_or_separate "
        "FROM financial_statements "
        "WHERE interest_expense IS NULL "
    )
    params = []
    if tickers_filter:
        sql += " AND ticker = ANY(%s)"
        params.append(tickers_filter)
    if years_filter:
        sql += " AND fiscal_year = ANY(%s)"
        params.append(years_filter)
    sql += " ORDER BY ticker, fiscal_year, report_code"
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def update_interest_expense(conn, ticker, year, report_code,
                              consolidated, value):
    """Update interest_expense on the matching row in
    financial_statements. Returns rows-affected."""
    if value is None:
        return 0
    # Map report_code -> (statement_type, fiscal_quarter)
    if report_code == "11011":
        stype, fq = "annual", None
    elif report_code == "11013":
        stype, fq = "quarterly", 1
    elif report_code == "11012":
        stype, fq = "quarterly", 2
    elif report_code == "11014":
        stype, fq = "quarterly", 3
    else:
        return 0

    cur = conn.cursor()
    if fq is None:
        cur.execute(
            "UPDATE financial_statements "
            "SET interest_expense = %s "
            "WHERE ticker = %s AND fiscal_year = %s "
            "  AND statement_type = %s AND fiscal_quarter IS NULL "
            "  AND consolidated_or_separate = %s",
            (value, ticker, year, stype, consolidated),
        )
    else:
        cur.execute(
            "UPDATE financial_statements "
            "SET interest_expense = %s "
            "WHERE ticker = %s AND fiscal_year = %s "
            "  AND statement_type = %s AND fiscal_quarter = %s "
            "  AND consolidated_or_separate = %s",
            (value, ticker, year, stype, fq, consolidated),
        )
    n = cur.rowcount
    conn.commit()
    cur.close()
    return n


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Re-fetch DART filings to fill NULL interest_expense "
                    "in financial_statements, using the expanded "
                    "ACCOUNT_MAP / ACCOUNT_PRIORITY from ingest_dart.py.")
    parser.add_argument("--tickers",
                        help="Comma-separated tickers to restrict to.")
    parser.add_argument("--years",
                        help="Comma-separated fiscal years to restrict to "
                             "(e.g. 2023,2024,2025).")
    parser.add_argument("--limit", type=int,
                        help="Max rows to process.")
    parser.add_argument("--timeout", type=int, default=30,
                        help="Per-request timeout (seconds).")
    parser.add_argument("--rate-limit", type=float, default=0.8,
                        help="Seconds between DART API calls.")
    parser.add_argument("--retry", type=int, default=2,
                        help="Per-fetch retry count.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be re-fetched, don't write.")
    args = parser.parse_args()

    tickers_filter = None
    if args.tickers:
        tickers_filter = [t.strip() for t in args.tickers.split(",")
                          if t.strip()]
    years_filter = None
    if args.years:
        years_filter = [int(y.strip()) for y in args.years.split(",")
                          if y.strip()]

    conn = psycopg2.connect(DATABASE_URL)

    print("=" * 70)
    print("DART interest_expense refresh")
    print("  Tickers filter: {0}".format(args.tickers or "(none)"))
    print("  Years filter:   {0}".format(args.years or "(none)"))
    print("  Limit:          {0}".format(args.limit or "(none)"))
    print("  Dry-run:        {0}".format(args.dry_run))
    print("  Rate-limit:     {0}s".format(args.rate_limit))
    print("=" * 70)

    targets = find_targets(conn, tickers_filter, years_filter, args.limit)
    print("\nTargets: {0} filing rows with NULL interest_expense".format(
        len(targets)))
    if not targets:
        print("Nothing to do.")
        conn.close()
        sys.exit(0)

    # Discover corp_code mapping once.
    print("\nLoading DART corp codes...")
    corp_code_map = download_corp_codes(args.timeout)
    print("  Loaded {0} corp codes".format(len(corp_code_map)))

    log_id = log_start(conn, {
        "tickers": args.tickers, "years": args.years,
        "limit": args.limit, "dry_run": args.dry_run,
    })

    n_processed = 0
    n_filled = 0
    n_no_filing = 0
    n_no_value = 0
    n_errors = 0
    n_404 = 0

    # Group by ticker to print progress meaningfully
    last_ticker = None
    try:
        for i, (ticker, year, report_code, consolidated) in enumerate(targets):
            if ticker != last_ticker:
                print("\n[{0}/{1}] {2}".format(i + 1, len(targets), ticker))
                last_ticker = ticker
            corp = corp_code_map.get(ticker)
            if not corp:
                print("  {0} {1}: no corp_code".format(year, report_code))
                n_no_filing += 1
                continue
            try:
                result, status = fetch_financials(
                    ticker, corp, year, report_code,
                    args.timeout, retry_count=args.retry,
                )
            except Exception as e:
                print("  {0} {1}: fetch err: {2}".format(
                    year, report_code, str(e)[:80]))
                n_errors += 1
                time.sleep(args.rate_limit)
                continue

            if result is None:
                # Common path: no filing / no data
                if "no report" in (status or "") or "no data" in (status or ""):
                    n_no_filing += 1
                else:
                    n_errors += 1
                print("  {0} {1}: {2}".format(year, report_code, status))
                time.sleep(args.rate_limit)
                continue

            ie = result.get("interest_expense")
            if ie is None:
                n_no_value += 1
                print("  {0} {1}: no interest_expense in filing".format(
                    year, report_code))
                time.sleep(args.rate_limit)
                continue

            # We have a value to write
            if args.dry_run:
                print("  {0} {1}: DRY-RUN would set interest_expense="
                      "{2:,} ({3})".format(
                          year, report_code, int(ie), consolidated))
                n_filled += 1
            else:
                rows = update_interest_expense(
                    conn, ticker, year, report_code, consolidated, ie)
                if rows > 0:
                    print("  {0} {1}: set interest_expense={2:,} "
                          "({3}, {4} row)".format(
                              year, report_code, int(ie),
                              consolidated, rows))
                    n_filled += 1
                else:
                    print("  {0} {1}: no matching row to update".format(
                        year, report_code))
                    n_404 += 1

            n_processed += 1
            time.sleep(args.rate_limit)

        print("\n" + "=" * 70)
        print("Summary")
        print("  Targets:                       {0}".format(len(targets)))
        print("  Processed (fetched):           {0}".format(n_processed))
        print("  Filled / would fill:           {0}".format(n_filled))
        print("  No filing / no data on DART:   {0}".format(n_no_filing))
        print("  Filing had no interest line:   {0}".format(n_no_value))
        print("  Fetch errors:                  {0}".format(n_errors))
        print("  No matching DB row to update:  {0}".format(n_404))
        print("=" * 70)

        log_finish(conn, log_id, "success",
                   rows_processed=n_processed,
                   rows_updated=n_filled,
                   rows_skipped=n_no_filing + n_no_value)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        log_finish(conn, log_id, "interrupted",
                   rows_processed=n_processed,
                   rows_updated=n_filled,
                   rows_skipped=n_no_filing + n_no_value,
                   error_message="KeyboardInterrupt")
        raise
    except Exception as e:
        log_finish(conn, log_id, "error",
                   rows_processed=n_processed,
                   error_message=str(e))
        print("ERROR: {0}".format(e))
        raise
    finally:
        conn.close()

    print("\nDone!")
    if not args.dry_run and n_filled > 0:
        print("\nNext steps:")
        print("  1. python normalize_dart_financials.py")
        print("  2. python calculate_factors.py "
              "--universe krx_all_current --as-of-date "
              "$ASOF --allow-snapshot-market-cap")
        print("  3. python run_ranking_snapshot.py "
              "--universe krx_all_current --as-of-date $ASOF "
              "--missing-category-policy neutral "
              "--min-active-weight-coverage 0.60 "
              "--min-category-count 3 --min-factor-count 10 "
              "--allow-snapshot-market-cap")
