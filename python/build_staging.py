"""
Build the staging layer from raw: clean, type, de-duplicate, repair and reject.

All SQL files in sql/staging/ run in ONE transaction. If anything fails, the previous staging
tables stay exactly as they were. Staging is always rebuilt from raw, so it can be re-run any time.

    python3 python/build_staging.py
"""
import logging

from warehouse import Warehouse

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

STAGING_SQL = "sql/staging"
STAGING_TABLES = ["customers", "accounts", "cards", "merchants", "devices", "transactions", "logins", "fraud_reports"]


def build_staging(warehouse: Warehouse, sql_folder: str = STAGING_SQL) -> None:
    warehouse.run_sql_folder(sql_folder)


def staging_summary(warehouse: Warehouse) -> dict:
    with warehouse.transaction() as tx:
        rows = {t: int(tx.query(f'SELECT count(*) FROM staging."{t}"')[0][0]) for t in STAGING_TABLES}
        actions = dict(tx.query("SELECT action, count(*) FROM staging.dq_findings GROUP BY action ORDER BY action"))
    return {"rows": rows, "actions": {k: int(v) for k, v in actions.items()}}


def main():
    warehouse = Warehouse()
    logger.info("Building staging from raw...")
    build_staging(warehouse)
    summary = staging_summary(warehouse)
    for table, n in summary["rows"].items():
        logger.info("  staging.%-14s %s rows", table, f"{n:,}")
    logger.info("Data quality findings by action: %s", summary["actions"])


if __name__ == "__main__":
    main()
