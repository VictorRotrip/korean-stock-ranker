"""
Ingest KOSPI and KOSDAQ stock universe into Supabase Postgres.

Two sources:

  snapshot    Uses fdr.StockListing("KRX") -- a CURRENT snapshot of all
              KRX listings. Convenient for current-day work but NOT
              point-in-time: a 2024-12-30 universe built this way includes
              stocks that listed AFTER 2024-12-30, biasing any historical
              ranking.

  historical  Uses the FinanceData/marcap GitHub dataset for the requested
              as-of date (or nearest prior trading day). Point-in-time
              correct: only stocks that were actually listed/tradable on
              that date appear. Required for backtests.

  auto        Picks historical when --as-of-date is in the past, snapshot
              otherwise.

Sampling strategies:
  largest    - sort by market cap descending
  liquid     - sort by trading value descending
  random     - random sample
  stratified - proportional to each market total count

Usage:
    # Current/snapshot (legacy):
    python ingest_universe.py --limit-per-market 100 --sample-strategy largest \
        --exclude-preferred --exclude-etf --exclude-spac --exclude-reit \
        --universe-name test_200_large

    # Point-in-time historical:
    python ingest_universe.py --source historical --as-of-date 2024-12-30 --require-pit \
        --limit-per-market 100 --sample-strategy largest \
        --exclude-preferred --exclude-etf --exclude-spac --exclude-reit \
        --universe-name test_200_large_pit_20241230
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
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "ingest_universe"


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


SECTOR_KEYWORDS = {
    "반도체": "Information Technology",
    "전자": "Information Technology",
    "소프트": "Information Technology",
    "IT": "Information Technology",
    "테크": "Information Technology",
    "솔루션": "Information Technology",
    "시스템": "Information Technology",
    "네트워크": "Information Technology",
    "인터넷": "Communication Services",
    "미디어": "Communication Services",
    "게임": "Communication Services",
    "엔터": "Communication Services",
    "통신": "Communication Services",
    "바이오": "Health Care",
    "제약": "Health Care",
    "의료": "Health Care",
    "헬스": "Health Care",
    "셀": "Health Care",
    "진단": "Health Care",
    "은행": "Financials",
    "금융": "Financials",
    "보험": "Financials",
    "증권": "Financials",
    "캐피탈": "Financials",
    "카드": "Financials",
    "저축": "Financials",
    "투자": "Financials",
    "자산운용": "Financials",
    "리스": "Financials",
    "자동차": "Consumer Discretionary",
    "현대차": "Consumer Discretionary",
    "기아": "Consumer Discretionary",
    "호텔": "Consumer Discretionary",
    "백화": "Consumer Discretionary",
    "의류": "Consumer Discretionary",
    "패션": "Consumer Discretionary",
    "식품": "Consumer Staples",
    "음료": "Consumer Staples",
    "농": "Consumer Staples",
    "유통": "Consumer Staples",
    "화학": "Materials",
    "소재": "Materials",
    "철강": "Materials",
    "시멘트": "Materials",
    "유리": "Materials",
    "제지": "Materials",
    "석유": "Energy",
    "에너지": "Energy",
    "정유": "Energy",
    "가스": "Energy",
    "전력": "Utilities",
    "전기": "Utilities",
    "발전": "Utilities",
    "건설": "Industrials",
    "중공업": "Industrials",
    "기계": "Industrials",
    "조선": "Industrials",
    "항공": "Industrials",
    "해운": "Industrials",
    "운송": "Industrials",
    "물산": "Industrials",
    "엔지니어": "Industrials",
    "방산": "Industrials",
    "리츠": "Real Estate",
    "부동산": "Real Estate",
}


def enrich_sector(df):
    """Best-effort sector assignment from stock name keywords."""
    if df.empty or "name" not in df.columns:
        return df

    def guess_sector(name):
        if not name:
            return None
        for keyword, sector in SECTOR_KEYWORDS.items():
            if keyword in str(name):
                return sector
        return None

    if "sector" not in df.columns:
        df["sector"] = None
    mask = df["sector"].isna()
    df.loc[mask, "sector"] = df.loc[mask, "name"].apply(guess_sector)
    return df


def load_from_historical_marcap(as_of_date, markets=None):
    """Load KRX universe from the FinanceData/marcap historical dataset.

    Returns (DataFrame, actual_trading_date) or (None, None) on failure.
    Output schema is compatible with downstream tag_stocks / apply_exclusions
    / sample_stocks: ticker, name, market, market_cap, shares_outstanding,
    trading_value, close, dept.
    """
    try:
        from data_sources import marcap_historical
    except ImportError as e:
        print("  ERROR: data_sources.marcap_historical not importable: {}".format(e))
        return None, None

    df, actual_date = marcap_historical.fetch_marcap_date(as_of_date)
    if df is None or len(df) == 0:
        print("  ERROR: historical marcap returned no data for {}".format(as_of_date))
        return None, None

    print("  Historical marcap: {} rows on trading date {} (requested {})".format(
        len(df), actual_date, as_of_date))

    if markets and "market" in df.columns:
        before = len(df)
        df = df[df["market"].isin(markets)].copy()
        print("  Filtered to markets {}: {} -> {} stocks".format(
            ",".join(markets), before, len(df)))

    return df, actual_date


def load_from_fdr(markets=None):
    """Load full KRX universe from FinanceDataReader (CURRENT snapshot)."""
    import FinanceDataReader as fdr

    if markets is None:
        markets = ["KOSPI", "KOSDAQ"]

    try:
        listing = fdr.StockListing("KRX")
    except Exception as e:
        print("  ERROR: fdr.StockListing('KRX') failed: {}".format(e))
        return pd.DataFrame()

    if listing is None or listing.empty:
        print("  ERROR: fdr.StockListing('KRX') returned empty")
        return pd.DataFrame()

    rename = {}
    for col in listing.columns:
        lc = col.lower()
        if lc in ("code", "symbol"):
            rename[col] = "ticker"
        elif lc == "name":
            rename[col] = "name"
        elif lc == "market":
            rename[col] = "market"
        elif lc == "dept":
            rename[col] = "dept"
        elif lc == "marcap":
            rename[col] = "market_cap"
        elif lc == "stocks":
            rename[col] = "shares_outstanding"
        elif lc == "amount":
            rename[col] = "trading_value"
        elif lc == "close":
            rename[col] = "close"
        elif lc == "sector":
            rename[col] = "sector"
        elif lc == "industry":
            rename[col] = "industry"
        elif lc in ("listingdate",):
            rename[col] = "listing_date"
    listing = listing.rename(columns=rename)

    if "ticker" not in listing.columns or "name" not in listing.columns:
        print("  ERROR: Missing ticker/name columns. Got: {}".format(
            list(listing.columns)))
        return pd.DataFrame()

    if "market" in listing.columns:
        market_map = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ", "KONEX": "KONEX"}
        listing["market"] = listing["market"].map(
            lambda m: market_map.get(str(m).upper().strip(), str(m)))
        listing = listing[listing["market"].isin(markets)]

    print("  FDR KRX: {} stocks (markets: {})".format(
        len(listing), ", ".join(markets)))
    return listing


def tag_stocks(df):
    """Add boolean classification columns."""
    if df.empty or "name" not in df.columns:
        for col in ["is_preferred", "is_spac", "is_financial",
                     "is_holding", "is_etf", "is_reit"]:
            if col not in df.columns:
                df[col] = False
        return df

    pref_keywords = ["우", "우B", "2우B", "우선", "Pfd"]
    df["is_preferred"] = df.apply(
        lambda r: (
            str(r.get("ticker", ""))[-1] in ("5", "7", "8", "9")
            and any(kw in str(r.get("name", "")) for kw in pref_keywords)
        ),
        axis=1,
    )

    df["is_spac"] = df["name"].apply(
        lambda n: "스팩" in str(n) or "SPAC" in str(n).upper())

    fin_kw = ["은행", "금융", "보험", "증권", "캐피탈", "저축", "카드", "투자", "자산운용", "리스"]
    df["is_financial"] = df["name"].apply(
        lambda n: any(kw in str(n) for kw in fin_kw))

    hold_kw = ["지주", "홀딩스", "Holdings"]
    df["is_holding"] = df["name"].apply(
        lambda n: any(kw in str(n) for kw in hold_kw))

    etf_kw = ["ETF", "KODEX", "TIGER", "KBSTAR", "ARIRANG", "ACE", "SOL", "HANARO", "ETN", "KOSEF", "FOCUS"]
    df["is_etf"] = df["name"].apply(
        lambda n: any(kw in str(n).upper() for kw in etf_kw))

    df["is_reit"] = df["name"].apply(
        lambda n: "리츠" in str(n) or "REIT" in str(n).upper())

    return df


def apply_exclusions(df, args):
    """Remove excluded stock types."""
    counts = {}
    before = len(df)

    if args.exclude_preferred and "is_preferred" in df.columns:
        n = df["is_preferred"].sum()
        df = df[~df["is_preferred"]]
        counts["preferred"] = int(n)

    if args.exclude_etf and "is_etf" in df.columns:
        n = df["is_etf"].sum()
        df = df[~df["is_etf"]]
        counts["etf"] = int(n)

    if args.exclude_spac and "is_spac" in df.columns:
        n = df["is_spac"].sum()
        df = df[~df["is_spac"]]
        counts["spac"] = int(n)

    if args.exclude_reit and "is_reit" in df.columns:
        n = df["is_reit"].sum()
        df = df[~df["is_reit"]]
        counts["reit"] = int(n)

    if args.exclude_financials and "is_financial" in df.columns:
        n = df["is_financial"].sum()
        df = df[~df["is_financial"]]
        counts["financial"] = int(n)

    if args.min_market_cap and "market_cap" in df.columns:
        mc_col = pd.to_numeric(df["market_cap"], errors="coerce")
        n = (mc_col < args.min_market_cap).sum()
        df = df[mc_col >= args.min_market_cap]
        counts["below_min_mcap"] = int(n)

    if args.min_trading_value and "trading_value" in df.columns:
        tv_col = pd.to_numeric(df["trading_value"], errors="coerce")
        n = (tv_col < args.min_trading_value).sum()
        df = df[tv_col >= args.min_trading_value]
        counts["below_min_tv"] = int(n)

    after = len(df)
    counts["total_excluded"] = before - after
    return df, counts


def _sort_by_strategy(df, strategy):
    """Sort df according to strategy."""
    if strategy == "largest" and "market_cap" in df.columns:
        mc = pd.to_numeric(df["market_cap"], errors="coerce")
        df = df.assign(_sort_key=mc).sort_values(
            "_sort_key", ascending=False, na_position="last"
        ).drop(columns=["_sort_key"])
    elif strategy == "liquid" and "trading_value" in df.columns:
        tv = pd.to_numeric(df["trading_value"], errors="coerce")
        df = df.assign(_sort_key=tv).sort_values(
            "_sort_key", ascending=False, na_position="last"
        ).drop(columns=["_sort_key"])
    elif strategy == "random":
        df = df.sample(frac=1, random_state=42).reset_index(drop=True)
    else:
        if "market_cap" in df.columns:
            mc = pd.to_numeric(df["market_cap"], errors="coerce")
            df = df.assign(_sort_key=mc).sort_values(
                "_sort_key", ascending=False, na_position="last"
            ).drop(columns=["_sort_key"])
    return df


def _stratified_sample(df, limit):
    """Proportional sample across markets."""
    markets = df["market"].value_counts()
    total = markets.sum()
    parts = []
    remaining = limit
    for i, (market, count) in enumerate(markets.items()):
        if i == len(markets) - 1:
            n = remaining
        else:
            n = max(1, int(round(limit * count / total)))
            remaining -= n
        mdf = df[df["market"] == market]
        mdf = _sort_by_strategy(mdf, "largest")
        parts.append(mdf.head(n))
        print("  stratified {}: {} of {} (pool={})".format(
            market, min(len(mdf), n), limit, count))
    return pd.concat(parts, ignore_index=True)


def sample_stocks(df, args):
    """Apply sampling strategy to select stocks."""
    strategy = args.sample_strategy or "largest"
    limit = args.limit
    limit_per_market = args.limit_per_market

    if limit_per_market:
        parts = []
        for market in sorted(df["market"].unique()):
            mdf = df[df["market"] == market]
            mdf = _sort_by_strategy(mdf, strategy)
            parts.append(mdf.head(limit_per_market))
            print("  {} {}: {} -> {} selected".format(
                strategy, market, len(mdf), min(len(mdf), limit_per_market)))
        return pd.concat(parts, ignore_index=True)

    if limit:
        if strategy == "stratified":
            return _stratified_sample(df, limit)
        df = _sort_by_strategy(df, strategy)
        return df.head(limit)

    return df


def upsert_to_db(conn, df, dry_run=False, source_label="fdr"):
    if df.empty:
        return 0, 0

    if dry_run:
        print("  [DRY RUN] Would insert/update {} stocks".format(len(df)))
        for market in sorted(df["market"].unique()):
            mdf = df[df["market"] == market]
            print("    {}: {}".format(market, len(mdf)))
            for _, row in mdf.head(10).iterrows():
                flags = []
                if row.get("is_financial"):
                    flags.append("FIN")
                if row.get("is_preferred"):
                    flags.append("PREF")
                if row.get("is_holding"):
                    flags.append("HOLD")
                flag_str = " [{}]".format(",".join(flags)) if flags else ""
                mc = row.get("market_cap")
                mc_str = " mcap={:,.0f}".format(mc) if pd.notna(mc) and mc else ""
                print("      {} {}{}{}".format(
                    row["ticker"], (row.get("name") or "?")[:20],
                    mc_str, flag_str))
            if len(mdf) > 10:
                print("      ... and {} more".format(len(mdf) - 10))
        return len(df), 0

    cur = conn.cursor()
    values = []
    for _, row in df.iterrows():
        listing_date = None
        if pd.notna(row.get("listing_date")):
            listing_date = str(row["listing_date"])[:10]
        values.append((
            row["ticker"], row["name"], row.get("name_en"),
            row["market"], row.get("sector"), row.get("industry"),
            listing_date, True,
            bool(row.get("is_spac", False)),
            bool(row.get("is_preferred", False)),
            bool(row.get("is_etf", False)),
            bool(row.get("is_reit", False)),
            bool(row.get("is_financial", False)),
            bool(row.get("is_holding", False)),
            source_label,
        ))

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
        is_preferred = EXCLUDED.is_preferred, is_etf = EXCLUDED.is_etf,
        is_reit = EXCLUDED.is_reit, is_financial = EXCLUDED.is_financial,
        is_holding = EXCLUDED.is_holding, updated_at = NOW()
    """
    execute_values(cur, query, values)
    conn.commit()
    cur.close()
    return len(values), 0


