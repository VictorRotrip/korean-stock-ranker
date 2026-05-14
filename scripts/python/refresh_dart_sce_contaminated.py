"""
Targeted refresh: re-fetch DART filings whose net_income was zeroed by
the SCE (Statement of Changes in Equity) contamination bug.

Why this exists
---------------
The original ingest_dart.py ACCOUNT_MAP loop matched line items by
account_nm alone, with no filter on sj_div (statement-type code).
The Korean label 당기순이익 ("net income") appears in BOTH the
Income Statement and the Statement of Changes in Equity, but in
SCE it is often a section header row with thstrm_amount = "0".
That zero overwrote the real IS value, corrupting net_income on
~1500 stocks including Samsung Electronics (005930), Kia (000270),
SK Hynix (000660), POSCO Holdings (005490), SK Telecom (017670),
and many others.

The fix in ingest_dart.py added a FIELD_TO_STATEMENT filter so income
fields only accept IS / CIS rows. But that fix only affects future
fetches — existing rows are still corrupted. This script re-fetches
filings where net_income = 0 but revenue and operating_income are
nonzero (the smoking-gun pattern) and rewrites all parsed fields.

After this script runs:
  1. Re-run normalize_dart_financials.py so fundamental_snapshots
     picks up the new values.
  2. Re-run calculate_factors + run_ranking_snapshot.

Usage
-----
    python refresh_dart_sce_contaminated.py --dry-run
    python refresh_dart_sce_contaminated.py
    python refresh_dart_sce_contaminated.py --years 2024,2025
    python refresh_dart_sce_contaminated.py --tickers 005930,000270
    python refresh_dart_sce_contaminated.py --limit 50 --dry-run
"""

import os
import sys
import argparse
import time

import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv

from ingest_dart import (
    fetch_financials,
    download_corp_codes,
)

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "refresh_dart_sce_contaminated"

# Fields the parser writes that could have been SCE-contaminated.
# We overwrite each one with the freshly-parsed value when present.
WRITABLE_FIELDS = (
    "revenue", "cost_of_revenue", "gross_profit",
    "operating_income", "net_income", "eps",
    "total_assets", "total_liabilities", "total_equity",
    "current_assets", "current_liabilities", "cash", "total_debt",
    "operating_cash_flow", "capital_expenditure", "free_cash_flow",
    "dividends_paid", "ebitda", "interest_expense", "depreciation",
    "shares_outstanding",
)


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
# Target discovery — the SCE smoking-gun pattern
# ---------------------------------------------------------------------------

def find_targets(conn, tickers_filter=None, years_filter=None, limit=None):
    """Return DEDUPED list of (ticker, fiscal_year, report_code,
    consolidated_or_separate) where net_income = 0 but revenue or
    operating_income is positive — i.e. clearly wrong, the SCE
    contamination."""
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
        "WHERE net_income = 0 "
        "  AND (revenue > 0 OR operating_income > 0) "
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


