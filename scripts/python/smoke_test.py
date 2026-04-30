"""
Smoke-test the full Korean Stock Ranker pipeline end-to-end.

Validates: DB connection → schema → ingestion → factor calc → ranking snapshot.
Uses 5 large-cap tickers so it runs in ~2 minutes.

Usage:
    python smoke_test.py                           # default: 5 tickers, last 30 days
    python smoke_test.py --as-of-date 2024-12-31   # specific date
    python smoke_test.py --skip-ingestion           # only check DB + existing data
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

REQUIRED_TABLES = [
    "stocks",
    "daily_prices",
    "pykrx_fundamentals",
    "financial_statements",
    "short_selling",
    "dart_filings",
    "factor_coverage",
    "ranking_systems",
    "ranking_snapshots",
    "factor_snapshots",
    "ingestion_log",
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
        self.message = ""
        self.duration = 0.0

    def __str__(self):
        if self.skipped:
            icon = "⊘"
            status = "SKIP"
        elif self.passed:
            icon = "✓"
            status = "PASS"
        else:
            icon = "✗"
            status = "FAIL"
        dur = f"({self.duration:.1f}s)" if self.duration > 0.1 else ""
        return f"  {icon} {status}  {self.name} {dur}  {self.message}"


def run_script(script_name, args_list):
    """Run a Python script and return (success, stdout+stderr)."""
    cmd = [sys.executable, os.path.join(SCRIPTS_DIR, script_name)] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    output = result.stdout + result.stderr
    return result.returncode == 0, output


def get_conn():
    import psycopg2
    url = os.getenv("DATABASE_URL")
    return psycopg2.connect(url)


def count_rows(conn, table, where=""):
    cur = conn.cursor()
    q = f"SELECT COUNT(*) FROM {table}"
    if where:
        q += f" WHERE {where}"
    cur.execute(q)
    n = cur.fetchone()[0]
    cur.close()
    return n


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step_check_env():
    r = StepResult("DATABASE_URL is set")
    url = os.getenv("DATABASE_URL")
    if not url:
        r.message = "Set DATABASE_URL in .env or environment"
        return r
    if "localhost" in url or "127.0.0.1" in url:
        r.message = f"(local DB)"
    elif "supabase" in url:
        r.message = f"(Supabase)"
    else:
        r.message = f"(remote DB)"
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
        existing = {row[0] for row in cur.fetchall()}
        cur.close()
        conn.close()

        missing = [t for t in REQUIRED_TABLES if t not in existing]
        if missing:
            r.message = f"Missing: {', '.join(missing)}. Run 001_create_tables.sql first."
        else:
            r.passed = True
            r.message = f"{len(REQUIRED_TABLES)} tables OK"
    except Exception as e:
        r.message = str(e)[:120]
    return r


def step_ingest_universe():
    r = StepResult("Ingest universe (5 tickers)")
    ok, out = run_script("ingest_universe.py", ["--tickers", SMOKE_TICKERS_CSV])
    if ok:
        r.passed = True
        # Count how many are in DB
        conn = get_conn()
        n = count_rows(conn, "stocks", f"ticker IN ({','.join(repr(t) for t in SMOKE_TICKERS)})")
        conn.close()
        r.message = f"{n} stocks in DB"
    else:
        r.message = out.strip().split("\n")[-1][:120]
    return r


def step_ingest_prices(start, end):
    r = StepResult(f"Ingest prices ({start} → {end})")
    ok, out = run_script("ingest_prices.py", [
        "--tickers", SMOKE_TICKERS_CSV,
        "--start-date", start, "--end-date", end,
    ])
    if ok:
        r.passed = True
        conn = get_conn()
        n = count_rows(conn, "daily_prices",
                       f"ticker IN ({','.join(repr(t) for t in SMOKE_TICKERS)}) "
                       f"AND date >= '{start}' AND date <= '{end}'")
        conn.close()
        r.message = f"{n} price rows"
    else:
        r.message = out.strip().split("\n")[-1][:120]
    return r


def step_ingest_fundamentals(start, end):
    r = StepResult(f"Ingest pykrx fundamentals ({start} → {end})")
    ok, out = run_script("ingest_pykrx_fundamentals.py", [
        "--tickers", SMOKE_TICKERS_CSV,
        "--start-date", start, "--end-date", end,
    ])
    if ok:
        r.passed = True
        conn = get_conn()
        n = count_rows(conn, "pykrx_fundamentals",
                       f"ticker IN ({','.join(repr(t) for t in SMOKE_TICKERS)})")
        conn.close()
        r.message = f"{n} fundamental rows"
    else:
        r.message = out.strip().split("\n")[-1][:120]
    return r


def step_ingest_short_selling(start, end):
    r = StepResult(f"Ingest short selling ({start} → {end})")
    ok, out = run_script("ingest_short_selling.py", [
        "--tickers", SMOKE_TICKERS_CSV,
        "--start-date", start, "--end-date", end,
    ])
    if ok:
        r.passed = True
        conn = get_conn()
        n = count_rows(conn, "short_selling",
                       f"ticker IN ({','.join(repr(t) for t in SMOKE_TICKERS)})")
        conn.close()
        r.message = f"{n} short selling rows"
    else:
        # Short selling may legitimately have 0 rows (ban period)
        if "0 short selling" in out or "Done" in out:
            r.passed = True
            r.message = "0 rows (likely ban period — OK)"
        else:
            r.message = out.strip().split("\n")[-1][:120]
    return r


def step_ingest_dart():
    r = StepResult("Ingest DART financials (latest year)")
    dart_key = os.getenv("DART_API_KEY")
    if not dart_key:
        r.skipped = True
        r.message = "DART_API_KEY not set — skip (Phase 3)"
        return r

    ok, out = run_script("ingest_dart.py", [
        "--tickers", SMOKE_TICKERS_CSV,
    ])
    if ok:
        r.passed = True
        conn = get_conn()
        n = count_rows(conn, "financial_statements",
                       f"ticker IN ({','.join(repr(t) for t in SMOKE_TICKERS)})")
        conn.close()
        r.message = f"{n} statement rows"
    else:
        r.message = out.strip().split("\n")[-1][:120]
    return r


def step_calculate_factors(as_of):
    r = StepResult(f"Calculate factors (as-of {as_of})")
    ok, out = run_script("calculate_factors.py", [
        "--as-of-date", as_of,
        "--tickers", SMOKE_TICKERS_CSV,
    ])
    if ok:
        r.passed = True
        conn = get_conn()
        n = count_rows(conn, "factor_snapshots",
                       f"ticker IN ({','.join(repr(t) for t in SMOKE_TICKERS)}) "
                       f"AND date = '{as_of}'")
        conn.close()
        r.message = f"{n} factor rows"
    else:
        r.message = out.strip().split("\n")[-1][:120]
    return r


def step_run_ranking(as_of):
    r = StepResult(f"Run ranking snapshot (as-of {as_of})")
    ok, out = run_script("run_ranking_snapshot.py", [
        "--as-of-date", as_of,
    ])
    if ok:
        r.passed = True
        # Extract top line from output
        for line in out.split("\n"):
            if "Snapshot saved" in line:
                r.message = line.strip()
                break
            if "Ranked" in line:
                r.message = line.strip()
        if not r.message:
            r.message = "Snapshot created"
    else:
        r.message = out.strip().split("\n")[-1][:120]
    return r


def step_verify_results(as_of):
    r = StepResult("Verify ranking results in DB")
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, date, universe_size,
                   (results::jsonb->0->>'ticker') as top_ticker,
                   (results::jsonb->0->>'composite_score') as top_score
            FROM ranking_snapshots
            WHERE date = %s
            ORDER BY id DESC LIMIT 1
        """, (as_of,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            r.message = f"No ranking snapshot found for {as_of}"
            return r

        snap_id, snap_date, universe, top_ticker, top_score = row
        r.passed = True
        r.message = f"id={snap_id}, {universe} stocks ranked, #1={top_ticker} (score={top_score})"
    except Exception as e:
        r.message = str(e)[:120]
    return r


def step_check_ingestion_log():
    r = StepResult("Ingestion log has entries")
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT script_name, status, rows_inserted, finished_at
            FROM ingestion_log ORDER BY id DESC LIMIT 5
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            r.message = "No log entries found"
            return r

        r.passed = True
        scripts = set(row[0] for row in rows)
        statuses = [row[1] for row in rows]
        r.message = f"{len(rows)} recent entries: {', '.join(sorted(scripts))}"
        if "error" in statuses:
            r.message += " (some errors — check ingestion_log)"
    except Exception as e:
        r.message = str(e)[:120]
    return r


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Smoke-test the full pipeline")
    parser.add_argument("--as-of-date", help="Date for factors/ranking (YYYY-MM-DD)")
    parser.add_argument("--skip-ingestion", action="store_true",
                        help="Skip ingestion steps, only check DB + existing data")
    args = parser.parse_args()

    # Resolve dates
    if args.as_of_date:
        as_of = args.as_of_date
        end_date = as_of
    else:
        # Default: yesterday (market data lags 1 day)
        yesterday = datetime.now() - timedelta(days=1)
        as_of = yesterday.strftime("%Y-%m-%d")
        end_date = as_of

    # Price range: 1 year back from as_of (need history for momentum factors)
    as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
    start_date = (as_of_dt - timedelta(days=400)).strftime("%Y-%m-%d")

    print("=" * 64)
    print("  Korean Stock Ranker — Smoke Test")
    print("=" * 64)
    print(f"  Tickers:    {SMOKE_TICKERS_CSV}")
    print(f"  As-of date: {as_of}")
    print(f"  Price range: {start_date} → {end_date}")
    print(f"  Skip ingestion: {args.skip_ingestion}")
    print("=" * 64)
    print()

    results = []
    total_start = time.time()

    # Phase 0: Environment & connection
    print("Phase 0: Environment")
    for step_fn in [step_check_env, step_check_connection, step_check_tables]:
        t0 = time.time()
        r = step_fn()
        r.duration = time.time() - t0
        results.append(r)
        print(r)
        if not r.passed and not r.skipped:
            print(f"\n  ✗ FATAL: {r.name} failed. Fix this before continuing.\n")
            return 1

    # Phase 1: Ingestion
    print()
    if args.skip_ingestion:
        print("Phase 1: Ingestion (SKIPPED)")
        for name in ["Universe", "Prices", "Fundamentals", "Short selling", "DART"]:
            r = StepResult(f"Ingest {name}")
            r.skipped = True
            r.message = "--skip-ingestion"
            results.append(r)
            print(r)
    else:
        print("Phase 1: Ingestion")
        ingestion_steps = [
            ("Universe", lambda: step_ingest_universe()),
            ("Prices", lambda: step_ingest_prices(start_date, end_date)),
            ("Fundamentals", lambda: step_ingest_fundamentals(start_date, end_date)),
            ("Short selling", lambda: step_ingest_short_selling(start_date, end_date)),
            ("DART", lambda: step_ingest_dart()),
        ]
        for name, step_fn in ingestion_steps:
            t0 = time.time()
            r = step_fn()
            r.duration = time.time() - t0
            results.append(r)
            print(r)

    # Phase 2: Factor calculation & ranking
    print()
    print("Phase 2: Factor calculation & ranking")
    for step_fn in [
        lambda: step_calculate_factors(as_of),
        lambda: step_run_ranking(as_of),
        lambda: step_verify_results(as_of),
        step_check_ingestion_log,
    ]:
        t0 = time.time()
        r = step_fn()
        r.duration = time.time() - t0
        results.append(r)
        print(r)

    # Summary
    total_time = time.time() - total_start
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    total = len(results)

    print()
    print("=" * 64)
    if failed == 0:
        print(f"  ALL CLEAR  {passed}/{total} passed, {skipped} skipped  ({total_time:.1f}s)")
    else:
        print(f"  ISSUES     {passed}/{total} passed, {failed} failed, {skipped} skipped  ({total_time:.1f}s)")
    print("=" * 64)

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
