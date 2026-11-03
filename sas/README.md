# SAS programs

The analytics and modelling layer of the project, written for **SAS OnDemand for Academics** (free, runs in the browser).

SAS OnDemand cannot connect to the project's PostgreSQL database, so the mart is exported to CSV files and uploaded (see [ADR 0004](../docs/adr/0004-export-csv-to-sas.md)).

## One-time setup

1. Run the pipeline locally: `python3 python/run_pipeline.py`. This creates the CSV files in `data/exports/`.
2. Log in to SAS OnDemand and open **SAS Studio**.
3. Under **Server Files and Folders → Files (Home)**, create the folder `fraud_platform`, and inside it a folder `exports`.
4. Upload the 7 CSV files **and** `export_manifest.json` from `data/exports/` into `fraud_platform/exports`.
5. Upload the `.sas` programs from this folder into `fraud_platform` (or open them from your computer).

## Run order

| Program | What it does |
|---|---|
| `00_setup.sas` | Paths and the `FRAUD` library. Run it first in every session. |
| `01_import_mart.sas` | Imports the 7 tables with explicit column definitions, then checks each one against the export manifest |
| `02_eda.sas` | Exploratory analysis: device signal, merchant risk, timing, amounts, scam targeting, reporting delay, label maturity. Saves `reports/eda_report.pdf`. |

## Reading the SAS log

After every run, check the log:
- `NOTE:` lines are information, and normal.
- `WARNING:` lines mean something unexpected happened: read them.
- `ERROR:` lines mean a step failed. Fix the first error first; later ones are often caused by it.

`01_import_mart.sas` ends with either `NOTE: Import check passed` or an `ERROR:` line naming the table that doesn't match.
