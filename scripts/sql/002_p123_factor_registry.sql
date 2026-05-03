-- =============================================================================
-- Migration 002: P123 Factor Registry
-- =============================================================================
-- Idempotent migration — safe to run multiple times.
-- No DROP, DELETE, or destructive renames.
--
-- Run via: Supabase Dashboard > SQL Editor > paste & Run
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. fundamental_snapshots — normalized DART financial data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fundamental_snapshots (
  id              integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker          varchar(10) NOT NULL,
  period_end      date NOT NULL,
  data_available_date date NOT NULL,
  fiscal_year     integer NOT NULL,
  fiscal_quarter  integer,
  report_code     varchar(10),
  consolidated_or_separate varchar(15) DEFAULT 'consolidated',
  -- Financials (KRW)
  revenue                   bigint,
  gross_profit              bigint,
  operating_income          bigint,
  net_income                bigint,
  eps                       double precision,
  total_assets              bigint,
  total_equity              bigint,
  total_liabilities         bigint,
  total_debt                bigint,
  cash_and_equivalents      bigint,
  inventory                 bigint,
  operating_cash_flow       bigint,
  capex                     bigint,
  free_cash_flow            bigint,
  depreciation_amortization bigint,
  interest_expense          bigint,
  ebitda                    bigint,
  shares_outstanding        bigint,
  dividends_paid            bigint,
  source          varchar(20) DEFAULT 'dart',
  updated_at      timestamptz DEFAULT now()
);

-- Unique index: one row per ticker/period/quarter/consolidation
CREATE UNIQUE INDEX IF NOT EXISTS fsnap_ticker_period_idx
  ON fundamental_snapshots (ticker, period_end, fiscal_quarter, consolidated_or_separate);

CREATE INDEX IF NOT EXISTS fsnap_data_avail_idx
  ON fundamental_snapshots (data_available_date);

CREATE INDEX IF NOT EXISTS fsnap_ticker_fiscal_idx
  ON fundamental_snapshots (ticker, fiscal_year);


-- ---------------------------------------------------------------------------
-- 2. ingestion_errors — detailed error tracking per ticker
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingestion_errors (
  id           integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  script_name  varchar(100) NOT NULL,
  ticker       varchar(10),
  error_type   varchar(50),
  error_message text,
  parameters   jsonb,
  created_at   timestamptz DEFAULT now()
);


-- ---------------------------------------------------------------------------
-- 3. factor_coverage — add new columns (idempotent)
-- ---------------------------------------------------------------------------

ALTER TABLE factor_coverage ADD COLUMN IF NOT EXISTS rank_direction      varchar(20);
ALTER TABLE factor_coverage ADD COLUMN IF NOT EXISTS scope               varchar(20);
ALTER TABLE factor_coverage ADD COLUMN IF NOT EXISTS subcategory         varchar(50);
ALTER TABLE factor_coverage ADD COLUMN IF NOT EXISTS implementation_status varchar(20) DEFAULT 'unavailable';
ALTER TABLE factor_coverage ADD COLUMN IF NOT EXISTS missing_value_policy varchar(30) DEFAULT 'exclude_from_factor';
ALTER TABLE factor_coverage ADD COLUMN IF NOT EXISTS lookback_days       integer;
ALTER TABLE factor_coverage ADD COLUMN IF NOT EXISTS data_source_detail  varchar(30);


-- ---------------------------------------------------------------------------
-- 4. factor_snapshots — add new columns (idempotent)
-- ---------------------------------------------------------------------------

ALTER TABLE factor_snapshots ADD COLUMN IF NOT EXISTS scope          varchar(20);
ALTER TABLE factor_snapshots ADD COLUMN IF NOT EXISTS scope_fallback boolean DEFAULT false;
ALTER TABLE factor_snapshots ADD COLUMN IF NOT EXISTS missing_reason varchar(30);


-- ---------------------------------------------------------------------------
-- 5. Seed factor_coverage — 45 P123-inspired factors
-- ---------------------------------------------------------------------------
-- ON CONFLICT updates all metadata so re-running refreshes definitions.

