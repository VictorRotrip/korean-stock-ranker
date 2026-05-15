"""
Diagnose one stock end-to-end:

  1. Show its row in `financial_statements` (most recent annual).
  2. Fetch the same filing directly from DART and dump rows by sj_div bucket,
     so we can compare what DART returns vs what we managed to parse.

Useful when a stock shows "N/A" for fields like total_debt, operating_cash_flow,
or EPS — we can see whether DART has the data and our parser missed it.

Usage:
    python diag_stock.py 213500
    python diag_stock.py 213500 --year 2025
"""

import os
import sys
import argparse
from datetime import date

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
DART_API_KEY = os.getenv("DART_API_KEY") or os.getenv("OPENDART_API_KEY")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set"); sys.exit(1)
if not DART_API_KEY:
    print("ERROR: DART_API_KEY not set"); sys.exit(1)


def db_row(ticker):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT period_end, filing_date, statement_type,
               revenue, gross_profit, operating_income, net_income, eps,
               total_assets, total_liabilities, total_equity, total_debt,
               cash, current_assets, current_liabilities,
               operating_cash_flow, capital_expenditure, free_cash_flow,
               dividends_paid, ebitda, interest_expense, depreciation,
               book_value_per_share, shares_outstanding
        FROM financial_statements
        WHERE ticker = %s AND statement_type = 'annual'
        ORDER BY period_end DESC LIMIT 3
    """, (ticker,))
    fields = [d.name for d in cur.description]
    rows = cur.fetchall()
    conn.close()
    return fields, rows


def corp_code_for(ticker):
    """Read the cached DART corp_codes file written by ingest_dart.py."""
    import json
    cache_path = os.path.join(os.path.dirname(__file__), ".cache",
                              "dart_corp_codes.json")
    if not os.path.exists(cache_path):
        # legacy path
        cache_path = os.path.join(os.path.dirname(__file__),
                                  ".dart_corp_codes.json")
    if not os.path.exists(cache_path):
        return None
    with open(cache_path) as f:
        m = json.load(f)
    return m.get(ticker)


def fetch_dart_filing(corp_code, year):
    """Hit fnlttSinglAcntAll for the annual filing and return rows."""
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    for fs_div in ("CFS", "OFS"):
        r = requests.get(url, params={
            "crtfc_key": DART_API_KEY,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": "11011",
            "fs_div": fs_div,
        }, timeout=30)
        if r.status_code != 200:
            continue
        data = r.json()
        if data.get("status") != "000":
            continue
        rows = data.get("list", []) or []
        if rows:
            return fs_div, rows
    return None, []


def main():
    p = argparse.ArgumentParser()
    p.add_argument("ticker")
    p.add_argument("--year", type=int, default=date.today().year - 1,
                   help="Fiscal year to inspect (default: last year)")
    args = p.parse_args()

    # ---------- DB row ----------
    print("=" * 78)
    print("DATABASE — financial_statements rows for {0}".format(args.ticker))
    print("=" * 78)
    fields, rows = db_row(args.ticker)
    if not rows:
        print("  (no rows)")
    for row in rows:
        print()
        for f, v in zip(fields, row):
            shown = "NULL" if v is None else "{:,}".format(v) if isinstance(v, int) else str(v)
            print("  {0:30s} {1}".format(f, shown))

    # ---------- DART raw filing ----------
    corp = corp_code_for(args.ticker)
    if not corp:
        print("\nWARN: couldn't resolve corp_code for {0} — skipping DART pull"
              .format(args.ticker))
        return

    print("\n" + "=" * 78)
    print("DART — direct fnlttSinglAcntAll  {0}  bsns_year={1}  reprt_code=11011"
          .format(args.ticker, args.year))
    print("=" * 78)
    fs_div, raws = fetch_dart_filing(corp, args.year)
    if not raws:
        print("  (no data returned)")
        return
    print("  fs_div={0}  total rows: {1}".format(fs_div, len(raws)))

    # Bucket by sj_div for readability
    buckets = {}
    for r in raws:
        sj = r.get("sj_div") or "(none)"
        buckets.setdefault(sj, []).append(r)

    bucket_labels = {
        "BS": "BS  (Balance Sheet)",
        "IS": "IS  (Income Statement)",
        "CIS": "CIS (Comprehensive Income)",
        "CF": "CF  (Cash Flow)",
        "SCE": "SCE (Statement of Changes in Equity) — usually ignored",
    }

    for sj in ("BS", "IS", "CIS", "CF", "SCE", "(none)"):
        rs = buckets.get(sj, [])
        if not rs:
            continue
        print("\n  --- {0} : {1} rows ---".format(
            bucket_labels.get(sj, sj), len(rs)))
        for r in rs:
            amt = r.get("thstrm_amount") or "-"
            fsd = r.get("fs_div") or "(empty)"
            print("    sj={0:<4} fs={1:<8} {2:<32} {3}".format(
                sj, fsd, (r.get("account_nm") or "")[:32], amt))


if __name__ == "__main__":
    main()
