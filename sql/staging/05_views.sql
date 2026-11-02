-- =====================================================================
-- STAGING: views for checking data quality
-- =====================================================================

-- How many problems of each kind, and what was done about them
CREATE OR REPLACE VIEW staging.v_dq_summary AS
SELECT source_table, finding, action, count(*) AS records
FROM staging.dq_findings
GROUP BY source_table, finding, action
ORDER BY source_table, records DESC;

-- Rejected transactions next to the ORIGINAL raw values, so an analyst can see exactly why
CREATE OR REPLACE VIEW staging.v_rejected_transactions AS
SELECT f.finding, f.record_key AS txn_id, r.txn_timestamp, r.account_id, r.txn_type, r.amount,
       r.status, l.file_name, f._row_number
FROM staging.dq_findings f
JOIN raw.transactions r ON r._load_id = f._load_id AND r._row_number = f._row_number
JOIN audit.file_load_log l ON l.load_id = f._load_id
WHERE f.source_table = 'transactions' AND f.action = 'REJECTED';
