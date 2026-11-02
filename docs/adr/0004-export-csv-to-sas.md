# 0004: The mart reaches SAS OnDemand as CSV files with a manifest

**Status:** Accepted · **Date:** 2026-11-03

## Context
SAS is the main modelling platform for this project, using the free SAS OnDemand for Academics, which runs in SAS's cloud. It cannot connect to the PostgreSQL database running on a personal computer. A licensed SAS installation inside a bank would normally connect to the warehouse directly (for example with SAS/ACCESS).

## Decision
- `python/export_mart.py` exports every mart table to CSV in `data/exports/`, plus `export_manifest.json` with row counts and SHA-256 checksums.
- Table and column names are checked to be valid SAS names (at most 32 characters) before export.
- Flags are stored as 0/1 integers and dates in ISO format (`YYYY-MM-DD`), so SAS reads them without custom formats.

## Consequences
- The SAS side works on a snapshot, not live data. After the pipeline runs again, the files must be re-exported and re-uploaded.
- The manifest lets the SAS programs confirm they are using a complete, matching set of files.
- Moving to a licensed SAS installation would replace the export with a direct connection, without changing the mart.
