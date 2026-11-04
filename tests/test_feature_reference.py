"""
Tests for the reference feature implementation (python/feature_reference.py).

The fast version uses vectorised tricks (sorted keys, binary search, prefix sums). These tests compare it
with a deliberately SLOW, obvious version (plain loops over every earlier event) on random data:
if both agree, the fast version is right. The SAS program is then checked against the fast version
with a parallel run.
"""
import re

import numpy as np
import pandas as pd
import pytest

from feature_reference import FEATURES, build_features, compare, feature_totals


@pytest.fixture(scope="module")
def random_world():
    rng = np.random.default_rng(7)
    n, start = 400, pd.Timestamp("2026-06-01")
    seconds = np.sort(rng.integers(0, 5 * 86400, n))
    seconds[10] = seconds[11]                                      # a tie: two transactions in the same second
    tx = pd.DataFrame({
        "txn_id": [f"T{i:04d}" for i in range(n)],
        "account_key": rng.integers(1, 6, n),
        "txn_timestamp": start + pd.to_timedelta(seconds, unit="s"),
        "txn_type": rng.choice(["TRANSFER_OUT", "CARD_PURCHASE", "TRANSFER_IN", "ATM_WITHDRAWAL"], n),
        "status": rng.choice(["SUCCESS", "DECLINED"], n, p=[0.85, 0.15]),
        "amount": rng.integers(1, 50_000, n) / 100,
        "balance_before": rng.choice([0, 100, 2500, 9000], n).astype(float),
        "counterparty_account": rng.choice(["P1", "P2", "P3", None], n),
        "merchant_key": rng.integers(1, 5, n),
        "channel": rng.choice(["MOBILE", "WEB", "POS"], n),
        "device_id": rng.choice(["D1", "D2", None], n),
    })
    tx["direction"] = np.where(tx["txn_type"] == "TRANSFER_IN", "CR", "DR")
    tx["customer_key"] = tx["account_key"] % 3                      # several accounts share a customer
    tx["hour_of_day"] = tx["txn_timestamp"].dt.hour
    tx["date_key"] = tx["txn_timestamp"].dt.strftime("%Y%m%d").astype(int)

    m = 300
    lseconds = np.sort(rng.integers(0, 5 * 86400, m))
    lseconds[5] = seconds[20]                                     # a login at exactly a transaction's second
    logins = pd.DataFrame({
        "event_id": [f"L{i:04d}" for i in range(m)],
        "customer_key": rng.integers(0, 3, m),
        "event_timestamp": start + pd.to_timedelta(lseconds, unit="s"),
        "is_failed": rng.choice([0, 1], m, p=[0.8, 0.2]),
        "is_foreign_ip": rng.choice([0, 1], m, p=[0.9, 0.1]),
        "device_known": rng.choice([0, 1, np.nan], m, p=[0.1, 0.8, 0.1]),
    })
    dates = pd.DataFrame({"date_key": sorted(tx["date_key"].unique())})
    dates["is_weekend"] = pd.to_datetime(dates["date_key"].astype(str)).dt.dayofweek.ge(5).astype(int)
    return tx, logins, dates


