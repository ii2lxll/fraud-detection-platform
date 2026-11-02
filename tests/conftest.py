"""
Shared test setup. pytest automatically loads this file and makes its fixtures available to every test.
`scope="session"` means the small simulated world is built once and reused by all tests that need it.
"""
import copy
from pathlib import Path

import pytest

from deliver_files import deliver_all
from generate_accounts import generate_accounts
from generate_customers import generate_customers, load_config
from generate_devices import generate_devices
from generate_fraud_reports import generate_fraud_reports
from generate_fraud_reports import write_daily_files as write_report_files
from generate_logins import generate_logins, write_daily_jsonl
from generate_merchants import generate_merchants
from generate_transactions import COLUMNS, LABEL_COLUMNS, simulate
from generate_transactions import write_daily_files as write_transaction_files
from utils import folder_hashes


@pytest.fixture(scope="session")
def small_world():
    cfg = load_config()
    cfg["customers"]["n_customers"] = 600
    cfg["simulation"]["n_days"] = 30
    cfg["fraud"]["cases"] = {"ACCOUNT_TAKEOVER": 15, "SCAM": 10, "CARD_NOT_PRESENT": 15}

    customers = generate_customers(cfg)
    accounts, cards = generate_accounts(customers, cfg)
    merchants = generate_merchants(cfg)
    devices = generate_devices(customers, cfg)
    result = simulate(customers, accounts, cards, merchants, devices, cfg)

    full = result["transactions"]
    return {
        "cfg": cfg, "customers": customers, "accounts": accounts, "cards": cards, "merchants": merchants,
        "devices": devices, "mules": result["mules"],
        "txns": full[COLUMNS],                                                  # what the pipeline sees
        "labels": full.loc[full["case_id"].notna(), ["txn_id"] + LABEL_COLUMNS],  # the answer key
    }


# Small file-level problems on early days, so a 30-day test world shows all of them
DRIFT = {"truncated_transactions_day": 5, "late_transactions_day": 10, "late_by_days": 2,
         "rename_column_day": 15, "new_column_day": 20}


@pytest.fixture(scope="session")
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
            "simulated_before": simulated_before, "drift": DRIFT}
