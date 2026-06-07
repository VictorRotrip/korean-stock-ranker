"""
Pre-compute forward returns for the rank-performance backtester.

For each snapshot_date with a ranking_snapshots row (one per
rebalance), compute the forward total return at multiple horizons
per ticker. The webapp's /backtest page joins these with
factor_snapshots to bucket the universe into deciles and plot the
top-minus-bottom spread without shipping raw price data to the
browser.

Storage: backtest_forward_returns (primary key = ticker +
snapshot_date + horizon_days). Idempotent upsert.

Horizons (calendar days): 30 (~1m), 90 (~3m), 180 (~6m), 365 (~12m).

Lookup logic per (ticker, snapshot_date, horizon):
  - start_close  = close on snapshot_date if available, else the
                   closest close within 5 prior trading days
  - target_end   = snapshot_date + horizon_days
  - end_close    = close on target_end if available, else the
                   closest close within 5 trading days after
  - forward_return = end_close / start_close - 1
  - skip if either close missing or non-positive

Usage
-----
    # Compute returns for all snapshot dates currently in ranking_snapshots:
    python backtest_forward_returns.py

    # Restrict to one universe (so it doesn't process foreign tickers):
    python backtest_forward_returns.py --universe krx_all_current

    # Dry run:
    python backtest_forward_returns.py --dry-run

    # Override horizons:
    python backtest_forward_returns.py --horizons 30,90,180,365,730
"""

import os
import sys
import argparse
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

SCRIPT_NAME = "backtest_forward_returns"
DEFAULT_HORIZONS = [30, 90, 180, 365]   # calendar days


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
        (status, rows_processed, rows_inserted, rows_updated,
         rows_skipped, error_message, log_id),
    )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def get_snapshot_dates(conn, since=None, until=None):
    """Distinct ranking_snapshot dates — these are our rebalance points."""
    cur = conn.cursor()
    sql = "SELECT DISTINCT date FROM ranking_snapshots"
    params = []
    if since or until:
        sql += " WHERE 1=1"
        if since:
            sql += " AND date >= %s"
            params.append(since)
        if until:
            sql += " AND date <= %s"
            params.append(until)
    sql += " ORDER BY date"
    cur.execute(sql, params)
    rows = [r[0] for r in cur.fetchall()]
    cur.close()
    return rows


def get_universe_tickers(conn, universe_name=None):
    cur = conn.cursor()
    if universe_name:
        cur.execute(
            "SELECT ticker FROM universe_memberships "
            "WHERE universe_name = %s ORDER BY ticker",
            (universe_name,))
    else:
        cur.execute(
            "SELECT ticker FROM stocks "
            "WHERE is_active = TRUE ORDER BY ticker")
    rows = [r[0] for r in cur.fetchall()]
    cur.close()
    return rows


# ---------------------------------------------------------------------------
# Forward-return computation
# ---------------------------------------------------------------------------

def fetch_close_window(conn, tickers, start_date, end_date):
    """Return dict ticker -> sorted list of (date, close, shares_outstanding).

    shares_outstanding may be None for days where we don't have it (older
    marcap rows occasionally lack it). The split detector skips those days.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, date, close, shares_outstanding
        FROM daily_prices
        WHERE ticker = ANY(%s)
          AND date >= %s AND date <= %s
          AND close IS NOT NULL AND close > 0
        ORDER BY ticker, date
    """, (tickers, start_date, end_date))
    out = {}
    for ticker, d, c, sh in cur.fetchall():
        sh_f = float(sh) if (sh is not None and sh > 0) else None
        out.setdefault(ticker, []).append((d, float(c), sh_f))
    cur.close()
    return out


