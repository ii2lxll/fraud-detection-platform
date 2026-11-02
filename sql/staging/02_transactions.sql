-- =====================================================================
-- STAGING: transactions
--
-- Step 1  parse every raw row into proper types and work out what is wrong with it
-- Step 2  record each problem in staging.dq_findings
-- Step 3  keep the rows we can trust in staging.transactions
--
-- Repairs use REDUNDANT information already in the row: a missing or negative amount on a
-- successful transaction can be recovered from the balance change (balance_after - balance_before).
-- =====================================================================

DROP TABLE IF EXISTS staging.transactions CASCADE;

-- ---------------------------------------------------------------- step 1: parse and check
CREATE TEMP TABLE transactions_parsed ON COMMIT DROP AS
SELECT
    r.txn_id, r.txn_ref, r.account_id, r.card_id, r.direction, r.channel, r.merchant_id,
    r.counterparty_account, r.counterparty_bank, r.status, r.decline_reason,
    r._load_id, r._row_number,
    r.txn_timestamp AS raw_timestamp,
    r.txn_type      AS raw_txn_type,
    r.currency      AS raw_currency,
    r.amount        AS raw_amount,
    -- duplicates: the first copy (earliest load, earliest row) is the one we keep
    row_number() OVER (PARTITION BY r.txn_id ORDER BY r._load_id, r._row_number) AS copy_number,
    -- the business date of the file this row arrived in (from its name, e.g. transactions_20260620.csv)
    to_date(substring(l.file_name FROM 'transactions_(\d{8})'), 'YYYYMMDD') AS file_date,
    -- schema drift: device_id was renamed device_fingerprint mid-way. to_jsonb(r) ->> 'col' returns
    -- NULL if the column doesn't exist yet, so this works before AND after the rename.
    coalesce(r.device_id, to_jsonb(r) ->> 'device_fingerprint') AS device_id,
    to_jsonb(r) ->> 'terminal_id'                               AS terminal_id,
    upper(replace(trim(r.txn_type), ' ', '_'))                  AS txn_type,
    CASE WHEN upper(trim(r.currency)) IN ('USD', 'US$') THEN 'USD' END AS currency,
    CASE
        WHEN r.txn_timestamp ~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$' THEN r.txn_timestamp::timestamp
        WHEN r.txn_timestamp ~ '^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$'
            THEN to_timestamp(r.txn_timestamp, 'MM/DD/YYYY HH24:MI')::timestamp
    END AS txn_timestamp,
    CASE WHEN r.amount ~ '^-?\$?[0-9,]+(\.[0-9]+)?$'
         THEN replace(replace(r.amount, '$', ''), ',', '')::numeric(14, 2) END AS amount_parsed,
    r.balance_before::numeric(14, 2) AS balance_before,
    r.balance_after::numeric(14, 2)  AS balance_after
FROM raw.transactions r
JOIN audit.file_load_log l ON l.load_id = r._load_id;

CREATE TEMP TABLE transactions_checked ON COMMIT DROP AS
SELECT p.*,
       abs(p.balance_after - p.balance_before) AS balance_change,
       CASE
           WHEN p.amount_parsed >= 0 THEN p.amount_parsed
           WHEN p.status = 'SUCCESS' AND p.amount_parsed IS NULL THEN abs(p.balance_after - p.balance_before)
           WHEN p.status = 'SUCCESS' AND abs(p.amount_parsed) = abs(p.balance_after - p.balance_before)
               THEN abs(p.amount_parsed)
       END AS amount,
       a.account_id IS NULL AS unknown_account,
       p.txn_type NOT IN ('CARD_PURCHASE', 'TRANSFER_OUT', 'TRANSFER_IN', 'BILL_PAYMENT',
                          'ATM_WITHDRAWAL', 'SALARY', 'BUSINESS_INCOME') AS invalid_txn_type
FROM transactions_parsed p
LEFT JOIN staging.accounts a ON a.account_id = p.account_id;

-- a row is rejected if ANY of these is true
CREATE TEMP TABLE transactions_decided ON COMMIT DROP AS
SELECT c.*,
       (c.unknown_account OR c.invalid_txn_type OR c.currency IS NULL
        OR c.txn_timestamp IS NULL OR c.amount IS NULL) AS rejected
FROM transactions_checked c
WHERE c.copy_number = 1;

-- ---------------------------------------------------------------- step 2: record findings
INSERT INTO staging.dq_findings (source_table, record_key, finding, action, _load_id, _row_number)
SELECT 'transactions', txn_id, 'DUPLICATE', 'REMOVED', _load_id, _row_number
FROM transactions_checked
WHERE copy_number > 1;

INSERT INTO staging.dq_findings (source_table, record_key, finding, action, _load_id, _row_number)
SELECT 'transactions', d.txn_id, f.finding, f.action, d._load_id, d._row_number
FROM transactions_decided d
CROSS JOIN LATERAL (VALUES
    ('NONSTANDARD_TXN_TYPE',  'REPAIRED', d.raw_txn_type <> d.txn_type AND NOT d.invalid_txn_type),
    ('INVALID_TXN_TYPE',      'REJECTED', d.invalid_txn_type),
    ('NONSTANDARD_CURRENCY',  'REPAIRED', d.raw_currency <> 'USD' AND d.currency = 'USD'),
    ('INVALID_CURRENCY',      'REJECTED', d.currency IS NULL),
    ('NONSTANDARD_TIMESTAMP', 'REPAIRED', d.txn_timestamp IS NOT NULL
                                          AND d.raw_timestamp !~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$'),
    ('INVALID_TIMESTAMP',     'REJECTED', d.txn_timestamp IS NULL),
    ('AMOUNT_TEXT_FORMAT',    'REPAIRED', d.raw_amount ~ '[$,]'),
    ('MISSING_AMOUNT',        CASE WHEN d.amount IS NULL THEN 'REJECTED' ELSE 'REPAIRED' END,
                              d.raw_amount IS NULL),
    ('NEGATIVE_AMOUNT',       CASE WHEN d.amount IS NULL THEN 'REJECTED' ELSE 'REPAIRED' END,
                              d.amount_parsed < 0),
    ('UNKNOWN_ACCOUNT',       'REJECTED', d.unknown_account),
    ('LATE_ARRIVING',         'FLAGGED',  d.txn_timestamp::date < d.file_date)
) AS f(finding, action, applies)
WHERE f.applies;

-- ---------------------------------------------------------------- step 3: the clean table
CREATE TABLE staging.transactions AS
SELECT txn_id, txn_ref, txn_timestamp, account_id, card_id, txn_type, direction,
       amount, currency, channel, merchant_id, counterparty_account, counterparty_bank,
       device_id, terminal_id, balance_before, balance_after, status, decline_reason,
       (txn_timestamp::date < file_date) AS arrived_late,
       _load_id    AS source_load_id,
       _row_number AS source_row_number
FROM transactions_decided
WHERE NOT rejected;

ALTER TABLE staging.transactions ADD PRIMARY KEY (txn_id);
CREATE INDEX ix_stg_transactions_account_time ON staging.transactions (account_id, txn_timestamp);
