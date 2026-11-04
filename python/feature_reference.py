"""
Reference implementation of the fraud features: an independent Python version of sas/03_features.sas.

Banks validate important model inputs with a PARALLEL RUN: the same definitions are built twice,
independently, and the results must agree. If SAS and Python produce the same totals for every
feature, both are almost certainly correct.

Every feature uses only information available BEFORE the transaction (no peeking into the future):
  account history     minutes_since_prev_txn, txns_prev_1h, txns_prev_24h, declined_prev_24h,
                      debit_amount_prev_24h, prior_debit_count, prior_avg_debit, amount_to_avg,
                      drain_ratio
  first time seen     new_payee, new_merchant, new_device_for_account
  customer logins     logins_prev_1h, failed_logins_prev_1h, foreign_logins_prev_1h,
  (last 60 minutes)   unknown_device_logins_prev_1h
  calendar            is_night, is_weekend

Usage:
    python3 python/feature_reference.py                                         # write Python totals
    python3 python/feature_reference.py --compare reports/feature_totals_sas.csv   # compare with SAS
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

EXPORT_DIR = Path("data/exports")
TOTALS_PATH = Path("reports/feature_totals_python.csv")
HOUR, DAY = 3600, 86400
KEY_SPACING = 10**11            # keeps every account's (or customer's) timestamps in its own number range

FEATURES = [
    "minutes_since_prev_txn", "txns_prev_1h", "txns_prev_24h", "declined_prev_24h",
    "debit_amount_prev_24h", "prior_debit_count", "prior_avg_debit", "amount_to_avg", "drain_ratio",
    "new_payee", "new_merchant", "new_device_for_account",
    "logins_prev_1h", "failed_logins_prev_1h", "foreign_logins_prev_1h", "unknown_device_logins_prev_1h",
    "is_night", "is_weekend",
]


def to_seconds(timestamps: pd.Series) -> np.ndarray:
    """Seconds since 1970, whatever unit pandas stores internally (pandas 3 uses microseconds)."""
    return ((pd.to_datetime(timestamps) - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).to_numpy()


def window_counts(keys: np.ndarray, values: np.ndarray, width: int) -> tuple[np.ndarray, np.ndarray]:
    """For each row i (keys sorted): how many EARLIER rows are within `width` seconds, and the sum of their values.
    keys = group * KEY_SPACING + seconds, so a window never reaches into another group."""
    left = np.searchsorted(keys, keys - width, side="left")
    prefix = np.concatenate([[0.0], np.cumsum(values)])
    position = np.arange(len(keys))
    return position - left, prefix[position] - prefix[left]


def build_features(tx: pd.DataFrame, logins: pd.DataFrame, dates: pd.DataFrame) -> pd.DataFrame:
    tx = tx.sort_values(["account_key", "txn_timestamp", "txn_id"], kind="stable").reset_index(drop=True)
    seconds = to_seconds(tx["txn_timestamp"])
    keys = tx["account_key"].to_numpy(np.int64) * KEY_SPACING + seconds
    is_debit = tx["direction"].eq("DR")
    success_debit_amount = np.where(is_debit & tx["status"].eq("SUCCESS"), tx["amount"], 0.0)
    declined = tx["status"].eq("DECLINED").to_numpy(float)

    f = tx[["txn_id"]].copy()
    previous_seconds = pd.Series(seconds).groupby(tx["account_key"]).shift()
    f["minutes_since_prev_txn"] = (seconds - previous_seconds) / 60
    f["txns_prev_1h"], _ = window_counts(keys, np.zeros(len(tx)), HOUR)
    f["txns_prev_24h"], f["declined_prev_24h"] = window_counts(keys, declined, DAY)
    _, f["debit_amount_prev_24h"] = window_counts(keys, success_debit_amount, DAY)

    success_debit = (is_debit & tx["status"].eq("SUCCESS")).astype(int)
    by_account = tx["account_key"]
    f["prior_debit_count"] = success_debit.groupby(by_account).cumsum() - success_debit
    prior_sum = pd.Series(success_debit_amount).groupby(by_account).cumsum() - success_debit_amount
    f["prior_avg_debit"] = (prior_sum / f["prior_debit_count"]).where(f["prior_debit_count"] > 0)
    f["amount_to_avg"] = np.where(is_debit & (f["prior_debit_count"] > 0), tx["amount"] / f["prior_avg_debit"], np.nan)
    f["drain_ratio"] = np.where(is_debit & (tx["balance_before"] > 0), tx["amount"] / tx["balance_before"], np.nan)

    def first_time(mask: pd.Series, columns: list[str]) -> pd.Series:
        """1 the first time this account uses this payee/merchant/device, 0 afterwards; NULL where not applicable."""
        result = pd.Series(np.nan, index=tx.index)
        rows = tx[mask]
        result[mask] = (rows.groupby(columns).cumcount() == 0).astype(float)
        return result

    f["new_payee"] = first_time(tx["txn_type"].eq("TRANSFER_OUT") & tx["counterparty_account"].notna(),
                                ["account_key", "counterparty_account"])
    f["new_merchant"] = first_time(tx["txn_type"].eq("CARD_PURCHASE"), ["account_key", "merchant_key"])
    f["new_device_for_account"] = first_time(tx["channel"].isin(["MOBILE", "WEB"]) & tx["device_id"].notna(),
                                             ["account_key", "device_id"])

    # logins by the same customer in the 60 minutes up to and including the transaction time
    lg = logins.sort_values(["customer_key", "event_timestamp", "event_id"], kind="stable").reset_index(drop=True)
    login_keys = lg["customer_key"].to_numpy(np.int64) * KEY_SPACING + to_seconds(lg["event_timestamp"])
    txn_keys = tx["customer_key"].to_numpy(np.int64) * KEY_SPACING + seconds
    high = np.searchsorted(login_keys, txn_keys, side="right")
    low = np.searchsorted(login_keys, txn_keys - HOUR, side="left")
    f["logins_prev_1h"] = high - low
    for name, flag in [("failed_logins_prev_1h", lg["is_failed"].eq(1)),
                       ("foreign_logins_prev_1h", lg["is_foreign_ip"].eq(1)),
                       ("unknown_device_logins_prev_1h", lg["device_known"].eq(0))]:
        prefix = np.concatenate([[0], np.cumsum(flag.to_numpy(int))])
        f[name] = prefix[high] - prefix[low]

    f["is_night"] = tx["hour_of_day"].between(0, 5).astype(int)
    f["is_weekend"] = tx["date_key"].map(dates.set_index("date_key")["is_weekend"])
    return f


def feature_totals(features: pd.DataFrame) -> pd.DataFrame:
    """Two numbers per feature: how many rows have a value, and the sum. SAS prints the same table."""
    rows = [{"feature": name, "n": int(features[name].notna().sum()),
             "total": round(float(features[name].sum()), 4)} for name in FEATURES]
    return pd.DataFrame(rows)


def compare(python: pd.DataFrame, sas: pd.DataFrame) -> pd.DataFrame:
    sas = sas.rename(columns=str.lower)
    merged = python.merge(sas, on="feature", how="outer", suffixes=("_python", "_sas"))
    close = np.isclose(merged["total_python"], merged["total_sas"], rtol=1e-9, atol=1e-3)
    merged["result"] = np.where((merged["n_python"] == merged["n_sas"]) & close, "MATCH", "DIFFERENT")
    return merged


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--compare", type=Path, help="feature totals CSV exported by sas/03_features.sas")
    args = parser.parse_args()

    tx = pd.read_csv(EXPORT_DIR / "fact_transactions.csv", parse_dates=["txn_timestamp"])
    logins = pd.read_csv(EXPORT_DIR / "fact_logins.csv", parse_dates=["event_timestamp"])
    dates = pd.read_csv(EXPORT_DIR / "dim_date.csv")
    totals = feature_totals(build_features(tx, logins, dates))
    TOTALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    totals.to_csv(TOTALS_PATH, index=False)
    logger.info("Python feature totals written to %s", TOTALS_PATH)

    if not args.compare:
        print(totals.to_string(index=False))
        return
    result = compare(totals, pd.read_csv(args.compare))
    print(result[["feature", "n_python", "n_sas", "total_python", "total_sas", "result"]].to_string(index=False))
    different = int((result["result"] != "MATCH").sum())
    if different:
        logger.error("%s of %s features DIFFER between SAS and Python", different, len(result))
        sys.exit(1)
    logger.info("Parallel run passed: all %s features match between SAS and Python", len(result))


if __name__ == "__main__":
    main()
