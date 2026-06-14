import psycopg2, os, time
from dotenv import load_dotenv
load_dotenv()

URL = os.getenv("DATABASE_URL2") or os.getenv("DATABASE_URL")
UNIVERSE = "krx_all_historical"
SYSTEM = "p123-inspired"


def connect():
    """Fresh connection with explicit read-write + extended statement timeout."""
    c = psycopg2.connect(URL, options="-c statement_timeout=600000 -c default_transaction_read_only=off")
    c.autocommit = False
    return c


def run_with_retry(conn_holder, sql, params=None, label="", max_attempts=6):
    """Execute one DELETE, reconnecting on ReadOnlySqlTransaction.

    conn_holder is a list of length 1 (mutable) so we can swap in a fresh
    connection without changing variable scope.
    """
    for attempt in range(max_attempts):
        try:
            cur = conn_holder[0].cursor()
            cur.execute(sql, params)
            n = cur.rowcount
            conn_holder[0].commit()
            cur.close()
            return n
        except psycopg2.errors.ReadOnlySqlTransaction:
            wait = 5 * (attempt + 1)
            print(f"  {label}: read-only transaction (attempt {attempt+1}/{max_attempts}); "
                  f"reconnecting in {wait}s...", flush=True)
            try:
                conn_holder[0].rollback()
                conn_holder[0].close()
            except Exception:
                pass
            time.sleep(wait)
            conn_holder[0] = connect()
        except Exception as e:
            print(f"  {label}: {e}; rolling back", flush=True)
            try:
                conn_holder[0].rollback()
            except Exception:
                pass
            raise
    raise RuntimeError(f"{label}: exhausted reconnect attempts")


conn = [connect()]   # mutable holder

# 1. Ranking snapshots — small
n = run_with_retry(conn,
    f"DELETE FROM ranking_snapshots WHERE universe_name = %s AND ranking_system_id = %s",
    (UNIVERSE, SYSTEM), label="ranking_snapshots")
print(f"deleted {n} ranking snapshots")

# 2. Factor snapshots — date-chunked
cur = conn[0].cursor()
cur.execute("SELECT DISTINCT date FROM factor_snapshots WHERE universe_name = %s ORDER BY date", (UNIVERSE,))
dates = [r[0] for r in cur.fetchall()]
cur.close()
print(f"found {len(dates)} dates in factor_snapshots to clear")

total = 0
for i, d in enumerate(dates, 1):
    n = run_with_retry(conn,
        "DELETE FROM factor_snapshots WHERE universe_name = %s AND date = %s",
        (UNIVERSE, d),
        label=f"[{i}/{len(dates)}] {d}")
    total += n
    print(f"  [{i}/{len(dates)}] {d}: deleted {n:,} (cumulative {total:,})", flush=True)

print(f"\nTotal factor_snapshots deleted: {total:,}")
conn[0].close()