INSERT INTO factor_coverage (
  factor_id, name, category, formula,
  preferred_source, fallback_source,
  is_available, coverage_ratio, uses_mock_data, point_in_time_safe,
  data_status,
  rank_direction, scope, subcategory, implementation_status,
  missing_value_policy, lookback_days, data_source_detail
) VALUES
  -- ===== VALUE (10 factors) =====
  ('pe_ttm_inv',          'P/E TTM (inverted)',         'value',    'Net Income TTM / Market Cap',
   'dart', 'pykrx', true, null, false, true, 'real',
   'higher_is_better', 'universe', 'earnings_based', 'real', 'exclude_from_factor', 0, 'dart'),

  ('ebitda_ev',            'EBITDA / EV',                'value',    'EBITDA / (Market Cap + Debt - Cash)',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'earnings_based', 'real', 'exclude_from_factor', 0, 'dart'),

  ('price_sales_ttm_inv', 'Price/Sales TTM (inverted)', 'value',    'Revenue TTM / Market Cap',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'sales_based', 'real', 'exclude_from_factor', 0, 'dart'),

  ('ev_sales_ttm_inv',    'EV/Sales TTM (inverted)',    'value',    'Revenue / EV',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'sales_based', 'real', 'exclude_from_factor', 0, 'dart'),

  ('gross_profit_ev',     'Gross Profit / EV',          'value',    'Gross Profit / EV',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'sales_based', 'real', 'exclude_from_factor', 0, 'dart'),

  ('fcf_mcap',            'FCF / Market Cap',           'value',    'Free Cash Flow / Market Cap',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'fcf_based', 'real', 'exclude_from_factor', 0, 'dart'),

  ('ocf_mcap',            'Operating CF / Market Cap',  'value',    'Operating CF / Market Cap',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'fcf_based', 'real', 'exclude_from_factor', 0, 'dart'),

  ('ufcf_ev',             'Unlevered FCF / EV',         'value',    '(OCF - Capex + 0.8*Interest) / EV',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'fcf_based', 'real', 'exclude_from_factor', 0, 'dart'),

  ('price_book',          'Price / Book',               'value',    'Total Equity / Market Cap',
   'dart', 'pykrx', true, null, false, true, 'real',
   'higher_is_better', 'universe', 'asset_based', 'real', 'exclude_from_factor', 0, 'dart'),

  ('dividend_yield',      'Dividend Yield',             'value',    'Dividends Paid / Market Cap',
   'dart', 'pykrx', true, null, false, true, 'real',
   'higher_is_better', 'universe', 'asset_based', 'real', 'exclude_from_factor', 0, 'dart'),

  -- ===== QUALITY (8 factors) =====
  ('operating_margin_ttm', 'Operating Margin TTM',      'quality',  'Operating Income / Revenue',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'margins', 'real', 'exclude_from_factor', 0, 'dart'),

  ('gross_margin_ttm',    'Gross Margin TTM',           'quality',  'Gross Profit / Revenue',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'margins', 'real', 'exclude_from_factor', 0, 'dart'),

  ('roe_ttm',             'ROE TTM',                    'quality',  'Net Income / Total Equity',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'return_on_capital', 'real', 'exclude_from_factor', 0, 'dart'),

  ('roa_ttm',             'ROA TTM',                    'quality',  'Net Income / Total Assets',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'return_on_capital', 'real', 'exclude_from_factor', 0, 'dart'),

  ('gross_profit_assets', 'Gross Profit / Assets',      'quality',  'Gross Profit / Total Assets',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'return_on_capital', 'real', 'exclude_from_factor', 0, 'dart'),

  ('asset_turnover_ttm',  'Asset Turnover TTM',         'quality',  'Revenue / Total Assets',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'turnover', 'real', 'exclude_from_factor', 0, 'dart'),

  ('debt_to_equity',      'Debt / Equity',              'quality',  'Total Debt / Total Equity',
   'dart', null, true, null, false, true, 'real',
   'lower_is_better', 'universe', 'finances', 'real', 'exclude_from_factor', 0, 'dart'),

  ('interest_coverage_ttm','Interest Coverage TTM',     'quality',  'EBITDA / Interest Expense',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'finances', 'real', 'exclude_from_factor', 0, 'dart'),

  -- ===== GROWTH (4 factors) =====
  ('sales_growth_yoy',    'Sales Growth YoY',           'growth',   'Revenue YoY change',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'sales', 'real', 'exclude_from_factor', 0, 'dart'),

  ('op_income_growth_yoy','Op Income Growth YoY',       'growth',   'Operating Income YoY change',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'operating_income', 'real', 'exclude_from_factor', 0, 'dart'),

  ('eps_growth_yoy',      'EPS Growth YoY',             'growth',   'EPS YoY change',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'eps', 'real', 'exclude_from_factor', 0, 'dart'),

  ('net_income_growth_yoy','Net Income Growth YoY',     'growth',   'Net Income YoY change',
   'dart', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'eps', 'real', 'exclude_from_factor', 0, 'dart'),

  -- ===== MOMENTUM (9 factors) =====
  ('price_change_120d',   'Price Change 120d',          'momentum', 'Close(0) / Close(120) - 1',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'price_changes', 'real', 'exclude_from_factor', 130, 'price'),

  ('price_change_180d',   'Price Change 180d',          'momentum', 'Close(0) / Close(180) - 1',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'price_changes', 'real', 'exclude_from_factor', 190, 'price'),

  ('momentum_3m',         '3-Month Return',             'momentum', '63-day price return',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'quarterly_returns', 'real', 'exclude_from_factor', 70, 'price'),

  ('momentum_6m',         '6-Month Return',             'momentum', '126-day price return',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'quarterly_returns', 'real', 'exclude_from_factor', 135, 'price'),

  ('momentum_12_1',       '12-1M Momentum',             'momentum', '12M return skipping recent month',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'quarterly_returns', 'real', 'exclude_from_factor', 260, 'price'),

  ('up_down_ratio_20',    'Up/Down Ratio 20d',          'momentum', 'Up days / Down days over 20d',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'technical', 'real', 'exclude_from_factor', 25, 'price'),

  ('up_down_ratio_60',    'Up/Down Ratio 60d',          'momentum', 'Up days / Down days over 60d',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'technical', 'real', 'exclude_from_factor', 65, 'price'),

  ('up_down_ratio_120',   'Up/Down Ratio 120d',         'momentum', 'Up days / Down days over 120d',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'technical', 'real', 'exclude_from_factor', 130, 'price'),

  ('rsi_200',             'RSI 200',                    'momentum', 'Relative Strength Index 200d',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'technical', 'real', 'exclude_from_factor', 210, 'price'),

  -- ===== RISK / LOW VOLATILITY (3 factors) =====
  ('volatility_252d',     '252-Day Volatility',         'risk',     'Annualized stdev of daily returns 252d',
   'marcap', null, true, null, false, true, 'real',
   'lower_is_better', 'universe', 'price_volatility', 'real', 'exclude_from_factor', 260, 'price'),

  ('volatility_60d',      '60-Day Volatility',          'risk',     'Annualized stdev of daily returns 60d',
   'marcap', null, true, null, false, true, 'real',
   'lower_is_better', 'universe', 'price_volatility', 'real', 'exclude_from_factor', 65, 'price'),

  ('max_drawdown_252d',   'Max Drawdown 252d',          'risk',     'Max drawdown over 252 trading days',
   'marcap', null, true, null, false, true, 'real',
   'lower_is_better', 'universe', 'price_volatility', 'real', 'exclude_from_factor', 260, 'price'),

  -- ===== LIQUIDITY / SIZE (5 factors) =====
  ('market_cap',          'Market Cap',                 'liquidity','Market capitalization',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'size', 'real', 'exclude_from_factor', 0, 'price'),

  ('log_market_cap',      'Log Market Cap',             'liquidity','ln(Market Cap)',
   'marcap', null, true, null, false, true, 'real',
   'lower_is_better', 'universe', 'size', 'real', 'exclude_from_factor', 0, 'price'),

  ('avg_trading_value_60d','Avg Trading Value 60d',     'liquidity','Avg daily trading value 60d',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'liquidity', 'real', 'exclude_from_factor', 65, 'price'),

  ('share_turnover_65d',  'Share Turnover 65d',         'liquidity','Median daily volume / shares out 65d',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'liquidity', 'real', 'exclude_from_factor', 70, 'price'),

  ('volume_increase_13d', 'Volume Increase 13d',        'liquidity','AvgVol(13) / AvgVol(13,30)',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'universe', 'liquidity', 'real', 'exclude_from_factor', 35, 'price'),

  -- ===== INDUSTRY MOMENTUM (2 factors) =====
  ('industry_momentum_26w','Industry 26W Momentum',     'industry_momentum','Avg 26-week return in same industry',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'industry', 'industry', 'real', 'exclude_from_factor', 135, 'derived'),

  ('industry_momentum_52w','Industry 52W Momentum',     'industry_momentum','Avg 52-week return in same industry',
   'marcap', null, true, null, false, true, 'real',
   'higher_is_better', 'industry', 'industry', 'real', 'exclude_from_factor', 260, 'derived'),

  -- ===== SENTIMENT (4 factors — mostly unavailable) =====
  ('eps_revision_fy',     'EPS Estimate Revision (FY)', 'sentiment','Change in consensus EPS estimate',
   null, null, false, null, false, true, 'unavailable',
   'higher_is_better', 'universe', 'estimate_revision', 'unavailable', 'exclude_from_factor', 0, 'estimates'),

  ('eps_surprise_q1',     'Latest Earnings Surprise',   'sentiment','Most recent EPS surprise vs consensus',
   null, null, false, null, false, true, 'unavailable',
   'higher_is_better', 'universe', 'surprise', 'unavailable', 'exclude_from_factor', 0, 'estimates'),

  ('avg_recommendation',  'Average Recommendation',     'sentiment','Mean analyst recommendation (1=buy 5=sell)',
   null, null, false, null, false, true, 'unavailable',
   'lower_is_better', 'universe', 'recommendations', 'unavailable', 'exclude_from_factor', 0, 'estimates'),

  ('short_interest_pct',  'Short Interest % Shares',    'sentiment','Short balance / shares outstanding',
   'pykrx', null, false, null, false, true, 'unavailable',
   'lower_is_better', 'universe', 'short_interest', 'unavailable', 'exclude_from_factor', 0, 'pykrx')

