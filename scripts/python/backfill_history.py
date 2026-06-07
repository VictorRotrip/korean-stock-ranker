"""
Backfill historical factor and ranking snapshots for the backtester.

For each rebalance date in a generated list (default: monthly month-ends
from 2024-01-31 through today), this script runs:

  1. calculate_factors.py    — populates factor_snapshots for that date
  2. run_ranking_snapshot.py — composites them into ranking_snapshots

It skips dates that already have a ranking_snapshots row for the
selected universe + system, so re-running picks up where it stopped.

Why this exists
---------------
The /backtest page needs historical composite scores to bucket
the universe into deciles at past rebalance dates. The Python pipeline
is point-in-time-safe (financial_statements.data_available_date <= as_of
filters the inputs), so re-running calculate_factors with a 2024 date
gives a clean PIT snapshot — provided the DART parser is correct (now
true after the SCE fix).

This script does NOT re-fetch DART or daily_prices — those are assumed
already ingested. It only runs the two compute steps.

Run time: ~5 min per date for calculate_factors + ~10s for the rank
snapshot. 28 monthly dates ≈ 2.5 hours sequential.

Usage
-----
    python backfill_history.py --dry-run
    python backfill_history.py
    python backfill_history.py --start 2024-01-31 --end 2026-04-30
    python backfill_history.py --dates 2024-01-31,2024-06-28,2024-12-31
"""

import os
import sys
import argparse
import subprocess
from datetime import datetime, date, timedelta
from calendar import monthrange

import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "backfill_history"
DEFAULT_UNIVERSE = "krx_all_current"
DEFAULT_SYSTEM = "p123-inspired"


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


def log_finish(conn, log_id, status, rows_processed=0, rows_updated=0,
               rows_skipped=0, error_message=None):
    cur = conn.cursor()
    cur.execute(
        "UPDATE ingestion_log "
        "SET finished_at = NOW(), status = %s, "
        "rows_processed = %s, rows_updated = %s, "
        "rows_skipped = %s, error_message = %s "
        "WHERE id = %s",
        (status, rows_processed, rows_updated, rows_skipped,
         error_message, log_id),
    )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Date generation
# ---------------------------------------------------------------------------

def month_ends_between(start_date, end_date):
    """List of month-end dates from start_date through end_date inclusive."""
    out = []
    y, m = start_date.year, start_date.month
    while True:
        last_day = monthrange(y, m)[1]
        d = date(y, m, last_day)
        if d > end_date:
            break
        if d >= start_date:
            out.append(d)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def nearest_trading_date(conn, target_date):
    """Return the closest daily_prices.date on or before target_date.
    Falls back to target_date itself if no prior price exists (the
    pipeline will then likely warn or no-op)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT MAX(date) FROM daily_prices WHERE date <= %s",
        (target_date,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row and row[0] else target_date


# ---------------------------------------------------------------------------
# Skip-already-done check
# ---------------------------------------------------------------------------

def already_done(conn, as_of, universe, system_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM ranking_snapshots
        WHERE date = %s AND universe_name = %s AND ranking_system_id = %s
        LIMIT 1
    """, (as_of, universe, system_id))
    found = cur.fetchone() is not None
    cur.close()
    return found


# ---------------------------------------------------------------------------
# Step runners
# ---------------------------------------------------------------------------

def run_calc_factors(as_of, universe, allow_snapshot_marcap):
    cmd = [
        sys.executable, "calculate_factors.py",
        "--universe", universe,
        "--as-of-date", str(as_of),
    ]
    if allow_snapshot_marcap:
        cmd.append("--allow-snapshot-market-cap")
    else:
        cmd.append("--require-pit-market-cap")
    print("\n  $ " + " ".join(cmd))
    # stdin=DEVNULL is critical when this script is run under nohup. The
    # parent's stdin gets detached by nohup, so children inheriting it
    # may fail to initialise sys.stdin/stdout/stderr after a few
    # iterations ("Fatal Python error: init_sys_streams"). Explicitly
    # giving each child /dev/null avoids that fd inheritance trap.
    proc = subprocess.run(cmd, check=False, stdin=subprocess.DEVNULL)
    return proc.returncode == 0


