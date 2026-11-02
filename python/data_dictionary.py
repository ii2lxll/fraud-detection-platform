"""
Generate the data dictionary from the database itself.

Descriptions live next to the code that creates each table (COMMENT ON ... in sql/mart/*.sql),
so the documentation cannot drift away from the real tables. A test fails if any column is undocumented.

    python3 python/data_dictionary.py      ->  docs/data_dictionary.md
"""
import logging
from datetime import datetime
from pathlib import Path

from build_mart import MART_TABLES
from warehouse import Warehouse

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

DICTIONARY_PATH = Path("docs/data_dictionary.md")

COLUMNS_SQL = """
SELECT c.table_name, c.column_name, c.data_type,
       col_description((quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass,
                       c.ordinal_position) AS description
FROM information_schema.columns c
WHERE c.table_schema = :schema
ORDER BY c.table_name, c.ordinal_position
"""

TABLES_SQL = """
SELECT t.table_name,
       obj_description((quote_ident(t.table_schema) || '.' || quote_ident(t.table_name))::regclass, 'pg_class')
FROM information_schema.tables t
WHERE t.table_schema = :schema AND t.table_type = 'BASE TABLE'
"""


def describe(warehouse: Warehouse, schema: str = "mart"):
    with warehouse.transaction() as tx:
        columns = tx.query_frame(COLUMNS_SQL, {"schema": schema})
        tables = dict(tx.query(TABLES_SQL, {"schema": schema}))
        keys = tx.query("""SELECT tc.table_name, kcu.column_name, tc.constraint_type, ccu.table_name
                           FROM information_schema.table_constraints tc
                           JOIN information_schema.key_column_usage kcu
                             ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
                           JOIN information_schema.constraint_column_usage ccu
                             ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
                           WHERE tc.table_schema = :schema
                             AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')""", {"schema": schema})
        counts = {t: int(tx.query(f'SELECT count(*) FROM {schema}."{t}"')[0][0]) for t in tables}
    return columns, tables, keys, counts


def undocumented(warehouse: Warehouse, schema: str = "mart") -> list[str]:
    columns, tables, _, _ = describe(warehouse, schema)
    missing = [f"{t} (table)" for t, d in tables.items() if not d]
    missing += [f"{r.table_name}.{r.column_name}" for r in columns.itertuples(index=False) if not r.description]
    return missing


def render(columns, tables, keys, counts) -> str:
    key_note = {}
    for table, column, kind, target in keys:
        note = "PK" if kind == "PRIMARY KEY" else f"FK → {target}"
        key_note[(table, column)] = note if (table, column) not in key_note else key_note[(table, column)]
    order = [t for t in MART_TABLES if t in tables] + sorted(set(tables) - set(MART_TABLES))

    lines = ["# Data Dictionary: mart schema", "",
             f"_Generated from the database on {datetime.now():%Y-%m-%d}. "
             "Do not edit by hand: change the `COMMENT ON` statements in `sql/mart/` and re-run "
             "`python3 python/data_dictionary.py`._", "",
             "The mart is a star schema: **fact tables** record events (transactions, logins, fraud claims) and "
             "join to **dimension tables** (customer, account, merchant, date) through integer keys.", "",
             "| Table | Rows | Description |", "|---|---:|---|"]
    lines += [f"| [{t}](#{t}) | {counts[t]:,} | {tables[t]} |" for t in order]
    for t in order:
        lines += ["", f"## {t}", "", tables[t] or "", "", "| Column | Type | Key | Description |", "|---|---|---|---|"]
        for r in columns[columns["table_name"] == t].itertuples(index=False):
            lines.append(f"| `{r.column_name}` | {r.data_type} | {key_note.get((t, r.column_name), '')} "
                         f"| {r.description or '**UNDOCUMENTED**'} |")
    return "\n".join(lines) + "\n"


def write_dictionary(warehouse: Warehouse, path: Path = DICTIONARY_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(*describe(warehouse)), encoding="utf-8")
    return path


def main():
    warehouse = Warehouse()
    path = write_dictionary(warehouse)
    missing = undocumented(warehouse)
    if missing:
        logger.warning("Undocumented: %s", missing)
    logger.info("Data dictionary written to %s", path)


if __name__ == "__main__":
    main()
