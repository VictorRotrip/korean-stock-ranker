"""
Ingest DART financial statements into Supabase Postgres.

Uses direct DART REST API calls (no OpenDartReader, which hangs on XML download).

This script:
1. For each stock, fetches annual and quarterly financial statements
2. Stores periodEnd, filingDate, AND dataAvailableDate for point-in-time safety
3. Extracts all major financial line items (IS, BS, CF)
4. Uses consolidated statements when available, separate as fallback
5. Downloads and caches corpCode.xml for unknown tickers
6. Supports --resume to skip already-ingested data
7. Supports --dry-run for preview
8. Retries with exponential backoff on failures

Usage:
    python ingest_dart.py --tickers 005930 --year 2024
    python ingest_dart.py --tickers 005930,000660 --years 2023,2024
    python ingest_dart.py --tickers 005930 --year 2024 --reports 11011
    python ingest_dart.py --limit 10 --year 2024
    python ingest_dart.py --full                  # All years 2015-present
    python ingest_dart.py --quarterly --year 2024 # Include quarterly reports
    python ingest_dart.py --timeout 30            # Per-request timeout in seconds
    python ingest_dart.py --resume                # Skip already-ingested ticker/year/report combos
    python ingest_dart.py --skip-existing         # Alias for --resume
    python ingest_dart.py --rate-limit 2.0        # Seconds between API calls
    python ingest_dart.py --dry-run               # Show what would be fetched
    python ingest_dart.py --retry 3               # Retry failed requests 3 times

Rate limit: DART API allows ~1000 requests/day for free keys.
"""

import os
import sys
import json
import argparse
import time
import urllib.request
import urllib.error
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from io import BytesIO

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
DART_CORP_CODES_CACHE = "scripts/python/.dart_corp_codes.json"

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
# Corp code resolution (download + cache corpCode.xml)
# ---------------------------------------------------------------------------

