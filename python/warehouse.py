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
import re
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text

from db import get_engine

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def ident(name: str) -> str:
    """Quote a table or column name. Column names come from file headers, so we never trust them blindly."""
    if not IDENTIFIER.match(name):
        raise ValueError(f"Unsafe or invalid name: {name!r}")
    return f'"{name}"'


def table_ref(schema: str, table: str) -> str:
    return f"{ident(schema)}.{ident(table)}"


def split_sql(script: str) -> list[str]:
    """Split a .sql file into statements (our scripts use -- comments and no semicolons inside text)."""
    without_comments = "\n".join(line.split("--", 1)[0] for line in script.splitlines())
    return [s.strip() for s in without_comments.split(";") if s.strip()]


class Transaction:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: dict | None = None) -> None:
        self._conn.execute(text(sql), params or {})

    def query(self, sql: str, params: dict | None = None) -> list[tuple]:
        return [tuple(row) for row in self._conn.execute(text(sql), params or {})]

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

    def apply_sql_files(self, folder: str | Path) -> list[str]:
        """Run every .sql file in a folder, in name order. Our scripts are safe to run repeatedly."""
        applied = []
        for path in sorted(Path(folder).glob("*.sql")):
            with self.transaction() as tx:
                for statement in split_sql(path.read_text(encoding="utf-8")):
                    tx.execute(statement)
            applied.append(path.name)
        return applied
