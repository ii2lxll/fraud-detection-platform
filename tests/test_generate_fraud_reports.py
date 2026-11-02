from datetime import date, timedelta

import pandas as pd
import pytest

from generate_fraud_reports import COLUMNS, generate_fraud_reports, write_daily_files


@pytest.fixture(scope="module")
def reports(small_world):
    w = small_world
    df, stats = generate_fraud_reports(w["txns"], w["labels"], w["accounts"], w["cfg"])
    return df, stats


@pytest.fixture(scope="module")
def claims(reports, small_world):
    """Each NEW claim joined to its transaction and the true answer."""
    df, _ = reports
    w = small_world
    new = df[df["record_type"] == "NEW"].merge(w["txns"][["txn_id", "txn_timestamp"]], on="txn_id")
    return new.merge(w["labels"], on="txn_id", how="left")


def test_reports_point_to_real_transactions(reports, small_world):
    df, _ = reports
    assert set(df["txn_id"]) <= set(small_world["txns"]["txn_id"])


def test_reports_come_after_the_transaction(claims):
    assert (claims["event_date"] >= claims["txn_timestamp"].dt.date).all()


def test_no_reports_outside_simulation_period(reports, small_world):
    df, _ = reports
    cfg = small_world["cfg"]
    start = date.fromisoformat(cfg["simulation"]["start_date"])
    end = start + timedelta(days=cfg["simulation"]["n_days"])
    assert (df["event_date"] >= start).all() and (df["event_date"] < end).all()


def test_investigation_outcomes_match_the_truth(reports, small_world):
    df, _ = reports
    truth = df.merge(small_world["labels"], on="txn_id", how="left")
    assert truth.loc[truth["status"] == "CONFIRMED_FRAUD", "case_id"].notna().all()
    assert truth.loc[truth["status"] == "REJECTED", "case_id"].isna().all()


def test_updates_follow_their_new_record(reports):
    df, _ = reports
    new = df[df["record_type"] == "NEW"].set_index("report_id")["event_date"]
    upd = df[df["record_type"] == "UPDATE"].set_index("report_id")["event_date"]
    assert upd.index.isin(new.index).all()
    assert (upd > new.reindex(upd.index)).all()


def test_not_all_fraud_is_known(reports):
    _, stats = reports
    assert stats["never_reported"] + stats["reported_after_data_ends"] > 0


def test_card_fraud_is_reported_later_than_account_takeover(claims):
    delay = (pd.to_datetime(claims["event_date"]) - claims["txn_timestamp"].dt.normalize()).dt.days
    by_type = delay.groupby(claims["fraud_type"]).median()
    assert by_type["CARD_NOT_PRESENT"] > by_type["ACCOUNT_TAKEOVER"]


def test_mule_flags_only_on_real_mules(reports, small_world):
    df, _ = reports
    flagged = set(df.loc[df["claim_type"] == "MULE_ACCOUNT", "account_id"])
    assert flagged <= {m["account_id"] for m in small_world["mules"]}


def test_a_file_for_every_day(reports, small_world, tmp_path):
    df, _ = reports
    n_files = write_daily_files(df, small_world["cfg"], tmp_path)
    files = sorted(tmp_path.glob("*.csv"))
    assert len(files) == n_files == small_world["cfg"]["simulation"]["n_days"]
    assert pd.read_csv(files[0], nrows=0).columns.tolist() == COLUMNS