def download_corp_codes(timeout):
    """Download and cache DART corpCode.xml to .dart_corp_codes.json.

    Returns: dict of {stock_code: corp_code} or {} if download fails.
    Uses cached JSON if available.
    """
    # Check cache first
    if os.path.exists(DART_CORP_CODES_CACHE):
        try:
            with open(DART_CORP_CODES_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached and isinstance(cached, dict):
                print("  Using cached corp codes ({} entries)".format(len(cached)))
                return cached
        except Exception as e:
            print("  Cache read error: {}".format(e))

    # Download corpCode.xml as ZIP
    print("  Downloading DART corpCode.xml...", end=" ", flush=True)
    url = "{}/corpCode.xml?crtfc_key={}".format(DART_API_BASE, DART_API_KEY)

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            zip_data = resp.read()
        print("OK", flush=True)
    except Exception as e:
        print("FAILED: {}".format(str(e)[:40]), flush=True)
        return {}

    # Extract and parse CORPCODE.xml from ZIP
    try:
        with zipfile.ZipFile(BytesIO(zip_data)) as zf:
            xml_content = zf.read("CORPCODE.xml").decode("utf-8")
    except Exception as e:
        print("  ZIP extraction error: {}".format(e))
        return {}

    # Parse XML
    result = {}
    try:
        root = ET.fromstring(xml_content)
        for item in root.findall("list"):
            stock_code = item.findtext("stock_code", "").strip()
            corp_code = item.findtext("corp_code", "").strip()
            # Only include listed companies (stock_code non-empty)
            if stock_code and corp_code:
                result[stock_code] = corp_code
    except Exception as e:
        print("  XML parse error: {}".format(e))
        return {}

    # Cache to file
    try:
        os.makedirs(os.path.dirname(DART_CORP_CODES_CACHE), exist_ok=True)
        with open(DART_CORP_CODES_CACHE, "w", encoding="utf-8") as f:
            json.dump(result, f)
        print("  Cached {} corp codes to {}".format(len(result), DART_CORP_CODES_CACHE))
    except Exception as e:
        print("  Cache write error: {}".format(e))

    return result


# ---------------------------------------------------------------------------
# DART API helpers (direct HTTP, no OpenDartReader)
# ---------------------------------------------------------------------------

def dart_api_call(endpoint, params, timeout, retry_count=2):
    """Make a DART API call with retry on failure.

    Returns parsed JSON or None.
    On failure, retries up to retry_count times with exponential backoff (1s, 2s, 4s).
    """
    params["crtfc_key"] = DART_API_KEY
    query = "&".join("{}={}".format(k, v) for k, v in params.items())
    url = "{}/{}.json?{}".format(DART_API_BASE, endpoint, query)

    attempt = 0
    while attempt <= retry_count:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data
        except urllib.error.URLError as e:
            if attempt < retry_count:
                backoff = 2 ** attempt
                print("retry({}s)".format(backoff), end=" ", flush=True)
                time.sleep(backoff)
                attempt += 1
            else:
                print("network error: {}".format(str(e.reason)[:30]), end=" ", flush=True)
                return None
        except Exception as e:
            if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                if attempt < retry_count:
                    backoff = 2 ** attempt
                    print("retry({}s)".format(backoff), end=" ", flush=True)
                    time.sleep(backoff)
                    attempt += 1
                else:
                    print("TIMEOUT({}s)".format(timeout), end=" ", flush=True)
                    return None
            else:
                if attempt < retry_count:
                    backoff = 2 ** attempt
                    print("retry({}s)".format(backoff), end=" ", flush=True)
                    time.sleep(backoff)
                    attempt += 1
                else:
                    print("error: {}".format(str(e)[:30]), end=" ", flush=True)
                    return None


def resolve_corp_code(ticker, corp_code_map):
    """Get DART corp_code for a stock ticker.

    Uses KNOWN_CORP_CODES cache first, then corp_code_map (from XML download).
    """
    if ticker in KNOWN_CORP_CODES:
        return KNOWN_CORP_CODES[ticker]
    if ticker in corp_code_map:
        return corp_code_map[ticker]
    return None


def find_filing(ticker, corp_code, year, report_code, timeout, retry_count=2):
    """Find a filing and return (filing_date, receipt_no) or (None, None)."""
    params = {
        "corp_code": corp_code,
        "bgn_de": "{}0101".format(year),
        "end_de": "{}0630".format(year + 1),
        "pblntf_ty": "A",  # Regular filings
        "page_count": "10",
    }

    print("    list...", end=" ", flush=True)
    data = dart_api_call("list", params, timeout, retry_count)

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


def fetch_finstate(corp_code, year, report_code, fs_div, timeout, retry_count=2):
    """Fetch financial statement data. Returns list of account dicts or None."""
    params = {
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": report_code,
        "fs_div": fs_div,
    }

    data = dart_api_call("fnlttSinglAcntAll", params, timeout, retry_count)

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
    "이자수익": "interest_income",
    "매출원가": "cost_of_revenue",
    "매출총이익": "gross_profit",
    "매출총이익(손실)": "gross_profit",
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
    "현금": "cash",
    "단기차입금": "short_term_debt",
    "장기차입금": "long_term_debt",
    "사채": "bonds_payable",
    "차입금": "total_debt",
    "재고자산": "inventory",
    "이익잉여금": "retained_earnings",
    # Cash Flow
    "영업활동현금흐름": "operating_cash_flow",
    "영업활동으로인한현금흐름": "operating_cash_flow",
    "투자활동현금흐름": "investing_cash_flow",
    "유형자산의 취득": "capex_tangible",
    "무형자산의 취득": "capex_intangible",
    "감가상각비": "depreciation",
    "무형자산상각비": "amortization",
    "이자비용": "interest_expense",
    "배당금지급": "dividends_paid",
    "배당금의지급": "dividends_paid",
    # Shares
    "발행주식수": "shares_outstanding",
}


