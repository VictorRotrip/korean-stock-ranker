"""
Calculate factor values from ingested data using the P123-inspired factor registry.

Reads from daily_prices, financial_statements, and short_selling tables.
Computes raw factor values AND percentile ranks within the universe, then
writes both to factor_snapshots.

SCORING METHOD: percentile_rank (NOT z-score)
    - Each factor is ranked 0-100 within the universe scope.
    - Direction is honored via factor_definitions.rank_direction
      ("higher" = higher raw value gets higher rank;
       "lower"  = lower raw value gets higher rank, e.g. for P/E or volatility).
    - Tied raw values get the average percentile rank.
    - Missing raw values stay missing (None) and are excluded from ranking.
      They are NOT silently coerced to 0 or 50 here; missing-data policy
      is applied later in run_ranking_snapshot.py.

The factor_snapshots table stores raw_value separately from percentile_rank,
so downstream consumers can re-rank with different scopes (sector / industry)
without losing the underlying signal.

Usage:
    python calculate_factors.py --tickers 005930,000660,035420,051910,005380 --as-of-date 2024-12-31
    python calculate_factors.py --as-of-date 2024-12-31              # all active stocks
    python calculate_factors.py --as-of-date 2024-12-31 --limit 50   # first 50 tickers
    python calculate_factors.py --universe test_200_large --as-of-date 2024-12-30
"""

import os
import sys
import argparse
from datetime import datetime
from collections import defaultdict

import psycopg2
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

from factor_definitions import FACTORS, get_implemented_factors
from factor_calculators import technical, fundamental, industry, sentiment

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "calculate_factors"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_start(conn, params=None):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ingestion_log (script_name, parameters) VALUES (%s, %s) RETURNING id",
        (SCRIPT_NAME, Json(params)),
    )
    log_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    return log_id


def log_finish(conn, log_id, status, rows_processed=0, rows_inserted=0,
               rows_updated=0, rows_skipped=0, error_message=None):
    cur = conn.cursor()
    cur.execute(
        """UPDATE ingestion_log
           SET finished_at = NOW(), status = %s,
               rows_processed = %s, rows_inserted = %s,
               rows_updated = %s, rows_skipped = %s, error_message = %s
           WHERE id = %s""",
        (status, rows_processed, rows_inserted, rows_updated,
         rows_skipped, error_message, log_id),
    )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def get_active_tickers(conn, tickers_filter=None, limit=None):
    cur = conn.cursor()
    if tickers_filter:
        placeholders = ",".join(["%s"] * len(tickers_filter))
        cur.execute(f"SELECT ticker FROM stocks WHERE ticker IN ({placeholders}) AND is_active = TRUE",
                    tuple(tickers_filter))
    else:
        q = "SELECT ticker FROM stocks WHERE is_active = TRUE ORDER BY ticker"
        if limit:
            q += " LIMIT {0}".format(int(limit))
        cur.execute(q)
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    return tickers


def get_stock_metadata(conn, ticker):
    """Get sector and industry for scope-aware ranking."""
    cur = conn.cursor()
    cur.execute("""
        SELECT sector, industry FROM stocks WHERE ticker = %s
    """, (ticker,))
    row = cur.fetchone()
    cur.close()
    if row:
        return {"sector": row[0], "industry": row[1]}
    return {"sector": None, "industry": None}


def get_price_history(conn, ticker, as_of, days=260):
    """Get up to `days` trading days of price history ending at as_of.

    Returns list of tuples: (date, open, high, low, close, volume, trading_value, market_cap, shares_outstanding)
    Sorted oldest-first.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT date, open, high, low, close, volume, trading_value, market_cap, shares_outstanding
        FROM daily_prices
        WHERE ticker = %s AND date <= %s
        ORDER BY date DESC LIMIT %s
    """, (ticker, as_of, days))
    rows = cur.fetchall()
    cur.close()
    return list(reversed(rows))  # oldest first


