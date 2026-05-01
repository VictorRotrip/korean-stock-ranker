"""
Ingest KOSPI and KOSDAQ stock universe into Supabase Postgres.

Data sources (in order of preference):
1. FinanceDataReader.StockListing("KRX") — most reliable, includes sector info
2. pykrx.stock.get_market_ticker_name() — for name lookups
3. Hardcoded KNOWN_TICKERS — fallback for smoke test tickers

Note: pykrx 1.0.51 broke get_market_ticker_list(). This script now uses
FinanceDataReader as the primary source for the universe.

Usage:
    python ingest_universe.py
    python ingest_universe.py --tickers 005930,000660,035420
    python ingest_universe.py --limit 20
"""

import os
import sys
import argparse
from datetime import datetime

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set. Copy .env.example -> .env.local and fill in your Supabase URL.")
    sys.exit(1)

SCRIPT_NAME = "ingest_universe"

# Well-known tickers for fallback (smoke test)
KNOWN_TICKERS = {
    "005930": ("삼성전자", "Samsung Electronics", "KOSPI"),
    "000660": ("SK하이닉스", "SK Hynix", "KOSPI"),
    "035420": ("NAVER", "Naver Corp", "KOSPI"),
    "051910": ("LG화학", "LG Chem", "KOSPI"),
    "005380": ("현대자동차", "Hyundai Motor", "KOSPI"),
    "035720": ("카카오", "Kakao Corp", "KOSPI"),
    "006400": ("삼성SDI", "Samsung SDI", "KOSPI"),
    "068270": ("셀트리온", "Celltrion", "KOSPI"),
    "028260": ("삼성물산", "Samsung C&T", "KOSPI"),
    "105560": ("KB금융", "KB Financial", "KOSPI"),
    "055550": ("신한지주", "Shinhan Financial", "KOSPI"),
    "003670": ("포스코퓨처엠", "POSCO Future M", "KOSPI"),
    "207940": ("삼성바이오로직스", "Samsung Biologics", "KOSPI"),
    "247540": ("에코프로비엠", "EcoPro BM", "KOSDAQ"),
    "373220": ("LG에너지솔루션", "LG Energy Solution", "KOSPI"),
}


# ---------------------------------------------------------------------------
# Ingestion logging
# ---------------------------------------------------------------------------

