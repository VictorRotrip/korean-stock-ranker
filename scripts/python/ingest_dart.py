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
    python ingest_dart.py --refresh-corp-codes     # Force re-download of corpCode.xml
    python ingest_dart.py --refresh-corp-codes --dry-run  # Test corp-code download only

Rate limit: DART API allows ~1000 requests/day for free keys.
"""

import os
import sys
import json
import re
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


# ---------------------------------------------------------------------------
# DB connection resilience
# ---------------------------------------------------------------------------
#
# The full-universe DART ingest can run for hours and skip thousands of
# already-ingested filings between actual writes. The Supabase connection has
# been observed timing out during long skip streaks, after which every
# subsequent upsert / log call raises and the exception path itself crashes
# because it re-uses the now-closed connection.
#
# These helpers make every DB-touching call site reconnect-resilient:
#   connect_db()            : single connection factory with TCP keepalives
#   ensure_connection(c)    : returns a live conn (reconnects if closed/dead)
#   db_write_with_retry(c, fn, *a, **kw)
#                           : runs fn(conn, ...). On psycopg2.{Operational,
#                             Interface}Error, rolls back, reconnects, and
#                             retries once. Returns (conn, result) so the
#                             caller can keep using the (possibly new) conn.

# Errors that mean "the connection is dead, throw it away and reconnect".
# DatabaseError is the parent of OperationalError/InterfaceError and is
# what Supabase pooler drops surface as in psycopg2 2.9.x — catching it
# here means we recover from "server closed the connection unexpectedly"
# instead of dying. Non-connection DatabaseErrors (IntegrityError etc.)
# never get raised in our hot paths, so this is safe in practice.
_CONN_DEAD_ERRORS = (
    psycopg2.OperationalError,
    psycopg2.InterfaceError,
    psycopg2.DatabaseError,
)


def connect_db():
    """Open a fresh DB connection with TCP keepalives so idle connections
    don't get silently reaped by the Supabase pooler / load balancer."""
    return psycopg2.connect(
        DATABASE_URL,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )


def ensure_connection(conn, verify=False, max_attempts=8):
    """Return a live DB connection, with retry-with-backoff resilience.

    Reconnects (with a "DB connection lost; reconnecting..." log line) when:
      * conn is None,
      * conn.closed is non-zero (psycopg2 sets this once close() runs or the
        server drops the connection), or
      * verify=True and a `SELECT 1` liveness probe raises a connection error.

    Backoff: 1s, 2s, 4s, 8s, 16s, 32s, 60s (capped). After `max_attempts`
    consecutive failures we raise — at that point either Supabase is down or
    the local network is broken and there's nothing useful to retry.
    """
    import time as _time

    # Fast path: existing conn that probably works
    if conn is not None and not getattr(conn, "closed", 0) and not verify:
        return conn

    # Loop attempts: either initial connect, or reconnect after dead conn
    last_err = None
    for attempt in range(max_attempts):
        # 1. Try the verify path first if we have a conn (cheap if alive)
        if attempt == 0 and conn is not None and not getattr(conn, "closed", 0) and verify:
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
                cur.close()
                return conn  # alive
            except Exception as e:
                last_err = e
                print("  DB connection stale ({0}); reconnecting...".format(
                    str(e).strip()[:60]), flush=True)
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None

        # 2. Try a fresh connection (and verify it)
        try:
            new_conn = connect_db()
            if verify:
                cur = new_conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
                cur.close()
            return new_conn
        except Exception as e:
            last_err = e
            backoff = min(60, 2 ** attempt)  # 1, 2, 4, 8, 16, 32, 60, 60
            print(
                "  Reconnect attempt {0}/{1} failed ({2}); waiting {3}s...".format(
                    attempt + 1, max_attempts,
                    str(e).strip()[:60], backoff),
                flush=True,
            )
            _time.sleep(backoff)

    raise RuntimeError(
        "ensure_connection: could not connect after {0} attempts; "
        "last error: {1}".format(max_attempts, last_err)
    )