def fetch_financials(ticker, corp_code, year, report_code, timeout, retry_count=2):
    """Fetch a single financial statement from DART via direct API.
    Returns a dict or None."""
    rt = REPORT_TYPES[report_code]

    # 1. Find filing date
    filing_date, receipt_no = find_filing(ticker, corp_code, year, report_code, timeout, retry_count)
    if not filing_date:
        return None

    # dataAvailableDate = filingDate + 1 day
    fd = datetime.strptime(filing_date, "%Y-%m-%d")
    data_available_date = (fd + timedelta(days=1)).strftime("%Y-%m-%d")

    # 2. Fetch financial statement — try consolidated first
    consolidated = "consolidated"
    print("CFS...", end=" ", flush=True)
    accounts = fetch_finstate(corp_code, year, report_code, "CFS", timeout, retry_count)

    if not accounts:
        print("OFS...", end=" ", flush=True)
        accounts = fetch_finstate(corp_code, year, report_code, "OFS", timeout, retry_count)
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

    # total_debt = short_term_debt + long_term_debt + bonds_payable
    if "total_debt" not in result:
        total_debt = 0
        if "short_term_debt" in result:
            total_debt += result["short_term_debt"]
        if "long_term_debt" in result:
            total_debt += result["long_term_debt"]
        if "bonds_payable" in result:
            total_debt += result["bonds_payable"]
        if total_debt > 0:
            result["total_debt"] = total_debt

    # capex = capex_tangible + capex_intangible (negative = outflows)
    if "capex" not in result:
        capex = 0
        if "capex_tangible" in result:
            capex += result["capex_tangible"]
        if "capex_intangible" in result:
            capex += result["capex_intangible"]
        if capex != 0:
            result["capex"] = capex

    # free_cash_flow = operating_cash_flow - abs(capex)
    if "free_cash_flow" not in result:
        if "operating_cash_flow" in result and ("capex" in result or "capital_expenditure" in result):
            ocf = result.get("operating_cash_flow", 0)
            capex_val = result.get("capex") or result.get("capital_expenditure", 0)
            result["free_cash_flow"] = ocf - abs(capex_val)

    # ebitda = operating_income + depreciation + amortization
    if "ebitda" not in result:
        ebitda = 0
        if "operating_income" in result:
            ebitda += result["operating_income"]
        if "depreciation" in result:
            ebitda += result["depreciation"]
        if "amortization" in result:
            ebitda += result["amortization"]
        if ebitda != 0:
            result["ebitda"] = ebitda

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


