# 0002: Staging is rebuilt from raw in one transaction, with every problem recorded

**Status:** Accepted · **Date:** 2026-11-02

## Context
Cleaning rules change over time. Updating staging row by row would make results depend on run history, and a half-finished run would leave staging inconsistent. Analysts also need to know what was changed and why, not just receive "clean" data.

## Decision
- Staging is dropped and rebuilt from raw on every run, with all SQL in `sql/staging/` executed in **one transaction**.
- Every problem is written to `staging.dq_findings` with one of four actions:
  - **REMOVED:** exact duplicates
  - **REPAIRED:** reformatted, or recovered from a trusted field (for example, a missing amount rebuilt from `balance_after − balance_before`)
  - **FLAGGED:** the row is kept but the bad value is set to NULL
  - **REJECTED:** the row cannot be trusted and is left out, with the reason
- Reference data (customers, merchants) is never rejected for one bad attribute, because that would orphan their accounts and transactions.
- A data quality report measures detection against the planted answer key, and verifies repairs against the original values.

## Consequences
- Re-running always gives the same result; a failed run leaves the previous staging intact.
- A full rebuild takes about 25 seconds at this data size. At much larger volumes, an incremental approach would be needed.
