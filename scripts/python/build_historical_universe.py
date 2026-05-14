"""
Build the krx_all_historical universe membership.

This is the *candidate* set of tickers for the 10-year PIT backtest:
"every ticker that traded on KRX at some point between START_DATE and
today". The PIT correctness comes from calculate_factors.py applying a
per-date filter on daily_prices, so a stock listed in 2018 simply won't
have any factor snapshot for a 2016 rebalance.

Idempotent: re-running is safe; existing rows are upserted.

Usage
-----
    python build_historical_universe.py
    python build_historical_universe.py --start 2015-01-01
    python build_historical_universe.py --name krx_all_historical
    python build_historical_universe.py --dry-run
"""

import os
import sys
import argparse

import psycopg2
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "build_historical_universe"


def log_start(conn, params=None):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ingestion_log (script_name, parameters) "
        "VALUES (%s, %s) RETURNING id",
        (SCRIPT_NAME, Json(params)),
    )
    log_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    return log_id


def log_finish(conn, log_id, status, rows_processed=0,
               rows_inserted=0, error_message=None):
    cur = conn.cursor()
    cur.execute(
        "UPDATE ingestion_log "
        "SET finished_at = NOW(), status = %s, "
        "rows_processed = %s, rows_inserted = %s, "
        "error_message = %s "
        "WHERE id = %s",
        (status, rows_processed, rows_inserted, error_message, log_id),
    )
    conn.commit()
    cur.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build krx_all_historical universe = union of all "
                    "tickers in daily_prices since --start.")
    parser.add_argument("--name", default="krx_all_historical",
                        help="Universe name (default: krx_all_historical).")
    parser.add_argument("--start", default="2015-01-01",
                        help="Earliest daily_prices date to include "
                             "(default: 2015-01-01).")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    log_id = log_start(conn, {
        "name": args.name, "start": args.start, "dry_run": args.dry_run,
    })

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT ticker FROM daily_prices
            WHERE date >= %s
            ORDER BY ticker
        """, (args.start,))
        tickers = [r[0] for r in cur.fetchall()]
        cur.close()

        print("=" * 70, flush=True)
        print("build_historical_universe", flush=True)
        print("  Universe:  {0}".format(args.name), flush=True)
        print("  Start:     {0}".format(args.start), flush=True)
        print("  Candidates: {0:,} distinct tickers in daily_prices".format(
            len(tickers)), flush=True)
        print("=" * 70, flush=True)

        if args.dry_run:
            print("\n[DRY RUN] Would insert {0:,} memberships.".format(
                len(tickers)), flush=True)
            for t in tickers[:20]:
                print("  {0}".format(t), flush=True)
            if len(tickers) > 20:
                print("  ... and {0:,} more".format(len(tickers) - 20),
                      flush=True)
            log_finish(conn, log_id, "success", rows_processed=len(tickers),
                       rows_inserted=0)
            conn.close()
            sys.exit(0)

        cur = conn.cursor()
        rows = [(args.name, t) for t in tickers]
        execute_values(cur, """
            INSERT INTO universe_memberships (universe_name, ticker)
            VALUES %s
            ON CONFLICT (universe_name, ticker) DO NOTHING
        """, rows)
        n = cur.rowcount
        conn.commit()
        cur.close()

        print("\n  Inserted memberships: {0:,}".format(n), flush=True)
        print("  Final size:           {0:,}".format(len(tickers)), flush=True)

        log_finish(conn, log_id, "success", rows_processed=len(tickers),
                   rows_inserted=n)
    except KeyboardInterrupt:
        log_finish(conn, log_id, "interrupted",
                   error_message="user cancelled")
        sys.exit(1)
    except Exception as e:
        log_finish(conn, log_id, "error", error_message=str(e))
        print("\n[ERROR] {0}".format(e), flush=True)
        raise
    finally:
        conn.close()

    print("\nDone.")
