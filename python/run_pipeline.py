"""
Run the data pipeline end to end, with one command:

    python3 python/run_pipeline.py

landing files -> raw (ingest) -> staging (clean, repair, reject) -> data quality report
"""
import logging
import time
from pathlib import Path

import pandas as pd

from build_staging import build_staging, staging_summary
from dq_report import load_truth, write_report
from generate_customers import load_config
from ingest import ingest
from warehouse import Warehouse

logger = logging.getLogger("run_pipeline")


def main():
    started = time.perf_counter()
    cfg = load_config()
    warehouse = Warehouse()
    warehouse.apply_sql_files("sql/init")

    logger.info("Step 1/3: ingest landing files into raw")
    results = ingest(warehouse, Path(cfg["paths"]["landing"]))
    logger.info("  %s", dict(results))

    logger.info("Step 2/3: build staging")
    build_staging(warehouse)
    logger.info("  %s", staging_summary(warehouse)["actions"])

    logger.info("Step 3/3: data quality report")
    planted = pd.read_csv(Path(cfg["paths"]["ground_truth"]) / "dq_issues.csv", dtype=str)
    report = write_report(warehouse, planted, load_truth(cfg))
    rows = report["rows"]
    repairs = report["repairs"]
    logger.info("  recall %.1f%%, %s false positives, repairs correct %s of %s -> %s",
                100 * rows["detected"].sum() / rows["planted"].sum(), rows["false_positives"].sum(),
                f"{repairs['correct'].sum():,}", f"{repairs['repaired'].sum():,}", report["path"])
    logger.info("Pipeline complete in %.1fs", time.perf_counter() - started)


if __name__ == "__main__":
    main()
