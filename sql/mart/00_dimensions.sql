-- =====================================================================
-- MART: dimension tables (the "who / what / where / when" around each fact)
--
-- Rebuilt from staging on every run, in one transaction. build_mart.py sets three values first:
--   current_setting('mart.as_of_date')    the reference snapshot date (ages and tenures are measured here)
--   current_setting('mart.home_country')  the bank's home country
--   current_setting('mart.bank_code')     our bank's code in counterparty fields
--
-- Every table has a surrogate key (customer_key, account_key, ...): a small integer that facts join on.
-- The original IDs are kept too, so any mart row can be traced back to staging and raw.
-- Flags are SMALLINT 0/1 rather than boolean: SAS and most ML libraries read them directly.
-- =====================================================================

-- ---------------------------------------------------------------- dim_date
DROP TABLE IF EXISTS mart.dim_date CASCADE;

CREATE TABLE mart.dim_date AS
WITH bounds AS (
    SELECT least((SELECT min(txn_timestamp)::date FROM staging.transactions),
                 (SELECT min(event_timestamp)::date FROM staging.logins)) AS first_day,
           greatest((SELECT max(txn_timestamp)::date FROM staging.transactions),
                    (SELECT max(event_timestamp)::date FROM staging.logins),
                    (SELECT max(event_date) FROM staging.fraud_reports)) AS last_day
)
SELECT to_char(d, 'YYYYMMDD')::integer           AS date_key,
       d::date                                  AS calendar_date,
       extract(isodow FROM d)::smallint         AS day_of_week,
       to_char(d, 'Dy')                         AS day_name,
       (extract(isodow FROM d) >= 6)::int::smallint AS is_weekend,
       extract(day FROM d)::smallint            AS day_of_month,
       extract(week FROM d)::smallint           AS iso_week,
       extract(month FROM d)::smallint          AS month_number,
       to_char(d, 'Mon')                        AS month_name,
       extract(quarter FROM d)::smallint        AS quarter,
       extract(year FROM d)::smallint           AS year
FROM bounds, generate_series(bounds.first_day, bounds.last_day, INTERVAL '1 day') AS d;

ALTER TABLE mart.dim_date ADD PRIMARY KEY (date_key);

COMMENT ON TABLE  mart.dim_date IS 'One row per calendar day covered by the data';
COMMENT ON COLUMN mart.dim_date.date_key IS 'Date as an integer YYYYMMDD (join key used by every fact table)';
COMMENT ON COLUMN mart.dim_date.calendar_date IS 'The calendar date';
COMMENT ON COLUMN mart.dim_date.day_of_week IS 'ISO day of week: 1 = Monday ... 7 = Sunday';
COMMENT ON COLUMN mart.dim_date.day_name IS 'Short day name, e.g. Mon';
COMMENT ON COLUMN mart.dim_date.is_weekend IS '1 if Saturday or Sunday, else 0';
COMMENT ON COLUMN mart.dim_date.day_of_month IS 'Day of the month, 1-31';
COMMENT ON COLUMN mart.dim_date.iso_week IS 'ISO week number';
COMMENT ON COLUMN mart.dim_date.month_number IS 'Month, 1-12';
COMMENT ON COLUMN mart.dim_date.month_name IS 'Short month name, e.g. Jun';
COMMENT ON COLUMN mart.dim_date.quarter IS 'Calendar quarter, 1-4';
COMMENT ON COLUMN mart.dim_date.year IS 'Calendar year';

-- ---------------------------------------------------------------- dim_customer (no personal data)
DROP TABLE IF EXISTS mart.dim_customer CASCADE;

CREATE TABLE mart.dim_customer AS
WITH aged AS (
    SELECT c.*,
           date_part('year', age(current_setting('mart.as_of_date')::date, c.date_of_birth))::smallint AS age
    FROM staging.customers c
)
SELECT row_number() OVER (ORDER BY customer_id)::integer AS customer_key,
       customer_id,
       age,
       CASE WHEN age IS NULL THEN 'UNKNOWN'
            WHEN age < 25 THEN '18-24' WHEN age < 35 THEN '25-34' WHEN age < 45 THEN '35-44'
            WHEN age < 55 THEN '45-54' WHEN age < 65 THEN '55-64' ELSE '65+' END AS age_band,
       segment, income_band, kyc_risk_rating, preferred_channel,
       coalesce(state, 'UNKNOWN') AS state,
       customer_since,
       (current_setting('mart.as_of_date')::date - customer_since) AS tenure_days
FROM aged;

ALTER TABLE mart.dim_customer ADD PRIMARY KEY (customer_key);
ALTER TABLE mart.dim_customer ADD UNIQUE (customer_id);

COMMENT ON TABLE  mart.dim_customer IS 'Customers for analysis. Names, email, phone and street address are deliberately excluded (data minimisation)';
COMMENT ON COLUMN mart.dim_customer.customer_key IS 'Surrogate key';
COMMENT ON COLUMN mart.dim_customer.customer_id IS 'Customer ID in the core banking system (pseudonymous)';
COMMENT ON COLUMN mart.dim_customer.age IS 'Age in whole years at the as-of date; NULL if the birth date was invalid';
COMMENT ON COLUMN mart.dim_customer.age_band IS 'Age group: 18-24, 25-34, 35-44, 45-54, 55-64, 65+ or UNKNOWN';
COMMENT ON COLUMN mart.dim_customer.segment IS 'RETAIL, PREMIUM or SMALL_BUSINESS';
COMMENT ON COLUMN mart.dim_customer.income_band IS 'Yearly income band, e.g. 50k-80k';
COMMENT ON COLUMN mart.dim_customer.kyc_risk_rating IS 'Know-your-customer risk rating at onboarding: LOW, MEDIUM or HIGH';
COMMENT ON COLUMN mart.dim_customer.preferred_channel IS 'MOBILE, WEB or BRANCH';
COMMENT ON COLUMN mart.dim_customer.state IS 'State of residence, or UNKNOWN if missing';
COMMENT ON COLUMN mart.dim_customer.customer_since IS 'Date the customer joined the bank';
COMMENT ON COLUMN mart.dim_customer.tenure_days IS 'Days between joining and the as-of date';

