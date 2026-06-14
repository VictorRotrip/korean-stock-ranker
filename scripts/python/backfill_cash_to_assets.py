"""One-off backfill for the cash_to_assets factor.

Why this exists
---------------
cash_to_assets was added to factor_definitions.py and is wired into the
P123 ranking tree, but the calculator originally read fin.get("cash") while
the TTM pipeline (fundamental_ttm.py) keys the field as
"cash_and_equivalents". As a result the factor came back None for every
ticker x date and never landed in factor_snapshots during the 10-year
backfill.

This script fixes the gap directly by:
  1. Loading every distinct date in ranking_snapshots for krx_all_historical.
  2. For each date, fetching the latest PIT-safe fundamental_snapshots row
     per universe ticker (cash_and_equivalents, total_assets).
  3. Computing cash / total_assets, percentile-ranking within the universe,
     and upserting into factor_snapshots with factor_id = 'cash_to_assets'.

Run with:
    DATABASE_URL="$POOLER_URL" python backfill_cash_to_assets.py
"""

import os
import sys
import psycopg2
from psycopg2.extras import execute_values

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL not set")

CONN_OPTS = (
    "-c statement_timeout=600000 "
    "-c default_transaction_read_only=off"
)
UNIVERSE = "krx_all_historical"
FACTOR_ID = "cash_to_assets"


def safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return float(a) / float(b)


def percentile_rank(values_dict):
    """Higher value = higher percentile. Ties get average rank. 0-100 scale.

    Matches the convention used elsewhere in the ranking pipeline
    (percentile_rank in calculate_factors.py).
    """
    items = [(t, v) for t, v in values_dict.items() if v is not None]
    if len(items) < 2:
        return {t: 50.0 for t, _ in items}
    items.sort(key=lambda x: x[1])
    n = len(items)
    ranks = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        # 1-indexed average rank within the tied block.
        avg_rank = (i + j + 2) / 2.0
        # Convert to a 0-100 percentile (higher value -> higher percentile).
        pct = (avg_rank - 0.5) / n * 100.0
        for k in range(i, j + 1):
            ranks[items[k][0]] = pct
        i = j + 1
    return ranks


def main():
    conn = psycopg2.connect(DATABASE_URL, options=CONN_OPTS)
    cur = conn.cursor()

    # 1. Universe tickers (canonical from universe_memberships).
    cur.execute(
        "SELECT DISTINCT ticker FROM universe_memberships "
        "WHERE universe_name = %s",
        (UNIVERSE,),
    )
    universe_tickers = [r[0] for r in cur.fetchall()]
    print(f"Universe '{UNIVERSE}': {len(universe_tickers)} tickers")

    # 2. Dates needing backfill (any date with ranking_snapshots for this
    #    universe — we want cash_to_assets coverage to match the broader
    #    factor_snapshots coverage so composites can fold it in).
    cur.execute(
        "SELECT DISTINCT date FROM ranking_snapshots "
        "WHERE universe_name = %s ORDER BY date",
        (UNIVERSE,),
    )
    dates = [r[0] for r in cur.fetchall()]
    print(f"Backfilling {len(dates)} dates from {dates[0]} to {dates[-1]}")
    print()

    total_with_data = 0
    for idx, as_of in enumerate(dates, 1):
        # 3. Latest PIT-safe consolidated filing per ticker. We read from
        #    `financial_statements` directly because the cash field never
        #    got mapped into `fundamental_snapshots.cash_and_equivalents`
        #    (a bug in normalize_dart_financials.py:207-210 — the alias
        #    table is keyed by fs-column names but iterated by snap-column
        #    names, so the alias is never applied). Reading from the
        #    source-of-truth sidesteps the normalization bug.
        cur.execute(
            """
            SELECT t.ticker, fs.cash, fs.total_assets
            FROM unnest(%s::text[]) AS t(ticker)
            LEFT JOIN LATERAL (
                SELECT cash, total_assets
                FROM financial_statements
                WHERE ticker = t.ticker
                  AND data_available_date <= %s
                  AND consolidated_or_separate = 'consolidated'
                  AND total_assets IS NOT NULL
                  AND cash IS NOT NULL
                ORDER BY period_end DESC,
                         COALESCE(fiscal_quarter, 99) DESC
                LIMIT 1
            ) fs ON true
            """,
            (universe_tickers, as_of),
        )
        rows = cur.fetchall()

        # 4. Compute the ratio per ticker.
        values = {}
        for ticker, cash, assets in rows:
            v = safe_div(cash, assets)
            if v is not None:
                values[ticker] = v

        # 5. Percentile-rank across all tickers that have a value.
        ranked = percentile_rank(values)

        # 6. Build upsert rows: a row per universe ticker, with NULLs for
        #    those that have no value. Mirrors how calculate_factors.py
        #    writes "no data" rows so the snapshot is dense.
        upsert_rows = []
        for ticker in universe_tickers:
            v = values.get(ticker)
            pct = ranked.get(ticker)
            if v is None:
                upsert_rows.append((
                    ticker, FACTOR_ID, as_of,
                    None,             # raw_value
                    None,             # percentile_rank
                    "no_data",        # source
                    "global",         # scope
                    None,             # scope_fallback
                    "no_data",        # missing_reason
                    UNIVERSE,
                ))
            else:
                upsert_rows.append((
                    ticker, FACTOR_ID, as_of,
                    float(v),
                    float(pct) if pct is not None else None,
                    "calculated",
                    "global",
                    None,
                    None,
                    UNIVERSE,
                ))

        # 7. Bulk upsert.
        execute_values(
            cur,
            """
            INSERT INTO factor_snapshots
                (ticker, factor_id, date, raw_value, percentile_rank,
                 source, scope, scope_fallback, missing_reason,
                 universe_name)
            VALUES %s
            ON CONFLICT (ticker, factor_id, date, universe_name)
            DO UPDATE SET
                raw_value       = EXCLUDED.raw_value,
                percentile_rank = EXCLUDED.percentile_rank,
                source          = EXCLUDED.source,
                missing_reason  = EXCLUDED.missing_reason
            """,
            upsert_rows,
            page_size=500,
        )
        conn.commit()

        with_data = len(values)
        total_with_data += with_data
        print(f"  [{idx:>3}/{len(dates)}] {as_of}: "
              f"{with_data:>5} tickers with data "
              f"(avg ratio {sum(values.values())/max(with_data,1):.3f})")

    cur.close()
    conn.close()
    print()
    print(f"Done! Inserted/updated cash_to_assets for {len(dates)} dates.")
    print(f"Total ticker-date pairs with data: {total_with_data:,}")


if __name__ == "__main__":
    main()
