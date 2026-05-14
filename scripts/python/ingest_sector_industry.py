"""
Ingest KRX sector / industry classifications into the `stocks` table.

Why this exists:
    diagnose_factor_inputs.py reports:
        Sector filled:   ~537/2500
        Industry filled: 0/2500
    Industry-momentum factors (industry_momentum_26w / 52w) need a ticker
    -> industry-group mapping so peers can be averaged. Without it those
    factors are uncomputable for the whole universe.

Data source (canonical):
    fdr.StockListing("KRX-DESC")
        -> finance-datareader fetches the cached KIND corporate listing
           (cached daily from kind.krx.co.kr/corpgeneral/corpList.do)
           with columns:
               Code, Name, Market, Sector, Industry, Products,
               ListingDate, SettleMonth, Representative, HomePage, Region

        IMPORTANT — what's actually in those columns (verified
        empirically on the cached CSV, not what FDR's docstring says):
          - "Sector"   = KIND 소속부 (KOSDAQ listing tier:
                         "중견기업부", "벤처기업부", "기술성장기업부",
                         "관리종목", ...). NOT an industry classification.
                         IGNORED by this script.
          - "Industry" = KSIC 업종 (Korean Standard Industrial
                         Classification, ~160 distinct values:
                         "소프트웨어 개발 및 공급업", "반도체 제조업",
                         "전자부품 제조업", ...). THIS IS WHAT WE WANT.
          - "Products" = KIND 주요제품 (mostly company-unique strings,
                         too granular for peer-group momentum).
                         Not used here.

Mapping:
    KIND "Industry" (KSIC 업종)  ->  stocks.sector
    KIND "Industry" (KSIC 업종)  ->  stocks.industry
    The same KSIC value is written to BOTH columns so that
    calculate_factors.py's `industry OR sector OR ticker` fallback
    always lands on KSIC for every classified ticker — gives clean
    cross-universe peer grouping for industry_momentum_26w / 52w.

Notes on the previous draft of this script:
    - pykrx index walking now requires KRX_ID / KRX_PW credentials
      (the venv prints "KRX 로그인 실패: KRX_ID 또는 KRX_PW 환경 변수가
      설정되지 않았습니다." at import time). We don't depend on that
      path anymore — it stays available behind `--use-pykrx` for users
      who have credentials.
    - fdr.StockListing("KRX") (no -DESC suffix) returns the marcap-style
      listing without sector data; that endpoint is NOT used here.

Output:
    Updates stocks.sector with KIND `Sector`  (Korean 업종 string)
    Updates stocks.industry with KIND `Industry` (Korean 주요제품 string).
    In fill-missing mode, existing non-null values are preserved
    (COALESCE-protected).

Run modes:
    --fill-missing-only (default)
        Only update stocks where sector IS NULL or sector = ''.
        Leaves the ~540 stocks that already have English GICS-style
        sector names alone.  This is conservative and reversible.
    --refresh-all
        Overwrite sector and industry for every active stock.  Use this
        when you want a single consistent KIND/KSIC taxonomy across the
        whole universe (recommended once you've verified the dry-run
        output looks sane).

Usage:
    python ingest_sector_industry.py --dry-run
    python ingest_sector_industry.py
    python ingest_sector_industry.py --refresh-all
    python ingest_sector_industry.py --tickers 005930,000660 --refresh-all
"""

import os
import sys
import argparse
import time
from datetime import datetime

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "ingest_sector_industry"


# ---------------------------------------------------------------------------
# Ingestion logging (matches the rest of the repo)
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
# FDR KIND-DESC fetcher
# ---------------------------------------------------------------------------

def _normalize_str(v):
    if v is None:
        return None
    if isinstance(v, float):
        # NaN guard
        if v != v:
            return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "-", "null"):
        return None
    return s


