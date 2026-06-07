"""
Run a ranking system against factor_snapshots and store the result in ranking_snapshots.

This script:
1. Loads factor_snapshots for the given date (factor scores are PERCENTILE RANKS, not z-scores)
2. Loads a ranking system tree
3. Determines which categories are globally available across the universe
4. Applies the configured missing-category policy:
     - neutral      -> stock-level missing categories scored 50 (universe-neutral)
     - exclude      -> stock-level missing categories cause stock to fail coverage
     - renormalize  -> legacy: skip and renormalize over available data (dangerous)
5. Applies minimum-coverage thresholds (active weight, category count, factor count)
6. Splits stocks into:
     - main ranking (passed minimum coverage)
     - insufficient-coverage ranking (failed)
7. Stores everything in ranking_snapshots

Usage:
    # P123-style defaults: neutral imputation, 60% active weight, 3 cats, 10 factors
    python run_ranking_snapshot.py --as-of-date 2024-12-30

    # Tighten to require all categories
    python run_ranking_snapshot.py --as-of-date 2024-12-30 \
        --missing-category-policy exclude \
        --min-active-weight-coverage 0.90

    # Loose / legacy behavior (NOT recommended, kept for compatibility)
    python run_ranking_snapshot.py --as-of-date 2024-12-30 \
        --missing-category-policy renormalize \
        --min-active-weight-coverage 0.0 \
        --min-category-count 1 \
        --min-factor-count 1

    # Include insufficient-coverage stocks in the main snapshot for inspection
    python run_ranking_snapshot.py --as-of-date 2024-12-30 --include-insufficient-coverage
"""

import os
import sys
import json
import argparse
from datetime import datetime, date as date_cls

import psycopg2
from psycopg2.extras import Json as PgJson
from dotenv import load_dotenv


