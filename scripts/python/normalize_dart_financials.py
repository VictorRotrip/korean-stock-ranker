"""
Normalize DART financial statements from financial_statements table
to fundamental_snapshots table.

Maps raw DART account names to canonical fields and derives missing fields.

Usage:
    python normalize_dart_financials.py
    python normalize_dart_financials.py --tickers 005930,000660
    python normalize_dart_financials.py --limit 10
    python normalize_dart_financials.py --years 2022,2023,2024
    python normalize_dart_financials.py --dry-run
    python normalize_dart_financials.py --tickers 005930 --years 2023,2024 --limit 5

Maps:
    financial_statements.* -> fundamental_snapshots.*

Derives missing fields:
    gross_profit = revenue - cost_of_revenue
    total_equity = total_assets - total_liabilities
    free_cash_flow = operating_cash_flow - abs(capital_expenditure)
    ebitda = operating_income + depreciation_amortization
"""

import os
import sys
import json
import argparse
from datetime import datetime

# Windows console UTF-8 fix
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
SCRIPT_NAME = "normalize_dart_financials"


# ---------------------------------------------------------------------------
# Ingestion logging
# ---------------------------------------------------------------------------

def log_start(conn, params=None):
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO ingestion_log (script_name, parameters)
           VALUES (%s, %s) RETURNING id""",
        (SCRIPT_NAME, psycopg2.extras.Json(params)),
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
               rows_updated = %s, rows_skipped = %s,
               error_message = %s
           WHERE id = %s""",
        (status, rows_processed, rows_inserted, rows_updated,
         rows_skipped, error_message, log_id),
    )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Fetch raw DART data
# ---------------------------------------------------------------------------