def run_rank_snapshot(as_of, universe, allow_snapshot_marcap):
    cmd = [
        sys.executable, "run_ranking_snapshot.py",
        "--universe", universe,
        "--as-of-date", str(as_of),
        "--missing-category-policy", "neutral",
        "--min-active-weight-coverage", "0.60",
        "--min-category-count", "3",
        "--min-factor-count", "10",
    ]
    if allow_snapshot_marcap:
        cmd.append("--allow-snapshot-market-cap")
    else:
        # Explicit PIT-strict for the backtest pipeline. If historical
        # marcap isn't ingested, the ranking step will refuse the run
        # rather than silently produce biased value factors.
        cmd.append("--require-pit-market-cap")
    print("\n  $ " + " ".join(cmd))
    proc = subprocess.run(cmd, check=False, stdin=subprocess.DEVNULL)
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill historical factor + ranking snapshots for the "
                    "rank-performance backtester.")
    parser.add_argument("--start", default="2024-01-31",
                        help="Start date YYYY-MM-DD (default: 2024-01-31).")
    parser.add_argument("--end",
                        help="End date YYYY-MM-DD (default: today).")
    parser.add_argument("--dates",
                        help="Explicit comma-separated list of dates "
                             "(overrides --start/--end).")
    parser.add_argument("--universe", default=DEFAULT_UNIVERSE,
                        help="Universe name (default: krx_all_current).")
    parser.add_argument("--system", default=DEFAULT_SYSTEM,
                        help="Ranking system id (default: p123-inspired).")
    # Default to PIT-strict (no snapshot fallback). For a historical
    # backtest, using today's market cap on a 2024-01-31 date would
    # corrupt every value factor — silently passing snapshot would
    # produce a nonsense backtest. Users must opt in to snapshot mode.
    parser.add_argument("--allow-snapshot-market-cap", action="store_true",
                        default=False,
                        help="Permit snapshot (current) market_cap when "
                             "PIT-correct historical marcap is missing. "
                             "OFF BY DEFAULT for backfill — using current "
                             "market cap on a historical date corrupts "
                             "value factors. Run ingest_marcap.py "
                             "--source historical first.")
    parser.add_argument("--snap-to-trading-day", action="store_true",
                        default=True,
                        help="If a month-end falls on a weekend or holiday, "
                             "snap to the nearest prior trading day. "
                             "Default: on.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan, don't run anything.")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)

    # Date list
    if args.dates:
        dates = [datetime.strptime(d.strip(), "%Y-%m-%d").date()
                 for d in args.dates.split(",") if d.strip()]
    else:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = (datetime.strptime(args.end, "%Y-%m-%d").date()
               if args.end else date.today())
        dates = month_ends_between(start, end)

    if args.snap_to_trading_day:
        dates = [nearest_trading_date(conn, d) for d in dates]
        # De-dupe in case two month-ends snap to the same Friday
        seen = set()
        deduped = []
        for d in dates:
            if d not in seen:
                seen.add(d)
                deduped.append(d)
        dates = deduped

    print("=" * 70)
    print("Backfill history")
    print("  Universe:                 {0}".format(args.universe))
    print("  System:                   {0}".format(args.system))
    print("  Allow snapshot market-cap: {0}".format(args.allow_snapshot_market_cap))
    print("  Snap to trading day:       {0}".format(args.snap_to_trading_day))
    print("  Dry-run:                   {0}".format(args.dry_run))
    print("  Dates ({0}):".format(len(dates)))
    for d in dates:
        marker = "  [done]" if already_done(conn, d, args.universe, args.system) else ""
        print("    {0}{1}".format(d, marker))
    print("=" * 70)

    pending = [d for d in dates
               if not already_done(conn, d, args.universe, args.system)]
    print("\n{0} dates need to be computed (others already have a snapshot)."
          .format(len(pending)))

    if not pending:
        print("Nothing to do.")
        conn.close()
        sys.exit(0)

    if args.dry_run:
        print("\nDry-run; exiting without running pipelines.")
        conn.close()
        sys.exit(0)

    log_id = log_start(conn, {
        "universe": args.universe, "system": args.system,
        "start": args.start, "end": args.end, "dates": args.dates,
        "snap_to_trading_day": args.snap_to_trading_day,
        "allow_snapshot_market_cap": args.allow_snapshot_market_cap,
    })

    n_ok = 0
    n_failed = 0
    n_skipped = 0
    MAX_ATTEMPTS = 3
    try:
        import time as _time
        for i, d in enumerate(pending, 1):
            print("\n" + "=" * 70)
            print("[{0}/{1}] {2}".format(i, len(pending), d))
            print("=" * 70)
            # Per-date retry. calculate_factors.py and run_ranking_snapshot.py
            # have no built-in DB-reconnect logic, so a brief Supabase pooler
            # hiccup mid-run will return a non-zero exit code. Retrying with
            # backoff (30s, 60s) gives the network time to settle without
            # losing the date.
            ok1 = ok2 = False
            for attempt in range(1, MAX_ATTEMPTS + 1):
                ok1 = run_calc_factors(d, args.universe,
                                       args.allow_snapshot_market_cap)
                if ok1:
                    break
                if attempt < MAX_ATTEMPTS:
                    wait = 30 * attempt
                    print("  ! calculate_factors failed (attempt {0}/{1}); "
                          "waiting {2}s before retry...".format(
                              attempt, MAX_ATTEMPTS, wait))
                    _time.sleep(wait)
            if not ok1:
                print("  ! calculate_factors failed after {0} attempts; "
                      "skipping ranking step.".format(MAX_ATTEMPTS))
                n_failed += 1
                continue
            for attempt in range(1, MAX_ATTEMPTS + 1):
                ok2 = run_rank_snapshot(d, args.universe,
                                        args.allow_snapshot_market_cap)
                if ok2:
                    break
                if attempt < MAX_ATTEMPTS:
                    wait = 30 * attempt
                    print("  ! run_ranking_snapshot failed (attempt {0}/{1}); "
                          "waiting {2}s before retry...".format(
                              attempt, MAX_ATTEMPTS, wait))
                    _time.sleep(wait)
            if not ok2:
                print("  ! run_ranking_snapshot failed after {0} attempts.".format(
                    MAX_ATTEMPTS))
                n_failed += 1
                continue
            n_ok += 1
        status = "success"
        err = None
    except KeyboardInterrupt:
        status = "interrupted"
        err = "user cancelled"
        print("\n[INTERRUPTED]")
    except Exception as e:
        status = "error"
        err = str(e)
        print("\n[ERROR] {0}".format(e))

    log_finish(conn, log_id, status,
               rows_processed=len(pending),
               rows_updated=n_ok,
               rows_skipped=n_skipped,
               error_message=err)

    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print("  Dates planned:   {0}".format(len(dates)))
    print("  Dates pending:   {0}".format(len(pending)))
    print("  Succeeded:       {0}".format(n_ok))
    print("  Failed:          {0}".format(n_failed))
    print("=" * 70)
    conn.close()

    print("\nNext step:")
    print("  python backtest_forward_returns.py --universe {0}".format(
        args.universe))
    print("\nDone!")
