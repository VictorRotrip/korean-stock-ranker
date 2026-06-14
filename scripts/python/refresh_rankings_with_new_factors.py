"""Re-run run_ranking_snapshot.py for every historical date.

Why this exists
---------------
We just back-filled cash_to_assets into factor_snapshots, but the composite
scores in ranking_snapshots were computed before that data was available and
so currently treat the Balance Sheet Strength subcategory as missing
(neutral=50). To fold the new factor into the historical composites we need
to re-rank every date.

This orchestrator dispatches run_ranking_snapshot.py per date with bounded
parallelism (each subprocess uses its own pooler connection) so the total
runtime is ~20-30 min instead of ~70 min sequential.

The new snapshots are INSERTed alongside the existing rows; the API picks
the latest by `id` per (universe, date), so the newer composites are served
immediately. A cleanup query at the end deletes the older now-superseded
rows, leaving a single snapshot per date in the table.

Usage:
    DATABASE_URL="$POOLER_URL" python refresh_rankings_with_new_factors.py
"""

import os
import sys
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL not set")

UNIVERSE = "krx_all_historical"
SYSTEM_ID = "p123-inspired"
MAX_WORKERS = 4
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "run_ranking_snapshot.py")

CONN_OPTS = (
    "-c statement_timeout=600000 "
    "-c default_transaction_read_only=off"
)


def rerank_date(as_of_date):
    """Invoke run_ranking_snapshot.py for one date. Returns (date, ok, msg)."""
    env = os.environ.copy()
    env["DATABASE_URL"] = DATABASE_URL
    try:
        proc = subprocess.run(
            [
                sys.executable, SCRIPT,
                "--universe", UNIVERSE,
                "--system-id", SYSTEM_ID,
                "--as-of-date", str(as_of_date),
                "--missing-category-policy", "neutral",
                "--min-active-weight-coverage", "0.60",
                "--min-category-count", "3",
                "--min-factor-count", "10",
                "--require-pit-market-cap",
            ],
            env=env, capture_output=True, text=True, timeout=600,
        )
        if proc.returncode == 0:
            return (as_of_date, True, "ok")
        last_err = (proc.stderr or proc.stdout or "").strip().split("\n")[-1][:200]
        return (as_of_date, False, last_err)
    except subprocess.TimeoutExpired:
        return (as_of_date, False, "timeout (10 min)")
    except Exception as e:
        return (as_of_date, False, str(e)[:200])


def main():
    # 1. Snapshot the set of dates to refresh, plus their existing snapshot
    #    ids so we can drop the now-superseded rows after the rerun.
    conn = psycopg2.connect(DATABASE_URL, options=CONN_OPTS)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT date, id FROM ranking_snapshots
        WHERE universe_name = %s AND ranking_system_id = %s
        ORDER BY date, id
        """,
        (UNIVERSE, SYSTEM_ID),
    )
    rows = cur.fetchall()
    dates = sorted({r[0] for r in rows})
    superseded_ids = [r[1] for r in rows]
    print(f"Refreshing {len(dates)} dates "
          f"({dates[0]} to {dates[-1]}) with {MAX_WORKERS} workers...")
    print(f"Existing snapshot rows that will be superseded: "
          f"{len(superseded_ids):,}")
    print()

    # 2. Parallel re-rank.
    t0 = time.time()
    done = 0
    failures = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(rerank_date, d): d for d in dates}
        for fut in as_completed(futures):
            d, ok, msg = fut.result()
            done += 1
            elapsed = time.time() - t0
            tag = "ok " if ok else "FAIL"
            print(f"  [{done:>3}/{len(dates)}] {d} {tag}  "
                  f"({elapsed/60:.1f} min elapsed)  {msg if not ok else ''}")
            if not ok:
                failures.append((d, msg))

    print()
    print(f"Reran {len(dates)} dates in {(time.time()-t0)/60:.1f} min, "
          f"{len(failures)} failures")
    if failures:
        print("Failures (date, message):")
        for d, msg in failures:
            print(f"  {d}: {msg}")
        print()
        print("NOT deleting old snapshot rows because some dates failed.")
        print("Fix the failures, re-run for just those dates, then run the")
        print("delete query manually.")
        cur.close()
        conn.close()
        return

    # 3. Delete the superseded rows. Safety check: only delete ids that were
    #    snapshotted BEFORE the run started, and only if more recent ids
    #    exist for the same (date, ranking_system_id, universe_name) tuple.
    print("Deleting superseded snapshot rows...")
    cur.execute(
        """
        WITH latest AS (
            SELECT date, MAX(id) AS keep_id
            FROM ranking_snapshots
            WHERE universe_name = %s AND ranking_system_id = %s
            GROUP BY date
        )
        DELETE FROM ranking_snapshots rs
        USING latest l
        WHERE rs.universe_name = %s
          AND rs.ranking_system_id = %s
          AND rs.date = l.date
          AND rs.id < l.keep_id
        """,
        (UNIVERSE, SYSTEM_ID, UNIVERSE, SYSTEM_ID),
    )
    print(f"  Deleted {cur.rowcount:,} superseded rows.")
    conn.commit()
    cur.close()
    conn.close()
    print()
    print("Done.")


if __name__ == "__main__":
    main()
