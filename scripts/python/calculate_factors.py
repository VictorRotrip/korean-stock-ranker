"""
Calculate factor values from ingested data and store in factor_snapshots.

This script reads from daily_prices, financial_statements, pykrx_fundamentals,
and short_selling tables, computes raw factor values and percentile ranks,
and writes to the factor_snapshots table.

Usage:
    python calculate_factors.py --tickers 005930,000660,035420,051910,005380 --as-of-date 2024-12-31
    python calculate_factors.py --as-of-date 2024-12-31              # all active stocks
    python calculate_factors.py --as-of-date 2024-12-31 --limit 50   # first 50 tickers
"""

import os
import sys
import argparse
import math
from datetime import datetime

import psycopg2
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

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
            q += f" LIMIT {int(limit)}"
        cur.execute(q)
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    return tickers


def get_latest_price(conn, ticker, as_of):
    cur = conn.cursor()
    cur.execute("""
        SELECT close, market_cap, shares_outstanding, volume
        FROM daily_prices WHERE ticker = %s AND date <= %s
        ORDER BY date DESC LIMIT 1
    """, (ticker, as_of))
    row = cur.fetchone()
    cur.close()
    if row:
        return {"close": row[0], "market_cap": row[1], "shares_outstanding": row[2], "volume": row[3]}
    return None


def get_price_history(conn, ticker, as_of, days=260):
    """Get up to `days` trading days of price history ending at as_of."""
    cur = conn.cursor()
    cur.execute("""
        SELECT date, close, high, volume FROM daily_prices
        WHERE ticker = %s AND date <= %s
        ORDER BY date DESC LIMIT %s
    """, (ticker, as_of, days))
    rows = cur.fetchall()
    cur.close()
    return list(reversed(rows))  # oldest first


def get_latest_financials(conn, ticker, as_of):
    """Point-in-time safe: only returns data where data_available_date <= as_of."""
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
    cur = conn.cursor()
    cur.execute("""
        SELECT revenue, operating_income, net_income, eps, free_cash_flow
        FROM financial_statements
        WHERE ticker = %s AND data_available_date <= %s
              AND statement_type = 'annual' AND consolidated_or_separate = 'consolidated'
              AND period_end < %s
        ORDER BY period_end DESC LIMIT 1
    """, (ticker, as_of, latest_period_end))
    row = cur.fetchone()
    cur.close()
    if row:
        return {"revenue": row[0], "operating_income": row[1], "net_income": row[2],
                "eps": row[3], "free_cash_flow": row[4]}
    return None


def get_short_selling(conn, ticker, as_of):
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
# Factor computations
# ---------------------------------------------------------------------------

def safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def compute_return(prices, days_back, skip=0):
    """Compute return from price history [(date, close, high, volume), ...]"""
    if len(prices) < days_back + skip + 1:
        return None
    end_idx = len(prices) - 1 - skip
    start_idx = end_idx - days_back
    if start_idx < 0 or end_idx < 0:
        return None
    p_start = prices[start_idx][1]  # close
    p_end = prices[end_idx][1]
    if not p_start or p_start == 0:
        return None
    return (p_end - p_start) / p_start


def compute_volatility(prices, window=60):
    recent = prices[-(window + 1):]
    if len(recent) < 30:
        return None
    returns = []
    for i in range(1, len(recent)):
        prev = recent[i - 1][1]
        curr = recent[i][1]
        if prev and prev > 0:
            returns.append((curr - prev) / prev)
    if len(returns) < 20:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(252)