def log_ingestion_error(conn, ticker, error_type, error_message, parameters=None):
    """Log a per-ticker ingestion error."""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO ingestion_errors (script_name, ticker, error_type, error_message, parameters)
        VALUES (%s, %s, %s, %s, %s)
    """, (SCRIPT_NAME, ticker, error_type, error_message, psycopg2.extras.Json(parameters)))
    conn.commit()
    cur.close()


def get_already_ingested(conn, tickers, years, report_codes):
    """Get set of (ticker, year, report_code) already in financial_statements."""
    if not tickers:
        return set()

    placeholders = ",".join(["%s"] * len(tickers))
    query = """
        SELECT DISTINCT ticker, fiscal_year, statement_type
        FROM financial_statements
        WHERE ticker IN ({})
        AND fiscal_year IN ({})
    """.format(
        placeholders,
        ",".join(["%s"] * len(years))
    )
    cur = conn.cursor()
    cur.execute(query, tickers + years)
    results = cur.fetchall()
    cur.close()

    # Map statement_type back to report_code
    type_to_code = {
        "annual": "11011",
        "Q1": "11013",
        "Q2": "11012",
        "Q3": "11014",
    }
    return {(r[0], r[1], type_to_code.get(r[2], r[2])) for r in results}


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
    parser.add_argument("--resume", action="store_true",
                        help="Skip ticker/year/report combos already in financial_statements")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Alias for --resume")
    parser.add_argument("--rate-limit", type=float, default=1.0,
                        help="Seconds between API calls (default: 1.0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be fetched without making API calls")
    parser.add_argument("--retry", type=int, default=2,
                        help="Number of retries per failed request (default: 2)")
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

    # --- Download corp codes (cache to file) ---
    print()
    print("Corp Code Resolution")
    print("=" * 50)
    corp_code_map = download_corp_codes(args.timeout)

    # --- Resolve corp codes ---
    ticker_corp = {}
    skipped_tickers = []
    for t in tickers:
        cc = resolve_corp_code(t, corp_code_map)
        if cc:
            ticker_corp[t] = cc
        else:
            skipped_tickers.append(t)

    if skipped_tickers:
        print("Warning: No DART corp code for {} tickers (skipping): {}".format(
            len(skipped_tickers), ", ".join(skipped_tickers[:10])))

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
    rate_limit = args.rate_limit
    retry_count = args.retry
    dry_run = args.dry_run
    resume = args.resume or args.skip_existing

    active_tickers = list(ticker_corp.keys())
    total_calls = len(active_tickers) * len(years) * len(report_codes)

    # Get already-ingested combos if resuming
    already_ingested = set()
    if resume:
        already_ingested = get_already_ingested(conn, active_tickers, years, report_codes)
        print("Resume mode: skipping {} already-ingested combos".format(len(already_ingested)))

    log_id = log_start(conn, {
        "tickers": ",".join(active_tickers),
        "years": years,
        "reports": report_codes,
        "timeout": timeout_secs,
        "rate_limit": rate_limit,
        "retry": retry_count,
        "dry_run": dry_run,
        "resume": resume,
    })

    total_processed = 0
    total_inserted = 0
    total_skipped = 0
    total_errors = 0

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
        print("  Rate limit: {}s between calls".format(rate_limit))
        print("  Retries: {} per failed request".format(retry_count))
        print("  Total potential API calls: ~{}".format(total_calls))
        if dry_run:
            print("  DRY RUN: no API calls will be made")
        if resume:
            print("  RESUME: skipping already-ingested combos")
        print("=" * 50)
        print()

        records = []
        for i, ticker in enumerate(active_tickers):
            corp_code = ticker_corp[ticker]
            for year in years:
                for report_code in report_codes:
                    label = REPORT_TYPES[report_code]["label"]

                    # Skip if already ingested
                    if (ticker, year, report_code) in already_ingested:
                        total_skipped += 1
                        print("  [{}/{}] {} / {} / {} -- SKIPPED (already ingested)".format(
                            total_processed + 1, total_calls, ticker, year, label))
                        total_processed += 1
                        continue

                    total_processed += 1
                    print("  [{}/{}] {} / {} / {}  ".format(
                        total_processed, total_calls, ticker, year, label),
                        end="", flush=True)

                    if dry_run:
                        print("DRY RUN (would fetch)", flush=True)
                    else:
                        try:
                            result = fetch_financials(ticker, corp_code, year, report_code,
                                                     timeout_secs, retry_count)

                            if result:
                                records.append(result)
                                upsert_dart_filing(conn, result)
                            else:
                                total_skipped += 1
                                log_ingestion_error(conn, ticker, "no_data",
                                                   "fetch_financials returned None",
                                                   {"year": year, "report_code": report_code})
                        except Exception as e:
                            total_errors += 1
                            error_msg = str(e)[:200]
                            print("\nERROR: {}".format(error_msg))
                            log_ingestion_error(conn, ticker, "fetch_error",
                                               error_msg,
                                               {"year": year, "report_code": report_code})

                        time.sleep(rate_limit)

            # Batch upsert every 50 records
            if len(records) >= 50:
                n = upsert_financials(conn, records)
                total_inserted += n
                print("  -> Upserted batch of {} records".format(n))
                records = []

        # Final upsert
        if records and not dry_run:
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
    print("Done! Processed {}, inserted {}, skipped {}, errors {}.".format(
        total_processed, total_inserted, total_skipped, total_errors))
