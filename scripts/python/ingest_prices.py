"""
Ingest daily OHLCV (and optionally market-cap supplementation) into Supabase
Postgres.

Defaults
--------
By default this script writes OHLCV ONLY. Historical market-cap, shares-
outstanding, and trading-value are now owned by ingest_marcap.py (PIT-safe
marcap_historical source). To avoid clobbering those PIT-safe rows:

  * market_cap / shares_outstanding / trading_value are NEVER written by
    default — even if FinanceDataReader returns them.
  * The ON CONFLICT clause preserves existing rows whose source is
    'marcap_historical' (we never downgrade PIT-correct rows to FDR/pykrx).
  * pykrx market-cap supplementation is OFF by default and only runs when
    --supplement-marcap-pykrx is passed AND KRX_ID/KRX_PW are present in env.

Data sources (in order):
1. FinanceDataReader: fdr.DataReader(ticker, start, end) — OHLCV (and marcap,
   ignored unless --supplement-marcap-pykrx).
2. pykrx per-ticker: stock.get_market_ohlcv(start, end, ticker) — fallback OHLCV.

Usage:
    python ingest_prices.py --tickers 005930,000660 --start-date 2024-01-01 --end-date 2024-12-31
    python ingest_prices.py --start-date 2024-06-01              # all active stocks from DB
    python ingest_prices.py --limit 20 --start-date 2024-01-01   # first 20 tickers in DB
    python ingest_prices.py --full                                # from 2015-01-01
    python ingest_prices.py --dry-run --limit 5
    python ingest_prices.py --resume --start-date 2024-06-01
    python ingest_prices.py --market KOSPI,KOSDAQ --start-date 2024-06-01
    python ingest_prices.py --universe test_200_large_pit_20251230 --start-date 2024-06-01 --supplement-marcap-pykrx
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

# Module-level flag to track if we've logged FDR columns yet
_fdr_columns_logged = False

# NOTE: ingest_prices.py treats every existing daily_prices.source value as
# immutable. It only fills in source when the existing row was NULL. The
# only place that overwrites source is ingest_marcap.py (which always sets
# 'marcap_historical' for historical-range upserts). So there's no
# allowlist/denylist needed here — see upsert_prices() docstring.


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

def get_db_tickers(conn, limit=None, market=None):
    """Get active tickers from DB, optionally filtered by market."""
    cur = conn.cursor()
    q = "SELECT ticker FROM stocks WHERE is_active = TRUE"
    if market:
        q += " AND market IN ({})".format(
            ",".join("'{}'".format(m) for m in market.split(","))
        )
    q += " ORDER BY ticker"
    if limit:
        q += " LIMIT {}".format(int(limit))
    cur.execute(q)
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    return tickers


def get_latest_date_for_ticker(conn, ticker):
    """Get the latest date already in daily_prices for a ticker."""
    cur = conn.cursor()
    cur.execute(
        "SELECT MAX(date) FROM daily_prices WHERE ticker = %s",
        (ticker,)
    )
    result = cur.fetchone()
    cur.close()
    if result and result[0]:
        return result[0]
    return None


def normalize_date(date_str):
    """Accept YYYY-MM-DD or YYYYMMDD, always return YYYY-MM-DD."""
    s = date_str.replace("-", "")
    return "{}-{}-{}".format(s[:4], s[4:6], s[6:8])


def to_yyyymmdd(iso_date):
    """YYYY-MM-DD -> YYYYMMDD."""
    return iso_date.replace("-", "")


# ---------------------------------------------------------------------------
# Data fetching — pykrx market cap (supplemental)
# ---------------------------------------------------------------------------

def fetch_market_cap_from_pykrx(ticker, start_yyyymmdd, end_yyyymmdd, timeout=10):
    """Fetch market cap + shares + trading value from pykrx per-ticker.

    Args:
        ticker: stock ticker
        start_yyyymmdd: YYYYMMDD format
        end_yyyymmdd: YYYYMMDD format
        timeout: timeout in seconds (unused, but kept for compatibility)

    Returns:
        DataFrame with columns: date, market_cap, shares_outstanding, trading_value
        or None if failed
    """
    from pykrx import stock
    try:
        df = stock.get_market_cap_by_date(start_yyyymmdd, end_yyyymmdd, ticker)
        time.sleep(0.3)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    df.index.name = "date_idx"
    df = df.reset_index()
    col_map = {
        "날짜": "date",
        "date_idx": "date",
        "시가총액": "market_cap",
        "거래대금": "trading_value",
        "상장주식수": "shares_outstanding",
        "거래량": "volume_pykrx",
    }
    df = df.rename(columns=col_map)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


# ---------------------------------------------------------------------------
# Data fetching — FinanceDataReader (primary)
# ---------------------------------------------------------------------------

def fetch_from_fdr(ticker, start, end, debug_columns=False, supplement_marcap=False):
    """Fetch OHLCV (and optionally Marcap) from FinanceDataReader.

    Args:
        ticker: stock ticker
        start: YYYY-MM-DD format
        end: YYYY-MM-DD format
        debug_columns: if True, always print columns; otherwise print only on first call
        supplement_marcap: if True, attempt to fill missing market_cap from pykrx.
                           Default False — historical marcap is owned by
                           ingest_marcap.py and we don't want to clobber
                           PIT-safe rows with FDR-only data.

    Returns:
        tuple: (df, source) where source is "fdr" or None if failed
               df columns: date, ticker, open, high, low, close, volume, market_cap, shares_outstanding, trading_value
    """
    global _fdr_columns_logged
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader(ticker, start, end)

        if df is None or df.empty:
            return None, None

        df.index.name = "date_idx"
        df = df.reset_index()

        # Debug: print columns on first call or if --debug-columns is set
        if debug_columns or not _fdr_columns_logged:
            print("  FDR columns for {}: {}".format(ticker, list(df.columns)), flush=True)
            _fdr_columns_logged = True

        # Normalize column names (FDR uses English column names usually)
        # Expanded to include more patterns: Amount/거래대금 -> trading_value, Stocks/상장주식수 -> shares_outstanding
        rename = {}
        for col in df.columns:
            lc = col.lower()
            if lc in ("date", "date_idx"):
                rename[col] = "date"
            elif lc in ("open", "시가"):
                rename[col] = "open"
            elif lc in ("high", "고가"):
                rename[col] = "high"
            elif lc in ("low", "저가"):
                rename[col] = "low"
            elif lc in ("close", "종가"):
                rename[col] = "close"
            elif lc in ("volume", "거래량"):
                rename[col] = "volume"
            elif lc in ("marcap", "시가총액"):
                rename[col] = "market_cap"
            elif lc in ("amount", "거래대금"):
                rename[col] = "trading_value"
            elif lc in ("stocks", "상장주식수"):
                rename[col] = "shares_outstanding"
            # Ignore: changes, 등락률 (change_pct)
        df = df.rename(columns=rename)

        # Convert date to ISO string
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        else:
            df["date"] = df.index.strftime("%Y-%m-%d") if hasattr(df.index, "strftime") else df.index

        df["ticker"] = ticker

        # Try to extract shares_outstanding from market_cap / close if not already present
        if "market_cap" in df.columns and "close" in df.columns and "shares_outstanding" not in df.columns:
            df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce")
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["shares_outstanding"] = df.apply(
                lambda r: int(r["market_cap"] / r["close"]) if r["market_cap"] > 0 and r["close"] > 0 else None,
                axis=1
            )

        # Optionally supplement missing market_cap from pykrx. Off by default
        # — historical marcap belongs to ingest_marcap.py.
        if supplement_marcap:
            has_marcap = "market_cap" in df.columns and df["market_cap"].notna().any()
            if not has_marcap:
                try:
                    mcap_df = fetch_market_cap_from_pykrx(ticker, to_yyyymmdd(start), to_yyyymmdd(end))
                    if mcap_df is not None and not mcap_df.empty:
                        # Merge on date
                        df = df.merge(
                            mcap_df[["date", "market_cap", "shares_outstanding", "trading_value"]].drop_duplicates("date"),
                            on="date", how="left", suffixes=("", "_pykrx")
                        )
                        # Use pykrx values where FDR is missing
                        for col in ["market_cap", "shares_outstanding", "trading_value"]:
                            pykrx_col = col + "_pykrx"
                            if pykrx_col in df.columns:
                                df[col] = df[col].fillna(df[pykrx_col])
                                df.drop(columns=[pykrx_col], inplace=True)
                except Exception:
                    pass

        # Ensure these columns exist
        if "shares_outstanding" not in df.columns:
            df["shares_outstanding"] = None
        if "trading_value" not in df.columns:
            df["trading_value"] = None

        return df, "fdr"

    except Exception as e:
        return None, None


# ---------------------------------------------------------------------------
# Data fetching — pykrx (fallback)
# ---------------------------------------------------------------------------

def fetch_from_pykrx(ticker, start, end, supplement_marcap=False):
    """Fetch OHLCV from pykrx (fallback only). Optionally supplement marcap.

    Args:
        ticker: stock ticker
        start: YYYYMMDD format
        end: YYYYMMDD format
        supplement_marcap: if True, also fetch market_cap/shares/trading_value
                           from pykrx market-cap endpoint. Default False.

    Returns:
        tuple: (df, source) where source is "pykrx" or None if failed
               df columns: date, ticker, open, high, low, close, volume, market_cap, shares_outstanding, trading_value
    """
    from pykrx import stock

    try:
        df = stock.get_market_ohlcv(start, end, ticker)
        time.sleep(0.3)
    except Exception as e:
        return None, None

    if df is None or df.empty:
        return None, None

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

    # Convert date to ISO string
    if "date_raw" in df.columns:
        df["date"] = pd.to_datetime(df["date_raw"]).dt.strftime("%Y-%m-%d")
    else:
        df["date"] = df.index.strftime("%Y-%m-%d") if hasattr(df.index, "strftime") else df.index

    df["ticker"] = ticker

    # Optionally supplement with market cap data from pykrx
    if supplement_marcap:
        try:
            mcap_df = fetch_market_cap_from_pykrx(ticker, start, end)
            if mcap_df is not None and not mcap_df.empty:
                df = df.merge(
                    mcap_df[["date", "market_cap", "shares_outstanding", "trading_value"]].drop_duplicates("date"),
                    on="date", how="left", suffixes=("", "_mcap")
                )
                for col in ["market_cap", "shares_outstanding", "trading_value"]:
                    mcap_col = col + "_mcap"
                    if mcap_col in df.columns:
                        df.drop(columns=[mcap_col], inplace=True)
            else:
                df["market_cap"] = None
                df["shares_outstanding"] = None
                df["trading_value"] = None
        except Exception:
            df["market_cap"] = None
            df["shares_outstanding"] = None
            df["trading_value"] = None
    else:
        # OHLCV-only mode: leave marcap fields blank so the upsert's COALESCE
        # preserves any existing PIT-safe (marcap_historical) values.
        df["market_cap"] = None
        df["shares_outstanding"] = None
        df["trading_value"] = None

    return df, "pykrx"


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_prices(conn, df, dry_run=False, supplement_marcap=False):
    """Upsert price data, filtering valid rows.

    Contract for marcap source state (designed so ingest_marcap.py is the
    sole authority for market_cap / shares_outstanding / trading_value /
    source provenance):

      * In OHLCV-only mode, market_cap / shares_outstanding / trading_value
        are forced to NULL on the EXCLUDED side, so they are NEVER pushed
        into daily_prices by this script.
      * Existing non-null market_cap / shares_outstanding / trading_value
        are NEVER overwritten by ingest_prices.py — even in supplement mode
        we only fill in when the existing column is NULL. ingest_marcap.py
        is the only caller that overwrites these fields.
      * Existing source is NEVER changed by ingest_prices.py. We only fill
        in `source` when the existing row had NULL — so a fresh row inserted
        on a date we haven't seen before lands as 'fdr' / 'pykrx', but a row
        whose source is already 'marcap_historical', 'fdr_listing_snapshot',
        'fdr+marcap', etc. is left alone. ingest_marcap.py later upgrades
        source to 'marcap_historical' on its own.

    These rules mean it is safe to run `ingest_prices.py` and
    `ingest_marcap.py` in any order, any number of times.
    """
    if df.empty:
        return 0

    if dry_run:
        return len(df)

    cur = conn.cursor()
    values = []
    for _, row in df.iterrows():
        close = row.get("close")
        if close is None or (isinstance(close, float) and pd.isna(close)) or close <= 0:
            continue

        if supplement_marcap:
            mcap = row.get("market_cap")
            shares = row.get("shares_outstanding")
            tval = row.get("trading_value")
        else:
            # OHLCV-only mode: never push marcap fields, even if FDR returned them.
            mcap = None
            shares = None
            tval = None

        values.append((
            row["ticker"], row["date"],
            row.get("open"), row.get("high"), row.get("low"), close,
            row.get("volume"),
            tval,
            mcap,
            shares,
            row.get("source", "unknown"),
        ))

    if not values:
        return 0

    # ON CONFLICT semantics:
    #   OHLCV (open/high/low/close/volume): always overwritten with EXCLUDED
    #     (FDR is the authority for OHLCV).
    #   trading_value / market_cap / shares_outstanding: fill-only — we keep
    #     whatever was there, and only insert into a NULL slot.
    #   source: fill-only — we never change an existing source.
    query = """
    INSERT INTO daily_prices (
        ticker, date, open, high, low, close,
        volume, trading_value, market_cap, shares_outstanding, source
    ) VALUES %s
    ON CONFLICT (ticker, date) DO UPDATE SET
        open   = EXCLUDED.open,
        high   = EXCLUDED.high,
        low    = EXCLUDED.low,
        close  = EXCLUDED.close,
        volume = EXCLUDED.volume,
        trading_value      = COALESCE(daily_prices.trading_value,      EXCLUDED.trading_value),
        market_cap         = COALESCE(daily_prices.market_cap,         EXCLUDED.market_cap),
        shares_outstanding = COALESCE(daily_prices.shares_outstanding, EXCLUDED.shares_outstanding),
        source             = COALESCE(daily_prices.source,             EXCLUDED.source)
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
    parser.add_argument("--universe", help="Use named universe from universe_memberships table")
    parser.add_argument("--start-date", help="Start date YYYY-MM-DD (default: 7 days ago)")
    parser.add_argument("--end-date", help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--market", help="Filter by market: KOSPI,KOSDAQ")
    parser.add_argument("--limit", type=int, help="Max tickers to process from DB")
    parser.add_argument("--batch-size", type=int, default=25, help="Upsert every N tickers (default: 25)")
    parser.add_argument("--full", action="store_true", help="Full history from 2015 (SLOW)")
    parser.add_argument("--resume", action="store_true", help="Skip tickers with full date range coverage")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be inserted but don't write")
    parser.add_argument("--debug-columns", action="store_true", help="Print FDR columns for every ticker")
    parser.add_argument(
        "--supplement-marcap-pykrx",
        action="store_true",
        help="If set, supplement missing market_cap from pykrx. Off by default — "
             "historical marcap is owned by ingest_marcap.py. Requires KRX_ID/KRX_PW "
             "in env; if those are missing the flag is auto-disabled at startup.",
    )
    args = parser.parse_args()

    # Resolve supplement-marcap mode once, up front. Avoids per-ticker pykrx
    # auth failures spamming the log.
    supplement_marcap = bool(args.supplement_marcap_pykrx)
    if supplement_marcap:
        krx_id = os.getenv("KRX_ID")
        krx_pw = os.getenv("KRX_PW")
        if not (krx_id and krx_pw):
            print(
                "WARN: --supplement-marcap-pykrx requested but KRX_ID/KRX_PW "
                "are not set in env. Disabling pykrx market-cap supplementation "
                "for this run (OHLCV-only).",
                flush=True,
            )
            supplement_marcap = False
        else:
            print("  pykrx market-cap supplementation: ENABLED", flush=True)
    else:
        print("  Mode: OHLCV-only (historical marcap handled by ingest_marcap.py)",
              flush=True)

    conn = psycopg2.connect(DATABASE_URL)

    # Resolve date range (internal format: YYYY-MM-DD)
    if args.full:
        start = "2015-01-01"
    elif args.start_date:
        start = normalize_date(args.start_date)
    else:
        start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    end = normalize_date(args.end_date) if args.end_date else datetime.now().strftime("%Y-%m-%d")

    # Resolve tickers
    if args.tickers:
        tickers = args.tickers.split(",")
    elif args.universe:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM universe_memberships WHERE universe_name = %s ORDER BY ticker", (args.universe,))
        tickers = [r[0] for r in cur.fetchall()]
        cur.close()
        if not tickers:
            print("ERROR: Universe '{}' not found or empty".format(args.universe))
            sys.exit(1)
        print("  Universe '{}': {} tickers".format(args.universe, len(tickers)))
    else:
        tickers = get_db_tickers(conn, args.limit, args.market)
        if not tickers:
            print("WARNING: No active tickers in stocks table. Use --tickers or run ingest_universe.py first.")

    log_id = log_start(conn, {
        "start": start, "end": end,
        "tickers": ",".join(tickers[:20] if len(tickers) > 20 else tickers),
        "count": len(tickers),
        "market": args.market,
        "limit": args.limit,
        "batch_size": args.batch_size,
        "full": args.full,
        "resume": args.resume,
        "dry_run": args.dry_run,
        "debug_columns": args.debug_columns,
        "supplement_marcap_pykrx": supplement_marcap,
    })

    total_rows = 0
    batch_data = []

    try:
        print("Ingesting prices from {} to {} for {} tickers...".format(start, end, len(tickers)))

        for i, ticker in enumerate(tickers):
            print("  [{}/{}] {}...".format(i + 1, len(tickers), ticker), end=" ", flush=True)

            # Check --resume condition
            if args.resume:
                latest = get_latest_date_for_ticker(conn, ticker)
                if latest and latest >= pd.to_datetime(end).date():
                    print("skip (covered)")
                    continue

            # 1. Try FDR first (primary source)
            df, source = fetch_from_fdr(
                ticker, start, end,
                debug_columns=args.debug_columns,
                supplement_marcap=supplement_marcap,
            )

            # 2. Fallback to pykrx if FDR failed or empty
            if df is None or df.empty:
                yyyymmdd_start = to_yyyymmdd(start)
                yyyymmdd_end = to_yyyymmdd(end)
                df, source = fetch_from_pykrx(
                    ticker, yyyymmdd_start, yyyymmdd_end,
                    supplement_marcap=supplement_marcap,
                )

            if df is None or df.empty:
                print("no data")
                continue

            # Add source column if not present
            if "source" not in df.columns:
                df["source"] = source

            rows = len(df)

            if not args.dry_run:
                n = upsert_prices(
                    conn, df, dry_run=False,
                    supplement_marcap=supplement_marcap,
                )
                total_rows += n
                print("{}: {} rows -> {} upserted".format(source, rows, n))
                batch_data.append((ticker, n))
            else:
                print("{}: {} rows".format(source, rows))
                total_rows += rows

            time.sleep(0.2)  # be nice to KRX servers

        # Final batch summary
        if not args.dry_run:
            print("\n  Batch summary: {} rows total".format(total_rows))

        if total_rows == 0:
            print("\nWARNING: Zero rows inserted. Possible causes:")
            print("  - FinanceDataReader/pykrx could not reach servers")
            print("  - Date range has no trading days")
            print("  - All tickers skipped by --resume")

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
