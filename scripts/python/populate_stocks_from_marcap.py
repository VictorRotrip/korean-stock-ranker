"""
Insert stub rows in `stocks` for any ticker in daily_prices that isn't
already there. Pulls name + market from the marcap package's historical
dataframe (which is already cached locally if you've run ingest_marcap
historical recently).

Why this exists
---------------
ingest_marcap.py writes only to daily_prices, not to stocks. After a
historical backfill that picks up delisted tickers (2015-2022), those new
tickers exist in daily_prices but have no metadata in stocks, which would
break every join in the rest of the pipeline.

This script reconciles. For each missing ticker we record:

  ticker, name, market (KOSPI/KOSDAQ), listing_date (earliest marcap date),
  delisting_date (last marcap date if it's > 30 days before today, else null),
  is_active (true if the stock has prices in the last 30 days, else false),
  source = "marcap_historical".

Usage
-----
    python populate_stocks_from_marcap.py
    python populate_stocks_from_marcap.py --dry-run
    python populate_stocks_from_marcap.py --start 2015-01-01 --end 2025-12-31
"""

import os
import sys
import argparse
from datetime import date, timedelta

import psycopg2
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "populate_stocks_from_marcap"


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


def log_finish(conn, log_id, status, rows_processed=0, rows_inserted=0,
               rows_updated=0, error_message=None):
    cur = conn.cursor()
    cur.execute(
        "UPDATE ingestion_log "
        "SET finished_at = NOW(), status = %s, "
        "rows_processed = %s, rows_inserted = %s, rows_updated = %s, "
        "error_message = %s "
        "WHERE id = %s",
        (status, rows_processed, rows_inserted, rows_updated,
         error_message, log_id),
    )
    conn.commit()
    cur.close()