def fetch_raw_financials(conn, tickers=None, years=None, limit=None):
    """Fetch financial_statements rows, optionally filtered.

    When limit is provided and tickers is not, first resolves distinct tickers
    (to ensure exactly limit stocks, not limit rows).

    Yields: dict with financial statement data.
    """
    where_clauses = []
    params = []

    # If limit is provided and no tickers, first resolve tickers
    resolved_tickers = tickers
    if limit and not tickers:
        cur2 = conn.cursor()
        cur2.execute(
            "SELECT DISTINCT ticker FROM financial_statements ORDER BY ticker LIMIT %s",
            (limit,)
        )
        resolved_tickers = [r[0] for r in cur2.fetchall()]
        cur2.close()

    if resolved_tickers:
        placeholders = ",".join(["%s"] * len(resolved_tickers))
        where_clauses.append("ticker IN ({})".format(placeholders))
        params.extend(resolved_tickers)

    if years:
        placeholders = ",".join(["%s"] * len(years))
        where_clauses.append("fiscal_year IN ({})".format(placeholders))
        params.extend(years)

    where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    query = """
        SELECT
            id, ticker, period_end, filing_date, data_available_date,
            fiscal_year, fiscal_quarter, statement_type,
            consolidated_or_separate, source,
            revenue, cost_of_revenue, gross_profit,
            operating_income, net_income, eps,
            total_assets, total_liabilities, total_equity,
            book_value_per_share,
            current_assets, current_liabilities, cash,
            total_debt, operating_cash_flow, capital_expenditure,
            free_cash_flow, dividends_paid, ebitda,
            interest_expense, depreciation, shares_outstanding
        FROM financial_statements
        {where_clause}
        ORDER BY ticker, period_end, fiscal_quarter
    """.format(where_clause=where_clause)

    cur = conn.cursor()
    cur.execute(query, params)

    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        yield dict(zip(cols, row))

    cur.close()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_financial(fs_row):
    """Normalize a financial_statements row to fundamental_snapshots format.

    Returns: dict with canonical field names or None if invalid.
    """
    # Determine report code from statement_type. fiscal_quarter is the
    # secondary signal (1/2/3) used when statement_type is missing or
    # uses some legacy label. We never want to leave report_code NULL,
    # because the unique index on fundamental_snapshots keys on it.
    type_to_code = {
        "annual": "11011",
        "Q1": "11013",
        "Q2": "11012",
        "Q3": "11014",
    }
    report_code = type_to_code.get(fs_row.get("statement_type"), None)
    if report_code is None:
        fq = fs_row.get("fiscal_quarter")
        if fq == 1:
            report_code = "11013"
        elif fq == 2:
            report_code = "11012"
        elif fq == 3:
            report_code = "11014"
        elif fq in (None, 4):
            report_code = "11011"

    # Build result with all available fields
    result = {
        "ticker": fs_row["ticker"],
        "period_end": fs_row["period_end"],
        "data_available_date": fs_row["data_available_date"],
        "fiscal_year": fs_row["fiscal_year"],
        "fiscal_quarter": fs_row["fiscal_quarter"],
        "report_code": report_code,
        "consolidated_or_separate": fs_row.get("consolidated_or_separate", "consolidated"),
        "source": fs_row.get("source", "dart"),
    }

    # Direct field mappings
    direct_fields = [
        "revenue", "gross_profit", "operating_income", "net_income", "eps",
        "total_assets", "total_equity", "total_liabilities", "total_debt",
        "cash_and_equivalents", "inventory", "operating_cash_flow",
        "capex", "free_cash_flow", "depreciation_amortization",
        "interest_expense", "ebitda", "shares_outstanding", "dividends_paid",
    ]

    # Map financial_statements column names to fundamental_snapshots names
    fs_to_snap = {
        "cash": "cash_and_equivalents",
        "depreciation": "depreciation_amortization",
        "capital_expenditure": "capex",
    }

    for field in direct_fields:
        fs_field = fs_to_snap.get(field, field)
        if fs_field in fs_row and fs_row[fs_field] is not None:
            result[field] = fs_row[fs_field]

    # Derive missing fields
    # gross_profit = revenue - cost_of_revenue
    if "gross_profit" not in result:
        if "revenue" in result and "cost_of_revenue" in fs_row and fs_row["cost_of_revenue"] is not None:
            result["gross_profit"] = result["revenue"] - fs_row["cost_of_revenue"]

    # total_equity = total_assets - total_liabilities
    if "total_equity" not in result:
        if "total_assets" in result and "total_liabilities" in result:
            result["total_equity"] = result["total_assets"] - result["total_liabilities"]

    # free_cash_flow = operating_cash_flow - abs(capex)
    if "free_cash_flow" not in result:
        if "operating_cash_flow" in result and "capex" in result:
            result["free_cash_flow"] = result["operating_cash_flow"] - abs(result["capex"])
        elif "operating_cash_flow" in result and "capital_expenditure" in fs_row and fs_row["capital_expenditure"] is not None:
            result["free_cash_flow"] = result["operating_cash_flow"] - abs(fs_row["capital_expenditure"])

    # ebitda = operating_income + depreciation_amortization
    if "ebitda" not in result:
        if "operating_income" in result and "depreciation_amortization" in result:
            result["ebitda"] = result["operating_income"] + result["depreciation_amortization"]
        elif "operating_income" in result and "depreciation" in fs_row and fs_row["depreciation"] is not None:
            result["ebitda"] = result["operating_income"] + fs_row["depreciation"]

    # Set updated_at
    result["updated_at"] = datetime.now()

    return result


