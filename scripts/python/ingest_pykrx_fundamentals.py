"""
Ingest pykrx fundamental proxies (PER, PBR, EPS, BPS, DPS, dividend yield).

These are KRX/Naver-derived values — NOT institutional-grade.
They will be superseded by DART data in Phase 3.

Usage:
    python ingest_pykrx_fundamentals.py --tickers 005930,000660 --date 2024-12-31
    python ingest_pykrx_fundamentals.py --start-date 2024-01-01 --end-date 2024-12-31
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


def fetch_fundamentals(date_str: str, market: str):
    """Fetch PER/PBR/EPS/BPS/DPS for all stocks on a given date (YYYYMMDD)."""
    from pykrx import stock

    df = stock.get_market_fundamental(date_str, market=market)
    time.sleep(0.3)
    if df.empty:
        return pd.DataFrame()

    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    df["date"] = iso_date
    df.index.name = "ticker"
    df = df.reset_index()

    col_map = {"PER": "per", "PBR": "pbr", "EPS": "eps", "BPS": "bps", "DPS": "dps", "DIV": "dividend_yield"}
    df = df.rename(columns=col_map)

    for col in ["per", "pbr"]:
        if col in df.columns:
            df.loc[df[col] == 0, col] = None

    return df


def upsert_fundamentals(conn, df: pd.DataFrame):
    cur = conn.cursor()
    values = [
        (row["ticker"], row["date"], row.get("per"), row.get("pbr"),
         row.get("eps"), row.get("bps"), row.get("dps"), row.get("dividend_yield"))
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
        start = to_yyyymmdd(args.date)
        end = start
    elif args.full:
        start = "20150101"
        end = datetime.now().strftime("%Y%m%d")
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
        print(f"Ingesting pykrx fundamentals from {start} to {end}...")

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
                    df = fetch_fundamentals(day_str, market)
                    if not df.empty and tickers_filter:
                        df = df[df["ticker"].isin(tickers_filter)]
                    if not df.empty and args.limit:
                        db_tickers = set(get_db_tickers(conn, args.limit))
                        df = df[df["ticker"].isin(db_tickers)]
                    if not df.empty:
                        total_processed += len(df)
                        n = upsert_fundamentals(conn, df)
                        total_rows += n
                        print(f"{market}={n}", end=" ", flush=True)
                except Exception as e:
                    print(f"Error {market}: {e}", end=" ")
            print()

        # Update factor_coverage
        cur = conn.cursor()
        cur.execute("""
            UPDATE factor_coverage SET data_status = 'proxy', is_available = TRUE,
                uses_mock_data = FALSE, last_updated = NOW()
            WHERE factor_id IN ('earnings_yield','book_to_market','dividend_yield','eps_growth')
              AND data_status = 'mock'
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

    print(f"\nDone! Inserted/updated {total_rows} fundamental records.")
