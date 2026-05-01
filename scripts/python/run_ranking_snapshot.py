"""
Run a ranking system against factor_snapshots and store the result in ranking_snapshots.

This script:
1. Loads factor_snapshots for the given date
2. Loads a ranking system (by ID or the default)
3. Applies the weighted tree aggregation
4. Stores the result as a ranking_snapshot in the DB

Usage:
    python run_ranking_snapshot.py --as-of-date 2024-12-31
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
# Default ranking system tree (matches DEFAULT_RANKING_SYSTEM in TypeScript)
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
# Tree aggregation
# ---------------------------------------------------------------------------

def compute_node_score(node, factor_ranks):
    """Recursively compute weighted score for a tree node."""
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
    """Get scores for each category (direct children of root)."""
    result = {}
    for child in tree.get("children", []):
        score = compute_node_score(child, factor_ranks)
        if score is not None:
            result[child["name"]] = round(score, 2)
    return result


# ---------------------------------------------------------------------------
# Ensure default ranking system exists
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS = {
    "winsorize": True,
    "winsorizeLevel": 5,
    "sectorNeutral": False,
    "higherIsBetter": True,
}


def ensure_default_ranking_system(conn):
    """Create the default ranking system in the DB if it doesn't exist."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM ranking_systems WHERE id = 'default'")
    if cur.fetchone():
        cur.close()
        return  # already exists

    print("  Seeding default ranking system into ranking_systems table...")
    cur.execute("""
        INSERT INTO ranking_systems (id, name, description, tree, options)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """, (
        "default",
        "Default Composite",
        "Default multi-factor ranking: Value 30%, Quality 25%, Growth 20%, Momentum 25%",
        PgJson(DEFAULT_TREE),
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
    parser.add_argument("--system-id", default="default", help="Ranking system ID (default: 'default')")
    parser.add_argument("--limit", type=int, help="Max stocks to rank")
    args = parser.parse_args()

    as_of = args.as_of_date
    conn = psycopg2.connect(DATABASE_URL)

    try:
        cur = conn.cursor()

        # 1. Load the ranking system tree
        tree = DEFAULT_TREE
        system_id = args.system_id

        # Ensure the default system exists in the DB (needed for FK on ranking_snapshots)
        ensure_default_ranking_system(conn)

        if system_id != "default":
            cur.execute("SELECT tree FROM ranking_systems WHERE id = %s", (system_id,))
            row = cur.fetchone()
            if row:
                tree = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            else:
                print(f"Warning: system '{system_id}' not found, using default")
                system_id = "default"

        # 2. Load factor snapshots for this date
        cur.execute("""
            SELECT ticker, factor_id, percentile_rank
            FROM factor_snapshots WHERE date = %s
        """, (as_of,))
        rows = cur.fetchall()

        if not rows:
            print(f"No factor snapshots found for {as_of}. Run calculate_factors.py first.")
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

        print(f"Ranking {len(tickers)} stocks using system '{system_id}' as of {as_of}...")

        # 3. Compute composite scores
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

        # 4. Sort and assign ranks
        rankings.sort(key=lambda x: x["composite_score"], reverse=True)
        for i, r in enumerate(rankings):
            r["rank"] = i + 1

        print(f"  Ranked {len(rankings)} stocks")

        # 5. Print top 10
        print("\n  Top 10:")
        print(f"  {'Rank':>4}  {'Ticker':<8}  {'Composite':>9}  Categories")
        print(f"  {'-'*4}  {'-'*8}  {'-'*9}  {'-'*40}")
        for r in rankings[:10]:
            cats = "  ".join(f"{k}={v:.1f}" for k, v in r["category_scores"].items())
            print(f"  {r['rank']:4d}  {r['ticker']:<8}  {r['composite_score']:9.2f}  {cats}")

        # 6. Store snapshot
        cur.execute("""
            INSERT INTO ranking_snapshots (ranking_system_id, date, results, universe_size)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (system_id, as_of, PgJson(rankings), len(rankings)))
        snapshot_id = cur.fetchone()[0]
        conn.commit()
        cur.close()

        print(f"\n  Snapshot saved (id={snapshot_id})")

    except Exception as e:
        print(f"ERROR: {e}")
        raise
    finally:
        conn.close()

    print("Done!")
