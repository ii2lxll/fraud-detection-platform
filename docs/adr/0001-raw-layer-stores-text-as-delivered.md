# 0001: The raw layer stores every value as text, exactly as delivered

**Status:** Accepted · **Date:** 2026-11-02

## Context
Source files arrive with problems: amounts like `"$1,234.56"`, three different timestamp formats, unknown codes, and columns that get renamed or added without warning. If the loader converted types while loading, one bad value would fail the whole file, or be silently changed and lose the evidence of what was delivered.

## Decision
- Every raw column is `TEXT`, and values are stored exactly as delivered. Duplicates are kept too.
- Each row gets lineage columns: `_load_id` (link to `audit.file_load_log`), `_row_number` and `_ingested_at`.
- New columns in a file are added to the raw table automatically and recorded in `audit.schema_change_log`.
- Files are checked against their manifest (row count and SHA-256) before loading; a mismatch rejects the whole file.

## Consequences
- Loading never fails on bad values, and the original data is always available to re-process if a cleaning rule was wrong.
- Any value in staging or the mart can be traced back to the exact file and row it came from.
- All type conversion and validation moves to staging (see 0002).
- Raw is not for analysis: it contains duplicates and unparsed text.
