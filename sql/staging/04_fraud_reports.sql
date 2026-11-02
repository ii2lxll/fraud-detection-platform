-- =====================================================================
-- STAGING: fraud reports
-- A missing amount is recovered from the transaction the report points to (a trusted source).
-- Each report has a NEW record and, once investigated, an UPDATE record: both are kept.
-- =====================================================================

DROP TABLE IF EXISTS staging.fraud_reports CASCADE;

CREATE TEMP TABLE fraud_reports_checked ON COMMIT DROP AS
SELECT r.*,
       row_number() OVER (PARTITION BY r.report_id, r.record_type ORDER BY r._load_id, r._row_number) AS copy_number,
       CASE WHEN replace(r.event_date, '/', '-') ~ '^\d{4}-\d{2}-\d{2}$'
            THEN replace(r.event_date, '/', '-')::date END AS event_date_clean,
       coalesce(r.amount::numeric(14, 2), t.amount) AS amount_clean
FROM raw.fraud_reports r
LEFT JOIN staging.transactions t ON t.txn_id = r.txn_id;

INSERT INTO staging.dq_findings (source_table, record_key, finding, action, _load_id, _row_number)
SELECT 'fraud_reports', c.report_id, f.finding, f.action, c._load_id, c._row_number
FROM fraud_reports_checked c
CROSS JOIN LATERAL (VALUES
    ('DUPLICATE',        'REMOVED',  c.copy_number > 1),
    ('NONSTANDARD_DATE', 'REPAIRED', c.copy_number = 1 AND c.event_date_clean IS NOT NULL
                                     AND c.event_date !~ '^\d{4}-\d{2}-\d{2}$'),
    ('INVALID_DATE',     'REJECTED', c.copy_number = 1 AND c.event_date_clean IS NULL),
    ('MISSING_AMOUNT',   CASE WHEN c.amount_clean IS NULL THEN 'FLAGGED' ELSE 'REPAIRED' END,
                         c.copy_number = 1 AND c.amount IS NULL)
) AS f(finding, action, applies)
WHERE f.applies;

CREATE TABLE staging.fraud_reports AS
SELECT report_id, record_type,
       event_date_clean AS event_date,
       bank_case_ref, report_channel, customer_id, account_id, txn_id,
       amount_clean     AS amount,
       claim_type, status,
       _load_id    AS source_load_id,
       _row_number AS source_row_number
FROM fraud_reports_checked
WHERE copy_number = 1 AND event_date_clean IS NOT NULL;

ALTER TABLE staging.fraud_reports ADD PRIMARY KEY (report_id, record_type);