def compute_all_factors(conn, ticker, as_of):
    """Compute all factors for a single stock. Returns dict of factorId → rawValue."""
    price = get_latest_price(conn, ticker, as_of)
    if not price or not price["close"]:
        return {}

    prices = get_price_history(conn, ticker, as_of)
    fin = get_latest_financials(conn, ticker, as_of)
    prior = get_prior_financials(conn, ticker, as_of, fin["period_end"]) if fin else None
    short = get_short_selling(conn, ticker, as_of)

    mcap = price["market_cap"]
    factors = {}

    # --- Value ---
    if fin and mcap:
        factors["earnings_yield"] = safe_div(fin["net_income"], mcap)
        factors["book_to_market"] = safe_div(fin["total_equity"], mcap)
        factors["sales_yield"] = safe_div(fin["revenue"], mcap)
        factors["cf_yield"] = safe_div(fin["free_cash_flow"], mcap)
        cash = fin["cash"] or 0
        debt = fin["total_debt"] or 0
        ev = mcap + debt - cash
        factors["ev_ebitda"] = safe_div(fin["ebitda"], ev) if ev and ev > 0 else None
        factors["dividend_yield"] = safe_div(abs(fin["dividends_paid"]) if fin["dividends_paid"] else None, mcap)

    # --- Quality ---
    if fin:
        factors["roe"] = safe_div(fin["net_income"], fin["total_equity"])
        factors["roa"] = safe_div(fin["net_income"], fin["total_assets"])
        factors["gross_profitability"] = safe_div(fin["gross_profit"], fin["total_assets"])
        factors["operating_margin"] = safe_div(fin["operating_income"], fin["revenue"])
        factors["debt_to_equity"] = safe_div(fin["total_debt"], fin["total_equity"])
        factors["interest_coverage"] = safe_div(
            fin["ebitda"], abs(fin["interest_expense"]) if fin["interest_expense"] else None)

    # --- Growth ---
    if fin and prior:
        factors["revenue_growth"] = safe_div(
            (fin["revenue"] or 0) - (prior["revenue"] or 0),
            abs(prior["revenue"]) if prior["revenue"] else None)
        factors["eps_growth"] = safe_div(
            (fin["eps"] or 0) - (prior["eps"] or 0),
            abs(prior["eps"]) if prior["eps"] else None)
        factors["op_income_growth"] = safe_div(
            (fin["operating_income"] or 0) - (prior["operating_income"] or 0),
            abs(prior["operating_income"]) if prior["operating_income"] else None)
        factors["fcf_growth"] = safe_div(
            (fin["free_cash_flow"] or 0) - (prior["free_cash_flow"] or 0),
            abs(prior["free_cash_flow"]) if prior["free_cash_flow"] else None)

    # --- Momentum ---
    factors["momentum_12_1"] = compute_return(prices, 231, skip=21)
    factors["momentum_6m"] = compute_return(prices, 126)
    factors["momentum_3m"] = compute_return(prices, 63)
    factors["reversal_1m"] = compute_return(prices, 21)

    if len(prices) >= 20:
        high_52w = max(p[2] for p in prices[-252:] if p[2])  # high column
        if high_52w and high_52w > 0:
            factors["dist_52w_high"] = price["close"] / high_52w

    # --- Risk ---
    factors["volatility_60d"] = compute_volatility(prices, 60)

    # --- Liquidity ---
    recent_30 = prices[-30:]
    if len(recent_30) >= 10 and price["shares_outstanding"] and price["shares_outstanding"] > 0:
        avg_vol = sum(p[3] for p in recent_30 if p[3]) / len(recent_30)
        factors["turnover_ratio"] = avg_vol / price["shares_outstanding"]

    # --- Short Interest ---
    if short:
        factors["short_ratio"] = (short["short_ratio"] or 0) / 100.0
        if short["short_balance"] and price["shares_outstanding"] and price["shares_outstanding"] > 0:
            factors["short_balance_ratio"] = short["short_balance"] / price["shares_outstanding"]

    # Filter out None values
    return {k: v for k, v in factors.items() if v is not None}


# ---------------------------------------------------------------------------
# Percentile ranking
# ---------------------------------------------------------------------------

# direction: higher raw = higher rank, except for these
LOWER_IS_BETTER = {"debt_to_equity", "volatility_60d", "short_ratio", "short_balance_ratio", "reversal_1m"}