-- ---------------------------------------------------------------- dim_account
DROP TABLE IF EXISTS mart.dim_account CASCADE;

CREATE TABLE mart.dim_account AS
WITH mule_flags AS (                         -- accounts investigators confirmed as money mules
    SELECT account_id, min(event_date) AS confirmed_date
    FROM staging.fraud_reports
    WHERE claim_type = 'MULE_ACCOUNT' AND status = 'CONFIRMED_FRAUD'
    GROUP BY account_id
)
SELECT row_number() OVER (ORDER BY a.account_id)::integer AS account_key,
       a.account_id,
       c.customer_key,
       a.account_type, a.status, a.open_date, a.close_date,
       (current_setting('mart.as_of_date')::date - a.open_date) AS account_age_days,
       a.opening_balance,
       EXISTS (SELECT 1 FROM staging.cards k
               WHERE k.account_id = a.account_id AND k.card_status = 'ACTIVE')::int::smallint AS has_active_card,
       (m.account_id IS NOT NULL)::int::smallint AS is_confirmed_mule,
       m.confirmed_date                          AS mule_confirmed_date
FROM staging.accounts a
JOIN mart.dim_customer c ON c.customer_id = a.customer_id
LEFT JOIN mule_flags m ON m.account_id = a.account_id;

ALTER TABLE mart.dim_account ADD PRIMARY KEY (account_key);
ALTER TABLE mart.dim_account ADD UNIQUE (account_id);
ALTER TABLE mart.dim_account ADD FOREIGN KEY (customer_key) REFERENCES mart.dim_customer (customer_key);

COMMENT ON TABLE  mart.dim_account IS 'Bank accounts, one row per account';
COMMENT ON COLUMN mart.dim_account.account_key IS 'Surrogate key';
COMMENT ON COLUMN mart.dim_account.account_id IS 'Account ID in the core banking system';
COMMENT ON COLUMN mart.dim_account.customer_key IS 'Owner of the account (dim_customer)';
COMMENT ON COLUMN mart.dim_account.account_type IS 'CHECKING, SAVINGS or BUSINESS_CHECKING';
COMMENT ON COLUMN mart.dim_account.status IS 'ACTIVE, DORMANT or CLOSED at the as-of date';
COMMENT ON COLUMN mart.dim_account.open_date IS 'Date the account was opened';
COMMENT ON COLUMN mart.dim_account.close_date IS 'Date the account was closed, if closed';
COMMENT ON COLUMN mart.dim_account.account_age_days IS 'Days between opening and the as-of date';
COMMENT ON COLUMN mart.dim_account.opening_balance IS 'Balance at the as-of date, before the transactions in this data';
COMMENT ON COLUMN mart.dim_account.has_active_card IS '1 if the account has an active debit card';
COMMENT ON COLUMN mart.dim_account.is_confirmed_mule IS '1 if investigators confirmed this as a money mule account. Known only FROM mule_confirmed_date: never use it as a model feature for earlier transactions';
COMMENT ON COLUMN mart.dim_account.mule_confirmed_date IS 'Date the mule finding was recorded';

-- ---------------------------------------------------------------- dim_merchant
DROP TABLE IF EXISTS mart.dim_merchant CASCADE;

CREATE TABLE mart.dim_merchant AS
SELECT row_number() OVER (ORDER BY merchant_id)::integer AS merchant_key,
       merchant_id, merchant_name, category, mcc, channel, country,
       (country <> current_setting('mart.home_country'))::int::smallint AS is_foreign,
       risk_tier
FROM staging.merchants
UNION ALL
SELECT -1, 'NONE', 'No merchant', 'NONE', NULL, 'NONE', NULL, 0, 'NONE';   -- "unknown member" for transfers, ATM, salary

ALTER TABLE mart.dim_merchant ADD PRIMARY KEY (merchant_key);

COMMENT ON TABLE  mart.dim_merchant IS 'Merchants. Key -1 means "no merchant" (transfers, ATM withdrawals, salaries)';
COMMENT ON COLUMN mart.dim_merchant.merchant_key IS 'Surrogate key; -1 = no merchant';
COMMENT ON COLUMN mart.dim_merchant.merchant_id IS 'Merchant ID from the card network';
COMMENT ON COLUMN mart.dim_merchant.merchant_name IS 'Trading name';
COMMENT ON COLUMN mart.dim_merchant.category IS 'Business category, e.g. GROCERY, ELECTRONICS';
COMMENT ON COLUMN mart.dim_merchant.mcc IS 'Merchant Category Code (ISO 18245)';
COMMENT ON COLUMN mart.dim_merchant.channel IS 'IN_STORE or ONLINE';
COMMENT ON COLUMN mart.dim_merchant.country IS 'Country of the merchant (ISO 3166 alpha-2)';
COMMENT ON COLUMN mart.dim_merchant.is_foreign IS '1 if the merchant is outside the bank''s home country';
COMMENT ON COLUMN mart.dim_merchant.risk_tier IS 'Fraud risk of the category: LOW, MEDIUM or HIGH';
