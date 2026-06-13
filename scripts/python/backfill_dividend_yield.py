"""One-off backfill for the dividend_yield factor.

dividend_yield = abs(dividends_paid) / market_cap

Reads dividends_paid from financial_statements (the source-of-truth, since
the fundamental_snapshots normalization has a known bug that prevents the
field from making it across). Market cap is the PIT-safe value from
daily_prices (source = 'marcap_historical') at each as_of date.

Run with:
    DATABASE_URL="$POOLER_URL" python backfill_dividend_yield.py
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
FACTOR_ID = "dividend_yield"


def safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return float(a) / float(b)


def percentile_rank(values_dict):
    """Higher value = higher percentile. Ties get average rank."""
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
        avg_rank = (i + j + 2) / 2.0
        pct = (avg_rank - 0.5) / n * 100.0
        for k in range(i, j + 1):
            ranks[items[k][0]] = pct
        i = j + 1
    return ranks


def main():
    conn = psycopg2.connect(DATABASE_URL, options=CONN_OPTS)
    cur = conn.cursor()

    cur.execute(
        "SELECT DISTINCT ticker FROM universe_memberships "
        "WHERE universe_name = %s",
        (UNIVERSE,),
    )
    universe_tickers = [r[0] for r in cur.fetchall()]
    print(f"Universe '{UNIVERSE}': {len(universe_tickers)} tickers")

    cur.execute(
        "SELECT DISTINCT date FROM ranking_snapshots "
        "WHERE universe_name = %s ORDER BY date",
        (UNIVERSE,),
    )
    dates = [r[0] for r in cur.fetchall()]
    print(f"Backfilling {len(dates)} dates "
          f"from {dates[0]} to {dates[-1]}")
    print()

    total_with_data = 0
    for idx, as_of in enumerate(dates, 1):
        # 1. Trailing-12-months sum of |dividends_paid| per ticker, PIT-safe.
        #    Sum the last ~4 quarters of dividends_paid (taking abs so cash
        #    outflows are treated as positive yields).
        cur.execute(
            """
            SELECT t.ticker, fs.divs_ttm
            FROM unnest(%s::text[]) AS t(ticker)
            LEFT JOIN LATERAL (
                SELECT SUM(ABS(dividends_paid)) AS divs_ttm
                FROM financial_statements
                WHERE ticker = t.ticker
                  AND data_available_date <= %s
                  AND consolidated_or_separate = 'consolidated'
                  AND dividends_paid IS NOT NULL
                  AND period_end > (
                      SELECT MAX(period_end) - INTERVAL '380 days'
                      FROM financial_statements
                      WHERE ticker = t.ticker
                        AND data_available_date <= %s
                        AND consolidated_or_separate = 'consolidated'
                  )
            ) fs ON true
            """,
            (universe_tickers, as_of, as_of),
        )
        divs_by_ticker = {t: d for t, d in cur.fetchall() if d is not None}

        # 2. PIT-safe market cap per ticker (latest marcap_historical row
        #    on or before as_of).
        cur.execute(
            """
            SELECT t.ticker, dp.market_cap
            FROM unnest(%s::text[]) AS t(ticker)
            LEFT JOIN LATERAL (
                SELECT market_cap FROM daily_prices
                WHERE ticker = t.ticker
                  AND date <= %s
                  AND market_cap IS NOT NULL AND market_cap > 0
                  AND source = 'marcap_historical'
                ORDER BY date DESC LIMIT 1
            ) dp ON true
            """,
            (universe_tickers, as_of),
        )
        mcap_by_ticker = {t: m for t, m in cur.fetchall() if m is not None}

        # 3. Compute ratio.
        values = {}
        for ticker in universe_tickers:
            divs = divs_by_ticker.get(ticker)
            mc = mcap_by_ticker.get(ticker)
            v = safe_div(divs, mc)
            if v is not None and v >= 0:
                values[ticker] = v

        ranked = percentile_rank(values)

        # 4. Build upsert rows: NULL row for tickers without data, real
        #    row for those with values. Matches calculate_factors.py's
        #    dense-snapshot style so coverage queries work correctly.
        upsert_rows = []
        for ticker in universe_tickers:
            v = values.get(ticker)
            pct = ranked.get(ticker)
            if v is None:
                upsert_rows.append((
                    ticker, FACTOR_ID, as_of,
                    None, None, "no_data", "global", None, "no_data", UNIVERSE,
                ))
            else:
                upsert_rows.append((
                    ticker, FACTOR_ID, as_of,
                    float(v),
                    float(pct) if pct is not None else None,
                    "calculated", "global", None, None, UNIVERSE,
                ))

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
        avg_yield = (sum(values.values()) / max(with_data, 1)) * 100
        print(f"  [{idx:>3}/{len(dates)}] {as_of}: "
              f"{with_data:>5} tickers w/ data "
              f"(avg yield {avg_yield:.2f}%)")

    cur.close()
    conn.close()
    print()
    print(f"Done! Inserted/updated dividend_yield for {len(dates)} dates.")
    print(f"Total ticker-date pairs with data: {total_with_data:,}")


if __name__ == "__main__":
    main()
