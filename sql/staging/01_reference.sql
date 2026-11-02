-- =====================================================================
-- STAGING: reference tables (customers, accounts, cards, merchants, devices)
--
-- Rule for reference data: never drop a customer or merchant because ONE attribute is bad.
-- Dropping a customer would orphan their accounts and transactions. Instead, the bad value
-- is set to NULL and flagged, or repaired when another trusted source has the right value.
-- =====================================================================

-- ---------------------------------------------------------------- customers
DROP TABLE IF EXISTS staging.customers CASCADE;

CREATE TEMP TABLE customers_checked ON COMMIT DROP AS
SELECT r.*,
       row_number() OVER (PARTITION BY r.customer_id ORDER BY r._load_id, r._row_number) AS copy_number,
       CASE WHEN r.date_of_birth ~ '^\d{4}-\d{2}-\d{2}$'
             AND r.date_of_birth::date BETWEEN l.delivery_date - INTERVAL '120 years'
                                           AND l.delivery_date - INTERVAL '18 years'
            THEN r.date_of_birth::date END                                   AS dob_clean,
       CASE WHEN r.email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' THEN lower(r.email) END AS email_clean
FROM raw.customers r
JOIN audit.file_load_log l ON l.load_id = r._load_id;

INSERT INTO staging.dq_findings (source_table, record_key, finding, action, _load_id, _row_number)
SELECT 'customers', c.customer_id, f.finding, f.action, c._load_id, c._row_number
FROM customers_checked c
CROSS JOIN LATERAL (VALUES
    ('DUPLICATE_CUSTOMER', 'REMOVED', c.copy_number > 1),
    ('INVALID_BIRTH_DATE', 'FLAGGED', c.copy_number = 1 AND c.dob_clean IS NULL),
    ('INVALID_EMAIL',      'FLAGGED', c.copy_number = 1 AND c.email_clean IS NULL),
    ('MISSING_STATE',      'FLAGGED', c.copy_number = 1 AND c.state IS NULL)
) AS f(finding, action, applies)
WHERE f.applies;

CREATE TABLE staging.customers AS
SELECT customer_id, first_name, last_name,
       dob_clean            AS date_of_birth,
       email_clean          AS email,
       phone, street_address, city, state, postcode, occupation,
       segment, income_band, kyc_risk_rating,
       customer_since::date AS customer_since,
       preferred_channel
FROM customers_checked
WHERE copy_number = 1;

ALTER TABLE staging.customers ADD PRIMARY KEY (customer_id);

-- ---------------------------------------------------------------- accounts
DROP TABLE IF EXISTS staging.accounts CASCADE;

CREATE TABLE staging.accounts AS
SELECT account_id, customer_id, account_type,
       open_date::date                  AS open_date,
       status,
       close_date::date                 AS close_date,
       currency,
       current_balance::numeric(14, 2)  AS opening_balance      -- balance at the snapshot date
FROM raw.accounts;

ALTER TABLE staging.accounts ADD PRIMARY KEY (account_id);

-- ---------------------------------------------------------------- cards
DROP TABLE IF EXISTS staging.cards CASCADE;

CREATE TABLE staging.cards AS
SELECT card_id, account_id, card_token, masked_pan, network,
       issue_date::date  AS issue_date,
       expiry_date::date AS expiry_date,
       card_status
FROM raw.cards;

ALTER TABLE staging.cards ADD PRIMARY KEY (card_id);

-- ---------------------------------------------------------------- merchants
-- A missing MCC is recovered from the other merchants in the same category.
DROP TABLE IF EXISTS staging.merchants CASCADE;

CREATE TEMP TABLE merchants_checked ON COMMIT DROP AS
WITH category_mcc AS (
    SELECT category, mode() WITHIN GROUP (ORDER BY mcc) AS mcc
    FROM raw.merchants
    WHERE mcc IS NOT NULL
    GROUP BY category
)
SELECT m.*, c.mcc AS category_mcc
FROM raw.merchants m
LEFT JOIN category_mcc c ON c.category = m.category;

INSERT INTO staging.dq_findings (source_table, record_key, finding, action, _load_id, _row_number)
SELECT 'merchants', merchant_id, 'MISSING_MCC',
       CASE WHEN category_mcc IS NOT NULL THEN 'REPAIRED' ELSE 'FLAGGED' END,
       _load_id, _row_number
FROM merchants_checked
WHERE mcc IS NULL;

CREATE TABLE staging.merchants AS
SELECT merchant_id, merchant_name, category,
       coalesce(mcc, category_mcc)::integer AS mcc,
       channel, country, city, state, risk_tier
FROM merchants_checked;

ALTER TABLE staging.merchants ADD PRIMARY KEY (merchant_id);

-- ---------------------------------------------------------------- devices
DROP TABLE IF EXISTS staging.devices CASCADE;

CREATE TABLE staging.devices AS
SELECT device_id, customer_id, device_type, device_model, user_agent,
       first_seen::date AS first_seen
FROM raw.devices;

ALTER TABLE staging.devices ADD PRIMARY KEY (device_id);