def upsert_fundamental_snapshots(conn, records):
    """Upsert normalized records to fundamental_snapshots."""
    if not records:
        return 0

    cur = conn.cursor()
    values = []
    for r in records:
        values.append((
            r["ticker"], r["period_end"], r["data_available_date"],
            r["fiscal_year"], r.get("fiscal_quarter"), r.get("report_code"),
            r.get("consolidated_or_separate", "consolidated"),
            r.get("revenue"), r.get("gross_profit"), r.get("operating_income"),
            r.get("net_income"), r.get("eps"),
            r.get("total_assets"), r.get("total_equity"), r.get("total_liabilities"),
            r.get("total_debt"),
            r.get("cash_and_equivalents"), r.get("inventory"),
            r.get("operating_cash_flow"), r.get("capex"), r.get("free_cash_flow"),
            r.get("depreciation_amortization"), r.get("interest_expense"),
            r.get("ebitda"),
            r.get("shares_outstanding"), r.get("dividends_paid"),
            r.get("source", "dart"), r.get("updated_at"),
        ))

    # Conflict target = the unique index created by migration 006:
    #   (ticker, fiscal_year, report_code, consolidated_or_separate)
    # NULLS NOT DISTINCT (Postgres 15+). The previous index keyed on
    # period_end + fiscal_quarter, which let NULL-fiscal_quarter annual
    # rows duplicate on every rerun.
    query = """
    INSERT INTO fundamental_snapshots (
        ticker, period_end, data_available_date,
        fiscal_year, fiscal_quarter, report_code,
        consolidated_or_separate,
        revenue, gross_profit, operating_income,
        net_income, eps,
        total_assets, total_equity, total_liabilities,
        total_debt,
        cash_and_equivalents, inventory,
        operating_cash_flow, capex, free_cash_flow,
        depreciation_amortization, interest_expense,
        ebitda,
        shares_outstanding, dividends_paid,
        source, updated_at
    ) VALUES %s
    ON CONFLICT (ticker, fiscal_year, report_code, consolidated_or_separate)
    DO UPDATE SET
        period_end = EXCLUDED.period_end,
        fiscal_quarter = EXCLUDED.fiscal_quarter,
        data_available_date = EXCLUDED.data_available_date,
        revenue = COALESCE(EXCLUDED.revenue, fundamental_snapshots.revenue),
        gross_profit = COALESCE(EXCLUDED.gross_profit, fundamental_snapshots.gross_profit),
        operating_income = COALESCE(EXCLUDED.operating_income, fundamental_snapshots.operating_income),
        net_income = COALESCE(EXCLUDED.net_income, fundamental_snapshots.net_income),
        eps = COALESCE(EXCLUDED.eps, fundamental_snapshots.eps),
        total_assets = COALESCE(EXCLUDED.total_assets, fundamental_snapshots.total_assets),
        total_equity = COALESCE(EXCLUDED.total_equity, fundamental_snapshots.total_equity),
        total_liabilities = COALESCE(EXCLUDED.total_liabilities, fundamental_snapshots.total_liabilities),
        total_debt = COALESCE(EXCLUDED.total_debt, fundamental_snapshots.total_debt),
        cash_and_equivalents = COALESCE(EXCLUDED.cash_and_equivalents, fundamental_snapshots.cash_and_equivalents),
        inventory = COALESCE(EXCLUDED.inventory, fundamental_snapshots.inventory),
        operating_cash_flow = COALESCE(EXCLUDED.operating_cash_flow, fundamental_snapshots.operating_cash_flow),
        capex = COALESCE(EXCLUDED.capex, fundamental_snapshots.capex),
        free_cash_flow = COALESCE(EXCLUDED.free_cash_flow, fundamental_snapshots.free_cash_flow),
        depreciation_amortization = COALESCE(EXCLUDED.depreciation_amortization, fundamental_snapshots.depreciation_amortization),
        interest_expense = COALESCE(EXCLUDED.interest_expense, fundamental_snapshots.interest_expense),
        ebitda = COALESCE(EXCLUDED.ebitda, fundamental_snapshots.ebitda),
        shares_outstanding = COALESCE(EXCLUDED.shares_outstanding, fundamental_snapshots.shares_outstanding),
        dividends_paid = COALESCE(EXCLUDED.dividends_paid, fundamental_snapshots.dividends_paid),
        updated_at = EXCLUDED.updated_at
    """
    execute_values(cur, query, values)
    conn.commit()
    cur.close()
    return len(values)


# ---------------------------------------------------------------------------
# Coverage stats
# ---------------------------------------------------------------------------

def compute_coverage_stats(conn, tickers=None):
    """Compute coverage for key fields in fundamental_snapshots.

    Returns: dict with coverage ratios.
    """
    conditions = []
    params = []
    if tickers:
        placeholders = ",".join(["%s"] * len(tickers))
        conditions.append("ticker IN ({})".format(placeholders))
        params = list(tickers)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total_query = "SELECT COUNT(DISTINCT ticker) FROM fundamental_snapshots " + where_clause
    cur = conn.cursor()
    cur.execute(total_query, params)
    total_tickers = cur.fetchone()[0]
    cur.close()

    if total_tickers == 0:
        return {}

    fields = ["revenue", "net_income", "total_assets", "total_debt",
              "operating_cash_flow", "free_cash_flow", "eps"]

    coverage = {}
    for field in fields:
        field_conditions = ["{} IS NOT NULL".format(field)] + conditions
        field_where = "WHERE " + " AND ".join(field_conditions)
        query = "SELECT COUNT(DISTINCT ticker) FROM fundamental_snapshots " + field_where
        cur = conn.cursor()
        cur.execute(query, params)
        count = cur.fetchone()[0]
        cur.close()
        ratio = count / total_tickers if total_tickers > 0 else 0.0
        coverage[field] = "{}/{} ({:.1f}%)".format(count, total_tickers, ratio * 100)

    return coverage


