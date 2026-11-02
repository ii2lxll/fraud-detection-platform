"""
Build the mart (star schema) from staging: the business-ready layer for analysis, SAS and models.

All SQL files in sql/mart/ run in ONE transaction, so a failed build never leaves a half-built mart.

    python3 python/build_mart.py
"""
import logging

from generate_customers import load_config
from warehouse import Warehouse

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

MART_SQL = "sql/mart"
MART_TABLES = ["dim_date", "dim_customer", "dim_account", "dim_merchant",
               "fact_transactions", "fact_logins", "fact_fraud_claims"]


def mart_settings(cfg: dict) -> dict:
    """Values the mart SQL reads with current_setting(...): taken from the config, never hardcoded in SQL."""
    return {
        "mart.as_of_date": cfg["project"]["snapshot_date"],
        "mart.home_country": cfg["project"]["home_country"],
        "mart.bank_code": cfg["simulation"]["bank_code"],
    }


def build_mart(warehouse: Warehouse, cfg: dict, sql_folder: str = MART_SQL) -> None:
    warehouse.run_sql_folder(sql_folder, settings=mart_settings(cfg))


def mart_summary(warehouse: Warehouse) -> dict:
    with warehouse.transaction() as tx:
        rows = {t: int(tx.query(f'SELECT count(*) FROM mart."{t}"')[0][0]) for t in MART_TABLES}
        labels = tx.query("""SELECT count(*) FILTER (WHERE fraud_label = 1),
                                    count(*) FILTER (WHERE claim_status = 'OPEN')
                             FROM mart.fact_transactions""")[0]
    return {"rows": rows, "confirmed_fraud": int(labels[0]), "open_claims": int(labels[1])}


def main():
    cfg = load_config()
    warehouse = Warehouse()
    logger.info("Building mart from staging...")
    build_mart(warehouse, cfg)
    summary = mart_summary(warehouse)
    for table, n in summary["rows"].items():
        logger.info("  mart.%-18s %s rows", table, f"{n:,}")
    logger.info("Transactions labelled as confirmed fraud: %s (%s more under investigation)",
                summary["confirmed_fraud"], summary["open_claims"])


if __name__ == "__main__":
    main()
