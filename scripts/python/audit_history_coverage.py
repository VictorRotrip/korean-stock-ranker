"""
Audit history coverage across the core tables.

Prints a per-year breakdown so we know exactly what to backfill for the
10-year point-in-time backtest. Should run in under a minute.

What we want to see before extending the backtest to 2015:

  daily_prices       : >2000 tickers per year (including delisted),
                       market_cap non-null
  stocks             : delisted tickers exist, with delisting_date populated
  financial_statements
                     : >2000 unique tickers filing per year
                       (annual + quarterlies)
  fundamental_snapshots
                     : same shape, this is what factor calc reads
  ranking_snapshots  : how many monthly snapshots exist now and for what universe

Usage
-----
    python audit_history_coverage.py
"""

import os
import sys
from datetime import date

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

START_YEAR = 2014
END_YEAR = 2026


def hdr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def fmt_int(n):
    return f"{n:,}" if n is not None else "    -"


def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # ------------------------------------------------------------------
    # stocks table — how many delisted tickers do we already have?
    # ------------------------------------------------------------------
    hdr("stocks table")
    cur.execute("""
        SELECT
            COUNT(*)                                              AS total,
            COUNT(*) FILTER (WHERE is_active = TRUE)              AS active,
            COUNT(*) FILTER (WHERE is_active = FALSE)             AS inactive,
            COUNT(*) FILTER (WHERE delisting_date IS NOT NULL)    AS has_delisting_date,
            COUNT(*) FILTER (WHERE listing_date IS NOT NULL)      AS has_listing_date,
            MIN(listing_date)                                     AS earliest_listing,
            MAX(listing_date)                                     AS latest_listing
        FROM stocks
    """)
    (total, active, inactive, has_del, has_list, min_list, max_list) = cur.fetchone()
    print(f"  total:                  {fmt_int(total)}")
    print(f"  active:                 {fmt_int(active)}")
    print(f"  inactive (delisted):    {fmt_int(inactive)}")
    print(f"  has delisting_date:     {fmt_int(has_del)}")
    print(f"  has listing_date:       {fmt_int(has_list)}")
    print(f"  earliest listing_date:  {min_list}")
    print(f"  latest listing_date:    {max_list}")
    print("\n  Note: for a PIT backtest going to 2015 we need delisted tickers")
    print("        present here too. If inactive count is 0, the marcap ingest")
    print("        was scoped to today's universe only.")

    # ------------------------------------------------------------------
    # daily_prices — yearly coverage
    # ------------------------------------------------------------------
    hdr("daily_prices coverage by year")
    print(f"  {'year':<6}{'rows':>14}{'tickers':>10}{'w/marcap':>12}"
          f"{'min_date':>14}{'max_date':>14}")
    print(f"  {'-'*6}{'-'*14:>14}{'-'*10:>10}{'-'*12:>12}{'-'*14:>14}{'-'*14:>14}")
    cur.execute("""
        SELECT EXTRACT(YEAR FROM date)::int AS yr,
               COUNT(*) AS n,
               COUNT(DISTINCT ticker) AS nt,
               COUNT(market_cap) AS nmc,
               MIN(date), MAX(date)
        FROM daily_prices
        WHERE date >= '%d-01-01' AND date < '%d-01-01'
        GROUP BY yr ORDER BY yr
    """ % (START_YEAR, END_YEAR + 1))
    rows = cur.fetchall()
    if not rows:
        print("  (no rows)")
    for yr, n, nt, nmc, mind, maxd in rows:
        print(f"  {yr:<6}{fmt_int(n):>14}{fmt_int(nt):>10}{fmt_int(nmc):>12}"
              f"{str(mind):>14}{str(maxd):>14}")

    # ------------------------------------------------------------------
    # financial_statements — yearly coverage
    # ------------------------------------------------------------------
    hdr("financial_statements coverage by period_end year")
    print(f"  {'year':<6}{'filings':>14}{'tickers':>10}{'annual':>10}{'Q1':>8}{'Q2':>8}{'Q3':>8}")
    print(f"  {'-'*6}{'-'*14:>14}{'-'*10:>10}{'-'*10:>10}{'-'*8:>8}{'-'*8:>8}{'-'*8:>8}")
    cur.execute("""
        SELECT fiscal_year AS yr,
               COUNT(*) AS n,
               COUNT(DISTINCT ticker) AS nt,
               COUNT(*) FILTER (WHERE statement_type = 'annual') AS annual,
               COUNT(*) FILTER (WHERE statement_type = 'Q1') AS q1,
               COUNT(*) FILTER (WHERE statement_type = 'Q2') AS q2,
               COUNT(*) FILTER (WHERE statement_type = 'Q3') AS q3
        FROM financial_statements
        WHERE fiscal_year BETWEEN %s AND %s
        GROUP BY fiscal_year ORDER BY fiscal_year
    """, (START_YEAR, END_YEAR))
    rows = cur.fetchall()
    if not rows:
        print("  (no rows)")
    for yr, n, nt, ann, q1, q2, q3 in rows:
        print(f"  {yr:<6}{fmt_int(n):>14}{fmt_int(nt):>10}{fmt_int(ann):>10}"
              f"{fmt_int(q1):>8}{fmt_int(q2):>8}{fmt_int(q3):>8}")

    # ------------------------------------------------------------------
    # fundamental_snapshots — yearly coverage
    # ------------------------------------------------------------------
    hdr("fundamental_snapshots coverage by fiscal_year")
    print(f"  {'year':<6}{'rows':>14}{'tickers':>10}")
    print(f"  {'-'*6}{'-'*14:>14}{'-'*10:>10}")
    cur.execute("""
        SELECT fiscal_year AS yr, COUNT(*) AS n, COUNT(DISTINCT ticker) AS nt
        FROM fundamental_snapshots
        WHERE fiscal_year BETWEEN %s AND %s
        GROUP BY fiscal_year ORDER BY fiscal_year
    """, (START_YEAR, END_YEAR))
    rows = cur.fetchall()
    if not rows:
        print("  (no rows)")
    for yr, n, nt in rows:
        print(f"  {yr:<6}{fmt_int(n):>14}{fmt_int(nt):>10}")

    # ------------------------------------------------------------------
    # ranking_snapshots
    # ------------------------------------------------------------------
    hdr("ranking_snapshots — current")
    cur.execute("""
        SELECT universe_name, ranking_system_id, COUNT(*) AS n,
               MIN(date), MAX(date)
        FROM ranking_snapshots
        GROUP BY universe_name, ranking_system_id
        ORDER BY universe_name, ranking_system_id
    """)
    rows = cur.fetchall()
    if not rows:
        print("  (no snapshots)")
    print(f"  {'universe':<25}{'system':<25}{'n':>6}{'min':>14}{'max':>14}")
    for un, sys_id, n, mind, maxd in rows:
        print(f"  {str(un):<25}{str(sys_id):<25}{fmt_int(n):>6}"
              f"{str(mind):>14}{str(maxd):>14}")

    # ------------------------------------------------------------------
    # universe_memberships
    # ------------------------------------------------------------------
    hdr("universe_memberships")
    cur.execute("""
        SELECT universe_name, COUNT(*) AS n
        FROM universe_memberships
        GROUP BY universe_name ORDER BY universe_name
    """)
    rows = cur.fetchall()
    if not rows:
        print("  (no universes)")
    for un, n in rows:
        print(f"  {un:<30}{fmt_int(n):>10}")

    # ------------------------------------------------------------------
    # Gap summary
    # ------------------------------------------------------------------
    hdr("Gap summary for 2015-2024 PIT backtest")
    # rough thresholds — "good" = >1500 tickers/year, "thin" = 500-1500, "missing" = <500
    cur.execute("""
        WITH year_series AS (
            SELECT generate_series(2015, 2024) AS yr
        ),
        prc AS (
            SELECT EXTRACT(YEAR FROM date)::int AS yr,
                   COUNT(DISTINCT ticker) AS nt
            FROM daily_prices
            WHERE date BETWEEN '2015-01-01' AND '2024-12-31'
            GROUP BY EXTRACT(YEAR FROM date)::int
        ),
        fin AS (
            SELECT fiscal_year AS yr,
                   COUNT(DISTINCT ticker) AS nt
            FROM financial_statements
            WHERE fiscal_year BETWEEN 2015 AND 2024
              AND statement_type = 'annual'
            GROUP BY fiscal_year
        )
        SELECT y.yr,
               COALESCE(p.nt, 0) AS prc_tickers,
               COALESCE(f.nt, 0) AS fin_tickers
        FROM year_series y
        LEFT JOIN prc p ON p.yr = y.yr
        LEFT JOIN fin f ON f.yr = y.yr
        ORDER BY y.yr
    """)
    rows = cur.fetchall()

    def label(n, good=1500, thin=500):
        if n >= good:
            return "OK"
        if n >= thin:
            return "thin"
        return "MISSING"

    print(f"  {'year':<6}{'price tickers':>16}{'status':>10}"
          f"{'annual filings':>18}{'status':>10}")
    print(f"  {'-'*6}{'-'*16:>16}{'-'*10:>10}{'-'*18:>18}{'-'*10:>10}")
    for yr, pn, fn in rows:
        print(f"  {yr:<6}{fmt_int(pn):>16}{label(pn):>10}"
              f"{fmt_int(fn):>18}{label(fn):>10}")

    print("\nKey:")
    print("  OK     = ≥1500 tickers, good for ranking")
    print("  thin   = 500-1500 tickers, ranking will work but be noisy")
    print("  MISSING = <500 tickers, ingest needed")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
