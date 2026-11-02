"""
Generate fraud reports (fraud operations system), one CSV file per day.

This is how a bank actually LEARNS about fraud, with all the real-world mess:
  - Delay:        victims report days or weeks after the fraud (card fraud often only on the statement)
  - Unreported:   some victims never report (especially scam victims)
  - Censoring:    fraud reported after the data ends is unknown to us
  - Partial:      customers overlook the tiny card "test" charge
  - False claims: customers dispute their own genuine transactions (rejected after investigation)
  - Updates:      each claim arrives as NEW (status OPEN), then an UPDATE when the investigation ends
  - Mule flags:   confirmed cases lead investigators to flag the receiving mule accounts

Inputs:  landing transactions + accounts, ground truth (to know which transactions were fraud)
Output:  data/landing/fraud_reports/fraud_reports_<YYYYMMDD>.csv   (a file every day, even if empty)
"""
import logging
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from generate_customers import load_config, weighted_choice
from utils import read_daily_files

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

COLUMNS = ["report_id", "record_type", "event_date", "bank_case_ref", "report_channel", "customer_id",
           "account_id", "txn_id", "amount", "claim_type", "status"]
CLAIM_TYPE = {"ACCOUNT_TAKEOVER": "UNAUTHORISED_TRANSFER", "SCAM": "SCAM_PAYMENT",
              "CARD_NOT_PRESENT": "UNAUTHORISED_CARD"}


def generate_fraud_reports(txns, labels, accounts, cfg) -> tuple[pd.DataFrame, dict]:
    r = cfg["fraud_reports"]
    rng = random.Random(cfg["project"]["seed"] + 7)
    start = date.fromisoformat(cfg["simulation"]["start_date"])
    end = start + timedelta(days=cfg["simulation"]["n_days"])
    test_max = cfg["fraud"]["card_not_present"]["test_amount"][1]

    t = txns.merge(labels, on="txn_id", how="left")
    t["day"] = pd.to_datetime(t["txn_timestamp"]).dt.date
    t["customer_id"] = t["account_id"].map(accounts.set_index("account_id")["customer_id"])

    rows, stats = [], {"reported": 0, "never_reported": 0, "reported_after_data_ends": 0}
    counters = {"report": 0, "case": 0}

    def add_claim(txn, report_day, resolve_day, case_ref, channel, claim_type, outcome):
        counters["report"] += 1
        base = {"report_id": f"RPT{counters['report']:07d}", "bank_case_ref": case_ref,
                "report_channel": channel, "customer_id": txn.customer_id, "account_id": txn.account_id,
                "txn_id": txn.txn_id, "amount": txn.amount, "claim_type": claim_type}
        rows.append({**base, "record_type": "NEW", "event_date": report_day, "status": "OPEN"})
        if resolve_day < end:                                   # investigation finished within our data
            rows.append({**base, "record_type": "UPDATE", "event_date": resolve_day, "status": outcome})

    # 1. Real fraud, reported by victims (or not)
    victim = t[(t["fraud_role"] == "VICTIM") & (t["status"] == "SUCCESS")]
    flagged_mules = set()
    for case_id, case in victim.groupby("case_id"):
        fraud_type = case["fraud_type"].iloc[0]
        if rng.random() >= r["report_probability"][fraud_type]:
            stats["never_reported"] += 1
            continue
        report_day = case["day"].max() + timedelta(days=rng.randint(*r["delay_days"][fraud_type]))
        if report_day >= end:
            stats["reported_after_data_ends"] += 1            # it happened, but we can't know yet
            continue
        stats["reported"] += 1
        counters["case"] += 1
        case_ref = f"FR-{counters['case']:06d}"
        resolve_day = report_day + timedelta(days=rng.randint(*r["investigation_days"]))
        channel = weighted_choice(rng, r["channels"][fraud_type])

        for txn in case.itertuples(index=False):
            overlooked = (fraud_type == "CARD_NOT_PRESENT" and txn.amount <= test_max
                          and rng.random() < r["missed_test_charge_share"])
            if not overlooked:
                add_claim(txn, report_day, resolve_day, case_ref, channel, CLAIM_TYPE[fraud_type], "CONFIRMED_FRAUD")

        # Investigators trace where the money went and flag mule accounts
        if resolve_day < end:
            inbound = t[(t["case_id"] == case_id) & (t["fraud_role"] == "MULE_INBOUND")]
            for mule in inbound.drop_duplicates("account_id").itertuples(index=False):
                if mule.account_id in flagged_mules:
                    continue
                flagged_mules.add(mule.account_id)
                counters["report"] += 1
                rows.append({"report_id": f"RPT{counters['report']:07d}", "record_type": "NEW",
                             "event_date": resolve_day, "bank_case_ref": case_ref, "report_channel": "INVESTIGATION",
                             "customer_id": mule.customer_id, "account_id": mule.account_id, "txn_id": mule.txn_id,
                             "amount": mule.amount, "claim_type": "MULE_ACCOUNT", "status": "CONFIRMED_FRAUD"})

    # 2. False claims: customers disputing their own genuine transactions
    genuine = t[t["case_id"].isna() & (t["status"] == "SUCCESS")
                & ((t["channel"] == "ECOM") | (t["txn_type"] == "TRANSFER_OUT"))]
    for txn in genuine.sample(n=min(r["false_claims"], len(genuine)), random_state=rng.randint(0, 10**6)) \
            .itertuples(index=False):
        report_day = txn.day + timedelta(days=rng.randint(2, 30))
        if report_day >= end:
            continue
        counters["case"] += 1
        claim_type = "UNAUTHORISED_CARD" if txn.txn_type == "CARD_PURCHASE" else "UNAUTHORISED_TRANSFER"
        channel = weighted_choice(rng, r["channels"]["CARD_NOT_PRESENT" if txn.txn_type == "CARD_PURCHASE"
                                                    else "ACCOUNT_TAKEOVER"])
        resolve_day = report_day + timedelta(days=rng.randint(*r["investigation_days"]))
        add_claim(txn, report_day, resolve_day, f"FR-{counters['case']:06d}", channel, claim_type, "REJECTED")

    return number_in_date_order(pd.DataFrame(rows, columns=COLUMNS)), stats


