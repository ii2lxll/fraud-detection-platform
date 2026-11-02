"""
Mart tests: the star schema must reconcile with staging, keep its keys intact, document every column,
and carry fraud labels that reflect ONLY what the bank actually knows.
"""
import json
import re

import pandas as pd
import pytest

from build_mart import MART_TABLES, build_mart, mart_summary
from build_staging import build_staging
from data_dictionary import describe, render, undocumented
from export_mart import export_mart
from ingest import ingest
from warehouse import split_sql


@pytest.fixture(scope="module")
def mart(warehouse_factory, delivered):
    warehouse = warehouse_factory("fraud_platform_test_mart")
    ingest(warehouse, delivered["landing"])
    build_staging(warehouse)
    build_mart(warehouse, delivered["cfg"])
    return warehouse


def q(warehouse, sql, **params):
    with warehouse.transaction() as tx:
        return tx.query(sql, params)


def frame(warehouse, sql):
    with warehouse.transaction() as tx:
        return tx.query_frame(sql)


# ---------------------------------------------------------------- reconciliation with staging
def test_fact_tables_reconcile_with_staging(mart):
    checks = q(mart, """SELECT (SELECT count(*) FROM mart.fact_transactions), (SELECT count(*) FROM staging.transactions),
                               (SELECT sum(amount) FROM mart.fact_transactions), (SELECT sum(amount) FROM staging.transactions),
                               (SELECT count(*) FROM mart.fact_logins), (SELECT count(*) FROM staging.logins)""")[0]
    assert checks[0] == checks[1] and checks[2] == checks[3] and checks[4] == checks[5]


def test_signed_amount_matches_direction(mart):
    bad = q(mart, """SELECT count(*) FROM mart.fact_transactions
                     WHERE signed_amount <> CASE WHEN direction = 'DR' THEN -amount ELSE amount END""")
    assert bad[0][0] == 0


# ---------------------------------------------------------------- keys
def test_every_fact_key_points_to_a_dimension_row(mart):
    """Foreign keys enforce this; the test proves no fact row was silently dropped by an inner join."""
    staged = q(mart, "SELECT count(*) FROM staging.transactions")[0][0]
    joined = q(mart, """SELECT count(*) FROM mart.fact_transactions f
                        JOIN mart.dim_date d USING (date_key) JOIN mart.dim_account a USING (account_key)
                        JOIN mart.dim_customer c ON c.customer_key = f.customer_key
                        JOIN mart.dim_merchant m USING (merchant_key)""")[0][0]
    assert joined == staged


def test_transactions_without_a_merchant_use_the_unknown_member(mart):
    rows = q(mart, """SELECT count(*) FILTER (WHERE merchant_key = -1 AND txn_type IN ('CARD_PURCHASE', 'BILL_PAYMENT')),
                             count(*) FILTER (WHERE merchant_key <> -1 AND txn_type IN ('TRANSFER_OUT', 'ATM_WITHDRAWAL'))
                      FROM mart.fact_transactions""")[0]
    assert rows == (0, 0)


def test_date_dimension_covers_every_fact_date(mart):
    missing = q(mart, """SELECT count(*) FROM (SELECT date_key FROM mart.fact_transactions
                                               UNION SELECT date_key FROM mart.fact_logins
                                               UNION SELECT reported_date_key FROM mart.fact_fraud_claims) k
                         LEFT JOIN mart.dim_date d USING (date_key) WHERE d.date_key IS NULL""")
    assert missing[0][0] == 0


# ---------------------------------------------------------------- privacy
def test_no_personal_data_in_the_customer_dimension(mart):
    columns = {r[0] for r in q(mart, """SELECT column_name FROM information_schema.columns
                                        WHERE table_schema = 'mart' AND table_name = 'dim_customer'""")}
    personal = {"first_name", "last_name", "email", "phone", "street_address", "postcode", "date_of_birth"}
    assert not columns & personal