def _check_marcap_pit_safety(conn, as_of_date, ticker_list):
    """Check whether market cap for the as-of-date is point-in-time safe.

    Prints a clear warning if the as-of-date is historical (before today)
    but market_cap data comes from a snapshot source rather than
    'marcap_historical'. Does NOT block ranking — just warns loudly.
    """
    if not ticker_list:
        return
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
        SELECT dp.source, COUNT(*) AS stocks
        FROM latest l
        JOIN daily_prices dp
          ON dp.ticker = l.ticker AND dp.date = l.d
        GROUP BY dp.source
    """, (ticker_list, as_of_date))
    rows = cur.fetchall()
    cur.close()

    pit_count = sum(n for src, n in rows if src == "marcap_historical")
    snap_count = sum(n for src, n in rows
                     if src and ("snapshot" in src.lower()
                                 or src.startswith("fdr+marcap")))
    today_iso = datetime.now().strftime("%Y-%m-%d")
    is_historical = as_of_date < today_iso

    if is_historical and snap_count > 0 and pit_count < len(ticker_list):
        print("", flush=True)
        print("  " + "!" * 60, flush=True)
        print("  WARNING: as-of-date {0} is historical, but {1} of {2} "
              "stocks have market_cap from a CURRENT SNAPSHOT, NOT "
              "point-in-time.".format(as_of_date, snap_count, len(ticker_list)),
              flush=True)
        print("  Value factors (P/E, P/B, EV/EBITDA, dividend yield, "
              "FCF yield, etc.) will be biased.", flush=True)
        print("  Fix: python ingest_marcap.py --source historical "
              "--as-of-date {0} --universe <name>".format(as_of_date),
              flush=True)
        print("  " + "!" * 60, flush=True)
        print("", flush=True)

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "run_ranking_snapshot"

# Scoring method recorded with each snapshot. Factor scores are
# percentile ranks (0-100), not z-scores. See calculate_factors.py.
SCORING_METHOD = "percentile_rank"

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
# P123-inspired ranking system tree (default)
# ---------------------------------------------------------------------------

P123_TREE = {
    "id": "root",
    "type": "composite",
    "name": "P123 Inspired Korea Multi-Factor",
    "weight": 100,
    "children": [
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
                # Asset-Based renormalized from 60/40 -> 50/25/25 when
                # Buyback Yield was added as a third shareholder-return
                # signal. Book/Market remains the dominant factor in
                # this sub-composite. Dividend Yield is still defined
                # but currently empty (no ingest source); the composite
                # will route weight to whichever sub-factors are present.
                {"id": "sub-val-asset", "type": "composite", "name": "Asset-Based", "weight": 15,
                 "children": [
                    {"id": "f-pb", "type": "factor", "name": "Book/Market", "weight": 50, "factorId": "price_book"},
                    {"id": "f-divy", "type": "factor", "name": "Dividend Yield", "weight": 25, "factorId": "dividend_yield"},
                    {"id": "f-bby", "type": "factor", "name": "Buyback Yield", "weight": 25, "factorId": "buyback_yield_yoy"},
                 ]},
            ],
        },
        {
            "id": "cat-quality", "type": "category", "name": "Quality", "weight": 30,
            "children": [
                {"id": "sub-q-margin", "type": "composite", "name": "Margins", "weight": 25,
                 "children": [
                    {"id": "f-opmgn", "type": "factor", "name": "Operating Margin", "weight": 60, "factorId": "operating_margin_ttm"},
                    {"id": "f-gpmgn", "type": "factor", "name": "Gross Margin", "weight": 40, "factorId": "gross_margin_ttm"},
                 ]},
                {"id": "sub-q-roc", "type": "composite", "name": "Return on Capital", "weight": 30,
                 "children": [
                    {"id": "f-roe", "type": "factor", "name": "ROE", "weight": 28, "factorId": "roe_ttm"},
                    {"id": "f-roa", "type": "factor", "name": "ROA", "weight": 24, "factorId": "roa_ttm"},
                    {"id": "f-gpa", "type": "factor", "name": "Gross Profit/Assets", "weight": 24, "factorId": "gross_profit_assets"},
                    {"id": "f-fcfa", "type": "factor", "name": "FCF/Assets", "weight": 24, "factorId": "fcf_to_assets"},
                 ]},
                {"id": "sub-q-bs", "type": "composite", "name": "Balance Sheet Strength", "weight": 10,
                 "children": [
                    {"id": "f-cta", "type": "factor", "name": "Cash/Assets", "weight": 100, "factorId": "cash_to_assets"},
                 ]},
                {"id": "sub-q-turn", "type": "composite", "name": "Turnover", "weight": 10,
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
        {
            "id": "cat-growth", "type": "category", "name": "Growth", "weight": 15,
            "children": [
                # Sub-weights total 100. Cash Flow Growth was added at
                # weight 20; the existing three were renormalized
                # 35/30/35 -> 30/25/25. The category's overall 15% in
                # the composite is unchanged.
                {"id": "sub-g-sales", "type": "composite", "name": "Sales Growth", "weight": 30,
                 "children": [
                    {"id": "f-sg", "type": "factor", "name": "Sales Growth YoY", "weight": 100, "factorId": "sales_growth_yoy"},
                 ]},
                {"id": "sub-g-opinc", "type": "composite", "name": "Op Income Growth", "weight": 25,
                 "children": [
                    {"id": "f-oig", "type": "factor", "name": "Op Income Growth YoY", "weight": 100, "factorId": "op_income_growth_yoy"},
                 ]},
                {"id": "sub-g-eps", "type": "composite", "name": "EPS Growth", "weight": 25,
                 "children": [
                    {"id": "f-epsg", "type": "factor", "name": "EPS Growth YoY", "weight": 50, "factorId": "eps_growth_yoy"},
                    {"id": "f-nig", "type": "factor", "name": "Net Income Growth YoY", "weight": 50, "factorId": "net_income_growth_yoy"},
                 ]},
                {"id": "sub-g-cf", "type": "composite", "name": "Cash Flow Growth", "weight": 20,
                 "children": [
                    {"id": "f-ocfg", "type": "factor", "name": "OCF Growth YoY", "weight": 50, "factorId": "ocf_growth_yoy"},
                    {"id": "f-fcfg", "type": "factor", "name": "FCF Growth YoY", "weight": 50, "factorId": "fcf_growth_yoy"},
                 ]},
            ],
        },
        {
            "id": "cat-momentum", "type": "category", "name": "Momentum", "weight": 10,
            "children": [
                # Sub-weights total to 100 and were renormalized when Industry
                # Momentum was added (previously 35/35/30; now 30/30/25/15).
                # The category's overall 10% in the composite is unchanged.
                {"id": "sub-m-price", "type": "composite", "name": "Price Changes", "weight": 30,
                 "children": [
                    {"id": "f-pc120", "type": "factor", "name": "120d Return", "weight": 50, "factorId": "price_change_120d"},
                    {"id": "f-pc180", "type": "factor", "name": "180d Return", "weight": 50, "factorId": "price_change_180d"},
                 ]},
                {"id": "sub-m-tech", "type": "composite", "name": "Technical", "weight": 30,
                 "children": [
                    {"id": "f-udr20", "type": "factor", "name": "UpDown 20d", "weight": 20, "factorId": "up_down_ratio_20"},
                    {"id": "f-udr60", "type": "factor", "name": "UpDown 60d", "weight": 30, "factorId": "up_down_ratio_60"},
                    {"id": "f-udr120", "type": "factor", "name": "UpDown 120d", "weight": 25, "factorId": "up_down_ratio_120"},
                    {"id": "f-rsi200", "type": "factor", "name": "RSI 200", "weight": 25, "factorId": "rsi_200"},
                 ]},
                {"id": "sub-m-qtr", "type": "composite", "name": "Quarterly Returns", "weight": 25,
                 "children": [
                    {"id": "f-m3", "type": "factor", "name": "3M Return", "weight": 30, "factorId": "momentum_3m"},
                    {"id": "f-m6", "type": "factor", "name": "6M Return", "weight": 35, "factorId": "momentum_6m"},
                    {"id": "f-m121", "type": "factor", "name": "12-1M Momentum", "weight": 35, "factorId": "momentum_12_1"},
                 ]},
                {"id": "sub-m-industry", "type": "composite", "name": "Industry Momentum", "weight": 15,
                 "children": [
                    {"id": "f-im26", "type": "factor", "name": "Industry 26W Momentum", "weight": 50, "factorId": "industry_momentum_26w"},
                    {"id": "f-im52", "type": "factor", "name": "Industry 52W Momentum", "weight": 50, "factorId": "industry_momentum_52w"},
                 ]},
            ],
        },
        {
            "id": "cat-risk", "type": "category", "name": "Low Volatility", "weight": 10,
            "children": [
                {"id": "f-vol252", "type": "factor", "name": "252d Volatility", "weight": 40, "factorId": "volatility_252d"},
                {"id": "f-vol60", "type": "factor", "name": "60d Volatility", "weight": 30, "factorId": "volatility_60d"},
                {"id": "f-mdd", "type": "factor", "name": "Max Drawdown 252d", "weight": 30, "factorId": "max_drawdown_252d"},
            ],
        },
        {
            "id": "cat-sentiment", "type": "category", "name": "Sentiment", "weight": 10,
            "children": [
                # Short interest still unavailable (KRX restricts the data
                # API), so the category currently relies entirely on
                # insider net buying from DART filings. Keep the
                # short_interest_pct slot for when the data becomes
                # available in the future.
                {"id": "f-insider", "type": "factor", "name": "Insider Net Buying 90d", "weight": 70, "factorId": "insider_net_buying_90d"},
                {"id": "f-si",      "type": "factor", "name": "Short Interest",        "weight": 30, "factorId": "short_interest_pct"},
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# Tree helpers
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
    """Recursively compute a weighted score for a tree node.

    WITHIN a category subtree, missing factors are skipped and the
    remaining factors are reweighted proportionally. This is sensible
    at the within-category level because the user's category "intent"
    is to measure that dimension with whatever data they have.

    The CROSS-CATEGORY composition is handled separately in
    compute_stock_ranking_with_policy() and is governed by the
    --missing-category-policy flag, NOT this function.

    Returns: percentile rank (0-100) or None if no data in subtree.
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


