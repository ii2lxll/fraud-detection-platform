"""
Data quality report: how many of the deliberately planted problems did the pipeline catch?

Compares what the pipeline FOUND (staging.dq_findings, audit tables) with what was PLANTED
(data/_ground_truth/dq_issues.csv, written by deliver_files.py and never read by the pipeline).

For each kind of problem:
    planted          unique records that really had the problem
    detected         of those, how many the pipeline found
    recall           detected / planted         (did we catch everything?)
    false positives  records flagged that were actually fine (did we cry wolf?)

    python3 python/dq_report.py      ->  reports/data_quality_report.md
"""
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from build_staging import staging_summary
from generate_customers import load_config
from utils import read_daily_files
from warehouse import Warehouse

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

REPORT_PATH = Path("reports/data_quality_report.md")

# (feed, problem planted by deliver_files.py) -> finding name the pipeline uses
ROW_LEVEL = {
    ("transactions", "DUPLICATE"): "DUPLICATE",
    ("transactions", "MISSING_AMOUNT"): "MISSING_AMOUNT",
    ("transactions", "AMOUNT_AS_TEXT"): "AMOUNT_TEXT_FORMAT",
    ("transactions", "MESSY_TXN_TYPE"): "NONSTANDARD_TXN_TYPE",
    ("transactions", "US_DATE_FORMAT"): "NONSTANDARD_TIMESTAMP",
    ("transactions", "ORPHAN_ACCOUNT"): "UNKNOWN_ACCOUNT",
    ("transactions", "NEGATIVE_AMOUNT"): "NEGATIVE_AMOUNT",
    ("transactions", "CURRENCY_VARIANT"): "NONSTANDARD_CURRENCY",
    ("transactions", "LATE_ARRIVING_ROW"): "LATE_ARRIVING",
    ("logins", "DUPLICATE"): "DUPLICATE",
    ("logins", "EPOCH_TIMESTAMP"): "NONSTANDARD_TIMESTAMP",
    ("logins", "MISSING_DEVICE"): "MISSING_DEVICE",
    ("logins", "LOWERCASE_COUNTRY"): "NONSTANDARD_COUNTRY",
    ("fraud_reports", "DUPLICATE"): "DUPLICATE",
    ("fraud_reports", "MISSING_AMOUNT"): "MISSING_AMOUNT",
    ("fraud_reports", "SLASH_DATE_FORMAT"): "NONSTANDARD_DATE",
    ("customers", "DUPLICATE_CUSTOMER"): "DUPLICATE_CUSTOMER",
    ("customers", "MISSING_STATE"): "MISSING_STATE",
    ("customers", "INVALID_BIRTH_DATE"): "INVALID_BIRTH_DATE",
    ("customers", "INVALID_EMAIL"): "INVALID_EMAIL",
    ("merchants", "MISSING_MCC"): "MISSING_MCC",
}


# ---------------------------------------------------------------- row-level evaluation (pure pandas)
def evaluate_rows(planted: pd.DataFrame, findings: pd.DataFrame) -> pd.DataFrame:
    """planted: feed, record_key, issue_type   |   findings: source_table, record_key, finding, action"""
    results = []
    for (feed, issue), finding in ROW_LEVEL.items():
        truth = set(planted.loc[(planted["feed"] == feed) & (planted["issue_type"] == issue), "record_key"])
        found_rows = findings[(findings["source_table"] == feed) & (findings["finding"] == finding)]
        found = set(found_rows["record_key"])
        caught = found_rows[found_rows["record_key"].isin(truth)].drop_duplicates("record_key")
        results.append({
            "feed": feed, "planted_problem": issue, "pipeline_finding": finding,
            "planted": len(truth), "detected": len(truth & found),
            "recall": len(truth & found) / len(truth) if truth else 1.0,
            "false_positives": len(found - truth),
            "handled": ", ".join(f"{a} {n}" for a, n in caught["action"].value_counts().items()),
        })
    return pd.DataFrame(results)


def unexpected_findings(findings: pd.DataFrame) -> pd.DataFrame:
    """Findings of a kind we never planted: real problems we didn't expect, or a bug in a check."""
    known = {(feed, finding) for (feed, _), finding in ROW_LEVEL.items()}
    mask = [(t, f) not in known for t, f in zip(findings["source_table"], findings["finding"])]
    return findings[mask].groupby(["source_table", "finding", "action"]).size().reset_index(name="records")


# ---------------------------------------------------------------- file-level evaluation (audit tables)
def file_date(name: str) -> date:
    return datetime.strptime(Path(name).stem.split("_")[1], "%Y%m%d").date()


