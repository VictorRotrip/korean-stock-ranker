#!/usr/bin/env python3
"""Detect corrected DART filings market-wide and refresh our data for them.

Companies sometimes refile a corrected periodic report (report name tagged
'정정') after we've already ingested the original — e.g. 063760 restated its
Q1 cash flows. This scans DART's recent periodic-filing list for corrections,
and for any that affect a ticker we track, re-fetches that period's financials,
overwrites our stored values, and stamps corrected_at so the dashboard can flag
"recently corrected".

Efficient: a handful of market-wide `list` calls, not one call per company.

Usage:
    python refresh_corrections.py --days 14
"""

import os
import sys
import re
import time
import argparse
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import psycopg2
from dotenv import load_dotenv

from ingest_dart import dart_api_call, fetch_financials, upsert_financials

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
DART_API_KEY = os.getenv("DART_API_KEY")
if not DATABASE_URL or not DART_API_KEY:
    print("ERROR: DATABASE_URL and DART_API_KEY must both be set.", flush=True)
    sys.exit(1)


def parse_report(report_nm):
    """Map a Korean report name to (year, report_code, fiscal_quarter)."""
    m = re.search(r"(\d{4})\.(\d{2})", report_nm)
    if not m:
        return None
    year = int(m.group(1))
    month = m.group(2)
    if "사업보고서" in report_nm:       # annual
        return (year, "11011", None)
    if "반기보고서" in report_nm:       # half-year
        return (year, "11012", 2)
    if "분기보고서" in report_nm:       # quarterly
        if month == "03":
            return (year, "11013", 1)
        if month == "09":
            return (year, "11014", 3)
        if month == "06":
            return (year, "11012", 2)
    return None


def _list_window(bgn, end, timeout):
    """Paginate one date window of periodic filings. DART status codes:
    000=ok, 013=no data (not an error)."""
    out = []
    page = 1
    while True:
        data = dart_api_call("list", {
            "bgn_de": bgn,
            "end_de": end,
            "pblntf_ty": "A",
            "page_no": page,
            "page_count": 100,
        }, timeout)
        if not data:
            print("    {0}..{1}: no response from DART".format(bgn, end), flush=True)
            break
        status = data.get("status")
        if status == "013":            # no data in this window — fine
            break
        if status != "000":
            print("    {0}..{1}: DART status {2} ({3})".format(
                bgn, end, status, data.get("message", "")), flush=True)
            break
        out.extend(data.get("list", []))
        total_page = int(data.get("total_page", 1) or 1)
        if page >= total_page:
            break
        page += 1
        time.sleep(0.3)
    return out


def list_recent_filings(bgn, end, timeout):
    """All periodic-report filings in [bgn, end]. The DART `list` endpoint caps
    the range at ~3 months when no corp_code is given, so we chunk into <=80-day
    windows and aggregate."""
    bgn_d = datetime.strptime(bgn, "%Y%m%d")
    end_d = datetime.strptime(end, "%Y%m%d")
    out = []
    cur = bgn_d
    while cur <= end_d:
        win_end = min(cur + timedelta(days=80), end_d)
        out.extend(_list_window(cur.strftime("%Y%m%d"), win_end.strftime("%Y%m%d"), timeout))
        cur = win_end + timedelta(days=1)
    return out


def known_tickers(conn):
    cur = conn.cursor()
    cur.execute("SELECT ticker FROM stocks")
    s = {r[0] for r in cur.fetchall()}
    cur.close()
    return s


def main():
    ap = argparse.ArgumentParser(description="Refresh corrected DART filings")
    ap.add_argument("--days", type=int, default=14, help="Look-back window")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--rate-limit", type=float, default=1.0)
    args = ap.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    tickers = known_tickers(conn)

    now = datetime.now(timezone.utc)
    end = now.strftime("%Y%m%d")
    bgn = (now - timedelta(days=args.days)).strftime("%Y%m%d")
    print("Scanning DART periodic filings {0}..{1} for corrections...".format(bgn, end), flush=True)

    filings = list_recent_filings(bgn, end, args.timeout)
    print("  {0} periodic filings in window".format(len(filings)), flush=True)

    corrections = [f for f in filings if "정정" in (f.get("report_nm") or "")]
    print("  {0} correction filings (정정)".format(len(corrections)), flush=True)

    refreshed = 0
    skipped = 0
    for f in corrections:
        stock_code = (f.get("stock_code") or "").strip()
        corp_code = (f.get("corp_code") or "").strip()
        report_nm = f.get("report_nm") or ""
        rcept_no = f.get("rcept_no")
        rcept_dt = f.get("rcept_dt")  # YYYYMMDD

        if not stock_code or stock_code not in tickers:
            continue
        parsed = parse_report(report_nm)
        if not parsed:
            continue
        year, report_code, quarter = parsed

        try:
            result, status = fetch_financials(stock_code, corp_code, year, report_code, args.timeout)
        except Exception as e:
            print("  {0} {1}: fetch error {2}".format(stock_code, report_nm, str(e)[:50]), flush=True)
            continue
        if not result:
            skipped += 1
            continue

        upsert_financials(conn, [result], overwrite=True)

        corr_date = None
        if rcept_dt and len(rcept_dt) == 8:
            corr_date = "{0}-{1}-{2}".format(rcept_dt[0:4], rcept_dt[4:6], rcept_dt[6:8])

        cur = conn.cursor()
        cur.execute("""
            UPDATE financial_statements
            SET receipt_no = %s, corrected_at = %s
            WHERE ticker = %s AND fiscal_year = %s
              AND fiscal_quarter IS NOT DISTINCT FROM %s
              AND consolidated_or_separate = %s
        """, (rcept_no, corr_date, stock_code, year, quarter,
              result.get("consolidated_or_separate", "consolidated")))
        conn.commit()
        cur.close()

        refreshed += 1
        print("  refreshed {0} {1} (corrected {2})".format(stock_code, report_nm, corr_date), flush=True)
        time.sleep(args.rate_limit)

    print("Done. Refreshed {0} corrected filings; {1} skipped (no data).".format(refreshed, skipped), flush=True)
    conn.close()


if __name__ == "__main__":
    main()
