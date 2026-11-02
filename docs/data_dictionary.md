# Data Dictionary: mart schema

_Generated from the database on 2026-11-03. Do not edit by hand: change the `COMMENT ON` statements in `sql/mart/` and re-run `python3 python/data_dictionary.py`._

The mart is a star schema: **fact tables** record events (transactions, logins, fraud claims) and join to **dimension tables** (customer, account, merchant, date) through integer keys.

| Table | Rows | Description |
|---|---:|---|
| [dim_date](#dim_date) | 90 | One row per calendar day covered by the data |
| [dim_customer](#dim_customer) | 3,000 | Customers for analysis. Names, email, phone and street address are deliberately excluded (data minimisation) |
| [dim_account](#dim_account) | 4,407 | Bank accounts, one row per account |
| [dim_merchant](#dim_merchant) | 501 | Merchants. Key -1 means "no merchant" (transfers, ATM withdrawals, salaries) |
| [fact_transactions](#fact_transactions) | 456,477 | One row per transaction, with the fraud label as known to the bank |
| [fact_logins](#fact_logins) | 207,229 | One row per digital banking login attempt |
| [fact_fraud_claims](#fact_fraud_claims) | 501 | Fraud claims and investigation outcomes: the bank's own knowledge of fraud |

## dim_date

One row per calendar day covered by the data

| Column | Type | Key | Description |
|---|---|---|---|
| `date_key` | integer | PK | Date as an integer YYYYMMDD (join key used by every fact table) |
| `calendar_date` | date |  | The calendar date |
| `day_of_week` | smallint |  | ISO day of week: 1 = Monday ... 7 = Sunday |
| `day_name` | text |  | Short day name, e.g. Mon |
| `is_weekend` | smallint |  | 1 if Saturday or Sunday, else 0 |
| `day_of_month` | smallint |  | Day of the month, 1-31 |
| `iso_week` | smallint |  | ISO week number |
| `month_number` | smallint |  | Month, 1-12 |
| `month_name` | text |  | Short month name, e.g. Jun |
| `quarter` | smallint |  | Calendar quarter, 1-4 |
| `year` | smallint |  | Calendar year |

## dim_customer

Customers for analysis. Names, email, phone and street address are deliberately excluded (data minimisation)

| Column | Type | Key | Description |
|---|---|---|---|
| `customer_key` | integer | PK | Surrogate key |
| `customer_id` | text |  | Customer ID in the core banking system (pseudonymous) |
| `age` | smallint |  | Age in whole years at the as-of date; NULL if the birth date was invalid |
| `age_band` | text |  | Age group: 18-24, 25-34, 35-44, 45-54, 55-64, 65+ or UNKNOWN |
| `segment` | text |  | RETAIL, PREMIUM or SMALL_BUSINESS |
| `income_band` | text |  | Yearly income band, e.g. 50k-80k |
| `kyc_risk_rating` | text |  | Know-your-customer risk rating at onboarding: LOW, MEDIUM or HIGH |
| `preferred_channel` | text |  | MOBILE, WEB or BRANCH |
| `state` | text |  | State of residence, or UNKNOWN if missing |
| `customer_since` | date |  | Date the customer joined the bank |
| `tenure_days` | integer |  | Days between joining and the as-of date |

## dim_account

Bank accounts, one row per account

| Column | Type | Key | Description |
|---|---|---|---|
| `account_key` | integer | PK | Surrogate key |
| `account_id` | text |  | Account ID in the core banking system |
| `customer_key` | integer | FK → dim_customer | Owner of the account (dim_customer) |
| `account_type` | text |  | CHECKING, SAVINGS or BUSINESS_CHECKING |
| `status` | text |  | ACTIVE, DORMANT or CLOSED at the as-of date |
| `open_date` | date |  | Date the account was opened |
| `close_date` | date |  | Date the account was closed, if closed |
| `account_age_days` | integer |  | Days between opening and the as-of date |
| `opening_balance` | numeric |  | Balance at the as-of date, before the transactions in this data |
| `has_active_card` | smallint |  | 1 if the account has an active debit card |
| `is_confirmed_mule` | smallint |  | 1 if investigators confirmed this as a money mule account. Known only FROM mule_confirmed_date: never use it as a model feature for earlier transactions |
| `mule_confirmed_date` | date |  | Date the mule finding was recorded |

## dim_merchant

Merchants. Key -1 means "no merchant" (transfers, ATM withdrawals, salaries)

| Column | Type | Key | Description |
|---|---|---|---|
| `merchant_key` | integer | PK | Surrogate key; -1 = no merchant |
| `merchant_id` | text |  | Merchant ID from the card network |
| `merchant_name` | text |  | Trading name |
| `category` | text |  | Business category, e.g. GROCERY, ELECTRONICS |
| `mcc` | integer |  | Merchant Category Code (ISO 18245) |
| `channel` | text |  | IN_STORE or ONLINE |
| `country` | text |  | Country of the merchant (ISO 3166 alpha-2) |
| `is_foreign` | integer |  | 1 if the merchant is outside the bank's home country |
| `risk_tier` | text |  | Fraud risk of the category: LOW, MEDIUM or HIGH |

## fact_transactions

One row per transaction, with the fraud label as known to the bank

| Column | Type | Key | Description |
|---|---|---|---|
| `txn_id` | text | PK | Transaction ID from the payments hub |
| `date_key` | integer | FK → dim_date | Transaction date (dim_date) |
| `txn_timestamp` | timestamp without time zone |  | Date and time of the transaction |
| `hour_of_day` | smallint |  | Hour of the transaction, 0-23 |
| `account_key` | integer | FK → dim_account | Account (dim_account) |
| `customer_key` | integer | FK → dim_customer | Account owner (dim_customer) |
| `merchant_key` | integer | FK → dim_merchant | Merchant (dim_merchant); -1 = no merchant |
| `txn_type` | text |  | CARD_PURCHASE, TRANSFER_OUT, TRANSFER_IN, BILL_PAYMENT, ATM_WITHDRAWAL, SALARY or BUSINESS_INCOME |
| `direction` | text |  | DR = money out, CR = money in |
| `channel` | text |  | POS, ECOM, ATM, MOBILE, WEB, BRANCH, NETWORK or INTERNAL |
| `amount` | numeric |  | Amount in USD, always positive |
| `signed_amount` | numeric |  | Amount with sign: negative for money out, positive for money in |
| `balance_before` | numeric |  | Account balance before the transaction |
| `balance_after` | numeric |  | Account balance after the transaction (unchanged if declined) |
| `status` | text |  | SUCCESS or DECLINED |
| `is_declined` | smallint |  | 1 if the transaction was declined |
| `counterparty_account` | text |  | Other party of a transfer or salary payment |
| `counterparty_bank` | text |  | Bank of the other party |
| `is_internal_transfer` | smallint |  | 1 if the other party banks with us |
| `device_id` | text |  | Device used for mobile or web transactions |
| `device_known` | smallint |  | Mobile/web only: 1 if the device is registered to this customer, 0 if not; NULL for other channels |
| `arrived_late` | smallint |  | 1 if the record arrived in a later day's file than its transaction date |
| `fraud_label` | smallint |  | 1 = confirmed fraud according to the bank's investigations. 0 = not confirmed (genuine, or fraud not yet reported) |
| `claim_status` | text |  | CONFIRMED, OPEN or REJECTED if a claim exists; NULL if nobody reported it |
| `first_reported_date` | date |  | Date the bank first heard about a problem with this transaction |
| `confirmed_date` | date |  | Date fraud was confirmed. A model may only learn this label from this date onwards |

## fact_logins

One row per digital banking login attempt

| Column | Type | Key | Description |
|---|---|---|---|
| `event_id` | text | PK | Login event ID |
| `date_key` | integer | FK → dim_date | Login date (dim_date) |
| `event_timestamp` | timestamp without time zone |  | Date and time of the attempt |
| `hour_of_day` | smallint |  | Hour of the attempt, 0-23 |
| `customer_key` | integer | FK → dim_customer | Customer whose account was accessed (dim_customer) |
| `session_id` | text |  | Groups the attempts of one login session |
| `device_id` | text |  | Device fingerprint; NULL if missing in the source |
| `device_known` | smallint |  | 1 if the device is registered to this customer, 0 if not; NULL if the device is missing |
| `channel` | text |  | MOBILE or WEB |
| `ip_address` | text |  | IP address. Personal data in a real bank: restrict access |
| `ip_country` | text |  | Country of the IP address (ISO 3166 alpha-2) |
| `is_foreign_ip` | smallint |  | 1 if the IP is outside the bank's home country |
| `result` | text |  | SUCCESS or FAILED |
| `is_failed` | smallint |  | 1 if the attempt failed |
| `failure_reason` | text |  | WRONG_PASSWORD or OTP_FAILED for failed attempts |

## fact_fraud_claims

Fraud claims and investigation outcomes: the bank's own knowledge of fraud

| Column | Type | Key | Description |
|---|---|---|---|
| `report_id` | text | PK | Claim (report) ID from the fraud operations system |
| `bank_case_ref` | text |  | Investigation case; one case can cover several transactions |
| `reported_date_key` | integer | FK → dim_date | Date the claim was received (dim_date) |
| `reported_date` | date |  | Date the claim was received |
| `resolved_date` | date |  | Date the investigation finished; NULL while still open |
| `account_key` | integer | FK → dim_account | Account the claim is about (dim_account) |
| `customer_key` | integer | FK → dim_customer | Customer the claim is about (dim_customer) |
| `txn_id` | text |  | Transaction the claim is about |
| `claim_type` | text |  | UNAUTHORISED_TRANSFER, SCAM_PAYMENT, UNAUTHORISED_CARD or MULE_ACCOUNT |
| `report_channel` | text |  | How the claim arrived: CALL_CENTRE, MOBILE_APP, BRANCH, CHARGEBACK or INVESTIGATION |
| `amount` | numeric |  | Amount of the transaction claimed |
| `claim_status` | text |  | CONFIRMED, REJECTED or OPEN (not yet investigated) |
| `days_to_report` | integer |  | Days between the transaction and the claim (reporting delay) |
| `days_to_resolve` | integer |  | Days the investigation took |
