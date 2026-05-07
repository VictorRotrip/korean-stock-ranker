"""
Diagnose factor input data availability for the ranking system.

Shows exactly why factors are missing: market cap, shares, DART coverage, etc.

Usage:
    python diagnose_factor_inputs.py --as-of-date 2024-12-30 --limit 50
    python diagnose_factor_inputs.py --as-of-date 2024-12-30 --tickers 005930,000660
"""

import os
import sys
import argparse
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


def datetime_now_iso():
    return datetime.now().strftime("%Y-%m-%d")


def main():
    parser = argparse.ArgumentParser(description="Diagnose factor input data")
    parser.add_argument("--as-of-date", required=True, help="Date YYYY-MM-DD")
    parser.add_argument("--tickers", help="Comma-separated tickers")
    parser.add_argument("--universe", help="Use named universe from universe_memberships table")
    parser.add_argument("--limit", type=int, help="Max stocks")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Get tickers
    if args.tickers:
        tickers = args.tickers.split(",")
    elif args.universe:
        cur.execute("SELECT ticker FROM universe_memberships WHERE universe_name = %s ORDER BY ticker", (args.universe,))
        tickers = [r[0] for r in cur.fetchall()]
        if not tickers:
            print("ERROR: Universe '{}' not found or empty".format(args.universe))
            sys.exit(1)
        print("  Universe '{}': {} tickers".format(args.universe, len(tickers)))
    else:
        q = "SELECT ticker, name FROM stocks WHERE is_active = TRUE ORDER BY ticker"
        if args.limit:
            q += " LIMIT {0}".format(args.limit)
        cur.execute(q)
        rows = cur.fetchall()
        tickers = [r[0] for r in rows]
        ticker_names = {r[0]: r[1] for r in rows}

    as_of = args.as_of_date

    print()
    print("=" * 70)
    print("  Factor Input Diagnostics as of {0}".format(as_of))
    print("  Stocks: {0}".format(len(tickers)))
    print("=" * 70)

    # 1. Price data
    print()
    print("--- PRICE DATA (daily_prices) ---")
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM daily_prices
        WHERE ticker = ANY(%s) AND date <= %s
    """, (tickers, as_of))
    with_prices = cur.fetchone()[0]
    print("  Stocks with any price rows:  {0}/{1}".format(with_prices, len(tickers)))

    # Latest close
    cur.execute("""
        SELECT COUNT(DISTINCT dp.ticker) FROM (
            SELECT ticker, MAX(date) as max_date FROM daily_prices
            WHERE ticker = ANY(%s) AND date <= %s
            GROUP BY ticker
        ) dp
        INNER JOIN daily_prices d ON d.ticker = dp.ticker AND d.date = dp.max_date
        WHERE d.close IS NOT NULL AND d.close > 0
    """, (tickers, as_of))
    with_close = cur.fetchone()[0]
    print("  Stocks with latest close:    {0}/{1}".format(with_close, len(tickers)))

    # Market cap in daily_prices (any date)
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM daily_prices
        WHERE ticker = ANY(%s) AND date <= %s
        AND market_cap IS NOT NULL AND market_cap > 0
    """, (tickers, as_of))
    with_mcap = cur.fetchone()[0]
    print("  Stocks with market_cap:      {0}/{1}  (any date <= as-of)".format(with_mcap, len(tickers)))

    # Market cap on exact as-of date
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM daily_prices
        WHERE ticker = ANY(%s) AND date = %s
        AND market_cap IS NOT NULL AND market_cap > 0
    """, (tickers, as_of))
    with_mcap_exact = cur.fetchone()[0]
    print("  Stocks with market_cap:      {0}/{1}  (exact as-of date)".format(with_mcap_exact, len(tickers)))

    # Latest date that has any market_cap
    cur.execute("""
        SELECT MAX(date) FROM daily_prices
        WHERE ticker = ANY(%s) AND date <= %s
        AND market_cap IS NOT NULL AND market_cap > 0
    """, (tickers, as_of))
    latest_mcap_date = cur.fetchone()[0]
    print("  Latest market_cap date:      {0}".format(latest_mcap_date or "NONE"))

    # Shares on exact as-of date
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM daily_prices
        WHERE ticker = ANY(%s) AND date = %s
        AND shares_outstanding IS NOT NULL AND shares_outstanding > 0
    """, (tickers, as_of))
    with_shares_exact = cur.fetchone()[0]
    print("  Stocks with shares_out:      {0}/{1}  (exact as-of date)".format(with_shares_exact, len(tickers)))

    # Trading value on exact as-of date
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM daily_prices
        WHERE ticker = ANY(%s) AND date = %s
        AND trading_value IS NOT NULL AND trading_value > 0
    """, (tickers, as_of))
    with_tv_exact = cur.fetchone()[0]
    print("  Stocks with trading_value:   {0}/{1}  (exact as-of date)".format(with_tv_exact, len(tickers)))

    # Shares outstanding in daily_prices
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM daily_prices
        WHERE ticker = ANY(%s) AND date <= %s
        AND shares_outstanding IS NOT NULL AND shares_outstanding > 0
    """, (tickers, as_of))
    with_shares_price = cur.fetchone()[0]
    print("  Stocks with shares_out:      {0}/{1}".format(with_shares_price, len(tickers)))

    # Trading value
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM daily_prices
        WHERE ticker = ANY(%s) AND date <= %s
        AND trading_value IS NOT NULL AND trading_value > 0
    """, (tickers, as_of))
    with_tv = cur.fetchone()[0]
    print("  Stocks with trading_value:   {0}/{1}".format(with_tv, len(tickers)))

    # 2. DART financial data
    print()
    print("--- DART FINANCIALS (financial_statements) ---")
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM financial_statements
        WHERE ticker = ANY(%s) AND data_available_date <= %s
        AND statement_type = 'annual' AND consolidated_or_separate = 'consolidated'
    """, (tickers, as_of))
    with_dart = cur.fetchone()[0]
    print("  Stocks with annual CFS:      {0}/{1}".format(with_dart, len(tickers)))

    # Key fields
    for field in ["revenue", "net_income", "total_equity", "total_assets",
                  "operating_cash_flow", "free_cash_flow", "ebitda",
                  "total_debt", "cash", "shares_outstanding", "interest_expense"]:
        cur.execute("""
            SELECT COUNT(DISTINCT ticker) FROM financial_statements
            WHERE ticker = ANY(%s) AND data_available_date <= %s
            AND statement_type = 'annual' AND consolidated_or_separate = 'consolidated'
            AND {0} IS NOT NULL
        """.format(field), (tickers, as_of))
        count = cur.fetchone()[0]
        print("    {0}: {1}/{2}".format(field.ljust(22), count, len(tickers)))

    # 3. Normalized fundamentals
    print()
    print("--- NORMALIZED (fundamental_snapshots) ---")
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) FROM fundamental_snapshots
        WHERE ticker = ANY(%s) AND data_available_date <= %s
    """, (tickers, as_of))
    with_norm = cur.fetchone()[0]
    print("  Stocks with snapshots:       {0}/{1}".format(with_norm, len(tickers)))

    # 3a. Annual coverage by fiscal year (PIT-safe).
    print()
    print("  Annual coverage by fiscal year (PIT-safe, data_available <= as_of):")
    cur.execute("""
        SELECT fiscal_year, COUNT(DISTINCT ticker) FROM fundamental_snapshots
        WHERE ticker = ANY(%s)
          AND data_available_date <= %s
          AND report_code = '11011'
        GROUP BY fiscal_year ORDER BY fiscal_year DESC
    """, (tickers, as_of))
    for fy, cnt in cur.fetchall():
        print("    FY{0}:  {1}/{2}".format(fy, cnt, len(tickers)))

    # 3b. Quarterly coverage by fiscal year and report code.
    print()
    print("  Quarterly coverage by fiscal year and report code (PIT-safe):")
    cur.execute("""
        SELECT fiscal_year, report_code, COUNT(DISTINCT ticker)
          FROM fundamental_snapshots
         WHERE ticker = ANY(%s)
           AND data_available_date <= %s
           AND report_code IN ('11013','11012','11014')
         GROUP BY fiscal_year, report_code
         ORDER BY fiscal_year DESC, report_code
    """, (tickers, as_of))
    code_label = {"11013": "Q1", "11012": "H1", "11014": "9M"}
    for fy, rc, cnt in cur.fetchall():
        print("    FY{0} {1:<8}: {2}/{3}".format(
            fy, code_label.get(rc, rc), cnt, len(tickers)))

    # 3c. Latest available fundamental report per ticker.
    print()
    print("  Latest fundamental period per ticker (PIT-safe):")
    cur.execute("""
        SELECT
          MAX(period_end) AS latest_period,
          COUNT(DISTINCT ticker) AS n_at_latest
        FROM fundamental_snapshots
        WHERE ticker = ANY(%s)
          AND data_available_date <= %s
    """, (tickers, as_of))
    latest_period, _ = cur.fetchone()
    if latest_period:
        cur.execute("""
            WITH latest_per_ticker AS (
              SELECT ticker, MAX(period_end) AS latest_pe
                FROM fundamental_snapshots
               WHERE ticker = ANY(%s) AND data_available_date <= %s
               GROUP BY ticker
            )
            SELECT latest_pe, COUNT(*) FROM latest_per_ticker
            GROUP BY latest_pe ORDER BY latest_pe DESC
            LIMIT 8
        """, (tickers, as_of))
        for pe, n in cur.fetchall():
            print("    period_end={0}: {1} tickers".format(pe, n))
    else:
        print("    (no PIT-safe fundamental rows for this scope)")

    # 3d. Future-leak detection: would FY2025 annuals filed in 2026 be
    # excluded from a 2025-12-30 PIT ranking? The check is general: any
    # fiscal_year >= as_of_year that has rows where data_available_date >
    # as_of indicates rows that would correctly be filtered out by the PIT
    # rule. We surface this so the operator sees the engine is doing the
    # right thing (and not silently using future data).
    cur.execute("""
        SELECT fiscal_year, report_code, COUNT(DISTINCT ticker)
          FROM fundamental_snapshots
         WHERE ticker = ANY(%s)
           AND data_available_date > %s
         GROUP BY fiscal_year, report_code
         ORDER BY fiscal_year, report_code
    """, (tickers, as_of))
    leak_rows = cur.fetchall()
    print()
    print("  Future-data exclusion check (rows filtered by data_available_date > as_of):")
    if leak_rows:
        for fy, rc, cnt in leak_rows:
            print("    FY{0} {1:<8}: {2} tickers (would be EXCLUDED)".format(
                fy, code_label.get(rc, rc) if rc != "11011" else "Annual",
                cnt))
        print("    OK - the PIT filter on data_available_date is preventing "
              "future-data leak.")
    else:
        print("    None. No fundamental rows in scope have "
              "data_available_date > {0}.".format(as_of))

    # 3e. TTM derivability per ticker — does each ticker have either 4
    # consecutive cumulative quarters or an annual report we can fall back
    # to? Counts are not exact (we approximate by checking the presence of
    # current-year cumulative slots); the precise computation lives in
    # factor_calculators.fundamental_ttm.
    print()
    print("  TTM derivability (approximate, PIT-safe):")
    cur.execute("""
        WITH years AS (
          SELECT DISTINCT fiscal_year
            FROM fundamental_snapshots
           WHERE ticker = ANY(%s)
             AND data_available_date <= %s
        ),
        slot_counts AS (
          SELECT ticker, fiscal_year, COUNT(DISTINCT report_code) AS n_slots
            FROM fundamental_snapshots
           WHERE ticker = ANY(%s)
             AND data_available_date <= %s
             AND report_code IN ('11013','11012','11014','11011')
           GROUP BY ticker, fiscal_year
        )
        SELECT
          (SELECT COUNT(DISTINCT ticker) FROM slot_counts WHERE n_slots = 4) AS full_year,
          (SELECT COUNT(DISTINCT ticker) FROM slot_counts WHERE n_slots = 3) AS three_slots,
          (SELECT COUNT(DISTINCT ticker) FROM slot_counts WHERE n_slots <= 2) AS two_or_less
    """, (tickers, as_of, tickers, as_of))
    full4, three, two_or_less = cur.fetchone() or (0, 0, 0)
    print("    Tickers with all 4 cumulative slots in some FY:  {0}".format(full4))
    print("    Tickers with 3 slots in some FY:                  {0}".format(three))
    print("    Tickers with <=2 slots in some FY:                {0}".format(two_or_less))

    # 4. Value factor eligibility
    print()
    print("--- VALUE FACTOR ELIGIBILITY ---")
    print("  (Requires: market_cap from prices OR shares_outstanding x close)")

    # Can we compute market_cap?
    cur.execute("""
        SELECT COUNT(DISTINCT t.ticker) FROM unnest(%s::text[]) t(ticker)
        WHERE EXISTS (
            SELECT 1 FROM daily_prices dp
            WHERE dp.ticker = t.ticker AND dp.date <= %s
            AND dp.market_cap IS NOT NULL AND dp.market_cap > 0
        )
        OR (
            EXISTS (
                SELECT 1 FROM daily_prices dp
                WHERE dp.ticker = t.ticker AND dp.date <= %s
                AND dp.close IS NOT NULL AND dp.close > 0
            )
            AND EXISTS (
                SELECT 1 FROM financial_statements fs
                WHERE fs.ticker = t.ticker AND fs.data_available_date <= %s
                AND fs.shares_outstanding IS NOT NULL AND fs.shares_outstanding > 0
            )
        )
    """, (tickers, as_of, as_of, as_of))
    eligible = cur.fetchone()[0]
    print("  Eligible for value factors:  {0}/{1}".format(eligible, len(tickers)))

    # PE (needs net_income + market_cap)
    cur.execute("""
        SELECT COUNT(DISTINCT fs.ticker) FROM financial_statements fs
        WHERE fs.ticker = ANY(%s) AND fs.data_available_date <= %s
        AND fs.statement_type = 'annual' AND fs.consolidated_or_separate = 'consolidated'
        AND fs.net_income IS NOT NULL
    """, (tickers, as_of))
    with_ni = cur.fetchone()[0]
    print("  With net_income (for PE):    {0}/{1}".format(with_ni, len(tickers)))

    # 5. Sector / industry coverage
    print()
    print("--- SECTOR / INDUSTRY COVERAGE ---")
    cur.execute("""
        SELECT COUNT(*) as total,
               COUNT(sector) as with_sector,
               COUNT(industry) as with_industry,
               COUNT(CASE WHEN is_preferred THEN 1 END) as preferred,
               COUNT(CASE WHEN is_etf THEN 1 END) as etf,
               COUNT(CASE WHEN is_spac THEN 1 END) as spac,
               COUNT(CASE WHEN is_reit THEN 1 END) as reit,
               COUNT(CASE WHEN is_financial THEN 1 END) as financial,
               COUNT(CASE WHEN is_holding THEN 1 END) as holding
        FROM stocks WHERE ticker = ANY(%s)
    """, (tickers,))
    sc = cur.fetchone()
    print("  Sector filled:   {0}/{1}".format(sc[1], sc[0]))
    print("  Industry filled: {0}/{1}".format(sc[2], sc[0]))
    print("  Flags: preferred={0}, etf={1}, spac={2}, reit={3}, financial={4}, holding={5}".format(
        sc[3], sc[4], sc[5], sc[6], sc[7], sc[8]))

    if sc[1] > 0:
        cur.execute("""
            SELECT sector, COUNT(*) FROM stocks
            WHERE ticker = ANY(%s) AND sector IS NOT NULL
            GROUP BY sector ORDER BY COUNT(*) DESC
        """, (tickers,))
        for sector, cnt in cur.fetchall():
            print("    {0}: {1}".format(sector, cnt))

    # 6. Financial-sector stocks
    print()
    print("--- FINANCIAL SECTOR STOCKS ---")
    cur.execute("""
        SELECT ticker, name, sector, industry FROM stocks
        WHERE ticker = ANY(%s) AND (is_financial = TRUE OR sector ILIKE '%%금융%%' OR sector ILIKE '%%bank%%' OR sector ILIKE '%%보험%%' OR industry ILIKE '%%금융%%')
    """, (tickers,))
    fin_stocks = cur.fetchall()
    if fin_stocks:
        print("  Found {0} financial sector stocks:".format(len(fin_stocks)))
        for t, n, s, ind in fin_stocks:
            print("    {0} {1} (sector={2}, industry={3})".format(t, n[:20] if n else "?", s or "?", ind or "?"))
    else:
        print("  No financial sector stocks detected")

    # 7. Sample: show first 15 stocks with all key columns from daily_prices
    print()
    print("--- SAMPLE: daily_prices on as-of date ({0}) ---".format(as_of))
    cur.execute("""
        SELECT s.ticker, s.name,
            dp.date, dp.close, dp.volume, dp.trading_value, dp.market_cap, dp.shares_outstanding, dp.source
        FROM stocks s
        LEFT JOIN LATERAL (
            SELECT date, close, volume, trading_value, market_cap, shares_outstanding, source
            FROM daily_prices
            WHERE ticker = s.ticker AND date <= %s
            ORDER BY date DESC LIMIT 1
        ) dp ON TRUE
        WHERE s.ticker = ANY(%s)
        ORDER BY s.ticker
        LIMIT 15
    """, (as_of, tickers))
    sample_rows = cur.fetchall()
    fmt = "  {0:<8} {1:<16} {2:<12} {3:>10} {4:>12} {5:>14} {6:>16} {7:>14} {8:<7}"
    print(fmt.format("Ticker", "Name", "Date", "Close", "Volume", "TradingVal", "MarketCap", "Shares", "Source"))
    print("  " + "-" * 115)
    for t, n, d, c, vol, tv, mc, sh, src in sample_rows:
        print(fmt.format(
            t, (n or "?")[:16], str(d or "NONE"),
            "{0:,.0f}".format(c) if c else "NULL",
            "{0:,.0f}".format(vol) if vol else "NULL",
            "{0:,.0f}".format(tv) if tv else "NULL",
            "{0:,.0f}".format(mc) if mc else "NULL",
            "{0:,.0f}".format(sh) if sh else "NULL",
            str(src or "?")[:7]))

    # 7b. Universe point-in-time status.
    # We classify each universe member into ONE of four buckets so the
    # diagnosis is actionable. "Missing from marcap_historical" used to mean
    # both "the parquet doesn't have this ticker" AND "the parquet has it
    # but we never backfilled it into daily_prices" — those are very
    # different problems and the recommendation is different too.
    #
    # Buckets (mutually exclusive, in priority order):
    #   1. PIT-EXACT  : has source='marcap_historical' on the exact as-of date.
    #   2. PIT-PRIOR  : has source='marcap_historical' on/before as-of, but
    #                   the exact as-of date row is either missing or
    #                   non-marcap_historical. Likely just needs an as-of
    #                   backfill of ingest_marcap.py.
    #   3. NON-PIT    : has any daily_prices row on/before as-of, but none
    #                   are source='marcap_historical'. The marcap was set
    #                   by ingest_prices.py / fdr_listing_snapshot — needs
    #                   a historical-range backfill of ingest_marcap.py.
    #   4. ABSENT     : universe member has no daily_prices row at all on
    #                   or before as-of. Either ingest_prices.py never
    #                   covered this date, or the ticker truly didn't trade
    #                   yet. Recommend re-running ingest_prices.py first
    #                   and only suggest rebuilding the universe if the
    #                   ticker is still absent.
    print()
    print("--- UNIVERSE POINT-IN-TIME STATUS ---")
    if args.universe:
        print("  Universe name:    {0}".format(args.universe))
    print("  Universe size:    {0}".format(len(tickers)))
    print("  As-of date:       {0}".format(as_of))

    # Single query computes all four counts in one pass to keep this section
    # cheap on large universes.
    cur.execute(
        """
        WITH per_ticker AS (
            SELECT
                s.ticker,
                MAX(dp.date) FILTER (WHERE dp.source = 'marcap_historical'
                                       AND dp.date <= %(as_of)s
                                       AND dp.market_cap IS NOT NULL) AS pit_max_date,
                MAX(dp.date) FILTER (WHERE dp.date <= %(as_of)s
                                       AND dp.market_cap IS NOT NULL) AS any_max_date,
                MAX(dp.date) FILTER (WHERE dp.date <= %(as_of)s) AS row_max_date
            FROM stocks s
            LEFT JOIN daily_prices dp ON dp.ticker = s.ticker
            WHERE s.ticker = ANY(%(tickers)s)
            GROUP BY s.ticker
        )
        SELECT
            COUNT(*) FILTER (WHERE pit_max_date = %(as_of)s::date)                             AS pit_exact,
            COUNT(*) FILTER (WHERE pit_max_date IS NOT NULL AND pit_max_date < %(as_of)s::date) AS pit_prior,
            COUNT(*) FILTER (WHERE pit_max_date IS NULL AND any_max_date IS NOT NULL)           AS non_pit,
            COUNT(*) FILTER (WHERE pit_max_date IS NULL AND any_max_date IS NULL
                               AND row_max_date IS NOT NULL)                                    AS no_marcap_only,
            COUNT(*) FILTER (WHERE row_max_date IS NULL)                                        AS absent_in_db
        FROM per_ticker
        """,
        {"as_of": as_of, "tickers": tickers},
    )
    pit_exact, pit_prior, non_pit, no_marcap_only, absent_in_db = cur.fetchone()

    print("  PIT-exact (marcap_historical on {0}):           {1}/{2}".format(
        as_of, pit_exact, len(tickers)))
    print("  PIT-prior (marcap_historical earlier than as-of): {0}/{1}".format(
        pit_prior, len(tickers)))
    print("  NON-PIT (in daily_prices but source != marcap_historical): {0}/{1}".format(
        non_pit, len(tickers)))
    print("  IN DB but no market_cap row on/before as-of:    {0}/{1}".format(
        no_marcap_only, len(tickers)))
    print("  ABSENT from daily_prices entirely on/before as-of: {0}/{1}".format(
        absent_in_db, len(tickers)))

    needs_exact_backfill = pit_prior + non_pit + no_marcap_only
    needs_range_backfill = non_pit + no_marcap_only
    needs_universe_rebuild = absent_in_db

    # Concrete, actionable suggestions per bucket. Only suggest rebuilding
    # the universe if a member has NO daily_prices row at all on/before the
    # as-of date (i.e. the ticker truly looks future-listed).
    if pit_exact == len(tickers):
        print("  STATUS: PIT-safe. Every universe member has marcap_historical "
              "on the exact as-of date {0}.".format(as_of))
    else:
        print()
        print("  RECOMMENDATIONS:")
        if pit_prior > 0 and pit_prior == needs_exact_backfill - non_pit - no_marcap_only:
            # Only pit_prior outstanding -> just need exact-date as-of refresh.
            pass
        if needs_exact_backfill > 0:
            print("    * {0} ticker(s) have no marcap_historical row on the exact "
                  "as-of date.".format(needs_exact_backfill))
            print("      Fix: backfill the exact date with:")
            print("        python ingest_marcap.py --source historical "
                  "--as-of-date {0} \\".format(as_of))
            print("          --universe {0}".format(args.universe or "<universe>"))
        if needs_range_backfill > 0:
            print("    * {0} ticker(s) have daily_prices rows but the marcap "
                  "source is non-PIT (fdr/snapshot/null) — needs a historical-"
                  "range backfill of ingest_marcap.py:".format(needs_range_backfill))
            print("        python ingest_marcap.py --source historical \\")
            print("          --start-date 2014-01-01 --end-date {0} \\".format(as_of))
            print("          --universe {0} \\".format(args.universe or "<universe>"))
            print("          --batch-size 25 --resume")
        if needs_universe_rebuild > 0:
            print("    * {0} ticker(s) are NOT in daily_prices on/before {1} at "
                  "all.".format(needs_universe_rebuild, as_of))
            print("      That can mean either:")
            print("        (a) ingest_prices.py was not run for this date range, OR")
            print("        (b) the ticker genuinely did not trade yet on {0}.".format(as_of))
            print("      First, re-run ingest_prices.py for the universe and "
                  "date range. If a ticker is still absent afterwards, only "
                  "then consider rebuilding the universe with:")
            print("        python ingest_universe.py --source historical "
                  "--as-of-date {0} --require-pit \\".format(as_of))
            print("          --limit-per-market 100 --sample-strategy largest \\")
            print("          --exclude-preferred --exclude-etf --exclude-spac --exclude-reit \\")
            print("          --universe-name <name>_pit_{0}".format(as_of.replace("-", "")))

    # 8. Factor snapshot coverage (scoped by universe + ticker filter so
    #    counts can never exceed the size of the chosen universe).
    print()
    if args.universe:
        scope_universe = args.universe
    elif args.tickers:
        scope_universe = "__tickers_filter__"
    else:
        scope_universe = "__all_active__"

    print("--- FACTOR SNAPSHOTS (universe='{0}', as-of {1}) ---".format(
        scope_universe, as_of))
    cur.execute("""
        SELECT factor_id, COUNT(*) as cnt
        FROM factor_snapshots
        WHERE date = %s
          AND universe_name = %s
          AND ticker = ANY(%s)
          AND raw_value IS NOT NULL
        GROUP BY factor_id ORDER BY factor_id
    """, (as_of, scope_universe, tickers))
    factor_rows = cur.fetchall()
    if factor_rows:
        for fid, cnt in factor_rows:
            # Coverage shown as N/total to make it obvious this is bounded
            # by the universe size.
            print("  {0}: {1}/{2}".format(
                fid.ljust(25), cnt, len(tickers)))
    else:
        print("  No factor snapshots found for date={0} universe={1}".format(
            as_of, scope_universe))
        print("  Run: python calculate_factors.py --universe {0} --as-of-date {1}".format(
            scope_universe, as_of))

    # 8a. Source-method breakdown across this universe's factor snapshots.
    # The source column on factor_snapshots is the per-(ticker,factor)
    # provenance label written by calculate_factors.py — values include
    # 'ttm_quarterly', 'annual_fallback', 'annual_only', 'non_pit_market_cap',
    # 'no_price_data', 'calculated', etc.
    print()
    print("--- FACTOR SOURCE-METHOD BREAKDOWN (universe='{0}', as-of {1}) ---"
          .format(scope_universe, as_of))
    cur.execute("""
        SELECT source, COUNT(*) AS n
          FROM factor_snapshots
         WHERE date = %s
           AND universe_name = %s
           AND ticker = ANY(%s)
         GROUP BY source
         ORDER BY n DESC
    """, (as_of, scope_universe, tickers))
    method_rows = cur.fetchall()
    if method_rows:
        for src, n in method_rows:
            print("    {0:<35} {1} rows".format(src or "(null)", n))
    else:
        print("    (no factor_snapshots rows for this scope)")

    # 8b. TTM vs annual-fallback per ticker — counts how many tickers had
    # at least one factor scored with each source method on this universe
    # / date. A healthy result is most tickers carrying 'ttm_quarterly'.
    print()
    print("  Tickers (distinct) by best-available fundamental source:")
    cur.execute("""
        SELECT source, COUNT(DISTINCT ticker)
          FROM factor_snapshots
         WHERE date = %s
           AND universe_name = %s
           AND ticker = ANY(%s)
           AND source IN ('ttm_quarterly','latest_annual','annual_fallback',
                          'annual_only','insufficient_quarterly_history',
                          'unavailable')
         GROUP BY source
         ORDER BY source
    """, (as_of, scope_universe, tickers))
    for src, n in cur.fetchall():
        print("    {0:<35} {1}/{2} tickers".format(
            src, n, len(tickers)))

    # Also show how many other universes have data on this date (for
    # awareness that the system is multi-universe).
    cur.execute("""
        SELECT universe_name, COUNT(DISTINCT ticker) AS stocks
        FROM factor_snapshots
        WHERE date = %s
        GROUP BY universe_name
        ORDER BY universe_name
    """, (as_of,))
    other_rows = cur.fetchall()
    if len(other_rows) > 1:
        print()
        print("  Other universes with factor data on this date:")
        for un, stocks in other_rows:
            marker = " <- this run" if un == scope_universe else ""
            print("    {0}: {1} stocks{2}".format(un, stocks, marker))

    # 9. Market cap source — point-in-time analysis
    print()
    print("--- MARKET CAP SOURCE (point-in-time analysis) ---")

    # Counts of each source label among rows that have market_cap, scoped to
    # the universe and the as-of date.
    cur.execute("""
        SELECT source, COUNT(DISTINCT ticker) AS stocks
        FROM daily_prices
        WHERE ticker = ANY(%s)
          AND date = %s
          AND market_cap IS NOT NULL AND market_cap > 0
        GROUP BY source
        ORDER BY source
    """, (tickers, as_of))
    src_rows_exact = cur.fetchall()

    # Same but for the latest market_cap row on or before as_of, per ticker
    cur.execute("""
        WITH latest AS (
            SELECT ticker, MAX(date) AS d
            FROM daily_prices
            WHERE ticker = ANY(%s)
              AND date <= %s
              AND market_cap IS NOT NULL AND market_cap > 0
            GROUP BY ticker
        )
        SELECT dp.source, COUNT(*) AS stocks
        FROM latest l
        JOIN daily_prices dp
          ON dp.ticker = l.ticker AND dp.date = l.d
        GROUP BY dp.source
        ORDER BY dp.source
    """, (tickers, as_of))
    src_rows_latest = cur.fetchall()

    print("  As-of-date rows ({0}):".format(as_of))
    if src_rows_exact:
        for src, n in src_rows_exact:
            print("    {0}: {1} stocks".format(src or "(null)", n))
    else:
        print("    (no rows on exact as-of date)")

    print("  Effective source per ticker (latest row <= as-of):")
    if src_rows_latest:
        for src, n in src_rows_latest:
            print("    {0}: {1} stocks".format(src or "(null)", n))
    else:
        print("    (no marcap data found)")

    # Classification: which sources are PIT-correct?
    PIT_SAFE = {"marcap_historical"}
    NOT_PIT = {"fdr_listing_snapshot", "fdr+marcap", "fdr+marcap+marcap"}

    pit_count = sum(n for src, n in src_rows_latest if src in PIT_SAFE)
    snap_count = sum(n for src, n in src_rows_latest
                     if src in NOT_PIT or (src and "snapshot" in src.lower()))
    other_count = sum(n for src, n in src_rows_latest
                      if src not in PIT_SAFE
                      and src not in NOT_PIT
                      and not (src and "snapshot" in src.lower()))

    print()
    print("  Point-in-time safety:")
    print("    marcap_historical (PIT-safe):    {0}/{1} stocks".format(
        pit_count, len(tickers)))
    print("    snapshot (NOT PIT):              {0}/{1} stocks".format(
        snap_count, len(tickers)))
    if other_count:
        print("    other / unknown source:          {0}/{1} stocks".format(
            other_count, len(tickers)))

    # Decide whether the as-of-date market cap is point-in-time safe overall
    today_iso = datetime_now_iso()
    is_historical_date = as_of < today_iso

    if pit_count == len(tickers):
        print()
        print("  STATUS: PIT-safe. All {0} stocks have point-in-time market_cap "
              "for {1}.".format(len(tickers), as_of))
    elif pit_count > 0 and snap_count > 0:
        print()
        print("  STATUS: MIXED. Some stocks are PIT-safe, some are snapshot-only.")
        if is_historical_date:
            print("    WARNING: as-of-date {0} is historical but {1} stocks "
                  "use snapshot.".format(as_of, snap_count))
            print("    Run: python ingest_marcap.py --source historical "
                  "--as-of-date {0} --universe <name>".format(as_of))
    elif snap_count > 0 and pit_count == 0:
        print()
        print("  STATUS: SNAPSHOT-ONLY. Market cap is from current FDR listing,")
        print("    NOT point-in-time. OK for current-ranking validation.")
        if is_historical_date:
            print("    WARNING: as-of-date {0} is historical. Backtests at this "
                  "date will be biased.".format(as_of))
            print("    Run: python ingest_marcap.py --source historical "
                  "--as-of-date {0} --universe <name>".format(as_of))

    # ---- 9b. Non-PIT ticker detail (the stocks that will be excluded from
    # historical rankings under --require-pit-market-cap) ----
    cur.execute("""
        WITH latest AS (
            SELECT ticker, MAX(date) AS d
            FROM daily_prices
            WHERE ticker = ANY(%s)
              AND date <= %s
              AND market_cap IS NOT NULL AND market_cap > 0
            GROUP BY ticker
        )
        SELECT s.ticker, s.name, s.market, dp.source,
               dp.close, dp.market_cap
        FROM latest l
        JOIN daily_prices dp ON dp.ticker = l.ticker AND dp.date = l.d
        JOIN stocks s ON s.ticker = dp.ticker
        WHERE dp.source IS DISTINCT FROM 'marcap_historical'
        ORDER BY s.ticker
    """, (tickers, as_of))
    non_pit_rows = cur.fetchall()

    if non_pit_rows:
        print()
        print("--- NON-PIT TICKERS (snapshot-only, would be excluded from historical ranking) ---")
        print("  {0:<8} {1:<22} {2:<8} {3:<22} {4:>14} {5:>16}  Reason".format(
            "Ticker", "Name", "Market", "Source", "Close", "MarketCap"))
        print("  " + "-" * 110)
        for t, nm, mkt, src, cl, mc in non_pit_rows:
            print("  {0:<8} {1:<22} {2:<8} {3:<22} {4:>14} {5:>16}  missing_from_marcap_historical_on_asof".format(
                t,
                (nm or "?")[:22],
                mkt or "?",
                (src or "?")[:22],
                "{0:,.0f}".format(cl) if cl else "NULL",
                "{0:,.0f}".format(mc) if mc else "NULL",
            ))
        print("  Tip: --require-pit-market-cap (default for historical dates) "
              "will exclude these {0} stocks from the main ranking.".format(
                  len(non_pit_rows)))

    print()
    print("=" * 70)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
