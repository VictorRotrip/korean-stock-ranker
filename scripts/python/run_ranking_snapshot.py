"""
Run a ranking system against factor_snapshots and store the result in ranking_snapshots.

This script:
1. Loads factor_snapshots for the given date
2. Loads a ranking system (by ID or the default)
3. Applies the weighted tree aggregation
4. Stores the result as a ranking_snapshot in the DB

Usage:
    python run_ranking_snapshot.py --as-of-date 2024-12-31
    python run_ranking_snapshot.py --as-of-date 2024-12-31 --system-id p123-inspired
    python run_ranking_snapshot.py --as-of-date 2024-12-31 --system-id default
    python run_ranking_snapshot.py --as-of-date 2024-12-31 --limit 100
"""

import os
import sys
import json
import argparse
from datetime import datetime

import psycopg2
from psycopg2.extras import Json as PgJson
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "run_ranking_snapshot"

# ---------------------------------------------------------------------------
# Legacy default ranking system tree (kept for backward compatibility)
# ---------------------------------------------------------------------------

DEFAULT_TREE = {
    "id": "root",
    "type": "composite",
    "name": "Composite",
    "weight": 100,
    "children": [
        {
            "id": "cat-value", "type": "category", "name": "Value", "weight": 30,
            "children": [
                {"id": "f-ey", "type": "factor", "name": "Earnings Yield", "weight": 35, "factorId": "earnings_yield"},
                {"id": "f-bm", "type": "factor", "name": "Book-to-Market", "weight": 25, "factorId": "book_to_market"},
                {"id": "f-ev", "type": "factor", "name": "EV/EBITDA (inv)", "weight": 25, "factorId": "ev_ebitda"},
                {"id": "f-cfy", "type": "factor", "name": "Cash Flow Yield", "weight": 15, "factorId": "cf_yield"},
            ],
        },
        {
            "id": "cat-quality", "type": "category", "name": "Quality", "weight": 25,
            "children": [
                {"id": "f-roe", "type": "factor", "name": "ROE", "weight": 35, "factorId": "roe"},
                {"id": "f-gp", "type": "factor", "name": "Gross Profitability", "weight": 30, "factorId": "gross_profitability"},
                {"id": "f-om", "type": "factor", "name": "Operating Margin", "weight": 20, "factorId": "operating_margin"},
                {"id": "f-de", "type": "factor", "name": "Debt/Equity", "weight": 15, "factorId": "debt_to_equity"},
            ],
        },
        {
            "id": "cat-growth", "type": "category", "name": "Growth", "weight": 20,
            "children": [
                {"id": "f-revg", "type": "factor", "name": "Revenue Growth", "weight": 40, "factorId": "revenue_growth"},
                {"id": "f-epsg", "type": "factor", "name": "EPS Growth", "weight": 35, "factorId": "eps_growth"},
                {"id": "f-opg", "type": "factor", "name": "Op Income Growth", "weight": 25, "factorId": "op_income_growth"},
            ],
        },
        {
            "id": "cat-momentum", "type": "category", "name": "Momentum", "weight": 25,
            "children": [
                {"id": "f-mom12", "type": "factor", "name": "12-1M Momentum", "weight": 50, "factorId": "momentum_12_1"},
                {"id": "f-mom6", "type": "factor", "name": "6M Momentum", "weight": 30, "factorId": "momentum_6m"},
                {"id": "f-52w", "type": "factor", "name": "Dist from 52W High", "weight": 20, "factorId": "dist_52w_high"},
            ],
        },
    ],
}

# ---------------------------------------------------------------------------
# P123-inspired ranking system tree (new default)
# ---------------------------------------------------------------------------