def get_latest_financials(conn, ticker, as_of):
    """Point-in-time safe: only returns data where data_available_date <= as_of.

    Returns dict with financial statement fields.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT revenue, cost_of_revenue, gross_profit, operating_income, net_income,
               eps, total_assets, total_liabilities, total_equity,
               current_assets, current_liabilities, cash, total_debt,
               operating_cash_flow, capital_expenditure, free_cash_flow,
               dividends_paid, ebitda, interest_expense, depreciation,
               shares_outstanding, period_end
        FROM financial_statements
        WHERE ticker = %s AND data_available_date <= %s
              AND statement_type = 'annual' AND consolidated_or_separate = 'consolidated'
        ORDER BY period_end DESC LIMIT 1
    """, (ticker, as_of))
    row = cur.fetchone()
    cur.close()
    if row:
        cols = ["revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income",
                "eps", "total_assets", "total_liabilities", "total_equity",
                "current_assets", "current_liabilities", "cash", "total_debt",
                "operating_cash_flow", "capital_expenditure", "free_cash_flow",
                "dividends_paid", "ebitda", "interest_expense", "depreciation",
                "shares_outstanding", "period_end"]
        return dict(zip(cols, row))
    return None


def get_prior_financials(conn, ticker, as_of, latest_period_end):
    """Get previous year financial statement (point-in-time safe)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT revenue, cost_of_revenue, gross_profit, operating_income, net_income,
               eps, total_assets, total_liabilities, total_equity,
               current_assets, current_liabilities, cash, total_debt,
               operating_cash_flow, capital_expenditure, free_cash_flow,
               dividends_paid, ebitda, interest_expense, depreciation,
               shares_outstanding, period_end
        FROM financial_statements
        WHERE ticker = %s AND data_available_date <= %s
              AND statement_type = 'annual' AND consolidated_or_separate = 'consolidated'
              AND period_end < %s
        ORDER BY period_end DESC LIMIT 1
    """, (ticker, as_of, latest_period_end))
    row = cur.fetchone()
    cur.close()
    if row:
        cols = ["revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income",
                "eps", "total_assets", "total_liabilities", "total_equity",
                "current_assets", "current_liabilities", "cash", "total_debt",
                "operating_cash_flow", "capital_expenditure", "free_cash_flow",
                "dividends_paid", "ebitda", "interest_expense", "depreciation",
                "shares_outstanding", "period_end"]
        return dict(zip(cols, row))
    return None


def get_short_selling(conn, ticker, as_of):
    """Get latest short selling data."""
    cur = conn.cursor()
    cur.execute("""
        SELECT short_volume, short_value, short_balance, short_balance_value, short_ratio
        FROM short_selling WHERE ticker = %s AND date <= %s
        ORDER BY date DESC LIMIT 1
    """, (ticker, as_of))
    row = cur.fetchone()
    cur.close()
    if row:
        return {"short_volume": row[0], "short_value": row[1], "short_balance": row[2],
                "short_balance_value": row[3], "short_ratio": row[4]}
    return None


# ---------------------------------------------------------------------------
# Factor computation orchestration
# ---------------------------------------------------------------------------