def compute_report_code_coverage(conn, tickers=None):
    """Distinct-ticker counts per report_code and fiscal_year.

    Powers the diagnostics block printed at the end of the normalization run
    so the operator can see whether quarterly DART data was actually picked up.
    Rows in fundamental_snapshots have report_code in {11011, 11013, 11012,
    11014} corresponding to {annual, Q1, half-year, 9M-cumulative}.
    """
    conditions = []
    params = []
    if tickers:
        placeholders = ",".join(["%s"] * len(tickers))
        conditions.append("ticker IN ({})".format(placeholders))
        params = list(tickers)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    cur = conn.cursor()
    cur.execute(
        """SELECT fiscal_year, report_code, COUNT(DISTINCT ticker), COUNT(*)
             FROM fundamental_snapshots
           {wc}
           GROUP BY fiscal_year, report_code
           ORDER BY fiscal_year DESC, report_code""".format(wc=where_clause),
        params,
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def compute_duplicate_logical_keys(conn, tickers=None):
    """Return (total_rows, distinct_logical_keys, duplicate_groups, surplus_rows).

    A duplicate group is a (ticker, fiscal_year, report_code,
    consolidated_or_separate) value with more than one row in
    fundamental_snapshots. After migration 006 this should always be 0.
    If it is non-zero, the script prints a WARNING telling the operator
    to run migration 006.
    """
    conditions = []
    params = []
    if tickers:
        placeholders = ",".join(["%s"] * len(tickers))
        conditions.append("ticker IN ({})".format(placeholders))
        params = list(tickers)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM fundamental_snapshots " + where_clause,
                params)
    total_rows = cur.fetchone()[0]

    cur.execute(
        """SELECT COUNT(*) FROM (
               SELECT 1
                 FROM fundamental_snapshots
               {wc}
               GROUP BY ticker,
                        fiscal_year,
                        COALESCE(report_code, 'unknown'),
                        COALESCE(consolidated_or_separate, 'consolidated')
           ) t""".format(wc=where_clause),
        params,
    )
    distinct_logical = cur.fetchone()[0]

    cur.execute(
        """SELECT COUNT(*) FROM (
               SELECT 1
                 FROM fundamental_snapshots
               {wc}
               GROUP BY ticker,
                        fiscal_year,
                        COALESCE(report_code, 'unknown'),
                        COALESCE(consolidated_or_separate, 'consolidated')
               HAVING COUNT(*) > 1
           ) t""".format(wc=where_clause),
        params,
    )
    duplicate_groups = cur.fetchone()[0]
    cur.close()

    surplus = total_rows - distinct_logical
    return total_rows, distinct_logical, duplicate_groups, surplus


