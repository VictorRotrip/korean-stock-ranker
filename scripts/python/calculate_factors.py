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
from factor_calculators import fundamental_ttm

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


def get_pit_marcap_status(conn, tickers, as_of):
    """For each ticker, determine the source of its effective market_cap row.

    Returns dict {ticker: source_label} where source_label is what's stored
    in daily_prices.source for the most recent market_cap row on or before
    as_of. Tickers with no market_cap row are absent from the result.
    """
    if not tickers:
        return {}
    cur = conn.cursor()
    cur.execute("""
        WITH latest AS (
            SELECT ticker, MAX(date) AS d
            FROM daily_prices
            WHERE ticker = ANY(%s)
              AND date <= %s
              AND market_cap IS NOT NULL AND market_cap > 0
            GROUP BY ticker
        )
        SELECT dp.ticker, dp.source
        FROM latest l
        JOIN daily_prices dp ON dp.ticker = l.ticker AND dp.date = l.d
    """, (list(tickers), as_of))
    rows = cur.fetchall()
    cur.close()
    return {t: s for t, s in rows}


# Factors that consume market_cap directly. When a stock's effective marcap
# source isn't 'marcap_historical' AND we're in PIT-strict mode, these are
# explicitly marked as missing with reason 'non_pit_market_cap' rather than
# computed against biased market cap.
MARKET_CAP_DEPENDENT_FACTORS = frozenset({
    "market_cap",
    "log_market_cap",
    "pe_ttm_inv",
    "price_book",
    "price_sales_ttm_inv",
    "ev_sales_ttm_inv",
    "ebitda_ev",
    "gross_profit_ev",
    "ocf_mcap",
    "fcf_mcap",
    "ufcf_ev",
    "dividend_yield",
})


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


def _ebitda_from_ttm(income_ttm):
    """Best-effort TTM EBITDA = operating_income_ttm + depreciation_ttm.

    Falls back to None if either component is missing.
    """
    oi = income_ttm.get("operating_income")
    da = income_ttm.get("depreciation_amortization")
    if oi is None:
        return None
    return oi + (da or 0)


def build_fundamental_inputs(conn, ticker, as_of, fallback_fin):
    """Build the (fin_dict, prior_dict, fundamental_meta) tuple used by the
    fundamental factor calculators.

    Strategy
    --------
    1. Try TTM from fundamental_snapshots (PIT-safe, quarterly-aware).
    2. If TTM is unavailable, fall back to the legacy annual-only `fin`
       passed in (which is what the original code path used).

    The prior dict for YoY growth uses fundamental_snapshots' same-window
    one-year-earlier view when TTM was available; otherwise it falls back
    to legacy prior-annual at the call site (handled in compute_all_factors).

    Returns
    -------
    fin_dict : dict   keys compatible with factor_calculators.fundamental
                      (revenue, net_income, total_equity, etc.)
    prior_dict : dict|None
    fundamental_meta : dict
        {
          "income_source":   "ttm_quarterly" | "latest_annual"
                            | "annual_fallback"
                            | "insufficient_quarterly_history"
                            | "annual_only"            # used legacy fin
                            | "unavailable",
          "balance_source":  "latest_quarterly" | "latest_annual"
                            | "unavailable",
          "ttm_period_end":  date or None,
          "annual_period_end": date or None,
          "available_quarters": int,
        }
    """
    snap = fundamental_ttm.get_pit_fundamentals(conn, ticker, as_of)
    src = snap["meta"]["income_source"]

    if src in ("ttm_quarterly", "annual_fallback", "latest_annual"):
        income = snap["income_ttm"]
        balance = snap["balance"]
        # The fundamental factor functions expect a single dict with the
        # union of income + balance fields. Build that union here, mapping
        # field names where they differ between fundamental_snapshots and
        # the fundamental.py calculator's expectations.
        fin_dict = {
            # income statement
            "revenue":            income.get("revenue"),
            "gross_profit":       income.get("gross_profit"),
            "operating_income":   income.get("operating_income"),
            "net_income":         income.get("net_income"),
            "eps":                income.get("eps"),
            "operating_cash_flow": income.get("operating_cash_flow"),
            "capital_expenditure": income.get("capex"),
            "free_cash_flow":     income.get("free_cash_flow"),
            "interest_expense":   income.get("interest_expense"),
            "depreciation":       income.get("depreciation_amortization"),
            "ebitda":             _ebitda_from_ttm(income),
            # balance sheet
            "total_assets":       balance.get("total_assets"),
            "total_equity":       balance.get("total_equity"),
            "total_liabilities":  balance.get("total_liabilities"),
            "total_debt":         balance.get("total_debt"),
            "cash":               balance.get("cash_and_equivalents"),
            "shares_outstanding": balance.get("shares_outstanding"),
            # legacy compatibility — none of these are sourced from TTM
            "cost_of_revenue":    None,
            "current_assets":     None,
            "current_liabilities": None,
            "dividends_paid":     None,
        }
        # Build the prior-year window for YoY growth.
        prior = fundamental_ttm.get_pit_fundamentals_prior_year(
            conn, ticker, as_of, snap["meta"].get("ttm_period_end")
            or snap["meta"].get("annual_period_end"))
        prior_dict = None
        if prior is not None:
            p_inc = prior["income_ttm"]
            prior_dict = {
                "revenue":            p_inc.get("revenue"),
                "gross_profit":       p_inc.get("gross_profit"),
                "operating_income":   p_inc.get("operating_income"),
                "net_income":         p_inc.get("net_income"),
                "eps":                p_inc.get("eps"),
                "operating_cash_flow": p_inc.get("operating_cash_flow"),
                "free_cash_flow":     p_inc.get("free_cash_flow"),
            }
        return fin_dict, prior_dict, snap["meta"]

    # TTM completely unavailable: fall back to legacy annual-only `fin`.
    if fallback_fin is not None:
        return fallback_fin, None, {
            "income_source": "annual_only",
            "balance_source": "annual_only",
            "ttm_period_end": None,
            "annual_period_end": fallback_fin.get("period_end"),
            "available_quarters": 0,
        }
    return None, None, {
        "income_source": "unavailable",
        "balance_source": "unavailable",
        "ttm_period_end": None,
        "annual_period_end": None,
        "available_quarters": 0,
    }


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

