"""
Ingest short selling data from pykrx into Supabase Postgres.

Note: pykrx 1.0.51 broke the market-wide short selling APIs.
This script tries the per-ticker API. If that also fails, it skips gracefully.

Also note: Korean short selling was banned Nov 2023 - Mar 2025 for most stocks.

Usage:
    python ingest_short_selling.py --tickers 005930,000660 --date 2025-04-15
    python ingest_short_selling.py --start-date 2025-04-01 --end-date 2025-04-30
    python ingest_short_selling.py --limit 20 --start-date 2025-04-01
"""

import os
import sys
import argparse
import time
from datetime import datetime, timedelta

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "ingest_short_selling"


def log_start(conn, params=None):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ingestion_log (script_name, parameters) VALUES (%s, %s) RETURNING id",
        (SCRIPT_NAME, Json(params)),
    )
    log_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    return log_id


def log_finish(conn, log_id, status, rows_processed=0, rows_inserted=0,
               rows_updated=0, rows_skipped=0, error_message=None):
    cur = conn.cursor()
    cur.execute(
        """UPDATE ingestion_log
           SET finished_at = NOW(), status = %s,
               rows_processed = %s, rows_inserted = %s,
               rows_updated = %s, rows_skipped = %s, error_message = %s
           WHERE id = %s""",
        (status, rows_processed, rows_inserted, rows_updated,
         rows_skipped, error_message, log_id),
    )
    conn.commit()
    cur.close()


def normalize_date(date_str):
    return date_str.replace("-", "")


def to_iso(yyyymmdd):
    s = yyyymmdd.replace("-", "")
    return "{}-{}-{}".format(s[:4], s[4:6], s[6:8])


def get_db_tickers(conn, limit=None):
    cur = conn.cursor()
    q = "SELECT ticker FROM stocks WHERE is_active = TRUE ORDER BY ticker"
    if limit:
        q += " LIMIT {}".format(int(limit))
    cur.execute(q)
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    return tickers


def is_short_selling_ban_period(start, end):
    """Check if entire date range falls in the ban (Nov 6 2023 - Mar 30 2025)."""
    s = int(start.replace("-", "")[:8])
    e = int(end.replace("-", "")[:8])
    return s >= 20231106 and e <= 20250330


# ---------------------------------------------------------------------------
# Per-ticker short selling (pykrx 1.0.51)
# ---------------------------------------------------------------------------

def fetch_short_selling_per_ticker(ticker, start, end):
    """Try pykrx per-ticker short selling volume.
    Returns DataFrame or empty."""
    from pykrx import stock

    try:
        df = stock.get_shorting_volume_by_date(start, end, ticker)
        time.sleep(0.3)
    except Exception as e:
        return pd.DataFrame(), str(e)

    if df is None or df.empty:
        return pd.DataFrame(), None

    df.index.name = "date_idx"
    df = df.reset_index()

    # Rename columns (Korean)
    col_map = {
        "date_idx": "date_raw",
        "공매도거래량": "short_volume",
        "공매도거래대금": "short_value",
        "비중": "short_ratio",
        "잔고수량": "short_balance",
        "잔고금액": "short_balance_value",
    }
    df = df.rename(columns=col_map)

    if "date_raw" in df.columns:
        df["date"] = pd.to_datetime(df["date_raw"]).dt.strftime("%Y-%m-%d")

    df["ticker"] = ticker

    return df, None


def upsert_short_selling(conn, df):
    if df.empty:
        return 0

    cur = conn.cursor()
    values = [
        (row["ticker"], row["date"],
         row.get("short_volume"), row.get("short_value"),
         row.get("short_balance"), row.get("short_balance_value"),
         row.get("short_ratio"), "pykrx")
        for _, row in df.iterrows()
    ]
    if not values:
        return 0

    query = """
    INSERT INTO short_selling (ticker, date, short_volume, short_value,
        short_balance, short_balance_value, short_ratio, source)
    VALUES %s
    ON CONFLICT (ticker, date) DO UPDATE SET
        short_volume = EXCLUDED.short_volume, short_value = EXCLUDED.short_value,
        short_balance = COALESCE(EXCLUDED.short_balance, short_selling.short_balance),
        short_balance_value = COALESCE(EXCLUDED.short_balance_value, short_selling.short_balance_value),
        short_ratio = EXCLUDED.short_ratio
    """
    execute_values(cur, query, values)
    conn.commit()
    cur.close()
    return len(values)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest short selling data")
    parser.add_argument("--tickers", help="Comma-separated tickers")
    parser.add_argument("--date", help="Single date YYYY-MM-DD")
    parser.add_argument("--start-date", help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", help="End date YYYY-MM-DD")
    parser.add_argument("--limit", type=int, help="Max tickers from DB")
    parser.add_argument("--full", action="store_true", help="Full from 2020")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)

    if args.date:
        start = normalize_date(args.date)
        end = start
    elif args.full:
        start, end = "20200101", datetime.now().strftime("%Y%m%d")
    elif args.start_date:
        start = normalize_date(args.start_date)
        end = normalize_date(args.end_date) if args.end_date else datetime.now().strftime("%Y%m%d")
    else:
        start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")

    # Resolve tickers
    if args.tickers:
        tickers = args.tickers.split(",")
    else:
        tickers = get_db_tickers(conn, args.limit)

    # Check ban period
    if is_short_selling_ban_period(start, end):
        print("NOTE: Date range {}-{} is in the short selling ban period (Nov 2023 - Mar 2025).".format(start, end))
        print("      No short selling data available. Marking factors as unavailable.")

        cur = conn.cursor()
        cur.execute("""
            UPDATE factor_coverage SET data_status = 'unavailable', is_available = FALSE,
                last_updated = NOW()
            WHERE factor_id IN ('short_ratio','short_balance_ratio')
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("Done! (skipped — ban period)")
        sys.exit(0)

    log_id = log_start(conn, {"start": start, "end": end, "tickers": args.tickers, "limit": args.limit})
    total_rows = 0
    total_errors = 0

    try:
        print("Ingesting short selling from {} to {} for {} tickers...".format(start, end, len(tickers)))

        for i, ticker in enumerate(tickers):
            print("  [{}/{}] {}...".format(i + 1, len(tickers), ticker), end=" ", flush=True)

            df, err = fetch_short_selling_per_ticker(ticker, start, end)

            if err:
                print("err: {}".format(err[:60]))
                total_errors += 1
                continue

            if df.empty:
                print("no data")
                continue

            n = upsert_short_selling(conn, df)
            total_rows += n
            print("{} rows".format(n))

            time.sleep(0.2)

        if total_rows > 0:
            cur = conn.cursor()
            cur.execute("""
                UPDATE factor_coverage SET data_status = 'real', is_available = TRUE,
                    uses_mock_data = FALSE, last_updated = NOW()
                WHERE factor_id IN ('short_ratio','short_balance_ratio') AND data_status = 'mock'
            """)
            conn.commit()
            cur.close()

        log_finish(conn, log_id, "success", rows_processed=len(tickers), rows_inserted=total_rows)

    except Exception as e:
        log_finish(conn, log_id, "error", error_message=str(e),
                   rows_processed=len(tickers), rows_inserted=total_rows)
        print("ERROR: {}".format(e))
        raise
    finally:
        conn.close()

    print("\nDone! Inserted/updated {} short selling records.".format(total_rows))
    if total_rows == 0 and total_errors > 0:
        print("WARNING: pykrx short selling APIs may be broken in version 1.0.51.")
        print("  Short selling factors are optional — the ranking engine handles missing data.")