def evaluate_files(warehouse: Warehouse, planted: pd.DataFrame) -> pd.DataFrame:
    files = planted[planted["feed"] == "transactions"]
    with warehouse.transaction() as tx:
        log = pd.DataFrame(tx.query("SELECT file_name, delivery_date, status FROM audit.file_load_log"),
                           columns=["file_name", "delivery_date", "status"])
        changes = dict(tx.query("SELECT column_name, first_seen_file FROM audit.schema_change_log "
                                "WHERE change_type = 'COLUMN_ADDED' AND table_name = 'raw.transactions'"))

    def statuses(name):
        return set(log.loc[log["file_name"] == f"transactions/{name}", "status"])

    results = []
    for name in files.loc[files["issue_type"] == "TRUNCATED_FILE", "file"]:
        ok = "REJECTED_MANIFEST" in statuses(name) and "LOADED" not in statuses(name)
        results.append(("Truncated file", name, ok, "rejected by manifest check" if ok else "NOT rejected"))
    for name in files.loc[files["issue_type"] == "RESENT_FILE", "file"]:
        ok = "LOADED" in statuses(name)
        results.append(("Re-sent file", name, ok, "loaded instead of the truncated copy" if ok else "NOT loaded"))
    for name in files.loc[files["issue_type"] == "LATE_FILE", "file"]:
        delivered = log.loc[log["file_name"] == f"transactions/{name}", "delivery_date"]
        days = (delivered.min() - file_date(name)).days if len(delivered) else None
        results.append(("Late file", name, bool(days and days > 0), f"recorded as delivered {days} days late"))
    for issue, column, label in [("COLUMN_RENAMED", "device_fingerprint", "Renamed column"),
                                 ("COLUMN_ADDED", "terminal_id", "New column")]:
        first = sorted(files.loc[files["issue_type"] == issue, "file"])
        seen = changes.get(column)
        ok = bool(first) and seen == f"transactions/{first[0]}"
        results.append((label, first[0] if first else "-", ok, f"'{column}' detected in {seen}"))
    return pd.DataFrame(results, columns=["problem", "file", "detected", "evidence"])


# ---------------------------------------------------------------- were the repairs CORRECT?
def verify_transaction_repairs(warehouse: Warehouse, truth: pd.DataFrame) -> pd.DataFrame:
    """Compare every repaired transaction value with the simulator's original (untouched) value.
    Detecting a problem is not enough: a "repair" that produces the wrong amount is worse than none."""
    with warehouse.transaction() as tx:
        rows = tx.query("""SELECT f.finding, t.txn_id, t.amount, t.txn_type, t.currency, t.txn_timestamp
                           FROM staging.dq_findings f
                           JOIN staging.transactions t ON t.txn_id = f.record_key
                           WHERE f.source_table = 'transactions' AND f.action = 'REPAIRED'""")
    repaired = pd.DataFrame(rows, columns=["finding", "txn_id", "amount", "txn_type", "currency", "txn_timestamp"])
    merged = repaired.merge(truth, on="txn_id", suffixes=("", "_true"))
    checks = {
        "MISSING_AMOUNT": lambda m: (m["amount"].astype(float) - m["amount_true"].astype(float)).abs() < 0.005,
        "NEGATIVE_AMOUNT": lambda m: (m["amount"].astype(float) - m["amount_true"].astype(float)).abs() < 0.005,
        "AMOUNT_TEXT_FORMAT": lambda m: (m["amount"].astype(float) - m["amount_true"].astype(float)).abs() < 0.005,
        "NONSTANDARD_TXN_TYPE": lambda m: m["txn_type"] == m["txn_type_true"],
        "NONSTANDARD_CURRENCY": lambda m: m["currency"] == m["currency_true"],
        # the US format has no seconds, so the best possible repair is correct to the minute
        "NONSTANDARD_TIMESTAMP": lambda m: pd.to_datetime(m["txn_timestamp"])
        == pd.to_datetime(m["txn_timestamp_true"]).dt.floor("min"),
    }
    results = []
    for finding, check in checks.items():
        part = merged[merged["finding"] == finding]
        correct = int(check(part).sum()) if len(part) else 0
        results.append({"finding": finding, "repaired": len(part), "correct": correct})
    return pd.DataFrame(results)


def load_findings(warehouse: Warehouse) -> pd.DataFrame:
    with warehouse.transaction() as tx:
        rows = tx.query("SELECT source_table, record_key, finding, action FROM staging.dq_findings")
    return pd.DataFrame(rows, columns=["source_table", "record_key", "finding", "action"])