# ---------------------------------------------------------------------------
# Globally-available categories
# ---------------------------------------------------------------------------

def determine_globally_active_categories(tree, all_ticker_factors):
    """For each top-level category in the tree, check whether ANY stock in the
    universe has data in that category.

    Categories with zero data anywhere in the universe are "globally
    unavailable" and their weight is removed from the composite total
    (they do not contribute and do not penalize stocks).

    Returns: dict {category_name: True/False}
    """
    result = {}
    for cat_node in tree.get("children", []):
        cat_has_data = False
        for ticker, ranks in all_ticker_factors.items():
            if count_available_factors_in_node(cat_node, ranks) > 0:
                cat_has_data = True
                break
        result[cat_node["name"]] = cat_has_data
    return result


# ---------------------------------------------------------------------------
# Stock-level ranking with policy
# ---------------------------------------------------------------------------

NEUTRAL_SCORE = 50.0


def compute_stock_ranking_with_policy(
    tree,
    factor_ranks,
    globally_active,
    policy,
    thresholds,
):
    """Compute composite + coverage info for a single stock.

    Args:
        tree: ranking tree (root node)
        factor_ranks: {factor_id: percentile} for this stock
        globally_active: {category_name: bool}
        policy: "neutral" | "exclude" | "renormalize"
        thresholds: dict with min_active_weight_coverage, min_category_count, min_factor_count

    Returns dict with:
        composite_score: float | None
        category_scores: {name: {score, weight, coverage, status}}
            status: "available" | "missing_imputed" | "missing" | "globally_unavailable"
        active_categories: list of category names with real data
        imputed_categories: list of category names that were neutral-imputed
        active_category_count: int
        factor_count: int
        active_weight_coverage: float in [0, 1]   (real data weight / globally-active weight)
        composite_weight_used: float in [0, 1]    (real + imputed weight / globally-active weight)
        passes_minimum: bool
        failure_reasons: list of str
    """
    cat_results = {}
    active_categories = []
    imputed_categories = []
    factor_count = 0

    universe_active_total = sum(
        c["weight"] for c in tree.get("children", [])
        if globally_active.get(c["name"], False)
    )

    real_weight_sum = 0.0       # categories with real data
    composite_weight_sum = 0.0  # real + imputed (whatever counts toward composite)
    weighted_score_sum = 0.0    # sum of category_score * category_weight for the categories that count

    for cat_node in tree.get("children", []):
        cat_name = cat_node["name"]
        cat_weight = cat_node.get("weight", 0)
        cat_total = count_factors_in_node(cat_node)
        cat_avail = count_available_factors_in_node(cat_node, factor_ranks)
        factor_count += cat_avail
        cat_score = compute_node_score(cat_node, factor_ranks)

        if not globally_active.get(cat_name, False):
            # Globally unavailable - never contribute
            cat_results[cat_name] = {
                "score": None,
                "weight": cat_weight,
                "coverage": "{0}/{1}".format(cat_avail, cat_total),
                "status": "globally_unavailable",
            }
            continue

        if cat_score is not None:
            # Real data for this stock+category
            cat_results[cat_name] = {
                "score": round(cat_score, 2),
                "weight": cat_weight,
                "coverage": "{0}/{1}".format(cat_avail, cat_total),
                "status": "available",
            }
            active_categories.append(cat_name)
            real_weight_sum += cat_weight
            composite_weight_sum += cat_weight
            weighted_score_sum += cat_score * cat_weight
        else:
            # Stock-level missing data in a globally-available category
            if policy == "neutral":
                cat_results[cat_name] = {
                    "score": NEUTRAL_SCORE,
                    "weight": cat_weight,
                    "coverage": "{0}/{1}".format(cat_avail, cat_total),
                    "status": "missing_imputed",
                }
                imputed_categories.append(cat_name)
                composite_weight_sum += cat_weight
                weighted_score_sum += NEUTRAL_SCORE * cat_weight
            elif policy == "exclude":
                cat_results[cat_name] = {
                    "score": None,
                    "weight": cat_weight,
                    "coverage": "{0}/{1}".format(cat_avail, cat_total),
                    "status": "missing",
                }
                # Does not contribute. Stock will fail coverage.
            else:  # renormalize (legacy)
                cat_results[cat_name] = {
                    "score": None,
                    "weight": cat_weight,
                    "coverage": "{0}/{1}".format(cat_avail, cat_total),
                    "status": "missing_renormalized",
                }
                # Does not contribute; remaining categories are renormalized below.

    # Compute composite based on policy
    composite = None
    if policy == "renormalize":
        if real_weight_sum > 0:
            real_weighted = sum(
                cat_results[cat_node["name"]]["score"] * cat_node["weight"]
                for cat_node in tree.get("children", [])
                if cat_results[cat_node["name"]]["status"] == "available"
            )
            composite = real_weighted / real_weight_sum
    elif policy == "exclude":
        # All globally-active categories must have real data
        all_active_have_data = all(
            cat_results[c["name"]]["status"] == "available"
            for c in tree.get("children", [])
            if globally_active.get(c["name"], False)
        )
        if all_active_have_data and real_weight_sum > 0:
            composite = weighted_score_sum / real_weight_sum
    else:  # neutral
        if composite_weight_sum > 0:
            composite = weighted_score_sum / composite_weight_sum

    # Coverage metrics
    if universe_active_total > 0:
        active_weight_coverage = real_weight_sum / universe_active_total
        composite_weight_used = composite_weight_sum / universe_active_total
    else:
        active_weight_coverage = 0.0
        composite_weight_used = 0.0

    # Minimum-coverage checks (always evaluated against REAL data,
    # not imputed; imputed data does not count toward "having coverage")
    failure_reasons = []
    if active_weight_coverage < thresholds["min_active_weight_coverage"]:
        failure_reasons.append(
            "active_weight_coverage {0:.2f} < min {1:.2f}".format(
                active_weight_coverage, thresholds["min_active_weight_coverage"]
            )
        )
    if len(active_categories) < thresholds["min_category_count"]:
        failure_reasons.append(
            "active_categories {0} < min {1}".format(
                len(active_categories), thresholds["min_category_count"]
            )
        )
    if factor_count < thresholds["min_factor_count"]:
        failure_reasons.append(
            "factor_count {0} < min {1}".format(
                factor_count, thresholds["min_factor_count"]
            )
        )
    if composite is None:
        failure_reasons.append("no composite computed")

    passes_minimum = (len(failure_reasons) == 0)

    return {
        "composite_score": round(composite, 2) if composite is not None else None,
        "category_scores": cat_results,
        "active_categories": active_categories,
        "imputed_categories": imputed_categories,
        "active_category_count": len(active_categories),
        "factor_count": factor_count,
        "active_weight_coverage": round(active_weight_coverage, 4),
        "composite_weight_used": round(composite_weight_used, 4),
        "passes_minimum": passes_minimum,
        "failure_reasons": failure_reasons,
    }