def log_start(conn, params=None):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ingestion_log (script_name, parameters) VALUES (%s, %s) RETURNING id",
        (SCRIPT_NAME, Json(params)),
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
               rows_updated = %s, rows_skipped = %s, error_message = %s
           WHERE id = %s""",
        (status, rows_processed, rows_inserted, rows_updated,
         rows_skipped, error_message, log_id),
    )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_from_fdr(tickers_filter=None):
    """Load stock universe from FinanceDataReader (primary source)."""
    import FinanceDataReader as fdr

    results = []
    for market in ["KOSPI", "KOSDAQ"]:
        try:
            listing = fdr.StockListing(market)
            if listing is None or listing.empty:
                print("  Warning: FDR StockListing({}) returned empty".format(market))
                continue

            # Normalize column names
            rename = {}
            for col in listing.columns:
                lc = col.lower()
                if lc in ("code", "symbol", "종목코드"):
                    rename[col] = "ticker"
                elif lc in ("name", "종목명"):
                    rename[col] = "name"
                elif lc in ("sector", "섹터"):
                    rename[col] = "sector"
                elif lc in ("industry", "산업"):
                    rename[col] = "industry"
                elif lc in ("listingdate", "상장일"):
                    rename[col] = "listing_date"
            listing = listing.rename(columns=rename)

            if "ticker" not in listing.columns or "name" not in listing.columns:
                print("  Warning: FDR {} missing ticker/name columns. Got: {}".format(
                    market, list(listing.columns)))
                continue

            listing["market"] = market

            if tickers_filter:
                listing = listing[listing["ticker"].isin(tickers_filter)]

            results.append(listing)
            print("  FDR {}: {} stocks".format(market, len(listing)))

        except Exception as e:
            print("  Warning: FDR StockListing({}) failed: {}".format(market, e))

    if results:
        df = pd.concat(results, ignore_index=True)
        return df

    return pd.DataFrame(columns=["ticker", "name", "market"])


def load_ticker_names_pykrx(tickers):
    """Look up names for specific tickers via pykrx (fallback)."""
    results = []
    try:
        from pykrx import stock
        import time
        for ticker in tickers:
            try:
                name = stock.get_market_ticker_name(ticker)
                time.sleep(0.1)
                if name:
                    results.append({"ticker": ticker, "name": name})
            except Exception:
                pass
    except ImportError:
        pass
    return results


def build_fallback_universe(tickers):
    """Build universe from KNOWN_TICKERS + pykrx name lookups."""
    results = []
    unknown = []

    for ticker in tickers:
        if ticker in KNOWN_TICKERS:
            name_kr, name_en, market = KNOWN_TICKERS[ticker]
            results.append({
                "ticker": ticker, "name": name_kr, "name_en": name_en, "market": market,
            })
        else:
            unknown.append(ticker)

    # Try pykrx for unknown tickers
    if unknown:
        pykrx_names = load_ticker_names_pykrx(unknown)
        found_tickers = set()
        for item in pykrx_names:
            results.append({
                "ticker": item["ticker"], "name": item["name"], "market": "KOSPI",
            })
            found_tickers.add(item["ticker"])

        # Last resort: use ticker as name
        for ticker in unknown:
            if ticker not in found_tickers:
                results.append({
                    "ticker": ticker, "name": ticker, "market": "KOSPI",
                })

    return pd.DataFrame(results) if results else pd.DataFrame(columns=["ticker", "name", "market"])


# ---------------------------------------------------------------------------
# Classification tags
# ---------------------------------------------------------------------------

def tag_stocks(df):
    if df.empty or "name" not in df.columns:
        for col in ["is_preferred", "is_spac", "is_financial", "is_holding", "is_etf", "is_reit"]:
            if col not in df.columns:
                df[col] = False
        return df

    df["is_preferred"] = df.apply(
        lambda r: str(r.get("ticker", ""))[-1] in ("5", "7", "8", "9")
        or any(kw in str(r.get("name", "")) for kw in ["우", "우B", "우C", "2우B"]),
        axis=1,
    )
    df["is_spac"] = df["name"].apply(lambda n: "스팩" in str(n) or "SPAC" in str(n).upper())

    fin_kw = ["은행", "금융", "보험", "증권", "캐피탈", "저축", "카드", "투자", "자산운용", "리스"]
    df["is_financial"] = df["name"].apply(lambda n: any(kw in str(n) for kw in fin_kw))

    hold_kw = ["지주", "홀딩스", "Holdings"]
    df["is_holding"] = df["name"].apply(lambda n: any(kw in str(n) for kw in hold_kw))

    df["is_etf"] = df["name"].apply(
        lambda n: any(kw in str(n).upper() for kw in ["ETF", "KODEX", "TIGER", "KBSTAR", "ARIRANG"])
    )
    df["is_reit"] = df["name"].apply(lambda n: "리츠" in str(n) or "REIT" in str(n).upper())

    return df


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_to_db(conn, df):
    if df.empty:
        return 0

    cur = conn.cursor()
    values = [
        (
            row["ticker"], row["name"], row.get("name_en"),
            row["market"], row.get("sector"), row.get("industry"),
            str(row["listing_date"])[:10] if pd.notna(row.get("listing_date")) else None,
            True, bool(row.get("is_spac", False)), bool(row.get("is_preferred", False)),
            bool(row.get("is_etf", False)), bool(row.get("is_reit", False)),
            bool(row.get("is_financial", False)), bool(row.get("is_holding", False)), "fdr",
        )
        for _, row in df.iterrows()
    ]

    query = """
    INSERT INTO stocks (
        ticker, name, name_en, market, sector, industry, listing_date,
        is_active, is_spac, is_preferred, is_etf, is_reit, is_financial,
        is_holding, source
    ) VALUES %s
    ON CONFLICT (ticker) DO UPDATE SET
        name = EXCLUDED.name, market = EXCLUDED.market,
        sector = COALESCE(EXCLUDED.sector, stocks.sector),
        industry = COALESCE(EXCLUDED.industry, stocks.industry),
        listing_date = COALESCE(EXCLUDED.listing_date, stocks.listing_date),
        is_active = EXCLUDED.is_active, is_spac = EXCLUDED.is_spac,
        is_preferred = EXCLUDED.is_preferred, is_financial = EXCLUDED.is_financial,
        is_holding = EXCLUDED.is_holding, updated_at = NOW()
    """
    execute_values(cur, query, values)
    conn.commit()
    cur.close()
    return len(values)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Korean stock universe")
    parser.add_argument("--tickers", help="Comma-separated tickers (e.g. 005930,000660)")
    parser.add_argument("--limit", type=int, help="Max number of stocks to ingest")
    args = parser.parse_args()

    tickers_filter = set(args.tickers.split(",")) if args.tickers else None

    conn = psycopg2.connect(DATABASE_URL)
    log_id = log_start(conn, {"tickers": args.tickers, "limit": args.limit})

    try:
        print("Loading Korean stock universe...")
        requested = len(tickers_filter) if tickers_filter else "all"
        print("  Requested: {} tickers".format(requested))

        # Try FDR first (most reliable in 2024/2025)
        df = pd.DataFrame(columns=["ticker", "name", "market"])
        try:
            df = load_from_fdr(tickers_filter)
            print("  Loaded {} stocks from FinanceDataReader".format(len(df)))
        except Exception as e:
            print("  Warning: FDR failed: {}".format(e))

        # Fallback for missing tickers
        if tickers_filter and len(df) < len(tickers_filter):
            found = set(df["ticker"].tolist()) if not df.empty else set()
            missing = tickers_filter - found
            if missing:
                print("  Building fallback for {} missing tickers: {}".format(
                    len(missing), sorted(missing)))
                fallback = build_fallback_universe(sorted(missing))
                if not fallback.empty:
                    df = pd.concat([df, fallback], ignore_index=True).drop_duplicates(subset="ticker")

        # If we still have nothing and tickers were specified, use pure fallback
        if df.empty and tickers_filter:
            print("  All sources failed. Using hardcoded fallback for {} tickers.".format(
                len(tickers_filter)))
            df = build_fallback_universe(sorted(tickers_filter))

        if df.empty:
            print("  ERROR: No stocks found from any source. Cannot proceed.")
            log_finish(conn, log_id, "error", error_message="No stocks found from any source")
            sys.exit(1)

        # Tag stock types
        df = tag_stocks(df)

        if args.limit:
            df = df.head(args.limit)
            print("  Limited to {} stocks".format(len(df)))

        n = upsert_to_db(conn, df)
        print("  Upserted {} stocks".format(n))

        log_finish(conn, log_id, "success", rows_processed=len(df), rows_inserted=n)

    except Exception as e:
        log_finish(conn, log_id, "error", error_message=str(e))
        print("ERROR: {}".format(e))
        raise
    finally:
        conn.close()

    print("Done!")
