"""
Ingestion: load landing files into the raw layer, exactly as delivered.

For every delivery day (in date order), read its manifest and, for each file listed:
  1. Already loaded (same name AND same checksum)?             -> SKIPPED_DUPLICATE
  2. Checksum or row count differs from the manifest?          -> REJECTED_MANIFEST (nothing loaded)
  3. Otherwise load it in ONE transaction: record the load, create or extend the raw table,
     copy every row. If anything fails, all of it rolls back    -> LOADED or FAILED

Raw tables keep every value as TEXT, exactly as delivered: "$1,234.56" stays "$1,234.56".
Cleaning is the staging layer's job. Each raw row also gets three metadata columns:
    _load_id      which load (row in audit.file_load_log) brought this row in
    _row_number   its position in the file
    _ingested_at  when it was loaded

Usage:
    python3 python/ingest.py                      # every delivery
    python3 python/ingest.py --date 2026-06-20    # one delivery day
"""
import argparse
import csv
import hashlib
import io
import json
import logging
from collections import Counter
from datetime import date
from pathlib import Path

from generate_customers import load_config
from warehouse import Warehouse, ident, table_ref

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

RAW, AUDIT = "raw", "audit"
METADATA_COLUMNS = ["_load_id", "_row_number"]          # _ingested_at is filled in by the database


