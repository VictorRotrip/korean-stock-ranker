-- =============================================================================
-- Migration 006: Make fundamental_snapshots idempotent
-- =============================================================================
-- Idempotent — safe to run multiple times.
--
-- Problem
-- -------
-- Migration 002 created the unique index as:
--     UNIQUE (ticker, period_end, fiscal_quarter, consolidated_or_separate)
-- For annual rows fiscal_quarter is NULL, and Postgres treats NULL ≠ NULL in
-- unique indexes by default. Result: every rerun of normalize_dart_financials
-- inserts a fresh annual row, so 2024 11011 grew from 200 to 827 over many
-- runs. Quarterly rows (fiscal_quarter = 1/2/3) were unaffected.
--
-- This migration:
--   1. Backfills NULL report_code / consolidated_or_separate so the new
--      unique key cannot have NULL components.
--   2. Deduplicates fundamental_snapshots on the correct logical key:
--          (ticker, fiscal_year, report_code, consolidated_or_separate)
--      Keeps the latest row per logical key (highest updated_at, tie-break
--      by highest id).
--   3. Drops the old NULLS-DISTINCT unique index and replaces it with one
--      keyed on the logical fundamentals identity. The new index uses
--      NULLS NOT DISTINCT so future NULLs (defensively) collapse rather
--      than slip through.
--   4. Prints before / after counts so the operator can confirm the dedupe.
--
-- Run via: Supabase Dashboard > SQL Editor > paste & Run
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 0. Diagnostic: how bad is it before we fix it?
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    total_rows                 bigint;
    distinct_logical_keys      bigint;
    duplicate_groups           bigint;
    surplus_rows               bigint;
    null_report_code_rows      bigint;
    null_consolidation_rows    bigint;
BEGIN
    SELECT COUNT(*) INTO total_rows FROM fundamental_snapshots;

    SELECT COUNT(*) INTO null_report_code_rows
      FROM fundamental_snapshots WHERE report_code IS NULL;

    SELECT COUNT(*) INTO null_consolidation_rows
      FROM fundamental_snapshots WHERE consolidated_or_separate IS NULL;

    SELECT COUNT(*) INTO distinct_logical_keys
      FROM (
        SELECT 1
          FROM fundamental_snapshots
         GROUP BY ticker,
                  fiscal_year,
                  COALESCE(report_code, 'unknown'),
                  COALESCE(consolidated_or_separate, 'consolidated')
      ) t;

    SELECT COUNT(*) INTO duplicate_groups
      FROM (
        SELECT 1
          FROM fundamental_snapshots
         GROUP BY ticker,
                  fiscal_year,
                  COALESCE(report_code, 'unknown'),
                  COALESCE(consolidated_or_separate, 'consolidated')
        HAVING COUNT(*) > 1
      ) t;

    surplus_rows := total_rows - distinct_logical_keys;

    RAISE NOTICE 'BEFORE  total_rows=%  distinct_logical_keys=%  '
                 'duplicate_groups=%  surplus_rows=%  '
                 'null_report_code=%  null_consolidation=%',
                 total_rows, distinct_logical_keys, duplicate_groups,
                 surplus_rows, null_report_code_rows, null_consolidation_rows;
END;
$$;


-- ---------------------------------------------------------------------------
-- 1. Backfill NULL report_code from fiscal_quarter / period_end so the
--    logical key is always populated.
-- ---------------------------------------------------------------------------

UPDATE fundamental_snapshots
   SET report_code = CASE
       WHEN fiscal_quarter = 1 THEN '11013'
       WHEN fiscal_quarter = 2 THEN '11012'
       WHEN fiscal_quarter = 3 THEN '11014'
       WHEN fiscal_quarter IS NULL OR fiscal_quarter = 4 THEN '11011'
       ELSE report_code
   END
 WHERE report_code IS NULL;

UPDATE fundamental_snapshots
   SET consolidated_or_separate = 'consolidated'
 WHERE consolidated_or_separate IS NULL;


-- ---------------------------------------------------------------------------
-- 2. Deduplicate. Keep the latest row per logical key (highest updated_at,
--    then highest id). Delete the rest. Wrapped in a CTE so the deletion
--    is atomic.
-- ---------------------------------------------------------------------------

WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY ticker,
                            fiscal_year,
                            report_code,
                            consolidated_or_separate
               ORDER BY updated_at DESC NULLS LAST,
                        id          DESC
           ) AS rn
      FROM fundamental_snapshots
)
DELETE FROM fundamental_snapshots
 WHERE id IN (SELECT id FROM ranked WHERE rn > 1);


-- ---------------------------------------------------------------------------
-- 3. Replace the unique index. The old one keyed on
--      (ticker, period_end, fiscal_quarter, consolidated_or_separate)
--    let NULL fiscal_quarter slip through. The new one keys on the logical
--    identity of a fundamentals snapshot and uses NULLS NOT DISTINCT as a
--    second layer of defence (Postgres 15+, which Supabase runs).
-- ---------------------------------------------------------------------------

DROP INDEX IF EXISTS fsnap_ticker_period_idx;

CREATE UNIQUE INDEX IF NOT EXISTS fsnap_logical_key_idx
    ON fundamental_snapshots (
        ticker,
        fiscal_year,
        report_code,
        consolidated_or_separate
    ) NULLS NOT DISTINCT;


-- ---------------------------------------------------------------------------
-- 4. Verification: the SAME duplicate-group query should now return zero.
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    total_rows                 bigint;
    distinct_logical_keys      bigint;
    duplicate_groups           bigint;
    surplus_rows               bigint;
BEGIN
    SELECT COUNT(*) INTO total_rows FROM fundamental_snapshots;

    SELECT COUNT(*) INTO distinct_logical_keys
      FROM (
        SELECT 1
          FROM fundamental_snapshots
         GROUP BY ticker, fiscal_year, report_code, consolidated_or_separate
      ) t;

    SELECT COUNT(*) INTO duplicate_groups
      FROM (
        SELECT 1
          FROM fundamental_snapshots
         GROUP BY ticker, fiscal_year, report_code, consolidated_or_separate
        HAVING COUNT(*) > 1
      ) t;

    surplus_rows := total_rows - distinct_logical_keys;

    RAISE NOTICE 'AFTER   total_rows=%  distinct_logical_keys=%  '
                 'duplicate_groups=%  surplus_rows=%',
                 total_rows, distinct_logical_keys, duplicate_groups,
                 surplus_rows;

    IF duplicate_groups > 0 THEN
        RAISE EXCEPTION
            'Migration 006 dedupe failed: % duplicate groups remain.',
            duplicate_groups;
    END IF;
END;
$$;

COMMIT;