def percentile_rank_all(ticker_factors, factor_id):
    """Rank a factor across all tickers. Returns dict ticker → percentile (0-100)."""
    items = [(t, v) for t, v in ticker_factors.items() if v is not None]
    if len(items) < 2:
        return {t: 50.0 for t, _ in items}

    items.sort(key=lambda x: x[1])
    n = len(items)
    ranks = {}
    i = 0
    while i < n:
        j = i
        while j < n and items[j][1] == items[i][1]:
            j += 1
        avg_rank = (i + j - 1) / 2
        for k in range(i, j):
            pct = (avg_rank / (n - 1)) * 100 if n > 1 else 50
            if factor_id in LOWER_IS_BETTER:
                pct = 100 - pct
            ranks[items[k][0]] = round(pct, 2)
        i = j
    return ranks


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_factor_snapshots(conn, rows):
    """rows: list of (ticker, factor_id, date, raw_value, percentile_rank, source)"""
    if not rows:
        return 0
    cur = conn.cursor()
    query = """
    INSERT INTO factor_snapshots (ticker, factor_id, date, raw_value, percentile_rank, source)
    VALUES %s
    ON CONFLICT (ticker, factor_id, date) DO UPDATE SET
        raw_value = EXCLUDED.raw_value,
        percentile_rank = EXCLUDED.percentile_rank,
        source = EXCLUDED.source
    """
    execute_values(cur, query, rows)
    conn.commit()
    cur.close()
    return len(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calculate factor values from DB data")
    parser.add_argument("--as-of-date", required=True, help="Ranking date YYYY-MM-DD")
    parser.add_argument("--tickers", help="Comma-separated tickers")
    parser.add_argument("--limit", type=int, help="Max tickers to process")
    args = parser.parse_args()

    as_of = args.as_of_date
    tickers_filter = args.tickers.split(",") if args.tickers else None

    conn = psycopg2.connect(DATABASE_URL)
    tickers = get_active_tickers(conn, tickers_filter, args.limit)

    log_id = log_start(conn, {"as_of_date": as_of, "tickers": args.tickers, "limit": args.limit})

    print(f"Calculating factors for {len(tickers)} stocks as of {as_of}...")

    try:
        # Step 1: compute raw values for all tickers
        all_raw = {}  # ticker → {factorId → rawValue}
        for i, ticker in enumerate(tickers):
            factors = compute_all_factors(conn, ticker, as_of)
            all_raw[ticker] = factors
            n_factors = len(factors)
            if (i + 1) % 50 == 0 or i == len(tickers) - 1:
                print(f"  [{i+1}/{len(tickers)}] {ticker}: {n_factors} factors computed")

        # Step 2: collect all factor IDs
        all_factor_ids = set()
        for factors in all_raw.values():
            all_factor_ids.update(factors.keys())

        print(f"  {len(all_factor_ids)} unique factors across {len(tickers)} stocks")

        # Step 3: percentile rank each factor
        snapshot_rows = []
        for factor_id in sorted(all_factor_ids):
            raw_by_ticker = {t: fs.get(factor_id) for t, fs in all_raw.items() if factor_id in fs}
            ranks = percentile_rank_all(raw_by_ticker, factor_id)

            for ticker, pct in ranks.items():
                raw = raw_by_ticker[ticker]
                snapshot_rows.append((ticker, factor_id, as_of, raw, pct, "calculated"))

        # Step 4: upsert
        n = upsert_factor_snapshots(conn, snapshot_rows)
        print(f"  Upserted {n} factor snapshot rows")

        log_finish(conn, log_id, "success", rows_processed=len(tickers), rows_inserted=n)

    except Exception as e:
        log_finish(conn, log_id, "error", error_message=str(e))
        print(f"ERROR: {e}")
        raise
    finally:
        conn.close()

    print("Done!")
