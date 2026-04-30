-- =============================================================================
-- Korean Stock Ranker — Initial Schema Migration
-- =============================================================================
-- Run this against your Supabase Postgres database to create all tables.
-- Alternatively, use: npx drizzle-kit push (from the project root with DATABASE_URL set)
--
-- Generated from: src/db/schema.ts
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Stocks (universe)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stocks (
  ticker       VARCHAR(10) PRIMARY KEY,
  name         TEXT NOT NULL,
  name_en      TEXT,
  market       VARCHAR(10) NOT NULL,  -- KOSPI | KOSDAQ
  sector       TEXT,
  industry     TEXT,
  listing_date DATE,
  delisting_date DATE,
  is_active    BOOLEAN NOT NULL DEFAULT true,
  is_spac      BOOLEAN DEFAULT false,
  is_preferred BOOLEAN DEFAULT false,
  is_etf       BOOLEAN DEFAULT false,
  is_reit      BOOLEAN DEFAULT false,
  is_financial BOOLEAN DEFAULT false,
  is_holding   BOOLEAN DEFAULT false,
  source       VARCHAR(20) DEFAULT 'marcap',
  updated_at   TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS stocks_market_idx ON stocks (market);
CREATE INDEX IF NOT EXISTS stocks_active_idx ON stocks (is_active);

-- ---------------------------------------------------------------------------
-- 2. Daily Prices
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS daily_prices (
  ticker             VARCHAR(10) NOT NULL,
  date               DATE NOT NULL,
  open               DOUBLE PRECISION,
  high               DOUBLE PRECISION,
  low                DOUBLE PRECISION,
  close              DOUBLE PRECISION NOT NULL,
  volume             BIGINT,
  trading_value      BIGINT,
  market_cap         BIGINT,
  shares_outstanding BIGINT,
  source             VARCHAR(20) DEFAULT 'marcap',
  PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS daily_prices_date_idx ON daily_prices (date);
CREATE INDEX IF NOT EXISTS daily_prices_ticker_date_idx ON daily_prices (ticker, date);

-- ---------------------------------------------------------------------------
-- 3. pykrx Fundamentals (Phase 2 — KRX valuation proxies)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pykrx_fundamentals (
  ticker         VARCHAR(10) NOT NULL,
  date           DATE NOT NULL,
  per            DOUBLE PRECISION,
  pbr            DOUBLE PRECISION,
  eps            DOUBLE PRECISION,
  bps            DOUBLE PRECISION,
  dps            DOUBLE PRECISION,
  dividend_yield DOUBLE PRECISION,
  PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS pykrx_fund_date_idx ON pykrx_fundamentals (date);

-- ---------------------------------------------------------------------------
-- 4. Financial Statements (Phase 3 — DART, point-in-time)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS financial_statements (
  id                         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker                     VARCHAR(10) NOT NULL,
  period_end                 DATE NOT NULL,
  filing_date                DATE NOT NULL,
  data_available_date        DATE NOT NULL,
  fiscal_year                INTEGER NOT NULL,
  fiscal_quarter             INTEGER,
  statement_type             VARCHAR(20) NOT NULL,  -- annual | Q1 | Q2 | Q3
  consolidated_or_separate   VARCHAR(15) DEFAULT 'consolidated',
  source                     VARCHAR(20) DEFAULT 'dart',
  -- Income Statement (KRW)
  revenue                    BIGINT,
  cost_of_revenue            BIGINT,
  gross_profit               BIGINT,
  operating_income           BIGINT,
  net_income                 BIGINT,
  eps                        DOUBLE PRECISION,
  -- Balance Sheet (KRW)
  total_assets               BIGINT,
  total_liabilities          BIGINT,
  total_equity               BIGINT,
  book_value_per_share       DOUBLE PRECISION,
  current_assets             BIGINT,
  current_liabilities        BIGINT,
  cash                       BIGINT,
  total_debt                 BIGINT,
  -- Cash Flow (KRW)
  operating_cash_flow        BIGINT,
  capital_expenditure        BIGINT,
  free_cash_flow             BIGINT,
  dividends_paid             BIGINT,
  -- Derived
  ebitda                     BIGINT,
  interest_expense           BIGINT,
  depreciation               BIGINT,
  shares_outstanding         BIGINT
);

CREATE UNIQUE INDEX IF NOT EXISTS fs_ticker_period_type_idx
  ON financial_statements (ticker, period_end, statement_type, consolidated_or_separate);
CREATE INDEX IF NOT EXISTS fs_filing_date_idx ON financial_statements (filing_date);
CREATE INDEX IF NOT EXISTS fs_data_avail_idx ON financial_statements (data_available_date);
CREATE INDEX IF NOT EXISTS fs_ticker_fiscal_idx ON financial_statements (ticker, fiscal_year);

-- ---------------------------------------------------------------------------
-- 5. Short Selling
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS short_selling (
  ticker              VARCHAR(10) NOT NULL,
  date                DATE NOT NULL,
  short_volume        BIGINT,
  short_value         BIGINT,
  short_balance       BIGINT,
  short_balance_value BIGINT,
  short_ratio         DOUBLE PRECISION,
  source              VARCHAR(20) DEFAULT 'pykrx',
  PRIMARY KEY (ticker, date)
);

-- ---------------------------------------------------------------------------
-- 6. DART Filings (metadata)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dart_filings (
  id           INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker       VARCHAR(10),
  corp_code    VARCHAR(20),
  receipt_no   VARCHAR(30),
  report_name  TEXT,
  filing_date  DATE,
  url          TEXT
);

-- ---------------------------------------------------------------------------
-- 7. Factor Coverage (metadata about data availability)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS factor_coverage (
  factor_id        VARCHAR(50) PRIMARY KEY,
  name             TEXT NOT NULL,
  category         VARCHAR(30) NOT NULL,
  formula          TEXT,
  preferred_source VARCHAR(20),
  fallback_source  VARCHAR(20),
  is_available     BOOLEAN DEFAULT false,
  coverage_ratio   DOUBLE PRECISION,
  uses_mock_data   BOOLEAN DEFAULT true,
  point_in_time_safe BOOLEAN DEFAULT false,
  data_status      VARCHAR(15) DEFAULT 'mock',  -- real | proxy | mock | unavailable
  last_updated     TIMESTAMP DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 8. Ranking Systems (user-created)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ranking_systems (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  description     TEXT,
  tree            JSONB NOT NULL,
  options         JSONB NOT NULL,
  universe_config JSONB,
  user_id         TEXT,
  created_at      TIMESTAMP DEFAULT now(),
  updated_at      TIMESTAMP DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 9. Ranking Snapshots
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ranking_snapshots (
  id                 INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ranking_system_id  TEXT REFERENCES ranking_systems(id),
  date               DATE NOT NULL,
  results            JSONB NOT NULL,
  universe_size      INTEGER,
  computed_at        TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS rs_system_date_idx
  ON ranking_snapshots (ranking_system_id, date);

-- ---------------------------------------------------------------------------
-- 10. Factor Snapshots (precomputed factor values)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS factor_snapshots (
  ticker          VARCHAR(10) NOT NULL,
  factor_id       VARCHAR(50) NOT NULL,
  date            DATE NOT NULL,
  raw_value       DOUBLE PRECISION,
  percentile_rank DOUBLE PRECISION,
  source          VARCHAR(20),
  PRIMARY KEY (ticker, factor_id, date)
);

CREATE INDEX IF NOT EXISTS factor_snap_date_idx ON factor_snapshots (date);

-- ---------------------------------------------------------------------------
-- 11. Ingestion Log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingestion_log (
  id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  script_name     VARCHAR(100) NOT NULL,
  started_at      TIMESTAMP DEFAULT now(),
  finished_at     TIMESTAMP,
  status          VARCHAR(20) DEFAULT 'running',  -- running | success | error
  rows_processed  INTEGER DEFAULT 0,
  rows_inserted   INTEGER DEFAULT 0,
  rows_updated    INTEGER DEFAULT 0,
  rows_skipped    INTEGER DEFAULT 0,
  error_message   TEXT,
  parameters      JSONB
);

-- ---------------------------------------------------------------------------
-- Seed factor_coverage with all 25 factors (initial state: all mock)
-- ---------------------------------------------------------------------------

INSERT INTO factor_coverage (factor_id, name, category, formula, preferred_source, fallback_source, is_available, coverage_ratio, uses_mock_data, point_in_time_safe, data_status)
VALUES
  -- Value
  ('earnings_yield',     'Earnings Yield',     'value',   'net_income / market_cap',            'dart',   'pykrx', false, 0, true, false, 'mock'),
  ('book_to_market',     'Book-to-Market',     'value',   'total_equity / market_cap',          'dart',   'pykrx', false, 0, true, false, 'mock'),
  ('sales_yield',        'Sales Yield',        'value',   'revenue / market_cap',               'dart',   NULL,    false, 0, true, false, 'mock'),
  ('cf_yield',           'Cash Flow Yield',    'value',   'operating_cf / market_cap',          'dart',   NULL,    false, 0, true, false, 'mock'),
  ('ev_ebitda',          'EV/EBITDA',          'value',   '(market_cap + debt - cash) / ebitda','dart',   NULL,    false, 0, true, false, 'mock'),
  ('dividend_yield',     'Dividend Yield',     'value',   'dps / price',                        'pykrx',  NULL,    false, 0, true, false, 'mock'),
  -- Quality
  ('roe',                'Return on Equity',   'quality', 'net_income / total_equity',          'dart',   NULL,    false, 0, true, false, 'mock'),
  ('roa',                'Return on Assets',   'quality', 'net_income / total_assets',          'dart',   NULL,    false, 0, true, false, 'mock'),
  ('gross_profitability','Gross Profitability', 'quality', 'gross_profit / total_assets',        'dart',   NULL,    false, 0, true, false, 'mock'),
  ('operating_margin',   'Operating Margin',   'quality', 'operating_income / revenue',         'dart',   NULL,    false, 0, true, false, 'mock'),
  ('debt_to_equity',     'Debt/Equity',        'quality', 'total_debt / total_equity',          'dart',   NULL,    false, 0, true, false, 'mock'),
  ('interest_coverage',  'Interest Coverage',  'quality', 'ebitda / interest_expense',          'dart',   NULL,    false, 0, true, false, 'mock'),
  -- Growth
  ('revenue_growth',     'Revenue Growth',     'growth',  '(rev_t / rev_t-1) - 1',             'dart',   NULL,    false, 0, true, false, 'mock'),
  ('eps_growth',         'EPS Growth',         'growth',  '(eps_t / eps_t-1) - 1',             'dart',   'pykrx', false, 0, true, false, 'mock'),
  ('op_income_growth',   'Op Income Growth',   'growth',  '(op_inc_t / op_inc_t-1) - 1',      'dart',   NULL,    false, 0, true, false, 'mock'),
  ('fcf_growth',         'FCF Growth',         'growth',  '(fcf_t / fcf_t-1) - 1',            'dart',   NULL,    false, 0, true, false, 'mock'),
  -- Momentum
  ('momentum_12_1',      '12-1M Momentum',     'momentum','price_12m_ago / price_1m_ago - 1',  'marcap', NULL,    false, 0, true, true,  'mock'),
  ('momentum_6m',        '6M Momentum',        'momentum','price / price_6m_ago - 1',          'marcap', NULL,    false, 0, true, true,  'mock'),
  ('momentum_3m',        '3M Momentum',        'momentum','price / price_3m_ago - 1',          'marcap', NULL,    false, 0, true, true,  'mock'),
  ('reversal_1m',        '1M Reversal',        'momentum','price / price_1m_ago - 1',          'marcap', NULL,    false, 0, true, true,  'mock'),
  ('dist_52w_high',      'Dist from 52W High', 'momentum','price / 52w_high - 1',             'marcap', NULL,    false, 0, true, true,  'mock'),
  -- Risk
  ('volatility_60d',     '60D Volatility',     'risk',    'stdev(daily_returns, 60)',           'marcap', NULL,    false, 0, true, true,  'mock'),
  -- Liquidity
  ('turnover_ratio',     'Turnover Ratio',     'liquidity','volume / shares_outstanding',       'marcap', NULL,    false, 0, true, true,  'mock'),
  -- Short Interest
  ('short_ratio',        'Short Ratio',        'short_interest','short_volume / total_volume',  'pykrx',  NULL,    false, 0, true, false, 'mock'),
  ('short_balance_ratio','Short Balance Ratio', 'short_interest','short_balance / shares_out',  'pykrx',  NULL,    false, 0, true, false, 'mock')
ON CONFLICT (factor_id) DO NOTHING;