def number_in_date_order(df: pd.DataFrame) -> pd.DataFrame:
    """Real systems number cases and reports in the order they are created."""
    new = df[df["record_type"] == "NEW"].sort_values(["event_date", "report_id"], kind="stable")
    case_order = new.drop_duplicates("bank_case_ref")["bank_case_ref"]
    case_map = {old: f"FR-{i:06d}" for i, old in enumerate(case_order, 1)}
    report_map = {old: f"RPT{i:07d}" for i, old in enumerate(new["report_id"], 1)}
    df = df.assign(bank_case_ref=df["bank_case_ref"].map(case_map), report_id=df["report_id"].map(report_map))
    return df.sort_values(["event_date", "record_type", "report_id"], kind="stable").reset_index(drop=True)


def write_daily_files(df: pd.DataFrame, cfg: dict, out_dir: Path) -> int:
    """A file for EVERY day, even empty ones: an empty file means 'no reports', a missing file means a problem."""
    out_dir.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(cfg["simulation"]["start_date"])
    for offset in range(cfg["simulation"]["n_days"]):
        day = start + timedelta(days=offset)
        df[df["event_date"] == day].to_csv(out_dir / f"fraud_reports_{day:%Y%m%d}.csv", index=False)
    return cfg["simulation"]["n_days"]


def main():
    cfg = load_config()
    snap = cfg["project"]["snapshot_date"].replace("-", "")
    txns = read_daily_files(cfg["paths"]["transactions"])
    labels = pd.read_csv(Path(cfg["paths"]["ground_truth"]) / "transaction_labels.csv")
    accounts = pd.read_csv(Path(cfg["paths"]["reference"]) / f"accounts_{snap}.csv")

    df, stats = generate_fraud_reports(txns, labels, accounts, cfg)
    n_files = write_daily_files(df, cfg, Path(cfg["paths"]["fraud_reports"]))

    new = df[df["record_type"] == "NEW"]
    logger.info("Created %s report records in %s daily files", f"{len(df):,}", n_files)
    logger.info("Fraud cases reported: %s | never reported: %s | reported after data ends: %s",
                stats["reported"], stats["never_reported"], stats["reported_after_data_ends"])
    logger.info("Claims by type: %s", new["claim_type"].value_counts().to_dict())
    logger.info("Final statuses: %s", df.drop_duplicates("report_id", keep="last")["status"].value_counts().to_dict())


if __name__ == "__main__":
    main()
