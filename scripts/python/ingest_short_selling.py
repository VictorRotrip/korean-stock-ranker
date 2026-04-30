"""
Ingest short selling data from pykrx into Supabase Postgres.

Usage:
    python ingest_short_selling.py --tickers 005930,000660 --date 2024-12-31
    python ingest_short_selling.py --start-date 2024-01-01 --end-date 2024-12-31
    python ingest_short_selling.py --limit 20 --start-date 2024-06-01

NOTE: Korean short selling was banned Nov 2023 – Mar 2025 for most stocks.
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


def to_yyyymmdd(iso: str) -> str:
    return iso.replace("-", "")


def get_db_tickers(conn, limit=None):
    cur = conn.cursor()
    q = "SELECT ticker FROM stocks WHERE is_active = TRUE ORDER BY ticker"
    if limit:
        q += f" LIMIT {int(limit)}"
    cur.execute(q)
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    return tickers


def fetch_short_volume(date_str: str, market: str):
    from pykrx import stock
    df = stock.get_shorting_volume_by_ticker(date_str, market=market)
    time.sleep(0.3)
    if df.empty:
        return pd.DataFrame()
    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    df["date"] = iso_date
    df.index.name = "ticker"
    df = df.reset_index()
    col_map = {"공매도거래량": "short_volume", "공매도거래대금": "short_value", "비중": "short_ratio"}
    df = df.rename(columns=col_map)
    return df


def fetch_short_balance(date_str: str, market: str):
    from pykrx import stock
    df = stock.get_shorting_balance_by_ticker(date_str, market=market)
    time.sleep(0.3)
    if df.empty:
        return pd.DataFrame()
    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    df["date"] = iso_date
    df.index.name = "ticker"
    df = df.reset_index()
    col_map = {"공매도잔고": "short_balance", "공매도금액": "short_balance_value"}
    df = df.rename(columns=col_map)
    return df


def upsert_short_selling(conn, df: pd.DataFrame):
    cur = conn.cursor()
    values = [
        (row["ticker"], row["date"], row.get("short_volume"), row.get("short_value"),
         row.get("short_balance"), row.get("short_balance_value"), row.get("short_ratio"), "pykrx")
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
        start = to_yyyymmdd(args.date)
        end = start
    elif args.full:
        start, end = "20200101", datetime.now().strftime("%Y%m%d")
    elif args.start_date:
        start = to_yyyymmdd(args.start_date)
        end = to_yyyymmdd(args.end_date) if args.end_date else datetime.now().strftime("%Y%m%d")
    else:
        start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")

    tickers_filter = set(args.tickers.split(",")) if args.tickers else None

    log_id = log_start(conn, {"start": start, "end": end, "tickers": args.tickers, "limit": args.limit})
    total_rows = 0
    total_processed = 0

    try:
        print(f"Ingesting short selling from {start} to {end}...")
        try:
            from pykrx import stock
            days = stock.get_previous_business_days(fromdate=start, todate=end)
        except Exception:
            days = pd.bdate_range(start, end)

        for day in days:
            day_str = day.strftime("%Y%m%d")
            print(f"  {day_str}...", end=" ", flush=True)
            for market in ["KOSPI", "KOSDAQ"]:
                try:
                    vol_df = fetch_short_volume(day_str, market)
                    bal_df = fetch_short_balance(day_str, market)

                    if vol_df.empty:
                        continue
                    result = vol_df[["ticker", "date", "short_volume", "short_value", "short_ratio"]].copy()
                    if not bal_df.empty:
                        bc = bal_df[["ticker", "date", "short_balance", "short_balance_value"]]
                        result = result.merge(bc, on=["ticker", "date"], how="left")
                    else:
                        result["short_balance"] = None
                        result["short_balance_value"] = None

                    if tickers_filter:
                        result = result[result["ticker"].isin(tickers_filter)]
                    if args.limit:
                        db_tickers = set(get_db_tickers(conn, args.limit))
                        result = result[result["ticker"].isin(db_tickers)]

                    if not result.empty:
                        total_processed += len(result)
                        n = upsert_short_selling(conn, result)
                        total_rows += n
                        print(f"{market}={n}", end=" ", flush=True)
                except Exception as e:
                    print(f"Error {market}: {e}", end=" ")
            print()

        cur = conn.cursor()
        cur.execute("""
            UPDATE factor_coverage SET data_status = 'real', is_available = TRUE,
                uses_mock_data = FALSE, last_updated = NOW()
            WHERE factor_id IN ('short_ratio','short_balance_ratio') AND data_status = 'mock'
        """)
        conn.commit()
        cur.close()

        log_finish(conn, log_id, "success", rows_processed=total_processed, rows_inserted=total_rows)

    except Exception as e:
        log_finish(conn, log_id, "error", error_message=str(e),
                   rows_processed=total_processed, rows_inserted=total_rows)
        print(f"ERROR: {e}")
        raise
    finally:
        conn.close()

    print(f"\nDone! Inserted/updated {total_rows} short selling records.")
