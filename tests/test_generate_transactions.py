from datetime import date, timedelta

import pytest

from generate_accounts import generate_accounts
from generate_customers import generate_customers, load_config
from generate_devices import generate_devices
from generate_merchants import generate_merchants
from generate_transactions import generate_transactions


@pytest.fixture(scope="module")
def data():
    cfg = load_config()
    cfg["customers"]["n_customers"] = 200          # small, fast test world
    cfg["simulation"]["n_days"] = 14
    customers = generate_customers(cfg)
    accounts, cards = generate_accounts(customers, cfg)
    merchants = generate_merchants(cfg)
    devices = generate_devices(customers, cfg)
    txns = generate_transactions(customers, accounts, cards, merchants, devices, cfg)
    return cfg, accounts, txns


def test_transaction_ids_are_unique(data):
    _, _, txns = data
    assert txns["txn_id"].is_unique


def test_all_transactions_inside_simulation_period(data):
    cfg, _, txns = data
    start = date.fromisoformat(cfg["simulation"]["start_date"])
    end = start + timedelta(days=cfg["simulation"]["n_days"])
    days = txns["txn_timestamp"].dt.date
    assert (days >= start).all() and (days < end).all()


def test_balance_arithmetic(data):
    _, _, txns = data
    ok = txns[txns["status"] == "SUCCESS"]
    sign = ok["direction"].map({"DR": -1, "CR": 1})
    assert ((ok["balance_before"] + sign * ok["amount"] - ok["balance_after"]).abs() < 0.01).all()


def test_balance_continuity_per_account(data):
    """Each transaction must start exactly where the account's previous one ended."""
    _, _, txns = data
    ordered = txns.sort_values("txn_id")
    previous_after = ordered.groupby("account_id")["balance_after"].shift()
    has_previous = previous_after.notna()
    assert ((ordered.loc[has_previous, "balance_before"] - previous_after[has_previous]).abs() < 0.01).all()


def test_no_negative_balances(data):
    _, _, txns = data
    assert (txns["balance_after"] >= 0).all()


def test_declined_transactions_do_not_move_money(data):
    _, _, txns = data
    declined = txns[txns["status"] == "DECLINED"]
    assert (declined["balance_before"] == declined["balance_after"]).all()
    assert (declined["decline_reason"] == "INSUFFICIENT_FUNDS").all()


def test_internal_transfers_are_paired(data):
    """Money sent to another customer of our bank must arrive in their account."""
    cfg, _, txns = data
    bank = cfg["simulation"]["bank_code"]
    sent = txns[(txns["txn_type"] == "TRANSFER_OUT") & (txns["counterparty_bank"] == bank)
                & (txns["status"] == "SUCCESS")]
    received = txns[(txns["txn_type"] == "TRANSFER_IN") & (txns["channel"] == "INTERNAL")]
    pairs = sent.merge(received, on="txn_ref", suffixes=("_out", "_in"))
    assert len(pairs) == len(sent) == len(received)
    assert (pairs["amount_out"] == pairs["amount_in"]).all()
    assert (pairs["counterparty_account_out"] == pairs["account_id_in"]).all()


def test_only_active_accounts_send_money(data):
    _, accounts, txns = data
    active = set(accounts.loc[accounts["status"] == "ACTIVE", "account_id"])
    assert set(txns.loc[txns["direction"] == "DR", "account_id"]) <= active


def test_card_transactions_have_card_and_merchant(data):
    _, _, txns = data
    purchases = txns[txns["txn_type"] == "CARD_PURCHASE"]
    assert purchases["card_id"].notna().all() and purchases["merchant_id"].notna().all()
    assert purchases["channel"].isin(["POS", "ECOM"]).all()
