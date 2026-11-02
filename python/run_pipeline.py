"""
Run the data pipeline end to end, with one command:

    python3 python/run_pipeline.py

landing files -> raw -> staging -> data quality report -> mart -> data dictionary -> SAS export
"""
import logging
import time
from pathlib import Path

import pandas as pd

from build_mart import build_mart, mart_summary
from build_staging import build_staging, staging_summary
from data_dictionary import undocumented, write_dictionary
from dq_report import load_truth, write_report
from export_mart import EXPORT_DIR, export_mart
from generate_customers import load_config
from ingest import ingest
from warehouse import Warehouse

logger = logging.getLogger("run_pipeline")


def main():
    started = time.perf_counter()
    cfg = load_config()
    warehouse = Warehouse()
    warehouse.apply_sql_files("sql/init")

    logger.info("Step 1/6: ingest landing files into raw")
    logger.info("  %s", dict(ingest(warehouse, Path(cfg["paths"]["landing"]))))

    logger.info("Step 2/6: build staging")
    build_staging(warehouse)
    logger.info("  findings: %s", staging_summary(warehouse)["actions"])

    logger.info("Step 3/6: data quality report")
    planted = pd.read_csv(Path(cfg["paths"]["ground_truth"]) / "dq_issues.csv", dtype=str)
    report = write_report(warehouse, planted, load_truth(cfg))
    rows, repairs = report["rows"], report["repairs"]
    logger.info("  recall %.1f%%, %s false positives, repairs correct %s of %s -> %s",
                100 * rows["detected"].sum() / rows["planted"].sum(), rows["false_positives"].sum(),
                f"{repairs['correct'].sum():,}", f"{repairs['repaired'].sum():,}", report["path"])

    logger.info("Step 4/6: build mart")
    build_mart(warehouse, cfg)
    summary = mart_summary(warehouse)
    logger.info("  %s transactions, %s confirmed fraud, %s claims still open",
                f"{summary['rows']['fact_transactions']:,}", summary["confirmed_fraud"], summary["open_claims"])

    logger.info("Step 5/6: data dictionary")
    path = write_dictionary(warehouse)
    missing = undocumented(warehouse)
    logger.info("  %s%s", path, f" (UNDOCUMENTED: {missing})" if missing else "")

    logger.info("Step 6/6: export mart for SAS")
    export_mart(warehouse)
    logger.info("  files in %s", EXPORT_DIR)

    logger.info("Pipeline complete in %.1fs", time.perf_counter() - started)


if __name__ == "__main__":
    main()
