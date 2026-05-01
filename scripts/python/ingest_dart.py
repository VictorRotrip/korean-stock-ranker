"""
Ingest DART financial statements into Supabase Postgres.

Uses direct DART REST API calls (no OpenDartReader, which hangs on XML download).

This script:
1. For each stock, fetches annual and quarterly financial statements
2. Stores periodEnd, filingDate, AND dataAvailableDate for point-in-time safety
3. Extracts all major financial line items (IS, BS, CF)
4. Uses consolidated statements when available, separate as fallback

Usage:
    python ingest_dart.py --tickers 005930 --year 2024
    python ingest_dart.py --tickers 005930,000660 --years 2023,2024
    python ingest_dart.py --tickers 005930 --year 2024 --reports 11011
    python ingest_dart.py --limit 10 --year 2024
    python ingest_dart.py --full                  # All years 2015-present
    python ingest_dart.py --quarterly --year 2024 # Include quarterly reports
    python ingest_dart.py --timeout 30            # Per-request timeout in seconds

Rate limit: DART API allows ~1000 requests/day for free keys.
"""

import os
import sys
import json
import argparse
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

# Windows console UTF-8 fix
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
DART_API_KEY = os.getenv("DART_API_KEY")
SCRIPT_NAME = "ingest_dart"

DART_API_BASE = "https://opendart.fss.or.kr/api"
DEFAULT_TIMEOUT = 30

# DART report type codes
REPORT_TYPES = {
    "11011": {"statement_type": "annual", "fiscal_quarter": None, "label": "Annual"},
    "11012": {"statement_type": "Q2", "fiscal_quarter": 2, "label": "Q2 Semi-annual"},
    "11013": {"statement_type": "Q1", "fiscal_quarter": 1, "label": "Q1"},
    "11014": {"statement_type": "Q3", "fiscal_quarter": 3, "label": "Q3"},
}

# Well-known DART corp codes for smoke-test tickers (avoids corpCode.xml download)
KNOWN_CORP_CODES = {
    "005930": "00126380",  # Samsung Electronics
    "000660": "00164779",  # SK Hynix
    "035420": "00266961",  # Naver
    "051910": "00356361",  # LG Chem
    "005380": "00164742",  # Hyundai Motor
    "035720": "00918444",  # Kakao
    "006400": "00164529",  # Samsung SDI
    "068270": "00110467",  # Celltrion
    "028260": "00128758",  # Samsung C&T
    "105560": "00433093",  # KB Financial
    "055550": "00382199",  # Shinhan Financial
    "003670": "00132541",  # POSCO Future M
    "207940": "01011884",  # Samsung Biologics
    "247540": "01310780",  # EcoPro BM
    "373220": "01588070",  # LG Energy Solution
}


# ---------------------------------------------------------------------------
# DART API helpers (direct HTTP, no OpenDartReader)
# ---------------------------------------------------------------------------

def dart_api_call(endpoint, params, timeout):
    """Make a DART API call, return parsed JSON or None."""
    params["crtfc_key"] = DART_API_KEY
    query = "&".join("{}={}".format(k, v) for k, v in params.items())
    url = "{}/{}.json?{}".format(DART_API_BASE, endpoint, query)

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data
    except urllib.error.URLError as e:
        print("network error: {}".format(e.reason), end=" ", flush=True)
        return None
    except Exception as e:
        if "timed out" in str(e).lower() or "timeout" in str(e).lower():
            print("TIMEOUT({}s)".format(timeout), end=" ", flush=True)
            return None
        print("error: {}".format(str(e)[:60]), end=" ", flush=True)
        return None


def resolve_corp_code(ticker, timeout):
    """Get DART corp_code for a stock ticker.
    Uses KNOWN_CORP_CODES cache first, then API lookup."""
    if ticker in KNOWN_CORP_CODES:
        return KNOWN_CORP_CODES[ticker]

    # Try the company search API
    data = dart_api_call("company", {"corp_code": ""}, timeout)
    # This won't work without corp_code — we need the XML for unknown tickers.
    # For unknown tickers, try listing filings by stock code instead.
    return None