def fetch_kind_desc(verbose=True):
    """Pull the KIND descriptive listing via finance-datareader.

    Returns a dict: ticker -> {"name": str, "ksic": str|None,
                               "listing_tier": str|None,
                               "products": str|None}

    The KSIC industry name ("ksic") is what we want for the
    industry-momentum peer grouping. listing_tier is captured for
    diagnostics only. products is captured for completeness but not
    used in the DB update — it's too granular for peer grouping.
    """
    try:
        import FinanceDataReader as fdr
    except ImportError:
        if verbose:
            print("  ERROR: finance-datareader is not installed.")
        return {}

    try:
        df = fdr.StockListing("KRX-DESC")
    except Exception as e:
        if verbose:
            print("  ERROR: fdr.StockListing('KRX-DESC') failed: {0}".format(
                str(e)[:160]))
        return {}

    if df is None or df.empty:
        if verbose:
            print("  ERROR: fdr.StockListing('KRX-DESC') returned empty.")
        return {}

    if verbose:
        print("  KIND-DESC rows: {0}".format(len(df)))
        print("  KIND-DESC columns: {0}".format(list(df.columns)))

    # Resolve column names case-insensitively. Note the empirically-
    # verified semantics:
    #   FDR "Sector"   == KIND 소속부 (listing tier — IGNORED for KSIC)
    #   FDR "Industry" == KSIC 업종   (what we map to stocks.sector
    #                                  AND stocks.industry)
    #   FDR "Products" == 주요제품    (kept for diagnostics only)
    cols_lower = {c.lower(): c for c in df.columns}
    code_col = (cols_lower.get("code") or cols_lower.get("symbol")
                or cols_lower.get("ticker"))
    name_col = cols_lower.get("name")
    listing_tier_col = cols_lower.get("sector")      # 소속부
    ksic_col = cols_lower.get("industry")             # KSIC 업종
    products_col = cols_lower.get("products")        # 주요제품

    if not code_col:
        if verbose:
            print("  ERROR: cannot find ticker column in KIND-DESC")
        return {}
    if not ksic_col:
        if verbose:
            print("  ERROR: KIND-DESC missing the 'Industry' (KSIC 업종) "
                  "column we need: {0}".format(list(df.columns)))
        return {}

    out = {}
    for _, row in df.iterrows():
        ticker = row.get(code_col)
        if ticker is None:
            continue
        ticker = str(ticker).strip()
        if len(ticker) == 5:
            ticker = ticker.zfill(6)
        if not ticker:
            continue
        out[ticker] = {
            "name": (_normalize_str(row.get(name_col))
                     if name_col else None),
            "ksic": _normalize_str(row.get(ksic_col)),
            "listing_tier": (_normalize_str(row.get(listing_tier_col))
                              if listing_tier_col else None),
            "products": (_normalize_str(row.get(products_col))
                          if products_col else None),
        }
    return out


# ---------------------------------------------------------------------------
# Optional pykrx KOSPI sector indices (gated, requires KRX_ID/KRX_PW)
# ---------------------------------------------------------------------------

KOSPI_SECTOR_INDEX_CODES = [
    "1005", "1006", "1007", "1008", "1009", "1010", "1011", "1012",
    "1013", "1014", "1015", "1016", "1017", "1018", "1019", "1020",
    "1021", "1022", "1024", "1025", "1026",
]


