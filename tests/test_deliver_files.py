import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from deliver_files import deliver_all
from generate_fraud_reports import generate_fraud_reports
from generate_fraud_reports import write_daily_files as write_report_files
from generate_logins import generate_logins, write_daily_jsonl
from generate_transactions import write_daily_files as write_transaction_files

DRIFT = {"truncated_transactions_day": 5, "late_transactions_day": 10, "late_by_days": 2,
         "rename_column_day": 15, "new_column_day": 20}


def folder_hashes(folder: Path) -> dict:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def delivered(small_world, tmp_path_factory):
    """Write the small world to a temporary 'simulated' folder, then deliver it to a temporary landing zone."""
    w = small_world
    cfg = copy.deepcopy(w["cfg"])
    root = tmp_path_factory.mktemp("delivery")
    for key in ["reference", "transactions", "logins", "fraud_reports", "ground_truth", "landing"]:
        cfg["paths"][key] = str(root / key)
    cfg["delivery"]["file_issues"].update(DRIFT)

    snap = cfg["project"]["snapshot_date"].replace("-", "")
    ref = Path(cfg["paths"]["reference"])
    ref.mkdir(parents=True)
    for table in ["customers", "accounts", "cards", "merchants", "devices"]:
        w[table].to_csv(ref / f"{table}_{snap}.csv", index=False)
    write_transaction_files(w["txns"], Path(cfg["paths"]["transactions"]))
    logins = generate_logins(w["txns"], w["labels"], w["accounts"], w["devices"], cfg)
    write_daily_jsonl(logins, Path(cfg["paths"]["logins"]))
    reports, _ = generate_fraud_reports(w["txns"], w["labels"], w["accounts"], cfg)
    write_report_files(reports, cfg, Path(cfg["paths"]["fraud_reports"]))

    simulated_before = folder_hashes(Path(cfg["paths"]["transactions"]))
    issues = deliver_all(cfg)
    return {"cfg": cfg, "issues": issues, "landing": Path(cfg["paths"]["landing"]),
            "simulated_before": simulated_before}


def manifest_entries(landing: Path) -> list:
    entries = []
    for path in sorted((landing / "manifests").glob("manifest_*.json")):
        manifest = json.loads(path.read_text())
        entries += [{**f, "delivery_date": manifest["delivery_date"]} for f in manifest["files"]]
    return entries


def data_rows(path: Path) -> int:
    lines = path.read_text().splitlines()
    return len(lines) if path.suffix == ".jsonl" else len(lines) - 1


def landing_transactions(landing: Path) -> pd.DataFrame:
    frames = []
    for f in sorted((landing / "transactions").glob("*.csv")):
        frames.append(pd.read_csv(f, dtype=str, keep_default_na=False).assign(file=f.name))
    return pd.concat(frames, ignore_index=True)


def test_every_landing_file_is_in_exactly_one_manifest(delivered):
    landing = delivered["landing"]
    listed = [e["file"] for e in manifest_entries(landing)]
    on_disk = {str(p.relative_to(landing)) for p in landing.rglob("*") if p.is_file() and p.parent.name != "manifests"}
    assert len(listed) == len(set(listed))
    assert set(listed) == on_disk


def test_only_the_truncated_file_fails_the_manifest_check(delivered):
    landing = delivered["landing"]
    failing = []
    for e in manifest_entries(landing):
        path = landing / e["file"]
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if data_rows(path) != e["rows"] or checksum != e["sha256"]:
            failing.append(e["file"])
    assert len(failing) == 1 and failing[0].startswith("transactions/") and "_resend" not in failing[0]


def test_resent_file_arrives_the_next_day(delivered):
    entries = {e["file"]: e for e in manifest_entries(delivered["landing"])}
    resend = [f for f in entries if f.endswith("_resend.csv")]
    assert len(resend) == 1
    original = resend[0].replace("_resend", "")
    assert pd.Timestamp(entries[resend[0]]["delivery_date"]) - pd.Timestamp(entries[original]["delivery_date"]) \
        == pd.Timedelta(days=1)


def test_late_file_is_delivered_days_later(delivered):
    late_files = delivered["issues"].query("issue_type == 'LATE_FILE'")["file"].tolist()
    assert len(late_files) == 1
    entry = next(e for e in manifest_entries(delivered["landing"]) if e["file"].endswith(late_files[0]))
    file_date = pd.Timestamp(late_files[0].split("_")[-1].replace(".csv", ""))
    assert pd.Timestamp(entry["delivery_date"]) - file_date == pd.Timedelta(days=DRIFT["late_by_days"])


def test_schema_drift_appears_on_the_configured_days(delivered):
    files = sorted(f for f in (delivered["landing"] / "transactions").glob("*.csv") if "_resend" not in f.name)
    for day_number, f in enumerate(files, start=1):
        header = pd.read_csv(f, nrows=0).columns
        renamed = day_number >= DRIFT["rename_column_day"]
        assert ("device_fingerprint" in header) == renamed and ("device_id" in header) == (not renamed)
        assert ("terminal_id" in header) == (day_number >= DRIFT["new_column_day"])


def test_no_transaction_is_lost(delivered, small_world):
    """Damaged, late or re-sent: every simulated transaction still reaches the landing zone somewhere."""
    landed = set(landing_transactions(delivered["landing"])["txn_id"])
    assert landed == set(small_world["txns"]["txn_id"])


def test_logged_damage_is_really_in_the_files(delivered):
    txns = landing_transactions(delivered["landing"])
    log = delivered["issues"]

    missing = log.query("feed == 'transactions' and issue_type == 'MISSING_AMOUNT'")
    rows = txns.merge(missing, left_on=["txn_id", "file"], right_on=["record_key", "file"])
    assert len(rows) == len(missing) and (rows["amount"] == "").all()

    dupes = log.query("feed == 'transactions' and issue_type == 'DUPLICATE'")
    counts = txns.groupby(["file", "txn_id"]).size()
    assert (counts.loc[list(zip(dupes["file"], dupes["record_key"]))] >= 2).all()

    epoch = log.query("feed == 'logins' and issue_type == 'EPOCH_TIMESTAMP'").iloc[0]
    events = [json.loads(line) for line in (delivered["landing"] / "logins" / epoch["file"]).read_text().splitlines()]
    event = next(e for e in events if e["event_id"] == epoch["record_key"])
    assert isinstance(event["event_timestamp"], int)


def test_issue_rates_are_close_to_config(delivered, small_world):
    rates = delivered["cfg"]["delivery"]["row_issue_rates"]["transactions"]
    counts = delivered["issues"].query("feed == 'transactions'")["issue_type"].value_counts()
    n = len(small_world["txns"])
    for issue in ["duplicate", "messy_txn_type", "us_date_format", "late_arriving"]:
        key = "LATE_ARRIVING_ROW" if issue == "late_arriving" else issue.upper()
        assert 0.5 * rates[issue] < counts[key] / n < 1.5 * rates[issue]


def test_simulated_files_are_never_modified(delivered):
    after = folder_hashes(Path(delivered["cfg"]["paths"]["transactions"]))
    assert after == delivered["simulated_before"]


def test_delivery_is_reproducible(delivered):
    first = folder_hashes(delivered["landing"])
    issues_again = deliver_all(delivered["cfg"])
    assert folder_hashes(delivered["landing"]) == first
    pd.testing.assert_frame_equal(issues_again, delivered["issues"])
