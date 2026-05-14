"""
Ingest KRX short-selling data via the public data.krx.co.kr bulk
endpoint — no login required.

Why this exists:
    The original ingest_short_selling.py uses pykrx, which since
    version 1.0.51 requires KRX_ID / KRX_PW for its bulk APIs. That's
    a hurdle for non-Korean users. This script hits the same public
    KRX bulk endpoint that FinanceDataReader uses for prices/listings
    (data.krx.co.kr/comm/bldAttendant/getJsonData.cmd) with the right
    User-Agent + Referer headers. No authentication needed.

Data sources (two KRX bulk endpoints):
    1. dbms/MDC/STAT/srt/MDCSTAT30101  (개별종목 공매도 거래 전종목)
       Daily short-trading per stock for one market:
         CVSRTSELL_TRDVOL = short volume
         CVSRTSELL_TRDVAL = short value (KRW)
         ACC_TRDVOL       = total volume
         TRDVAL_WT        = short value weight (%)

    2. dbms/MDC/STAT/srt/MDCSTAT30501  (전종목 공매도 잔고)
       Daily short balance per stock for one market:
         BAL_QTY  = shares held short
         BAL_AMT  = balance amount (KRW)
         BAL_RTO  = balance ratio = BAL_QTY / LIST_SHRS * 100

Output is upserted into the short_selling table:
    short_volume         <- CVSRTSELL_TRDVOL
    short_value          <- CVSRTSELL_TRDVAL
    short_balance        <- BAL_QTY
    short_balance_value  <- BAL_AMT
    short_ratio          <- BAL_RTO
    source               = 'krx'

Note on history:
    Korean short selling was banned Nov 2023 - Mar 2025 for most stocks.
    Trading-data rows before Mar 2025 will be mostly zero. Balance rows
    can pre-date the trading ban (existing positions stayed on the
    books).

Usage:
    python ingest_short_selling_krx.py --date 2026-05-06
    python ingest_short_selling_krx.py --start-date 2025-04-01 \\
        --end-date 2026-05-06
    python ingest_short_selling_krx.py --date 2026-05-06 --markets KOSPI
    python ingest_short_selling_krx.py --date 2026-05-06 --dry-run
"""

import os
import sys
import argparse
import json
import time
from datetime import datetime, timedelta

import requests
import psycopg2
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "ingest_short_selling_krx"


# ---------------------------------------------------------------------------
# KRX endpoint constants (public, no auth)
# ---------------------------------------------------------------------------

KRX_BULK_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_WARMUP_URL = "https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd"

# Headers matched to what pykrx 1.0.51 sends. Critical fields:
#  - https Referer (not http; KRX redirects http and may then reject)
#  - X-Requested-With marks the call as XHR; some KRX endpoints reject
#    POSTs that don't carry this header
KRX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0.0.0 Safari/537.36",
    "Referer": KRX_WARMUP_URL,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://data.krx.co.kr",
}

BLD_TRADING = "dbms/MDC/STAT/srt/MDCSTAT30101"   # per-stock daily short trading
BLD_BALANCE = "dbms/MDC/STAT/srt/MDCSTAT30501"   # per-stock daily short balance

# Market codes differ between the two endpoints — KRX is not consistent.
TRADING_MKT_IDS = {"KOSPI": "STK", "KOSDAQ": "KSQ"}
BALANCE_MKT_IDS = {"KOSPI": 1,     "KOSDAQ": 2}


# ---------------------------------------------------------------------------
# Ingestion logging
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


def log_finish(conn, log_id, status, rows_processed=0,
               rows_inserted=0, rows_updated=0,
               rows_skipped=0, error_message=None):
    cur = conn.cursor()
    cur.execute(
        "UPDATE ingestion_log "
        "SET finished_at = NOW(), status = %s, "
        "rows_processed = %s, rows_inserted = %s, "
        "rows_updated = %s, rows_skipped = %s, "
        "error_message = %s "
        "WHERE id = %s",
        (status, rows_processed, rows_inserted,
         rows_updated, rows_skipped,
         error_message, log_id),
    )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_date(date_str):
    """YYYY-MM-DD -> YYYYMMDD"""
    return date_str.replace("-", "")