def fetch_pykrx_sector_map(date_yyyymmdd, sleep=0.3, verbose=True):
    """OPTIONAL: walk KOSPI KRX sector indices via pykrx.

    Returns ticker -> sector_name (Korean KRX sector label, e.g. '전기전자').
    Many pykrx versions now require KRX_ID/KRX_PW env vars for the index
    APIs; if those aren't set, this silently returns {}.
    """
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        if verbose:
            print("  [pykrx] KRX_ID/KRX_PW not set in env; skipping pykrx.")
        return {}, []

    try:
        from pykrx import stock
    except ImportError:
        if verbose:
            print("  [pykrx] not installed; skipping.")
        return {}, []

    ticker_sector = {}
    audit = []
    for code in KOSPI_SECTOR_INDEX_CODES:
        try:
            name = stock.get_index_ticker_name(code)
        except Exception as e:
            if verbose:
                print("    {0}: name lookup failed ({1})".format(
                    code, str(e)[:60]))
            continue
        if not name:
            continue
        try:
            members = stock.get_index_portfolio_deposit_file(
                code, date_yyyymmdd)
        except Exception as e:
            if verbose:
                print("    {0} {1}: members failed ({2})".format(
                    code, name, str(e)[:60]))
            continue
        members = list(members or [])
        for t in members:
            if t not in ticker_sector:
                ticker_sector[t] = name.strip()
        audit.append(("KOSPI", code, name.strip(), len(members)))
        time.sleep(sleep)

    return ticker_sector, audit


# ---------------------------------------------------------------------------
# DB read/write
# ---------------------------------------------------------------------------

