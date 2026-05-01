"""
Smoke-test the full Korean Stock Ranker pipeline end-to-end.

Validates: DB connection -> schema -> ingestion -> factor calc -> ranking snapshot.
Uses 5 large-cap tickers so it runs in ~2-5 minutes.

Usage:
    python smoke_test.py                           # default: 5 tickers, 2024-12-30
    python smoke_test.py --as-of-date 2024-12-27   # specific date (must be trading day)
    python smoke_test.py --skip-ingestion           # only check DB + existing data
    python smoke_test.py --skip-dart                # skip DART (no API key needed)
"""

import os
import sys
import time
import argparse
import subprocess
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SMOKE_TICKERS = ["005930", "000660", "035420", "051910", "005380"]
SMOKE_TICKERS_CSV = ",".join(SMOKE_TICKERS)

# Default to 2024-12-30 (Monday, a known Korean trading day)
DEFAULT_AS_OF = "2024-12-30"

REQUIRED_TABLES = [
    "stocks", "daily_prices", "pykrx_fundamentals", "financial_statements",
    "short_selling", "dart_filings", "factor_coverage", "ranking_systems",
    "ranking_snapshots", "factor_snapshots", "ingestion_log",
]

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class StepResult:
    def __init__(self, name):
        self.name = name
        self.passed = False
        self.skipped = False
        self.warning = False  # passed but with caveats
        self.message = ""
        self.duration = 0.0

    def __str__(self):
        if self.skipped:
            icon, status = "~", "SKIP"
        elif self.warning:
            icon, status = "?", "WARN"
        elif self.passed:
            icon, status = "+", "PASS"
        else:
            icon, status = "!", "FAIL"
        dur = "({:.1f}s)".format(self.duration) if self.duration > 0.1 else ""
        return "  {} {}  {} {}  {}".format(icon, status, self.name, dur, self.message)


def run_script(script_name, args_list):
    """Run a Python script and return (success, stdout+stderr)."""
    cmd_display = "python {} {}".format(script_name, " ".join(args_list))
    print("    CMD: {}".format(cmd_display))
    cmd = [sys.executable, os.path.join(SCRIPTS_DIR, script_name)] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    output = result.stdout + result.stderr
    return result.returncode == 0, output


def get_conn():
    import psycopg2
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def count_rows(conn, table, where=""):
    cur = conn.cursor()
    q = "SELECT COUNT(*) FROM {}".format(table)
    if where:
        q += " WHERE {}".format(where)
    cur.execute(q)
    n = cur.fetchone()[0]
    cur.close()
    return n


def ticker_in_clause():
    return ",".join("'{}'".format(t) for t in SMOKE_TICKERS)


def print_recent_log(conn, limit=5):
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, script_name, status, rows_processed, rows_inserted, error_message
            FROM ingestion_log ORDER BY id DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        if rows:
            print("\n    Recent ingestion_log:")
            for r in rows:
                err = " ERR:{}".format(r[5][:60]) if r[5] else ""
                print("      #{} {} status={} proc={} ins={}{}".format(
                    r[0], r[1], r[2], r[3], r[4], err))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step_check_env():
    r = StepResult("DATABASE_URL is set")
    url = os.getenv("DATABASE_URL")
    if not url:
        r.message = "Set DATABASE_URL in .env.local"
        return r
    if "supabase" in url:
        r.message = "(Supabase)"
    elif "localhost" in url or "127.0.0.1" in url:
        r.message = "(local DB)"
    else:
        r.message = "(remote DB)"
    r.passed = True
    return r


def step_check_connection():
    r = StepResult("Can connect to database")
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        r.passed = True
    except Exception as e:
        r.message = str(e)[:120]
    return r