# ---------------------------------------------------------------------------
# Ensure ranking systems exist
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS = {
    "scoringMethod": SCORING_METHOD,
    "winsorize": True,
    "winsorizeLevel": 5,
    "sectorNeutral": False,
    "higherIsBetter": True,
}


def ensure_ranking_systems(conn):
    """Create default ranking systems in the DB if they don't exist."""
    cur = conn.cursor()

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

    # Upsert (not just insert-if-absent) so the DB metadata row stays in
    # sync as P123_TREE evolves. Without this, edits to the tree above
    # (e.g. adding Industry Momentum as a Momentum sub-composite) would
    # not propagate to ranking_systems.tree and audit queries would show
    # a stale model definition.
    print("  Seeding/refreshing 'p123-inspired' ranking system...")
    cur.execute("""
        INSERT INTO ranking_systems (id, name, description, tree, options)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            description = EXCLUDED.description,
            tree = EXCLUDED.tree,
            options = EXCLUDED.options
    """, (
        "p123-inspired",
        "P123 Inspired Korea Multi-Factor",
        "P123-inspired multi-factor: Value 25%, Quality 30%, "
        "Growth 15% (Sales 30 / OpInc 25 / EPS 25 / CashFlow 20), "
        "Momentum 10% (Price 30 / Technical 30 / Quarterly 25 / Industry 15), "
        "Low Volatility 10%, Sentiment 10%",
        PgJson(P123_TREE),
        PgJson(DEFAULT_OPTIONS),
    ))
    conn.commit()

    cur.close()


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _format_imputed_marker(cat_data):
    if cat_data["status"] == "missing_imputed":
        return "*"
    return ""


