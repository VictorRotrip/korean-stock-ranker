"""
Ingest daily OHLCV and market cap data into Supabase Postgres.

Data sources (in order):
1. pykrx per-ticker: stock.get_market_ohlcv(start, end, ticker) — most reliable in 1.0.51
2. FinanceDataReader: fdr.DataReader(ticker, start, end) — fallback

Note: pykrx 1.0.51 broke the market-wide single-date calls.
This script uses the per-ticker date-range API instead.

Usage:
    python ingest_prices.py --tickers 005930,000660 --start-date 2024-01-01 --end-date 2024-12-31
    python ingest_prices.py --start-date 2024-06-01              # all active stocks from DB
    python ingest_prices.py --limit 20 --start-date 2024-01-01   # first 20 tickers in DB
    python ingest_prices.py --full                                # WARNING: full 2015+ load
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
    print("ERROR: DATABASE_URL not set. Copy .env.example -> .env.local and fill in your Supabase URL.")
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
    cur = conn.cursor()
    q = "SELECT ticker FROM stocks WHERE is_active = TRUE ORDER BY ticker"
    if limit:
        q += " LIMIT {}".format(int(limit))
    cur.execute(q)
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    return tickers


def normalize_date(date_str):
    """Accept YYYY-MM-DD or YYYYMMDD, always return YYYYMMDD."""
    return date_str.replace("-", "")


def to_iso(yyyymmdd):
    """YYYYMMDD -> YYYY-MM-DD."""
    s = yyyymmdd.replace("-", "")
    return "{}-{}-{}".format(s[:4], s[4:6], s[6:8])


# ---------------------------------------------------------------------------
# Data fetching — per-ticker (works in pykrx 1.0.51)
# ---------------------------------------------------------------------------

def fetch_ohlcv_per_ticker(ticker, start, end):
    """Fetch OHLCV for a single ticker over a date range.
    start/end: YYYYMMDD format.
    Returns DataFrame with columns: date, open, high, low, close, volume.
    """
    from pykrx import stock

    try:
        df = stock.get_market_ohlcv(start, end, ticker)
        time.sleep(0.3)
    except Exception as e:
        print("pykrx-err:{}".format(e), end=" ")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df.index.name = "date_idx"
    df = df.reset_index()

    # Map Korean column names
    col_map = {
        "날짜": "date_raw",
        "date_idx": "date_raw",
        "시가": "open",
        "고가": "high",
        "저가": "low",
        "종가": "close",
        "거래량": "volume",
        "등락률": "change_pct",
    }
    df = df.rename(columns=col_map)

    # Convert date index to ISO string
    if "date_raw" in df.columns:
        df["date"] = pd.to_datetime(df["date_raw"]).dt.strftime("%Y-%m-%d")
    else:
        # Index was already datetime
        df["date"] = df.index.strftime("%Y-%m-%d") if hasattr(df.index, "strftime") else df.index

    df["ticker"] = ticker

    return df


def fetch_market_cap_fdr(ticker, start, end):
    """Try to get market cap from FinanceDataReader."""
    try:
        import FinanceDataReader as fdr
        # FDR uses YYYY-MM-DD
        iso_start = to_iso(start)
        iso_end = to_iso(end)
        df = fdr.DataReader(ticker, iso_start, iso_end)
        if df is not None and not df.empty and "Marcap" in df.columns:
            result = df[["Marcap"]].copy()
            result.columns = ["market_cap"]
            result.index.name = "date"
            result = result.reset_index()
            result["date"] = pd.to_datetime(result["date"]).dt.strftime("%Y-%m-%d")
            return result
    except Exception:
        pass
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_prices(conn, df):
    if df.empty:
        return 0

    cur = conn.cursor()
    values = []
    for _, row in df.iterrows():
        close = row.get("close")
        if close is None or (isinstance(close, float) and pd.isna(close)) or close <= 0:
            continue
        values.append((
            row["ticker"], row["date"],
            row.get("open"), row.get("high"), row.get("low"), close,
            row.get("volume"),
            row.get("trading_value"),
            row.get("market_cap"),
            row.get("shares_outstanding"),
            "pykrx",
        ))

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
        trading_value = COALESCE(EXCLUDED.trading_value, daily_prices.trading_value),
        market_cap = COALESCE(EXCLUDED.market_cap, daily_prices.market_cap),
        shares_outstanding = COALESCE(EXCLUDED.shares_outstanding, daily_prices.shares_outstanding)
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
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)

    # Resolve date range (internal format: YYYYMMDD)
    if args.full:
        start = "20150101"
    elif args.start_date:
        start = normalize_date(args.start_date)
    else:
        start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

    end = normalize_date(args.end_date) if args.end_date else datetime.now().strftime("%Y%m%d")

    # Resolve tickers
    if args.tickers:
        tickers = args.tickers.split(",")
    else:
        tickers = get_db_tickers(conn, args.limit)
        if not tickers:
            print("WARNING: No active tickers in stocks table. Use --tickers or run ingest_universe.py first.")

    log_id = log_start(conn, {
        "start": start, "end": end,
        "tickers": ",".join(tickers[:20]), "count": len(tickers),
        "limit": args.limit, "full": args.full,
    })

    total_rows = 0

    try:
        print("Ingesting prices from {} to {} for {} tickers...".format(start, end, len(tickers)))

        for i, ticker in enumerate(tickers):
            print("  [{}/{}] {}...".format(i + 1, len(tickers), ticker), end=" ", flush=True)

            # 1. Get OHLCV from pykrx (per-ticker, works in 1.0.51)
            df = fetch_ohlcv_per_ticker(ticker, start, end)

            if df.empty:
                print("no data")
                continue

            # 2. Try to get market cap from FDR
            mcap_df = fetch_market_cap_fdr(ticker, start, end)
            if not mcap_df.empty:
                df = df.merge(mcap_df, on="date", how="left")
                print("ohlcv={} +mcap".format(len(df)), end=" ", flush=True)
            else:
                df["market_cap"] = None
                print("ohlcv={}".format(len(df)), end=" ", flush=True)

            # Fill missing columns
            for col in ["trading_value", "shares_outstanding"]:
                if col not in df.columns:
                    df[col] = None

            # 3. Upsert
            n = upsert_prices(conn, df)
            total_rows += n
            print("-> {} rows".format(n))

            time.sleep(0.2)  # be nice to KRX servers

        log_finish(conn, log_id, "success",
                   rows_processed=len(tickers), rows_inserted=total_rows)

    except Exception as e:
        log_finish(conn, log_id, "error", error_message=str(e),
                   rows_processed=0, rows_inserted=total_rows)
        print("ERROR: {}".format(e))
        raise
    finally:
        conn.close()

    print("\nDone! Inserted/updated {} price records for {} tickers.".format(total_rows, len(tickers)))
    if total_rows == 0:
        print("WARNING: Zero rows inserted. Possible causes:")
        print("  - pykrx could not reach KRX servers")
        print("  - Date range has no trading days")
        print("  - Run: python debug_pykrx.py to diagnose")
