"""
Pre-compute forward returns for the rank-performance backtester.

For each snapshot_date with a ranking_snapshots row (one per
rebalance), compute the forward total return at multiple horizons
per ticker. The webapp's /backtest page joins these with
factor_snapshots to bucket the universe into deciles and plot the
top-minus-bottom spread without shipping raw price data to the
browser.

Storage: backtest_forward_returns (primary key = ticker +
snapshot_date + horizon_days). Idempotent upsert.

Horizons (calendar days): 30 (~1m), 90 (~3m), 180 (~6m), 365 (~12m).

Lookup logic per (ticker, snapshot_date, horizon):
  - start_close  = close on snapshot_date if available, else the
                   closest close within 5 prior trading days
  - target_end   = snapshot_date + horizon_days
  - end_close    = close on target_end if available, else the
                   closest close within 5 trading days after
  - forward_return = end_close / start_close - 1
  - skip if either close missing or non-positive

Usage
-----
    # Compute returns for all snapshot dates currently in ranking_snapshots:
    python backtest_forward_returns.py

    # Restrict to one universe (so it doesn't process foreign tickers):
    python backtest_forward_returns.py --universe krx_all_current

    # Dry run:
    python backtest_forward_returns.py --dry-run

    # Override horizons:
    python backtest_forward_returns.py --horizons 30,90,180,365,730
"""

import os
import sys
import argparse
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "backtest_forward_returns"
DEFAULT_HORIZONS = [30, 90, 180, 365]   # calendar days


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

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
               rows_inserted=0, rows_updated=0,
               rows_skipped=0, error_message=None):
    cur = conn.cursor()
    cur.execute(
        "UPDATE ingestion_log "
        "SET finished_at = NOW(), status = %s, "
        "rows_processed = %s, rows_inserted = %s, "
        "rows_updated = %s, rows_skipped = %s, "
        "error_message = %s "
        "WHERE id = %s",
        (status, rows_processed, rows_inserted, rows_updated,
         rows_skipped, error_message, log_id),
    )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def get_snapshot_dates(conn, since=None, until=None):
    """Distinct ranking_snapshot dates — these are our rebalance points."""
    cur = conn.cursor()
    sql = "SELECT DISTINCT date FROM ranking_snapshots"
    params = []
    if since or until:
        sql += " WHERE 1=1"
        if since:
            sql += " AND date >= %s"
            params.append(since)
        if until:
            sql += " AND date <= %s"
            params.append(until)
    sql += " ORDER BY date"
    cur.execute(sql, params)
    rows = [r[0] for r in cur.fetchall()]
    cur.close()
    return rows


def get_universe_tickers(conn, universe_name=None):
    cur = conn.cursor()
    if universe_name:
        cur.execute(
            "SELECT ticker FROM universe_memberships "
            "WHERE universe_name = %s ORDER BY ticker",
            (universe_name,))
    else:
        cur.execute(
            "SELECT ticker FROM stocks "
            "WHERE is_active = TRUE ORDER BY ticker")
    rows = [r[0] for r in cur.fetchall()]
    cur.close()
    return rows


# ---------------------------------------------------------------------------
# Forward-return computation
# ---------------------------------------------------------------------------

def fetch_close_window(conn, tickers, start_date, end_date):
    """Return dict ticker -> sorted list of (date, close) within
    [start_date, end_date]."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, date, close
        FROM daily_prices
        WHERE ticker = ANY(%s)
          AND date >= %s AND date <= %s
          AND close IS NOT NULL AND close > 0
        ORDER BY ticker, date
    """, (tickers, start_date, end_date))
    out = {}
    for ticker, d, c in cur.fetchall():
        out.setdefault(ticker, []).append((d, float(c)))
    cur.close()
    return out


def find_close_on_or_before(rows, target_date, max_days_back=5):
    """rows = list of (date, close) sorted ascending. Return the last
    (date, close) with date <= target_date, but not more than
    max_days_back calendar days earlier. None if no match."""
    best = None
    for d, c in rows:
        if d > target_date:
            break
        best = (d, c)
    if best is None:
        return None
    days_gap = (target_date - best[0]).days
    if days_gap > max_days_back:
        return None
    return best


def find_close_on_or_after(rows, target_date, max_days_forward=5):
    """First (date, close) with date >= target_date, within
    max_days_forward calendar days. None if no match."""
    for d, c in rows:
        if d < target_date:
            continue
        days_gap = (d - target_date).days
        if days_gap > max_days_forward:
            return None
        return (d, c)
    return None


