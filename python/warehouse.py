"""
A thin layer between the pipeline and PostgreSQL.

The pipeline only ever does four things with the database, so they live here in one place:
    tx.execute(sql, params)          run a statement
    tx.query(sql, params)            run a query and get rows back
    tx.copy_rows(table, cols, rows)  bulk-load rows with COPY (far faster than INSERT for big files)
    warehouse.transaction()          group statements so they all succeed or all roll back

Parameters are written as :name in the SQL and passed separately, NEVER pasted into the string.
That is what protects against SQL injection.
"""
import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from db import get_engine

logger = logging.getLogger(__name__)

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def ident(name: str) -> str:
    """Quote a table or column name. Column names come from file headers, so we never trust them blindly."""
    if not IDENTIFIER.match(name):
        raise ValueError(f"Unsafe or invalid name: {name!r}")
    return f'"{name}"'


def table_ref(schema: str, table: str) -> str:
    return f"{ident(schema)}.{ident(table)}"


def split_sql(script: str) -> list[str]:
    """Split a .sql file into statements at semicolons, removing -- comments.
    Text inside 'single quotes' is left alone, so a ';' or '--' inside a string never splits a statement."""
    statements, current, i, in_string = [], [], 0, False
    while i < len(script):
        char = script[i]
        if in_string:
            current.append(char)
            if char == "'":
                if script[i + 1:i + 2] == "'":          # '' is an escaped quote inside a string
                    current.append("'")
                    i += 1
                else:
                    in_string = False
        elif char == "'":
            in_string = True
            current.append(char)
        elif script.startswith("--", i):               # comment: skip to the end of the line
            end = script.find("\n", i)
            i = len(script) if end == -1 else end
            continue
        elif char == ";":
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
        i += 1
    statements.append("".join(current))
    return [s.strip() for s in statements if s.strip()]


class Transaction:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: dict | None = None) -> None:
        self._conn.execute(text(sql), params or {})

    def query(self, sql: str, params: dict | None = None) -> list[tuple]:
        return [tuple(row) for row in self._conn.execute(text(sql), params or {})]

    def query_frame(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        """Run a query and return the result as a pandas DataFrame, with column names."""
        result = self._conn.execute(text(sql), params or {})
        return pd.DataFrame(result.fetchall(), columns=list(result.keys()))

    def copy_rows(self, table: str, columns: list[str], rows) -> int:
        """Stream rows into a table with PostgreSQL's COPY command, inside the current transaction."""
        column_list = ", ".join(ident(c) for c in columns)
        count = 0
        with self._conn.connection.driver_connection.cursor() as cur:
            with cur.copy(f"COPY {table} ({column_list}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
                    count += 1
        return count


class Warehouse:
    def __init__(self, engine=None):
        self.engine = engine or get_engine()

    @contextmanager
    def transaction(self):
        """Everything inside commits together at the end, or rolls back together if anything fails."""
        with self.engine.begin() as conn:
            yield Transaction(conn)

    def run_sql_folder(self, folder: str | Path, settings: dict | None = None) -> None:
        """Run every .sql file in a folder, in name order, ALL in one transaction (all or nothing).
        `settings` are made available to the SQL as current_setting('name') for this transaction only."""
        files = sorted(Path(folder).glob("*.sql"))
        if not files:
            raise FileNotFoundError(f"No .sql files in {folder}")
        with self.transaction() as tx:
            for name, value in (settings or {}).items():
                tx.execute("SELECT set_config(:name, :value, true)", {"name": name, "value": str(value)})
            for path in files:
                started = time.perf_counter()
                for statement in split_sql(path.read_text(encoding="utf-8")):
                    tx.execute(statement)
                logger.info("  %-26s %.1fs", path.name, time.perf_counter() - started)

    def apply_sql_files(self, folder: str | Path) -> list[str]:
        """Run every .sql file in a folder, in name order. Our scripts are safe to run repeatedly."""
        applied = []
        for path in sorted(Path(folder).glob("*.sql")):
            with self.transaction() as tx:
                for statement in split_sql(path.read_text(encoding="utf-8")):
                    tx.execute(statement)
            applied.append(path.name)
        return applied
