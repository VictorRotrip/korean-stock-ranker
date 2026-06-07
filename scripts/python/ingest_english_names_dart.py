"""
Backfill English company names from DART's company.json endpoint.

Most Korean corps registered with DART include a `corp_name_eng` field
("Samsung Electronics Co., Ltd."). We currently only have English names
for 5 of 3,425 stocks — let's fix that so the app is readable to
international users.

Two-pass:
    1. Fetch corp_name_eng for every stock where stocks.name_en is NULL.
    2. Apply preferred-share inheritance — for tickers like 005935
       (Samsung Electronics preferred), use the parent's name_en plus
       a suffix marker (e.g. "Pref" or "1Pref").

Usage:
    python ingest_english_names_dart.py                      # fill all NULL
    python ingest_english_names_dart.py --dry-run            # don't write
    python ingest_english_names_dart.py --refresh-all        # overwrite even non-null
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error

import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL2") or os.getenv("DATABASE_URL")
DART_API_KEY = os.getenv("DART_API_KEY") or os.getenv("OPENDART_API_KEY")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set."); sys.exit(1)
if not DART_API_KEY:
    print("ERROR: DART_API_KEY not set."); sys.exit(1)

SCRIPT_NAME = "ingest_english_names_dart"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, ".cache")
DART_CORP_CODES_CACHE = os.path.join(CACHE_DIR, "dart_corp_codes.json")

DART_COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"
RATE_LIMIT_SEC = 0.4
TIMEOUT_SEC = 30


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


def log_finish(conn, log_id, status, rows_processed=0, rows_updated=0,
               error_message=None):
    try:
        conn.rollback()
    except Exception:
        pass
    cur = conn.cursor()
    cur.execute(
        "UPDATE ingestion_log "
        "SET finished_at = NOW(), status = %s, "
        "rows_processed = %s, rows_updated = %s, error_message = %s "
        "WHERE id = %s",
        (status, rows_processed, rows_updated, error_message, log_id),
    )
    conn.commit()
    cur.close()


def load_corp_code_cache():
    if not os.path.exists(DART_CORP_CODES_CACHE):
        print(f"ERROR: corp code cache not found at {DART_CORP_CODES_CACHE}")
        print("  Run ingest_dart.py once to populate the cache, then retry.")
        sys.exit(1)
    with open(DART_CORP_CODES_CACHE, "r") as f:
        return json.load(f)


def fetch_company_info(corp_code, retry=2):
    """Return DART company.json payload as dict, or None."""
    params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code}
    url = DART_COMPANY_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    for attempt in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
                data = json.loads(r.read().decode("utf-8"))
                if data.get("status") != "000":
                    return None
                return data
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            if attempt < retry:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None
        except Exception:
            return None
    return None


def fill_english_names(conn, corp_code_map, dry_run=False, refresh_all=False,
                      verbose=True):
    cur = conn.cursor()
    if refresh_all:
        cur.execute("SELECT ticker FROM stocks ORDER BY ticker")
    else:
        cur.execute("""
            SELECT ticker FROM stocks
            WHERE name_en IS NULL OR name_en = ''
            ORDER BY ticker
        """)
    todo = [r[0] for r in cur.fetchall()]
    cur.close()

    if verbose:
        print("=" * 70)
        print(f"Stocks needing English name: {len(todo)}")
        print(f"ETA at {RATE_LIMIT_SEC}s/call: ~{int(len(todo)*RATE_LIMIT_SEC/60)} min")
        print("=" * 70)

    n_filled = 0
    n_no_corp = 0
    n_no_data = 0
    n_empty_eng = 0

    for i, ticker in enumerate(todo, 1):
        corp = corp_code_map.get(ticker)
        if not corp:
            n_no_corp += 1
            continue
        info = fetch_company_info(corp)
        time.sleep(RATE_LIMIT_SEC)
        if not info:
            n_no_data += 1
            continue
        name_eng = (info.get("corp_name_eng") or "").strip()
        if not name_eng:
            n_empty_eng += 1
            continue
        if not dry_run:
            update_cur = conn.cursor()
            update_cur.execute(
                "UPDATE stocks SET name_en = %s, updated_at = NOW() "
                "WHERE ticker = %s",
                (name_eng, ticker),
            )
            update_cur.close()
            if i % 25 == 0:
                conn.commit()
        n_filled += 1
        if verbose and i % 100 == 0:
            print(f"  [{i}/{len(todo)}] {ticker} -> {name_eng[:50]}")

    if not dry_run:
        conn.commit()

    if verbose:
        print()
        print("Summary:")
        print(f"  Filled:                  {n_filled}")
        print(f"  No corp_code in cache:   {n_no_corp}")
        print(f"  DART returned no data:   {n_no_data}")
        print(f"  Empty corp_name_eng:     {n_empty_eng}")
    return n_filled


def inherit_preferred_shares(conn, dry_run=False, verbose=True):
    """Preferred shares (e.g. 005935 우) often have no separate DART corp
    registration. Fall back to inheriting the parent's name_en + ' Pref'."""
    if dry_run:
        return 0
    cur = conn.cursor()
    cur.execute("""
        UPDATE stocks p
        SET name_en = CASE
              WHEN parent.name_en IS NOT NULL THEN parent.name_en || ' (Pref)'
              ELSE NULL
            END,
            updated_at = NOW()
        FROM stocks parent
        WHERE (p.name_en IS NULL OR p.name_en = '')
          AND parent.name_en IS NOT NULL
          AND substring(p.ticker FROM 1 FOR 5) = substring(parent.ticker FROM 1 FOR 5)
          AND p.ticker <> parent.ticker
    """)
    n = cur.rowcount
    conn.commit()
    cur.close()
    if verbose:
        print(f"  Inherited from parent for {n} preferred shares")
    return n


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill English company names from DART's company.json."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh-all", action="store_true",
                        help="Overwrite existing name_en values too.")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    corp_code_map = load_corp_code_cache()

    log_id = log_start(conn, {
        "dry_run": args.dry_run,
        "refresh_all": args.refresh_all,
    })

    try:
        print("\n=== Step 1: fetch corp_name_eng from DART ===")
        n_filled = fill_english_names(
            conn, corp_code_map,
            dry_run=args.dry_run, refresh_all=args.refresh_all, verbose=True,
        )

        if not args.dry_run:
            print("\n=== Step 2: inherit name_en for preferred shares ===")
            inherit_preferred_shares(conn, dry_run=False, verbose=True)

        log_finish(conn, log_id, "success",
                   rows_processed=n_filled, rows_updated=n_filled)
    except KeyboardInterrupt:
        try:
            conn.rollback()
        except Exception:
            pass
        log_finish(conn, log_id, "interrupted",
                   error_message="user cancelled")
        sys.exit(1)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            log_finish(conn, log_id, "error", error_message=str(e))
        except Exception:
            pass
        print(f"\n[ERROR] {e}")
        raise
    finally:
        conn.close()

    print("\nDone.")
