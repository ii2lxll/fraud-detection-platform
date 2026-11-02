-- =====================================================================
-- MART: fact_transactions (one row per transaction: the main table for fraud analysis)
--
-- The fraud label comes from the bank's OWN claims (fact_fraud_claims), never from the simulator's
-- answer key. So, like a real bank, the mart only knows about fraud that was reported and confirmed:
--   fraud_label = 1  confirmed fraud
--   fraud_label = 0  no confirmed fraud: genuine, OR fraud nobody has reported yet
--   claim_status     tells you which transactions are still under investigation (OPEN)
-- =====================================================================

DROP TABLE IF EXISTS mart.fact_transactions CASCADE;

CREATE TEMP TABLE transaction_claims ON COMMIT DROP AS
SELECT txn_id,
       min(reported_date) AS first_reported_date,
       CASE WHEN bool_or(claim_status = 'CONFIRMED') THEN 'CONFIRMED'
            WHEN bool_or(claim_status = 'OPEN') THEN 'OPEN'
            ELSE 'REJECTED' END AS claim_status,
       max(resolved_date) FILTER (WHERE claim_status = 'CONFIRMED') AS confirmed_date
FROM mart.fact_fraud_claims
WHERE claim_type <> 'MULE_ACCOUNT'           -- mule findings describe the receiving account, not the payment
GROUP BY txn_id;

CREATE TABLE mart.fact_transactions AS
SELECT t.txn_id,
       to_char(t.txn_timestamp, 'YYYYMMDD')::integer      AS date_key,
       t.txn_timestamp,
       extract(hour FROM t.txn_timestamp)::smallint       AS hour_of_day,
       a.account_key,
       a.customer_key,
       coalesce(m.merchant_key, -1)                        AS merchant_key,
       t.txn_type, t.direction, t.channel,
       t.amount,
       CASE WHEN t.direction = 'DR' THEN -t.amount ELSE t.amount END AS signed_amount,
       t.balance_before, t.balance_after,
       t.status,
       (t.status = 'DECLINED')::int::smallint             AS is_declined,
       t.counterparty_account, t.counterparty_bank,
       coalesce(t.counterparty_bank = current_setting('mart.bank_code'), false)::int::smallint AS is_internal_transfer,
       t.device_id,
       CASE WHEN t.channel IN ('MOBILE', 'WEB') THEN (d.device_id IS NOT NULL)::int::smallint END AS device_known,
       t.arrived_late::int::smallint                      AS arrived_late,
       coalesce((c.claim_status = 'CONFIRMED')::int, 0)::smallint AS fraud_label,
       c.claim_status,
       c.first_reported_date,
       c.confirmed_date
FROM staging.transactions t
JOIN staging.accounts sa    ON sa.account_id = t.account_id
JOIN mart.dim_account a     ON a.account_id = t.account_id
LEFT JOIN mart.dim_merchant m ON m.merchant_id = t.merchant_id
LEFT JOIN staging.devices d ON d.device_id = t.device_id AND d.customer_id = sa.customer_id
LEFT JOIN transaction_claims c ON c.txn_id = t.txn_id;

ALTER TABLE mart.fact_transactions ADD PRIMARY KEY (txn_id);
ALTER TABLE mart.fact_transactions ADD FOREIGN KEY (date_key) REFERENCES mart.dim_date (date_key);
ALTER TABLE mart.fact_transactions ADD FOREIGN KEY (account_key) REFERENCES mart.dim_account (account_key);
ALTER TABLE mart.fact_transactions ADD FOREIGN KEY (customer_key) REFERENCES mart.dim_customer (customer_key);
ALTER TABLE mart.fact_transactions ADD FOREIGN KEY (merchant_key) REFERENCES mart.dim_merchant (merchant_key);
CREATE INDEX ix_fact_txn_account_time ON mart.fact_transactions (account_key, txn_timestamp);
CREATE INDEX ix_fact_txn_date ON mart.fact_transactions (date_key);

COMMENT ON TABLE  mart.fact_transactions IS 'One row per transaction, with the fraud label as known to the bank';
COMMENT ON COLUMN mart.fact_transactions.txn_id IS 'Transaction ID from the payments hub';
COMMENT ON COLUMN mart.fact_transactions.date_key IS 'Transaction date (dim_date)';
COMMENT ON COLUMN mart.fact_transactions.txn_timestamp IS 'Date and time of the transaction';
COMMENT ON COLUMN mart.fact_transactions.hour_of_day IS 'Hour of the transaction, 0-23';
COMMENT ON COLUMN mart.fact_transactions.account_key IS 'Account (dim_account)';
COMMENT ON COLUMN mart.fact_transactions.customer_key IS 'Account owner (dim_customer)';
COMMENT ON COLUMN mart.fact_transactions.merchant_key IS 'Merchant (dim_merchant); -1 = no merchant';
COMMENT ON COLUMN mart.fact_transactions.txn_type IS 'CARD_PURCHASE, TRANSFER_OUT, TRANSFER_IN, BILL_PAYMENT, ATM_WITHDRAWAL, SALARY or BUSINESS_INCOME';
COMMENT ON COLUMN mart.fact_transactions.direction IS 'DR = money out, CR = money in';
COMMENT ON COLUMN mart.fact_transactions.channel IS 'POS, ECOM, ATM, MOBILE, WEB, BRANCH, NETWORK or INTERNAL';
COMMENT ON COLUMN mart.fact_transactions.amount IS 'Amount in USD, always positive';
COMMENT ON COLUMN mart.fact_transactions.signed_amount IS 'Amount with sign: negative for money out, positive for money in';
COMMENT ON COLUMN mart.fact_transactions.balance_before IS 'Account balance before the transaction';
COMMENT ON COLUMN mart.fact_transactions.balance_after IS 'Account balance after the transaction (unchanged if declined)';
COMMENT ON COLUMN mart.fact_transactions.status IS 'SUCCESS or DECLINED';
COMMENT ON COLUMN mart.fact_transactions.is_declined IS '1 if the transaction was declined';
COMMENT ON COLUMN mart.fact_transactions.counterparty_account IS 'Other party of a transfer or salary payment';
COMMENT ON COLUMN mart.fact_transactions.counterparty_bank IS 'Bank of the other party';
COMMENT ON COLUMN mart.fact_transactions.is_internal_transfer IS '1 if the other party banks with us';
COMMENT ON COLUMN mart.fact_transactions.device_id IS 'Device used for mobile or web transactions';
COMMENT ON COLUMN mart.fact_transactions.device_known IS 'Mobile/web only: 1 if the device is registered to this customer, 0 if not; NULL for other channels';
COMMENT ON COLUMN mart.fact_transactions.arrived_late IS '1 if the record arrived in a later day''s file than its transaction date';
COMMENT ON COLUMN mart.fact_transactions.fraud_label IS '1 = confirmed fraud according to the bank''s investigations. 0 = not confirmed (genuine, or fraud not yet reported)';
COMMENT ON COLUMN mart.fact_transactions.claim_status IS 'CONFIRMED, OPEN or REJECTED if a claim exists; NULL if nobody reported it';
COMMENT ON COLUMN mart.fact_transactions.first_reported_date IS 'Date the bank first heard about a problem with this transaction';
COMMENT ON COLUMN mart.fact_transactions.confirmed_date IS 'Date fraud was confirmed. A model may only learn this label from this date onwards';