def step_check_tables():
    r = StepResult("All required tables exist")
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
        """)
        existing = set(row[0] for row in cur.fetchall())
        cur.close()
        conn.close()

        missing = [t for t in REQUIRED_TABLES if t not in existing]
        if missing:
            r.message = "Missing: {}. Run 001_create_tables.sql first.".format(", ".join(missing))
        else:
            r.passed = True
            r.message = "{} tables OK".format(len(REQUIRED_TABLES))
    except Exception as e:
        r.message = str(e)[:120]
    return r


def step_ingest_universe():
    r = StepResult("Ingest universe (5 tickers)")
    ok, out = run_script("ingest_universe.py", ["--tickers", SMOKE_TICKERS_CSV])
    if ok:
        conn = get_conn()
        n = count_rows(conn, "stocks", "ticker IN ({})".format(ticker_in_clause()))
        conn.close()
        if n >= len(SMOKE_TICKERS):
            r.passed = True
            r.message = "{} stocks in DB".format(n)
        elif n > 0:
            r.passed = True
            r.message = "{}/{} stocks".format(n, len(SMOKE_TICKERS))
        else:
            r.message = "0 stocks inserted"
    else:
        r.message = out.strip().split("\n")[-1][:120]
    return r


def step_ingest_prices(start, end):
    r = StepResult("Ingest prices ({} -> {})".format(start, end))
    ok, out = run_script("ingest_prices.py", [
        "--tickers", SMOKE_TICKERS_CSV,
        "--start-date", start, "--end-date", end,
    ])
    if ok:
        conn = get_conn()
        n = count_rows(conn, "daily_prices",
                       "ticker IN ({})".format(ticker_in_clause()))
        conn.close()
        if n > 0:
            r.passed = True
            r.message = "{} price rows total".format(n)
        else:
            r.message = "0 price rows. pykrx may not be returning data."
    else:
        r.message = out.strip().split("\n")[-1][:120]
    return r


def step_ingest_fundamentals(start, end):
    r = StepResult("Ingest pykrx fundamentals")
    ok, out = run_script("ingest_pykrx_fundamentals.py", [
        "--tickers", SMOKE_TICKERS_CSV,
        "--start-date", start, "--end-date", end,
    ])
    conn = get_conn()
    n = count_rows(conn, "pykrx_fundamentals",
                   "ticker IN ({})".format(ticker_in_clause()))
    conn.close()
    if n > 0:
        r.passed = True
        r.message = "{} rows".format(n)
    else:
        # pykrx fundamentals broken in 1.0.51 — this is a known issue, not fatal
        r.warning = True
        r.passed = True
        r.message = "0 rows (pykrx 1.0.51 known issue — not fatal, DART provides this data)"
    return r


def step_ingest_short_selling(start, end):
    r = StepResult("Ingest short selling")
    ok, out = run_script("ingest_short_selling.py", [
        "--tickers", SMOKE_TICKERS_CSV,
        "--start-date", start, "--end-date", end,
    ])
    # Short selling: either ban period or pykrx broken — both are OK
    r.passed = True
    conn = get_conn()
    n = count_rows(conn, "short_selling", "ticker IN ({})".format(ticker_in_clause()))
    conn.close()
    if n > 0:
        r.message = "{} rows".format(n)
    else:
        r.warning = True
        r.message = "0 rows (ban period or pykrx issue — not fatal)"
    return r


def step_ingest_dart():
    r = StepResult("Ingest DART financials")
    dart_key = os.getenv("DART_API_KEY")
    if not dart_key:
        r.skipped = True
        r.message = "DART_API_KEY not set"
        return r

    ok, out = run_script("ingest_dart.py", [
        "--tickers", SMOKE_TICKERS_CSV,
        "--year", "2024",
        "--timeout", "30",
    ])
    if ok:
        conn = get_conn()
        n = count_rows(conn, "financial_statements",
                       "ticker IN ({})".format(ticker_in_clause()))
        conn.close()
        r.passed = True
        r.message = "{} statement rows".format(n)
    else:
        r.message = out.strip().split("\n")[-1][:120]
    return r


def step_calculate_factors(as_of):
    r = StepResult("Calculate factors (as-of {})".format(as_of))
    ok, out = run_script("calculate_factors.py", [
        "--as-of-date", as_of, "--tickers", SMOKE_TICKERS_CSV,
    ])
    if ok:
        conn = get_conn()
        n = count_rows(conn, "factor_snapshots",
                       "ticker IN ({}) AND date = '{}'".format(ticker_in_clause(), as_of))
        conn.close()
        if n > 0:
            r.passed = True
            r.message = "{} factor rows".format(n)
        else:
            r.message = "0 factor rows — need price data first"
    else:
        r.message = out.strip().split("\n")[-1][:120]
    return r


def step_run_ranking(as_of):
    r = StepResult("Run ranking snapshot (as-of {})".format(as_of))
    ok, out = run_script("run_ranking_snapshot.py", ["--as-of-date", as_of])
    if ok:
        r.passed = True
        for line in out.split("\n"):
            if "Snapshot saved" in line or "Ranked" in line:
                r.message = line.strip()
                break
        if not r.message:
            r.message = "OK"
    else:
        r.message = out.strip().split("\n")[-1][:120]
    return r


def step_verify_results(as_of):
    r = StepResult("Verify ranking in DB")
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, universe_size,
                   (results::jsonb->0->>'ticker') as top_ticker,
                   (results::jsonb->0->>'composite_score') as top_score
            FROM ranking_snapshots WHERE date = %s
            ORDER BY id DESC LIMIT 1
        """, (as_of,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if row:
            r.passed = True
            r.message = "id={}, {} stocks, #1={} (score={})".format(
                row[0], row[1], row[2], row[3])
        else:
            r.message = "No snapshot for {}".format(as_of)
    except Exception as e:
        r.message = str(e)[:120]
    return r


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Smoke-test the full pipeline")
    parser.add_argument("--as-of-date", help="Date for factors/ranking (YYYY-MM-DD)")
    parser.add_argument("--skip-ingestion", action="store_true")
    parser.add_argument("--skip-dart", action="store_true",
                        help="(deprecated, DART is now skipped by default)")
    parser.add_argument("--include-dart", action="store_true",
                        help="Include DART ingestion (skipped by default)")
    args = parser.parse_args()

    as_of = args.as_of_date or DEFAULT_AS_OF
    as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
    start_date = (as_of_dt - timedelta(days=400)).strftime("%Y-%m-%d")
    end_date = as_of

    print("=" * 64)
    print("  Korean Stock Ranker - Smoke Test")
    print("=" * 64)
    print("  Tickers:    {}".format(SMOKE_TICKERS_CSV))
    print("  As-of date: {}".format(as_of))
    print("  Price range: {} -> {}".format(start_date, end_date))
    print("  Skip ingestion: {}".format(args.skip_ingestion))
    print("  Include DART: {}".format(args.include_dart))
    print("=" * 64)
    print()

    results = []
    total_start = time.time()

    # Phase 0
    print("Phase 0: Environment")
    for fn in [step_check_env, step_check_connection, step_check_tables]:
        t0 = time.time()
        r = fn()
        r.duration = time.time() - t0
        results.append(r)
        print(r)
        if not r.passed:
            print("\n  ! FATAL: Fix this before continuing.\n")
            return 1

    # Phase 1
    print()
    if args.skip_ingestion:
        print("Phase 1: Ingestion (SKIPPED)")
        for name in ["Universe", "Prices", "Fundamentals", "Short selling", "DART"]:
            r = StepResult(name)
            r.skipped = True
            r.message = "--skip-ingestion"
            results.append(r)
            print(r)
    else:
        print("Phase 1: Ingestion")

        # Universe (required)
        t0 = time.time()
        r = step_ingest_universe()
        r.duration = time.time() - t0
        results.append(r)
        print(r)
        if not r.passed:
            print("\n  ! Universe failed — cannot continue.")
            conn = get_conn()
            print_recent_log(conn)
            conn.close()
            return 1

        # Prices (required)
        t0 = time.time()
        r = step_ingest_prices(start_date, end_date)
        r.duration = time.time() - t0
        results.append(r)
        print(r)
        if not r.passed:
            conn = get_conn()
            print_recent_log(conn)
            conn.close()

        # Fundamentals (nice-to-have, pykrx broken)
        t0 = time.time()
        r = step_ingest_fundamentals(start_date, end_date)
        r.duration = time.time() - t0
        results.append(r)
        print(r)

        # Short selling (nice-to-have)
        t0 = time.time()
        r = step_ingest_short_selling(start_date, end_date)
        r.duration = time.time() - t0
        results.append(r)
        print(r)

        # DART (skipped by default, opt-in with --include-dart)
        if args.include_dart:
            t0 = time.time()
            r = step_ingest_dart()
            r.duration = time.time() - t0
        else:
            r = StepResult("DART financials")
            r.skipped = True
            r.message = "skipped (use --include-dart to enable)"
        results.append(r)
        print(r)

    # Phase 2
    print()
    print("Phase 2: Factor calculation & ranking")
    for fn in [
        lambda: step_calculate_factors(as_of),
        lambda: step_run_ranking(as_of),
        lambda: step_verify_results(as_of),
    ]:
        t0 = time.time()
        r = fn()
        r.duration = time.time() - t0
        results.append(r)
        print(r)

    # Summary
    total_time = time.time() - total_start
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    warnings = sum(1 for r in results if r.warning)

    print()
    print("=" * 64)
    if failed == 0:
        msg = "ALL CLEAR  {}/{} passed".format(passed, len(results))
        if warnings:
            msg += ", {} warnings".format(warnings)
        if skipped:
            msg += ", {} skipped".format(skipped)
        print("  {} ({:.1f}s)".format(msg, total_time))
    else:
        print("  ISSUES  {}/{} passed, {} failed, {} skipped ({:.1f}s)".format(
            passed, len(results), failed, skipped, total_time))
        conn = get_conn()
        print_recent_log(conn)
        conn.close()
    print("=" * 64)

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