def detect_splits_in_window(rows, window_start, window_end,
                            min_shares_ratio=1.5):
    """Detect stock splits / reverse splits within (window_start, window_end].

    Args:
      rows: list of (date, close, shares_outstanding) sorted ascending,
            covering at least a few days before window_start.
      window_start, window_end: inclusive-exclusive split-search range.
      min_shares_ratio: minimum |log(shares_today / shares_yesterday)| that
                        qualifies for a split candidate. 1.5 covers the
                        smallest realistic split (3:2 = 1.5x) and excludes
                        normal dilutive issuance (typically <10% per event).

    Returns:
      list of (effective_date, split_factor) where:
        split_factor = post_shares / pre_shares
        > 1 = forward split (e.g. 2 for a 2:1 split)
        < 1 = reverse split / consolidation (e.g. 0.1 for a 10:1 reverse)

    Logic:
      A clean split has:
        * Sharp shares-outstanding jump (>=1.5x in either direction)
        * Price moves approximately inversely (so market cap is preserved,
          ±20% tolerance for normal day's volatility)
      Anything that doesn't pass both filters is left alone — it's
      probably an issuance, buyback, or just noisy data.
    """
    splits = []
    prev = None
    for cur in rows:
        d, close, sh = cur
        if prev is not None:
            prev_d, prev_close, prev_sh = prev
            if (prev_sh is not None and sh is not None
                    and prev_sh > 0 and sh > 0
                    and prev_close > 0 and close > 0):
                shares_ratio = sh / prev_sh
                price_ratio = close / prev_close
                # Big enough share-count jump?
                big = shares_ratio >= min_shares_ratio or shares_ratio <= 1.0 / min_shares_ratio
                if big:
                    # Market cap should be preserved (clean split). The
                    # combined ratio shares_ratio * price_ratio should be
                    # ~1.0 within ±20% tolerance.
                    combined = shares_ratio * price_ratio
                    if 0.8 <= combined <= 1.25:
                        if window_start < d <= window_end:
                            splits.append((d, shares_ratio))
        prev = cur
    return splits


def fetch_dividends_window(conn, tickers, start_year, end_year):
    """Return dict ticker -> list of (ex_date, dps) for dividends with
    fiscal-year ends in [start_year, end_year].

    Dividends per share are derived from `financial_statements.dividends_paid`
    (total cash dividends in fiscal year, from DART) divided by
    `shares_outstanding`. The ex-date is approximated as the fiscal year-end
    minus 3 calendar days — close enough for monthly rebalances since
    Korean Dec-fiscal-year companies have ex-dates around Dec 27-29.

    Notes & caveats:
      * dividends_paid in DART is signed: companies report a NEGATIVE
        figure when reporting cash *outflows* on the cash-flow statement.
        We take abs() so the dividend amount is positive for our addition.
      * Companies that don't report dividends or have null shares
        outstanding are silently absent from the result.
      * For Mar/Jun-fiscal-year-end companies (rare), the approximation
        is closer to those year-ends. Still reasonable.
      * Interim dividends (paid mid-year) are NOT captured here — we'd
        need precise dividend-decision filings from DART for that.
        Lump-sum annual treatment understates ~5-10% of stocks that pay
        semi-annually.
    """
    if not tickers:
        return {}
    cur = conn.cursor()
    cur.execute("""
        SELECT fs.ticker, fs.period_end,
               fs.dividends_paid,
               COALESCE(fs.shares_outstanding,
                        dp.shares_outstanding) AS sh_out
        FROM financial_statements fs
        LEFT JOIN LATERAL (
            SELECT shares_outstanding
            FROM daily_prices
            WHERE daily_prices.ticker = fs.ticker
              AND daily_prices.date <= fs.period_end
              AND daily_prices.shares_outstanding IS NOT NULL
              AND daily_prices.shares_outstanding > 0
            ORDER BY date DESC LIMIT 1
        ) dp ON TRUE
        WHERE fs.ticker = ANY(%s)
          AND fs.statement_type = 'annual'
          AND fs.dividends_paid IS NOT NULL
          AND fs.dividends_paid <> 0
          AND EXTRACT(YEAR FROM fs.period_end) >= %s
          AND EXTRACT(YEAR FROM fs.period_end) <= %s
    """, (tickers, start_year, end_year))
    out = {}
    for ticker, period_end, dividends_paid, sh_out in cur.fetchall():
        if not sh_out or sh_out <= 0:
            continue
        # Cash dividends paid can come in as a negative number (cash
        # outflow convention) — take absolute value.
        amount = abs(float(dividends_paid)) / float(sh_out)
        if amount <= 0:
            continue
        # Approximate ex-date as fiscal year-end minus 3 calendar days
        ex_date = period_end - timedelta(days=3)
        out.setdefault(ticker, []).append((ex_date, amount))
    # Sort each ticker's list by ex_date
    for t in out:
        out[t].sort(key=lambda x: x[0])
    cur.close()
    return out


def find_close_on_or_before(rows, target_date, max_days_back=5):
    """rows = list of (date, close, shares_outstanding) sorted ascending.
    Return the last (date, close) with date <= target_date, but not more than
    max_days_back calendar days earlier. None if no match."""
    best = None
    for r in rows:
        d, c, _ = r
        if d > target_date:
            break
        best = (d, c)
    if best is None:
        return None
    days_gap = (target_date - best[0]).days
    if days_gap > max_days_back:
        return None
    return best


