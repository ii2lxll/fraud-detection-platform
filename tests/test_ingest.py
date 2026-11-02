"""
Integration tests: run the real ingestion pipeline against a real PostgreSQL database.

They use a separate throwaway database (fraud_platform_test), created at the start and dropped at the end,
so your real warehouse is never touched. If the database isn't running, these tests are SKIPPED, not failed.
"""
import hashlib
import json
import shutil

import pytest
from sqlalchemy import text

from db import get_engine
from ingest import ingest, parse_file, raw_row_counts
from warehouse import Warehouse, ident

TEST_DB = "fraud_platform_test"


@pytest.fixture(scope="module")
def warehouse():
    try:
        admin = get_engine().execution_options(isolation_level="AUTOCOMMIT")   # CREATE DATABASE can't run in a transaction
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable ({exc.__class__.__name__}): start it with 'docker compose up -d'")

    engine = get_engine(database=TEST_DB)
    wh = Warehouse(engine)
    wh.apply_sql_files("sql/init")
    yield wh

    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))


@pytest.fixture(scope="module")
def first_run(warehouse, delivered):
    return ingest(warehouse, delivered["landing"])


def q(warehouse, sql, **params):
    with warehouse.transaction() as tx:
        return tx.query(sql, params)


def manifest_entries(landing):
    entries = []
    for path in sorted((landing / "manifests").glob("*.json")):
        m = json.loads(path.read_text())
        entries += [{**e, "delivery_date": m["delivery_date"]} for e in m["files"]]
    return entries


# ---------------------------------------------------------------- manifest checks
def test_only_the_truncated_file_is_rejected(first_run, warehouse, delivered):
    n_files = len(manifest_entries(delivered["landing"]))
    assert first_run == {"LOADED": n_files - 1, "REJECTED_MANIFEST": 1}
    rejected = q(warehouse, "SELECT file_name, message FROM audit.file_load_log WHERE status = 'REJECTED_MANIFEST'")
    assert len(rejected) == 1
    assert rejected[0][0].startswith("transactions/") and "_resend" not in rejected[0][0]
    assert "checksum mismatch" in rejected[0][1]


def test_resent_copy_is_loaded_instead(first_run, warehouse):
    loaded = q(warehouse, "SELECT file_name FROM audit.file_load_log WHERE status = 'LOADED' AND file_name LIKE :p",
               p="%\\_resend.csv")
    assert len(loaded) == 1


def test_late_file_keeps_its_real_delivery_date(first_run, warehouse, delivered):
    late_file = delivered["issues"].query("issue_type == 'LATE_FILE'")["file"].iloc[0]
    rows = q(warehouse, "SELECT delivery_date FROM audit.file_load_log WHERE file_name = :f",
             f=f"transactions/{late_file}")
    file_date = late_file.split("_")[-1].replace(".csv", "")
    assert (rows[0][0] - __import__("datetime").date.fromisoformat(
        f"{file_date[:4]}-{file_date[4:6]}-{file_date[6:]}")).days == delivered["drift"]["late_by_days"]


# ---------------------------------------------------------------- what lands in raw
def test_raw_row_counts_match_the_manifests(first_run, warehouse, delivered):
    expected = {}
    rejected = {r[0] for r in q(warehouse, "SELECT file_name FROM audit.file_load_log WHERE status <> 'LOADED'")}
    for e in manifest_entries(delivered["landing"]):
        if e["file"] in rejected:
            continue
        table = e["file"].split("/")[1].rsplit("_", 1)[0] if e["feed"] == "reference" else e["feed"]
        expected[table] = expected.get(table, 0) + e["rows"]
    assert raw_row_counts(warehouse) == expected


def test_every_raw_row_traces_back_to_its_load(first_run, warehouse):
    orphans = q(warehouse, """SELECT count(*) FROM raw.transactions t
                              LEFT JOIN audit.file_load_log l ON l.load_id = t._load_id
                              WHERE l.load_id IS NULL OR l.status <> 'LOADED'""")
    assert orphans[0][0] == 0
    mismatched = q(warehouse, """SELECT count(*) FROM (
                                    SELECT l.load_id, l.loaded_rows, count(t._load_id) AS n
                                    FROM audit.file_load_log l JOIN raw.transactions t ON t._load_id = l.load_id
                                    GROUP BY l.load_id, l.loaded_rows) x
                                 WHERE x.n <> x.loaded_rows""")
    assert mismatched[0][0] == 0