def print_main_ranking(rankings, top_n=10):
    """Print the main ranking with full coverage info."""
    if not rankings:
        return
    print("\n  Top {0} (main ranking, passed coverage requirements):".format(top_n))
    print("  Rank  Ticker    Composite  Coverage  Cats    Factors  Status  Categories")
    print("  ----  --------  ---------  --------  ------  -------  ------  " + "-"*60)
    for r in rankings[:top_n]:
        cat_parts = []
        for k, v in r["category_scores"].items():
            marker = _format_imputed_marker(v)
            if v["status"] == "available":
                cat_parts.append("{0}={1:.1f}{2} ({3})".format(k, v["score"], marker, v["coverage"]))
            elif v["status"] == "missing_imputed":
                cat_parts.append("{0}=50.0* (imputed)".format(k))
            elif v["status"] == "globally_unavailable":
                cat_parts.append("{0}=N/A [global]".format(k))
            else:
                cat_parts.append("{0}=N/A".format(k))
        cat_str = "  ".join(cat_parts)
        print("  {0:4d}  {1:<8}  {2:9.2f}  {3:7.0%}   {4}/{5}     {6:<7}  {7}    {8}".format(
            r["rank"],
            r["ticker"],
            r["composite_score"],
            r["active_weight_coverage"],
            r["active_category_count"],
            r["total_globally_active_categories"],
            r["factor_count"],
            "PASS",
            cat_str,
        ))
    print("\n  * = neutral-imputed (50) for missing category")


