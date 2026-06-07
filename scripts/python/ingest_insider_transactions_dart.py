"""
Ingest insider transactions from DART's `elestock.json` endpoint
(임원·주요주주 특정증권등 소유상황 보고서).

Every executive, board member, or 10%+ shareholder of a Korean listed
corp is required to disclose changes in their company's stock holdings
within 5 business days. This is the official Korean equivalent of an
SEC Form 4 and the canonical source for "insider net-buying" sentiment
signals.

Schema target: `insider_transactions` (see scripts/sql/008_insider_transactions.sql).
Auto-creates the table if it doesn't already exist.

Usage:
    python ingest_insider_transactions_dart.py
    python ingest_insider_transactions_dart.py --universe krx_all_historical
    python ingest_insider_transactions_dart.py --bgn 20150101 --end 20261231
    python ingest_insider_transactions_dart.py --dry-run

Notes:
  * Default date range is 2010-01-01 → today, covering everything for the
    10-year backtest plus older history if you want to extend.
  * DART rate limit: nominal 10K/day, in practice closer to 20K. Total
    call budget here is ~3,400 corps × ~1-3 pages each ≈ 4,000-8,000
    calls = 1-2 hours runtime.
  * Idempotent: ON CONFLICT DO NOTHING on a composite unique key. Re-runs
    pick up new filings without duplicating.
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error
from datetime import date

import psycopg2
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL2") or os.getenv("DATABASE_URL")
DART_API_KEY = os.getenv("DART_API_KEY") or os.getenv("OPENDART_API_KEY")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set."); sys.exit(1)
if not DART_API_KEY:
    print("ERROR: DART_API_KEY not set."); sys.exit(1)

SCRIPT_NAME = "ingest_insider_transactions_dart"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, ".cache")
DART_CORP_CODES_CACHE = os.path.join(CACHE_DIR, "dart_corp_codes.json")

DART_ELESTOCK_URL = "https://opendart.fss.or.kr/api/elestock.json"
RATE_LIMIT_SEC = 0.3
TIMEOUT_SEC = 30
DEFAULT_PAGE_COUNT = 100   # DART max per page


# ---------------------------------------------------------------------------
# Schema bootstrap (creates table on first run, no-op afterwards)
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS insider_transactions (
    id                BIGSERIAL PRIMARY KEY,
    ticker            VARCHAR(10) NOT NULL,
    receipt_no        VARCHAR(30) NOT NULL,
    filing_date       DATE        NOT NULL,
    transaction_date  DATE,
    filer_name        TEXT,
    filer_role        TEXT,
    officer_title     TEXT,
    is_main_shrholdr  BOOLEAN,
    relation          TEXT,
    stock_type        TEXT,
    share_change      BIGINT,
    share_balance_after BIGINT,
    change_reason     TEXT,
    source            VARCHAR(20) DEFAULT 'dart',
    created_at        TIMESTAMP   DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS insider_dedup_idx
    ON insider_transactions
    (ticker, receipt_no, filer_name, transaction_date, share_change);

CREATE INDEX IF NOT EXISTS insider_ticker_txn_date_idx
    ON insider_transactions (ticker, transaction_date);
CREATE INDEX IF NOT EXISTS insider_filing_date_idx
    ON insider_transactions (filing_date);
CREATE INDEX IF NOT EXISTS insider_ticker_filing_date_idx
    ON insider_transactions (ticker, filing_date);
"""


def ensure_table(conn):
    cur = conn.cursor()
    cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_start(conn, params=None):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ingestion_log (script_name, parameters) "
        "VALUES (%s, %s) RETURNING id",
        (SCRIPT_NAME, Json(params)),
    )
    log_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    return log_id


def log_finish(conn, log_id, status, rows_processed=0, rows_inserted=0,
               rows_skipped=0, error_message=None):
    try:
        conn.rollback()
    except Exception:
        pass
    cur = conn.cursor()
    cur.execute(
        "UPDATE ingestion_log "
        "SET finished_at = NOW(), status = %s, "
        "rows_processed = %s, rows_inserted = %s, rows_skipped = %s, "
        "error_message = %s WHERE id = %s",
        (status, rows_processed, rows_inserted, rows_skipped,
         error_message, log_id),
    )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_corp_code_cache():
    if not os.path.exists(DART_CORP_CODES_CACHE):
        print(f"ERROR: corp code cache not found at {DART_CORP_CODES_CACHE}")
        print("  Run ingest_dart.py once to populate the cache, then retry.")
        sys.exit(1)
    with open(DART_CORP_CODES_CACHE, "r") as f:
        return json.load(f)