def find_close_on_or_after(rows, target_date, max_days_forward=5):
    """First (date, close) with date >= target_date, within
    max_days_forward calendar days. None if no match.

    Accepts 3-tuples (date, close, shares_outstanding) from fetch_close_window
    but returns only (date, close).
    """
    for r in rows:
        d, c, _ = r
        if d < target_date:
            continue
        days_gap = (d - target_date).days
        if days_gap > max_days_forward:
            return None
        return (d, c)
    return None


def find_end_close(rows, target_date, start_date, max_days_forward=5):
    """Return (end_date, end_close, is_delisting_proxy) for the holding period.

    Logic:
      1. Try the normal "first price on/after target_date within window".
      2. If that fails AND we have at least one price strictly after the
         start_date but before target_date, the stock probably DELISTED
         mid-window. Use the latest available price as a delisting proxy.
         The investor sold at the last quoted price (M&A take-out, last
         tick before bankruptcy halt, etc.).
      3. Otherwise return None — the horizon simply hasn't elapsed yet.
    """
    # Normal path: price on/after target_date
    after = find_close_on_or_after(rows, target_date, max_days_forward)
    if after is not None:
        return after[0], after[1], False

    # Delisting proxy: latest available price strictly inside (start, target)
    # — that means the stock stopped trading mid-window.
    last_in_window = None
    for r in rows:
        d, c, _ = r
        if d > start_date and d < target_date:
            last_in_window = (d, c)
    if last_in_window is not None:
        return last_in_window[0], last_in_window[1], True

    return None


def compute_returns_for_date(conn, snapshot_date, tickers, horizons):
    """Returns list of upsert tuples for this snapshot_date."""
    max_h = max(horizons)
    window_start = snapshot_date - timedelta(days=10)
    window_end = snapshot_date + timedelta(days=max_h + 10)

    by_ticker = fetch_close_window(conn, tickers, window_start, window_end)

    # Pre-fetch dividends for all relevant fiscal years touched by any
    # horizon at this snapshot date. With max_h = 365, this is 1 year
    # forward and we widen by 1 to capture year-end dividends comfortably.
    div_start_year = snapshot_date.year
    div_end_year = snapshot_date.year + 2
    divs_by_ticker = fetch_dividends_window(conn, tickers,
                                            div_start_year, div_end_year)

    out = []
    for ticker, rows in by_ticker.items():
        start = find_close_on_or_before(rows, snapshot_date)
        if start is None:
            continue
        start_date, start_close = start
        ticker_divs = divs_by_ticker.get(ticker, [])
        for h in horizons:
            target_end = snapshot_date + timedelta(days=h)
            end = find_end_close(rows, target_end, start_date)
            if end is None:
                # Horizon hasn't elapsed yet. Will be filled on a future
                # run once daily_prices catches up.
                continue
            end_date, end_close, is_delisted = end

            # ----- Split adjustment ------------------------------------
            # Detect any stock splits / reverse splits that occurred
            # strictly INSIDE (snapshot_date, end_date]. For each split,
            # split_factor = post_shares / pre_shares. The end_close on
            # those post-split days is artificially scaled by 1/factor
            # (forward split = lower price; reverse split = higher
            # price). To recover the actual investor return we divide
            # the end_close by the cumulative product of split_factors.
            #
            # Worked example (100:1 reverse split, Q4 2022 KOSDAQ wave):
            #   pre: 100M shares @ 400 KRW close
            #   post: 1M shares @ 40,000 KRW close
            #   split_factor = 1/100 = 0.01
            #   raw end_close = 40,000
            #   adjusted = 40,000 / 0.01 ... wait that gives 4,000,000
            # Hmm let me re-check. Adjusted should match the pre-split
            # price scale (~400-ish if organic flat). Adjusted should be
            # end_close * (split_factor). Let me redo:
            #   end_close = 40,000  (post-split close)
            #   investor's shares at end = pre_shares × split_factor
            #     = 100M × 0.01 = 1M shares
            #   end_value = 1M × 40,000 = 40B
            #   start_value = 100M × 400 = 40B
            #   ratio = end_value / start_value = 1.0 — flat, correct.
            # Equivalent per-share view: adjusted_end_per_share =
            #   end_close × split_factor = 40,000 × 0.01 = 400. Matches
            #   start_close = 400. So we MULTIPLY end_close by the
            #   cumulative split_factor.
            splits_in_window = detect_splits_in_window(
                rows, snapshot_date, end_date,
            )
            cum_split_factor = 1.0
            for _sd, sf in splits_in_window:
                cum_split_factor *= sf
            adjusted_end_close = end_close * cum_split_factor

            # Sum dividends paid out with ex-date strictly inside
            # (snapshot_date, end_date]. Investor of record at snapshot
            # would have received any dividend with ex-date in the
            # holding window.
            dividends_in_period = sum(
                amt for ex_d, amt in ticker_divs
                if snapshot_date < ex_d <= end_date
            )

            # Total return = (split-adjusted price + dividends) / start
            forward_return = (
                (adjusted_end_close + dividends_in_period) / start_close - 1.0
            )
            out.append((
                ticker, snapshot_date, h,
                forward_return,
                start_close, end_close, end_date,
            ))
    return out


