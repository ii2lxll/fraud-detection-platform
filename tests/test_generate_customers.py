from datetime import date

import pandas as pd
import pytest

from generate_customers import generate_customers, load_config


@pytest.fixture(scope="module")
def cfg():
    config = load_config()
    config["customers"]["n_customers"] = 500   # smaller sample = faster tests
    return config


@pytest.fixture(scope="module")
def customers(cfg):
    return generate_customers(cfg)


def test_customer_ids_are_unique(customers):
    assert customers["customer_id"].is_unique


def test_all_customers_are_adults(customers, cfg):
    snapshot = date.fromisoformat(cfg["project"]["snapshot_date"])
    age_days = customers["date_of_birth"].apply(lambda d: (snapshot - d).days)
    assert (age_days >= 18 * 365).all()


def test_joined_after_18_and_not_in_future(customers, cfg):
    snapshot = date.fromisoformat(cfg["project"]["snapshot_date"])
    age_at_join = (customers["customer_since"] - customers["date_of_birth"]).apply(lambda d: d.days)
    assert (age_at_join >= 18 * 365).all()
    assert (customers["customer_since"] <= snapshot).all()


def test_income_band_matches_segment(customers, cfg):
    bands = cfg["customers"]["income_bands"]
    valid = customers.apply(lambda r: r["income_band"] in bands[r["segment"]], axis=1)
    assert valid.all()


def test_same_seed_gives_same_data(cfg):
    pd.testing.assert_frame_equal(generate_customers(cfg), generate_customers(cfg))