def get_target_stocks(conn, fill_missing_only=True,
                     tickers_filter=None, limit=None):
    """Return list of (ticker, name, market, sector, industry) for stocks
    needing classification."""
    cur = conn.cursor()
    q = ("SELECT ticker, name, market, sector, industry "
         "FROM stocks WHERE is_active = TRUE")
    params = []
    if tickers_filter:
        q += " AND ticker = ANY(%s)"
        params.append(tickers_filter)
    if fill_missing_only:
        q += " AND (sector IS NULL OR sector = '')"
    q += " ORDER BY ticker"
    if limit:
        q += " LIMIT %s"
        params.append(int(limit))
    cur.execute(q, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def apply_updates(conn, updates, refresh_all):
    """Apply sector/industry updates.

    updates: list of (ticker, sector, industry).
    When refresh_all=True, both columns are overwritten unconditionally.
    When refresh_all=False, COALESCE preserves any existing non-null
    value on the row.
    Returns rows affected.
    """
    if not updates:
        return 0
    cur = conn.cursor()
    if refresh_all:
        sql = (
            "UPDATE stocks AS s "
            "SET sector = v.sector, "
            "    industry = v.industry, "
            "    updated_at = NOW() "
            "FROM (VALUES %s) AS v(ticker, sector, industry) "
            "WHERE s.ticker = v.ticker"
        )
    else:
        sql = (
            "UPDATE stocks AS s "
            "SET sector = COALESCE(s.sector, v.sector), "
            "    industry = COALESCE(s.industry, v.industry), "
            "    updated_at = NOW() "
            "FROM (VALUES %s) AS v(ticker, sector, industry) "
            "WHERE s.ticker = v.ticker"
        )
    execute_values(cur, sql, updates, template="(%s, %s, %s)")
    conn.commit()
    n = cur.rowcount
    cur.close()
    return n


def count_filled(conn):
    cur = conn.cursor()
    cur.execute(
        "SELECT "
        "COUNT(*) FILTER (WHERE sector IS NOT NULL AND sector <> ''), "
        "COUNT(*) FILTER (WHERE industry IS NOT NULL AND industry <> ''), "
        "COUNT(*) "
        "FROM stocks WHERE is_active = TRUE"
    )
    sec, ind, tot = cur.fetchone()
    cur.close()
    return sec, ind, tot


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest KRX/KIND sector and industry classifications "
                    "into the stocks table.")
    parser.add_argument("--refresh-all", action="store_true",
                        help="Overwrite existing sector AND industry "
                             "values. Default fills only NULL columns.")
    parser.add_argument("--tickers",
                        help="Comma-separated tickers to restrict to "
                             "(useful for testing).")
    parser.add_argument("--limit", type=int,
                        help="Max number of target tickers to process.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute the diff but do not write to DB.")
    parser.add_argument("--use-pykrx", action="store_true",
                        help="Also try pykrx KOSPI sector indices "
                             "(requires KRX_ID and KRX_PW env vars).")
    parser.add_argument("--pykrx-date",
                        default=datetime.now().strftime("%Y-%m-%d"),
                        help="Date YYYY-MM-DD for the pykrx index "
                             "snapshot (default: today).")
    parser.add_argument("--sleep", type=float, default=0.3,
                        help="Seconds between pykrx index calls.")
    args = parser.parse_args()

    fill_missing_only = not args.refresh_all

    print("=" * 70)
    print("KRX sector/industry ingest")
    print("  Mode:                 {0}".format(
        "REFRESH all" if args.refresh_all else "fill missing only"))
    print("  Dry-run:              {0}".format(args.dry_run))
    print("  pykrx supplemental:   {0}".format(
        "enabled" if args.use_pykrx else "DISABLED"))
    print("=" * 70)

    conn = psycopg2.connect(DATABASE_URL)

    tickers_filter = None
    if args.tickers:
        tickers_filter = [t.strip() for t in args.tickers.split(",")
                          if t.strip()]

    targets = get_target_stocks(
        conn,
        fill_missing_only=fill_missing_only,
        tickers_filter=tickers_filter,
        limit=args.limit,
    )

    sec_before, ind_before, tot_active = count_filled(conn)
    print("\nBefore: sector={0}/{1}, industry={2}/{1}".format(
        sec_before, tot_active, ind_before))
    print("Target tickers (will receive an update if data is available): "
          "{0}".format(len(targets)))

    if not targets:
        print("\nNothing to do. (Use --refresh-all to re-classify "
              "every active stock.)")
        conn.close()
        sys.exit(0)

    log_id = log_start(conn, {
        "fill_missing_only": fill_missing_only,
        "tickers_filter": args.tickers,
        "limit": args.limit,
        "dry_run": args.dry_run,
        "use_pykrx": args.use_pykrx,
    })

    try:
        target_set = set(r[0] for r in targets)

        # ---------- 1) FDR KIND-DESC (primary) ----------
        print("\n[1] FDR KIND-DESC (kind.krx.co.kr corpList):")
        kind_map = fetch_kind_desc(verbose=True)
        kind_hits = sum(1 for t in kind_map if t in target_set)
        print("  KIND-DESC classified: {0} tickers total, "
              "{1} of which are in target set".format(
                  len(kind_map), kind_hits))

        # ---------- 2) pykrx (optional supplement) ----------
        pykrx_map = {}
        pykrx_audit = []
        if args.use_pykrx:
            print("\n[2] pykrx KOSPI sector indices (date={0})...".format(
                args.pykrx_date.replace("-", "")))
            pykrx_map, pykrx_audit = fetch_pykrx_sector_map(
                args.pykrx_date.replace("-", ""),
                sleep=args.sleep, verbose=True)
            pykrx_hits = sum(1 for t in pykrx_map if t in target_set)
            print("  pykrx classified: {0} tickers total, "
                  "{1} of which are in target set".format(
                      len(pykrx_map), pykrx_hits))
            if pykrx_audit:
                print("\n  pykrx sector audit:")
                print("  {0:<8} {1:<6} {2:<24} {3:>6}".format(
                    "Market", "Code", "Name", "N"))
                for market, code, name, n in pykrx_audit:
                    print("  {0:<8} {1:<6} {2:<24} {3:>6}".format(
                        market, code, (name or "")[:24], n))

        # ---------- 3) Merge & build updates ----------
        updates = []  # (ticker, sector, industry)
        breakdown = {"kind": 0, "pykrx": 0, "unclassified": 0}
        unclassified = []
        sector_distribution = {}
        industry_distribution = {}

        for ticker, name, market, cur_sector, cur_industry in targets:
            new_sector = None
            new_industry = None
            source = None

            kind_entry = kind_map.get(ticker)
            if kind_entry:
                ksic = kind_entry.get("ksic")
                if ksic:
                    # The same KSIC value is written to BOTH columns so
                    # calculate_factors.py's industry_map always lands on
                    # KSIC regardless of which column it prefers.
                    new_sector = ksic
                    new_industry = ksic
                    source = "kind"

            # pykrx supplements sector only if KIND missed this ticker
            if not new_sector and ticker in pykrx_map:
                new_sector = pykrx_map[ticker]
                new_industry = pykrx_map[ticker]
                if source is None:
                    source = "pykrx"

            if not new_sector and not new_industry:
                breakdown["unclassified"] += 1
                unclassified.append((ticker, name, market))
                continue

            breakdown[source] += 1
            # pass through; SQL COALESCE / overwrite handled by apply_updates
            updates.append((ticker, new_sector, new_industry))

            if new_sector:
                sector_distribution[new_sector] = (
                    sector_distribution.get(new_sector, 0) + 1)
            if new_industry:
                industry_distribution[new_industry] = (
                    industry_distribution.get(new_industry, 0) + 1)

        print("\nClassification source breakdown:")
        for k in ("kind", "pykrx", "unclassified"):
            print("  {0:<14} {1:>5} / {2}".format(
                k, breakdown[k], len(targets)))

        if sector_distribution:
            print("\nKSIC 업종 distribution (written to BOTH stocks.sector "
                  "and stocks.industry) — top 20 of {0}:".format(
                      len(sector_distribution)))
            top = sorted(sector_distribution.items(),
                         key=lambda x: -x[1])[:20]
            for sec, n in top:
                print("  {0:<60} {1}".format(sec[:60], n))
            if len(sector_distribution) > 20:
                print("  ... and {0} more KSIC categories".format(
                    len(sector_distribution) - 20))

        if unclassified:
            print("\nUnclassified ({0} stocks, first 20):".format(
                len(unclassified)))
            print("  {0:<8} {1:<6} {2}".format("Ticker", "Market", "Name"))
            for tk, nm, mkt in unclassified[:20]:
                print("  {0:<8} {1:<6} {2}".format(
                    tk, mkt or "-", (nm or "")[:40]))
            if len(unclassified) > 20:
                print("  ... and {0} more".format(len(unclassified) - 20))

        # ---------- 4) Write ----------
        if args.dry_run:
            print("\nDRY-RUN: would update {0} stocks. "
                  "Nothing written.".format(len(updates)))
            log_finish(conn, log_id, "success",
                       rows_processed=len(targets),
                       rows_updated=0,
                       rows_skipped=breakdown["unclassified"])
        else:
            print("\nWriting {0} updates ({1} mode)...".format(
                len(updates),
                "REFRESH" if args.refresh_all else "fill-missing COALESCE"))
            n = apply_updates(conn, updates, refresh_all=args.refresh_all)
            print("  DB rows affected: {0}".format(n))
            log_finish(conn, log_id, "success",
                       rows_processed=len(targets),
                       rows_updated=n,
                       rows_skipped=breakdown["unclassified"])

        sec_after, ind_after, _ = count_filled(conn)
        print("\nAfter:  sector={0}/{1}, industry={2}/{1}".format(
            sec_after, tot_active, ind_after))
        print("  delta sector:   +{0}".format(sec_after - sec_before))
        print("  delta industry: +{0}".format(ind_after - ind_before))

        if not args.refresh_all and sec_after > sec_before:
            print("\nNOTE: --fill-missing-only kept any pre-existing English")
            print("  GICS-style sector labels on stocks that already had one.")
            print("  New rows got KIND Korean 업종 labels. The two label")
            print("  taxonomies don't overlap, so industry_momentum_26w/52w")
            print("  will only find peers WITHIN each taxonomy.")
            print("  Run with --refresh-all once you've reviewed results to")
            print("  unify the universe under a single (KIND) classification.")

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
