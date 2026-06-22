#!/usr/bin/env python3
"""Audit stored financials against a fresh DART fetch — field by field.

For each ticker it re-fetches the primary financial statements from DART (using
the same parser the ingest uses), loads the matching row from our
financial_statements table, and prints a side-by-side diff of every mapped
field with a MATCH / MISMATCH flag. It also compares DART's current receipt_no
against the one we have on record, which catches the "we ingested a different
(or corrected) filing" case.

Needs DART API access + network, so run it on your machine (not the sandbox).

Usage:
    python audit_dart.py --tickers 063760 --year 2026 --reports 11013
    python audit_dart.py --tickers 005930,000660 --year 2025 --reports 11011
    python audit_dart.py --universe krx_all_current --limit 25 --year 2026 --reports 11013
"""

import os
import sys
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import psycopg2
from dotenv import load_dotenv

from ingest_dart import (
    download_corp_codes,
    resolve_corp_code,
    fetch_financials,
)

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.", flush=True)
    sys.exit(1)

# Fields worth auditing (numeric statement lines stored in financial_statements).
FIELDS = [
    "revenue", "cost_of_revenue", "gross_profit", "operating_income",
    "net_income", "eps", "total_assets", "total_liabilities", "total_equity",
    "current_assets", "current_liabilities", "cash", "total_debt",
    "operating_cash_flow", "capital_expenditure", "free_cash_flow",
    "dividends_paid", "ebitda", "interest_expense", "depreciation",
    "shares_outstanding", "book_value_per_share",
]

REPORT_QUARTER = {"11013": 1, "11012": 2, "11014": 3, "11011": None}


def rel_diff(a, b):
    if a is None or b is None:
        return None
    if a == 0 and b == 0:
        return 0.0
    denom = max(abs(a), abs(b), 1.0)
    return abs(a - b) / denom


def db_row(conn, ticker, year, quarter, consolidated):
    cur = conn.cursor()
    cols = ", ".join(FIELDS)
    q = """
        SELECT {0}
        FROM financial_statements
        WHERE ticker=%s AND fiscal_year=%s
          AND consolidated_or_separate=%s
          AND fiscal_quarter IS NOT DISTINCT FROM %s
        LIMIT 1
    """.format(cols)
    cur.execute(q, (ticker, year, consolidated, quarter))
    row = cur.fetchone()
    cur.close()
    if not row:
        return None
    return {f: row[i] for i, f in enumerate(FIELDS)}


def db_latest_receipt(conn, ticker):
    cur = conn.cursor()
    cur.execute("""
        SELECT receipt_no FROM dart_filings
        WHERE ticker=%s AND receipt_no IS NOT NULL
        ORDER BY filing_date DESC LIMIT 1
    """, (ticker,))
    r = cur.fetchone()
    cur.close()
    return r[0] if r else None


def audit_ticker(conn, ticker, corp_code, year, report, timeout):
    quarter = REPORT_QUARTER.get(report)
    try:
        dart, status = fetch_financials(ticker, corp_code, year, report, timeout)
    except Exception as e:
        print("  {0}: DART fetch error: {1}".format(ticker, str(e)[:80]), flush=True)
        return
    if not dart:
        print("  {0}: no DART data ({1})".format(ticker, status), flush=True)
        return

    cons = dart.get("consolidated_or_separate", "consolidated")
    db = db_row(conn, ticker, year, quarter, cons)

    print("\n=== {0}  FY{1} {2} ({3}) ===".format(ticker, year, report, cons), flush=True)
    dart_rcp = dart.get("receipt_no")
    db_rcp = db_latest_receipt(conn, ticker)
    flag = "" if dart_rcp == db_rcp else "  <-- DIFFERENT FILING"
    print("  receipt_no: DART={0}  DB={1}{2}".format(dart_rcp, db_rcp, flag), flush=True)
    if db is None:
        print("  (no matching DB row)", flush=True)
        return

    print("  {0:<24} {1:>22} {2:>22}  {3}".format("field", "DART", "DB", "status"), flush=True)
    mismatches = 0
    for f in FIELDS:
        dv = dart.get(f)
        bv = db.get(f)
        if dv is None and bv is None:
            continue
        rd = rel_diff(dv, bv)
        if rd is None:
            tag = "one-missing"
        elif rd <= 0.005:
            tag = "ok"
        else:
            tag = "MISMATCH ({:.0%})".format(rd)
            mismatches += 1
        dvs = "{:,.0f}".format(dv) if isinstance(dv, (int, float)) else str(dv)
        bvs = "{:,.0f}".format(bv) if isinstance(bv, (int, float)) else str(bv)
        print("  {0:<24} {1:>22} {2:>22}  {3}".format(f, dvs, bvs, tag), flush=True)
    print("  -> {0} mismatched field(s)".format(mismatches), flush=True)


def main():
    ap = argparse.ArgumentParser(description="Audit stored financials vs DART")
    ap.add_argument("--tickers", help="Comma-separated tickers")
    ap.add_argument("--universe", help="Universe name (uses universe_memberships)")
    ap.add_argument("--limit", type=int, help="Cap number of tickers")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--reports", default="11013", help="Report code: 11011/11013/11012/11014")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    conn = psycopg2.connect(DATABASE_URL)

    tickers = []
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    elif args.universe:
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker FROM universe_memberships WHERE universe_name=%s
            ORDER BY ticker
        """, (args.universe,))
        tickers = [r[0] for r in cur.fetchall()]
        cur.close()
    else:
        print("ERROR: provide --tickers or --universe", flush=True)
        sys.exit(1)

    if args.limit:
        tickers = tickers[:args.limit]

    print("Resolving corp codes...", flush=True)
    corp_map = download_corp_codes(args.timeout)

    report = args.reports.split(",")[0].strip()
    for t in tickers:
        corp = resolve_corp_code(t, corp_map)
        if not corp:
            print("  {0}: no corp_code".format(t), flush=True)
            continue
        audit_ticker(conn, t, corp, args.year, report, args.timeout)

    conn.close()


if __name__ == "__main__":
    main()
