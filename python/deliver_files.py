"""
Deliver the simulated source-system files to the landing zone, the way real files arrive: imperfect.

The simulator's output in data/_simulated/ is perfect. Real deliveries are not. This script copies
every feed to data/landing/ and adds the problems data engineers deal with every day:

  Row-level issues   duplicates, missing values, wrong formats, unknown keys, late-arriving records
  File-level issues  a truncated file (re-sent the next day), a file delivered late,
                     a renamed column, a new column appearing mid-way (schema drift)
  Manifests          one JSON file per delivery day listing each file with its row count and
                     SHA-256 checksum, so the pipeline can check files arrived complete

Every damage is recorded in data/_ground_truth/dq_issues.csv, so we can later measure how well
the pipeline caught them. The landing folder is rebuilt from scratch on every run (same output each time).
"""
import hashlib
import json
import logging
import random
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from generate_customers import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- helpers
class IssueLog:
    """Records every problem we deliberately introduce (the data quality answer key)."""

    def __init__(self):
        self.rows = []

    def add(self, feed: str, file: str, issue: str, key: str = ""):
        self.rows.append({"feed": feed, "file": file, "record_key": key, "issue_type": issue})

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=["feed", "file", "record_key", "issue_type"])


def choose_rows(rng: random.Random, candidates: list, rates: dict, n_total: int) -> dict:
    """Pick DIFFERENT rows for each issue type, so two issues never hit the same row."""
    pool = list(candidates)
    rng.shuffle(pool)
    chosen, i = {}, 0
    for issue, rate in rates.items():
        k = rng.binomialvariate(n_total, rate)        # each row damaged with probability `rate`
        chosen[issue] = pool[i:i + k]
        i += k
    return chosen


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_day(path: Path) -> date:
    """transactions_20260601.csv -> date(2026, 6, 1)"""
    return datetime.strptime(path.stem.split("_")[-1], "%Y%m%d").date()


def add_duplicates(df: pd.DataFrame, rows: list) -> pd.DataFrame:
    """Insert an exact copy right after each chosen row."""
    order = pd.Series(range(len(df)), index=df.index, dtype=float)
    copies = df.loc[rows].copy()
    combined = pd.concat([df.assign(_o=order), copies.assign(_o=order.loc[rows] + 0.5)])
    return combined.sort_values("_o", kind="stable").drop(columns="_o").reset_index(drop=True)