def db_write_with_retry(conn, fn, *args, **kwargs):
    """Run fn(conn, *args, **kwargs) with one reconnect-and-retry on
    connection-loss errors.

    Returns (conn, result). If fn raises a non-connection error, that
    exception bubbles up untouched — only OperationalError / InterfaceError
    trigger the reconnect dance. Callers must reassign their local `conn`
    from the returned tuple so subsequent calls use the fresh connection.
    """
    conn = ensure_connection(conn)
    try:
        return conn, fn(conn, *args, **kwargs)
    except _CONN_DEAD_ERRORS as e:
        print(
            "  DB error during write ({0}); reconnecting and retrying once...".format(
                str(e).strip()[:80]),
            flush=True,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        conn = connect_db()
        return conn, fn(conn, *args, **kwargs)

DART_API_BASE = "https://opendart.fss.or.kr/api"
DEFAULT_TIMEOUT = 30

# Cache location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, ".cache")
DART_CORP_CODES_CACHE = os.path.join(CACHE_DIR, "dart_corp_codes.json")
LEGACY_CACHE_PATH = os.path.join(SCRIPT_DIR, ".dart_corp_codes.json")

# DART report type codes
REPORT_TYPES = {
    "11011": {"statement_type": "annual", "fiscal_quarter": None, "label": "Annual"},
    "11012": {"statement_type": "Q2", "fiscal_quarter": 2, "label": "Q2 Semi-annual"},
    "11013": {"statement_type": "Q1", "fiscal_quarter": 1, "label": "Q1"},
    "11014": {"statement_type": "Q3", "fiscal_quarter": 3, "label": "Q3"},
}

# Well-known DART corp codes for smoke-test tickers
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

def download_corp_codes(timeout, force_refresh=False):
    """Download and cache DART corpCode.xml to .cache/dart_corp_codes.json."""
    # Migrate legacy cache
    if os.path.exists(LEGACY_CACHE_PATH) and not os.path.exists(DART_CORP_CODES_CACHE):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            os.rename(LEGACY_CACHE_PATH, DART_CORP_CODES_CACHE)
            print("  Migrated legacy cache -> {}".format(DART_CORP_CODES_CACHE), flush=True)
        except Exception:
            pass

    # Check cache first (unless forcing refresh)
    if not force_refresh and os.path.exists(DART_CORP_CODES_CACHE):
        try:
            cache_mtime = os.path.getmtime(DART_CORP_CODES_CACHE)
            cache_age_hours = (time.time() - cache_mtime) / 3600
            cache_size = os.path.getsize(DART_CORP_CODES_CACHE)
            with open(DART_CORP_CODES_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached and isinstance(cached, dict):
                print("  Loaded DART corp-code cache: {} tickers ({:.0f}KB, {:.0f}h old)".format(
                    len(cached), cache_size / 1024, cache_age_hours), flush=True)
                return cached
        except Exception as e:
            print("  Cache read error: {} -- will re-download".format(e), flush=True)

    # Download corpCode.xml as ZIP
    print("  Starting corpCode.xml download...", flush=True)
    url = "{}/corpCode.xml?crtfc_key={}".format(DART_API_BASE, DART_API_KEY)

    t0 = time.time()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            zip_data = resp.read()
        elapsed = time.time() - t0
        print("  Download completed: {:.1f}KB in {:.1f}s".format(
            len(zip_data) / 1024, elapsed), flush=True)
    except Exception as e:
        elapsed = time.time() - t0
        print("  Download FAILED after {:.1f}s: {}".format(elapsed, str(e)[:60]), flush=True)
        return {}

    # Extract XML from ZIP
    print("  Unzipping and parsing XML...", flush=True)
    try:
        with zipfile.ZipFile(BytesIO(zip_data)) as zf:
            xml_content = zf.read("CORPCODE.xml").decode("utf-8")
        print("  XML extracted: {:.1f}KB".format(len(xml_content) / 1024), flush=True)
    except Exception as e:
        print("  ZIP extraction error: {}".format(e), flush=True)
        return {}

    # Parse XML
    all_corps = 0
    listed_corps = 0
    result = {}
    try:
        root = ET.fromstring(xml_content)
        for item in root.findall("list"):
            all_corps += 1
            stock_code = item.findtext("stock_code", "").strip()
            corp_code = item.findtext("corp_code", "").strip()
            if stock_code and corp_code:
                listed_corps += 1
                result[stock_code] = corp_code
        print("  Parsed {} total records, {} listed tickers mapped".format(
            all_corps, listed_corps), flush=True)
    except Exception as e:
        print("  XML parse error: {}".format(e), flush=True)
        return {}

    # Cache to file
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(DART_CORP_CODES_CACHE, "w", encoding="utf-8") as f:
            json.dump(result, f)
        cache_size = os.path.getsize(DART_CORP_CODES_CACHE)
        print("  Cached {} corp codes -> {} ({:.0f}KB)".format(
            len(result), DART_CORP_CODES_CACHE, cache_size / 1024), flush=True)
    except Exception as e:
        print("  Cache write error: {}".format(e), flush=True)

    return result


# ---------------------------------------------------------------------------
# DART API helpers
# ---------------------------------------------------------------------------

def dart_api_call(endpoint, params, timeout, retry_count=2):
    """Make a DART API call with retry + exponential backoff."""
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
        except Exception as e:
            err_str = str(e).lower()
            is_timeout = "timed out" in err_str or "timeout" in err_str
            if attempt < retry_count:
                backoff = 2 ** attempt
                print("retry({}s)".format(backoff), end=" ", flush=True)
                time.sleep(backoff)
                attempt += 1
            else:
                if is_timeout:
                    print("TIMEOUT({}s)".format(timeout), end=" ", flush=True)
                elif isinstance(e, urllib.error.URLError):
                    reason = getattr(e, 'reason', str(e))
                    print("network: {}".format(str(reason)[:30]), end=" ", flush=True)
                else:
                    print("err: {}".format(str(e)[:30]), end=" ", flush=True)
                return None


def resolve_corp_code(ticker, corp_code_map):
    """Get DART corp_code for a stock ticker."""
    if ticker in KNOWN_CORP_CODES:
        return KNOWN_CORP_CODES[ticker]
    if ticker in corp_code_map:
        return corp_code_map[ticker]
    return None


def find_filing(ticker, corp_code, year, report_code, timeout, retry_count=2):
    """Find a filing and return (filing_date, receipt_no) or (None, None).

    Matching is done in three passes so we don't miss Q1/Q3 filings whose
    report_nm uses a period marker like '(2024.03)' instead of an explicit
    '1분기'/'3분기' label:

      PASS 1  exact label match (1분기 / 3분기 / 반기 / 사업보고서)
      PASS 2  period-marker fallback for Q1 / Q3 ('분기보고서' + '(YYYY.03)'
              or '(YYYY.09)' in any of the common separator variants)
      PASS 3  rcept_dt month fallback for Q1 / Q3:
                * Q1 reports usually filed in months 04-06 of `year`
                * Q3 reports usually filed in months 10-12 of `year`
              We pick the earliest matching '분기보고서' filing in that window.
      PASS 4  loosest fallback for annual ('사업보고서' even if 분기/반기 also
              appears in the name).
    """
    params = {
        "corp_code": corp_code,
        "bgn_de": "{}0101".format(year),
        "end_de": "{}0630".format(year + 1),
        "pblntf_ty": "A",
        # bumped from 10 -> 100. Some companies file >10 misc. reports in a
        # year (auditor changes, corrections), which would push the actual
        # quarterly off the first page and produce a false 'no report'.
        "page_count": "100",
    }

    data = dart_api_call("list", params, timeout, retry_count)

    if not data or data.get("status") != "000":
        return None, None

    filings_list = data.get("list", []) or []

    # PASS 1 — strict label match.
    matching = None
    for f in filings_list:
        report_nm = f.get("report_nm", "")
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

    # PASS 2 — period-marker fallback for Q1 / Q3.
    if not matching and report_code in ("11013", "11014"):
        period_token_year = year
        if report_code == "11013":
            markers = (
                "({0}.03)".format(period_token_year),
                "({0}.3)".format(period_token_year),
                "({0}-03)".format(period_token_year),
                "({0}/03)".format(period_token_year),
            )
        else:  # 11014
            markers = (
                "({0}.09)".format(period_token_year),
                "({0}.9)".format(period_token_year),
                "({0}-09)".format(period_token_year),
                "({0}/09)".format(period_token_year),
            )
        for f in filings_list:
            report_nm = f.get("report_nm", "")
            if "분기보고서" in report_nm and any(m in report_nm for m in markers):
                matching = f
                break

    # PASS 3 — rcept_dt month-window fallback for Q1 / Q3.
    if not matching and report_code in ("11013", "11014"):
        if report_code == "11013":
            target_months = ("04", "05", "06")
        else:
            target_months = ("10", "11", "12")
        candidates = [
            f for f in filings_list
            if "분기보고서" in (f.get("report_nm") or "")
        ]
        # Sort ascending by rcept_dt so we pick the earliest in-window filing
        # (a later filing is usually a correction of the original).
        candidates.sort(key=lambda x: str(x.get("rcept_dt") or ""))
        for f in candidates:
            rd = str(f.get("rcept_dt") or "")
            if (len(rd) >= 6
                    and rd[:4] == str(year)
                    and rd[4:6] in target_months):
                matching = f
                break

    # PASS 4 — loosest fallback for annual.
    if not matching and report_code == "11011":
        for f in filings_list:
            report_nm = f.get("report_nm", "")
            if "사업보고서" in report_nm:
                matching = f
                break

    if not matching:
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


def fetch_finstate_raw(corp_code, year, report_code, fs_div, timeout, retry_count=2):
    """Same as fetch_finstate but returns the WHOLE response dict (status,
    message, list) instead of just `list`. Used by --debug-dart-report so we
    can show the operator the exact OpenDART status code and message."""
    params = {
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": report_code,
        "fs_div": fs_div,
    }
    data = dart_api_call("fnlttSinglAcntAll", params, timeout, retry_count)
    return params, data


def fetch_shares_outstanding(corp_code, year, report_code, timeout, retry_count=2):
    """Outstanding common-share count from DART's stockTotqySttus disclosure.

    Share counts are NOT carried in the financial-statement endpoint
    (fnlttSinglAcntAll), which is why EPS and book-value-per-share were almost
    always NULL. This separate disclosure ("주식의 총수 현황") gives, per share
    class (se): issued total (istc_totqy), treasury (tesstk_co) and outstanding
    /distributed (distb_stock_co).

    Returns outstanding common shares (보통주 유통주식수), falling back to
    issued-minus-treasury, then to the 합계 (total) row. Returns None on any
    failure so it never blocks the rest of the ingest.
    """
    params = {
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": report_code,
    }
    try:
        data = dart_api_call("stockTotqySttus", params, timeout, retry_count)
    except Exception:
        return None
    if not data or data.get("status") != "000":
        return None

    def _outstanding(row):
        v = parse_amount(row.get("distb_stock_co"))
        if v is None or v <= 0:
            issued = parse_amount(row.get("istc_totqy"))
            treasury = parse_amount(row.get("tesstk_co")) or 0
            v = (issued - treasury) if issued is not None else None
        return v if (v is not None and v > 0) else None

    common = None
    total = None
    for row in (data.get("list") or []):
        se = _normalize_account_name(str(row.get("se") or ""))
        shares = _outstanding(row)
        if shares is None:
            continue
        if "보통주" in se:
            common = shares
        elif "합계" in se or "총계" in se:
            total = shares
    return common if common is not None else total


# ---------------------------------------------------------------------------
# Debug helpers (--debug-dart-report)
# ---------------------------------------------------------------------------

def _filing_match_reason(report_code, report_nm, year):
    """Return a short human reason if the filing should match the report code,
    otherwise None. Mirrors the logic in find_filing() PASS 1 + PASS 2 so the
    debug output explains why a filing was/wasn't picked."""
    if not report_nm:
        return None
    if report_code == "11011":
        if ("사업보고서" in report_nm
                and "분기" not in report_nm
                and "반기" not in report_nm):
            return "primary: '사업보고서' (annual)"
        if "사업보고서" in report_nm:
            return "fallback: '사업보고서' (annual)"
    elif report_code == "11012":
        if "반기" in report_nm:
            return "primary: '반기' (H1)"
    elif report_code == "11013":
        if "1분기" in report_nm:
            return "primary: '1분기'"
        if "분기보고서" in report_nm and any(
            m in report_nm for m in (
                "({0}.03)".format(year), "({0}.3)".format(year),
                "({0}-03)".format(year), "({0}/03)".format(year))):
            return "fallback: '분기보고서' + Q1 period marker"
    elif report_code == "11014":
        if "3분기" in report_nm:
            return "primary: '3분기'"
        if "분기보고서" in report_nm and any(
            m in report_nm for m in (
                "({0}.09)".format(year), "({0}.9)".format(year),
                "({0}-09)".format(year), "({0}/09)".format(year))):
            return "fallback: '분기보고서' + Q3 period marker"
    return None


def debug_one_report(ticker, corp_code, year, report_code, timeout, retry_count=2):
    """Print a deep-dive trace for one (ticker, year, report_code) covering:
        1. Identifiers + how the report code maps to fiscal_quarter / period_end.
        2. Method A: list -> find_filing -> fnlttSinglAcntAll (current path).
        3. Method B: direct fnlttSinglAcntAll, bypassing find_filing.
       For each path, print:
           - exact endpoint and raw params
           - HTTP-level errors / OpenDART status code + message
           - rows-returned count
           - first 5 raw rows incl. account_nm, sj_div, fs_div,
             thstrm_amount, thstrm_add_amount
           - whether thstrm_amount and thstrm_add_amount are populated at all
           - final classification reason (ok / no_report / no_data)."""
    rt = REPORT_TYPES.get(report_code)
    label = rt["label"] if rt else "?"
    statement_type = rt["statement_type"] if rt else "?"
    fiscal_quarter = rt["fiscal_quarter"] if rt else None
    period_end = determine_period_end(year, report_code)

    print()
    print("=" * 78)
    print("DEBUG  ticker={0}  corp_code={1}  year={2}  report_code={3} ({4})"
          .format(ticker, corp_code, year, report_code, label))
    print("       Maps to: statement_type={0}  fiscal_quarter={1}  period_end={2}"
          .format(statement_type, fiscal_quarter, period_end))
    print("=" * 78)

    # ---------------- Method A: current pipeline path ----------------
    print()
    print("[Method A] Current path: 'list' -> match report_nm -> 'fnlttSinglAcntAll'")
    print("-" * 78)
    list_params = {
        "corp_code": corp_code,
        "bgn_de": "{}0101".format(year),
        "end_de": "{}0630".format(year + 1),
        "pblntf_ty": "A",
        "page_count": "100",
    }
    print("  endpoint: GET {0}/list.json".format(DART_API_BASE))
    print("  params:   {0}".format(json.dumps(list_params, ensure_ascii=False)))
    list_data = dart_api_call("list", list_params, timeout, retry_count)
    if not list_data:
        print("  status:   NETWORK ERROR (no JSON returned)")
        print("  classification: skip: list endpoint failed")
        return
    print("  status:   {0}  message: {1}".format(
        list_data.get("status"), list_data.get("message")))
    filings = list_data.get("list", []) or []
    print("  rows:     {0}".format(len(filings)))
    print("  First 5 filings:")
    for f in filings[:5]:
        print("    rcept_dt={0}  rcept_no={1}  report_nm={2}".format(
            f.get("rcept_dt"), f.get("rcept_no"),
            (f.get("report_nm") or "")[:55]))

    matched = None
    matched_reason = None
    for f in filings:
        reason = _filing_match_reason(
            report_code, f.get("report_nm", ""), year)
        if reason:
            matched = f
            matched_reason = reason
            break
    if matched:
        print("  matched filing:")
        print("    rcept_dt={0}  rcept_no={1}".format(
            matched.get("rcept_dt"), matched.get("rcept_no")))
        print("    report_nm={0}".format(
            (matched.get("report_nm") or "")[:55]))
        print("    reason: {0}".format(matched_reason))
    else:
        print("  matched filing: NONE")
        print("  classification: skip: no report (find_filing returned None)")

    # Always proceed to the fnlttSinglAcntAll call so the operator can see
    # whether the financials *would* be available even if find_filing
    # mismatched on names. This is the smoking gun if Method A fails but
    # Method B succeeds.
    for fs_div in ("CFS", "OFS"):
        print()
        print("  fnlttSinglAcntAll  fs_div={0}".format(fs_div))
        params, data = fetch_finstate_raw(
            corp_code, year, report_code, fs_div, timeout, retry_count)
        print("    endpoint: GET {0}/fnlttSinglAcntAll.json".format(DART_API_BASE))
        print("    params:   {0}".format(json.dumps(params, ensure_ascii=False)))
        if not data:
            print("    status:   NETWORK ERROR")
            continue
        print("    status:   {0}  message: {1}".format(
            data.get("status"), data.get("message")))
        rows = data.get("list", []) or []
        print("    rows:     {0}".format(len(rows)))
        if rows:
            print("    First 5 rows:")
            for r in rows[:5]:
                print("      sj_div={0:<5} fs_div={1:<5} account_nm={2:<28} "
                      "thstrm_amount={3:<15} thstrm_add_amount={4}".format(
                          (r.get("sj_div") or "")[:5],
                          (r.get("fs_div") or "")[:5],
                          (r.get("account_nm") or "")[:28],
                          str(r.get("thstrm_amount") or "-")[:15],
                          str(r.get("thstrm_add_amount") or "-")))
            has_thstrm = any(r.get("thstrm_amount") not in (None, "", "-")
                             for r in rows)
            has_add = any(r.get("thstrm_add_amount") not in (None, "", "-")
                          for r in rows)
            cfs_present = any(str(r.get("fs_div") or "").upper() == "CFS"
                              for r in rows)
            ofs_present = any(str(r.get("fs_div") or "").upper() == "OFS"
                              for r in rows)
            print("    has thstrm_amount values:      {0}".format(has_thstrm))
            print("    has thstrm_add_amount values:  {0}  "
                  "(needed for cumulative YTD on Q1/H1/Q3)".format(has_add))
            print("    CFS rows present: {0}   OFS rows present: {1}"
                  .format(cfs_present, ofs_present))
            break
        else:
            print("    no rows -> falling through to next fs_div")

    # ---------------- Method B: direct fnlttSinglAcntAll ----------------
    print()
    print("[Method B] Direct fnlttSinglAcntAll (bypasses find_filing)")
    print("-" * 78)
    succeeded_div = None
    for fs_div in ("CFS", "OFS"):
        params, data = fetch_finstate_raw(
            corp_code, year, report_code, fs_div, timeout, retry_count)
        print("  endpoint: GET {0}/fnlttSinglAcntAll.json  fs_div={1}".format(
            DART_API_BASE, fs_div))
        print("  params:   {0}".format(json.dumps(params, ensure_ascii=False)))
        if not data:
            print("  status:   NETWORK ERROR")
            continue
        print("  status:   {0}  message: {1}".format(
            data.get("status"), data.get("message")))
        rows = data.get("list", []) or []
        print("  rows:     {0}".format(len(rows)))
        if rows:
            if succeeded_div is None:
                succeeded_div = fs_div
            print("  First 5 rows:")
            for r in rows[:5]:
                print("    sj_div={0:<5} fs_div={1:<5} account_nm={2:<28} "
                      "thstrm_amount={3:<15} thstrm_add_amount={4}".format(
                          (r.get("sj_div") or "")[:5],
                          (r.get("fs_div") or "")[:5],
                          (r.get("account_nm") or "")[:28],
                          str(r.get("thstrm_amount") or "-")[:15],
                          str(r.get("thstrm_add_amount") or "-")))
            break
        else:
            print("  no rows for fs_div={0}".format(fs_div))

    # ---------------- Verdict ----------------
    print()
    print("[Verdict] for ticker={0} year={1} report_code={2} ({3})".format(
        ticker, year, report_code, label))
    if succeeded_div:
        print("  Method B (direct endpoint) WORKS via fs_div={0}.".format(
            succeeded_div))
        if matched:
            print("  Method A (find_filing) ALSO matched a filing -> production "
                  "should already work for this case.")
        else:
            print("  Method A FAILED (no filing matched). Production sees "
                  "'skip: no report'. The fix is to relax find_filing's "
                  "report_nm matching for {0}.".format(label))
    else:
        print("  Method B (direct endpoint) returned NO ROWS for both CFS and "
              "OFS. The data does not exist in OpenDART for this "
              "ticker/year/report_code. No code fix will recover it; the "
              "company simply does not have a {0} filing for FY{1}.".format(
                  label, year))
    print("=" * 78)


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

def get_active_tickers_with_names(conn):
    """Get active tickers with names from DB."""
    cur = conn.cursor()
    cur.execute("SELECT ticker, name FROM stocks WHERE is_active = TRUE ORDER BY ticker")
    rows = cur.fetchall()
    cur.close()
    return rows


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
    """Latest FY whose annual report is likely filed."""
    now = datetime.now()
    if now.month >= 4:
        return now.year - 1
    else:
        return now.year - 2


def format_elapsed(seconds):
    """Format seconds as human-readable elapsed time."""
    if seconds < 60:
        return "{:.1f}s".format(seconds)
    elif seconds < 3600:
        return "{:.0f}m {:.0f}s".format(seconds // 60, seconds % 60)
    else:
        return "{:.0f}h {:.0f}m".format(seconds // 3600, (seconds % 3600) // 60)


# ---------------------------------------------------------------------------
# Data fetching — account mapping + fetch_financials
# ---------------------------------------------------------------------------

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
    "당기순손익": "net_income",                  # alt phrasing: 손익 instead of 이익
    # Net income attributable to owners of the parent — used (when present) for
    # more accurate per-share derivation than total net income, which includes
    # the minority/non-controlling interest.
    "지배기업의소유주에게귀속되는당기순이익": "net_income_owners",
    "지배기업의소유주에게귀속되는당기순이익(손실)": "net_income_owners",
    "지배기업소유주지분순이익": "net_income_owners",
    "지배기업소유주순이익": "net_income_owners",
    "지배주주순이익": "net_income_owners",
    "주당이익": "eps",
    "기본주당이익(손실)": "eps",
    "기본주당순이익": "eps",
    "기본주당순손익": "eps",                      # 한솔제지-style phrasing
    "주당순이익": "eps",
    "기본주당순이익(손실)": "eps",
    # Balance Sheet
    "자산총계": "total_assets",
    "부채총계": "total_liabilities",
    "자본총계": "total_equity",
    # Equity attributable to owners of the parent — used (when present) for
    # more accurate book-value-per-share than total equity.
    "지배기업의소유주에게귀속되는자본": "equity_owners",
    "지배기업소유주지분": "equity_owners",
    "지배주주지분": "equity_owners",
    "유동자산": "current_assets",
    "유동부채": "current_liabilities",
    "현금및현금성자산": "cash",
    "현금": "cash",
    "단기차입금": "short_term_debt",
    "단기차입금및사채": "short_term_debt",        # combined form: borrowings + bonds
    "장기차입금": "long_term_debt",
    "장기차입금및사채": "long_term_debt",
    "유동성장기부채": "short_term_debt",          # current portion of LT debt
    "유동성장기차입금": "short_term_debt",
    "사채": "bonds_payable",
    "차입금": "total_debt",
    "재고자산": "inventory",
    "이익잉여금": "retained_earnings",
    # Cash Flow
    "영업활동현금흐름": "operating_cash_flow",
    "영업활동으로인한현금흐름": "operating_cash_flow",
    "영업활동순현금흐름": "operating_cash_flow",
    "투자활동현금흐름": "investing_cash_flow",
    "유형자산의취득": "capex_tangible",
    "유형자산취득": "capex_tangible",
    "무형자산의취득": "capex_intangible",
    "무형자산취득": "capex_intangible",
    "감가상각비": "depreciation",
    # Combined D&A lines (common on the cash-flow reconciliation) -> fold into
    # depreciation; EBITDA only sums whatever is present, so a combined value
    # here still builds EBITDA correctly without double counting amortization.
    "감가상각비와무형자산상각비": "depreciation",
    "감가상각비및무형자산상각비": "depreciation",
    "감가상각비와기타상각비": "depreciation",
    "유형자산감가상각비": "depreciation",
    "무형자산상각비": "amortization",
    "무형자산상각": "amortization",
    "무형자산의상각": "amortization",
    "이자비용": "interest_expense",
    "배당금지급": "dividends_paid",
    "배당금의지급": "dividends_paid",
    # Shares
    "발행주식수": "shares_outstanding",
    "유통주식수": "shares_outstanding",
}


import unicodedata as _unicodedata


def _normalize_account_name(s):
    """Canonicalise a DART account name for lookup. Strips whitespace
    (DART filings inconsistently include spaces inside Korean compound
    words like "영업활동으로 인한 현금흐름" vs "영업활동으로인한현금흐름") AND
    applies Unicode NFC composition (some DART responses use decomposed
    Hangul, which doesn't byte-match composed Hangul in source code)."""
    if not s:
        return ""
    return "".join(_unicodedata.normalize("NFC", s).split())


# Pre-normalised lookup table, built once at import time.
_ACCOUNT_MAP_NORM = {
    _normalize_account_name(k): v for k, v in ACCOUNT_MAP.items()
}


def _lookup_field(account_name):
    """Return the canonical field for a DART account name, tolerant of
    whitespace and Unicode-form differences."""
    if not account_name:
        return None
    return _ACCOUNT_MAP_NORM.get(_normalize_account_name(account_name))


# Per-field priority lists for fields that have multiple Korean K-IFRS
# concept names. When a filing reports more than one of these in the
# same statement, the FIRST entry (= highest priority = most precise)
# wins. Applied in fetch_financials AFTER the regular ACCOUNT_MAP loop,
# so it can override the simpler mapping when a more precise concept
# was also present.
#
# interest_expense must be TRUE interest cost only. Korean issuers often
# report 금융비용 (finance costs — a broad bucket that includes FX losses and
# fair-value losses) on the face of the income statement while the precise
# 이자비용 sits in the notes. We previously substituted 금융비용 when 이자비용
# was absent, which badly overstated interest for exporters (e.g. Spigen showed
# ~10.5B of "interest" that was mostly FX) and corrupted the interest-coverage
# factor. We now capture ONLY genuine interest-expense concepts; if none is on
# the face, interest_expense stays NULL (the precise figure lives in the XBRL
# notes, which this endpoint doesn't return). 금융비용 is deliberately excluded.
ACCOUNT_PRIORITY = {
    "interest_expense": [
        # Most precise -> least precise (all are genuine interest cost)
        "이자비용",
        "이자비용및유사비용",
        "이자비용 및 유사비용",
        "이자비용및유사한비용",
        "차입금이자비용",
        "사채이자비용",
        "차입금이자",
        "사채이자",
    ],
}


# Interest-bearing borrowing line items on the balance sheet. total_debt is the
# SUM of every matching line (current + non-current borrowings and all bond
# types) — the generic ACCOUNT_MAP loop overwrites duplicate field names, so a
# company listing both bank borrowings and convertible bonds would otherwise be
# under-counted. Lease liabilities are deliberately excluded (operating leases
# aren't interest-bearing financial debt in the classic sense).
BORROWING_LABELS = [
    "단기차입금", "단기차입금및사채", "유동성장기부채", "유동성장기차입금",
    "유동성사채", "단기사채", "유동성전환사채",
    "장기차입금", "장기차입금및사채", "장기성차입금",
    "사채", "전환사채", "신주인수권부사채", "교환사채",
]
BORROWING_LABELS_NORM = {_normalize_account_name(x) for x in BORROWING_LABELS}


# ---------------------------------------------------------------------------
# Statement-type filter (sj_div) — prevents cross-statement contamination
# ---------------------------------------------------------------------------
# DART filings group line items into named statements via the `sj_div` field:
#   BS / BSE = 재무상태표 (Balance Sheet)
#   IS / CIS = 손익계산서 / 포괄손익계산서 (Income / Comprehensive Income Statement)
#   CF / CFS = 현금흐름표 (Cash Flow Statement)
#   SCE      = 자본변동표 (Statement of Changes in Equity)
#
# The same Korean account name can legitimately appear in multiple statements.
# Critically, 당기순이익 ("net income") appears both in IS/CIS (where the
# value is the real number) AND in SCE (where it's often a header row with
# thstrm_amount = "0" because the actual movement is broken out below it).
# Without a sj_div filter the parser overwrites the real value with the SCE
# zero, corrupting net_income for ~1500 stocks including Samsung Electronics,
# Kia, SK Hynix, POSCO, SK Telecom, etc.
#
# Each field in ACCOUNT_MAP is tagged here with the sj_div values it is
# allowed to come from. Fields whose data could appear in more than one
# statement (e.g. depreciation can show up in both IS and CF) list both.

IS_DIVS = ("IS", "CIS")        # income statement / comprehensive income
BS_DIVS = ("BS", "BSE")        # balance sheet (also used inside fetch_financials)
CF_DIVS = ("CF", "CFS")        # cash flow statement

FIELD_TO_STATEMENT = {
    # Income statement fields
    "revenue":            IS_DIVS,
    "interest_income":    IS_DIVS,
    "cost_of_revenue":    IS_DIVS,
    "gross_profit":       IS_DIVS,
    "operating_income":   IS_DIVS,
    "net_income":         IS_DIVS,
    "net_income_owners":  IS_DIVS,
    "eps":                IS_DIVS,
    "interest_expense":   IS_DIVS,
    # Depreciation/amortization is reported on both IS and the CF
    # reconciliation; either source is fine.
    "depreciation":       IS_DIVS + CF_DIVS,
    "amortization":       IS_DIVS + CF_DIVS,
    # Balance sheet fields
    "total_assets":       BS_DIVS,
    "total_liabilities":  BS_DIVS,
    "total_equity":       BS_DIVS,
    "equity_owners":      BS_DIVS,
    "current_assets":     BS_DIVS,
    "current_liabilities": BS_DIVS,
    "cash":               BS_DIVS,
    "total_debt":         BS_DIVS,
    "short_term_debt":    BS_DIVS,
    "long_term_debt":     BS_DIVS,
    "bonds_payable":      BS_DIVS,
    "inventory":          BS_DIVS,
    "retained_earnings":  BS_DIVS,
    # Cash flow fields
    "operating_cash_flow": CF_DIVS,
    "investing_cash_flow": CF_DIVS,
    "capex_tangible":     CF_DIVS,
    "capex_intangible":   CF_DIVS,
    "dividends_paid":     CF_DIVS,
    # Shares outstanding can appear in BS or in a separate disclosure row;
    # be permissive here — empty tuple means "no filter".
    "shares_outstanding": (),
}


def _sj_div_ok(field, sj_div):
    """Return True if a line item with this sj_div is allowed to write
    to this field. Unknown fields are permissive (no filter). Empty
    sj_div (rare, but possible for old filings) is also permissive so
    we don't silently lose data — the SCE contamination we are guarding
    against has a populated sj_div = 'SCE'."""
    if not sj_div:
        return True
    allowed = FIELD_TO_STATEMENT.get(field)
    if not allowed:   # empty tuple or missing -> no filter
        return True
    return sj_div in allowed


def fetch_financials(ticker, corp_code, year, report_code, timeout, retry_count=2):
    """Fetch a single financial statement from DART.
    Returns (result_dict, status_string)."""
    rt = REPORT_TYPES[report_code]

    # 1. Find filing date
    filing_date, receipt_no = find_filing(ticker, corp_code, year, report_code, timeout, retry_count)
    if not filing_date:
        return None, "skip: no report"

    # dataAvailableDate = filingDate + 1 day
    fd = datetime.strptime(filing_date, "%Y-%m-%d")
    data_available_date = (fd + timedelta(days=1)).strftime("%Y-%m-%d")

    # 2. Fetch financial statement — try consolidated first
    consolidated = "consolidated"
    accounts = fetch_finstate(corp_code, year, report_code, "CFS", timeout, retry_count)

    if not accounts:
        accounts = fetch_finstate(corp_code, year, report_code, "OFS", timeout, retry_count)
        consolidated = "separate"

    if not accounts:
        return None, "skip: no data in filing"

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

    # For interim filings (Q1 / H1 / Q3) the TTM derivation in
    # fundamental_ttm.py expects income statement and cash flow values to be
    # CUMULATIVE YTD so it can compute single-quarter values by subtracting
    # the prior cumulative window. DART exposes the cumulative-since-start-
    # of-fiscal-year amount as `thstrm_add_amount`; `thstrm_amount` on
    # interim filings is just the current-period amount and is not what we
    # want for income/CF. Balance sheet rows are point-in-time, so we always
    # use `thstrm_amount` for them.
    is_interim = report_code in ("11012", "11013", "11014")

    # Helper: extract the right amount column for a line item.
    def _item_value(item):
        sj_div = str(item.get("sj_div") or "").upper()
        is_balance_sheet = sj_div in BS_DIVS
        if is_interim and not is_balance_sheet:
            v = parse_amount(item.get("thstrm_add_amount"))
            if v is None:
                v = parse_amount(item.get("thstrm_amount"))
            return v
        return parse_amount(item.get("thstrm_amount"))

    for item in accounts:
        account_name = str(item.get("account_nm", "")).strip()
        field = _lookup_field(account_name)
        if field is None:
            continue
        # Statement-type filter: skip rows whose sj_div doesn't match the
        # statement(s) this field is supposed to come from. Prevents the
        # SCE 당기순이익 = 0 contamination that was zeroing net_income for
        # ~1500 stocks including all major chaebols.
        sj_div = str(item.get("sj_div") or "").upper()
        if not _sj_div_ok(field, sj_div):
            continue
        val = _item_value(item)
        if val is not None:
            result[field] = val

    # ----- Priority resolution -----
    # For fields with ACCOUNT_PRIORITY lists, walk all items, collect
    # any value tagged with a priority concept, then pick the highest-
    # priority (lowest list-index) one. Overwrites whatever the simple
    # ACCOUNT_MAP loop above wrote, so the more precise concept wins.
    for target_field, priority_list in ACCOUNT_PRIORITY.items():
        # Whitespace + Unicode-normalised priority list for tolerant matching.
        priority_norm = [_normalize_account_name(p) for p in priority_list]
        best_idx = None
        best_val = None
        for item in accounts:
            name = str(item.get("account_nm", "")).strip()
            name_norm = _normalize_account_name(name)
            if name_norm not in priority_norm:
                continue
            sj_div = str(item.get("sj_div") or "").upper()
            if not _sj_div_ok(target_field, sj_div):
                continue
            idx = priority_norm.index(name_norm)
            if best_idx is not None and idx >= best_idx:
                # already have a more-or-equally-precise candidate
                continue
            v = _item_value(item)
            if v is None:
                continue
            best_idx = idx
            best_val = v
        if best_val is not None:
            result[target_field] = best_val

    # Derived fields
    if "revenue" in result and "cost_of_revenue" in result:
        result.setdefault("gross_profit", result["revenue"] - result["cost_of_revenue"])
    if "total_assets" in result and "total_liabilities" in result:
        result.setdefault("total_equity", result["total_assets"] - result["total_liabilities"])

    if "total_debt" not in result:
        # Sum EVERY interest-bearing borrowing line on the balance sheet (read
        # straight from the raw accounts, so multiple borrowing/bond lines add
        # up instead of overwriting one another).
        debt_sum = 0
        found_borrowing = False
        for item in accounts:
            sj = str(item.get("sj_div") or "").upper()
            if sj and sj not in BS_DIVS:
                continue
            name = _normalize_account_name(str(item.get("account_nm", "")))
            if name in BORROWING_LABELS_NORM:
                v = _item_value(item)
                if v is not None:
                    debt_sum += v
                    found_borrowing = True
        if found_borrowing:
            result["total_debt"] = debt_sum
        else:
            # No borrowing line items on a balance sheet we clearly parsed in
            # detail, and no genuine interest expense -> treat as unlevered and
            # record an explicit 0 (so Debt/Equity scores it well). Otherwise
            # leave NULL = unknown -> excluded from the factor, never fabricated.
            bs_detailed = (
                "total_assets" in result
                and "total_liabilities" in result
                and "current_liabilities" in result
            )
            no_interest = not result.get("interest_expense")
            if bs_detailed and no_interest:
                result["total_debt"] = 0

    if "capex" not in result:
        capex = 0
        for k in ("capex_tangible", "capex_intangible"):
            if k in result:
                capex += result[k]
        if capex != 0:
            result["capex"] = capex

    # Mirror the internal `capex` field into the DB column name. The upsert
    # reads `capital_expenditure`, not `capex`, so without this copy capex
    # silently never reaches the database. (It was already used to derive
    # free_cash_flow above, which is why FCF appears but capital_expenditure
    # stays NULL — a quiet bug introduced when capex aggregation was added.)
    if "capital_expenditure" not in result and "capex" in result:
        result["capital_expenditure"] = result["capex"]

    if "free_cash_flow" not in result:
        if "operating_cash_flow" in result and ("capex" in result or "capital_expenditure" in result):
            ocf = result.get("operating_cash_flow", 0)
            capex_val = result.get("capex") or result.get("capital_expenditure", 0)
            result["free_cash_flow"] = ocf - abs(capex_val)

    if "ebitda" not in result:
        ebitda = 0
        for k in ("operating_income", "depreciation", "amortization"):
            if k in result:
                ebitda += result[k]
        if ebitda != 0:
            result["ebitda"] = ebitda

    # Shares outstanding (and the per-share metrics that depend on it) aren't
    # in the financial statements — pull from the stock-total-count disclosure.
    if not result.get("shares_outstanding"):
        shares = fetch_shares_outstanding(corp_code, year, report_code, timeout, retry_count)
        if shares:
            result["shares_outstanding"] = shares

    # Derive EPS and book value per share when the filing didn't report them
    # directly but we now have a share count. EPS uses period net income (a
    # close enough proxy for the weighted-average-based figure for ranking);
    # only fills when missing, so a reported EPS is never overwritten.
    shares_out = result.get("shares_outstanding")
    if shares_out and shares_out > 0:
        # Prefer owners-of-parent figures (exclude minority interest); fall back
        # to the consolidated totals when the attributable line isn't reported.
        ni = result.get("net_income_owners")
        if ni is None:
            ni = result.get("net_income")
        eq = result.get("equity_owners")
        if eq is None:
            eq = result.get("total_equity")
        if not result.get("eps") and ni is not None:
            result["eps"] = ni / shares_out
        if not result.get("book_value_per_share") and eq is not None:
            result["book_value_per_share"] = eq / shares_out

    field_count = sum(1 for k in result if k not in (
        "ticker", "period_end", "filing_date", "data_available_date",
        "fiscal_year", "fiscal_quarter", "statement_type",
        "consolidated_or_separate", "source", "receipt_no"))

    return result, "ok: {} fields ({})".format(field_count, consolidated)


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
        dividends_paid = COALESCE(EXCLUDED.dividends_paid, financial_statements.dividends_paid),
        book_value_per_share = COALESCE(EXCLUDED.book_value_per_share, financial_statements.book_value_per_share),
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

    type_to_code = {
        "annual": "11011",
        "Q1": "11013",
        "Q2": "11012",
        "Q3": "11014",
    }
    return {(r[0], r[1], type_to_code.get(r[2], r[2])) for r in results}


# Map the human-readable report label back to the API report_code.
_LABEL_TO_REPORT_CODE = {
    "Annual": "11011",
    "Q1": "11013",
    "Q2 Semi-annual": "11012",
    "Q3": "11014",
}

# Match a single ingest-log line ending in "skip: no data" or
# "skip: no report". Anchored to the bracketed counter at the start so we
# don't accidentally match a stray Korean ticker name containing the word
# "skip". Tested against the real log format:
#   [34/107976] 000030 우리은행 / 2015 / Q1 -> skip: no data in filing (4.0s)
#   [35/107976] 000030 우리은행 / 2015 / Q3 -> skip: no report (1.6s)
_LOG_EMPTY_LINE_RE = re.compile(
    # The display_name segment between ticker and first `/` may be empty
    # for delisted stocks with no Korean name on file, so we accept any
    # non-`/` content (including just whitespace).
    r"\[\d+/\d+\]\s+(\d{6})\s+[^/]*?/\s*(\d{4})\s*/\s*"
    r"(Annual|Q1|Q2 Semi-annual|Q3)\s*->\s*skip:\s+no\s+(?:data|report)"
)


def load_known_empty_from_logs(log_dir):
    """Scan dart_*.log files in `log_dir` for past "no data" / "no report"
    entries. Returns a set of (ticker, year, report_code) tuples that we
    can confidently skip on resume — DART has already told us these are
    empty.

    Files older than 30 days are still scanned (DART filings don't move
    once filed), so this is a near-permanent negative cache. The cost of
    a false positive (skipping a filing that secretly does have data now)
    is negligible: it just means we'd miss one filing until a fresh full
    run without --skip-existing. Users can force a re-check by deleting
    the log files.
    """
    import glob
    known = set()
    log_pattern = os.path.join(log_dir, "dart_*.log")
    log_files = sorted(glob.glob(log_pattern))
    if not log_files:
        return known
    for path in log_files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m = _LOG_EMPTY_LINE_RE.search(line)
                    if not m:
                        continue
                    ticker, year_s, label = m.group(1), m.group(2), m.group(3)
                    code = _LABEL_TO_REPORT_CODE.get(label)
                    if code:
                        known.add((ticker, int(year_s), code))
        except OSError:
            continue
    return known


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest DART financial statements")
    parser.add_argument("--tickers", help="Comma-separated tickers (e.g. 005930,000660)")
    parser.add_argument("--ticker", help="Single ticker (legacy, prefer --tickers)")
    parser.add_argument("--universe", help="Use named universe from universe_memberships table")
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
    parser.add_argument("--refresh-corp-codes", action="store_true",
                        help="Force re-download of corpCode.xml (ignoring cache)")
    parser.add_argument(
        "--debug-dart-report", action="store_true",
        help="Diagnostic mode: for each (ticker, year, report_code) print the "
             "exact OpenDART list / fnlttSinglAcntAll calls, response status, "
             "raw rows, presence of thstrm_amount / thstrm_add_amount, and "
             "compare the current 'find_filing -> fetch_finstate' path against "
             "a direct fnlttSinglAcntAll call. Does not write to the DB. "
             "Use --ticker / --tickers, --year, --reports.",
    )
    args = parser.parse_args()

    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set.")
        sys.exit(1)

    if not DART_API_KEY:
        print("DART_API_KEY not set. Skipping DART ingestion.")
        sys.exit(0)

    run_start_time = time.time()
    # All DB access in this script goes through connect_db() so TCP keepalives
    # are set; long skip streaks would otherwise silently kill the connection.
    conn = connect_db()

    # --- Resolve tickers ---
    ticker_names = {}
    if args.tickers:
        tickers = args.tickers.split(",")
    elif args.ticker:
        tickers = [args.ticker]
    elif args.universe:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM universe_memberships WHERE universe_name = %s ORDER BY ticker", (args.universe,))
        tickers = [r[0] for r in cur.fetchall()]
        cur.close()
        if not tickers:
            print("ERROR: Universe '{}' not found or empty".format(args.universe), flush=True)
            sys.exit(1)
        print("  Universe '{}': {} tickers".format(args.universe, len(tickers)), flush=True)
    else:
        rows = get_active_tickers_with_names(conn)
        tickers = [r[0] for r in rows]
        ticker_names = {r[0]: r[1] for r in rows}
        if args.limit:
            tickers = tickers[:args.limit]

    # Look up names if we don't have them yet
    if tickers and not ticker_names:
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(tickers))
        cur.execute("SELECT ticker, name FROM stocks WHERE ticker IN ({})".format(placeholders), tickers)
        for row in cur.fetchall():
            ticker_names[row[0]] = row[1]
        cur.close()

    # ===================================================================
    # STEP 1: Corp Code Resolution
    # ===================================================================
    print(flush=True)
    print("=" * 60, flush=True)
    print("  STEP 1: Corp Code Resolution", flush=True)
    print("=" * 60, flush=True)
    corp_code_map = download_corp_codes(args.timeout, force_refresh=args.refresh_corp_codes)

    ticker_corp = {}
    missing_corp_tickers = []
    known_hit = 0
    xml_hit = 0
    for t in tickers:
        if t in KNOWN_CORP_CODES:
            ticker_corp[t] = KNOWN_CORP_CODES[t]
            known_hit += 1
        elif t in corp_code_map:
            ticker_corp[t] = corp_code_map[t]
            xml_hit += 1
        else:
            missing_corp_tickers.append(t)

    print(flush=True)
    print("  Corp code summary:", flush=True)
    print("    Requested tickers:    {}".format(len(tickers)), flush=True)
    print("    Matched (hardcoded):  {}".format(known_hit), flush=True)
    print("    Matched (XML cache):  {}".format(xml_hit), flush=True)
    print("    Total with corp code: {}".format(len(ticker_corp)), flush=True)
    print("    Missing corp code:    {}".format(len(missing_corp_tickers)), flush=True)
    if missing_corp_tickers:
        preview = missing_corp_tickers[:10]
        preview_str = ", ".join("{} ({})".format(t, ticker_names.get(t, "?")[:15]) for t in preview)
        if len(missing_corp_tickers) > 10:
            preview_str += " ... +{} more".format(len(missing_corp_tickers) - 10)
        print("    Missing: {}".format(preview_str), flush=True)

    # If --refresh-corp-codes with --dry-run, stop here
    if args.refresh_corp_codes and args.dry_run:
        print(flush=True)
        print("Corp-code refresh complete (dry-run). Exiting.", flush=True)
        conn.close()
        sys.exit(0)

    if not ticker_corp:
        print("No tickers with known DART corp codes. Nothing to do.", flush=True)
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
        print("  No --year given. Defaulting to FY {}".format(default_year), flush=True)

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

    # ---------------- DEBUG MODE: per-report deep-dive ----------------
    # Runs the diagnostic helper for every (ticker, year, report_code)
    # combination. Does NOT write to the database. Exits before any normal
    # ingestion logic so the operator can read the verdict cleanly.
    if args.debug_dart_report:
        print()
        print("=" * 78)
        print("DART DEBUG MODE  (no writes to financial_statements)")
        print("  Tickers: {0}".format(", ".join(ticker_corp.keys())))
        print("  Years:   {0}".format(years))
        print("  Reports: {0}".format(report_codes))
        print("=" * 78)
        for t in ticker_corp:
            for y in years:
                for rc in report_codes:
                    debug_one_report(
                        t, ticker_corp[t], y, rc,
                        args.timeout, args.retry,
                    )
                    time.sleep(args.rate_limit)
        conn.close()
        sys.exit(0)

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
        # Also load "known empty" combos from past log files. These are
        # filings that previous runs already confirmed have no parseable
        # data in DART (either "no report" or "no data in filing"). Since
        # they never landed in financial_statements, --skip-existing alone
        # would re-ask DART on every restart, wasting ~3-4 sec per filing.
        # Treating them as already-done saves ~20 hours of catch-up time
        # per restart for a full quarterly run.
        known_empty = load_known_empty_from_logs(os.path.dirname(__file__))
        if known_empty:
            before = len(already_ingested)
            already_ingested |= known_empty
            added = len(already_ingested) - before
            print("  Loaded {0} known-empty combos from past logs "
                  "({1} new beyond DB rows)".format(
                      len(known_empty), added), flush=True)

    log_id = log_start(conn, {
        "tickers": ",".join(active_tickers[:20]),
        "ticker_count": len(active_tickers),
        "years": years,
        "reports": report_codes,
        "timeout": timeout_secs,
        "rate_limit": rate_limit,
        "retry": retry_count,
        "dry_run": dry_run,
        "resume": resume,
    })

    # Counters
    cnt_processed = 0
    cnt_ok = 0
    cnt_skipped_existing = 0
    cnt_no_report = 0
    cnt_no_data = 0
    cnt_errors = 0
    cnt_inserted = 0
    cnt_api_calls = 0

    try:
        # ===================================================================
        # STEP 2: DART Financial Statement Ingestion
        # ===================================================================
        print(flush=True)
        print("=" * 60, flush=True)
        print("  STEP 2: DART Financial Statement Ingestion", flush=True)
        print("=" * 60, flush=True)
        print("  Tickers:        {} with corp codes".format(len(active_tickers)), flush=True)
        print("  Years:          {}".format(", ".join(str(y) for y in years)), flush=True)
        print("  Reports:        {}".format(
            ", ".join("{} ({})".format(rc, REPORT_TYPES[rc]["label"]) for rc in report_codes)), flush=True)
        print("  Expected calls: ~{}  (ticker x year x report)".format(total_calls), flush=True)
        if resume:
            remaining = total_calls - len(already_ingested)
            print("  Already done:   {} (will skip)".format(len(already_ingested)), flush=True)
            print("  Remaining:      ~{}".format(max(0, remaining)), flush=True)
        print("  Timeout:        {}s per request".format(timeout_secs), flush=True)
        print("  Rate limit:     {}s between calls".format(rate_limit), flush=True)
        print("  Retries:        {} per failed request".format(retry_count), flush=True)
        if dry_run:
            print("  MODE:           *** DRY RUN (no API calls) ***", flush=True)
        print("-" * 60, flush=True)
        print(flush=True)

        records = []
        call_number = 0

        for i, ticker in enumerate(active_tickers):
            corp_code = ticker_corp[ticker]
            name = ticker_names.get(ticker, "")
            display_name = name[:20] if name else ""

            for year in years:
                for report_code in report_codes:
                    call_number += 1
                    label = REPORT_TYPES[report_code]["label"]
                    prefix = "[{}/{}]".format(call_number, total_calls)

                    # Skip if already ingested
                    if (ticker, year, report_code) in already_ingested:
                        cnt_skipped_existing += 1
                        print("  {} {} {} / {} / {} -> skip: existing".format(
                            prefix, ticker, display_name, year, label), flush=True)
                        # Long skip streaks can leave the connection idle long
                        # enough for the load balancer to drop it. Verify
                        # liveness every 500 skips so the next real upsert
                        # doesn't crash.
                        if cnt_skipped_existing % 500 == 0:
                            conn = ensure_connection(conn, verify=True)
                        continue

                    cnt_processed += 1

                    if dry_run:
                        print("  {} {} {} / {} / {} -> dry-run".format(
                            prefix, ticker, display_name, year, label), flush=True)
                        continue

                    # Print progress (result appended inline)
                    print("  {} {} {} / {} / {} -> ".format(
                        prefix, ticker, display_name, year, label),
                        end="", flush=True)

                    try:
                        cnt_api_calls += 1
                        t0 = time.time()
                        result, status_msg = fetch_financials(
                            ticker, corp_code, year, report_code,
                            timeout_secs, retry_count)
                        elapsed = time.time() - t0

                        if result:
                            cnt_ok += 1
                            records.append(result)
                            try:
                                conn, _ = db_write_with_retry(
                                    conn, upsert_dart_filing, result)
                            except _CONN_DEAD_ERRORS as db_err:
                                # Reconnect + one retry already failed inside
                                # db_write_with_retry. Log it (best-effort)
                                # and keep going so other tickers still run.
                                cnt_errors += 1
                                print(" db error after retry: {0}".format(
                                    str(db_err).strip()[:80]), flush=True)
                                try:
                                    conn, _ = db_write_with_retry(
                                        conn, log_ingestion_error,
                                        ticker, "db_write_error",
                                        str(db_err)[:200],
                                        {"year": year, "report_code": report_code})
                                except Exception as log_err:
                                    print("  WARN: log_ingestion_error failed: "
                                          "{0}".format(str(log_err)[:80]),
                                          flush=True)
                            else:
                                print("{} ({:.1f}s)".format(status_msg, elapsed),
                                      flush=True)
                        else:
                            if "no report" in status_msg:
                                cnt_no_report += 1
                            else:
                                cnt_no_data += 1
                            print("{} ({:.1f}s)".format(status_msg, elapsed), flush=True)

                    except Exception as e:
                        cnt_errors += 1
                        error_msg = str(e)[:80]
                        print("error: {}".format(error_msg), flush=True)
                        # Use the resilient wrapper so a connection-drop in
                        # the middle of error logging doesn't cascade.
                        try:
                            conn, _ = db_write_with_retry(
                                conn, log_ingestion_error,
                                ticker, "fetch_error", error_msg,
                                {"year": year, "report_code": report_code})
                        except Exception as log_err:
                            print("  WARN: log_ingestion_error failed: {0}".format(
                                str(log_err)[:80]), flush=True)

                    time.sleep(rate_limit)

            # Batch upsert every 50 records
            if len(records) >= 50:
                conn, n = db_write_with_retry(
                    conn, upsert_financials, records)
                cnt_inserted += n
                print("  -> Batch upsert: {} records".format(n), flush=True)
                records = []

        # Final upsert
        if records and not dry_run:
            conn, n = db_write_with_retry(
                conn, upsert_financials, records)
            cnt_inserted += n
            print("  -> Final upsert: {} records".format(n), flush=True)

        # Update factor_coverage with new P123 factor IDs
        if cnt_inserted > 0:
            def _update_factor_coverage(c):
                cur = c.cursor()
                cur.execute("""
                    UPDATE factor_coverage
                    SET data_status = 'real', is_available = TRUE,
                        uses_mock_data = FALSE, point_in_time_safe = TRUE,
                        preferred_source = 'dart', last_updated = NOW()
                    WHERE factor_id IN (
                        'pe_ttm_inv', 'price_book', 'price_sales_ttm_inv', 'fcf_mcap',
                        'ebitda_ev', 'roe_ttm', 'roa_ttm', 'gross_profit_assets',
                        'operating_margin_ttm', 'debt_to_equity', 'interest_coverage_ttm',
                        'sales_growth_yoy', 'eps_growth_yoy', 'op_income_growth_yoy',
                        'gross_margin_ttm', 'asset_turnover_ttm', 'ocf_mcap', 'ufcf_ev',
                        'ev_sales_ttm_inv', 'gross_profit_ev', 'dividend_yield',
                        'net_income_growth_yoy'
                    )
                """)
                c.commit()
                cur.close()
            conn, _ = db_write_with_retry(conn, _update_factor_coverage)

        try:
            conn, _ = db_write_with_retry(
                conn, log_finish, log_id, "success",
                rows_processed=cnt_processed,
                rows_inserted=cnt_inserted,
                rows_skipped=cnt_skipped_existing)
        except Exception as log_err:
            print("  WARN: failed to write ingestion_log success row: {0}".format(
                str(log_err)[:80]), flush=True)

    except Exception as e:
        # The fatal handler MUST never propagate a connection-loss error
        # from the logging step itself — otherwise the operator sees the
        # logging failure and not the original cause. Reconnect first, log
        # best-effort, then re-raise the original.
        try:
            conn, _ = db_write_with_retry(
                conn, log_finish, log_id, "error",
                error_message=str(e),
                rows_processed=cnt_processed,
                rows_inserted=cnt_inserted,
                rows_skipped=cnt_skipped_existing)
        except Exception as log_err:
            print("  WARN: failed to write ingestion_log error row: {0}".format(
                str(log_err)[:80]), flush=True)
        print("FATAL ERROR: {}".format(e), flush=True)
        raise
    finally:
        try:
            if conn is not None and not getattr(conn, "closed", 0):
                conn.close()
        except Exception:
            pass

    # ===================================================================
    # SUMMARY
    # ===================================================================
    run_elapsed = time.time() - run_start_time

    print(flush=True)
    print("=" * 60, flush=True)
    print("  SUMMARY", flush=True)
    print("=" * 60, flush=True)
    print("  Requested tickers:   {}".format(len(tickers)), flush=True)
    print("  Matched corp codes:  {}".format(len(ticker_corp)), flush=True)
    print("  Missing corp codes:  {}".format(len(missing_corp_tickers)), flush=True)
    print("  -" * 30, flush=True)
    print("  API calls attempted: {}".format(cnt_api_calls), flush=True)
    print("  Successful (ok):     {}".format(cnt_ok), flush=True)
    print("  Skipped (existing):  {}".format(cnt_skipped_existing), flush=True)
    print("  Skipped (no report): {}".format(cnt_no_report), flush=True)
    print("  Skipped (no data):   {}".format(cnt_no_data), flush=True)
    print("  Errors:              {}".format(cnt_errors), flush=True)
    print("  Rows inserted:       {}".format(cnt_inserted), flush=True)
    print("  -" * 30, flush=True)
    print("  Elapsed time:        {}".format(format_elapsed(run_elapsed)), flush=True)
    print("=" * 60, flush=True)
