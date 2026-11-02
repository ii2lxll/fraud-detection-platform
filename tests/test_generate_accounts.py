from datetime import date

import pytest

from generate_accounts import generate_accounts
from generate_customers import generate_customers, load_config


@pytest.fixture(scope="module")
def data():
    cfg = load_config()
    cfg["customers"]["n_customers"] = 300
    customers = generate_customers(cfg)
    accounts, cards = generate_accounts(customers, cfg)
    snapshot = date.fromisoformat(cfg["project"]["snapshot_date"])
    return customers, accounts, cards, snapshot


def test_account_ids_are_unique(data):
    _, accounts, _, _ = data
    assert accounts["account_id"].is_unique


def test_every_customer_has_one_main_account(data):
    customers, accounts, _, _ = data
    main = accounts[accounts["account_type"] != "SAVINGS"]
    assert main["customer_id"].is_unique
    assert set(main["customer_id"]) == set(customers["customer_id"])


def test_accounts_open_after_customer_joined(data):
    customers, accounts, _, snapshot = data
    merged = accounts.merge(customers[["customer_id", "customer_since"]], on="customer_id")
    assert (merged["open_date"] >= merged["customer_since"]).all()
    assert (merged["open_date"] <= snapshot).all()


def test_closed_accounts_have_zero_balance_and_close_date(data):
    _, accounts, _, _ = data
    closed = accounts[accounts["status"] == "CLOSED"]
    not_closed = accounts[accounts["status"] != "CLOSED"]
    assert (closed["current_balance"] == 0).all()
    assert closed["close_date"].notna().all()
    assert not_closed["close_date"].isna().all()


def test_cards_only_on_checking_accounts(data):
    _, accounts, cards, _ = data
    linked = cards.merge(accounts, on="account_id")
    assert len(linked) == len(cards)                         # no orphan cards
    assert (linked["account_type"] != "SAVINGS").all()


def test_active_cards_are_not_expired(data):
    _, _, cards, snapshot = data
    active = cards[cards["card_status"] == "ACTIVE"]
    assert (active["issue_date"] <= snapshot).all()
    assert (active["expiry_date"] > snapshot).all()