# ---------------------------------------------------------------- fraud labels = what the bank knows
@pytest.fixture(scope="module")
def labelled(mart, small_world):
    facts = frame(mart, "SELECT txn_id, status, fraud_label, claim_status, device_known FROM mart.fact_transactions")
    truth = small_world["labels"]
    victims = truth[truth["fraud_role"] == "VICTIM"]
    return facts.merge(victims[["txn_id", "fraud_type"]], on="txn_id", how="left")


def test_every_fraud_label_is_real_fraud(labelled):
    assert labelled.loc[labelled["fraud_label"] == 1, "fraud_type"].notna().all()


def test_labels_are_incomplete_like_in_a_real_bank(labelled):
    real_fraud = labelled[labelled["fraud_type"].notna() & (labelled["status"] == "SUCCESS")]
    coverage = real_fraud["fraud_label"].mean()
    assert 0 < coverage < 1, "some real fraud must still be unknown to the bank"


def test_labels_match_confirmed_claims_exactly(mart):
    mismatch = q(mart, """SELECT count(*) FROM mart.fact_transactions f
                          WHERE (f.fraud_label = 1) <> EXISTS (
                              SELECT 1 FROM mart.fact_fraud_claims c
                              WHERE c.txn_id = f.txn_id AND c.claim_status = 'CONFIRMED'
                                AND c.claim_type <> 'MULE_ACCOUNT')""")
    assert mismatch[0][0] == 0


def test_confirmed_fraud_has_a_confirmation_date(mart):
    rows = q(mart, """SELECT count(*) FILTER (WHERE fraud_label = 1 AND confirmed_date IS NULL),
                             count(*) FILTER (WHERE fraud_label = 0 AND confirmed_date IS NOT NULL)
                      FROM mart.fact_transactions""")[0]
    assert rows == (0, 0)


def test_account_takeover_comes_from_unknown_devices(labelled):
    ato = labelled[labelled["fraud_type"] == "ACCOUNT_TAKEOVER"]
    assert (ato["device_known"] == 0).all()
    scams = labelled[(labelled["fraud_type"] == "SCAM") & labelled["device_known"].notna()]
    assert (scams["device_known"] == 1).all()        # scam victims use their own device


def test_mule_flags_only_on_real_mules(mart, small_world):
    flagged = {r[0] for r in q(mart, "SELECT account_id FROM mart.dim_account WHERE is_confirmed_mule = 1")}
    assert flagged and flagged <= {m["account_id"] for m in small_world["mules"]}


# ---------------------------------------------------------------- documentation
def test_every_mart_table_and_column_is_documented(mart):
    assert undocumented(mart) == []


def test_data_dictionary_lists_every_table(mart):
    text = render(*describe(mart))
    assert all(f"## {t}" in text for t in MART_TABLES)
    assert "UNDOCUMENTED" not in text


# ---------------------------------------------------------------- export for SAS
def test_export_is_complete_and_sas_friendly(mart, tmp_path):
    manifest = export_mart(mart, tmp_path)
    assert {f["file"] for f in manifest["files"]} == {f"{t}.csv" for t in MART_TABLES}
    saved = json.loads((tmp_path / "export_manifest.json").read_text())
    for entry in saved["files"]:
        df = pd.read_csv(tmp_path / entry["file"], nrows=5)
        assert all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", c) for c in df.columns)
        assert sum(1 for _ in open(tmp_path / entry["file"])) - 1 == entry["rows"]
    flags = pd.read_csv(tmp_path / "fact_transactions.csv", usecols=["device_known"], dtype=str)["device_known"]
    assert flags.dropna().isin(["0", "1"]).all()     # integers stay integers ("1", never "1.0")


# ---------------------------------------------------------------- re-running
def test_rebuilding_the_mart_gives_the_same_result(mart, delivered):
    before = mart_summary(mart)
    build_mart(mart, delivered["cfg"])
    assert mart_summary(mart) == before


# ---------------------------------------------------------------- pure check (no database)
def test_sql_splitter_respects_quotes_and_comments():
    script = "SELECT 1; -- a comment; with a semicolon\nCOMMENT ON X IS 'a; b -- not a comment';\nSELECT 'it''s; fine'"
    assert split_sql(script) == ["SELECT 1", "COMMENT ON X IS 'a; b -- not a comment'", "SELECT 'it''s; fine'"]