def slow_features(tx, logins, dates):
    """The obvious, slow way: for every transaction, look back through every earlier event one by one."""
    tx = tx.sort_values(["account_key", "txn_timestamp", "txn_id"], kind="stable").reset_index(drop=True)
    weekend = dates.set_index("date_key")["is_weekend"]
    rows = []
    for i, t in tx.iterrows():
        prev = tx.iloc[:i][tx.iloc[:i]["account_key"] == t.account_key]
        age = (t.txn_timestamp - prev["txn_timestamp"]).dt.total_seconds()
        ok_debits = prev[(prev["direction"] == "DR") & (prev["status"] == "SUCCESS")]
        in_day = age <= 86400
        avg = ok_debits["amount"].mean() if len(ok_debits) else np.nan
        debit = t.direction == "DR"

        def first_time(applies, same):
            return np.nan if not applies else float(not same.any())

        payee_rows = prev[(prev["txn_type"] == "TRANSFER_OUT") & prev["counterparty_account"].notna()]
        recent_logins = logins[(logins["customer_key"] == t.customer_key)
                               & (logins["event_timestamp"] <= t.txn_timestamp)
                               & ((t.txn_timestamp - logins["event_timestamp"]).dt.total_seconds() <= 3600)]
        rows.append({
            "txn_id": t.txn_id,
            "minutes_since_prev_txn": age.iloc[-1] / 60 if len(prev) else np.nan,
            "txns_prev_1h": int((age <= 3600).sum()),
            "txns_prev_24h": int(in_day.sum()),
            "declined_prev_24h": int((in_day & (prev["status"] == "DECLINED")).sum()),
            "debit_amount_prev_24h": ok_debits.loc[in_day[ok_debits.index], "amount"].sum(),
            "prior_debit_count": len(ok_debits),
            "prior_avg_debit": avg,
            "amount_to_avg": t.amount / avg if debit and len(ok_debits) else np.nan,
            "drain_ratio": t.amount / t.balance_before if debit and t.balance_before > 0 else np.nan,
            "new_payee": first_time(t.txn_type == "TRANSFER_OUT" and pd.notna(t.counterparty_account),
                                    payee_rows["counterparty_account"] == t.counterparty_account),
            "new_merchant": first_time(t.txn_type == "CARD_PURCHASE",
                                       (prev["txn_type"] == "CARD_PURCHASE") & (prev["merchant_key"] == t.merchant_key)),
            "new_device_for_account": first_time(t.channel in ("MOBILE", "WEB") and pd.notna(t.device_id),
                                                 prev["channel"].isin(["MOBILE", "WEB"]) & (prev["device_id"] == t.device_id)),
            "logins_prev_1h": len(recent_logins),
            "failed_logins_prev_1h": int((recent_logins["is_failed"] == 1).sum()),
            "foreign_logins_prev_1h": int((recent_logins["is_foreign_ip"] == 1).sum()),
            "unknown_device_logins_prev_1h": int((recent_logins["device_known"] == 0).sum()),
            "is_night": int(0 <= t.hour_of_day <= 5),
            "is_weekend": int(weekend[t.date_key]),
        })
    return pd.DataFrame(rows)


def test_fast_features_match_the_slow_obvious_version(random_world):
    fast = build_features(*random_world).set_index("txn_id").sort_index()
    slow = slow_features(*random_world).set_index("txn_id").sort_index()
    for name in FEATURES:
        pd.testing.assert_series_equal(fast[name].astype(float), slow[name].astype(float),
                                       check_names=False, rtol=1e-9, obj=name)


def test_features_never_use_the_future(random_world):
    """Changing everything AFTER a transaction must not change that transaction's features."""
    tx, logins, dates = random_world
    cut = tx["txn_timestamp"].sort_values().iloc[200]
    full = build_features(tx, logins, dates).set_index("txn_id")
    past_only = build_features(tx[tx["txn_timestamp"] <= cut], logins[logins["event_timestamp"] <= cut], dates)
    past_only = past_only.set_index("txn_id")
    pd.testing.assert_frame_equal(full.loc[past_only.index, FEATURES].astype(float), past_only[FEATURES].astype(float))


def test_compare_flags_any_difference():
    python = pd.DataFrame({"feature": ["a", "b"], "n": [10, 10], "total": [5.0, 7.0]})
    sas = pd.DataFrame({"FEATURE": ["a", "b"], "N": [10, 9], "TOTAL": [5.0, 7.0]})   # SAS exports may be upper case
    result = compare(python, sas).set_index("feature")["result"]
    assert result["a"] == "MATCH" and result["b"] == "DIFFERENT"


def test_totals_cover_every_feature(random_world):
    totals = feature_totals(build_features(*random_world))
    assert list(totals["feature"]) == FEATURES


# ---------------------------------------------------------------- the SAS program must use the same feature list
def sas_program() -> str:
    text = open("sas/03_features.sas", encoding="utf-8").read()
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)        # without /* comments */


def test_sas_totals_list_the_same_features_in_the_same_order():
    totals_step = sas_program().split("stackodsoutput;", 1)[1]
    var_list = re.search(r"\bvar\b(.*?);", totals_step, flags=re.DOTALL).group(1).split()
    assert var_list == FEATURES


def test_sas_program_creates_every_feature():
    program = sas_program()
    for name in FEATURES:
        assert re.search(rf"\b{name}\b\s*=|as {name}\b|\bd\.{name}\b", program), f"SAS never creates {name}"
