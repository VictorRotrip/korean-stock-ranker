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

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


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

    # 9. Market cap source warning
    print()
    print("--- MARKET CAP SOURCE ---")
    cur.execute("""
        SELECT DISTINCT source FROM daily_prices
        WHERE ticker = ANY(%s) AND date <= %s
        AND market_cap IS NOT NULL AND market_cap > 0
    """, (tickers, as_of))
    sources = [r[0] for r in cur.fetchall()]
    for src in sources:
        print("  Source: {0}".format(src))
    if any("marcap" in (s or "").lower() or "listing" in (s or "").lower()
           or "snapshot" in (s or "").lower() for s in sources):
        print("  WARNING: Market cap may come from a current snapshot")
        print("  (fdr.StockListing), not point-in-time historical data.")
        print("  OK for current ranking validation, not for backtests.")

    print()
    print("=" * 70)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
