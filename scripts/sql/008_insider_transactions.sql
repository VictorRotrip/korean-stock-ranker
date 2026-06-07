-- =============================================================================
-- Insider Transactions (DART 임원·주요주주 특정증권등 소유상황 보고)
-- =============================================================================
-- Korean executives, board members, and 10%+ shareholders are required to
-- report changes in their company's stock holdings within 5 business days
-- via DART's 임원·주요주주 특정증권등 소유상황 보고. This is the standard
-- "Form 4"-equivalent disclosure and is the data behind any "insider net
-- buying" sentiment factor.
--
-- Source: DART /api/elestock.json
-- Ingested by: scripts/python/ingest_insider_transactions_dart.py
-- =============================================================================

CREATE TABLE IF NOT EXISTS insider_transactions (
    id                BIGSERIAL PRIMARY KEY,
    ticker            VARCHAR(10) NOT NULL,
    receipt_no        VARCHAR(30) NOT NULL,    -- DART filing receipt
    filing_date       DATE        NOT NULL,    -- when DART received the filing
    transaction_date  DATE,                    -- actual trade date (may be null on old filings)
    filer_name        TEXT,                    -- 보고자 — executive / shareholder name
    filer_role        TEXT,                    -- 등기/미등기 임원 등 (executive registration status)
    officer_title     TEXT,                    -- 직위 (board member, CEO, etc.)
    is_main_shrholdr  BOOLEAN,                 -- TRUE if 주요주주 (>10% owner)
    relation          TEXT,                    -- 관계 (self / family / affiliate)
    stock_type        TEXT,                    -- 보통주 / 우선주 / etc.
    share_change      BIGINT,                  -- signed: + buy, - sell
    share_balance_after BIGINT,                -- shares held after the transaction
    change_reason     TEXT,                    -- 변동 사유 (market trade, gift, inheritance, exercise...)
    source            VARCHAR(20) DEFAULT 'dart',
    created_at        TIMESTAMP   DEFAULT NOW()
);

-- Composite uniqueness — same (filing, filer, transaction_date, change) row
-- shouldn't get inserted twice. ON CONFLICT relies on this.
CREATE UNIQUE INDEX IF NOT EXISTS insider_dedup_idx
    ON insider_transactions (ticker, receipt_no, filer_name, transaction_date, share_change);

CREATE INDEX IF NOT EXISTS insider_ticker_txn_date_idx
    ON insider_transactions (ticker, transaction_date);
CREATE INDEX IF NOT EXISTS insider_filing_date_idx
    ON insider_transactions (filing_date);
CREATE INDEX IF NOT EXISTS insider_ticker_filing_date_idx
    ON insider_transactions (ticker, filing_date);
