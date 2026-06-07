"""
Backfill industry classifications for stocks that don't have them, using
DART's company.json endpoint.

Why this exists:
    The existing ingest_sector_industry.py uses FinanceDataReader, which
    only covers currently-listed stocks. After populate_stocks_from_marcap
    added ~897 delisted tickers to the `stocks` table, their sector and
    industry columns are NULL. Industry-relative ranking for those names
    falls back to universe-wide ranking, which is fine but suboptimal.

    DART's company.json returns `induty_code` (5-digit KSIC code) for every
    registered corp, including delisted ones. This script:
      1. Samples active stocks (which already have a Korean KSIC NAME like
         "반도체 제조업" in stocks.industry) and fetches their DART
         induty_code to build an empirical {code -> name} mapping.
      2. For each delisted stock with no sector/industry, fetches the
         DART induty_code and looks it up in the mapping.
      3. If a code wasn't seen in the active-stock sample, the script
         falls back to a built-in seed mapping covering the most common
         KOSPI/KOSDAQ KSIC codes.
      4. Writes the mapped Korean industry name into both
         stocks.sector and stocks.industry (the same convention
         ingest_sector_industry.py uses) so peer grouping works.

Usage:
    python ingest_industry_dart.py                    # fill all NULL rows
    python ingest_industry_dart.py --dry-run          # show what would happen
    python ingest_industry_dart.py --refresh-mapping  # rebuild active-stock sample
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

DATABASE_URL = os.getenv("DATABASE_URL")
DART_API_KEY = os.getenv("DART_API_KEY") or os.getenv("OPENDART_API_KEY")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set."); sys.exit(1)
if not DART_API_KEY:
    print("ERROR: DART_API_KEY not set."); sys.exit(1)

SCRIPT_NAME = "ingest_industry_dart"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, ".cache")
DART_CORP_CODES_CACHE = os.path.join(CACHE_DIR, "dart_corp_codes.json")
INDUSTRY_MAPPING_CACHE = os.path.join(CACHE_DIR, "ksic_code_to_name.json")

DART_COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"
RATE_LIMIT_SEC = 0.4   # ~150 calls/min, comfortably under DART's hourly cap
TIMEOUT_SEC = 30


# Built-in seed mapping covering common KSIC codes used by KOSPI/KOSDAQ
# corps. Used as a final fallback when neither the empirical mapping
# (built from active stocks) nor the prefix-truncation fallback match.
# Includes entries for the top "unknown codes" surfaced during a dry-run
# against the 897 delisted stocks (financial services, software, etc.).
SEED_KSIC_NAME = {
    # Manufacturing — electronics / semiconductors
    "26110": "반도체 제조업",
    "26120": "반도체 제조업",
    "26299": "전자부품 제조업",
    "262":   "전자부품 제조업",
    "2629":  "전자부품 제조업",
    "26410": "통신 및 방송 장비 제조업",
    "26411": "통신 및 방송 장비 제조업",
    "26421": "통신 및 방송 장비 제조업",
    "26429": "통신 및 방송 장비 제조업",
    "26511": "영상 및 음향기기 제조업",
    "27199": "전기장비 제조업",
    # Manufacturing — machinery / motor
    "292":   "기타 기계 및 장비 제조업",
    "29420": "기타 특수목적용 기계 제조업",
    "29271": "기타 특수목적용 기계 제조업",
    "30122": "자동차용 엔진 및 자동차 제조업",
    "30310": "자동차 신품 부품 제조업",
    # Software / IT services
    "62010": "소프트웨어 개발 및 공급업",
    "63112": "포털 및 기타 인터넷 정보매개 서비스업",
    "582":   "소프트웨어 개발 및 공급업",
    "58221": "소프트웨어 개발 및 공급업",
    # Pharma / chemicals
    "21210": "기초 의약물질 및 생물학적 제제 제조업",
    "21300": "의약품 제조업",
    "213":   "의료용품 및 기타 의약 관련제품 제조업",
    "20423": "기타 화학제품 제조업",
    # Retail
    "47611": "체인화 편의점",
    # Finance — banks / brokers / holdings / insurance
    "64101": "은행 및 저축기관",
    "64201": "투자기관",
    "64992": "기타 금융업",
    "661":   "기타 금융업",
    "66199": "기타 금융업",
    "65201": "보험업",
    # Services
    "76190": "사업지원 서비스업",
    "761":   "사업지원 서비스업",
}


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
        print("ERROR: DART corp code cache not found at {0}".format(
            DART_CORP_CODES_CACHE))
        print("  Run ingest_dart.py once to populate the cache, then retry.")
        sys.exit(1)
    with open(DART_CORP_CODES_CACHE, "r") as f:
        return json.load(f)


def fetch_induty_code(corp_code, retry=2):
    """Hit DART company.json and return induty_code (or None)."""
    params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code}
    url = DART_COMPANY_URL + "?" + "&".join(
        "{0}={1}".format(k, v) for k, v in params.items()
    )
    for attempt in range(retry + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
                data = json.loads(r.read().decode("utf-8"))
                if data.get("status") != "000":
                    return None
                return data.get("induty_code") or None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            if attempt < retry:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None
        except Exception:
            return None
    return None


def build_empirical_mapping(conn, corp_code_map, sample_per_code=3,
                            verbose=True):
    """Sample some active stocks per (unique industry name), look up their
    induty_code, and build {induty_code -> industry_name}. Returns the
    mapping plus stats.

    To keep the API budget tight, we cap at sample_per_code per industry
    name. The first code we see for each industry wins; if multiple codes
    map to the same name, we accept whichever we see first.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, industry FROM stocks
        WHERE is_active = TRUE
          AND industry IS NOT NULL AND industry <> ''
        ORDER BY industry, ticker
    """)
    rows = cur.fetchall()
    cur.close()

    # Group by industry name, take first N tickers per group
    by_industry = {}
    for ticker, industry in rows:
        by_industry.setdefault(industry, []).append(ticker)

    if verbose:
        print("  Sampling DART induty_code for {0} unique industry names...".format(
            len(by_industry)), flush=True)
        print("  (~{0} API calls at {1}s each)".format(
            sum(min(sample_per_code, len(v)) for v in by_industry.values()),
            RATE_LIMIT_SEC), flush=True)

    mapping = {}
    code_counts = {}    # induty_code -> count seen
    n_calls = 0
    for industry, tickers in by_industry.items():
        samples = tickers[:sample_per_code]
        votes = {}
        for t in samples:
            corp = corp_code_map.get(t)
            if not corp:
                continue
            code = fetch_induty_code(corp)
            n_calls += 1
            time.sleep(RATE_LIMIT_SEC)
            if code:
                votes[code] = votes.get(code, 0) + 1
                code_counts[code] = code_counts.get(code, 0) + 1
        if votes:
            # Pick the most common code observed for this industry name
            winner = max(votes.items(), key=lambda x: x[1])[0]
            mapping[winner] = industry
        if verbose and len(mapping) % 25 == 0 and len(mapping) > 0:
            print("    {0} mappings built so far ({1} API calls)".format(
                len(mapping), n_calls), flush=True)

    if verbose:
        print("  Empirical mapping: {0} codes -> industry names "
              "(total {1} DART calls)".format(len(mapping), n_calls),
              flush=True)
    return mapping


def save_mapping(mapping):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(INDUSTRY_MAPPING_CACHE, "w") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


def load_mapping():
    if not os.path.exists(INDUSTRY_MAPPING_CACHE):
        return None
    with open(INDUSTRY_MAPPING_CACHE, "r") as f:
        return json.load(f)


def lookup_industry(code, mapping, seed):
    """Find an industry name for the given KSIC code with prefix fallback.

    KSIC codes are hierarchical (5-digit = most specific, then 4, 3, 2,
    1 broader). DART sometimes returns short variants (3-4 digit) for the
    same conceptual industry that active stocks reported as 5-digit. We
    look up:
      1. Exact code in mapping
      2. Exact code in seed
      3. Successive prefixes (4, 3, 2, 1 char) against both mapping and seed
    Returns the industry name or None.
    """
    if not code:
        return None
    if code in mapping:
        return mapping[code]
    if code in seed:
        return seed[code]
    # Prefix fallback. Try shorter and shorter prefixes against both tables.
    for prefix_len in range(len(code) - 1, 0, -1):
        prefix = code[:prefix_len]
        # Direct prefix match in seed
        if prefix in seed:
            return seed[prefix]
        # Or any mapping entry that itself starts with this prefix (the
        # "siblings" share the broader category)
        for full_code, name in mapping.items():
            if full_code.startswith(prefix):
                return name
    return None


def fill_missing_industries(conn, corp_code_map, mapping, dry_run=False,
                            verbose=True):
    """For each ticker in stocks where industry IS NULL, fetch DART
    induty_code and write the mapped industry name into both sector and
    industry. Returns counts."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, name FROM stocks
        WHERE (industry IS NULL OR industry = '')
        ORDER BY ticker
    """)
    todo = cur.fetchall()
    cur.close()

    if verbose:
        print("  Stocks missing industry: {0}".format(len(todo)), flush=True)
        print("  ETA at {0}s/call: ~{1} min".format(
            RATE_LIMIT_SEC, int(len(todo) * RATE_LIMIT_SEC / 60)), flush=True)

    n_filled = 0
    n_seen_codes = {}    # induty_code -> count
    n_unknown_codes = {} # induty_code -> count (not in mapping)
    n_no_corp = 0
    n_no_code = 0

    for i, (ticker, name) in enumerate(todo, 1):
        corp = corp_code_map.get(ticker)
        if not corp:
            n_no_corp += 1
            continue
        code = fetch_induty_code(corp)
        time.sleep(RATE_LIMIT_SEC)
        if not code:
            n_no_code += 1
            continue
        n_seen_codes[code] = n_seen_codes.get(code, 0) + 1
        industry_name = lookup_industry(code, mapping, SEED_KSIC_NAME)
        if not industry_name:
            n_unknown_codes[code] = n_unknown_codes.get(code, 0) + 1
            continue
        if not dry_run:
            update_cur = conn.cursor()
            update_cur.execute("""
                UPDATE stocks
                SET sector   = COALESCE(sector, %s),
                    industry = COALESCE(industry, %s),
                    updated_at = NOW()
                WHERE ticker = %s
            """, (industry_name, industry_name, ticker))
            update_cur.close()
            if i % 25 == 0:
                conn.commit()
        n_filled += 1
        if verbose and i % 50 == 0:
            print("    [{0}/{1}] {2} {3} -> {4} (code {5})".format(
                i, len(todo), ticker, (name or "")[:20],
                industry_name[:30], code), flush=True)

    if not dry_run:
        conn.commit()

    if verbose:
        print("", flush=True)
        print("  Summary:", flush=True)
        print("    Filled:                  {0}".format(n_filled), flush=True)
        print("    No corp_code in cache:   {0}".format(n_no_corp), flush=True)
        print("    DART returned no code:   {0}".format(n_no_code), flush=True)
        print("    Unknown KSIC codes:      {0} distinct".format(
            len(n_unknown_codes)), flush=True)
        if n_unknown_codes:
            print("    Top unknown codes (count, code):", flush=True)
            for code, cnt in sorted(
                    n_unknown_codes.items(),
                    key=lambda x: -x[1])[:10]:
                print("      {0}x  {1}".format(cnt, code), flush=True)
    return n_filled


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill industry classifications for delisted/missing "
                    "stocks using DART's company.json endpoint.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen, don't write.")
    parser.add_argument("--refresh-mapping", action="store_true",
                        help="Rebuild the KSIC-code -> industry-name "
                             "mapping by sampling active stocks again. "
                             "Slow (~400 DART calls).")
    parser.add_argument("--sample-per-code", type=int, default=3,
                        help="Samples per industry name when building "
                             "the empirical mapping (default 3).")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    corp_code_map = load_corp_code_cache()

    log_id = log_start(conn, {
        "dry_run": args.dry_run,
        "refresh_mapping": args.refresh_mapping,
    })

    try:
        # --- Build / load the {induty_code -> industry_name} mapping ---
        mapping = load_mapping() if not args.refresh_mapping else None
        if mapping is None:
            print("=" * 70, flush=True)
            print("Step 1: Build empirical {induty_code -> industry_name} mapping",
                  flush=True)
            print("=" * 70, flush=True)
            mapping = build_empirical_mapping(
                conn, corp_code_map,
                sample_per_code=args.sample_per_code,
                verbose=True,
            )
            if mapping:
                save_mapping(mapping)
                print("  Saved mapping to {0}".format(INDUSTRY_MAPPING_CACHE),
                      flush=True)
        else:
            print("  Loaded existing mapping: {0} entries".format(
                len(mapping)), flush=True)

        # --- Fill in missing industries ---
        print(flush=True)
        print("=" * 70, flush=True)
        print("Step 2: Fill missing industry for delisted / NULL stocks",
              flush=True)
        print("=" * 70, flush=True)
        n_filled = fill_missing_industries(
            conn, corp_code_map, mapping,
            dry_run=args.dry_run, verbose=True,
        )

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
        print("\n[ERROR] {0}".format(e), flush=True)
        raise
    finally:
        conn.close()

    print("\nDone.")