def test_values_are_kept_exactly_as_delivered(first_run, warehouse, delivered):
    issues = delivered["issues"]
    as_text = issues.query("feed == 'transactions' and issue_type == 'AMOUNT_AS_TEXT'")["record_key"].iloc[0]
    missing = issues.query("feed == 'transactions' and issue_type == 'MISSING_AMOUNT'")["record_key"].iloc[0]
    epoch = issues.query("feed == 'logins' and issue_type == 'EPOCH_TIMESTAMP'")["record_key"].iloc[0]

    assert q(warehouse, "SELECT amount FROM raw.transactions WHERE txn_id = :t", t=as_text)[0][0].startswith("$")
    assert q(warehouse, "SELECT amount FROM raw.transactions WHERE txn_id = :t", t=missing)[0][0] is None
    assert q(warehouse, "SELECT event_timestamp FROM raw.logins WHERE event_id = :e", e=epoch)[0][0].isdigit()


def test_duplicates_are_kept_in_raw(first_run, warehouse):
    """Raw keeps everything that arrived. Removing duplicates is the staging layer's job."""
    dupes = q(warehouse, "SELECT count(*) FROM (SELECT txn_id FROM raw.transactions GROUP BY txn_id "
                         "HAVING count(*) > 1) d")
    assert dupes[0][0] > 0


# ---------------------------------------------------------------- schema drift
def test_new_columns_are_added_and_logged(first_run, warehouse, delivered):
    columns = {r[0] for r in q(warehouse, "SELECT column_name FROM information_schema.columns "
                                          "WHERE table_schema = 'raw' AND table_name = 'transactions'")}
    assert {"device_id", "device_fingerprint", "terminal_id"} <= columns

    changes = dict(q(warehouse, "SELECT column_name, first_seen_file FROM audit.schema_change_log "
                                "WHERE change_type = 'COLUMN_ADDED' AND table_name = 'raw.transactions'"))
    files = sorted(p.name for p in (delivered["landing"] / "transactions").glob("*.csv") if "_resend" not in p.name)
    drift = delivered["drift"]
    assert changes == {"device_fingerprint": f"transactions/{files[drift['rename_column_day'] - 1]}",
                       "terminal_id": f"transactions/{files[drift['new_column_day'] - 1]}"}


def test_rows_from_before_the_rename_have_no_fingerprint(first_run, warehouse):
    rows = q(warehouse, """SELECT count(*) FILTER (WHERE device_id IS NOT NULL AND device_fingerprint IS NOT NULL)
                           FROM raw.transactions""")
    assert rows[0][0] == 0                         # a row has one or the other, never both


# ---------------------------------------------------------------- running twice
def test_second_run_loads_nothing_new(first_run, warehouse, delivered):
    before = raw_row_counts(warehouse)
    second = ingest(warehouse, delivered["landing"])
    assert second == {"SKIPPED_DUPLICATE": first_run["LOADED"], "REJECTED_MANIFEST": 1}
    assert raw_row_counts(warehouse) == before


# ---------------------------------------------------------------- a broken file rolls back completely
def test_failed_load_leaves_nothing_behind(warehouse, tmp_path):
    """A file that passes the manifest check but breaks during loading must leave NO rows and NO new table."""
    bad = "widget_id,colour\nW1,red\nW2,blue,EXTRA\n"
    name = "reference/widgets_20260601.csv"
    (tmp_path / "reference").mkdir()
    (tmp_path / name).write_text(bad)
    (tmp_path / "manifests").mkdir()
    manifest = {"delivery_date": "2026-06-01", "files": [
        {"feed": "reference", "file": name, "rows": 2, "sha256": hashlib.sha256(bad.encode()).hexdigest()}]}
    (tmp_path / "manifests" / "manifest_20260601.json").write_text(json.dumps(manifest))

    assert ingest(warehouse, tmp_path) == {"FAILED": 1}
    assert not q(warehouse, "SELECT 1 FROM information_schema.tables WHERE table_schema = 'raw' "
                            "AND table_name = 'widgets'")
    status = q(warehouse, "SELECT status, message FROM audit.file_load_log WHERE file_name = :f", f=name)
    assert status[0][0] == "FAILED" and "fields" in status[0][1]
    shutil.rmtree(tmp_path)


# ---------------------------------------------------------------- pure checks (no database needed)
def test_unsafe_column_names_are_refused():
    for bad in ['amount"; DROP TABLE raw.transactions; --', "Amount", "1st", "has space"]:
        with pytest.raises(ValueError):
            ident(bad)
    assert ident("device_fingerprint") == '"device_fingerprint"'


def test_parse_jsonl_keeps_numbers_as_text(tmp_path):
    path = tmp_path / "x.jsonl"
    header, rows = parse_file(path, '{"a":1748764800000,"b":null}\n{"a":"2026-06-01T08:00:00","b":"US"}\n')
    assert header == ["a", "b"]
    assert rows == [["1748764800000", None], ["2026-06-01T08:00:00", "US"]]