def print_summary(df):
    """Print summary stats about the selected universe."""
    if df.empty:
        print("  [Summary] No stocks selected")
        return

    total = len(df)
    markets = df["market"].value_counts().to_dict() if "market" in df.columns else {}
    preferred = int(df["is_preferred"].sum()) if "is_preferred" in df.columns else 0
    etf = int(df["is_etf"].sum()) if "is_etf" in df.columns else 0
    spac = int(df["is_spac"].sum()) if "is_spac" in df.columns else 0
    reit = int(df["is_reit"].sum()) if "is_reit" in df.columns else 0
    financial = int(df["is_financial"].sum()) if "is_financial" in df.columns else 0
    holding = int(df["is_holding"].sum()) if "is_holding" in df.columns else 0
    with_sector = int(df["sector"].notna().sum()) if "sector" in df.columns else 0

    print()
    print("  === UNIVERSE SUMMARY ===")
    print("  Total: {} stocks".format(total))
    for m in sorted(markets):
        print("    {}: {}".format(m, markets[m]))
    print("  Flags: preferred={}, etf={}, spac={}, reit={}, financial={}, holding={}".format(
        preferred, etf, spac, reit, financial, holding))
    print("  Sector coverage: {}/{} ({:.0f}%)".format(
        with_sector, total, 100 * with_sector / total if total else 0))

    if "sector" in df.columns and with_sector > 0:
        print("  Sector breakdown:")
        sector_counts = df["sector"].value_counts()
        for sector, count in sector_counts.items():
            if pd.notna(sector):
                print("    {}: {}".format(sector, count))
        null_count = df["sector"].isna().sum()
        if null_count > 0:
            print("    (no sector): {}".format(null_count))

    if "market_cap" in df.columns:
        mc = pd.to_numeric(df["market_cap"], errors="coerce").dropna()
        if not mc.empty:
            print("  Market cap range: {:,.0f} - {:,.0f}  (median {:,.0f})".format(
                mc.min(), mc.max(), mc.median()))
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest Korean stock universe with sampling guardrails")

    parser.add_argument("--tickers",
        help="Comma-separated tickers (overrides all sampling)")
    parser.add_argument("--market", default="KOSPI,KOSDAQ",
        help="Markets to include (default: KOSPI,KOSDAQ)")
    parser.add_argument("--limit", type=int,
        help="Total max stocks (applied after exclusions)")
    parser.add_argument("--limit-per-market", type=int,
        help="Max stocks per market (overrides --limit)")

    parser.add_argument("--sample-strategy",
        choices=["largest", "liquid", "random", "stratified"],
        default="largest",
        help="How to pick stocks when limit applies (default: largest)")

    parser.add_argument("--exclude-preferred", action="store_true",
        help="Exclude preferred shares")
    parser.add_argument("--exclude-etf", action="store_true",
        help="Exclude ETFs and ETNs")
    parser.add_argument("--exclude-spac", action="store_true",
        help="Exclude SPACs")
    parser.add_argument("--exclude-reit", action="store_true",
        help="Exclude REITs")
    parser.add_argument("--exclude-financials", action="store_true",
        help="Exclude financial-sector stocks")

    parser.add_argument("--min-market-cap", type=float,
        help="Minimum market cap in KRW")
    parser.add_argument("--min-trading-value", type=float,
        help="Minimum trading value in KRW")

    # Universe naming
    parser.add_argument("--universe-name",
        help="Name this universe (e.g. test_200_large). "
             "Saves membership to universe_memberships table. "
             "Downstream scripts can use --universe to reference it.")

    parser.add_argument("--dry-run", action="store_true",
        help="Print what would be inserted but don't write")
    parser.add_argument("--resume", action="store_true",
        help="Skip tickers already in DB")

    # Point-in-time options
    parser.add_argument("--as-of-date",
        help="Reference date YYYY-MM-DD. Required for --source historical; "
             "in --source auto, triggers historical mode for past dates.")
    parser.add_argument("--source", default="snapshot",
        choices=["snapshot", "historical", "auto"],
        help=("Universe source. snapshot=current FDR listing (NOT PIT); "
              "historical=FinanceData/marcap on as-of date (PIT-correct); "
              "auto=historical for past --as-of-date, snapshot otherwise. "
              "Default: snapshot."))
    parser.add_argument("--require-pit", action="store_true",
        help=("Refuse to fall back to snapshot if historical source is "
              "unavailable or --as-of-date is current. Use for backtests."))

    args = parser.parse_args()

    markets = [m.strip() for m in args.market.split(",")]
    tickers_filter = set(args.tickers.split(",")) if args.tickers else None

    conn = psycopg2.connect(DATABASE_URL)

    log_id = log_start(conn, {
        "tickers": args.tickers,
        "market": args.market,
        "limit": args.limit,
        "limit_per_market": args.limit_per_market,
        "sample_strategy": args.sample_strategy,
        "exclude_preferred": args.exclude_preferred,
        "exclude_etf": args.exclude_etf,
        "exclude_spac": args.exclude_spac,
        "exclude_reit": args.exclude_reit,
        "exclude_financials": args.exclude_financials,
        "min_market_cap": args.min_market_cap,
        "min_trading_value": args.min_trading_value,
        "universe_name": args.universe_name,
        "dry_run": args.dry_run,
        "resume": args.resume,
    })

    try:
        print()
        print("=" * 60)
        print("  Korean Stock Universe Ingestion")
        if args.universe_name:
            print("  Universe: {}".format(args.universe_name))
        print("  Markets: {}".format(", ".join(markets)))
        if args.limit_per_market:
            print("  Limit: {} per market".format(args.limit_per_market))
        elif args.limit:
            print("  Limit: {} total".format(args.limit))
        print("  Strategy: {}".format(args.sample_strategy))
        excl = []
        if args.exclude_preferred:
            excl.append("preferred")
        if args.exclude_etf:
            excl.append("ETF/ETN")
        if args.exclude_spac:
            excl.append("SPAC")
        if args.exclude_reit:
            excl.append("REIT")
        if args.exclude_financials:
            excl.append("financials")
        if excl:
            print("  Excluding: {}".format(", ".join(excl)))
        if args.min_market_cap:
            print("  Min market cap: {:,.0f}".format(args.min_market_cap))
        if args.min_trading_value:
            print("  Min trading value: {:,.0f}".format(args.min_trading_value))
        print("=" * 60)
        print()

        # ---- Resolve source (snapshot / historical / auto) ----
        today_iso = datetime.now().strftime("%Y-%m-%d")
        is_historical_date = bool(args.as_of_date) and args.as_of_date < today_iso

        if args.source == "auto":
            effective_source = "historical" if is_historical_date else "snapshot"
        else:
            effective_source = args.source

        if effective_source == "historical" and not args.as_of_date:
            print("ERROR: --source historical requires --as-of-date")
            log_finish(conn, log_id, "error",
                       error_message="historical without as-of-date")
            conn.close()
            sys.exit(1)

        if args.require_pit and not is_historical_date:
            print("ERROR: --require-pit requires --as-of-date in the past")
            log_finish(conn, log_id, "error",
                       error_message="require-pit without past as-of-date")
            conn.close()
            sys.exit(1)

        actual_trading_date = None
        if effective_source == "historical":
            print("Loading historical marcap universe for {}...".format(args.as_of_date))
            df, actual_trading_date = load_from_historical_marcap(
                args.as_of_date, markets)
            if df is None or len(df) == 0:
                if args.require_pit:
                    print("ERROR: --require-pit but historical source unavailable. "
                          "Refusing to fall back to snapshot.")
                    log_finish(conn, log_id, "error",
                               error_message="require-pit historical unavailable")
                    conn.close()
                    sys.exit(1)
                print("WARNING: historical source unavailable, falling back to snapshot.")
                print("         Universe will NOT be point-in-time safe.")
                effective_source = "snapshot_fallback"
                df = load_from_fdr(markets)
        else:
            if args.source == "snapshot" and is_historical_date:
                print("WARNING: --source snapshot used with historical "
                      "as-of-date {}. Universe is NOT point-in-time safe.".format(
                          args.as_of_date))
                if args.require_pit:
                    print("ERROR: --require-pit forbids snapshot for historical date.")
                    log_finish(conn, log_id, "error",
                               error_message="require-pit forbids snapshot for historical")
                    conn.close()
                    sys.exit(1)
            print("Loading KRX universe (snapshot)...")
            df = load_from_fdr(markets)

        if tickers_filter:
            print("Filtering to specific tickers: {}".format(
                ", ".join(sorted(tickers_filter))))
            df = df[df["ticker"].isin(tickers_filter)]
            print("  Matched: {}/{}".format(len(df), len(tickers_filter)))

        if df.empty:
            print("ERROR: No stocks found. Cannot proceed.")
            log_finish(conn, log_id, "error",
                       error_message="No stocks found")
            conn.close()
            sys.exit(1)

        df = tag_stocks(df)
        df = enrich_sector(df)

        df, excl_counts = apply_exclusions(df, args)
        if excl_counts.get("total_excluded", 0) > 0:
            print("  Exclusions applied:")
            for k, v in excl_counts.items():
                if k != "total_excluded" and v > 0:
                    print("    {}: {} removed".format(k, v))
            print("    Remaining: {}".format(len(df)))

        if df.empty:
            print("ERROR: All stocks excluded. Loosen filters.")
            log_finish(conn, log_id, "error",
                       error_message="All stocks excluded")
            conn.close()
            sys.exit(1)

        if not tickers_filter:
            df = sample_stocks(df, args)

        if args.resume:
            cur = conn.cursor()
            cur.execute("SELECT ticker FROM stocks")
            existing = set(r[0] for r in cur.fetchall())
            cur.close()
            before = len(df)
            df = df[~df["ticker"].isin(existing)]
            skipped = before - len(df)
            if skipped:
                print("  Resume: skipped {} existing, {} new".format(
                    skipped, len(df)))
            if df.empty:
                print("All tickers already in DB. Nothing to do.")
                log_finish(conn, log_id, "success",
                           rows_processed=0, rows_skipped=skipped)
                conn.close()
                print("Done!")
                sys.exit(0)

        print_summary(df)

        # Stock-source label: 'marcap_historical' if we used the PIT source,
        # 'fdr' for snapshot. Recorded on stocks.source so future audits
        # can tell which path created the row.
        stocks_source_label = (
            "marcap_historical" if effective_source == "historical" else "fdr"
        )

        n, _ = upsert_to_db(conn, df, dry_run=args.dry_run,
                            source_label=stocks_source_label)
        if not args.dry_run:
            print("  Upserted {} stocks to database".format(n))

        # Save universe membership
        is_pit_safe = (effective_source == "historical")

        if args.universe_name and not args.dry_run:
            uname = args.universe_name
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM universe_memberships WHERE universe_name = %s",
                (uname,))
            deleted = cur.rowcount
            mem_values = [(uname, row["ticker"]) for _, row in df.iterrows()]
            execute_values(cur,
                "INSERT INTO universe_memberships (universe_name, ticker) "
                "VALUES %s ON CONFLICT DO NOTHING",
                mem_values)
            conn.commit()
            cur.close()
            print("  Universe '{}': {} members saved (replaced {})".format(
                uname, len(mem_values), deleted))
        elif args.universe_name and args.dry_run:
            print("  [DRY RUN] Would save universe '{}' with {} members".format(
                args.universe_name, len(df)))

        # ---- Universe metadata block (always print) ----
        # universe_memberships currently has no metadata columns; we print
        # this for the operator. TODO: persist in a universe_metadata table
        # so the UI / diagnose script can read it back without grepping logs.
        excl = []
        if args.exclude_preferred: excl.append("preferred")
        if args.exclude_etf:       excl.append("etf")
        if args.exclude_spac:      excl.append("spac")
        if args.exclude_reit:      excl.append("reit")
        if args.exclude_financials: excl.append("financials")

        market_counts = df["market"].value_counts().to_dict() if "market" in df.columns else {}

        print()
        print("  === UNIVERSE METADATA ===")
        print("  Universe name:       {0}".format(args.universe_name or "(unnamed)"))
        print("  Source:              {0}".format(effective_source))
        if args.as_of_date:
            print("  Requested as-of:     {0}".format(args.as_of_date))
        if actual_trading_date:
            print("  Trading date used:   {0}".format(actual_trading_date))
        print("  Selection strategy:  {0}".format(args.sample_strategy))
        if market_counts:
            print("  Market counts:       {0}".format(
                ", ".join("{}={}".format(m, c)
                         for m, c in sorted(market_counts.items()))))
        print("  Total selected:      {0}".format(len(df)))
        print("  Exclusions applied:  {0}".format(
            ", ".join(excl) if excl else "(none)"))
        print("  Point-in-time safe:  {0}".format("yes" if is_pit_safe else "no"))
        if not is_pit_safe and args.as_of_date and is_historical_date:
            print("  ! WARNING: --as-of-date {0} is historical but universe is "
                  "snapshot-derived. NOT suitable for backtests.".format(
                      args.as_of_date))

        log_finish(conn, log_id, "success",
                   rows_processed=len(df), rows_inserted=n)

    except Exception as e:
        log_finish(conn, log_id, "error", error_message=str(e))
        print("ERROR: {}".format(e))
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()

    print("Done!")