def find_filing(ticker, corp_code, year, report_code, timeout):
    """Find a filing and return (filing_date, receipt_no) or (None, None)."""
    params = {
        "corp_code": corp_code,
        "bgn_de": "{}0101".format(year),
        "end_de": "{}0630".format(year + 1),
        "pblntf_ty": "A",  # Regular filings
        "page_count": "10",
    }

    print("    list...", end=" ", flush=True)
    data = dart_api_call("list", params, timeout)

    if not data or data.get("status") != "000":
        status = data.get("status", "?") if data else "no response"
        msg = data.get("message", "") if data else ""
        if status == "013":
            print("no filings", end=" ", flush=True)
        else:
            print("status={} {}".format(status, msg[:40]), end=" ", flush=True)
        return None, None

    # Filter for the specific report code
    report_label_map = {
        "11011": "사업보고서",
        "11012": "반기보고서",
        "11013": "분기보고서",  # Q1 and Q3 share this label
        "11014": "분기보고서",
    }
    target_label = report_label_map.get(report_code, "")

    filings_list = data.get("list", [])
    matching = None
    for f in filings_list:
        report_nm = f.get("report_nm", "")
        # Match by report name pattern
        if report_code == "11011" and "사업보고서" in report_nm and "분기" not in report_nm and "반기" not in report_nm:
            matching = f
            break
        elif report_code == "11012" and "반기" in report_nm:
            matching = f
            break
        elif report_code == "11013" and "1분기" in report_nm:
            matching = f
            break
        elif report_code == "11014" and "3분기" in report_nm:
            matching = f
            break

    # Fallback: just use the first filing if we have one and it's the only type we asked for
    if not matching and filings_list:
        # For annual reports, check if any filing name contains the year
        for f in filings_list:
            report_nm = f.get("report_nm", "")
            if report_code == "11011" and "사업보고서" in report_nm:
                matching = f
                break

    if not matching:
        print("no matching filing", end=" ", flush=True)
        return None, None

    filing_date_raw = matching.get("rcept_dt", "")
    receipt_no = matching.get("rcept_no", "")
    filing_date = None
    if filing_date_raw and len(filing_date_raw) >= 8:
        s = str(filing_date_raw)
        filing_date = "{}-{}-{}".format(s[:4], s[4:6], s[6:8])

    return filing_date, receipt_no


def fetch_finstate(corp_code, year, report_code, fs_div, timeout):
    """Fetch financial statement data. Returns list of account dicts or None."""
    params = {
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": report_code,
        "fs_div": fs_div,
    }

    data = dart_api_call("fnlttSinglAcntAll", params, timeout)

    if not data or data.get("status") != "000":
        return None

    return data.get("list", [])


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
# Helpers
# ---------------------------------------------------------------------------

def get_active_tickers(conn):
    cur = conn.cursor()
    cur.execute("SELECT ticker FROM stocks WHERE is_active = TRUE ORDER BY ticker")
    tickers = [row[0] for row in cur.fetchall()]
    cur.close()
    return tickers


def parse_amount(val):
    """Parse a DART amount string to int."""
    if val is None:
        return None
    s = str(val).replace(",", "").replace(" ", "").strip()
    if not s or s == "-" or s == "":
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def determine_period_end(year, report_code):
    if report_code == "11011":
        return "{}-12-31".format(year)
    elif report_code == "11013":
        return "{}-03-31".format(year)
    elif report_code == "11012":
        return "{}-06-30".format(year)
    elif report_code == "11014":
        return "{}-09-30".format(year)
    return "{}-12-31".format(year)


def latest_completed_fiscal_year():
    """Latest FY whose annual report is likely filed.
    Annual reports filed ~90 days after FY end (by March 31).
    Before April: use current_year - 2. From April: current_year - 1."""
    now = datetime.now()
    if now.month >= 4:
        return now.year - 1
    else:
        return now.year - 2


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

# DART account name -> our schema field
ACCOUNT_MAP = {
    # Income Statement
    "매출액": "revenue",
    "수익(매출액)": "revenue",
    "영업수익": "revenue",
    "매출원가": "cost_of_revenue",
    "매출총이익": "gross_profit",
    "영업이익": "operating_income",
    "영업이익(손실)": "operating_income",
    "당기순이익": "net_income",
    "당기순이익(손실)": "net_income",
    "주당이익": "eps",
    "기본주당이익(손실)": "eps",
    # Balance Sheet
    "자산총계": "total_assets",
    "부채총계": "total_liabilities",
    "자본총계": "total_equity",
    "유동자산": "current_assets",
    "유동부채": "current_liabilities",
    "현금및현금성자산": "cash",
    "단기차입금": "short_term_debt",
    # Cash Flow
    "영업활동현금흐름": "operating_cash_flow",
    "영업활동으로인한현금흐름": "operating_cash_flow",
    "투자활동현금흐름": "investing_cash_flow",
}


