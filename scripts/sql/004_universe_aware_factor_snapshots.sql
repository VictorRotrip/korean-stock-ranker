-- =============================================================================
-- 004_universe_aware_factor_snapshots.sql
--
-- Make factor_snapshots universe-aware. A stock's RAW factor value is universe-
-- independent (e.g. its TTM net income), but its PERCENTILE RANK is not -- it
-- depends on which other stocks it's being ranked against. Running calculate
-- factors for two different universes against the same as-of-date previously
-- caused rows to overwrite or pollute each other.
--
-- This migration:
--   1. Adds factor_snapshots.universe_name (nullable, defaulting to a sentinel).
--   2. Backfills existing rows with '__legacy_unknown__' so they don't collide.
--   3. Adds a new UNIQUE index on (universe_name, ticker, factor_id, date).
--   4. Adds a query index on (universe_name, date) for fast per-universe reads.
--
-- This script is IDEMPOTENT and does NOT drop or delete anything. The old
-- PRIMARY KEY on (ticker, factor_id, date) still exists after this script
-- runs, and it BLOCKS the new universe-aware writes. See "Manual step
-- required" below.
--
-- ---------------------------------------------------------------------------
-- MANUAL STEP REQUIRED IN SUPABASE SQL EDITOR (run ONCE before re-running
-- calculate_factors.py against multiple universes):
--
--   ALTER TABLE factor_snapshots DROP CONSTRAINT factor_snapshots_pkey;
--
-- After dropping, the new unique index created below becomes the de-facto
-- uniqueness constraint. If you only ever rank one universe, the old PK is
-- actually still fine -- but if you want test_50 and test_200_large to
-- coexist, you MUST drop it.
--
-- If you want to be more conservative, you can promote the new unique index
-- to a primary key after dropping the old one:
--
--   ALTER TABLE factor_snapshots
--       ADD CONSTRAINT factor_snapshots_pkey
--       PRIMARY KEY USING INDEX factor_snapshots_universe_unique;
--
-- This is optional; the unique index is sufficient for ON CONFLICT.
-- =============================================================================

-- 1. Add universe_name column (nullable, no default, idempotent)
ALTER TABLE factor_snapshots
    ADD COLUMN IF NOT EXISTS universe_name TEXT;

-- 2. Backfill existing rows with a sentinel so they don't collide with new rows
--    Idempotent: only updates rows that don't yet have a universe_name.
UPDATE factor_snapshots
   SET universe_name = '__legacy_unknown__'
 WHERE universe_name IS NULL;

-- 3. Create new unique index that includes universe_name.
--    Once the old PK is dropped, this becomes the uniqueness constraint.
CREATE UNIQUE INDEX IF NOT EXISTS factor_snapshots_universe_unique
    ON factor_snapshots (universe_name, ticker, factor_id, date);

-- 4. Helpful query index: filter by universe + date (the most common pattern).
CREATE INDEX IF NOT EXISTS factor_snapshots_universe_date_idx
    ON factor_snapshots (universe_name, date);

-- =============================================================================
-- Verification queries (read-only, safe to run repeatedly)
-- =============================================================================

-- Inspect distribution of universe_name values:
--   SELECT universe_name, COUNT(*) FROM factor_snapshots GROUP BY universe_name;
--
-- Confirm new index exists:
--   SELECT indexname, indexdef FROM pg_indexes
--   WHERE tablename = 'factor_snapshots' ORDER BY indexname;
--
-- Confirm old PK still present (you'll need to drop it manually):
--   SELECT conname FROM pg_constraint
--   WHERE conrelid = 'factor_snapshots'::regclass AND contype = 'p';