def print_excluded_ranking(excluded, max_n=20):
    """Print stocks that failed minimum coverage."""
    if not excluded:
        return
    print("\n  Excluded for insufficient coverage ({0} stocks):".format(len(excluded)))
    print("  Ticker    Active Cats   ActiveWt   Factors   Reason")
    print("  --------  ------------  ---------  --------  " + "-"*40)
    for r in excluded[:max_n]:
        cat_list = ",".join(r["active_categories"]) if r["active_categories"] else "(none)"
        # Truncate cat list
        if len(cat_list) > 30:
            cat_list = cat_list[:27] + "..."
        reason_short = "; ".join(r["failure_reasons"])
        if len(reason_short) > 50:
            reason_short = reason_short[:47] + "..."
        print("  {0:<8}  {1:<12}  {2:7.0%}    {3:<8}  {4}".format(
            r["ticker"],
            cat_list,
            r["active_weight_coverage"],
            r["factor_count"],
            reason_short,
        ))
    if len(excluded) > max_n:
        print("  ... and {0} more".format(len(excluded) - max_n))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run ranking and store snapshot")
    parser.add_argument(
        "--as-of-date", required=True, help="Ranking date YYYY-MM-DD")
    parser.add_argument(
        "--system-id", default="p123-inspired",
        help="Ranking system ID (default: 'p123-inspired')")
    parser.add_argument(
        "--universe", help="Use named universe from universe_memberships table")
    parser.add_argument(
        "--limit", type=int, help="Max stocks to rank")

    # Missing-data policy and minimum-coverage rules
    parser.add_argument(
        "--missing-category-policy", default="neutral",
        choices=["neutral", "exclude", "renormalize"],
        help=("How to treat stock-level missing categories. "
              "neutral=score 50 (default, recommended); "
              "exclude=stock fails coverage if any category missing; "
              "renormalize=legacy buggy behavior (NOT recommended)."))
    parser.add_argument(
        "--min-active-weight-coverage", type=float, default=0.60,
        help=("Minimum fraction of globally-active category weight a stock "
              "must have REAL data for, before imputation, to be included "
              "in the main ranking. Default 0.60."))
    parser.add_argument(
        "--min-category-count", type=int, default=3,
        help=("Minimum number of categories with real data a stock must have "
              "to be included in the main ranking. Default 3."))
    parser.add_argument(
        "--min-factor-count", type=int, default=10,
        help=("Minimum number of factor scores a stock must have to be "
              "included in the main ranking. Default 10."))
    parser.add_argument(
        "--include-insufficient-coverage", action="store_true",
        help=("Also include insufficient-coverage stocks in the main snapshot. "
              "Default False -- they're stored in a separate section only."))

    # Point-in-time market cap enforcement
    pit_group = parser.add_mutually_exclusive_group()
    pit_group.add_argument(
        "--require-pit-market-cap", dest="pit_mode",
        action="store_const", const="require",
        help=("Require point-in-time market cap. Stocks whose effective "
              "market_cap source is not 'marcap_historical' are excluded "
              "from the main ranking. Default for historical as-of dates."))
    pit_group.add_argument(
        "--allow-snapshot-market-cap", dest="pit_mode",
        action="store_const", const="allow",
        help=("Allow snapshot market cap. Use only for current-day "
              "validation; produces biased value factors for historical dates."))
    parser.set_defaults(pit_mode=None)  # auto

    args = parser.parse_args()

    as_of = args.as_of_date
    conn = psycopg2.connect(DATABASE_URL)

    thresholds = {
        "min_active_weight_coverage": args.min_active_weight_coverage,
        "min_category_count": args.min_category_count,
        "min_factor_count": args.min_factor_count,
    }
    policy = args.missing_category_policy

    try:
        cur = conn.cursor()

        # 1. Ensure both ranking systems exist in DB
        ensure_ranking_systems(conn)

        # 2. Load the ranking system tree
        system_id = args.system_id
        tree = P123_TREE if system_id == "p123-inspired" else DEFAULT_TREE

        if system_id not in ("default", "p123-inspired"):
            cur.execute(
                "SELECT tree FROM ranking_systems WHERE id = %s",
                (system_id,))
            row = cur.fetchone()
            if row:
                tree = (row[0] if isinstance(row[0], dict)
                        else json.loads(row[0]))
            else:
                print("Warning: system '{}' not found, "
                      "using p123-inspired".format(system_id))
                system_id = "p123-inspired"
                tree = P123_TREE

        # 3. Load factor snapshots for this date, scoped by universe.
        # Percentile ranks depend on the universe used at calculation time;
        # we read ONLY rows tagged with this universe to avoid mixing scores
        # from different universes (e.g. test_50 vs test_200_large).
        if args.universe:
            scope_universe = args.universe
        else:
            scope_universe = "__all_active__"

        cur.execute("""
            SELECT ticker, factor_id, percentile_rank
            FROM factor_snapshots
            WHERE date = %s AND universe_name = %s
        """, (as_of, scope_universe))
        rows = cur.fetchall()

        if not rows:
            print("No factor snapshots found for date={0} universe={1}.".format(as_of, scope_universe))
            print("Run: python calculate_factors.py --universe {0} --as-of-date {1}".format(scope_universe, as_of))
            sys.exit(1)

        print("  Reading factor scores tagged with universe='{0}' ({1} rows)".format(
            scope_universe, len(rows)))

        # Build ticker -> {factorId -> percentile}.
        # IMPORTANT: this dict is keyed only by tickers that have at least
        # one factor row. The CANONICAL ticker list comes from
        # universe_memberships below, NOT from factor_snapshots, so that
        # stocks with zero factor rows still get classified (typically as
        # insufficient coverage or non-PIT, instead of silently dropped).
        ticker_factors = {}
        for ticker, fid, pct in rows:
            if ticker not in ticker_factors:
                ticker_factors[ticker] = {}
            ticker_factors[ticker][fid] = pct

        # Canonical ticker list:
        #   - With --universe: always start from universe_memberships so the
        #     accounting (main + insufficient + non-PIT) sums to the universe
        #     size, even for stocks that have zero factor rows.
        #   - Without --universe: fall back to whatever has factor data (the
        #     legacy unscoped flow).
        if args.universe:
            cur.execute(
                "SELECT ticker FROM universe_memberships "
                "WHERE universe_name = %s ORDER BY ticker",
                (args.universe,))
            universe_tickers = [r[0] for r in cur.fetchall()]
            if not universe_tickers:
                print("ERROR: Universe '{}' not found or empty".format(args.universe))
                sys.exit(1)
            tickers = sorted(universe_tickers)
            print("  Universe '{0}': {1} tickers (canonical from universe_memberships)".format(
                args.universe, len(tickers)))
            with_factors = sum(1 for t in tickers if t in ticker_factors)
            without_factors = len(tickers) - with_factors
            if without_factors > 0:
                print("  -> {0} have factor rows, {1} have zero "
                      "(will be classified as insufficient or non-PIT).".format(
                          with_factors, without_factors))
        else:
            tickers = sorted(ticker_factors.keys())
            print("  Using {0} tickers derived from factor_snapshots "
                  "(no --universe specified)".format(len(tickers)))

        if args.limit:
            tickers = tickers[:args.limit]

        # Ensure every canonical ticker has an entry in ticker_factors (the
        # ranking pipeline indexes into this dict). Empty dict = no factor
        # data for that stock; the pipeline handles this naturally.
        for t in tickers:
            if t not in ticker_factors:
                ticker_factors[t] = {}

        universe_member_count = len(tickers)

        # Resolve PIT mode: explicit flag overrides; otherwise auto from date.
        _today_iso = datetime.now().strftime("%Y-%m-%d")
        _is_historical = as_of < _today_iso
        if args.pit_mode == "require":
            require_pit = True
        elif args.pit_mode == "allow":
            require_pit = False
        else:
            require_pit = _is_historical  # auto

        # Build per-ticker PIT-safety map. A ticker is PIT-safe iff its
        # effective (latest <= as_of) market_cap row carries
        # source = 'marcap_historical'. Tickers with no daily_prices
        # market_cap row at all are treated as non-PIT (no historical
        # market cap available means we can't trust value factors for them).
        cur.execute("""
            WITH latest AS (
                SELECT ticker, MAX(date) AS d
                FROM daily_prices
                WHERE ticker = ANY(%s)
                  AND date <= %s
                  AND market_cap IS NOT NULL AND market_cap > 0
                GROUP BY ticker
            )
            SELECT dp.ticker, dp.source, dp.market_cap
            FROM latest l
            JOIN daily_prices dp ON dp.ticker = l.ticker AND dp.date = l.d
        """, (tickers, as_of))
        marcap_rows = cur.fetchall()
        ticker_to_source = {t: src for t, src, _mc in marcap_rows}
        ticker_to_mc = {t: mc for t, _src, mc in marcap_rows}

        # Names come from `stocks` so that we can label non-PIT stocks even
        # if they have no daily_prices market_cap row at all.
        cur.execute(
            "SELECT ticker, name FROM stocks WHERE ticker = ANY(%s)",
            (tickers,))
        ticker_to_name = {t: n for t, n in cur.fetchall()}

        non_pit_tickers = set()
        if require_pit:
            for t in tickers:
                src = ticker_to_source.get(t)
                # 'marcap_historical' is the only PIT-safe label. None means
                # the stock has no marcap row at all -- still non-PIT.
                if src != "marcap_historical":
                    non_pit_tickers.add(t)

        print("Ranking {0} stocks using system '{1}' as of {2}...".format(
            len(tickers), system_id, as_of))
        print("  Scoring method:                 {0}".format(SCORING_METHOD))
        print("  Missing-category policy:        {0}".format(policy))
        print("  Min active weight coverage:     {0:.0%}".format(thresholds["min_active_weight_coverage"]))
        print("  Min active category count:      {0}".format(thresholds["min_category_count"]))
        print("  Min factor count:               {0}".format(thresholds["min_factor_count"]))
        print("  Include insufficient coverage:  {0}".format(args.include_insufficient_coverage))
        print("  PIT market cap:                 {0} ({1} date)".format(
            "REQUIRED" if require_pit else "snapshot allowed",
            "historical" if _is_historical else "current"))
        if require_pit and non_pit_tickers:
            print("  -> {0} stocks will be excluded for non point-in-time "
                  "market cap.".format(len(non_pit_tickers)))

        # Warn if market_cap source is non-PIT for a historical as-of date.
        # Does not block; just makes the bias visible if PIT not enforced.
        if not require_pit:
            _check_marcap_pit_safety(conn, as_of, tickers)

        # 4. Determine which categories are globally available across the universe
        scoped_factors = {t: ticker_factors[t] for t in tickers if t in ticker_factors}
        globally_active = determine_globally_active_categories(tree, scoped_factors)

        cat_weights = {c["name"]: c.get("weight", 0) for c in tree.get("children", [])}
        all_cat_names = list(cat_weights.keys())

        globally_unavailable = [c for c in all_cat_names if not globally_active.get(c, False)]
        globally_available_count = sum(1 for c in all_cat_names if globally_active.get(c, False))

        if globally_unavailable:
            print("\n  Globally unavailable categories (excluded from composite):")
            for c in globally_unavailable:
                print("    - {0} (weight={1}%)".format(c, cat_weights[c]))
            active_w = sum(cat_weights[c] for c in all_cat_names if globally_active.get(c, False))
            total_w = sum(cat_weights.values())
            print("  Globally active weight: {0}% / {1}%".format(active_w, total_w))

        # 5. Compute per-stock ranking with policy
        all_results = []  # all stocks with policy/coverage info
        for ticker in tickers:
            fr = ticker_factors[ticker]
            r = compute_stock_ranking_with_policy(
                tree, fr, globally_active, policy, thresholds
            )
            r["ticker"] = ticker
            r["total_globally_active_categories"] = globally_available_count
            r["mcap_source"] = ticker_to_source.get(ticker)
            r["mcap_name"] = ticker_to_name.get(ticker)
            r["non_pit_market_cap"] = ticker in non_pit_tickers
            all_results.append(r)

        # 6. Split into pass / non-PIT excluded / coverage-fail.
        # Non-PIT exclusion takes priority over coverage failure: a stock
        # that's both non-PIT and low coverage shows up in the non-PIT
        # bucket so the operator sees the more important reason.
        non_pit_excluded = [r for r in all_results if r["non_pit_market_cap"]]
        remaining = [r for r in all_results if not r["non_pit_market_cap"]]
        passing = [r for r in remaining if r["passes_minimum"]]
        failing = [r for r in remaining if not r["passes_minimum"]]

        # Annotate non-PIT excluded stocks with explicit failure reason
        for r in non_pit_excluded:
            r["failure_reasons"] = list(r.get("failure_reasons", []))
            r["failure_reasons"].insert(
                0,
                "non_pit_market_cap (source={0})".format(
                    r.get("mcap_source") or "missing"),
            )
            r["passes_minimum"] = False

        # 7. Sort and assign ranks within the "passing" set
        passing.sort(
            key=lambda x: (x["composite_score"] is not None, x["composite_score"] or 0),
            reverse=True,
        )
        for i, r in enumerate(passing):
            r["rank"] = i + 1

        # 8. Failing stocks get None for rank but are sorted by composite if available
        failing.sort(
            key=lambda x: (x["composite_score"] is not None, x["composite_score"] or 0),
            reverse=True,
        )
        for i, r in enumerate(failing):
            r["rank"] = None
            r["fallback_position"] = i + 1
        for i, r in enumerate(non_pit_excluded):
            r["rank"] = None
            r["fallback_position"] = i + 1

        # Accounting: every stock in the canonical universe must land in
        # exactly one of (passing | failing | non_pit_excluded). The total
        # must equal universe_member_count.
        total_accounted = len(passing) + len(failing) + len(non_pit_excluded)
        print("\n  Universe:                        {0} tickers".format(
            universe_member_count))
        print("  Main ranking:                    {0} stocks".format(len(passing)))
        print("  Insufficient coverage:           {0} stocks".format(len(failing)))
        print("  Non point-in-time market cap:    {0} stocks".format(len(non_pit_excluded)))
        print("  Total accounted:                 {0}/{1}".format(
            total_accounted, universe_member_count))
        if total_accounted != universe_member_count:
            print("  ! WARNING: accounting mismatch ({0} != {1}). "
                  "This is a bug.".format(total_accounted, universe_member_count))

        # 9. Print top of main ranking
        print_main_ranking(passing, top_n=10)

        # 10. Print insufficient-coverage section
        print_excluded_ranking(failing, max_n=20)

        # 10b. Print non-PIT section
        if non_pit_excluded:
            print("\n  Excluded for non point-in-time market cap "
                  "({0} stocks):".format(len(non_pit_excluded)))
            print("  Ticker    Name                       Source                "
                  "Reason")
            print("  --------  -------------------------  --------------------  "
                  + "-" * 30)
            for r in non_pit_excluded[:30]:
                src = r.get("mcap_source") or "(no marcap row)"
                nm = (r.get("mcap_name") or "?")[:25]
                print("  {0:<8}  {1:<25}  {2:<20}  {3}".format(
                    r["ticker"], nm, src[:20], "missing_from_marcap_historical_on_asof"
                ))
            if len(non_pit_excluded) > 30:
                print("  ... and {0} more".format(len(non_pit_excluded) - 30))
            print("  WARNING: snapshot market cap is NOT point-in-time. "
                  "These stocks would bias value factors at this historical date.")
            print("  To include them anyway, re-run with --allow-snapshot-market-cap.")

        # 11. Build snapshot results JSON
        # Each entry mirrors the legacy fields for backward-compat plus the new ones.
        def to_json(r, status):
            return {
                "ticker": r["ticker"],
                "rank": r["rank"],
                "composite_score": r["composite_score"],
                "category_scores": {
                    name: {
                        "score": data["score"],
                        "weight": data["weight"],
                        "coverage": data["coverage"],
                        "status": data["status"],
                    } for name, data in r["category_scores"].items()
                },
                # Legacy compatibility: simple {name: score} map.
                # For globally-unavailable categories the score is null;
                # for missing_imputed it is 50.0; otherwise the real score.
                "category_scores_simple": {
                    name: data["score"]
                    for name, data in r["category_scores"].items()
                },
                "active_categories": r["active_categories"],
                "imputed_categories": r["imputed_categories"],
                "active_category_count": r["active_category_count"],
                "factor_count": r["factor_count"],
                "active_weight_coverage": r["active_weight_coverage"],
                "composite_weight_used": r["composite_weight_used"],
                "passes_minimum": r["passes_minimum"],
                "failure_reasons": r["failure_reasons"],
                "coverage_status": status,  # "passed" | "insufficient"
            }

        results_main = [to_json(r, "passed") for r in passing]
        results_excluded = [to_json(r, "insufficient") for r in failing]
        results_non_pit = [to_json(r, "non_pit_market_cap") for r in non_pit_excluded]

        if args.include_insufficient_coverage:
            stored_results = results_main + results_excluded + results_non_pit
        else:
            stored_results = results_main

        # 12. Snapshot metadata block
        snapshot_meta = {
            "scoring_method": SCORING_METHOD,
            "missing_category_policy": policy,
            "thresholds": thresholds,
            "globally_unavailable_categories": globally_unavailable,
            "globally_active_categories": [c for c in all_cat_names if globally_active.get(c, False)],
            "category_weights": cat_weights,
            # Canonical accounting: these four counts are guaranteed to sum
            # to universe_member_count for any --universe-scoped run.
            "universe_member_count": universe_member_count,
            "ranked_count": len(passing),
            "insufficient_coverage_count": len(failing),
            "non_pit_excluded_count": len(non_pit_excluded),
            "total_accounted_count": total_accounted,
            # Legacy aliases (kept for backwards-compat with anything that
            # already reads them):
            "passed_count": len(passing),
            "insufficient_count": len(failing),
            "non_pit_excluded_tickers": [r["ticker"] for r in non_pit_excluded],
            "require_pit_market_cap": require_pit,
            "include_insufficient_coverage": args.include_insufficient_coverage,
            "as_of_date": as_of,
            "universe_name": args.universe,
            "system_id": system_id,
        }

        # The 'results' JSONB stores both the array of stocks AND the metadata
        # under reserved keys; older readers that just iterate stocks ignore the
        # _meta key. The API route looks for the _meta key first.
        results_envelope = {
            "_meta": snapshot_meta,
            "rankings": stored_results,
        }

        # 13. Store snapshot
        cur.execute("""
            INSERT INTO ranking_snapshots
                (ranking_system_id, date, results,
                 universe_size, universe_name)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (system_id, as_of,
              PgJson(results_envelope), len(stored_results), args.universe))
        snapshot_id = cur.fetchone()[0]
        conn.commit()
        cur.close()

        print("\n  Snapshot saved (id={0})".format(snapshot_id))

    except Exception as e:
        print("ERROR: {}".format(e))
        raise
    finally:
        conn.close()

    print("Done!")
