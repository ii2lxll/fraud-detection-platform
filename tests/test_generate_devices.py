from datetime import date

import pytest

from generate_customers import generate_customers, load_config
from generate_devices import generate_devices


@pytest.fixture(scope="module")
def data():
    cfg = load_config()
    cfg["customers"]["n_customers"] = 300
    customers = generate_customers(cfg)
    devices = generate_devices(customers, cfg)
    snapshot = date.fromisoformat(cfg["project"]["snapshot_date"])
    return customers, devices, snapshot


def test_device_ids_are_unique(data):
    _, devices, _ = data
    assert devices["device_id"].is_unique


def test_every_device_belongs_to_a_customer(data):
    customers, devices, _ = data
    assert set(devices["customer_id"]) <= set(customers["customer_id"])


def test_first_seen_between_join_date_and_snapshot(data):
    customers, devices, snapshot = data
    merged = devices.merge(customers[["customer_id", "customer_since"]], on="customer_id")
    assert (merged["first_seen"] >= merged["customer_since"]).all()
    assert (merged["first_seen"] <= snapshot).all()


def test_mobile_has_model_and_web_has_user_agent(data):
    _, devices, _ = data
    web = devices[devices["device_type"] == "WEB_BROWSER"]
    mobile = devices[devices["device_type"] != "WEB_BROWSER"]
    assert web["user_agent"].notna().all() and web["device_model"].isna().all()
    assert mobile["device_model"].notna().all() and mobile["user_agent"].isna().all()


def test_mobile_customers_have_a_device(data):
    customers, devices, _ = data
    mobile_customers = set(customers.loc[customers["preferred_channel"] == "MOBILE", "customer_id"])
    assert mobile_customers <= set(devices["customer_id"])