def upsert_returns(conn, rows):
    if not rows:
        return 0
    cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO backtest_forward_returns
            (ticker, snapshot_date, horizon_days, forward_return,
             start_close, end_close, end_date)
        VALUES %s
        ON CONFLICT (ticker, snapshot_date, horizon_days) DO UPDATE SET
            forward_return = EXCLUDED.forward_return,
            start_close    = EXCLUDED.start_close,
            end_close      = EXCLUDED.end_close,
            end_date       = EXCLUDED.end_date,
            computed_at    = NOW()
    """, rows)
    n = cur.rowcount
    conn.commit()
    cur.close()
    return n


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pre-compute forward returns per (ticker, snapshot_date, "
                    "horizon_days) for the rank-performance backtester.")
    parser.add_argument("--universe",
                        help="Universe to scope tickers (default: all active).")
    parser.add_argument("--since",
                        help="Only process snapshot dates >= this (YYYY-MM-DD).")
    parser.add_argument("--until",
                        help="Only process snapshot dates <= this (YYYY-MM-DD).")
    parser.add_argument("--horizons", default="30,90,180,365",
                        help="Comma-separated horizon days "
                             "(default: 30,90,180,365).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute and report, but don't write.")
    args = parser.parse_args()

    horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]

    conn = psycopg2.connect(DATABASE_URL)

    print("=" * 70)
    print("Backtest forward-return precompute")
    print("  Universe:  {0}".format(args.universe or "(all active)"))
    print("  Since:     {0}".format(args.since or "(none)"))
    print("  Until:     {0}".format(args.until or "(none)"))
    print("  Horizons:  {0}".format(horizons))
    print("  Dry-run:   {0}".format(args.dry_run))
    print("=" * 70)

    dates = get_snapshot_dates(conn, since=args.since, until=args.until)
    print("\n{0} snapshot dates found in ranking_snapshots".format(len(dates)))
    if not dates:
        print("Nothing to do.")
        conn.close()
        sys.exit(0)

    tickers = get_universe_tickers(conn, args.universe)
    print("{0} tickers in scope".format(len(tickers)))

    log_id = log_start(conn, {
        "universe": args.universe, "since": args.since, "until": args.until,
        "horizons": horizons, "dry_run": args.dry_run,
    })

    n_written = 0
    n_total_rows = 0
    try:
        for d in dates:
            rows = compute_returns_for_date(conn, d, tickers, horizons)
            n_total_rows += len(rows)
            if args.dry_run:
                print("  {0}: {1} (ticker, horizon) rows".format(d, len(rows)))
            else:
                n = upsert_returns(conn, rows)
                n_written += n
                # Per-horizon coverage breakdown
                per_h = {}
                for r in rows:
                    per_h[r[2]] = per_h.get(r[2], 0) + 1
                summary = " ".join("h{0}d={1}".format(h, per_h.get(h, 0))
                                   for h in horizons)
                print("  {0}: wrote {1} rows  [{2}]".format(d, n, summary))
        status = "success"
        err = None
    except KeyboardInterrupt:
        status = "interrupted"
        err = "user cancelled"
        print("\n[INTERRUPTED] partial progress is saved.")
    except Exception as e:
        status = "error"
        err = str(e)
        print("\n[ERROR] {0}".format(e))

    log_finish(conn, log_id, status,
               rows_processed=n_total_rows,
               rows_inserted=0,
               rows_updated=n_written,
               rows_skipped=0,
               error_message=err)

    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print("  Snapshot dates processed:    {0}".format(len(dates)))
    print("  Total (ticker,horizon) rows: {0}".format(n_total_rows))
    print("  Rows written:                {0}".format(n_written))
    print("=" * 70)
    conn.close()
    print("\nDone!")
