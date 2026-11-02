"""
Staging tests: the strongest check we can make is to compare the CLEANED data with the TRUTH.

The simulator's original data (before deliver_files.py damaged it) is the truth. After ingesting the
damaged files and building staging, every value that survived should match the original.
"""
from pathlib import Path

import pandas as pd
import pytest

from build_staging import build_staging, staging_summary
from dq_report import evaluate_files, evaluate_rows, load_findings, unexpected_findings, verify_transaction_repairs
from ingest import ingest


@pytest.fixture(scope="module")
def staged(warehouse_factory, delivered):
    warehouse = warehouse_factory("fraud_platform_test_staging")
    ingest(warehouse, delivered["landing"])
    build_staging(warehouse)
    return warehouse


def frame(warehouse, sql, columns):
    with warehouse.transaction() as tx:
        return pd.DataFrame(tx.query(sql), columns=columns)


@pytest.fixture(scope="module")
def planted(delivered):
    return delivered["issues"]


@pytest.fixture(scope="module")
def findings(staged):
    return load_findings(staged)


# ---------------------------------------------------------------- detection
def test_every_planted_row_problem_is_detected(planted, findings):
    results = evaluate_rows(planted, findings)
    missed = results[results["recall"] < 1]
    assert missed.empty, f"missed problems:\n{missed}"


def test_no_false_positives(planted, findings):
    results = evaluate_rows(planted, findings)
    assert results["false_positives"].sum() == 0
    assert unexpected_findings(findings).empty


def test_every_file_level_problem_is_detected(staged, planted):
    files = evaluate_files(staged, planted)
    assert len(files) == 5 and files["detected"].all(), files


# ---------------------------------------------------------------- cleaned data == truth
@pytest.fixture(scope="module")
def cleaned_vs_truth(staged, small_world):
    clean = frame(staged, """SELECT txn_id, txn_timestamp, account_id, txn_type, amount, currency, device_id,
                                    balance_before, balance_after FROM staging.transactions""",
                  ["txn_id", "txn_timestamp", "account_id", "txn_type", "amount", "currency", "device_id",
                   "balance_before", "balance_after"])
    return clean.merge(small_world["txns"], on="txn_id", suffixes=("", "_true"))


def test_staging_has_one_row_per_real_transaction(staged, small_world, findings):
    clean_ids = set(frame(staged, "SELECT txn_id FROM staging.transactions", ["txn_id"])["txn_id"])
    rejected = set(findings.loc[(findings["source_table"] == "transactions") &
                                (findings["action"] == "REJECTED"), "record_key"])
    truth = set(small_world["txns"]["txn_id"])
    assert clean_ids.isdisjoint(rejected)
    assert clean_ids | rejected == truth              # nothing lost, nothing invented


def test_cleaned_values_match_the_original(cleaned_vs_truth):
    m = cleaned_vs_truth
    assert (m["amount"].astype(float) - m["amount_true"]).abs().max() < 0.005
    assert (m["txn_type"] == m["txn_type_true"]).all()
    assert (m["currency"] == m["currency_true"]).all()
    assert (m["account_id"] == m["account_id_true"]).all()
    assert (m["balance_after"].astype(float) - m["balance_after_true"]).abs().max() < 0.005


def test_timestamps_match_the_original_to_the_minute(cleaned_vs_truth):
    m = cleaned_vs_truth
    clean = pd.to_datetime(m["txn_timestamp"])
    truth = pd.to_datetime(m["txn_timestamp_true"])
    exact = clean == truth
    assert ((clean == truth.dt.floor("min")) | exact).all()
    assert exact.mean() > 0.99                        # only the US-format rows lost their seconds


def test_device_survives_the_column_rename(cleaned_vs_truth):
    m = cleaned_vs_truth
    has_truth = m["device_id_true"].notna()
    assert (m.loc[has_truth, "device_id"] == m.loc[has_truth, "device_id_true"]).all()


def test_every_repair_is_correct(staged, small_world):
    repairs = verify_transaction_repairs(staged, small_world["txns"])
    assert repairs["repaired"].sum() > 0
    assert (repairs["correct"] == repairs["repaired"]).all(), repairs


