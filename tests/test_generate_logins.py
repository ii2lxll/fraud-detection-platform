from datetime import datetime, timedelta

import pandas as pd
import pytest

from generate_logins import COLUMNS, generate_logins, write_daily_jsonl


@pytest.fixture(scope="module")
def logins(small_world):
    w = small_world
    return generate_logins(w["txns"], w["labels"], w["accounts"], w["devices"], w["cfg"])


@pytest.fixture(scope="module")
def ato_transfers(small_world):
    w = small_world
    ato = w["labels"][(w["labels"]["fraud_type"] == "ACCOUNT_TAKEOVER") & (w["labels"]["fraud_role"] == "VICTIM")]
    return w["txns"].merge(ato, on="txn_id")


def test_event_ids_are_unique(logins):
    assert logins["event_id"].is_unique


def test_logins_inside_simulation_period(logins, small_world):
    cfg = small_world["cfg"]
    start = datetime.fromisoformat(cfg["simulation"]["start_date"])
    end = start + timedelta(days=cfg["simulation"]["n_days"])
    assert (logins["ts"] >= start).all() and (logins["ts"] < end).all()


def test_landing_files_have_no_labels(logins, tmp_path):
    write_daily_jsonl(logins, tmp_path)
    first = pd.read_json(sorted(tmp_path.glob("*.jsonl"))[0], lines=True)
    assert list(first.columns) == COLUMNS


def test_every_account_takeover_has_attacker_login_first(logins, ato_transfers):
    success = logins[(logins["result"] == "SUCCESS") & logins["case_id"].notna()]
    first_transfer = ato_transfers.groupby("case_id")["txn_timestamp"].min()
    first_login = success.groupby("case_id")["ts"].min()
    assert set(first_transfer.index) == set(first_login.index)
    assert (first_login < first_transfer.reindex(first_login.index)).all()


def test_attackers_use_unregistered_devices_customers_use_their_own(logins, small_world):
    registered = set(small_world["devices"]["device_id"])
    attacker = logins[logins["case_id"].notna()]
    customer = logins[logins["case_id"].isna()]
    assert not attacker["device_id"].isin(registered).any()
    assert customer["device_id"].isin(registered).all()


def test_customers_log_in_before_their_online_transfers(logins, small_world):
    """Each online transaction is either part of an ongoing session (recent activity on the device)
    or follows a fresh successful login on that device a few minutes earlier."""
    w = small_world
    l = w["cfg"]["logins"]
    attack_ids = set(w["labels"].loc[w["labels"]["fraud_type"] == "ACCOUNT_TAKEOVER", "txn_id"])
    own = w["txns"][w["txns"]["channel"].isin(["MOBILE", "WEB"]) & ~w["txns"]["txn_id"].isin(attack_ids)]
    own = own.sort_values("txn_timestamp")

    success = logins[(logins["result"] == "SUCCESS") & logins["case_id"].isna()].sort_values("ts")
    matched = pd.merge_asof(own, success[["ts", "device_id"]], left_on="txn_timestamp", right_on="ts",
                            by="device_id", direction="backward")
    matched = matched.sort_values(["device_id", "txn_timestamp"])
    since_login = matched["txn_timestamp"] - matched["ts"]
    since_previous_txn = matched.groupby("device_id")["txn_timestamp"].diff()

    fresh_login = since_login <= timedelta(minutes=l["minutes_before_activity"][1])
    ongoing = since_previous_txn <= timedelta(minutes=l["session_timeout_minutes"])
    start = datetime.fromisoformat(w["cfg"]["simulation"]["start_date"])
    first_minutes = matched["txn_timestamp"] < start + timedelta(minutes=l["minutes_before_activity"][1])
    assert (fresh_login | ongoing | first_minutes).all()


def test_failed_login_rate_is_realistic(logins):
    assert 0.01 < (logins["result"] == "FAILED").mean() < 0.06