def compute_all_factors(conn, ticker, as_of, mcap_pit_safe=True):
    """Compute all implemented factors for a single stock.

    Args:
        conn: psycopg2 connection
        ticker: KRX ticker
        as_of: ISO date string
        mcap_pit_safe: True if this ticker's effective market_cap comes from a
            point-in-time source ('marcap_historical'). When False AND we're
            in PIT-strict mode (handled by the caller via the value passed
            here), all market-cap-dependent factors are explicitly marked
            missing with reason 'non_pit_market_cap'.

    Returns:
        dict: {factorId: rawValue or None, ...}
        dict: {factorId: missingReason or None, ...}
    """
    prices = get_price_history(conn, ticker, as_of)
    # Legacy annual-only PIT fundamentals — kept as a fallback for tickers
    # that don't have rows in fundamental_snapshots yet.
    fin_legacy = get_latest_financials(conn, ticker, as_of)
    prior_legacy = None
    if fin_legacy:
        prior_legacy = get_prior_financials(conn, ticker, as_of, fin_legacy["period_end"])
    # New: build TTM-aware `fin` from fundamental_snapshots.
    # `fin_meta` records whether income data came from TTM, annual fallback,
    # or was unavailable; downstream factor source labels use this.
    fin, prior_ttm, fin_meta = build_fundamental_inputs(
        conn, ticker, as_of, fallback_fin=fin_legacy)
    # Use the TTM same-period prior when available; otherwise fall back to
    # the legacy prior-annual we already loaded.
    prior = prior_ttm if prior_ttm is not None else prior_legacy
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
            if fin and fin.get("shares_outstanding") and fin["shares_outstanding"] > 0:
                shares_outstanding = fin["shares_outstanding"]
                market_cap = latest_close * shares_outstanding
            elif shares_outstanding and shares_outstanding > 0:
                market_cap = latest_close * shares_outstanding

    # PIT enforcement: if the caller has determined this ticker's market cap
    # is not point-in-time safe, treat market_cap as missing for all factor
    # calculations. This cascades cleanly to all market-cap-dependent factors.
    if not mcap_pit_safe:
        market_cap = None

    factors = {}
    missing_reasons = {}
    source_methods = {}

    implemented = get_implemented_factors()

    for factor_id, factor_meta in implemented.items():
        data_source = factor_meta.get("data_source")
        # Hard-skip market-cap-dependent factors when PIT-unsafe so they
        # carry the explicit reason 'non_pit_market_cap' instead of a
        # generic 'no_data'. The downstream UI / diagnostics show this
        # reason verbatim.
        if not mcap_pit_safe and factor_id in MARKET_CAP_DEPENDENT_FACTORS:
            factors[factor_id] = None
            missing_reasons[factor_id] = "non_pit_market_cap"
            source_methods[factor_id] = "non_pit_market_cap"
            continue

        compute_fn_name = factor_meta.get("compute_function")
        if not compute_fn_name:
            factors[factor_id] = None
            missing_reasons[factor_id] = "unavailable"
            source_methods[factor_id] = "unavailable"
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

            # Record source-method per factor. For DART-derived factors,
            # use the TTM/annual provenance recorded in fin_meta. Other
            # factor types get their own source labels.
            if data_source == "dart":
                if raw_value is None:
                    # If the factor failed for fundamentals reasons, surface
                    # the same fin_meta source so the operator can see
                    # whether quarterly data was the limiting factor.
                    source_methods[factor_id] = fin_meta.get(
                        "income_source", "unavailable")
                else:
                    source_methods[factor_id] = fin_meta.get(
                        "income_source", "calculated")
            elif data_source == "price":
                source_methods[factor_id] = (
                    "calculated" if raw_value is not None else "no_price_data"
                )
            elif data_source == "short_interest":
                source_methods[factor_id] = (
                    "calculated" if raw_value is not None else "no_short_data"
                )
            elif data_source == "estimates":
                source_methods[factor_id] = "data_unavailable"
            elif data_source == "derived":
                source_methods[factor_id] = "post_processed"
            else:
                source_methods[factor_id] = (
                    "calculated" if raw_value is not None else "unknown"
                )

        except Exception as e:
            factors[factor_id] = None
            missing_reasons[factor_id] = "computation_error: {0}".format(str(e))
            source_methods[factor_id] = "computation_error"

    return factors, missing_reasons, source_methods


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