def fetch_missing_tickers(conn):
    """Tickers present in daily_prices but not in stocks."""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT dp.ticker
        FROM daily_prices dp
        LEFT JOIN stocks s ON s.ticker = dp.ticker
        WHERE s.ticker IS NULL
        ORDER BY dp.ticker
    """)
    rows = [r[0] for r in cur.fetchall()]
    cur.close()
    return rows


def get_marcap_metadata(start_date, end_date, missing_tickers):
    """Build {ticker: {name, market, first_date, last_date}} using the
    project's data_sources.marcap_historical helper (same one ingest_marcap.py
    uses). The returned DataFrame is already normalised to lowercase columns:
    date, ticker, name, market, open, high, low, close, ...
    """
    print("  Loading marcap data for {0} -> {1}...".format(start_date, end_date),
          flush=True)
    try:
        from data_sources import marcap_historical
    except ImportError as e:
        print("  ERROR: cannot import data_sources.marcap_historical: {0}".format(e),
              flush=True)
        print("  (run this script from the scripts/python/ directory)",
              flush=True)
        return {}

    df = marcap_historical.fetch_marcap_range(
        str(start_date), str(end_date),
        tickers=list(missing_tickers),
        verbose=False,
    )
    if df is None or len(df) == 0:
        print("  ERROR: marcap returned no data for the range", flush=True)
        return {}

    print("  marcap rows: {0:,}, unique tickers: {1:,}".format(
        len(df), df["ticker"].nunique()), flush=True)

    from datetime import datetime as _dt

    out = {}
    # Sort once, then iterate; groupby with sort=False preserves date order.
    df = df.sort_values(["ticker", "date"])
    for tk, g in df.groupby("ticker", sort=False):
        first_row = g.iloc[0]
        last_row = g.iloc[-1]
        # Handle pandas NaN safely
        name = str(first_row["name"]).strip() if first_row["name"] == first_row["name"] else ""
        name = name or None
        market = str(first_row["market"]).strip() if first_row["market"] == first_row["market"] else ""
        if market and market.upper() in ("KOSPI", "KOSDAQ", "KONEX"):
            market = market.upper()
        else:
            market = None

        # date column is ISO strings after _normalize_df — parse to date
        try:
            first_date = _dt.strptime(first_row["date"], "%Y-%m-%d").date()
            last_date = _dt.strptime(last_row["date"], "%Y-%m-%d").date()
        except Exception:
            continue

        out[tk] = {
            "name": name,
            "market": market,
            "first_date": first_date,
            "last_date": last_date,
        }
    return out


def upsert_stocks(conn, ticker_rows, dry_run=False):
    """ticker_rows: list of (ticker, name, market, listing_date,
                             delisting_date, is_active, source)."""
    if not ticker_rows:
        return 0
    if dry_run:
        print("  [DRY RUN] Would insert {0:,} stock rows".format(len(ticker_rows)),
              flush=True)
        for r in ticker_rows[:10]:
            print("    {0!r}".format(r), flush=True)
        if len(ticker_rows) > 10:
            print("    ... and {0:,} more".format(len(ticker_rows) - 10), flush=True)
        return 0

    cur = conn.cursor()
    # 7 columns inserted; updated_at picks up its column DEFAULT NOW() on
    # insert and is set explicitly on conflict.
    execute_values(cur, """
        INSERT INTO stocks
            (ticker, name, market, listing_date, delisting_date, is_active,
             source)
        VALUES %s
        ON CONFLICT (ticker) DO UPDATE SET
            name           = COALESCE(EXCLUDED.name, stocks.name),
            market         = COALESCE(EXCLUDED.market, stocks.market),
            listing_date   = COALESCE(EXCLUDED.listing_date, stocks.listing_date),
            delisting_date = COALESCE(EXCLUDED.delisting_date, stocks.delisting_date),
            is_active      = EXCLUDED.is_active,
            updated_at     = NOW()
    """, ticker_rows)
    n = cur.rowcount
    conn.commit()
    cur.close()
    return n


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Populate stocks table for tickers in daily_prices "
                    "that have no metadata yet (typically delisted tickers "
                    "picked up by a historical marcap backfill).")
    parser.add_argument("--start", default="2015-01-01",
                        help="Marcap query start (default: 2015-01-01).")
    parser.add_argument("--end", default=str(date.today()),
                        help="Marcap query end (default: today).")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)

    log_id = log_start(conn, {
        "start": args.start, "end": args.end, "dry_run": args.dry_run,
    })

    try:
        missing = fetch_missing_tickers(conn)
        print("=" * 70, flush=True)
        print("populate_stocks_from_marcap", flush=True)
        print("  Missing tickers: {0:,}".format(len(missing)), flush=True)
        print("=" * 70, flush=True)

        if not missing:
            print("Nothing to do; stocks is consistent with daily_prices.",
                  flush=True)
            log_finish(conn, log_id, "success", 0, 0, 0)
            conn.close()
            sys.exit(0)

        meta = get_marcap_metadata(args.start, args.end, missing)

        today = date.today()
        active_cutoff = today - timedelta(days=30)

        rows = []
        rows_no_meta = []
        for t in missing:
            m = meta.get(t)
            if m is None:
                # No marcap metadata — insert a bare row so downstream joins
                # at least find the ticker. stocks.name and stocks.market are
                # NOT NULL, so use the ticker as a placeholder name and
                # "UNKNOWN" as the market. Mark inactive.
                rows.append((t, t, "UNKNOWN", None, None, False, "marcap_historical"))
                rows_no_meta.append(t)
                continue
            is_active = m["last_date"] >= active_cutoff
            delisting_date = None if is_active else m["last_date"]
            # Defensive defaults for the NOT NULL columns. Korean-name nulls
            # appear sometimes in marcap for very old/preferred/oddly-coded
            # listings; using the ticker as a fallback keeps the row insertable
            # without losing information (we know the ticker).
            name = m["name"] if m["name"] else t
            market = m["market"] if m["market"] else "UNKNOWN"
            rows.append((
                t,
                name,
                market,
                m["first_date"],
                delisting_date,
                is_active,
                "marcap_historical",
            ))

        print("\n  With marcap metadata:    {0:,}".format(len(rows) - len(rows_no_meta)),
              flush=True)
        print("  Without marcap metadata: {0:,}".format(len(rows_no_meta)),
              flush=True)
        if rows_no_meta[:10]:
            print("    examples: {0}".format(", ".join(rows_no_meta[:10])),
                  flush=True)

        n = upsert_stocks(conn, rows, dry_run=args.dry_run)
        print("\n  Inserted/updated stocks rows: {0:,}".format(n), flush=True)

        log_finish(conn, log_id, "success", rows_processed=len(rows),
                   rows_inserted=n)
    except KeyboardInterrupt:
        try:
            conn.rollback()
        except Exception:
            pass
        log_finish(conn, log_id, "interrupted",
                   error_message="user cancelled")
        print("\n[INTERRUPTED]", flush=True)
        sys.exit(1)
    except Exception as e:
        # If the failure was a SQL error, the connection's transaction is
        # in an aborted state — every subsequent statement fails until a
        # ROLLBACK clears it. Roll back BEFORE attempting log_finish so
        # we don't lose the original error message to a secondary
        # InFailedSqlTransaction crash.
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            log_finish(conn, log_id, "error", error_message=str(e))
        except Exception as log_err:
            print("\n[WARN] could not write log_finish: {0}".format(log_err),
                  flush=True)
        print("\n[ERROR] {0}".format(e), flush=True)
        raise
    finally:
        conn.close()

    print("\nDone.")