def fetch_financials(ticker, corp_code, year, report_code, timeout):
    """Fetch a single financial statement from DART via direct API.
    Returns a dict or None."""
    rt = REPORT_TYPES[report_code]

    # 1. Find filing date
    filing_date, receipt_no = find_filing(ticker, corp_code, year, report_code, timeout)
    if not filing_date:
        return None

    # dataAvailableDate = filingDate + 1 day
    fd = datetime.strptime(filing_date, "%Y-%m-%d")
    data_available_date = (fd + timedelta(days=1)).strftime("%Y-%m-%d")

    # 2. Fetch financial statement — try consolidated first
    consolidated = "consolidated"
    print("CFS...", end=" ", flush=True)
    accounts = fetch_finstate(corp_code, year, report_code, "CFS", timeout)

    if not accounts:
        print("OFS...", end=" ", flush=True)
        accounts = fetch_finstate(corp_code, year, report_code, "OFS", timeout)
        consolidated = "separate"

    if not accounts:
        print("no data", flush=True)
        return None

    # 3. Parse line items
    period_end = determine_period_end(year, report_code)

    result = {
        "ticker": ticker,
        "period_end": period_end,
        "filing_date": filing_date,
        "data_available_date": data_available_date,
        "fiscal_year": year,
        "fiscal_quarter": rt["fiscal_quarter"],
        "statement_type": rt["statement_type"],
        "consolidated_or_separate": consolidated,
        "source": "dart",
        "receipt_no": receipt_no,
    }

    for item in accounts:
        account_name = str(item.get("account_nm", "")).strip()
        if account_name in ACCOUNT_MAP:
            field = ACCOUNT_MAP[account_name]
            val = parse_amount(item.get("thstrm_amount"))
            if val is not None:
                result[field] = val

    # Derived fields
    if "revenue" in result and "cost_of_revenue" in result:
        result.setdefault("gross_profit", result["revenue"] - result["cost_of_revenue"])
    if "total_assets" in result and "total_liabilities" in result:
        result.setdefault("total_equity", result["total_assets"] - result["total_liabilities"])

    field_count = sum(1 for k in result if k not in (
        "ticker", "period_end", "filing_date", "data_available_date",
        "fiscal_year", "fiscal_quarter", "statement_type",
        "consolidated_or_separate", "source", "receipt_no"))

    print("OK ({} fields, {})".format(field_count, consolidated), flush=True)
    return result


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_financials(conn, records):
    if not records:
        return 0

    cur = conn.cursor()
    values = [
        (
            r["ticker"], r["period_end"], r["filing_date"], r["data_available_date"],
            r["fiscal_year"], r.get("fiscal_quarter"), r["statement_type"],
            r.get("consolidated_or_separate", "consolidated"), r.get("source", "dart"),
            r.get("revenue"), r.get("cost_of_revenue"), r.get("gross_profit"),
            r.get("operating_income"), r.get("net_income"), r.get("eps"),
            r.get("total_assets"), r.get("total_liabilities"), r.get("total_equity"),
            r.get("book_value_per_share"),
            r.get("current_assets"), r.get("current_liabilities"), r.get("cash"),
            r.get("total_debt"), r.get("operating_cash_flow"),
            r.get("capital_expenditure"), r.get("free_cash_flow"),
            r.get("dividends_paid"), r.get("ebitda"),
            r.get("interest_expense"), r.get("depreciation"),
            r.get("shares_outstanding"),
        )
        for r in records
    ]

    query = """
    INSERT INTO financial_statements (
        ticker, period_end, filing_date, data_available_date,
        fiscal_year, fiscal_quarter, statement_type,
        consolidated_or_separate, source,
        revenue, cost_of_revenue, gross_profit,
        operating_income, net_income, eps,
        total_assets, total_liabilities, total_equity,
        book_value_per_share,
        current_assets, current_liabilities, cash, total_debt,
        operating_cash_flow, capital_expenditure, free_cash_flow,
        dividends_paid, ebitda, interest_expense, depreciation,
        shares_outstanding
    ) VALUES %s
    ON CONFLICT (ticker, period_end, statement_type, consolidated_or_separate)
    DO UPDATE SET
        filing_date = EXCLUDED.filing_date,
        data_available_date = EXCLUDED.data_available_date,
        revenue = COALESCE(EXCLUDED.revenue, financial_statements.revenue),
        cost_of_revenue = COALESCE(EXCLUDED.cost_of_revenue, financial_statements.cost_of_revenue),
        gross_profit = COALESCE(EXCLUDED.gross_profit, financial_statements.gross_profit),
        operating_income = COALESCE(EXCLUDED.operating_income, financial_statements.operating_income),
        net_income = COALESCE(EXCLUDED.net_income, financial_statements.net_income),
        eps = COALESCE(EXCLUDED.eps, financial_statements.eps),
        total_assets = COALESCE(EXCLUDED.total_assets, financial_statements.total_assets),
        total_liabilities = COALESCE(EXCLUDED.total_liabilities, financial_statements.total_liabilities),
        total_equity = COALESCE(EXCLUDED.total_equity, financial_statements.total_equity),
        current_assets = COALESCE(EXCLUDED.current_assets, financial_statements.current_assets),
        current_liabilities = COALESCE(EXCLUDED.current_liabilities, financial_statements.current_liabilities),
        cash = COALESCE(EXCLUDED.cash, financial_statements.cash),
        total_debt = COALESCE(EXCLUDED.total_debt, financial_statements.total_debt),
        operating_cash_flow = COALESCE(EXCLUDED.operating_cash_flow, financial_statements.operating_cash_flow),
        capital_expenditure = COALESCE(EXCLUDED.capital_expenditure, financial_statements.capital_expenditure),
        free_cash_flow = COALESCE(EXCLUDED.free_cash_flow, financial_statements.free_cash_flow),
        ebitda = COALESCE(EXCLUDED.ebitda, financial_statements.ebitda),
        interest_expense = COALESCE(EXCLUDED.interest_expense, financial_statements.interest_expense),
        depreciation = COALESCE(EXCLUDED.depreciation, financial_statements.depreciation),
        shares_outstanding = COALESCE(EXCLUDED.shares_outstanding, financial_statements.shares_outstanding)
    """
    execute_values(cur, query, values)
    conn.commit()
    cur.close()
    return len(values)