ON CONFLICT (factor_id) DO UPDATE SET
  name                  = EXCLUDED.name,
  category              = EXCLUDED.category,
  formula               = EXCLUDED.formula,
  preferred_source      = EXCLUDED.preferred_source,
  fallback_source       = EXCLUDED.fallback_source,
  is_available          = EXCLUDED.is_available,
  uses_mock_data        = EXCLUDED.uses_mock_data,
  point_in_time_safe    = EXCLUDED.point_in_time_safe,
  data_status           = EXCLUDED.data_status,
  rank_direction        = EXCLUDED.rank_direction,
  scope                 = EXCLUDED.scope,
  subcategory           = EXCLUDED.subcategory,
  implementation_status = EXCLUDED.implementation_status,
  missing_value_policy  = EXCLUDED.missing_value_policy,
  lookback_days         = EXCLUDED.lookback_days,
  data_source_detail    = EXCLUDED.data_source_detail,
  last_updated          = now();


-- ---------------------------------------------------------------------------
-- 6. Seed ranking systems
-- ---------------------------------------------------------------------------

-- 6a. Legacy "default" system
INSERT INTO ranking_systems (id, name, description, tree, options)
VALUES (
  'default',
  'Default Composite (Legacy)',
  'Legacy multi-factor ranking: Value 30%, Quality 25%, Growth 20%, Momentum 25%',
  '{"id":"root","type":"composite","name":"Composite","weight":100,"children":[{"id":"cat-value","type":"category","name":"Value","weight":30,"children":[{"id":"f-ey","type":"factor","name":"Earnings Yield","weight":35,"factorId":"earnings_yield"},{"id":"f-bm","type":"factor","name":"Book-to-Market","weight":25,"factorId":"book_to_market"},{"id":"f-ev","type":"factor","name":"EV/EBITDA (inv)","weight":25,"factorId":"ev_ebitda"},{"id":"f-cfy","type":"factor","name":"Cash Flow Yield","weight":15,"factorId":"cf_yield"}]},{"id":"cat-quality","type":"category","name":"Quality","weight":25,"children":[{"id":"f-roe","type":"factor","name":"ROE","weight":35,"factorId":"roe"},{"id":"f-gp","type":"factor","name":"Gross Profitability","weight":30,"factorId":"gross_profitability"},{"id":"f-om","type":"factor","name":"Operating Margin","weight":20,"factorId":"operating_margin"},{"id":"f-de","type":"factor","name":"Debt/Equity","weight":15,"factorId":"debt_to_equity"}]},{"id":"cat-growth","type":"category","name":"Growth","weight":20,"children":[{"id":"f-revg","type":"factor","name":"Revenue Growth","weight":40,"factorId":"revenue_growth"},{"id":"f-epsg","type":"factor","name":"EPS Growth","weight":35,"factorId":"eps_growth"},{"id":"f-opg","type":"factor","name":"Op Income Growth","weight":25,"factorId":"op_income_growth"}]},{"id":"cat-momentum","type":"category","name":"Momentum","weight":25,"children":[{"id":"f-mom12","type":"factor","name":"12-1M Momentum","weight":50,"factorId":"momentum_12_1"},{"id":"f-mom6","type":"factor","name":"6M Momentum","weight":30,"factorId":"momentum_6m"},{"id":"f-52w","type":"factor","name":"Dist from 52W High","weight":20,"factorId":"dist_52w_high"}]}]}'::jsonb,
  '{"winsorize":true,"winsorizeLevel":5,"sectorNeutral":false,"higherIsBetter":true}'::jsonb
)
ON CONFLICT (id) DO UPDATE SET
  name        = EXCLUDED.name,
  description = EXCLUDED.description,
  tree        = EXCLUDED.tree,
  options     = EXCLUDED.options,
  updated_at  = now();