def compute_returns_for_date(conn, snapshot_date, tickers, horizons):
    """Returns list of upsert tuples for this snapshot_date."""
    max_h = max(horizons)
    window_start = snapshot_date - timedelta(days=10)
    window_end = snapshot_date + timedelta(days=max_h + 10)

    by_ticker = fetch_close_window(conn, tickers, window_start, window_end)

    out = []
    for ticker, rows in by_ticker.items():
        start = find_close_on_or_before(rows, snapshot_date)
        if start is None:
            continue
        start_date, start_close = start
        for h in horizons:
            target_end = snapshot_date + timedelta(days=h)
            end = find_close_on_or_after(rows, target_end)
            if end is None:
                # Probably the horizon stretches past the latest price
                # we have. Skip; the row simply isn't materialised yet
                # and will appear once daily_prices catches up.
                continue
            end_date, end_close = end
            forward_return = end_close / start_close - 1.0
            out.append((
                ticker, snapshot_date, h,
                forward_return,
                start_close, end_close, end_date,
            ))
    return out


def upsert_returns(conn, rows):
    if not rows:
        return 0
    cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO backtest_forward_returns
            (ticker, snapshot_date, horizon_days, forward_return,
             start_close, end_close, end_date)
        VALUES %s
        ON CONFLICT (ticker, snapshot_date, horizon_days) DO UPDATE SET
            forward_return = EXCLUDED.forward_return,
            start_close    = EXCLUDED.start_close,
            end_close      = EXCLUDED.end_close,
            end_date       = EXCLUDED.end_date,
            computed_at    = NOW()
    """, rows)
    n = cur.rowcount
    conn.commit()
    cur.close()
    return n


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pre-compute forward returns per (ticker, snapshot_date, "
                    "horizon_days) for the rank-performance backtester.")
    parser.add_argument("--universe",
                        help="Universe to scope tickers (default: all active).")
    parser.add_argument("--since",
                        help="Only process snapshot dates >= this (YYYY-MM-DD).")
    parser.add_argument("--until",
                        help="Only process snapshot dates <= this (YYYY-MM-DD).")
    parser.add_argument("--horizons", default="30,90,180,365",
                        help="Comma-separated horizon days "
                             "(default: 30,90,180,365).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute and report, but don't write.")
    args = parser.parse_args()

    horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]

    conn = psycopg2.connect(DATABASE_URL)

    print("=" * 70)
    print("Backtest forward-return precompute")
    print("  Universe:  {0}".format(args.universe or "(all active)"))
    print("  Since:     {0}".format(args.since or "(none)"))
    print("  Until:     {0}".format(args.until or "(none)"))
    print("  Horizons:  {0}".format(horizons))
    print("  Dry-run:   {0}".format(args.dry_run))
    print("=" * 70)

    dates = get_snapshot_dates(conn, since=args.since, until=args.until)
    print("\n{0} snapshot dates found in ranking_snapshots".format(len(dates)))
    if not dates:
        print("Nothing to do.")
        conn.close()
        sys.exit(0)

    tickers = get_universe_tickers(conn, args.universe)
    print("{0} tickers in scope".format(len(tickers)))

    log_id = log_start(conn, {
        "universe": args.universe, "since": args.since, "until": args.until,
        "horizons": horizons, "dry_run": args.dry_run,
    })

    n_written = 0
    n_total_rows = 0
    try:
        for d in dates:
            rows = compute_returns_for_date(conn, d, tickers, horizons)
            n_total_rows += len(rows)
            if args.dry_run:
                print("  {0}: {1} (ticker, horizon) rows".format(d, len(rows)))
            else:
                n = upsert_returns(conn, rows)
                n_written += n
                # Per-horizon coverage breakdown
                per_h = {}
                for r in rows:
                    per_h[r[2]] = per_h.get(r[2], 0) + 1
                summary = " ".join("h{0}d={1}".format(h, per_h.get(h, 0))
                                   for h in horizons)
                print("  {0}: wrote {1} rows  [{2}]".format(d, n, summary))
        status = "success"
        err = None
    except KeyboardInterrupt:
        status = "interrupted"
        err = "user cancelled"
        print("\n[INTERRUPTED] partial progress is saved.")
    except Exception as e:
        status = "error"
        err = str(e)
        print("\n[ERROR] {0}".format(e))

    log_finish(conn, log_id, status,
               rows_processed=n_total_rows,
               rows_inserted=0,
               rows_updated=n_written,
               rows_skipped=0,
               error_message=err)

    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print("  Snapshot dates processed:    {0}".format(len(dates)))
    print("  Total (ticker,horizon) rows: {0}".format(n_total_rows))
    print("  Rows written:                {0}".format(n_written))
    print("=" * 70)
    conn.close()
    print("\nDone!")
