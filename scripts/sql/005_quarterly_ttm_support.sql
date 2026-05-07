-- =============================================================================
-- Migration 005: Quarterly + TTM support
-- =============================================================================
-- Idempotent — safe to run multiple times.
--
-- Goals
-- -----
-- 1) Track HOW each factor was computed (TTM vs annual fallback vs unavailable)
--    by widening factor_snapshots.source so we can store labels like
--    'ttm_quarterly', 'annual_fallback', 'insufficient_quarterly_history'.
-- 2) Optional metadata columns on fundamental_snapshots so the normalizer can
--    record whether a quarterly row's per-quarter value was 'raw' (Q1) or
--    'derived' (Q2/Q3/Q4 by YTD subtraction).
--
-- Run via: Supabase Dashboard > SQL Editor > paste & Run
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Widen factor_snapshots.source so it can hold method labels.
-- ---------------------------------------------------------------------------
ALTER TABLE factor_snapshots
  ALTER COLUMN source TYPE VARCHAR(50);

-- ---------------------------------------------------------------------------
-- 2. fundamental_snapshots: optional quarterly-derivation metadata.
--    These columns are populated by normalize_dart_financials.py for rows
--    representing single-quarter values derived from cumulative YTD reports.
--    They never cause data loss when null.
-- ---------------------------------------------------------------------------
ALTER TABLE fundamental_snapshots
  ADD COLUMN IF NOT EXISTS quarterly_method VARCHAR(20);
  -- values: 'raw'        — Q1 cumulative used as Q1 single quarter
  --         'derived'    — Q2/Q3/Q4 obtained by subtracting prior YTD
  --         'unavailable'— quarter not derivable yet from available rows

COMMIT;