def test_only_unfixable_transactions_are_rejected(findings, small_world):
    """Rejected = unknown account, or a missing/negative amount on a DECLINED transaction (no balance change)."""
    rejected = findings[(findings["source_table"] == "transactions") & (findings["action"] == "REJECTED")]
    truth = small_world["txns"].set_index("txn_id")
    amount_rejects = rejected[rejected["finding"].isin(["MISSING_AMOUNT", "NEGATIVE_AMOUNT"])]
    assert (truth.loc[amount_rejects["record_key"], "status"] == "DECLINED").all()
    assert set(rejected["finding"]) <= {"UNKNOWN_ACCOUNT", "MISSING_AMOUNT", "NEGATIVE_AMOUNT"}


# ---------------------------------------------------------------- other tables
def test_no_customer_is_dropped_for_one_bad_value(staged, small_world):
    customers = frame(staged, "SELECT customer_id, date_of_birth, email FROM staging.customers",
                      ["customer_id", "date_of_birth", "email"])
    assert set(customers["customer_id"]) == set(small_world["customers"]["customer_id"])
    assert customers["email"].dropna().str.contains("@").all()


def test_repaired_mcc_matches_the_original(staged, small_world):
    merchants = frame(staged, "SELECT merchant_id, mcc FROM staging.merchants", ["merchant_id", "mcc"])
    merged = merchants.merge(small_world["merchants"][["merchant_id", "mcc"]], on="merchant_id", suffixes=("", "_true"))
    assert len(merged) == len(small_world["merchants"])
    assert (merged["mcc"] == merged["mcc_true"]).all()


def test_login_timestamps_and_countries_are_restored(staged, delivered):
    simulated = delivered["cfg"]["paths"]["logins"]
    truth = pd.concat(pd.read_json(f, lines=True, dtype=False)
                      for f in sorted(Path(simulated).glob("*.jsonl")))
    clean = frame(staged, "SELECT event_id, event_timestamp, ip_country FROM staging.logins",
                  ["event_id", "event_timestamp", "ip_country"])
    merged = clean.merge(truth, on="event_id", suffixes=("", "_true"))
    assert len(merged) == len(truth)
    assert (pd.to_datetime(merged["event_timestamp"]) == pd.to_datetime(merged["event_timestamp_true"])).all()
    assert (merged["ip_country"] == merged["ip_country_true"]).all()


def test_fraud_report_amounts_are_restored(staged, delivered):
    simulated = delivered["cfg"]["paths"]["fraud_reports"]
    files = sorted(Path(simulated).glob("*.csv"))
    truth = pd.concat(pd.read_csv(f) for f in files)
    clean = frame(staged, "SELECT report_id, record_type, amount, event_date FROM staging.fraud_reports",
                  ["report_id", "record_type", "amount", "event_date"])
    merged = clean.merge(truth, on=["report_id", "record_type"], suffixes=("", "_true"))
    assert len(merged) == len(truth)
    assert (merged["amount"].astype(float) - merged["amount_true"]).abs().max() < 0.005
    assert (merged["event_date"].astype(str) == merged["event_date_true"].astype(str)).all()


# ---------------------------------------------------------------- re-running
def test_rebuilding_staging_gives_the_same_result(staged):
    before = staging_summary(staged)
    build_staging(staged)
    assert staging_summary(staged) == before


# ---------------------------------------------------------------- pure check (no database)
def test_recall_and_false_positives_are_counted_correctly():
    planted = pd.DataFrame({"feed": ["transactions"] * 3, "record_key": ["T1", "T2", "T2"],
                            "issue_type": ["DUPLICATE"] * 3})               # T2 planted twice: counts once
    findings = pd.DataFrame({"source_table": ["transactions"] * 2, "record_key": ["T1", "T9"],
                             "finding": ["DUPLICATE"] * 2, "action": ["REMOVED"] * 2})
    row = evaluate_rows(planted, findings).set_index(["feed", "planted_problem"]).loc[("transactions", "DUPLICATE")]
    assert row["planted"] == 2 and row["detected"] == 1 and row["false_positives"] == 1
    assert row["recall"] == 0.5
