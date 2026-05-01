"""
Ingest fundamental proxies (PER, PBR, EPS, BPS, DPS, dividend yield).

Data sources (tried in order):
1. pykrx per-ticker: stock.get_market_fundamental(start, end, ticker)
2. pykrx market-wide: stock.get_market_fundamental(date, market=...) [broken in 1.0.51]

Note: pykrx 1.0.51 broke most market-wide APIs. This script tries the
per-ticker range call first. If that also fails, it skips gracefully.

These are KRX/Naver-derived values — NOT institutional-grade.
They will be superseded by DART data in Phase 3.

Usage:
    python ingest_pykrx_fundamentals.py --tickers 005930,000660 --date 2024-12-27
    python ingest_pykrx_fundamentals.py --tickers 005930 --start-date 2024-12-01 --end-date 2024-12-31
    python ingest_pykrx_fundamentals.py --limit 20 --start-date 2024-06-01
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

SCRIPT_NAME = "ingest_pykrx_fundamentals"


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


# ---------------------------------------------------------------------------
# Data fetching — per-ticker (pykrx 1.0.51 compatible)
# ---------------------------------------------------------------------------

def fetch_fundamentals_per_ticker(ticker, start, end):
    """Try pykrx per-ticker fundamental call.
    Returns DataFrame with date, per, pbr, eps, bps, dps, dividend_yield or empty."""
    from pykrx import stock

    try:
        df = stock.get_market_fundamental(start, end, ticker)
        time.sleep(0.3)
    except Exception as e:
        # pykrx 1.0.51: this may fail too
        return pd.DataFrame(), str(e)

    if df is None or df.empty:
        return pd.DataFrame(), "empty"

    # Index is date
    df.index.name = "date_idx"
    df = df.reset_index()

    col_map = {
        "date_idx": "date_raw",
        "BPS": "bps", "PER": "per", "PBR": "pbr",
        "EPS": "eps", "DIV": "dividend_yield", "DPS": "dps",
    }
    df = df.rename(columns=col_map)

    if "date_raw" in df.columns:
        df["date"] = pd.to_datetime(df["date_raw"]).dt.strftime("%Y-%m-%d")

    df["ticker"] = ticker

    # Zero PER/PBR = missing
    for col in ["per", "pbr"]:
        if col in df.columns:
            df.loc[df[col] == 0, col] = None

    return df, None


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_fundamentals(conn, df):
    if df.empty:
        return 0

    cur = conn.cursor()
    values = [
        (row["ticker"], row["date"],
         row.get("per"), row.get("pbr"), row.get("eps"),
         row.get("bps"), row.get("dps"), row.get("dividend_yield"))
        for _, row in df.iterrows()
    ]
    if not values:
        return 0

    query = """
    INSERT INTO pykrx_fundamentals (ticker, date, per, pbr, eps, bps, dps, dividend_yield)
    VALUES %s
    ON CONFLICT (ticker, date) DO UPDATE SET
        per = EXCLUDED.per, pbr = EXCLUDED.pbr, eps = EXCLUDED.eps,
        bps = EXCLUDED.bps, dps = EXCLUDED.dps, dividend_yield = EXCLUDED.dividend_yield
    """
    execute_values(cur, query, values)
    conn.commit()
    cur.close()
    return len(values)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest pykrx fundamentals")
    parser.add_argument("--tickers", help="Comma-separated tickers")
    parser.add_argument("--date", help="Single date YYYY-MM-DD")
    parser.add_argument("--start-date", help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", help="End date YYYY-MM-DD")
    parser.add_argument("--limit", type=int, help="Max tickers to process from DB")
    parser.add_argument("--full", action="store_true", help="Full history from 2015")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)

    # Resolve dates
    if args.date:
        start = normalize_date(args.date)
        end = start
    elif args.full:
        start = "20150101"
        end = datetime.now().strftime("%Y%m%d")
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
        if not tickers:
            print("WARNING: No active tickers in DB. Use --tickers or run ingest_universe.py first.")

    log_id = log_start(conn, {"start": start, "end": end, "tickers": args.tickers, "limit": args.limit})
    total_rows = 0
    total_errors = 0

    try:
        print("Ingesting pykrx fundamentals from {} to {} for {} tickers...".format(
            start, end, len(tickers)))

        for i, ticker in enumerate(tickers):
            print("  [{}/{}] {}...".format(i + 1, len(tickers), ticker), end=" ", flush=True)

            df, err = fetch_fundamentals_per_ticker(ticker, start, end)

            if err:
                print("err: {}".format(err[:60]))
                total_errors += 1
                continue

            if df.empty:
                print("no data")
                continue

            n = upsert_fundamentals(conn, df)
            total_rows += n
            print("{} rows".format(n))

            time.sleep(0.2)

        # Update factor_coverage if we got data
        if total_rows > 0:
            cur = conn.cursor()
            cur.execute("""
                UPDATE factor_coverage SET data_status = 'proxy', is_available = TRUE,
                    uses_mock_data = FALSE, last_updated = NOW()
                WHERE factor_id IN ('earnings_yield','book_to_market','dividend_yield','eps_growth')
                  AND data_status = 'mock'
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

    print("\nDone! Inserted/updated {} fundamental records.".format(total_rows))
    if total_rows == 0:
        print("WARNING: Zero rows inserted.")
        if total_errors > 0:
            print("  pykrx fundamental APIs may be broken in your version (1.0.51 known issue).")
            print("  Value factors will work once DART financials are ingested instead.")
        print("  The ranking engine handles missing factors gracefully.")
