"""
Ingest KOSPI and KOSDAQ stock universe into Supabase Postgres.

Data sources (in order of preference):
1. pykrx.stock.get_market_ticker_list() — most reliable for current listings
2. FinanceDataReader.StockListing("KRX") — includes sector info

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
    print("ERROR: DATABASE_URL not set. Copy .env.example → .env.local and fill in your Supabase URL.")
    sys.exit(1)

SCRIPT_NAME = "ingest_universe"


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

def load_from_pykrx(tickers_filter=None):
    """Load stock universe using pykrx."""
    from pykrx import stock

    today = datetime.now().strftime("%Y%m%d")
    results = []
    for market in ["KOSPI", "KOSDAQ"]:
        all_tickers = stock.get_market_ticker_list(today, market=market)
        for ticker in all_tickers:
            if tickers_filter and ticker not in tickers_filter:
                continue
            name = stock.get_market_ticker_name(ticker)
            results.append({"ticker": ticker, "name": name, "market": market})

    return pd.DataFrame(results)


def enrich_with_fdr(df: pd.DataFrame) -> pd.DataFrame:
    """Merge sector/industry from FinanceDataReader."""
    try:
        import FinanceDataReader as fdr

        kospi = fdr.StockListing("KOSPI")
        kosdaq = fdr.StockListing("KOSDAQ")
        kospi["market"] = "KOSPI"
        kosdaq["market"] = "KOSDAQ"
        fdr_df = pd.concat([kospi, kosdaq], ignore_index=True)

        # Normalize column names
        rename = {}
        for col in fdr_df.columns:
            lc = col.lower()
            if lc in ("code", "symbol", "종목코드"):
                rename[col] = "ticker"
            elif lc in ("sector", "섹터"):
                rename[col] = "sector"
            elif lc in ("industry", "산업"):
                rename[col] = "industry"
            elif lc in ("listingdate", "상장일"):
                rename[col] = "listing_date"
        fdr_df = fdr_df.rename(columns=rename)

        if "ticker" in fdr_df.columns:
            cols = [c for c in ["ticker", "sector", "industry", "listing_date"] if c in fdr_df.columns]
            fdr_df = fdr_df[cols].drop_duplicates(subset="ticker")
            df = df.merge(fdr_df, on="ticker", how="left")
            print(f"  Enriched {df['sector'].notna().sum()} stocks with sector info from FDR")
    except Exception as e:
        print(f"  Warning: Could not enrich from FDR: {e}")
    return df


# ---------------------------------------------------------------------------
# Classification tags
# ---------------------------------------------------------------------------

def tag_stocks(df: pd.DataFrame) -> pd.DataFrame:
    """Tag preferred shares, SPACs, financials, holdings, etc."""
    df["is_preferred"] = df.apply(
        lambda r: r["ticker"][-1] in ("5", "7", "8", "9")
        or any(kw in str(r["name"]) for kw in ["우", "우B", "우C", "2우B"]),
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

def upsert_to_db(conn, df: pd.DataFrame):
    cur = conn.cursor()
    values = [
        (
            row["ticker"], row["name"], row.get("name_en"),
            row["market"], row.get("sector"), row.get("industry"),
            str(row["listing_date"])[:10] if pd.notna(row.get("listing_date")) else None,
            True, bool(row.get("is_spac", False)), bool(row.get("is_preferred", False)),
            bool(row.get("is_etf", False)), bool(row.get("is_reit", False)),
            bool(row.get("is_financial", False)), bool(row.get("is_holding", False)), "marcap",
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
        try:
            df = load_from_pykrx(tickers_filter)
            print(f"  Loaded {len(df)} stocks from pykrx")
            df = enrich_with_fdr(df)
        except ImportError:
            print("  pykrx not available — cannot proceed")
            sys.exit(1)

        df = tag_stocks(df)

        if args.limit:
            df = df.head(args.limit)
            print(f"  Limited to {len(df)} stocks")

        n = upsert_to_db(conn, df)
        print(f"  Upserted {n} stocks")
        log_finish(conn, log_id, "success", rows_processed=len(df), rows_inserted=n)

    except Exception as e:
        log_finish(conn, log_id, "error", error_message=str(e))
        print(f"ERROR: {e}")
        raise
    finally:
        conn.close()

    print("Done!")
