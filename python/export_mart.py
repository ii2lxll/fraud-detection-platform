"""
Export the mart to CSV files for SAS OnDemand for Academics.

SAS OnDemand runs in the cloud and cannot connect to the PostgreSQL database on your computer,
so the mart is exported as files and uploaded (see docs/adr/0004-export-csv-to-sas.md).

Like the source systems, the export comes with a manifest (row counts + SHA-256 checksums),
so the SAS side can confirm it received complete files.

    python3 python/export_mart.py      ->  data/exports/*.csv + export_manifest.json
"""
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from build_mart import MART_TABLES
from warehouse import Warehouse

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

EXPORT_DIR = Path("data/exports")
SAS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")      # SAS names: max 32 characters, letters/digits/_


def check_sas_names(table: str, columns: list[str]) -> None:
    bad = [c for c in [table] + columns if not SAS_NAME.match(c)]
    if bad:
        raise ValueError(f"Not valid SAS names in {table}: {bad}")


def export_mart(warehouse: Warehouse, out_dir: Path = EXPORT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"exported_at": datetime.now().isoformat(timespec="seconds"), "files": []}
    for table in MART_TABLES:
        with warehouse.transaction() as tx:
            df = tx.query_frame(f'SELECT * FROM mart."{table}" ORDER BY 1')
            integer_columns = [r[0] for r in tx.query(
                """SELECT column_name FROM information_schema.columns WHERE table_schema = 'mart'
                   AND table_name = :t AND data_type IN ('smallint', 'integer', 'bigint')""", {"t": table})]
        check_sas_names(table, list(df.columns))
        for column in integer_columns:            # keep 1 as "1", not "1.0", when a column has blanks
            df[column] = df[column].astype("Int64")
        path = out_dir / f"{table}.csv"
        df.to_csv(path, index=False)
        manifest["files"].append({"file": path.name, "rows": len(df), "columns": len(df.columns),
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        logger.info("  %-24s %9s rows  %5.1f MB", path.name, f"{len(df):,}", path.stat().st_size / 1e6)
    (out_dir / "export_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    logger.info("Exporting the mart to %s ...", EXPORT_DIR)
    export_mart(Warehouse())


if __name__ == "__main__":
    main()