# ---------------------------------------------------------------- the report
def render_markdown(rows: pd.DataFrame, files: pd.DataFrame, unexpected: pd.DataFrame, summary: dict,
                    repairs: pd.DataFrame) -> str:
    total, caught = int(rows["planted"].sum()), int(rows["detected"].sum())
    n_repaired, n_correct = int(repairs["repaired"].sum()), int(repairs["correct"].sum())
    lines = [
        "# Data Quality Report",
        "",
        f"_Generated {datetime.now():%Y-%m-%d %H:%M}. Compares what the pipeline found with the problems "
        "deliberately planted in the delivered files._",
        "",
        "## Summary",
        "",
        f"- **Row-level problems:** {caught:,} of {total:,} detected "
        f"(**{caught / total:.1%} recall**), {int(rows['false_positives'].sum())} false positives",
        f"- **Transaction repairs verified:** {n_correct:,} of {n_repaired:,} match the original value "
        f"({n_correct / n_repaired:.1%})" if n_repaired else "- **Transaction repairs verified:** none to check",
        f"- **File-level problems:** {int(files['detected'].sum())} of {len(files)} detected",
        f"- **Unexpected findings:** {len(unexpected)}",
        "",
        "## Row-level problems",
        "",
        "| Feed | Planted problem | Pipeline finding | Planted | Detected | Recall | False positives | How it was handled |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for r in rows.itertuples(index=False):
        lines.append(f"| {r.feed} | {r.planted_problem} | {r.pipeline_finding} | {r.planted:,} | {r.detected:,} "
                     f"| {r.recall:.0%} | {r.false_positives} | {r.handled} |")
    lines += ["", "## Were the transaction repairs correct?", "",
              "Each repaired value compared with the original, untouched value from the source system.", "",
              "| Repair | Repaired | Correct | Accuracy |", "|---|---:|---:|---:|"]
    for r in repairs.itertuples(index=False):
        accuracy = f"{r.correct / r.repaired:.0%}" if r.repaired else "-"
        lines.append(f"| {r.finding} | {r.repaired:,} | {r.correct:,} | {accuracy} |")
    lines += ["", "_Timestamps delivered in US format (`06/01/2026 08:13`) carry no seconds, so they can only be "
              "restored to the minute._"]
    lines += ["", "## File-level problems", "", "| Problem | File | Detected | Evidence |", "|---|---|---|---|"]
    for r in files.itertuples(index=False):
        lines.append(f"| {r.problem} | {r.file} | {'yes' if r.detected else '**NO**'} | {r.evidence} |")
    lines += ["", "## Staging tables", "", "| Table | Rows |", "|---|---:|"]
    lines += [f"| staging.{t} | {n:,} |" for t, n in summary["rows"].items()]
    lines += ["", "**Actions taken:** " + ", ".join(f"{a} {n:,}" for a, n in summary["actions"].items()), "",
              "- **REMOVED:** exact duplicates dropped",
              "- **REPAIRED:** reformatted, or recovered from a trusted field (balance change, related record)",
              "- **FLAGGED:** kept, with the bad value set to NULL",
              "- **REJECTED:** could not be trusted and left out of staging (see `staging.v_rejected_transactions`)"]
    if len(unexpected):
        lines += ["", "## Unexpected findings", "", "| Table | Finding | Action | Records |", "|---|---|---|---:|"]
        lines += [f"| {r.source_table} | {r.finding} | {r.action} | {r.records:,} |"
                  for r in unexpected.itertuples(index=False)]
    return "\n".join(lines) + "\n"


def write_report(warehouse: Warehouse, planted: pd.DataFrame, truth: pd.DataFrame,
                 path: Path = REPORT_PATH) -> dict:
    """planted: the DQ answer key | truth: the simulator's original transactions, before delivery damaged them"""
    findings = load_findings(warehouse)
    rows = evaluate_rows(planted, findings)
    files = evaluate_files(warehouse, planted)
    unexpected = unexpected_findings(findings)
    repairs = verify_transaction_repairs(warehouse, truth)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(rows, files, unexpected, staging_summary(warehouse), repairs), encoding="utf-8")
    return {"rows": rows, "files": files, "unexpected": unexpected, "repairs": repairs, "path": path}


def load_truth(cfg: dict) -> pd.DataFrame:
    columns = ["txn_id", "amount", "txn_type", "currency", "txn_timestamp"]
    return read_daily_files(cfg["paths"]["transactions"])[columns]


def main():
    cfg = load_config()
    planted = pd.read_csv(Path(cfg["paths"]["ground_truth"]) / "dq_issues.csv", dtype=str)
    result = write_report(Warehouse(), planted, load_truth(cfg))
    rows, repairs = result["rows"], result["repairs"]
    logger.info("Row-level recall: %s of %s planted problems detected (%.1f%%), %s false positives",
                f"{rows['detected'].sum():,}", f"{rows['planted'].sum():,}",
                100 * rows["detected"].sum() / rows["planted"].sum(), rows["false_positives"].sum())
    logger.info("Repairs verified correct: %s of %s", f"{repairs['correct'].sum():,}", f"{repairs['repaired'].sum():,}")
    logger.info("File-level: %s of %s detected", result["files"]["detected"].sum(), len(result["files"]))
    missed = rows[rows["recall"] < 1]
    for r in missed.itertuples(index=False):
        logger.warning("Missed some %s in %s: %s of %s", r.planted_problem, r.feed, r.detected, r.planted)
    logger.info("Report written to %s", result["path"])


if __name__ == "__main__":
    main()