def compute_all_factors(conn, ticker, as_of):
    """Compute all implemented factors for a single stock.

    Returns:
        dict: {factorId: rawValue or None, ...}
        dict: {factorId: missingReason or None, ...}
    """
    prices = get_price_history(conn, ticker, as_of)
    fin = get_latest_financials(conn, ticker, as_of)
    prior = None
    if fin:
        prior = get_prior_financials(conn, ticker, as_of, fin["period_end"])
    short = get_short_selling(conn, ticker, as_of)

    # Determine latest market cap and shares outstanding
    market_cap = None
    shares_outstanding = None
    if prices:
        for p in reversed(prices):
            if p[7] is not None and p[7] > 0:  # market_cap
                market_cap = p[7]
            if p[8] is not None and p[8] > 0:  # shares_outstanding
                shares_outstanding = p[8]
            if market_cap and shares_outstanding:
                break

    # Fallback: compute market_cap from shares * close if not available from prices
    if market_cap is None and prices:
        latest_close = None
        for p in reversed(prices):
            if p[4] is not None and p[4] > 0:  # close
                latest_close = p[4]
                break
        if latest_close:
            # Try shares from financial statements first
            if fin and fin.get("shares_outstanding") and fin["shares_outstanding"] > 0:
                shares_outstanding = fin["shares_outstanding"]
                market_cap = latest_close * shares_outstanding
            # Try shares from daily_prices
            elif shares_outstanding and shares_outstanding > 0:
                market_cap = latest_close * shares_outstanding

    factors = {}
    missing_reasons = {}

    implemented = get_implemented_factors()

    for factor_id, factor_meta in implemented.items():
        compute_fn_name = factor_meta.get("compute_function")
        if not compute_fn_name:
            factors[factor_id] = None
            missing_reasons[factor_id] = "unavailable"
            continue

        try:
            raw_value = _compute_single_factor(
                factor_id, factor_meta, compute_fn_name,
                prices, fin, prior, market_cap, shares_outstanding, short
            )

            if raw_value is None:
                missing_reasons[factor_id] = _get_missing_reason(
                    factor_id, factor_meta, prices, fin, prior, market_cap, shares_outstanding
                )
            factors[factor_id] = raw_value

        except Exception as e:
            factors[factor_id] = None
            missing_reasons[factor_id] = "computation_error: {0}".format(str(e))

    return factors, missing_reasons


def _compute_single_factor(factor_id, factor_meta, compute_fn_name,
                           prices, fin, prior, market_cap, shares_outstanding, short):
    """Compute a single factor by dispatching to the appropriate calculator."""
    data_source = factor_meta.get("data_source")
    params = factor_meta.get("params", {})

    if data_source == "price":
        # Technical factors
        if not prices or len(prices) < factor_meta.get("lookback_days", 0):
            return None

        fn = getattr(technical, compute_fn_name, None)
        if not fn:
            return None

        if params:
            return fn(prices, **params)
        else:
            return fn(prices)

    elif data_source == "dart":
        # Fundamental factors
        if not fin:
            return None

        fn = getattr(fundamental, compute_fn_name, None)
        if not fn:
            return None

        return fn(fin, prior, market_cap, shares_outstanding)

    elif data_source == "short_interest":
        # Short interest factors
        fn = getattr(sentiment, compute_fn_name, None)
        if not fn:
            return None

        return fn(short, shares_outstanding)

    elif data_source == "estimates":
        # Sentiment factors (unavailable)
        fn = getattr(sentiment, compute_fn_name, None)
        if not fn:
            return None
        return fn()

    elif data_source == "derived":
        # Industry/derived factors (computed in post-processing)
        return None

    return None


def _get_missing_reason(factor_id, factor_meta, prices, fin, prior, market_cap, shares_outstanding):
    """Determine why a factor is missing."""
    data_source = factor_meta.get("data_source")
    lookback = factor_meta.get("lookback_days", 0)

    if data_source == "price":
        if not prices:
            return "no_price_data"
        if len(prices) < lookback:
            return "insufficient_history"
        return "no_data"

    elif data_source == "dart":
        if not fin:
            return "no_financial_data"
        return "no_data"

    elif data_source == "short_interest":
        return "no_short_data"

    elif data_source == "estimates":
        return "data_unavailable"

    elif data_source == "derived":
        return "requires_postprocessing"

    return "unknown"


# ---------------------------------------------------------------------------
# Percentile ranking
# ---------------------------------------------------------------------------