# ---------------------------------------------------------------- reading files
def read_manifests(landing: Path, only_date: date | None = None) -> list[dict]:
    manifests = []
    for path in sorted((landing / "manifests").glob("manifest_*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["delivery_date"] = date.fromisoformat(manifest["delivery_date"])
        if only_date is None or manifest["delivery_date"] == only_date:
            manifests.append(manifest)
    return manifests


def parse_file(path: Path, content: str) -> tuple[list[str], list[list]]:
    """Return (column names, rows). Values stay text; empty CSV cells and JSON nulls become NULL."""
    if path.suffix == ".jsonl":
        events = [json.loads(line) for line in content.splitlines() if line.strip()]
        header = list(dict.fromkeys(key for event in events for key in event))   # every key, first-seen order
        rows = [[None if e.get(k) is None else str(e[k]) for k in header] for e in events]
        return header, rows

    reader = csv.reader(io.StringIO(content, newline=""))
    header = next(reader)
    rows = []
    for number, row in enumerate(reader, start=2):
        if len(row) != len(header):
            raise ValueError(f"line {number} has {len(row)} fields, header has {len(header)}")
        rows.append([value if value != "" else None for value in row])
    return header, rows


def raw_table_for(entry: dict) -> str:
    """transactions/... -> transactions;  reference/customers_20260601.csv -> customers"""
    if entry["feed"] == "reference":
        return Path(entry["file"]).stem.rsplit("_", 1)[0]
    return entry["feed"]


# ---------------------------------------------------------------- audit log
def log_attempt(tx, entry: dict, delivery_date: date, status: str, actual_sha: str | None,
                loaded_rows: int | None = None, message: str | None = None, finished: bool = True) -> int:
    rows = tx.query(
        f"""INSERT INTO {table_ref(AUDIT, 'file_load_log')}
                (feed, file_name, delivery_date, expected_rows, loaded_rows,
                 expected_sha256, actual_sha256, status, message, finished_at)
            VALUES (:feed, :file, :delivery_date, :expected_rows, :loaded_rows,
                    :expected_sha, :actual_sha, :status, :message, CASE WHEN :finished THEN now() END)
            RETURNING load_id""",
        {"feed": entry["feed"], "file": entry["file"], "delivery_date": delivery_date,
         "expected_rows": entry["rows"], "loaded_rows": loaded_rows, "expected_sha": entry["sha256"],
         "actual_sha": actual_sha, "status": status, "message": message, "finished": finished},
    )
    return int(rows[0][0])


def already_loaded(tx, entry: dict, actual_sha: str) -> bool:
    rows = tx.query(
        f"""SELECT 1 FROM {table_ref(AUDIT, 'file_load_log')}
            WHERE feed = :feed AND file_name = :file AND actual_sha256 = :sha AND status = 'LOADED'""",
        {"feed": entry["feed"], "file": entry["file"], "sha": actual_sha},
    )
    return bool(rows)


# ---------------------------------------------------------------- schema handling
def existing_columns(tx, table: str) -> list[str]:
    rows = tx.query(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = :schema AND table_name = :table ORDER BY ordinal_position""",
        {"schema": RAW, "table": table},
    )
    return [r[0] for r in rows]


def log_schema_change(tx, table: str, change: str, column: str | None, file: str, load_id: int):
    tx.execute(
        f"""INSERT INTO {table_ref(AUDIT, 'schema_change_log')}
                (table_name, change_type, column_name, first_seen_file, load_id)
            VALUES (:table, :change, :column, :file, :load_id)""",
        {"table": f"{RAW}.{table}", "change": change, "column": column, "file": file, "load_id": load_id},
    )


def ensure_table(tx, table: str, header: list[str], file: str, load_id: int) -> list[str]:
    """Create the raw table if needed, and add any column this file has that the table doesn't (schema drift)."""
    current = existing_columns(tx, table)
    if not current:
        columns = ",\n".join(f"    {ident(c)} TEXT" for c in header)
        tx.execute(f"""CREATE TABLE {table_ref(RAW, table)} (
    "_load_id"     BIGINT      NOT NULL REFERENCES {table_ref(AUDIT, 'file_load_log')} (load_id),
    "_row_number"  INTEGER     NOT NULL,
    "_ingested_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
{columns}
)""")
        log_schema_change(tx, table, "TABLE_CREATED", None, file, load_id)
        return []

    added = [c for c in header if c not in current]
    for column in added:
        tx.execute(f"ALTER TABLE {table_ref(RAW, table)} ADD COLUMN {ident(column)} TEXT")
        log_schema_change(tx, table, "COLUMN_ADDED", column, file, load_id)
    return added


# ---------------------------------------------------------------- loading one file
def load_file(warehouse: Warehouse, landing: Path, entry: dict, delivery_date: date) -> str:
    path = landing / entry["file"]
    if not path.exists():
        with warehouse.transaction() as tx:
            log_attempt(tx, entry, delivery_date, "FAILED", None, message="listed in manifest but not found")
        logger.error("MISSING   %s", entry["file"])
        return "FAILED"

    raw_bytes = path.read_bytes()
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()

    with warehouse.transaction() as tx:
        if already_loaded(tx, entry, actual_sha):
            log_attempt(tx, entry, delivery_date, "SKIPPED_DUPLICATE", actual_sha, message="already loaded")
            return "SKIPPED_DUPLICATE"

        if actual_sha != entry["sha256"]:
            content = raw_bytes.decode("utf-8", errors="replace")
            data_lines = content.count("\n") - (0 if path.suffix == ".jsonl" else 1)
            message = f"checksum mismatch: file has {data_lines} rows, manifest says {entry['rows']}"
            log_attempt(tx, entry, delivery_date, "REJECTED_MANIFEST", actual_sha, message=message)
            logger.warning("REJECTED  %s (%s)", entry["file"], message)
            return "REJECTED_MANIFEST"

    try:
        header, rows = parse_file(path, raw_bytes.decode("utf-8"))
        if len(rows) != entry["rows"]:
            raise ValueError(f"row count {len(rows)} differs from manifest {entry['rows']}")
        if len(set(header)) != len(header):
            raise ValueError("duplicate column names in header")

        table = raw_table_for(entry)
        with warehouse.transaction() as tx:
            load_id = log_attempt(tx, entry, delivery_date, "LOADED", actual_sha, finished=False)
            added = ensure_table(tx, table, header, entry["file"], load_id)
            loaded = tx.copy_rows(
                table_ref(RAW, table), METADATA_COLUMNS + header,
                ([load_id, number] + row for number, row in enumerate(rows, start=1)),
            )
            tx.execute(
                f"UPDATE {table_ref(AUDIT, 'file_load_log')} SET loaded_rows = :n, finished_at = now() "
                "WHERE load_id = :id", {"n": loaded, "id": load_id})
        if added:
            logger.info("SCHEMA    %s: new column(s) %s in %s", table, added, entry["file"])
        return "LOADED"

    except Exception as exc:                       # the load transaction has already rolled back
        with warehouse.transaction() as tx:
            log_attempt(tx, entry, delivery_date, "FAILED", actual_sha, message=str(exc)[:500])
        logger.error("FAILED    %s: %s", entry["file"], exc)
        return "FAILED"


# ---------------------------------------------------------------- the whole run
def ingest(warehouse: Warehouse, landing: Path, only_date: date | None = None) -> Counter:
    manifests = read_manifests(landing, only_date)
    if not manifests:
        logger.warning("No manifests found%s", f" for {only_date}" if only_date else "")
        return Counter()

    results = Counter()
    for manifest in manifests:
        day = manifest["delivery_date"]
        day_results = Counter(load_file(warehouse, landing, e, day) for e in manifest["files"])
        results.update(day_results)
        logger.info("Delivery %s: %s", day, dict(day_results))

    if only_date is None:                          # files that arrived without being announced
        listed = {e["file"] for m in manifests for e in m["files"]}
        on_disk = {str(p.relative_to(landing)) for p in landing.rglob("*")
                   if p.is_file() and p.parent.name != "manifests"}
        for extra in sorted(on_disk - listed):
            logger.warning("UNLISTED  %s is not in any manifest and was not loaded", extra)
    return results


def raw_row_counts(warehouse: Warehouse) -> dict:
    with warehouse.transaction() as tx:
        tables = [r[0] for r in tx.query(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = :s ORDER BY 1", {"s": RAW})]
        return {t: int(tx.query(f"SELECT count(*) FROM {table_ref(RAW, t)}")[0][0]) for t in tables}


def main():
    parser = argparse.ArgumentParser(description="Load landing files into the raw layer")
    parser.add_argument("--date", type=date.fromisoformat, help="only this delivery date (YYYY-MM-DD)")
    args = parser.parse_args()

    cfg = load_config()
    warehouse = Warehouse()
    warehouse.apply_sql_files("sql/init")           # make sure all audit tables exist

    results = ingest(warehouse, Path(cfg["paths"]["landing"]), args.date)
    logger.info("This run: %s", dict(results))
    for table, n in raw_row_counts(warehouse).items():
        logger.info("  raw.%-14s %s rows", table, f"{n:,}")


if __name__ == "__main__":
    main()
