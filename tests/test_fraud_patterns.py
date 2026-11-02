from datetime import date, timedelta

import pandas as pd
import pytest

from generate_accounts import generate_accounts
from generate_customers import generate_customers, load_config
from generate_devices import generate_devices
from generate_merchants import generate_merchants
from generate_transactions import COLUMNS, LABEL_COLUMNS, simulate, write_daily_files


@pytest.fixture(scope="module")
def world():
    cfg = load_config()
    cfg["customers"]["n_customers"] = 600
    cfg["simulation"]["n_days"] = 30
    cfg["fraud"]["cases"] = {"ACCOUNT_TAKEOVER": 15, "SCAM": 10, "CARD_NOT_PRESENT": 15}
    customers = generate_customers(cfg)
    accounts, cards = generate_accounts(customers, cfg)
    merchants = generate_merchants(cfg)
    devices = generate_devices(customers, cfg)
    result = simulate(customers, accounts, cards, merchants, devices, cfg)
    return {"cfg": cfg, "customers": customers, "merchants": merchants, "devices": devices,
            "txns": result["transactions"], "mules": result["mules"]}


def victim_rows(txns, fraud_type):
    return txns[(txns["fraud_type"] == fraud_type) & (txns["fraud_role"] == "VICTIM")]


def test_every_fraud_type_is_present(world):
    victims = world["txns"][world["txns"]["fraud_role"] == "VICTIM"]
    assert set(victims["fraud_type"]) == {"ACCOUNT_TAKEOVER", "SCAM", "CARD_NOT_PRESENT"}


def test_fraud_is_rare(world):
    share = world["txns"]["case_id"].notna().mean()
    assert 0.0005 < share < 0.02


def test_landing_files_contain_no_labels(world, tmp_path):
    """The pipeline must never see the answers."""
    write_daily_files(world["txns"], tmp_path)
    first_file = sorted(tmp_path.glob("*.csv"))[0]
    header = pd.read_csv(first_file, nrows=0).columns.tolist()
    assert header == COLUMNS
    assert not set(LABEL_COLUMNS) & set(header)


def test_account_takeover_uses_unknown_devices(world):
    ato = victim_rows(world["txns"], "ACCOUNT_TAKEOVER")
    assert not ato["device_id"].isin(world["devices"]["device_id"]).any()


def test_scam_uses_victims_own_device(world):
    scam = victim_rows(world["txns"], "SCAM")
    known = scam["device_id"].isin(world["devices"]["device_id"]) | scam["device_id"].isna()
    assert known.all()


def test_scam_pays_one_payee_never_paid_before(world):
    txns = world["txns"]
    for case_id, case in victim_rows(txns, "SCAM").groupby("case_id"):
        assert case["counterparty_account"].nunique() == 1
        account = case["account_id"].iloc[0]
        payee = case["counterparty_account"].iloc[0]
        earlier = txns[(txns["account_id"] == account) & (txns["txn_timestamp"] < case["txn_timestamp"].min())]
        assert payee not in set(earlier["counterparty_account"])


def test_card_fraud_starts_small_at_risky_online_merchants(world):
    cnp = victim_rows(world["txns"], "CARD_NOT_PRESENT").merge(
        world["merchants"][["merchant_id", "risk_tier", "channel"]], on="merchant_id", suffixes=("", "_m"))
    assert (cnp["risk_tier"] == "HIGH").all() and (cnp["channel_m"] == "ONLINE").all()
    first_purchase = cnp.sort_values("txn_timestamp").groupby("case_id")["amount"].first()
    assert (first_purchase <= world["cfg"]["fraud"]["card_not_present"]["test_amount"][1]).all()


def test_mules_are_young_recent_customers(world):
    cfg = world["cfg"]
    snapshot = date.fromisoformat(cfg["project"]["snapshot_date"])
    assert world["mules"], "expected at least one mule"
    for mule in world["mules"]:
        assert (snapshot - mule["date_of_birth"]).days // 365 <= cfg["fraud"]["mule_max_age"]
        assert (snapshot - mule["customer_since"]).days <= cfg["fraud"]["mule_max_tenure_days"]


def test_mules_cash_out_after_receiving_and_not_more(world):
    txns = world["txns"]
    received = txns[txns["fraud_role"] == "MULE_INBOUND"]
    cashed = txns[(txns["fraud_role"] == "MULE_CASHOUT") & (txns["status"] == "SUCCESS")]
    assert len(cashed) > 0
    mule_ids = {m["account_id"] for m in world["mules"]}
    assert set(received["account_id"]) <= mule_ids and set(cashed["account_id"]) <= mule_ids

    first_in = received.groupby("case_id")["txn_timestamp"].min()
    first_out = cashed.groupby("case_id")["txn_timestamp"].min()
    assert (first_out > first_in.reindex(first_out.index)).all()

    total_in = received.groupby("case_id")["amount"].sum()
    total_out = cashed.groupby("case_id")["amount"].sum()
    assert (total_out <= total_in.reindex(total_out.index) + 0.01).all()


def test_fraud_inside_simulation_period(world):
    cfg = world["cfg"]
    start = date.fromisoformat(cfg["simulation"]["start_date"])
    end = start + timedelta(days=cfg["simulation"]["n_days"])
    days = world["txns"].loc[world["txns"]["case_id"].notna(), "txn_timestamp"].dt.date
    assert (days >= start).all() and (days < end).all()