P123_TREE = {
    "id": "root",
    "type": "composite",
    "name": "P123 Inspired Korea Multi-Factor",
    "weight": 100,
    "children": [
        # === Core: Value — 25% ===
        {
            "id": "cat-value", "type": "category", "name": "Value", "weight": 25,
            "children": [
                {"id": "sub-val-earn", "type": "composite", "name": "Earnings-Based", "weight": 35,
                 "children": [
                    {"id": "f-ey", "type": "factor", "name": "Earnings Yield", "weight": 50, "factorId": "pe_ttm_inv"},
                    {"id": "f-ebitda-ev", "type": "factor", "name": "EBITDA/EV", "weight": 50, "factorId": "ebitda_ev"},
                 ]},
                {"id": "sub-val-sales", "type": "composite", "name": "Sales-Based", "weight": 30,
                 "children": [
                    {"id": "f-ps", "type": "factor", "name": "Sales Yield", "weight": 40, "factorId": "price_sales_ttm_inv"},
                    {"id": "f-evs", "type": "factor", "name": "Revenue/EV", "weight": 30, "factorId": "ev_sales_ttm_inv"},
                    {"id": "f-gpev", "type": "factor", "name": "Gross Profit/EV", "weight": 30, "factorId": "gross_profit_ev"},
                 ]},
                {"id": "sub-val-fcf", "type": "composite", "name": "FCF-Based", "weight": 20,
                 "children": [
                    {"id": "f-fcfy", "type": "factor", "name": "FCF Yield", "weight": 40, "factorId": "fcf_mcap"},
                    {"id": "f-ocfy", "type": "factor", "name": "OCF Yield", "weight": 30, "factorId": "ocf_mcap"},
                    {"id": "f-ufcf", "type": "factor", "name": "Unlevered FCF/EV", "weight": 30, "factorId": "ufcf_ev"},
                 ]},
                {"id": "sub-val-asset", "type": "composite", "name": "Asset-Based", "weight": 15,
                 "children": [
                    {"id": "f-pb", "type": "factor", "name": "Book/Market", "weight": 60, "factorId": "price_book"},
                    {"id": "f-divy", "type": "factor", "name": "Dividend Yield", "weight": 40, "factorId": "dividend_yield"},
                 ]},
            ],
        },
        # === Core: Quality — 30% ===
        {
            "id": "cat-quality", "type": "category", "name": "Quality", "weight": 30,
            "children": [
                {"id": "sub-q-margin", "type": "composite", "name": "Margins", "weight": 25,
                 "children": [
                    {"id": "f-opmgn", "type": "factor", "name": "Operating Margin", "weight": 60, "factorId": "operating_margin_ttm"},
                    {"id": "f-gpmgn", "type": "factor", "name": "Gross Margin", "weight": 40, "factorId": "gross_margin_ttm"},
                 ]},
                {"id": "sub-q-roc", "type": "composite", "name": "Return on Capital", "weight": 35,
                 "children": [
                    {"id": "f-roe", "type": "factor", "name": "ROE", "weight": 35, "factorId": "roe_ttm"},
                    {"id": "f-roa", "type": "factor", "name": "ROA", "weight": 30, "factorId": "roa_ttm"},
                    {"id": "f-gpa", "type": "factor", "name": "Gross Profit/Assets", "weight": 35, "factorId": "gross_profit_assets"},
                 ]},
                {"id": "sub-q-turn", "type": "composite", "name": "Turnover", "weight": 15,
                 "children": [
                    {"id": "f-at", "type": "factor", "name": "Asset Turnover", "weight": 100, "factorId": "asset_turnover_ttm"},
                 ]},
                {"id": "sub-q-fin", "type": "composite", "name": "Finances", "weight": 25,
                 "children": [
                    {"id": "f-de", "type": "factor", "name": "Debt/Equity", "weight": 50, "factorId": "debt_to_equity"},
                    {"id": "f-ic", "type": "factor", "name": "Interest Coverage", "weight": 50, "factorId": "interest_coverage_ttm"},
                 ]},
            ],
        },
        # === Core: Growth — 15% ===
        {
            "id": "cat-growth", "type": "category", "name": "Growth", "weight": 15,
            "children": [
                {"id": "sub-g-sales", "type": "composite", "name": "Sales Growth", "weight": 35,
                 "children": [
                    {"id": "f-sg", "type": "factor", "name": "Sales Growth YoY", "weight": 100, "factorId": "sales_growth_yoy"},
                 ]},
                {"id": "sub-g-opinc", "type": "composite", "name": "Op Income Growth", "weight": 30,
                 "children": [
                    {"id": "f-oig", "type": "factor", "name": "Op Income Growth YoY", "weight": 100, "factorId": "op_income_growth_yoy"},
                 ]},
                {"id": "sub-g-eps", "type": "composite", "name": "EPS Growth", "weight": 35,
                 "children": [
                    {"id": "f-epsg", "type": "factor", "name": "EPS Growth YoY", "weight": 50, "factorId": "eps_growth_yoy"},
                    {"id": "f-nig", "type": "factor", "name": "Net Income Growth YoY", "weight": 50, "factorId": "net_income_growth_yoy"},
                 ]},
            ],
        },
        # === Core: Momentum — 10% ===
        {
            "id": "cat-momentum", "type": "category", "name": "Momentum", "weight": 10,
            "children": [
                {"id": "sub-m-price", "type": "composite", "name": "Price Changes", "weight": 35,
                 "children": [
                    {"id": "f-pc120", "type": "factor", "name": "120d Return", "weight": 50, "factorId": "price_change_120d"},
                    {"id": "f-pc180", "type": "factor", "name": "180d Return", "weight": 50, "factorId": "price_change_180d"},
                 ]},
                {"id": "sub-m-tech", "type": "composite", "name": "Technical", "weight": 35,
                 "children": [
                    {"id": "f-udr20", "type": "factor", "name": "UpDown 20d", "weight": 20, "factorId": "up_down_ratio_20"},
                    {"id": "f-udr60", "type": "factor", "name": "UpDown 60d", "weight": 30, "factorId": "up_down_ratio_60"},
                    {"id": "f-udr120", "type": "factor", "name": "UpDown 120d", "weight": 25, "factorId": "up_down_ratio_120"},
                    {"id": "f-rsi200", "type": "factor", "name": "RSI 200", "weight": 25, "factorId": "rsi_200"},
                 ]},
                {"id": "sub-m-qtr", "type": "composite", "name": "Quarterly Returns", "weight": 30,
                 "children": [
                    {"id": "f-m3", "type": "factor", "name": "3M Return", "weight": 30, "factorId": "momentum_3m"},
                    {"id": "f-m6", "type": "factor", "name": "6M Return", "weight": 35, "factorId": "momentum_6m"},
                    {"id": "f-m121", "type": "factor", "name": "12-1M Momentum", "weight": 35, "factorId": "momentum_12_1"},
                 ]},
            ],
        },
        # === Core: Low Volatility — 10% ===
        {
            "id": "cat-risk", "type": "category", "name": "Low Volatility", "weight": 10,
            "children": [
                {"id": "f-vol252", "type": "factor", "name": "252d Volatility", "weight": 40, "factorId": "volatility_252d"},
                {"id": "f-vol60", "type": "factor", "name": "60d Volatility", "weight": 30, "factorId": "volatility_60d"},
                {"id": "f-mdd", "type": "factor", "name": "Max Drawdown 252d", "weight": 30, "factorId": "max_drawdown_252d"},
            ],
        },
        # === Core: Sentiment — 10% (mostly unavailable) ===
        {
            "id": "cat-sentiment", "type": "category", "name": "Sentiment", "weight": 10,
            "children": [
                {"id": "f-si", "type": "factor", "name": "Short Interest", "weight": 100, "factorId": "short_interest_pct"},
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# Tree aggregation
# ---------------------------------------------------------------------------

def count_factors_in_node(node):
    """Count total factors in a subtree."""
    if node["type"] == "factor":
        return 1
    children = node.get("children", [])
    return sum(count_factors_in_node(child) for child in children)


def count_available_factors_in_node(node, factor_ranks):
    """Count factors in a subtree that have data (non-None percentile)."""
    if node["type"] == "factor":
        fid = node.get("factorId")
        return 1 if factor_ranks.get(fid) is not None else 0
    children = node.get("children", [])
    return sum(count_available_factors_in_node(child, factor_ranks) for child in children)


def compute_node_score(node, factor_ranks):
    """Recursively compute weighted score for a tree node.

    Skips factors with missing data (None percentile) and reweights
    available factors proportionally.
    """
    if node["type"] == "factor":
        fid = node.get("factorId")
        return factor_ranks.get(fid)  # percentile rank or None

    children = node.get("children", [])
    if not children:
        return None

    total_weight = 0
    weighted_sum = 0
    for child in children:
        score = compute_node_score(child, factor_ranks)
        if score is not None:
            weighted_sum += score * child["weight"]
            total_weight += child["weight"]

    if total_weight == 0:
        return None
    return weighted_sum / total_weight


def collect_category_scores(tree, factor_ranks):
    """Get scores and coverage for each category (direct children of root)."""
    result = {}
    for child in tree.get("children", []):
        score = compute_node_score(child, factor_ranks)
        if score is not None:
            total = count_factors_in_node(child)
            avail = count_available_factors_in_node(child, factor_ranks)
            result[child["name"]] = {
                "score": round(score, 2),
                "coverage": "{0}/{1}".format(avail, total),
            }
    return result


def collect_factor_details(tree, factor_ranks, max_depth=5):
    """Collect factor-level details for display (up to max_depth)."""
    def recurse(node, depth=0):
        if depth > max_depth:
            return []

        if node["type"] == "factor":
            fid = node.get("factorId")
            percentile = factor_ranks.get(fid)
            if percentile is not None:
                return [{
                    "name": node.get("name"),
                    "factorId": fid,
                    "percentile": round(percentile, 2),
                }]
            return []

        results = []
        for child in node.get("children", []):
            results.extend(recurse(child, depth + 1))
        return results

    return recurse(tree)


# ---------------------------------------------------------------------------
# Ensure ranking systems exist
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS = {
    "winsorize": True,
    "winsorizeLevel": 5,
    "sectorNeutral": False,
    "higherIsBetter": True,
}


def ensure_ranking_systems(conn):
    """Create default ranking systems in the DB if they don't exist."""
    cur = conn.cursor()

    # Ensure legacy 'default' system exists
    cur.execute("SELECT id FROM ranking_systems WHERE id = 'default'")
    if not cur.fetchone():
        print("  Seeding legacy 'default' ranking system...")
        cur.execute("""
            INSERT INTO ranking_systems (id, name, description, tree, options)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            "default",
            "Default Composite (Legacy)",
            "Legacy multi-factor ranking: Value 30%, Quality 25%, Growth 20%, Momentum 25%",
            PgJson(DEFAULT_TREE),
            PgJson(DEFAULT_OPTIONS),
        ))
        conn.commit()

    # Ensure new 'p123-inspired' system exists
    cur.execute("SELECT id FROM ranking_systems WHERE id = 'p123-inspired'")
    if not cur.fetchone():
        print("  Seeding 'p123-inspired' ranking system...")
        cur.execute("""
            INSERT INTO ranking_systems (id, name, description, tree, options)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            "p123-inspired",
            "P123 Inspired Korea Multi-Factor",
            "P123-inspired multi-factor: Value 25%, Quality 30%, Growth 15%, Momentum 10%, Low Volatility 10%, Sentiment 10%",
            PgJson(P123_TREE),
            PgJson(DEFAULT_OPTIONS),
        ))
        conn.commit()

    cur.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run ranking and store snapshot")
    parser.add_argument("--as-of-date", required=True, help="Ranking date YYYY-MM-DD")
    parser.add_argument("--system-id", default="p123-inspired", help="Ranking system ID (default: 'p123-inspired')")
    parser.add_argument("--limit", type=int, help="Max stocks to rank")
    args = parser.parse_args()

    as_of = args.as_of_date
    conn = psycopg2.connect(DATABASE_URL)

    try:
        cur = conn.cursor()

        # 1. Ensure both ranking systems exist in DB
        ensure_ranking_systems(conn)

        # 2. Load the ranking system tree
        system_id = args.system_id
        tree = P123_TREE if system_id == "p123-inspired" else DEFAULT_TREE

        if system_id not in ("default", "p123-inspired"):
            cur.execute("SELECT tree FROM ranking_systems WHERE id = %s", (system_id,))
            row = cur.fetchone()
            if row:
                tree = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            else:
                print("Warning: system '{0}' not found, using p123-inspired".format(system_id))
                system_id = "p123-inspired"
                tree = P123_TREE

        # 3. Load factor snapshots for this date
        cur.execute("""
            SELECT ticker, factor_id, percentile_rank
            FROM factor_snapshots WHERE date = %s
        """, (as_of,))
        rows = cur.fetchall()

        if not rows:
            print("No factor snapshots found for {0}. Run calculate_factors.py first.".format(as_of))
            sys.exit(1)

        # Build ticker → {factorId → percentile}
        ticker_factors = {}
        for ticker, fid, pct in rows:
            if ticker not in ticker_factors:
                ticker_factors[ticker] = {}
            ticker_factors[ticker][fid] = pct

        tickers = sorted(ticker_factors.keys())
        if args.limit:
            tickers = tickers[:args.limit]

        print("Ranking {0} stocks using system '{1}' as of {2}...".format(len(tickers), system_id, as_of))

        # 4. Compute composite scores
        rankings = []
        for ticker in tickers:
            fr = ticker_factors[ticker]
            composite = compute_node_score(tree, fr)
            if composite is None:
                continue
            cat_scores = collect_category_scores(tree, fr)
            rankings.append({
                "ticker": ticker,
                "composite_score": round(composite, 2),
                "category_scores": cat_scores,
                "factor_count": len(fr),
            })

        # 5. Sort and assign ranks
        rankings.sort(key=lambda x: x["composite_score"], reverse=True)
        for i, r in enumerate(rankings):
            r["rank"] = i + 1

        print("  Ranked {0} stocks".format(len(rankings)))

        # 6. Print top 5 with factor-level detail
        print("\n  Top 5 (with factor coverage):")
        print("  {0}  {1:<8}  {2:>9}  {3}".format("Rank", "Ticker", "Composite", "Categories"))
        print("  {0}  {1:<8}  {2:>9}  {3}".format("-"*4, "-"*8, "-"*9, "-"*50))
        for r in rankings[:5]:
            cat_str = "  ".join("{0}={1[score]:.1f} ({1[coverage]})".format(k, v) for k, v in r["category_scores"].items())
            print("  {0:4d}  {1:<8}  {2:9.2f}  {3}".format(r["rank"], r["ticker"], r["composite_score"], cat_str))

        # 7. Print top 10 basic ranks
        print("\n  Top 10:")
        print("  {0}  {1:<8}  {2:>9}".format("Rank", "Ticker", "Composite"))
        print("  {0}  {1:<8}  {2:>9}".format("-"*4, "-"*8, "-"*9))
        for r in rankings[:10]:
            print("  {0:4d}  {1:<8}  {2:9.2f}".format(r["rank"], r["ticker"], r["composite_score"]))

        # 8. Store snapshot
        cur.execute("""
            INSERT INTO ranking_snapshots (ranking_system_id, date, results, universe_size)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (system_id, as_of, PgJson(rankings), len(rankings)))
        snapshot_id = cur.fetchone()[0]
        conn.commit()
        cur.close()

        print("\n  Snapshot saved (id={0})".format(snapshot_id))

    except Exception as e:
        print("ERROR: {0}".format(e))
        raise
    finally:
        conn.close()

    print("Done!")
