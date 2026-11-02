-- =====================================================================
-- MART: fact_fraud_claims (one row per claim: what the bank was told, and what it concluded)
--
-- Each claim arrives as a NEW record (status OPEN) and, once investigated, an UPDATE record
-- (CONFIRMED_FRAUD or REJECTED). Mule findings arrive already confirmed.
-- =====================================================================

DROP TABLE IF EXISTS mart.fact_fraud_claims CASCADE;

CREATE TABLE mart.fact_fraud_claims AS
WITH claims AS (
    SELECT n.report_id, n.bank_case_ref, n.claim_type, n.report_channel, n.txn_id,
           n.account_id, n.amount,
           n.event_date                       AS reported_date,
           u.event_date                       AS resolved_date,
           coalesce(u.status, n.status)       AS final_status
    FROM staging.fraud_reports n
    LEFT JOIN staging.fraud_reports u ON u.report_id = n.report_id AND u.record_type = 'UPDATE'
    WHERE n.record_type = 'NEW'
)
SELECT c.report_id,
       c.bank_case_ref,
       to_char(c.reported_date, 'YYYYMMDD')::integer AS reported_date_key,
       c.reported_date,
       c.resolved_date,
       a.account_key,
       a.customer_key,
       c.txn_id,
       c.claim_type,
       c.report_channel,
       c.amount,
       CASE c.final_status WHEN 'CONFIRMED_FRAUD' THEN 'CONFIRMED' ELSE c.final_status END AS claim_status,
       (c.reported_date - t.txn_timestamp::date) AS days_to_report,
       (c.resolved_date - c.reported_date)      AS days_to_resolve
FROM claims c
JOIN mart.dim_account a ON a.account_id = c.account_id
LEFT JOIN staging.transactions t ON t.txn_id = c.txn_id;

ALTER TABLE mart.fact_fraud_claims ADD PRIMARY KEY (report_id);
ALTER TABLE mart.fact_fraud_claims ADD FOREIGN KEY (reported_date_key) REFERENCES mart.dim_date (date_key);
ALTER TABLE mart.fact_fraud_claims ADD FOREIGN KEY (account_key) REFERENCES mart.dim_account (account_key);
ALTER TABLE mart.fact_fraud_claims ADD FOREIGN KEY (customer_key) REFERENCES mart.dim_customer (customer_key);

COMMENT ON TABLE  mart.fact_fraud_claims IS 'Fraud claims and investigation outcomes: the bank''s own knowledge of fraud';
COMMENT ON COLUMN mart.fact_fraud_claims.report_id IS 'Claim (report) ID from the fraud operations system';
COMMENT ON COLUMN mart.fact_fraud_claims.bank_case_ref IS 'Investigation case; one case can cover several transactions';
COMMENT ON COLUMN mart.fact_fraud_claims.reported_date_key IS 'Date the claim was received (dim_date)';
COMMENT ON COLUMN mart.fact_fraud_claims.reported_date IS 'Date the claim was received';
COMMENT ON COLUMN mart.fact_fraud_claims.resolved_date IS 'Date the investigation finished; NULL while still open';
COMMENT ON COLUMN mart.fact_fraud_claims.account_key IS 'Account the claim is about (dim_account)';
COMMENT ON COLUMN mart.fact_fraud_claims.customer_key IS 'Customer the claim is about (dim_customer)';
COMMENT ON COLUMN mart.fact_fraud_claims.txn_id IS 'Transaction the claim is about';
COMMENT ON COLUMN mart.fact_fraud_claims.claim_type IS 'UNAUTHORISED_TRANSFER, SCAM_PAYMENT, UNAUTHORISED_CARD or MULE_ACCOUNT';
COMMENT ON COLUMN mart.fact_fraud_claims.report_channel IS 'How the claim arrived: CALL_CENTRE, MOBILE_APP, BRANCH, CHARGEBACK or INVESTIGATION';
COMMENT ON COLUMN mart.fact_fraud_claims.amount IS 'Amount of the transaction claimed';
COMMENT ON COLUMN mart.fact_fraud_claims.claim_status IS 'CONFIRMED, REJECTED or OPEN (not yet investigated)';
COMMENT ON COLUMN mart.fact_fraud_claims.days_to_report IS 'Days between the transaction and the claim (reporting delay)';
COMMENT ON COLUMN mart.fact_fraud_claims.days_to_resolve IS 'Days the investigation took';
