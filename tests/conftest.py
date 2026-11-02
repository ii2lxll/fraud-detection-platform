"""
Shared test setup. pytest automatically loads this file and makes its fixtures available to every test.
`scope="session"` means the small simulated world is built once and reused by all tests that need it.
"""
import pytest

from generate_accounts import generate_accounts
from generate_customers import generate_customers, load_config
from generate_devices import generate_devices
from generate_merchants import generate_merchants
from generate_transactions import COLUMNS, LABEL_COLUMNS, simulate


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
