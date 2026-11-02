"""
Check that Python can reach the database and that the warehouse layers exist.

    python3 python/check_db.py
"""
import logging
import sys

from sqlalchemy import text

from db import get_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

EXPECTED_SCHEMAS = {"raw", "staging", "mart", "audit"}


def main() -> int:
    engine = get_engine()
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar()
            schemas = set(conn.execute(text(
                "SELECT schema_name FROM information_schema.schemata")).scalars())
            tables = conn.execute(text(
                "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                "WHERE table_schema = ANY(:s) ORDER BY 1"), {"s": list(EXPECTED_SCHEMAS)}).scalars().all()
    except Exception as exc:                       # show a clear message instead of a long traceback
        logger.error("Could not connect to the database: %s", exc.__class__.__name__)
        logger.error("Is Docker Desktop running, and did you start the database with 'docker compose up -d'?")
        return 1

    logger.info("Connected: %s", version.split(",")[0])
    missing = EXPECTED_SCHEMAS - schemas
    if missing:
        logger.error("Missing schemas: %s (the init script did not run)", sorted(missing))
        return 1
    logger.info("Schemas present: %s", sorted(EXPECTED_SCHEMAS))
    logger.info("Tables: %s", tables)
    return 0


if __name__ == "__main__":
    sys.exit(main())
