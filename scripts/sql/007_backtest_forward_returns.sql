-- =============================================================================
-- Backtest Forward Returns
-- =============================================================================
-- Pre-computed forward total return per (ticker, snapshot_date, horizon_days).
-- Written by scripts/python/backtest_forward_returns.py.
-- Joined with factor_snapshots in the /backtest UI to bucket stocks into
-- deciles by composite-score-with-tweakable-weights and plot the spread
-- between top and bottom deciles over time.
-- =============================================================================

CREATE TABLE IF NOT EXISTS backtest_forward_returns (
    ticker            VARCHAR(10)      NOT NULL,
    snapshot_date     DATE             NOT NULL,
    horizon_days      INTEGER          NOT NULL,
    forward_return    DOUBLE PRECISION,
    start_close       DOUBLE PRECISION,
    end_close         DOUBLE PRECISION,
    end_date          DATE,
    computed_at       TIMESTAMP        DEFAULT NOW(),
    PRIMARY KEY (ticker, snapshot_date, horizon_days)
);

-- Index for the common UI query: pick a snapshot_date and horizon, then
-- join with factor_snapshots for that date.
CREATE INDEX IF NOT EXISTS bfr_date_horizon_idx
    ON backtest_forward_returns (snapshot_date, horizon_days);