def compute_raw_statement_type_coverage(conn, tickers=None):
    """Distinct-ticker counts per statement_type / fiscal_year in the RAW
    financial_statements table.

    This is the input side of the pipeline. If Q1 / Q3 don't show up here,
    they're not in DART yet — re-run ingest_dart.py with --quarterly. If
    they DO show up here but not in fundamental_snapshots, that's a
    normalizer bug.

    statement_type values written by ingest_dart.py: 'annual', 'Q1', 'Q2',
    'Q3'. We translate each to its DART report code so the diagnostic
    columns match the rest of the pipeline.
    """
    conditions = []
    params = []
    if tickers:
        placeholders = ",".join(["%s"] * len(tickers))
        conditions.append("ticker IN ({})".format(placeholders))
        params = list(tickers)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    cur = conn.cursor()
    cur.execute(
        """SELECT fiscal_year, statement_type,
                  COUNT(DISTINCT ticker), COUNT(*)
             FROM financial_statements
           {wc}
           GROUP BY fiscal_year, statement_type
           ORDER BY fiscal_year DESC, statement_type""".format(wc=where_clause),
        params,
    )
    rows = cur.fetchall()
    cur.close()
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Normalize DART financials to fundamental_snapshots")
    parser.add_argument("--tickers", help="Comma-separated tickers (e.g. 005930,000660)")
    parser.add_argument("--universe", help="Use named universe from universe_memberships table")
    parser.add_argument("--limit", type=int, help="Max stocks to process")
    parser.add_argument("--years", help="Comma-separated fiscal years (e.g. 2022,2023,2024)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without writing")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set.")
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)

    # Parse arguments
    tickers = None
    if args.tickers:
        tickers = args.tickers.split(",")
    elif args.universe:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM universe_memberships WHERE universe_name = %s ORDER BY ticker", (args.universe,))
        tickers = [r[0] for r in cur.fetchall()]
        cur.close()
        if not tickers:
            print("ERROR: Universe '{}' not found or empty".format(args.universe))
            sys.exit(1)
        print("  Universe '{}': {} tickers".format(args.universe, len(tickers)))

    years = [int(y.strip()) for y in args.years.split(",")] if args.years else None
    limit = args.limit
    dry_run = args.dry_run

    log_id = log_start(conn, {
        "tickers": args.tickers,
        "years": args.years,
        "limit": limit,
        "dry_run": dry_run,
    })

    total_processed = 0
    total_inserted = 0
    total_skipped = 0

    try:
        print()
        print("DART Financial Normalization")
        print("=" * 50)
        if tickers:
            print("  Tickers: {}".format(", ".join(tickers[:5]) +
                                         (" ... +{0} more".format(len(tickers) - 5)
                                          if len(tickers) > 5 else "")))
        if years:
            print("  Years:   {}".format(years))
        if limit:
            print("  Limit:   {}".format(limit))
        if dry_run:
            print("  DRY RUN: no writes will be made")
        print("=" * 50)
        print()

        # Pre-flight: idempotency check. If migration 006 hasn't been applied
        # there will already be duplicate logical keys in fundamental_snapshots
        # — abort with a loud warning rather than silently doubling them.
        total_rows, distinct_logical, dup_groups, surplus = (
            compute_duplicate_logical_keys(conn, tickers)
        )
        if dup_groups > 0:
            print()
            print("!" * 70)
            print("WARNING: fundamental_snapshots has {0} duplicate logical "
                  "key group(s)".format(dup_groups))
            print("         total_rows={0}  distinct_logical_keys={1}  "
                  "surplus_rows={2}".format(
                      total_rows, distinct_logical, surplus))
            print("         The unique index keyed on (ticker, period_end, "
                  "fiscal_quarter, consolidated_or_separate) treats NULL "
                  "fiscal_quarter (= annual) as distinct, so re-runs of this")
            print("         normalizer have been inserting duplicate annual "
                  "rows on every pass.")
            print("         Fix: run scripts/sql/006_fundamental_snapshots_"
                  "dedupe.sql in Supabase SQL Editor, then rerun this "
                  "script.")
            print("         Aborting to avoid making the duplicates worse.")
            print("!" * 70)
            sys.exit(2)

        # Pre-flight: print RAW financial_statements coverage so the operator
        # can immediately see whether Q1/Q2/Q3 are present BEFORE normalizing.
        # If a code is missing here, no amount of normalizing will produce it
        # in fundamental_snapshots; the fix is to re-run ingest_dart.py with
        # --quarterly for the missing years.
        print("RAW financial_statements coverage (input)")
        print("=" * 50)
        type_to_code = {
            "annual": "11011",
            "Q1": "11013",
            "Q2": "11012",
            "Q3": "11014",
        }
        raw_rows = compute_raw_statement_type_coverage(conn, tickers)
        if not raw_rows:
            print("  (no rows in financial_statements for this scope)")
            print("  Hint: run ingest_dart.py --universe <name> --full --quarterly")
        else:
            print("  {0:<6} {1:<18} {2:>14} {3:>10}".format(
                "FY", "Code (Type)", "DistinctTickers", "Rows"))
            for fy, st, n_t, n_rows in raw_rows:
                rc = type_to_code.get(st, "?")
                print("  {0:<6} {1:<18} {2:>14} {3:>10}".format(
                    fy or "?",
                    "{0} ({1})".format(rc, st or "?"),
                    n_t, n_rows))
        print("=" * 50)
        print()

        # Fetch and normalize
        records_to_insert = []
        for i, fs_row in enumerate(fetch_raw_financials(conn, tickers, years, limit)):
            total_processed += 1
            normalized = normalize_financial(fs_row)

            if normalized:
                records_to_insert.append(normalized)
            else:
                total_skipped += 1

            # Batch insert every 50 records
            if len(records_to_insert) >= 50:
                if not dry_run:
                    n = upsert_fundamental_snapshots(conn, records_to_insert)
                    total_inserted += n
                    print("  Upserted batch of {} records".format(n))
                else:
                    print("  DRY RUN: would insert {} records".format(len(records_to_insert)))
                records_to_insert = []

        # Final batch
        if records_to_insert:
            if not dry_run:
                n = upsert_fundamental_snapshots(conn, records_to_insert)
                total_inserted += n
                print("  Upserted final batch of {} records".format(n))
            else:
                print("  DRY RUN: would insert {} records".format(len(records_to_insert)))

        # Coverage stats
        print()
        print("Coverage Statistics")
        print("=" * 50)
        coverage = compute_coverage_stats(conn, tickers)
        for field, stat in sorted(coverage.items()):
            print("  {}: {}".format(field, stat))
        print("=" * 50)

        # Report-code coverage breakdown (annual vs quarterly)
        print()
        print("Report-Code Coverage (rows in fundamental_snapshots)")
        print("=" * 50)
        # Map DART report codes to human labels matching how the rest of the
        # pipeline talks about them.
        code_label = {
            "11011": "Annual",
            "11013": "Q1",
            "11012": "H1 (Q2)",
            "11014": "9M (Q3)",
        }
        rc_rows = compute_report_code_coverage(conn, tickers)
        if not rc_rows:
            print("  (no rows in fundamental_snapshots for this scope)")
        else:
            print("  {0:<6} {1:<10} {2:>14} {3:>10}".format(
                "FY", "Code", "DistinctTickers", "Rows"))
            for fy, rc, n_t, n_rows in rc_rows:
                print("  {0:<6} {1:<10} {2:>14} {3:>10}".format(
                    fy or "?",
                    "{0} ({1})".format(rc or "?", code_label.get(rc, "?")),
                    n_t, n_rows))
        print("=" * 50)

        # Post-run idempotency check. After a successful normalize the row
        # count and the distinct-logical-key count should match exactly. If
        # they don't, the unique index isn't doing its job (e.g. the
        # migration was rolled back) — surface that loudly.
        total_rows, distinct_logical, dup_groups, surplus = (
            compute_duplicate_logical_keys(conn, tickers)
        )
        print()
        print("Idempotency Check (fundamental_snapshots)")
        print("=" * 50)
        print("  total_rows:            {0}".format(total_rows))
        print("  distinct_logical_keys: {0}".format(distinct_logical))
        print("  duplicate_groups:      {0}".format(dup_groups))
        print("  surplus_rows:          {0}".format(surplus))
        if dup_groups == 0 and surplus == 0:
            print("  STATUS: clean. Re-running this normalizer will not grow "
                  "the table.")
        else:
            print("  STATUS: DUPLICATES PRESENT. Run migration 006 to fix.")
        print("=" * 50)

        log_finish(conn, log_id, "success",
                   rows_processed=total_processed, rows_inserted=total_inserted,
                   rows_skipped=total_skipped)

    except Exception as e:
        log_finish(conn, log_id, "error", error_message=str(e),
                   rows_processed=total_processed, rows_inserted=total_inserted,
                   rows_skipped=total_skipped)
        print("ERROR: {}".format(e))
        raise
    finally:
        conn.close()

    print()
    print("Done! Processed {}, inserted {}, skipped {}.".format(
        total_processed, total_inserted, total_skipped))
