"""
Ingest daily OHLCV and market cap data into Supabase Postgres.

Data source: pykrx (stock.get_market_ohlcv, stock.get_market_cap)

Usage:
    python ingest_prices.py --tickers 005930,000660 --start-date 2024-01-01 --end-date 2024-12-31
    python ingest_prices.py --start-date 2024-06-01              # all stocks, from date
    python ingest_prices.py --limit 20 --start-date 2024-01-01   # first 20 tickers in DB
    python ingest_prices.py --full                                # WARNING: full 2015+ load, takes hours
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
    print("ERROR: DATABASE_URL not set. Copy .env.example → .env.local and fill in your Supabase URL.")
    sys.exit(1)

SCRIPT_NAME = "ingest_prices"


# ---------------------------------------------------------------------------
# Ingestion logging
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_db_tickers(conn, limit=None):
    """Fetch active tickers from DB."""
    cur = conn.cursor()
    q = "SELECT ticker FROM stocks WHERE is_active = TRUE ORDER BY ticker"
    if limit:
        q += f" LIMIT {int(limit)}"
    cur.execute(q)
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    return tickers


def to_yyyymmdd(iso: str) -> str:
    """Convert YYYY-MM-DD → YYYYMMDD."""
    return iso.replace("-", "")


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_daily_data(date_str: str, market: str):
    """Fetch OHLCV + market cap for all stocks on a date (YYYYMMDD format)."""
    from pykrx import stock

    ohlcv = stock.get_market_ohlcv(date_str, market=market)
    time.sleep(0.3)
    cap = stock.get_market_cap(date_str, market=market)
    time.sleep(0.3)

    if ohlcv.empty or cap.empty:
        return pd.DataFrame()

    merged = ohlcv.join(cap, how="inner")
    merged["date"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    merged.index.name = "ticker"
    merged = merged.reset_index()

    col_map = {
        "시가": "open", "고가": "high", "저가": "low", "종가": "close",
        "거래량": "volume", "거래대금": "trading_value",
        "시가총액": "market_cap", "상장주식수": "shares_outstanding",
    }
    merged = merged.rename(columns=col_map)
    return merged


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_prices(conn, df: pd.DataFrame):
    cur = conn.cursor()
    values = [
        (
            row["ticker"], row["date"], row.get("open"), row.get("high"),
            row.get("low"), row["close"], row.get("volume"),
            row.get("trading_value"), row.get("market_cap"),
            row.get("shares_outstanding"), "marcap",
        )
        for _, row in df.iterrows()
        if pd.notna(row.get("close")) and row["close"] > 0
    ]
    if not values:
        return 0

    query = """
    INSERT INTO daily_prices (
        ticker, date, open, high, low, close,
        volume, trading_value, market_cap, shares_outstanding, source
    ) VALUES %s
    ON CONFLICT (ticker, date) DO UPDATE SET
        open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
        close = EXCLUDED.close, volume = EXCLUDED.volume,
        trading_value = EXCLUDED.trading_value, market_cap = EXCLUDED.market_cap,
        shares_outstanding = EXCLUDED.shares_outstanding
    """
    execute_values(cur, query, values)
    conn.commit()
    cur.close()
    return len(values)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest daily price data")
    parser.add_argument("--tickers", help="Comma-separated tickers (e.g. 005930,000660)")
    parser.add_argument("--start-date", help="Start date YYYY-MM-DD (default: 7 days ago)")
    parser.add_argument("--end-date", help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--limit", type=int, help="Max tickers to process from DB")
    parser.add_argument("--full", action="store_true", help="Full history from 2015 (SLOW)")
    # Legacy compat
    parser.add_argument("--start", help=argparse.SUPPRESS)
    parser.add_argument("--ticker", help=argparse.SUPPRESS)
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)

    # Resolve date range
    if args.full:
        start = "20150101"
    elif args.start_date:
        start = to_yyyymmdd(args.start_date)
    elif args.start:
        start = args.start
    else:
        start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

    end = to_yyyymmdd(args.end_date) if args.end_date else datetime.now().strftime("%Y%m%d")

    # Resolve tickers to filter
    tickers_filter = None
    if args.tickers:
        tickers_filter = set(args.tickers.split(","))
    elif args.ticker:
        tickers_filter = {args.ticker}

    log_id = log_start(conn, {
        "start": start, "end": end,
        "tickers": args.tickers, "limit": args.limit, "full": args.full,
    })

    total_rows = 0
    total_processed = 0

    try:
        print(f"Ingesting prices from {start} to {end}...")

        # Get trading days
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
                    df = fetch_daily_data(day_str, market)
                    if not df.empty and tickers_filter:
                        df = df[df["ticker"].isin(tickers_filter)]
                    if not df.empty and args.limit:
                        db_tickers = set(get_db_tickers(conn, args.limit))
                        df = df[df["ticker"].isin(db_tickers)]
                    if not df.empty:
                        total_processed += len(df)
                        n = upsert_prices(conn, df)
                        total_rows += n
                        print(f"{market}={n}", end=" ", flush=True)
                except Exception as e:
                    print(f"Error {market}: {e}", end=" ")

            print()

        log_finish(conn, log_id, "success",
                   rows_processed=total_processed, rows_inserted=total_rows)

    except Exception as e:
        log_finish(conn, log_id, "error", error_message=str(e),
                   rows_processed=total_processed, rows_inserted=total_rows)
        print(f"ERROR: {e}")
        raise
    finally:
        conn.close()

    print(f"\nDone! Inserted/updated {total_rows} price records.")