def upsert_dart_filing(conn, record):
    if not record.get("receipt_no"):
        return
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO dart_filings (ticker, receipt_no, filing_date)
        VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING
    """, (record["ticker"], record["receipt_no"], record["filing_date"]))
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest DART financial statements")
    parser.add_argument("--tickers", help="Comma-separated tickers (e.g. 005930,000660)")
    parser.add_argument("--ticker", help="Single ticker (legacy, prefer --tickers)")
    parser.add_argument("--year", type=int, help="Specific fiscal year (e.g. 2024)")
    parser.add_argument("--years", help="Comma-separated fiscal years (e.g. 2023,2024)")
    parser.add_argument("--reports", help="Report codes: 11011=annual, 11013=Q1, 11012=Q2, 11014=Q3")
    parser.add_argument("--full", action="store_true", help="All years 2015-present")
    parser.add_argument("--quarterly", action="store_true", help="Include Q1, Q2, Q3 reports")
    parser.add_argument("--limit", type=int, help="Max tickers from DB")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="Per-request timeout in seconds (default: {})".format(DEFAULT_TIMEOUT))
    args = parser.parse_args()

    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set.")
        sys.exit(1)

    if not DART_API_KEY:
        print("DART_API_KEY not set. Skipping DART ingestion.")
        sys.exit(0)

    conn = psycopg2.connect(DATABASE_URL)

    # --- Resolve tickers ---
    if args.tickers:
        tickers = args.tickers.split(",")
    elif args.ticker:
        tickers = [args.ticker]
    else:
        tickers = get_active_tickers(conn)
        if args.limit:
            tickers = tickers[:args.limit]

    # --- Resolve corp codes (skip tickers without known corp codes) ---
    ticker_corp = {}
    skipped_tickers = []
    for t in tickers:
        cc = KNOWN_CORP_CODES.get(t)
        if cc:
            ticker_corp[t] = cc
        else:
            skipped_tickers.append(t)

    if skipped_tickers:
        print("Warning: No DART corp code for {} tickers (skipping): {}".format(
            len(skipped_tickers), ", ".join(skipped_tickers[:10])))
        print("  Add them to KNOWN_CORP_CODES in ingest_dart.py or use OpenDartReader for bulk lookup.")

    if not ticker_corp:
        print("No tickers with known DART corp codes. Nothing to do.")
        conn.close()
        sys.exit(0)

    # --- Resolve years ---
    if args.full:
        years = list(range(2015, datetime.now().year + 1))
    elif args.years:
        years = [int(y.strip()) for y in args.years.split(",")]
    elif args.year:
        years = [args.year]
    else:
        default_year = latest_completed_fiscal_year()
        years = [default_year]
        print("No --year specified. Defaulting to latest completed FY: {}".format(default_year))

    # --- Resolve report codes ---
    if args.reports:
        report_codes = [c.strip() for c in args.reports.split(",")]
        for rc in report_codes:
            if rc not in REPORT_TYPES:
                print("ERROR: Unknown report code '{}'. Valid: {}".format(rc, list(REPORT_TYPES.keys())))
                sys.exit(1)
    else:
        report_codes = ["11011"]
        if args.quarterly:
            report_codes += ["11013", "11012", "11014"]

    timeout_secs = args.timeout
    active_tickers = list(ticker_corp.keys())
    total_calls = len(active_tickers) * len(years) * len(report_codes)

    log_id = log_start(conn, {
        "tickers": ",".join(active_tickers),
        "years": years,
        "reports": report_codes,
        "timeout": timeout_secs,
    })

    total_processed = 0
    total_inserted = 0
    total_skipped = 0

    try:
        print()
        print("DART Financial Statement Ingestion")
        print("=" * 50)
        print("  Tickers: {} ({})".format(
            len(active_tickers),
            ", ".join(active_tickers[:5]) + ("..." if len(active_tickers) > 5 else "")))
        print("  Years:   {}".format(years))
        print("  Reports: {} ({})".format(
            report_codes,
            ", ".join(REPORT_TYPES[rc]["label"] for rc in report_codes)))
        print("  Timeout: {}s per request".format(timeout_secs))
        print("  Total API calls: ~{}".format(total_calls))
        print("=" * 50)
        print()

        records = []
        for i, ticker in enumerate(active_tickers):
            corp_code = ticker_corp[ticker]
            for year in years:
                for report_code in report_codes:
                    label = REPORT_TYPES[report_code]["label"]
                    total_processed += 1
                    print("  [{}/{}] {} / {} / {}  ".format(
                        total_processed, total_calls, ticker, year, label),
                        end="", flush=True)

                    result = fetch_financials(ticker, corp_code, year, report_code, timeout_secs)

                    if result:
                        records.append(result)
                        upsert_dart_filing(conn, result)
                    else:
                        total_skipped += 1

                    time.sleep(1.0)  # Rate limiting

            # Batch upsert every 50 records
            if len(records) >= 50:
                n = upsert_financials(conn, records)
                total_inserted += n
                print("  -> Upserted batch of {} records".format(n))
                records = []

        # Final upsert
        if records:
            n = upsert_financials(conn, records)
            total_inserted += n
            print("  -> Upserted {} records".format(n))

        # Update factor_coverage
        if total_inserted > 0:
            cur = conn.cursor()
            cur.execute("""
                UPDATE factor_coverage
                SET data_status = 'real', is_available = TRUE,
                    uses_mock_data = FALSE, point_in_time_safe = TRUE,
                    preferred_source = 'dart', last_updated = NOW()
                WHERE factor_id IN (
                    'earnings_yield', 'book_to_market', 'sales_yield', 'cf_yield',
                    'ev_ebitda', 'roe', 'roa', 'gross_profitability',
                    'operating_margin', 'debt_to_equity', 'interest_coverage',
                    'revenue_growth', 'eps_growth', 'op_income_growth', 'fcf_growth'
                )
            """)
            conn.commit()
            cur.close()

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
