# TODO: Point-in-time Market Cap Backfill

## The problem

`ingest_marcap.py` currently uses `fdr.StockListing("KRX")`, which returns a
**current snapshot** of market cap, shares outstanding, and trading value —
not point-in-time historical data.

This means that when we run the ranking snapshot at `--as-of-date 2024-12-30`,
every stock's market cap is **today's** market cap, not the market cap as of
2024-12-30. As a result, all value factors that divide TTM fundamentals by
market cap are biased:

- `pe_ttm_inv` (earnings yield)
- `price_book` (book / market)
- `ebitda_ev`
- `ev_sales_ttm_inv`
- `price_sales_ttm_inv`
- `dividend_yield`
- `fcf_mcap`
- `ocf_mcap`
- `ufcf_ev`
- `gross_profit_ev`

A stock that has doubled in price since 2024-12-30 will look artificially
expensive (its TTM earnings are unchanged, but the denominator is too large).
A stock that has halved will look artificially cheap.

This is **OK for current-day ranking validation** — it's roughly self-consistent
when as-of-date is close to today. It is **NOT OK for backtests**, where
ranking history needs to be reconstructed at multiple historical dates.

## Status

- `ingest_marcap.py` prints a warning to make this visible.
- `diagnose_factor_inputs.py` echoes the same warning.
- The ranking snapshot does NOT currently fix this.

## Plan

1. Switch to a true historical market cap source. Three options, in order of
   preference:

   a. **The `marcap` PyPI package** (already commented out in
      `requirements.txt`). This package ships a CSV bundle of historical KRX
      market caps from 1995-present, updated daily. Smallest code change.

      ```bash
      pip install marcap
      ```

      In Python:
      ```python
      from marcap import marcap_data
      df = marcap_data(start, end, code=ticker)
      # columns include Code, Name, Marcap (KRW), Stocks (shares),
      #                Open, High, Low, Close, Volume, Amount (trading value)
      ```

   b. **`pykrx` with KRX_ID/KRX_PW** in `.env`. The function
      `pykrx.stock.get_market_cap_by_date(date_str, ticker)` returns
      historical market cap, shares outstanding, and trading value, but it
      requires a (free) KRX login because the unauthenticated endpoint that
      pykrx falls back to has been crippled by KRX. Fragile; not recommended
      as the primary source.

   c. **Compute it from `daily_prices`**:
      `market_cap = close × shares_outstanding`. Shares outstanding is
      slow-moving and we already have it from DART for the annual periods.
      This works as a fallback but loses precision for stocks that did
      splits / buybacks / new issuances mid-year.

2. Refactor `ingest_marcap.py` to:
   - Accept a `--start-date` and `--end-date` (or default to the universe's
     price-history date range).
   - For each (ticker, date) row in `daily_prices`, fill `market_cap`,
     `shares_outstanding`, `trading_value` from the historical source.
   - Stop using `fdr.StockListing()`.

3. Backfill historical `daily_prices` rows for the test_200_large universe
   from 2022-01-01 onward. About 200 tickers × 750 trading days = 150k rows.

4. Re-run `calculate_factors.py` for the historical as-of dates we care
   about (e.g. month-ends 2022-01-31 through 2024-12-31), then rebuild
   `factor_snapshots` so the time-series of factor values is point-in-time
   correct.

5. Re-run `run_ranking_snapshot.py` for those dates. Only after this is done
   are backtests trustworthy.

## Acceptance criteria

- `ingest_marcap.py` no longer uses `fdr.StockListing()` for historical fills.
- Spot-check: for 005930 (Samsung) on 2024-12-30, the stored `market_cap`
  matches the value reported by KRX historical data within ~0.1%
  (allowing for rounding).
- Spot-check: 005930 on 2023-12-29 has a different `market_cap` than on
  2024-12-30 (current behavior would show the same number).
- The diagnose script's "Market cap source" warning is removed or replaced
  with a "point-in-time" confirmation.
- Backtests across a multi-year window produce stable rankings that don't
  swing wildly when the as-of-date moves.

## Out of scope for this task

- Free-float adjustments / public float vs. total shares.
- KRX corporate-action history (splits, mergers, ticker changes).
- True point-in-time DART filings (already handled correctly via
  `data_available_date <= as_of` in `calculate_factors.py`).

## Owner

Unassigned. File this when work begins.

## Detailed implementation plan

### Phase 1: Add the historical source

1. Uncomment `marcap>=0.3.2` in `scripts/python/requirements.txt` and re-run
   `pip install -r requirements.txt` inside the venv.

2. Add a new `daily_prices.source` enum value: `marcap_historical`. This is
   the value `ingest_marcap.py` should write when it pulls true point-in-time
   data, as distinct from the current `fdr_listing_snapshot` /
   `fdr+marcap` sentinels.

3. Refactor `scripts/python/ingest_marcap.py`:

   a. Accept `--start-date` and `--end-date`. If omitted, use the
      universe's existing `daily_prices` date range.

   b. Use `marcap.marcap_data(start, end, code=ticker)` per ticker (or batch
      by date if the API supports it). The package returns a DataFrame with
      columns including `Marcap` (KRW), `Stocks` (shares outstanding),
      `Volume`, `Amount` (trading value).

   c. For each row, UPSERT into `daily_prices` with:
        - `market_cap = Marcap`
        - `shares_outstanding = Stocks`
        - `trading_value = Amount`
        - `source = 'marcap_historical'`

   d. Make it `--resume`-safe so an interrupted multi-month backfill can
      pick up where it left off.

### Phase 2: Backfill and rerun

4. Backfill `daily_prices` for `test_200_large` from 2022-01-01 onward.
   Approx 200 tickers × 750 trading days = ~150k rows.

5. Spot-check Samsung 005930:
   - Stored `market_cap` on 2024-12-30 should match KRX historical data.
   - Stored `market_cap` on 2023-12-29 should be DIFFERENT from 2024-12-30
     (current snapshot would have made them equal — that's the bug).

6. Re-run the factor pipeline at the historical as-of dates:
   ```bash
   for d in 2022-12-30 2023-12-29 2024-06-28 2024-12-30; do
       python calculate_factors.py --universe test_200_large --as-of-date $d
       python run_ranking_snapshot.py --universe test_200_large --as-of-date $d \
           --missing-category-policy neutral
   done
   ```

### Phase 3: Gate backtests

7. In any backtest entry point, refuse to run unless every required
   `daily_prices` row in the date range has `source = 'marcap_historical'`.
   Until then, surface a clear error: "Backtests are disabled because
   market_cap is from a current FDR snapshot. Run ingest_marcap.py with
   the historical marcap source first."

8. The `diagnose_factor_inputs.py` "MARKET CAP SOURCE" section should
   distinguish:
   - `marcap_historical`: trusted, point-in-time, OK for backtests
   - `fdr_listing_snapshot` / `fdr+marcap`: current snapshot, NOT OK
   - mixed: warn loudly

### Phase 4: Mark complete

9. Remove the warnings from `ingest_marcap.py` and `diagnose_factor_inputs.py`
   for universes whose entire date range is `marcap_historical`.

10. Update this TODO to "DONE" with a date and the migration commit hash.