def update_record(conn, ticker, year, report_code, consolidated, result):
    """Overwrite WRITABLE_FIELDS on the matching row(s) of
    financial_statements with the freshly-parsed values. Returns
    rows-affected."""
    if not result:
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

    # Build SET ... = %s for every WRITABLE_FIELDS key present in result.
    # We overwrite (not COALESCE) because the existing 0s are wrong.
    fields_to_set = [(f, result.get(f)) for f in WRITABLE_FIELDS
                     if f in result]
    if not fields_to_set:
        return 0

    set_clause = ", ".join("{0} = %s".format(f) for f, _ in fields_to_set)
    values = [v for _, v in fields_to_set]

    cur = conn.cursor()
    if fq is None:
        sql = (
            "UPDATE financial_statements SET " + set_clause +
            " WHERE ticker = %s AND fiscal_year = %s "
            "  AND statement_type = %s AND fiscal_quarter IS NULL "
            "  AND consolidated_or_separate = %s"
        )
        cur.execute(sql, values + [ticker, year, stype, consolidated])
    else:
        sql = (
            "UPDATE financial_statements SET " + set_clause +
            " WHERE ticker = %s AND fiscal_year = %s "
            "  AND statement_type = %s AND fiscal_quarter = %s "
            "  AND consolidated_or_separate = %s"
        )
        cur.execute(sql, values + [ticker, year, stype, fq, consolidated])
    n = cur.rowcount
    conn.commit()
    cur.close()
    return n


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Re-fetch DART filings where net_income was zeroed "
                    "by the SCE contamination bug (net_income = 0 but "
                    "revenue/operating_income > 0).")
    parser.add_argument("--tickers",
                        help="Comma-separated tickers to restrict to.")
    parser.add_argument("--years",
                        help="Comma-separated fiscal years to restrict to "
                             "(e.g. 2024,2025).")
    parser.add_argument("--limit", type=int,
                        help="Max filings to process.")
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
    print("DART SCE-contamination refresh")
    print("  Pattern: net_income = 0 AND (revenue > 0 OR operating_income > 0)")
    print("  Tickers filter: {0}".format(args.tickers or "(none)"))
    print("  Years filter:   {0}".format(args.years or "(none)"))
    print("  Limit:          {0}".format(args.limit or "(none)"))
    print("  Dry-run:        {0}".format(args.dry_run))
    print("  Rate-limit:     {0}s".format(args.rate_limit))
    print("=" * 70)

    targets = find_targets(conn, tickers_filter, years_filter, args.limit)
    print("\nTargets: {0} filings to re-fetch".format(len(targets)))
    if not targets:
        print("Nothing to do.")
        conn.close()
        sys.exit(0)

    print("\nLoading DART corp codes...")
    corp_code_map = download_corp_codes(args.timeout)
    print("  Loaded {0} corp codes".format(len(corp_code_map)))

    log_id = log_start(conn, {
        "tickers": args.tickers, "years": args.years,
        "limit": args.limit, "dry_run": args.dry_run,
    })

    n_processed = 0
    n_updated = 0
    n_no_filing = 0
    n_no_data = 0
    n_no_rows = 0
    n_errors = 0

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
                print("  {0} {1}: ERROR {2}".format(year, report_code, e))
                n_errors += 1
                time.sleep(args.rate_limit)
                continue
            n_processed += 1

            if not result:
                print("  {0} {1}: {2}".format(year, report_code, status))
                n_no_data += 1
                time.sleep(args.rate_limit)
                continue

            new_ni = result.get("net_income")
            print("  {0} {1}: net_income {2} -> {3}".format(
                year, report_code,
                "0", new_ni if new_ni is not None else "NULL"))

            if not args.dry_run:
                n = update_record(conn, ticker, year, report_code,
                                  consolidated, result)
                if n > 0:
                    n_updated += 1
                else:
                    n_no_rows += 1

            time.sleep(args.rate_limit)

        status = "success"
        err = None
    except KeyboardInterrupt:
        status = "interrupted"
        err = "user cancelled"
        print("\n[INTERRUPTED] partial progress will be saved.")
    except Exception as e:
        status = "error"
        err = str(e)
        print("\n[ERROR] {0}".format(e))

    log_finish(conn, log_id, status,
               rows_processed=n_processed,
               rows_inserted=0,
               rows_updated=n_updated,
               rows_skipped=n_no_filing + n_no_data,
               error_message=err)

    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print("  Targets:                       {0}".format(len(targets)))
    print("  Processed (fetched):           {0}".format(n_processed))
    print("  Updated:                       {0}".format(n_updated))
    print("  No filing / no corp_code:      {0}".format(n_no_filing))
    print("  Filing had no data on DART:    {0}".format(n_no_data))
    print("  No matching DB row to update:  {0}".format(n_no_rows))
    print("  Fetch errors:                  {0}".format(n_errors))
    print("=" * 70)
    conn.close()

    print("\nNext steps:")
    print("  1. python normalize_dart_financials.py")
    print("  2. python calculate_factors.py --universe krx_all_current "
          "--as-of-date $ASOF --allow-snapshot-market-cap")
    print("  3. python run_ranking_snapshot.py --universe krx_all_current "
          "--as-of-date $ASOF \\")
    print("       --missing-category-policy neutral "
          "--min-active-weight-coverage 0.60 \\")
    print("       --min-category-count 3 --min-factor-count 10 "
          "--allow-snapshot-market-cap")
    print("\nDone!")