-- 6b. P123-inspired system
INSERT INTO ranking_systems (id, name, description, tree, options)
VALUES (
  'p123-inspired',
  'P123 Inspired Korea Multi-Factor',
  'P123-inspired multi-factor: Value 25%, Quality 30%, Growth 15%, Momentum 10%, Low Volatility 10%, Sentiment 10%',
  '{"id":"root","type":"composite","name":"P123 Inspired Korea Multi-Factor","weight":100,"children":[{"id":"cat-value","type":"category","name":"Value","weight":25,"children":[{"id":"sub-val-earn","type":"composite","name":"Earnings-Based","weight":35,"children":[{"id":"f-ey","type":"factor","name":"Earnings Yield","weight":50,"factorId":"pe_ttm_inv"},{"id":"f-ebitda-ev","type":"factor","name":"EBITDA/EV","weight":50,"factorId":"ebitda_ev"}]},{"id":"sub-val-sales","type":"composite","name":"Sales-Based","weight":30,"children":[{"id":"f-ps","type":"factor","name":"Sales Yield","weight":40,"factorId":"price_sales_ttm_inv"},{"id":"f-evs","type":"factor","name":"Revenue/EV","weight":30,"factorId":"ev_sales_ttm_inv"},{"id":"f-gpev","type":"factor","name":"Gross Profit/EV","weight":30,"factorId":"gross_profit_ev"}]},{"id":"sub-val-fcf","type":"composite","name":"FCF-Based","weight":20,"children":[{"id":"f-fcfy","type":"factor","name":"FCF Yield","weight":40,"factorId":"fcf_mcap"},{"id":"f-ocfy","type":"factor","name":"OCF Yield","weight":30,"factorId":"ocf_mcap"},{"id":"f-ufcf","type":"factor","name":"Unlevered FCF/EV","weight":30,"factorId":"ufcf_ev"}]},{"id":"sub-val-asset","type":"composite","name":"Asset-Based","weight":15,"children":[{"id":"f-pb","type":"factor","name":"Book/Market","weight":60,"factorId":"price_book"},{"id":"f-divy","type":"factor","name":"Dividend Yield","weight":40,"factorId":"dividend_yield"}]}]},{"id":"cat-quality","type":"category","name":"Quality","weight":30,"children":[{"id":"sub-q-margin","type":"composite","name":"Margins","weight":25,"children":[{"id":"f-opmgn","type":"factor","name":"Operating Margin","weight":60,"factorId":"operating_margin_ttm"},{"id":"f-gpmgn","type":"factor","name":"Gross Margin","weight":40,"factorId":"gross_margin_ttm"}]},{"id":"sub-q-roc","type":"composite","name":"Return on Capital","weight":35,"children":[{"id":"f-roe","type":"factor","name":"ROE","weight":35,"factorId":"roe_ttm"},{"id":"f-roa","type":"factor","name":"ROA","weight":30,"factorId":"roa_ttm"},{"id":"f-gpa","type":"factor","name":"Gross Profit/Assets","weight":35,"factorId":"gross_profit_assets"}]},{"id":"sub-q-turn","type":"composite","name":"Turnover","weight":15,"children":[{"id":"f-at","type":"factor","name":"Asset Turnover","weight":100,"factorId":"asset_turnover_ttm"}]},{"id":"sub-q-fin","type":"composite","name":"Finances","weight":25,"children":[{"id":"f-de","type":"factor","name":"Debt/Equity","weight":50,"factorId":"debt_to_equity"},{"id":"f-ic","type":"factor","name":"Interest Coverage","weight":50,"factorId":"interest_coverage_ttm"}]}]},{"id":"cat-growth","type":"category","name":"Growth","weight":15,"children":[{"id":"sub-g-sales","type":"composite","name":"Sales Growth","weight":35,"children":[{"id":"f-sg","type":"factor","name":"Sales Growth YoY","weight":100,"factorId":"sales_growth_yoy"}]},{"id":"sub-g-opinc","type":"composite","name":"Op Income Growth","weight":30,"children":[{"id":"f-oig","type":"factor","name":"Op Income Growth YoY","weight":100,"factorId":"op_income_growth_yoy"}]},{"id":"sub-g-eps","type":"composite","name":"EPS Growth","weight":35,"children":[{"id":"f-epsg","type":"factor","name":"EPS Growth YoY","weight":50,"factorId":"eps_growth_yoy"},{"id":"f-nig","type":"factor","name":"Net Income Growth YoY","weight":50,"factorId":"net_income_growth_yoy"}]}]},{"id":"cat-momentum","type":"category","name":"Momentum","weight":10,"children":[{"id":"sub-m-price","type":"composite","name":"Price Changes","weight":35,"children":[{"id":"f-pc120","type":"factor","name":"120d Return","weight":50,"factorId":"price_change_120d"},{"id":"f-pc180","type":"factor","name":"180d Return","weight":50,"factorId":"price_change_180d"}]},{"id":"sub-m-tech","type":"composite","name":"Technical","weight":35,"children":[{"id":"f-udr20","type":"factor","name":"UpDown 20d","weight":20,"factorId":"up_down_ratio_20"},{"id":"f-udr60","type":"factor","name":"UpDown 60d","weight":30,"factorId":"up_down_ratio_60"},{"id":"f-udr120","type":"factor","name":"UpDown 120d","weight":25,"factorId":"up_down_ratio_120"},{"id":"f-rsi200","type":"factor","name":"RSI 200","weight":25,"factorId":"rsi_200"}]},{"id":"sub-m-qtr","type":"composite","name":"Quarterly Returns","weight":30,"children":[{"id":"f-m3","type":"factor","name":"3M Return","weight":30,"factorId":"momentum_3m"},{"id":"f-m6","type":"factor","name":"6M Return","weight":35,"factorId":"momentum_6m"},{"id":"f-m121","type":"factor","name":"12-1M Momentum","weight":35,"factorId":"momentum_12_1"}]}]},{"id":"cat-risk","type":"category","name":"Low Volatility","weight":10,"children":[{"id":"f-vol252","type":"factor","name":"252d Volatility","weight":40,"factorId":"volatility_252d"},{"id":"f-vol60","type":"factor","name":"60d Volatility","weight":30,"factorId":"volatility_60d"},{"id":"f-mdd","type":"factor","name":"Max Drawdown 252d","weight":30,"factorId":"max_drawdown_252d"}]},{"id":"cat-sentiment","type":"category","name":"Sentiment","weight":10,"children":[{"id":"f-si","type":"factor","name":"Short Interest","weight":100,"factorId":"short_interest_pct"}]}]}'::jsonb,
  '{"winsorize":true,"winsorizeLevel":5,"sectorNeutral":false,"higherIsBetter":true}'::jsonb
)
ON CONFLICT (id) DO UPDATE SET
  name        = EXCLUDED.name,
  description = EXCLUDED.description,
  tree        = EXCLUDED.tree,
  options     = EXCLUDED.options,
  updated_at  = now();


-- ---------------------------------------------------------------------------
-- Done
-- ---------------------------------------------------------------------------

COMMIT;