def to_iso(yyyymmdd):
    s = yyyymmdd.replace("-", "")
    return "{0}-{1}-{2}".format(s[:4], s[4:6], s[6:8])


def _parse_num(v):
    """Parse a KRX numeric string ('1,234' or '-' or '')."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s == "-":
        return None
    try:
        if "." in s:
            return float(s)
        return int(s)
    except (ValueError, TypeError):
        return None


def _extract_ticker(isu_cd):
    """KRX returns 12-char ISIN like 'KR7005930003'. Pull the 6-char
    ticker out (chars 3..9). For non-standard items return the raw."""
    if not isu_cd:
        return None
    s = str(isu_cd).strip()
    if len(s) == 12 and s.startswith("KR"):
        return s[3:9]
    if len(s) == 6:
        return s
    return s


# Module-level session: visit the warmup URL once to receive JSESSIONID
# and any anti-bot cookies KRX expects on subsequent POSTs. Subsequent
# requests reuse the same TCP/TLS connection (faster) and carry the
# session cookies (which KRX uses for some endpoints).
_session = None


def _get_session():
    global _session
    if _session is not None:
        return _session
    s = requests.Session()
    s.headers.update(KRX_HEADERS)
    try:
        s.get(KRX_WARMUP_URL, timeout=10)
    except requests.exceptions.RequestException:
        # Warmup is best-effort. If it fails, we'll still try the POST;
        # KRX often serves data without an established session.
        pass
    _session = s
    return s


def _post_krx_bulk(bld, payload, timeout=30, retry=2):
    """POST to the KRX bulk endpoint; return parsed JSON dict, or None.

    Retries up to `retry` times on network errors and empty responses.
    `bld` is the endpoint identifier; `payload` contains the per-call
    parameters (trdDd, mktId, etc). We do NOT add share/money/
    csvxls_isNo here — those are only sent by KRX's marcap-style
    endpoints and cause HTTP 400 on the srt (short-selling) endpoints.
    """
    sess = _get_session()
    data = {"bld": bld, **payload}
    last_err = None
    for attempt in range(retry + 1):
        try:
            r = sess.post(KRX_BULK_URL, data=data, timeout=timeout)
        except requests.exceptions.RequestException as e:
            last_err = str(e)
            time.sleep(0.5 * (attempt + 1))
            continue
        if r.status_code != 200:
            # Capture a snippet of the body for debugging KRX 400s.
            snippet = (r.text or "")[:200].replace("\n", " ").strip()
            last_err = "HTTP {0} (body: {1!r})".format(
                r.status_code, snippet)
            time.sleep(0.5 * (attempt + 1))
            continue
        text = (r.text or "").strip()
        if not text:
            last_err = "empty body"
            time.sleep(0.5 * (attempt + 1))
            continue
        try:
            j = json.loads(text)
        except json.JSONDecodeError as e:
            last_err = "JSON decode: {0} (body: {1!r})".format(
                str(e)[:80], text[:200])
            time.sleep(0.5 * (attempt + 1))
            continue
        return j
    if last_err:
        print("    KRX request failed ({0}): {1}".format(bld, last_err))
    return None


def fetch_trading(date_yyyymmdd, market):
    """Per-stock daily short trading for one market on one date.
    Returns list of dicts keyed by ticker, or [] on failure.

    Param set matches pykrx's 개별종목_공매도_거래_전종목.fetch() exactly
    (trdDd, mktId, inqCond). The srt endpoints reject the extra
    share/money/csvxls_isNo flags that other KRX endpoints accept.
    """
    mkt_id = TRADING_MKT_IDS[market]
    j = _post_krx_bulk(BLD_TRADING, {
        "trdDd": date_yyyymmdd,
        "mktId": mkt_id,
        "inqCond": "STMFRTSCIFDRFS",  # stocks only (not ETF/ETN/ELW)
    })
    if j is None:
        return []
    rows = j.get("OutBlock_1") or []
    out = []
    for r in rows:
        ticker = _extract_ticker(r.get("ISU_CD"))
        if not ticker:
            continue
        out.append({
            "ticker": ticker,
            "name": r.get("ISU_ABBRV"),
            "short_volume": _parse_num(r.get("CVSRTSELL_TRDVOL")),
            "short_value": _parse_num(r.get("CVSRTSELL_TRDVAL")),
        })
    return out


def fetch_balance(date_yyyymmdd, market):
    """Per-stock short balance for one market on one date.
    Returns list of dicts keyed by ticker, or [] on failure.

    Param set matches pykrx's 전종목_공매도_잔고.fetch() exactly:
    just trdDd and mktTpCd (integer 1/2/3).
    """
    mkt_code = BALANCE_MKT_IDS[market]
    j = _post_krx_bulk(BLD_BALANCE, {
        "trdDd": date_yyyymmdd,
        "mktTpCd": mkt_code,
    })
    if j is None:
        return []
    rows = j.get("OutBlock_1") or []
    out = []
    for r in rows:
        ticker = _extract_ticker(r.get("ISU_CD"))
        if not ticker:
            continue
        out.append({
            "ticker": ticker,
            "short_balance": _parse_num(r.get("BAL_QTY")),
            "short_balance_value": _parse_num(r.get("BAL_AMT")),
            "short_ratio": _parse_num(r.get("BAL_RTO")),
        })
    return out


def merge_trading_balance(trading_rows, balance_rows):
    """Merge by ticker. Returns list of dicts ready for upsert."""
    by_ticker = {}
    for t in trading_rows:
        by_ticker[t["ticker"]] = {
            "ticker": t["ticker"],
            "short_volume": t.get("short_volume"),
            "short_value": t.get("short_value"),
            "short_balance": None,
            "short_balance_value": None,
            "short_ratio": None,
        }
    for b in balance_rows:
        row = by_ticker.setdefault(b["ticker"], {
            "ticker": b["ticker"],
            "short_volume": None,
            "short_value": None,
        })
        row["short_balance"] = b.get("short_balance")
        row["short_balance_value"] = b.get("short_balance_value")
        row["short_ratio"] = b.get("short_ratio")
    return list(by_ticker.values())


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_short_selling(conn, date_iso, rows):
    """Upsert into short_selling. `rows` is list of dicts."""
    if not rows:
        return 0
    cur = conn.cursor()
    values = []
    for r in rows:
        values.append((
            r["ticker"], date_iso,
            r.get("short_volume"),
            r.get("short_value"),
            r.get("short_balance"),
            r.get("short_balance_value"),
            r.get("short_ratio"),
            "krx",
        ))
    if not values:
        return 0
    sql = """
        INSERT INTO short_selling
            (ticker, date, short_volume, short_value,
             short_balance, short_balance_value, short_ratio, source)
        VALUES %s
        ON CONFLICT (ticker, date) DO UPDATE SET
            short_volume        = COALESCE(EXCLUDED.short_volume,
                                            short_selling.short_volume),
            short_value         = COALESCE(EXCLUDED.short_value,
                                            short_selling.short_value),
            short_balance       = COALESCE(EXCLUDED.short_balance,
                                            short_selling.short_balance),
            short_balance_value = COALESCE(EXCLUDED.short_balance_value,
                                            short_selling.short_balance_value),
            short_ratio         = COALESCE(EXCLUDED.short_ratio,
                                            short_selling.short_ratio),
            source              = EXCLUDED.source
    """
    execute_values(cur, sql, values)
    conn.commit()
    n = cur.rowcount
    cur.close()
    return n


# ---------------------------------------------------------------------------
# Date iteration
# ---------------------------------------------------------------------------

def iter_business_days(start_iso, end_iso):
    """Yield YYYYMMDD strings for weekdays in [start, end] inclusive."""
    d = datetime.strptime(start_iso, "%Y-%m-%d")
    end = datetime.strptime(end_iso, "%Y-%m-%d")
    while d <= end:
        if d.weekday() < 5:  # Mon=0 .. Fri=4
            yield d.strftime("%Y%m%d")
        d += timedelta(days=1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest KRX short-selling data via the public "
                    "data.krx.co.kr bulk endpoint (no login required).")
    parser.add_argument("--date",
                        help="Single date YYYY-MM-DD (overrides "
                             "--start-date / --end-date).")
    parser.add_argument("--start-date",
                        help="Range start YYYY-MM-DD.")
    parser.add_argument("--end-date",
                        help="Range end YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--markets",
                        default="KOSPI,KOSDAQ",
                        help="Comma-separated markets to fetch "
                             "(default: KOSPI,KOSDAQ).")
    parser.add_argument("--sleep", type=float, default=0.4,
                        help="Seconds between KRX requests (default 0.4).")
    parser.add_argument("--timeout", type=int, default=30,
                        help="Per-request timeout in seconds.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be ingested; don't write DB.")
    args = parser.parse_args()

    # Resolve date range
    if args.date:
        start_iso = end_iso = args.date
    else:
        if not args.start_date:
            print("ERROR: provide --date or --start-date.")
            sys.exit(2)
        start_iso = args.start_date
        end_iso = args.end_date or datetime.now().strftime("%Y-%m-%d")

    markets = [m.strip().upper() for m in args.markets.split(",")
                 if m.strip()]
    bad = [m for m in markets if m not in TRADING_MKT_IDS]
    if bad:
        print("ERROR: unknown markets: {0}".format(bad))
        sys.exit(2)

    print("=" * 70)
    print("KRX short-selling ingest (public bulk endpoint)")
    print("  Range:     {0} -> {1}".format(start_iso, end_iso))
    print("  Markets:   {0}".format(", ".join(markets)))
    print("  Dry-run:   {0}".format(args.dry_run))
    print("  Sleep:     {0}s between requests".format(args.sleep))
    print("=" * 70)

    conn = psycopg2.connect(DATABASE_URL)
    log_id = log_start(conn, {
        "start": start_iso, "end": end_iso,
        "markets": markets, "dry_run": args.dry_run,
    })

    total_dates = 0
    total_rows = 0
    total_errors = 0

    try:
        for yyyymmdd in iter_business_days(start_iso, end_iso):
            iso = to_iso(yyyymmdd)
            print("\n[{0}]".format(iso), flush=True)
            day_rows_all = []
            day_failure = False
            for market in markets:
                print("  {0}: trading...".format(market),
                      end=" ", flush=True)
                trading = fetch_trading(yyyymmdd, market)
                time.sleep(args.sleep)
                print("balance...", end=" ", flush=True)
                balance = fetch_balance(yyyymmdd, market)
                time.sleep(args.sleep)

                merged = merge_trading_balance(trading, balance)
                non_null = sum(
                    1 for r in merged
                    if r.get("short_volume") is not None
                    or r.get("short_balance") is not None
                )
                print("rows: {0} (trading={1}, balance={2}, "
                      "non-null={3})".format(
                          len(merged), len(trading), len(balance),
                          non_null))
                if not merged:
                    day_failure = True
                    continue
                day_rows_all.extend(merged)

            if day_failure and not day_rows_all:
                print("    skip: both markets returned no data")
                total_errors += 1
                continue

            if args.dry_run:
                print("  DRY-RUN: would upsert {0} rows".format(
                    len(day_rows_all)))
            else:
                n = upsert_short_selling(conn, iso, day_rows_all)
                print("  upserted: {0} rows".format(n))
                total_rows += n
            total_dates += 1

        print("\n" + "=" * 70)
        print("Summary")
        print("  Dates processed: {0}".format(total_dates))
        print("  Dates with errors / no data: {0}".format(total_errors))
        if not args.dry_run:
            print("  Total DB rows upserted: {0}".format(total_rows))
        print("=" * 70)

        log_finish(conn, log_id, "success",
                   rows_processed=total_dates,
                   rows_inserted=total_rows,
                   rows_skipped=total_errors)
    except Exception as e:
        try:
            log_finish(conn, log_id, "error", error_message=str(e))
        except Exception:
            pass
        print("\nERROR: {0}".format(e))
        raise
    finally:
        conn.close()

    print("\nDone!")
