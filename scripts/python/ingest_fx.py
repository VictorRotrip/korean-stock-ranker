#!/usr/bin/env python3
"""Ingest the USD/KRW exchange rate into the fx_rates table.

Korean figures (market cap, liquidity) are stored in won; the web UI shows an
approximate USD equivalent. This keeps that conversion accurate by pulling the
daily USD/KRW close from FinanceDataReader (works from non-Korea IPs, unlike
KRX/pykrx) and upserting it into fx_rates.

Usage:
    python ingest_fx.py                 # last ~10 days, upsert
    python ingest_fx.py --days 30
"""

import os
import sys
import argparse
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.", flush=True)
    sys.exit(1)

PAIR = "USD/KRW"


def fetch_usdkrw(days):
    import FinanceDataReader as fdr
    start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    df = fdr.DataReader("USD/KRW", start)
    if df is None or len(df) == 0 or "Close" not in df.columns:
        return []
    df = df.reset_index()
    date_col = next((c for c in ("Date", "index", "date") if c in df.columns), df.columns[0])
    rows = []
    for _, r in df.iterrows():
        close = r.get("Close")
        if close is None or close != close or float(close) <= 0:  # NaN / nonpositive
            continue
        d = r[date_col]
        d_iso = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        rows.append((PAIR, d_iso, float(close), "fdr"))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Ingest USD/KRW into fx_rates")
    ap.add_argument("--days", type=int, default=10, help="Look-back window in days")
    args = ap.parse_args()

    print("Fetching {0} (last {1} days) from FinanceDataReader...".format(PAIR, args.days), flush=True)
    rows = fetch_usdkrw(args.days)
    if not rows:
        print("ERROR: no USD/KRW data returned.", flush=True)
        sys.exit(1)

    latest = max(rows, key=lambda x: x[1])
    print("  {0} rows; latest {1} = {2:.2f}".format(len(rows), latest[1], latest[2]), flush=True)

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO fx_rates (pair, date, rate, source)
        VALUES %s
        ON CONFLICT (pair, date) DO UPDATE SET
            rate = EXCLUDED.rate,
            source = EXCLUDED.source,
            updated_at = now()
    """, rows)
    conn.commit()
    cur.close()
    conn.close()
    print("Done. Upserted {0} USD/KRW rows.".format(len(rows)), flush=True)


if __name__ == "__main__":
    main()