def percentile_rank(values_dict, factor_meta):
    """Rank a factor using provided direction (higher or lower).

    Args:
        values_dict: {ticker: value} with value or None
        factor_meta: factor metadata with rank_direction

    Returns:
        {ticker: percentile_rank (0-100)}
    """
    items = [(t, v) for t, v in values_dict.items() if v is not None]
    if len(items) < 2:
        # If only 0 or 1 values, return 50% for the one that exists
        return {t: 50.0 for t, _ in items}

    items.sort(key=lambda x: x[1])
    n = len(items)
    ranks = {}

    i = 0
    while i < n:
        j = i
        while j < n and items[j][1] == items[i][1]:
            j += 1
        avg_rank = (i + j - 1) / 2.0
        pct = (avg_rank / (n - 1)) * 100.0 if n > 1 else 50.0

        # Invert if lower is better
        if factor_meta.get("rank_direction") == "lower":
            pct = 100.0 - pct

        for k in range(i, j):
            ranks[items[k][0]] = round(pct, 2)

        i = j

    return ranks


# ---------------------------------------------------------------------------
# Post-processing: industry momentum and other derived factors
# ---------------------------------------------------------------------------

def compute_industry_factors(all_raw_factors, ticker_industry_map, tickers):
    """Compute industry momentum factors after individual factors are done.

    Updates all_raw_factors in place with industry_momentum_26w and industry_momentum_52w.
    """
    # Precompute individual returns for all tickers
    price_returns_126d = {}
    price_returns_252d = {}

    for ticker in tickers:
        ret_126 = all_raw_factors.get(ticker, {}).get("momentum_6m")
        ret_252 = all_raw_factors.get(ticker, {}).get("price_change_180d")  # closest to 252d
        if ret_126 is not None:
            price_returns_126d[ticker] = ret_126
        if ret_252 is not None:
            price_returns_252d[ticker] = ret_252

    # Compute industry momentum for each ticker
    for ticker in tickers:
        if "industry_momentum_26w" not in all_raw_factors.get(ticker, {}):
            result = industry.calc_industry_momentum(
                ticker, ticker_industry_map, price_returns_126d
            )
            all_raw_factors.setdefault(ticker, {})["industry_momentum_26w"] = result

        if "industry_momentum_52w" not in all_raw_factors.get(ticker, {}):
            result = industry.calc_industry_momentum(
                ticker, ticker_industry_map, price_returns_252d
            )
            all_raw_factors.setdefault(ticker, {})["industry_momentum_52w"] = result


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_factor_snapshots(conn, rows):
    """Write factor snapshot rows, scoped by universe_name.

    Args:
        rows: list of (universe_name, ticker, factor_id, date, raw_value,
                       percentile_rank, source)

    Universe-awareness: percentile ranks depend on the universe in which the
    stock was ranked, so the unique key includes universe_name. Two universes
    can both have a factor_snapshot row for the same (ticker, factor_id, date)
    with different percentile ranks -- they live side by side.

    Requires migration 004 (adds universe_name column and unique index).
    The OLD primary key on (ticker, factor_id, date) must be dropped manually
    in Supabase SQL Editor; see scripts/sql/004_universe_aware_factor_snapshots.sql
    for the exact statement.
    """
    if not rows:
        return 0
    cur = conn.cursor()
    query = """
    INSERT INTO factor_snapshots
        (universe_name, ticker, factor_id, date, raw_value, percentile_rank, source)
    VALUES %s
    ON CONFLICT (universe_name, ticker, factor_id, date) DO UPDATE SET
        raw_value = EXCLUDED.raw_value,
        percentile_rank = EXCLUDED.percentile_rank,
        source = EXCLUDED.source
    """
    execute_values(cur, query, rows)
    conn.commit()
    cur.close()
    return len(rows)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_coverage_summary(all_raw_factors, all_missing_reasons, all_factor_ids, tickers):
    """Print summary of factor coverage and computation."""
    print("")
    print("=== FACTOR COVERAGE SUMMARY ===")
    print("")

    category_coverage = defaultdict(lambda: {"computed": 0, "total": 0})
    factor_coverage = {}

    for factor_id in all_factor_ids:
        factor_meta = FACTORS.get(factor_id, {})
        category = factor_meta.get("category", "unknown")

        computed = 0
        missing = 0

        for ticker in tickers:
            if all_raw_factors.get(ticker, {}).get(factor_id) is not None:
                computed += 1
            else:
                missing += 1

        factor_coverage[factor_id] = {
            "computed": computed,
            "missing": missing,
            "coverage_pct": round(100.0 * computed / len(tickers), 1) if tickers else 0,
        }
        category_coverage[category]["computed"] += computed
        category_coverage[category]["total"] += len(tickers)

    # Per-factor summary
    print("Per-Factor Coverage:")
    for factor_id in sorted(all_factor_ids):
        cov = factor_coverage[factor_id]
        factor_meta = FACTORS.get(factor_id, {})
        print("  {0}: {1}/{2} ({3}%)".format(
            factor_id,
            cov["computed"],
            len(tickers),
            cov["coverage_pct"]
        ))

    # Per-category summary
    print("")
    print("Per-Category Coverage:")
    for category in sorted(category_coverage.keys()):
        cov = category_coverage[category]
        pct = round(100.0 * cov["computed"] / cov["total"], 1) if cov["total"] > 0 else 0
        print("  {0}: {1}/{2} ({3}%)".format(
            category,
            cov["computed"],
            cov["total"],
            pct
        ))

    # Overall
    total_computed = sum(cov["computed"] for cov in factor_coverage.values())
    total_possible = len(all_factor_ids) * len(tickers)
    print("")
    print("Overall: {0}/{1} factor-stock combinations computed ({2}%)".format(
        total_computed,
        total_possible,
        round(100.0 * total_computed / total_possible, 1) if total_possible > 0 else 0
    ))
    print("")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calculate factor values from DB data")
    parser.add_argument("--as-of-date", required=True, help="Ranking date YYYY-MM-DD")
    parser.add_argument("--tickers", help="Comma-separated tickers")
    parser.add_argument("--universe", help="Use named universe from universe_memberships table")
    parser.add_argument("--limit", type=int, help="Max tickers to process")
    parser.add_argument("--exclude-financials", action="store_true",
                        help="Exclude financial-sector stocks (banks, insurance, securities)")
    args = parser.parse_args()

    as_of = args.as_of_date
    tickers_filter = None

    # Universe scoping for factor_snapshots writes.
    # If --universe is set, percentile ranks are tagged with that universe.
    # Otherwise we use a synthetic name so legacy CLI flows still write
    # somewhere, but they won't pollute named-universe rankings.
    if args.universe:
        universe_name_for_scope = args.universe
    elif args.tickers:
        universe_name_for_scope = "__tickers_filter__"
    else:
        universe_name_for_scope = "__all_active__"

    if args.tickers:
        tickers_filter = args.tickers.split(",")
    elif args.universe:
        cur = psycopg2.connect(DATABASE_URL).cursor()
        cur.execute("SELECT ticker FROM universe_memberships WHERE universe_name = %s ORDER BY ticker", (args.universe,))
        tickers_filter = [r[0] for r in cur.fetchall()]
        cur.close()
        if not tickers_filter:
            print("ERROR: Universe '{}' not found or empty".format(args.universe))
            sys.exit(1)
        print("  Universe '{}': {} tickers".format(args.universe, len(tickers_filter)))

    conn = psycopg2.connect(DATABASE_URL)
    tickers = get_active_tickers(conn, tickers_filter, args.limit)

    # Exclude financial-sector stocks if requested
    if args.exclude_financials:
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(tickers))
        cur.execute("""
            SELECT ticker FROM stocks
            WHERE ticker IN ({0})
              AND (is_financial = TRUE
                   OR sector ILIKE '%%금융%%' OR sector ILIKE '%%bank%%'
                   OR sector ILIKE '%%보험%%' OR sector ILIKE '%%insurance%%'
                   OR industry ILIKE '%%금융%%' OR industry ILIKE '%%은행%%'
                   OR industry ILIKE '%%증권%%' OR industry ILIKE '%%securities%%')
        """.format(placeholders), tuple(tickers))
        financial_tickers = set(r[0] for r in cur.fetchall())
        cur.close()
        if financial_tickers:
            tickers = [t for t in tickers if t not in financial_tickers]
            print("Excluded {0} financial-sector stocks: {1}".format(
                len(financial_tickers), ", ".join(sorted(financial_tickers))))

    log_id = log_start(conn, {"as_of_date": as_of, "tickers": args.tickers, "limit": args.limit,
                               "exclude_financials": args.exclude_financials})

    print("Calculating factors for {0} stocks as of {1}...".format(len(tickers), as_of))
    print("  Scoring method: percentile_rank (0-100, ties get average rank)")
    print("  Universe scope: {0}".format(universe_name_for_scope))

    try:
        # Step 1: Load stock metadata for industry/sector grouping
        ticker_metadata = {}
        ticker_industry_map = {}
        for ticker in tickers:
            meta = get_stock_metadata(conn, ticker)
            ticker_metadata[ticker] = meta
            ticker_industry_map[ticker] = meta.get("industry") or meta.get("sector") or ticker

        # Step 2: Compute raw values for all tickers
        all_raw = {}  # ticker -> {factorId: rawValue}
        all_missing = {}  # ticker -> {factorId: missingReason}
        for i, ticker in enumerate(tickers):
            factors, missing = compute_all_factors(conn, ticker, as_of)
            all_raw[ticker] = factors
            all_missing[ticker] = missing
            n_factors = sum(1 for v in factors.values() if v is not None)
            if (i + 1) % 50 == 0 or i == len(tickers) - 1:
                print("  [{0}/{1}] {2}: {3} factors computed".format(i + 1, len(tickers), ticker, n_factors))

        # Diagnostic: market_cap source summary
        mcap_count = sum(1 for t in tickers if all_raw.get(t, {}).get("market_cap") is not None)
        print("  Market cap available: {0}/{1} stocks".format(mcap_count, len(tickers)))

        # Step 3: Compute industry momentum
        compute_industry_factors(all_raw, ticker_industry_map, tickers)

        # Step 4: Collect all factor IDs
        all_factor_ids = set()
        for factors in all_raw.values():
            all_factor_ids.update(factors.keys())

        print("  {0} unique factors across {1} stocks".format(len(all_factor_ids), len(tickers)))

        # Step 5: Percentile rank each factor within the chosen universe scope.
        # Each row carries universe_name so multiple universes can store
        # ranks for the same (ticker, factor_id, date) without collision.
        snapshot_rows = []
        for factor_id in sorted(all_factor_ids):
            factor_meta = FACTORS.get(factor_id, {})
            raw_by_ticker = {t: fs.get(factor_id) for t, fs in all_raw.items()}
            ranks = percentile_rank(raw_by_ticker, factor_meta)

            for ticker, pct_rank in ranks.items():
                raw = raw_by_ticker[ticker]
                snapshot_rows.append((
                    universe_name_for_scope, ticker, factor_id, as_of,
                    raw, pct_rank, "calculated",
                ))

        # Step 6: Upsert
        n = upsert_factor_snapshots(conn, snapshot_rows)
        print("  Upserted {0} factor snapshot rows".format(n))

        # Step 7: Print coverage summary
        print_coverage_summary(all_raw, all_missing, all_factor_ids, tickers)

        log_finish(conn, log_id, "success", rows_processed=len(tickers), rows_inserted=n)

    except Exception as e:
        log_finish(conn, log_id, "error", error_message=str(e))
        print("ERROR: {0}".format(e))
        raise
    finally:
        conn.close()

    print("Done!")
