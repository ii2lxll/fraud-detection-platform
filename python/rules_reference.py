"""
Reference implementation of the rules baseline: the same rules as sas/04_rules_baseline.sas, in Python.

Used for the parallel run: both must produce the same alerts and fraud caught for every rule.
Because Python can also read the simulator's answer key, it shows one more thing SAS cannot:
how many "false" alerts were really fraud that nobody has reported yet.

Usage:
    python3 python/rules_reference.py                                          # results + answer-key view
    python3 python/rules_reference.py --compare reports/rules_results_sas.csv  # parallel run
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from feature_reference import EXPORT_DIR, build_features

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

MATURITY_DAYS = 30
RESULTS_PATH = Path("reports/rules_results_python.csv")
GROUND_TRUTH = Path("data/_ground_truth/transaction_labels.csv")


def _transfer(f):
    return f["txn_type"].eq("TRANSFER_OUT")


def _card(f):
    return f["txn_type"].eq("CARD_PURCHASE")


# name -> (description, rule). Comparisons with missing values are False, exactly as in SAS.
RULES = {
    "unknown_device_new_payee": ("Transfer from an unknown device to a new payee",
                                 lambda f: _transfer(f) & f["device_known"].eq(0) & f["new_payee"].eq(1)),
    "login_attack": ("Transfer after 2+ failed logins and a foreign login in the hour before",
                     lambda f: _transfer(f) & (f["failed_logins_prev_1h"] >= 2) & (f["foreign_logins_prev_1h"] >= 1)),
    "night_drain": ("Night-time transfer taking 30%+ of the balance",
                    lambda f: _transfer(f) & f["is_night"].eq(1) & (f["drain_ratio"] >= 0.3)),
    "large_new_payee": ("New payee and 5x the usual amount",
                        lambda f: _transfer(f) & f["new_payee"].eq(1) & (f["amount_to_avg"] >= 5)),
    "risky_merchant_burst": ("Card burst: new high-risk merchant and 2+ transactions in the hour before",
                             lambda f: _card(f) & f["merchant_risk_tier"].eq("HIGH") & f["new_merchant"].eq(1)
                             & (f["txns_prev_1h"] >= 2)),
    "foreign_risky_spike": ("Card: foreign high-risk merchant and 3x the usual amount",
                            lambda f: _card(f) & f["merchant_risk_tier"].eq("HIGH") & f["merchant_is_foreign"].eq(1)
                            & (f["amount_to_avg"] >= 3)),
}


def feature_table(export_dir: Path = EXPORT_DIR) -> pd.DataFrame:
    """The Python equivalent of FRAUD.TXN_FEATURES: features plus the context the rules need."""
    tx = pd.read_csv(export_dir / "fact_transactions.csv", parse_dates=["txn_timestamp"])
    logins = pd.read_csv(export_dir / "fact_logins.csv", parse_dates=["event_timestamp"])
    dates = pd.read_csv(export_dir / "dim_date.csv", parse_dates=["calendar_date"])
    merchants = pd.read_csv(export_dir / "dim_merchant.csv")[["merchant_key", "risk_tier", "is_foreign"]]
    features = build_features(tx, logins, dates)
    table = tx.merge(features, on="txn_id").merge(
        merchants.rename(columns={"risk_tier": "merchant_risk_tier", "is_foreign": "merchant_is_foreign"}),
        on="merchant_key", how="left")
    table["in_scope"] = table["txn_type"].isin(["TRANSFER_OUT", "CARD_PURCHASE"]).astype(int)
    table["days_to_data_end"] = (dates["calendar_date"].max() - table["txn_timestamp"].dt.normalize()).dt.days
    return table


def evaluation_population(table: pd.DataFrame, maturity_days: int = MATURITY_DAYS) -> pd.DataFrame:
    keep = table["in_scope"].eq(1) & table["status"].eq("SUCCESS") & (table["days_to_data_end"] >= maturity_days)
    return table[keep]


def apply_rules(population: pd.DataFrame) -> pd.DataFrame:
    hits = pd.DataFrame({name: rule(population).fillna(False).astype(bool)
                         for name, (_, rule) in RULES.items()}, index=population.index)
    hits["any_rule"] = hits.any(axis=1)
    return hits


def measure(population: pd.DataFrame, hits: pd.DataFrame, label: str = "fraud_label") -> pd.DataFrame:
    fraud = population[label].eq(1)
    days = population["date_key"].nunique()
    total_fraud, total_amount = fraud.sum(), population.loc[fraud, "amount"].sum()
    rows = []
    for rule in hits.columns:
        hit = hits[rule]
        alerts, caught = int(hit.sum()), int((hit & fraud).sum())
        amount = float(population.loc[hit & fraud, "amount"].sum())
        rows.append({"rule": rule, "alerts": alerts, "alerts_per_day": alerts / days, "fraud_caught": caught,
                     "precision": caught / alerts if alerts else np.nan, "recall": caught / total_fraud,
                     "fraud_amount_caught": round(amount, 2), "value_detection": amount / total_amount})
    return pd.DataFrame(rows)


def compare(python: pd.DataFrame, sas: pd.DataFrame) -> pd.DataFrame:
    sas = sas.rename(columns=str.lower)
    merged = python[["rule", "alerts", "fraud_caught", "fraud_amount_caught"]].merge(
        sas, on="rule", how="outer", suffixes=("_python", "_sas"))
    same = ((merged["alerts_python"] == merged["alerts_sas"])
            & (merged["fraud_caught_python"] == merged["fraud_caught_sas"])
            & np.isclose(merged["fraud_amount_caught_python"], merged["fraud_amount_caught_sas"], atol=0.01))
    merged["result"] = np.where(same, "MATCH", "DIFFERENT")
    return merged


def main():
    parser = argparse.ArgumentParser(description="Rules baseline (Python reference)")
    parser.add_argument("--compare", type=Path, help="rules results CSV exported by sas/04_rules_baseline.sas")
    args = parser.parse_args()

    population = evaluation_population(feature_table())
    hits = apply_rules(population)
    results = measure(population, hits)
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(RESULTS_PATH, index=False)

    pd.set_option("display.width", 200)
    if args.compare:
        comparison = compare(results, pd.read_csv(args.compare))
        print(comparison.to_string(index=False))
        different = int((comparison["result"] != "MATCH").sum())
        if different:
            logger.error("%s of %s rules DIFFER between SAS and Python", different, len(comparison))
            sys.exit(1)
        logger.info("Parallel run passed: all %s rules match between SAS and Python", len(comparison))
        return

    print(results.round(3).to_string(index=False))
    if GROUND_TRUTH.exists():                      # only the simulator knows the full truth
        labels = pd.read_csv(GROUND_TRUTH)
        victims = set(labels.loc[labels["fraud_role"] == "VICTIM", "txn_id"])
        truth = population.assign(true_fraud=population["txn_id"].isin(victims).astype(int))
        true_results = measure(truth, hits, label="true_fraud")
        view = results[["rule", "precision"]].merge(true_results[["rule", "precision", "recall"]], on="rule",
                                                    suffixes=("_bank_labels", "_true"))
        print("\nAgainst the answer key (impossible in a real bank): some 'false' alerts are unreported fraud")
        print(view.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
