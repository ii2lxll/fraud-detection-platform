# Data Quality Report

_Generated 2026-11-03 00:04. Compares what the pipeline found with the problems deliberately planted in the delivered files._

## Summary

- **Row-level problems:** 14,176 of 14,176 detected (**100.0% recall**), 0 false positives
- **Transaction repairs verified:** 6,176 of 6,176 match the original value (100.0%)
- **File-level problems:** 5 of 5 detected
- **Unexpected findings:** 0

## Row-level problems

| Feed | Planted problem | Pipeline finding | Planted | Detected | Recall | False positives | How it was handled |
|---|---|---|---:|---:|---:|---:|---|
| transactions | DUPLICATE | DUPLICATE | 1,342 | 1,342 | 100% | 0 | REMOVED 1342 |
| transactions | MISSING_AMOUNT | MISSING_AMOUNT | 456 | 456 | 100% | 0 | REPAIRED 448, REJECTED 8 |
| transactions | AMOUNT_AS_TEXT | AMOUNT_TEXT_FORMAT | 899 | 899 | 100% | 0 | REPAIRED 899 |
| transactions | MESSY_TXN_TYPE | NONSTANDARD_TXN_TYPE | 2,333 | 2,333 | 100% | 0 | REPAIRED 2333 |
| transactions | US_DATE_FORMAT | NONSTANDARD_TIMESTAMP | 1,381 | 1,381 | 100% | 0 | REPAIRED 1381 |
| transactions | ORPHAN_ACCOUNT | UNKNOWN_ACCOUNT | 220 | 220 | 100% | 0 | REJECTED 220 |
| transactions | NEGATIVE_AMOUNT | NEGATIVE_AMOUNT | 225 | 225 | 100% | 0 | REPAIRED 217, REJECTED 8 |
| transactions | CURRENCY_VARIANT | NONSTANDARD_CURRENCY | 898 | 898 | 100% | 0 | REPAIRED 898 |
| transactions | LATE_ARRIVING_ROW | LATE_ARRIVING | 2,194 | 2,194 | 100% | 0 | FLAGGED 2194 |
| logins | DUPLICATE | DUPLICATE | 443 | 443 | 100% | 0 | REMOVED 443 |
| logins | EPOCH_TIMESTAMP | NONSTANDARD_TIMESTAMP | 2,007 | 2,007 | 100% | 0 | REPAIRED 2007 |
| logins | MISSING_DEVICE | MISSING_DEVICE | 609 | 609 | 100% | 0 | FLAGGED 577, REPAIRED 32 |
| logins | LOWERCASE_COUNTRY | NONSTANDARD_COUNTRY | 1,063 | 1,063 | 100% | 0 | REPAIRED 1063 |
| fraud_reports | DUPLICATE | DUPLICATE | 19 | 19 | 100% | 0 | REMOVED 19 |
| fraud_reports | MISSING_AMOUNT | MISSING_AMOUNT | 8 | 8 | 100% | 0 | REPAIRED 8 |
| fraud_reports | SLASH_DATE_FORMAT | NONSTANDARD_DATE | 18 | 18 | 100% | 0 | REPAIRED 18 |
| customers | DUPLICATE_CUSTOMER | DUPLICATE_CUSTOMER | 12 | 12 | 100% | 0 | REMOVED 12 |
| customers | MISSING_STATE | MISSING_STATE | 31 | 31 | 100% | 0 | FLAGGED 31 |
| customers | INVALID_BIRTH_DATE | INVALID_BIRTH_DATE | 4 | 4 | 100% | 0 | FLAGGED 4 |
| customers | INVALID_EMAIL | INVALID_EMAIL | 10 | 10 | 100% | 0 | FLAGGED 10 |
| merchants | MISSING_MCC | MISSING_MCC | 4 | 4 | 100% | 0 | REPAIRED 4 |

## Were the transaction repairs correct?

Each repaired value compared with the original, untouched value from the source system.

| Repair | Repaired | Correct | Accuracy |
|---|---:|---:|---:|
| MISSING_AMOUNT | 448 | 448 | 100% |
| NEGATIVE_AMOUNT | 217 | 217 | 100% |
| AMOUNT_TEXT_FORMAT | 899 | 899 | 100% |
| NONSTANDARD_TXN_TYPE | 2,333 | 2,333 | 100% |
| NONSTANDARD_CURRENCY | 898 | 898 | 100% |
| NONSTANDARD_TIMESTAMP | 1,381 | 1,381 | 100% |

_Timestamps delivered in US format (`06/01/2026 08:13`) carry no seconds, so they can only be restored to the minute._

## File-level problems

| Problem | File | Detected | Evidence |
|---|---|---|---|
| Truncated file | transactions_20260620.csv | yes | rejected by manifest check |
| Re-sent file | transactions_20260620_resend.csv | yes | loaded instead of the truncated copy |
| Late file | transactions_20260710.csv | yes | recorded as delivered 2 days late |
| Renamed column | transactions_20260731.csv | yes | 'device_fingerprint' detected in transactions/transactions_20260731.csv |
| New column | transactions_20260814.csv | yes | 'terminal_id' detected in transactions/transactions_20260814.csv |

## Staging tables

| Table | Rows |
|---|---:|
| staging.customers | 3,000 |
| staging.accounts | 4,407 |
| staging.cards | 3,000 |
| staging.merchants | 500 |
| staging.devices | 3,713 |
| staging.transactions | 456,477 |
| staging.logins | 207,229 |
| staging.fraud_reports | 902 |

**Actions taken:** FLAGGED 2,816, REJECTED 236, REMOVED 1,816, REPAIRED 9,308

- **REMOVED:** exact duplicates dropped
- **REPAIRED:** reformatted, or recovered from a trusted field (balance change, related record)
- **FLAGGED:** kept, with the bad value set to NULL
- **REJECTED:** could not be trusted and left out of staging (see `staging.v_rejected_transactions`)
