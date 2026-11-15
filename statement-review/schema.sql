-- Tables the app reads and writes (PostgreSQL syntax). The app creates them
-- itself when AUTO_CREATE_TABLES=1; use this if you manage schema by hand.
CREATE TABLE IF NOT EXISTS bank_statements (
    id               SERIAL PRIMARY KEY,
    bank             VARCHAR(64)  NOT NULL,
    account_type     VARCHAR(32)  NOT NULL,          -- checking | savings | credit_card
    account_name     VARCHAR(128),
    account_last4    VARCHAR(8),
    period_start     DATE,
    period_end       DATE,
    opening_balance  NUMERIC(14,2),
    closing_balance  NUMERIC(14,2),
    total_credits    NUMERIC(14,2),
    total_debits     NUMERIC(14,2),
    source_filename  VARCHAR(255),
    file_sha256      VARCHAR(64),
    extra            JSON,                             -- custom fields added in the UI
    created_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_bank_statements_file_sha256 ON bank_statements (file_sha256);

CREATE TABLE IF NOT EXISTS bank_statement_transactions (
    id            SERIAL PRIMARY KEY,
    statement_id  INTEGER NOT NULL REFERENCES bank_statements(id) ON DELETE CASCADE,
    line_no       INTEGER NOT NULL,
    txn_date      DATE    NOT NULL,
    post_date     DATE,
    description   TEXT    NOT NULL,
    category      VARCHAR(128),
    amount        NUMERIC(14,2) NOT NULL,  -- + money in / card payment, - money out / card charge
    extra         JSON                      -- custom columns added in the UI
);
CREATE INDEX IF NOT EXISTS ix_bank_statement_transactions_statement_id ON bank_statement_transactions (statement_id);