def parse_dart_date(s):
    """Parse 'YYYYMMDD' or 'YYYY-MM-DD' to date or None."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip().replace("-", "").replace(".", "").replace("/", "")
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, TypeError):
        return None


def parse_share_change(s):
    """Parse '−1,234' / '+5,678' / '-100' / etc. to signed integer."""
    if s is None or s == "" or s == "-":
        return None
    s = str(s).replace(",", "").strip()
    # DART uses several minus-sign variants in returned JSON
    s = s.replace("−", "-").replace("－", "-").replace("‒", "-")
    if s == "" or s == "-":
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def fetch_page(corp_code, page_no, bgn_de, end_de, retry=2):
    """One paged call against DART elestock.json. Returns dict or None."""
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bgn_de": bgn_de,
        "end_de": end_de,
        "page_no": page_no,
        "page_count": DEFAULT_PAGE_COUNT,
    }
    url = DART_ELESTOCK_URL + "?" + "&".join(
        f"{k}={v}" for k, v in params.items()
    )
    for attempt in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            if attempt < retry:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None
        except Exception:
            return None
    return None


def normalize_row(ticker, row):
    """Coerce a single DART row to our insider_transactions schema.
    Returns the upsert tuple or None on parse failure.
    """
    receipt_no = (row.get("rcept_no") or "").strip()
    if not receipt_no:
        return None
    # Filing date is usually encoded in the first 8 chars of receipt_no
    filing_date = parse_dart_date(receipt_no[:8])
    if filing_date is None:
        return None
    # Real transaction date — try a few field names DART has used over time
    txn_date = (parse_dart_date(row.get("trd_dt"))
                or parse_dart_date(row.get("trad_dt"))
                or parse_dart_date(row.get("rceipt_dt"))
                or None)
    filer_name = (row.get("repror") or "").strip() or None
    is_exec_registered = (row.get("isu_exctv_rgist_at") or "").strip()
    is_exec_registered = is_exec_registered.upper() if is_exec_registered else None
    officer_title = (row.get("isu_exctv_ofcps") or "").strip() or None
    is_main = (row.get("isu_main_shrholdr") or "").strip()
    is_main_bool = (True if is_main and is_main[:1].upper() == "Y"
                    else False if is_main else None)
    relation = (row.get("nm") or "").strip() or None  # filer relationship
    stock_type = (row.get("stk_etc") or row.get("sp_stock_lmp_etc") or "").strip() or None
    share_change = parse_share_change(row.get("sp_stock_lmp_irds_cnt"))
    share_balance_after = parse_share_change(row.get("sp_stock_lmp_cnt"))
    change_reason = (row.get("chg_rsn") or row.get("etc") or "").strip() or None

    return (
        ticker,
        receipt_no,
        filing_date,
        txn_date,
        filer_name,
        is_exec_registered,
        officer_title,
        is_main_bool,
        relation,
        stock_type,
        share_change,
        share_balance_after,
        change_reason,
    )


def upsert_rows(conn, rows):
    if not rows:
        return 0
    cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO insider_transactions
            (ticker, receipt_no, filing_date, transaction_date,
             filer_name, filer_role, officer_title, is_main_shrholdr,
             relation, stock_type, share_change, share_balance_after,
             change_reason)
        VALUES %s
        ON CONFLICT (ticker, receipt_no, filer_name, transaction_date, share_change)
        DO NOTHING
    """, rows)
    n = cur.rowcount
    conn.commit()
    cur.close()
    return n


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe",
                        help="Restrict to a named universe (default: all "
                             "stocks with corp_code cache entries).")
    parser.add_argument("--bgn", default="20100101",
                        help="Start date YYYYMMDD (default: 20100101).")
    parser.add_argument("--end", default=date.today().strftime("%Y%m%d"),
                        help="End date YYYYMMDD (default: today).")
    parser.add_argument("--limit", type=int,
                        help="Cap tickers processed (for testing).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't write to DB; just count filings per ticker.")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    ensure_table(conn)
    corp_code_map = load_corp_code_cache()

    # Build target ticker list
    if args.universe:
        cur = conn.cursor()
        cur.execute(
            "SELECT ticker FROM universe_memberships WHERE universe_name = %s ORDER BY ticker",
            (args.universe,))
        tickers = [r[0] for r in cur.fetchall()]
        cur.close()
        if not tickers:
            print(f"ERROR: universe '{args.universe}' empty or not found.")
            sys.exit(1)
        print(f"  Universe '{args.universe}': {len(tickers)} tickers")
    else:
        tickers = sorted(corp_code_map.keys())
        print(f"  All tickers with corp_code: {len(tickers)}")

    if args.limit:
        tickers = tickers[:args.limit]
        print(f"  Limited to first {len(tickers)}")

    log_id = log_start(conn, {
        "universe": args.universe,
        "bgn": args.bgn, "end": args.end,
        "limit": args.limit, "dry_run": args.dry_run,
    })

    n_total_rows = 0
    n_inserted = 0
    n_no_corp = 0
    n_api_errors = 0
    n_empty = 0
    n_tickers_with_data = 0

    print()
    print("=" * 70)
    print(f"DART insider-transactions ingest")
    print(f"  Date range: {args.bgn} → {args.end}")
    print(f"  Rate limit: {RATE_LIMIT_SEC}s between API calls")
    print(f"  ETA at 1 call/ticker: ~{int(len(tickers) * RATE_LIMIT_SEC / 60)} min "
          "(longer for tickers needing pagination)")
    print("=" * 70)
    print()

    try:
        for i, ticker in enumerate(tickers, 1):
            corp = corp_code_map.get(ticker)
            if not corp:
                n_no_corp += 1
                continue

            # Page through results until empty
            page_no = 1
            ticker_rows = []
            while True:
                data = fetch_page(corp, page_no, args.bgn, args.end)
                time.sleep(RATE_LIMIT_SEC)
                if data is None:
                    n_api_errors += 1
                    break
                status = data.get("status", "")
                # 013 = no data; 000 = success
                if status == "013":
                    break
                if status != "000":
                    # Some other DART error (rate limit, etc.) — stop and move on
                    n_api_errors += 1
                    break
                page_rows = data.get("list", []) or []
                if not page_rows:
                    break
                for r in page_rows:
                    rec = normalize_row(ticker, r)
                    if rec is not None:
                        ticker_rows.append(rec)
                # Last page?
                total = data.get("total_count", 0)
                this_page_rows = len(page_rows)
                seen_so_far = page_no * DEFAULT_PAGE_COUNT
                if this_page_rows < DEFAULT_PAGE_COUNT or seen_so_far >= total:
                    break
                page_no += 1

            if ticker_rows:
                n_tickers_with_data += 1
                n_total_rows += len(ticker_rows)
                if not args.dry_run:
                    inserted = upsert_rows(conn, ticker_rows)
                    n_inserted += inserted
            else:
                n_empty += 1

            if i % 50 == 0 or i == len(tickers):
                print(f"  [{i:>4}/{len(tickers)}] processed; "
                      f"{n_tickers_with_data} tickers had filings, "
                      f"{n_total_rows:,} rows parsed, "
                      f"{n_inserted:,} new inserted", flush=True)
    except KeyboardInterrupt:
        log_finish(conn, log_id, "interrupted",
                   rows_processed=n_total_rows, rows_inserted=n_inserted,
                   error_message="user cancelled")
        print("\n[INTERRUPTED]")
        sys.exit(1)
    except Exception as e:
        log_finish(conn, log_id, "error",
                   rows_processed=n_total_rows, rows_inserted=n_inserted,
                   error_message=str(e))
        print(f"\n[ERROR] {e}")
        raise

    log_finish(conn, log_id, "success",
               rows_processed=n_total_rows, rows_inserted=n_inserted,
               rows_skipped=n_total_rows - n_inserted)

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Tickers processed:         {len(tickers)}")
    print(f"    with insider filings:     {n_tickers_with_data}")
    print(f"    no DART corp_code:        {n_no_corp}")
    print(f"    no filings in range:      {n_empty}")
    print(f"    API errors:               {n_api_errors}")
    print(f"  Insider-filing rows parsed: {n_total_rows:,}")
    print(f"  New rows inserted:          {n_inserted:,}")
    print("=" * 70)
    conn.close()


if __name__ == "__main__":
    main()