class Delivery:
    """Writes landing files and remembers which manifest (delivery day) each file belongs to."""

    def __init__(self, landing: Path):
        self.landing = landing
        self.manifests = {}

    def write(self, feed: str, name: str, text: str, n_rows: int, delivered_on: date,
              manifest_rows: int | None = None, manifest_sha: str | None = None):
        path = self.landing / feed / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.manifests.setdefault(delivered_on, []).append({
            "feed": feed, "file": f"{feed}/{name}",
            "rows": n_rows if manifest_rows is None else manifest_rows,     # what the SENDER says
            "sha256": sha256(text) if manifest_sha is None else manifest_sha,
        })

    def write_manifests(self, start: date, n_days: int):
        out = self.landing / "manifests"
        out.mkdir(parents=True, exist_ok=True)
        for offset in range(n_days):
            day = start + timedelta(days=offset)
            manifest = {"delivery_date": day.isoformat(), "files": self.manifests.get(day, [])}
            (out / f"manifest_{day:%Y%m%d}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- transactions
def damage_transactions(df: pd.DataFrame, rng, rates: dict, log: IssueLog, file: str) -> pd.DataFrame:
    general = {k: v for k, v in rates.items() if k not in ("late_arriving", "negative_amount")}
    chosen = choose_rows(rng, df.index, general, len(df))
    used = {i for rows in chosen.values() for i in rows}
    free_debits = [i for i in df.index[df["direction"] == "DR"] if i not in used]    # only debits can go negative
    k = min(len(free_debits), rng.binomialvariate(len(df), rates["negative_amount"]))
    chosen["negative_amount"] = rng.sample(free_debits, k)

    for i in chosen["missing_amount"]:
        df.at[i, "amount"] = ""
    for i in chosen["amount_as_text"]:
        df.at[i, "amount"] = f"${float(df.at[i, 'amount']):,.2f}"
    for i in chosen["messy_txn_type"]:
        value = df.at[i, "txn_type"]
        df.at[i, "txn_type"] = rng.choice([value.lower(), value.replace("_", " ").title(), f" {value}"])
    for i in chosen["us_date_format"]:
        ts = datetime.fromisoformat(df.at[i, "txn_timestamp"])
        df.at[i, "txn_timestamp"] = ts.strftime("%m/%d/%Y %H:%M")
    for i in chosen["orphan_account"]:
        df.at[i, "account_id"] = f"ACC9{rng.randint(0, 999999):06d}"
    for i in chosen["negative_amount"]:
        df.at[i, "amount"] = "-" + df.at[i, "amount"]
    for i in chosen["currency_variant"]:
        df.at[i, "currency"] = rng.choice(["usd", "US$", "Usd"])

    for issue, rows in chosen.items():
        for i in rows:
            log.add("transactions", file, issue.upper(), df.at[i, "txn_id"])
    return add_duplicates(df, chosen["duplicate"])


def deliver_transactions(cfg, rng, log: IssueLog, delivery: Delivery):
    d = cfg["delivery"]
    rates = d["row_issue_rates"]["transactions"]
    fi = d["file_issues"]
    files = sorted(Path(cfg["paths"]["transactions"]).glob("transactions_*.csv"))
    frames = [pd.read_csv(f, dtype=str, keep_default_na=False) for f in files]   # strings: keep exact values

    def complete_copy(n: int) -> str:
        """Name of the file that holds day n's COMPLETE data (the re-sent copy on the truncated day)."""
        name = files[n - 1].name
        return name.replace(".csv", "_resend.csv") if n == fi["truncated_transactions_day"] else name

    # Late-arriving records: some rows of day N only show up in day N+1's file
    for n in range(len(frames) - 1):
        k = rng.binomialvariate(len(frames[n]), rates["late_arriving"])
        late = sorted(rng.sample(range(len(frames[n])), k))
        moved = frames[n].iloc[late]
        frames[n] = frames[n].drop(frames[n].index[late]).reset_index(drop=True)
        frames[n + 1] = pd.concat([frames[n + 1], moved], ignore_index=True)
        for txn_id in moved["txn_id"]:
            log.add("transactions", complete_copy(n + 2), "LATE_ARRIVING_ROW", txn_id)

    for n, (path, df) in enumerate(zip(files, frames), start=1):
        day = file_day(path)
        df = damage_transactions(df, rng, rates, log, complete_copy(n))

        if n >= fi["rename_column_day"]:                                # schema drift 1: renamed column
            df = df.rename(columns={"device_id": "device_fingerprint"})
            log.add("transactions", path.name, "COLUMN_RENAMED")
        if n >= fi["new_column_day"]:                                   # schema drift 2: new column
            has_terminal = df["channel"].isin(["ATM", "POS"])
            df["terminal_id"] = [f"TRM{rng.randint(10**5, 10**6 - 1)}" if t else "" for t in has_terminal]
            log.add("transactions", path.name, "COLUMN_ADDED")

        text = df.to_csv(index=False)
        if n == fi["truncated_transactions_day"]:
            # The transfer broke off part-way: the manifest describes the FULL file the sender meant to send
            keep = int(len(df) * fi["truncated_keep_share"])
            lines = text.splitlines(keepends=True)
            delivery.write("transactions", path.name, "".join(lines[:keep + 1]), keep, day,
                           manifest_rows=len(df), manifest_sha=sha256(text))
            delivery.write("transactions", f"{path.stem}_resend.csv", text, len(df), day + timedelta(days=1))
            log.add("transactions", path.name, "TRUNCATED_FILE")
            log.add("transactions", f"{path.stem}_resend.csv", "RESENT_FILE")
        elif n == fi["late_transactions_day"]:
            delivery.write("transactions", path.name, text, len(df), day + timedelta(days=fi["late_by_days"]))
            log.add("transactions", path.name, "LATE_FILE")
        else:
            delivery.write("transactions", path.name, text, len(df), day)


# ---------------------------------------------------------------- logins (JSON Lines)
def deliver_logins(cfg, rng, log: IssueLog, delivery: Delivery):
    rates = cfg["delivery"]["row_issue_rates"]["logins"]
    for path in sorted(Path(cfg["paths"]["logins"]).glob("logins_*.jsonl")):
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        chosen = choose_rows(rng, range(len(events)), rates, len(events))

        for i in chosen["epoch_timestamp"]:
            ts = datetime.fromisoformat(events[i]["event_timestamp"])
            events[i]["event_timestamp"] = int((ts - datetime(1970, 1, 1)).total_seconds() * 1000)
        for i in chosen["missing_device"]:
            events[i]["device_id"] = None
        for i in chosen["lowercase_country"]:
            events[i]["ip_country"] = events[i]["ip_country"].lower()
        for issue, rows in chosen.items():
            for i in rows:
                log.add("logins", path.name, issue.upper(), events[i]["event_id"])

        duplicates = set(chosen["duplicate"])
        out = []
        for i, event in enumerate(events):
            out.append(event)
            if i in duplicates:
                out.append(dict(event))
        text = "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in out)
        delivery.write("logins", path.name, text, len(out), file_day(path))


# ---------------------------------------------------------------- fraud reports
def deliver_fraud_reports(cfg, rng, log: IssueLog, delivery: Delivery):
    rates = cfg["delivery"]["row_issue_rates"]["fraud_reports"]
    for path in sorted(Path(cfg["paths"]["fraud_reports"]).glob("fraud_reports_*.csv")):
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        if len(df):
            chosen = choose_rows(rng, df.index, rates, len(df))
            for i in chosen["missing_amount"]:
                df.at[i, "amount"] = ""
            for i in chosen["slash_date_format"]:
                df.at[i, "event_date"] = df.at[i, "event_date"].replace("-", "/")
            for issue, rows in chosen.items():
                for i in rows:
                    log.add("fraud_reports", path.name, issue.upper(), df.at[i, "report_id"])
            df = add_duplicates(df, chosen["duplicate"])
        delivery.write("fraud_reports", path.name, df.to_csv(index=False), len(df), file_day(path))


# ---------------------------------------------------------------- reference tables
def deliver_reference(cfg, rng, log: IssueLog, delivery: Delivery):
    ref_dir = Path(cfg["paths"]["reference"])
    rates = cfg["delivery"]["row_issue_rates"]
    first_day = date.fromisoformat(cfg["simulation"]["start_date"])

    for path in sorted(ref_dir.glob("*.csv")):
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        table = path.stem.rsplit("_", 1)[0]

        if table == "customers":
            chosen = choose_rows(rng, df.index, rates["customers"], len(df))
            for i in chosen["missing_state"]:
                df.at[i, "state"] = ""
            for i in chosen["invalid_birth_date"]:
                df.at[i, "date_of_birth"] = rng.choice(["1900-01-01", "2031-07-14"])
            for i in chosen["invalid_email"]:
                df.at[i, "email"] = df.at[i, "email"].replace("@", " at ")
            for issue, rows in chosen.items():
                for i in rows:
                    log.add("customers", path.name, issue.upper(), df.at[i, "customer_id"])
            # duplicates are NOT exact copies: the same customer, email in capitals, phone digits only
            copies = df.loc[chosen["duplicate_customer"]].copy()
            copies["email"] = copies["email"].str.upper()
            copies["phone"] = copies["phone"].str.replace(r"\D", "", regex=True)
            df = pd.concat([df, copies], ignore_index=True)

        elif table == "merchants":
            chosen = choose_rows(rng, df.index, rates["merchants"], len(df))
            for i in chosen["missing_mcc"]:
                df.at[i, "mcc"] = ""
                log.add("merchants", path.name, "MISSING_MCC", df.at[i, "merchant_id"])

        delivery.write("reference", path.name, df.to_csv(index=False), len(df), first_day)


# ---------------------------------------------------------------- main
def deliver_all(cfg) -> pd.DataFrame:
    landing = Path(cfg["paths"]["landing"])
    if landing.exists():
        shutil.rmtree(landing)                     # rebuild from scratch: same result every run
    rng = random.Random(cfg["project"]["seed"] + 8)
    log, delivery = IssueLog(), Delivery(landing)

    deliver_reference(cfg, rng, log, delivery)
    deliver_transactions(cfg, rng, log, delivery)
    deliver_logins(cfg, rng, log, delivery)
    deliver_fraud_reports(cfg, rng, log, delivery)
    delivery.write_manifests(date.fromisoformat(cfg["simulation"]["start_date"]), cfg["simulation"]["n_days"])

    issues = log.to_frame()
    gt = Path(cfg["paths"]["ground_truth"])
    gt.mkdir(parents=True, exist_ok=True)
    issues.to_csv(gt / "dq_issues.csv", index=False)
    return issues


def main():
    cfg = load_config()
    issues = deliver_all(cfg)
    landing = Path(cfg["paths"]["landing"])
    n_files = sum(1 for p in landing.rglob("*") if p.is_file())
    logger.info("Delivered %s files (including manifests) to %s", n_files, landing)
    logger.info("Issues introduced: %s", f"{len(issues):,}")
    for feed, counts in issues.groupby("feed")["issue_type"].value_counts().groupby(level=0):
        logger.info("  %-14s %s", feed, counts.droplevel(0).to_dict())


if __name__ == "__main__":
    main()
