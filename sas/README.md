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
| `03_features.sas` | Feature engineering: 18 point-in-time fraud features per transaction in `FRAUD.TXN_FEATURES`. Saves `reports/feature_report.pdf` and `reports/feature_totals_sas.csv`. |

## Validating the features: the parallel run

The same 18 feature definitions are implemented independently in Python ([`python/feature_reference.py`](../python/feature_reference.py)), which is itself tested against a slow brute-force version. To confirm the SAS results:

1. Run `03_features.sas`, then download `reports/feature_totals_sas.csv` from SAS Studio into the project's `reports/` folder
2. Run `python3 python/feature_reference.py --compare reports/feature_totals_sas.csv`
3. Every feature must show `MATCH`. Any `DIFFERENT` means the two implementations disagree, and the definition needs checking.

## Reading the SAS log

After every run, check the log:
- `NOTE:` lines are information, and normal.
- `WARNING:` lines mean something unexpected happened: read them.
- `ERROR:` lines mean a step failed. Fix the first error first; later ones are often caused by it.

`01_import_mart.sas` ends with either `NOTE: Import check passed` or an `ERROR:` line naming the table that doesn't match.