def clear_existing_snapshots(conn, universe_name, as_of, tickers):
    """Delete factor_snapshots for the given universe + date + ticker scope.

    This runs at the start of every recalculation pass. Without it, factor
    rows from a PREVIOUS calculation can outlive the new run when a factor
    becomes missing (e.g. PIT enforcement suppresses a market-cap-dependent
    factor) -- the upsert wouldn't touch those stale rows because no new
    INSERT row exists for the same (universe, ticker, factor, date) key.

    Returns the number of rows deleted.
    """
    if not tickers:
        return 0
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM factor_snapshots
        WHERE universe_name = %s
          AND date = %s
          AND ticker = ANY(%s)
    """, (universe_name, as_of, list(tickers)))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    return deleted


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

    # Point-in-time market cap enforcement.
    # Default behavior: PIT-strict for historical as-of dates, off for current.
    pit_group = parser.add_mutually_exclusive_group()
    pit_group.add_argument(
        "--require-pit-market-cap", dest="pit_mode",
        action="store_const", const="require",
        help=("Enforce PIT market cap. Stocks whose effective market_cap "
              "source is not 'marcap_historical' get all market-cap-dependent "
              "factors marked missing with reason 'non_pit_market_cap'. "
              "Default for historical as-of dates."))
    pit_group.add_argument(
        "--allow-snapshot-market-cap", dest="pit_mode",
        action="store_const", const="allow",
        help=("Allow snapshot market cap. Use only for current-day "
              "validation; produces biased value factors for historical dates."))
    parser.set_defaults(pit_mode=None)  # auto

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

    # Resolve PIT mode: explicit flag overrides; otherwise auto from date.
    _today_iso = datetime.now().strftime("%Y-%m-%d")
    _is_historical = as_of < _today_iso
    if args.pit_mode == "require":
        require_pit = True
    elif args.pit_mode == "allow":
        require_pit = False
    else:
        require_pit = _is_historical  # auto: strict for historical dates

    print("  PIT mode:       {0} ({1})".format(
        "require_pit_market_cap" if require_pit else "allow_snapshot_market_cap",
        "historical date" if _is_historical else "current date"))

    # Build per-ticker PIT-safety map for the universe.
    mcap_sources = get_pit_marcap_status(conn, tickers, as_of)
    pit_safe_set = set()
    non_pit_set = set()
    no_mcap_set = set()
    for t in tickers:
        src = mcap_sources.get(t)
        if src is None:
            no_mcap_set.add(t)
        elif src == "marcap_historical":
            pit_safe_set.add(t)
        else:
            non_pit_set.add(t)
    print("  Market cap PIT: {0} PIT-safe, {1} snapshot-only, {2} no marcap row".format(
        len(pit_safe_set), len(non_pit_set), len(no_mcap_set)))
    if require_pit and non_pit_set:
        print("  -> {0} stocks will get non_pit_market_cap on all "
              "market-cap-dependent factors.".format(len(non_pit_set)))

    # Tell the operator what's about to happen with non-PIT market cap.
    # The wording differs based on whether we're enforcing PIT (suppressing
    # the dependent factors) or allowing snapshot (computing biased values).
    if _is_historical and non_pit_set:
        if require_pit:
            print("  ! Note: {0}/{1} stocks have non-PIT market cap on {2}. "
                  "Market-cap-dependent factors will be SUPPRESSED for these "
                  "stocks (reason='non_pit_market_cap').".format(
                      len(non_pit_set), len(tickers), as_of))
            print("  ! To backfill PIT data: python ingest_marcap.py --source "
                  "historical --as-of-date {0} --universe {1}".format(
                      as_of, universe_name_for_scope))
        else:
            print("  ! WARNING: --allow-snapshot-market-cap is in effect and "
                  "{0}/{1} stocks use snapshot market_cap on {2} (historical). "
                  "Value factors WILL BE BIASED for these stocks.".format(
                      len(non_pit_set), len(tickers), as_of))
            print("  ! For PIT-safe ranking, re-run with --require-pit-market-cap "
                  "or backfill: python ingest_marcap.py --source historical "
                  "--as-of-date {0} --universe {1}".format(
                      as_of, universe_name_for_scope))

    try:
        # Step 1: Load stock metadata for industry/sector grouping
        ticker_metadata = {}
        ticker_industry_map = {}
        for ticker in tickers:
            meta = get_stock_metadata(conn, ticker)
            ticker_metadata[ticker] = meta
            ticker_industry_map[ticker] = meta.get("industry") or meta.get("sector") or ticker

        # Step 2: Compute raw values for all tickers.
        # Each ticker's mcap_pit_safe flag depends on (a) the policy
        # (require_pit) and (b) the source of its effective market_cap row.
        all_raw = {}  # ticker -> {factorId: rawValue}
        all_missing = {}  # ticker -> {factorId: missingReason}
        all_methods = {}  # ticker -> {factorId: source_method}
        ttm_status_counts = defaultdict(int)
        for i, ticker in enumerate(tickers):
            if require_pit:
                pit_safe = ticker in pit_safe_set
            else:
                pit_safe = True  # treat all as safe; biased factors allowed
            factors, missing, methods = compute_all_factors(
                conn, ticker, as_of, mcap_pit_safe=pit_safe,
            )
            all_raw[ticker] = factors
            all_missing[ticker] = missing
            all_methods[ticker] = methods
            # Record this ticker's TTM status (the source method on a single
            # representative DART factor). 'earnings_yield' is consumed by
            # the value scoring pass and is sourced from TTM income, so we
            # treat its method as a proxy for the ticker's fundamental
            # source. Falls back to scanning any DART factor.
            ts = methods.get("earnings_yield")
            if ts is None:
                for fid, m in methods.items():
                    fmeta = FACTORS.get(fid, {})
                    if fmeta.get("data_source") == "dart":
                        ts = m
                        break
            ttm_status_counts[ts or "unknown"] += 1
            n_factors = sum(1 for v in factors.values() if v is not None)
            if (i + 1) % 50 == 0 or i == len(tickers) - 1:
                print("  [{0}/{1}] {2}: {3} factors computed".format(i + 1, len(tickers), ticker, n_factors))

        # TTM coverage summary across the universe.
        print("")
        print("  TTM / fundamental source breakdown:")
        for label in sorted(ttm_status_counts.keys()):
            print("    {0:<35} {1}/{2}".format(label, ttm_status_counts[label], len(tickers)))

        # Diagnostic: market_cap source summary
        mcap_count = sum(1 for t in tickers if all_raw.get(t, {}).get("market_cap") is not None)
        print("  Market cap available: {0}/{1} stocks".format(mcap_count, len(tickers)))

        # Step 3: Compute industry momentum
        compute_industry_factors(all_raw, ticker_industry_map, tickers)

        # Industry factors are computed in post-processing; record their
        # source method so the snapshot row doesn't end up with the default.
        for ticker in tickers:
            for fid in ("industry_momentum_26w", "industry_momentum_52w"):
                if all_raw.get(ticker, {}).get(fid) is not None:
                    all_methods.setdefault(ticker, {})[fid] = "post_processed"
                else:
                    all_methods.setdefault(ticker, {})[fid] = "no_industry_data"

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
                # Per-factor source method records the fundamental provenance
                # ('ttm_quarterly', 'annual_fallback', 'non_pit_market_cap',
                # etc). Truncated to 50 chars to fit the migrated VARCHAR(50)
                # column on factor_snapshots.source.
                src_method = (
                    all_methods.get(ticker, {}).get(factor_id)
                    or "calculated"
                )
                if len(src_method) > 50:
                    src_method = src_method[:50]
                snapshot_rows.append((
                    universe_name_for_scope, ticker, factor_id, as_of,
                    raw, pct_rank, src_method,
                ))

        # Step 6a: Clear stale snapshots for this universe+date+ticker scope.
        # Without this, factors that become missing in the new run (e.g.
        # market-cap-dependent factors suppressed by PIT enforcement) would
        # leave their old percentile_rank rows in place. The downstream
        # ranking would then read those stale rows and produce wrong scores.
        deleted = clear_existing_snapshots(
            conn, universe_name_for_scope, as_of, tickers)
        print("  Cleared {0} existing factor snapshots for "
              "universe={1} date={2}".format(
                  deleted, universe_name_for_scope, as_of))

        # Step 6b: Upsert the freshly computed rows.
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
