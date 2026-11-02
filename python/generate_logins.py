"""
Generate login events (digital banking system), one JSON Lines file per day.

Three kinds of logins:
  1. Customers logging in shortly before their own online activity (transfers, bill payments)
  2. Extra logins with no transaction (checking the balance)
  3. Criminals logging in before an account takeover: unknown device, often abroad,
     frequently after failed password or one-time-code attempts

Inputs:  landing transactions + reference tables, ground truth (to know which logins are attacks)
Outputs: data/landing/logins/logins_<YYYYMMDD>.jsonl   (NO labels)
         data/_ground_truth/login_labels.csv             (which login events were the attacker)
"""
import logging
import random
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

from generate_customers import load_config
from utils import read_daily_files

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

COLUMNS = ["event_id", "event_timestamp", "customer_id", "session_id", "device_id", "channel",
           "ip_address", "ip_country", "result", "failure_reason"]
DIGITAL = ["MOBILE", "WEB"]


def login_record(ts, customer_id, session_id, device_id, channel, ip, country, result, reason=None, case_id=None):
    return {"ts": ts, "customer_id": customer_id, "session_id": session_id, "device_id": device_id,
            "channel": channel, "ip_address": ip, "ip_country": country, "result": result,
            "failure_reason": reason, "case_id": case_id}


def customer_session(rng, fake, ts, customer_id, device_id, channel, home_ip, cfg) -> list:
    """A normal customer login: usually from home, occasionally travelling, sometimes after a typo."""
    l = cfg["logins"]
    home = cfg["project"]["home_country"]
    if rng.random() < l["travel_share"]:
        country, ip = rng.choice(cfg["merchants"]["foreign_countries"]), fake.ipv4_public()
    else:
        country = home
        ip = home_ip if rng.random() < l["home_ip_share"] else fake.ipv4_public()

    session_id = str(uuid.UUID(int=rng.getrandbits(128)))
    events = []
    if rng.random() < l["failed_login_rate"]:
        events.append(login_record(ts - timedelta(seconds=rng.randint(10, 90)), customer_id, session_id,
                                   device_id, channel, ip, country, "FAILED", "WRONG_PASSWORD"))
    events.append(login_record(ts, customer_id, session_id, device_id, channel, ip, country, "SUCCESS"))
    return events


def attacker_session(rng, fake, case: pd.Series, cfg) -> list:
    """The criminal's login before an account takeover."""
    a = cfg["logins"]["attacker"]
    foreign = rng.random() < a["foreign_share"]
    country = rng.choice(a["countries"]) if foreign else cfg["project"]["home_country"]
    ip = fake.ipv4_public()
    session_id = str(uuid.UUID(int=rng.getrandbits(128)))
    ts = case["first_ts"] - timedelta(minutes=rng.uniform(*a["minutes_before_transfer"]))

    events = []
    n_failed = rng.randint(*a["failed_attempts"])
    for i in range(n_failed):                         # guessing attempts, seconds apart
        reason = rng.choice(["WRONG_PASSWORD", "OTP_FAILED"])
        events.append(login_record(ts - timedelta(seconds=40 * (n_failed - i)), case["customer_id"], session_id,
                                   case["device_id"], case["channel"], ip, country, "FAILED", reason,
                                   case["case_id"]))
    events.append(login_record(ts, case["customer_id"], session_id, case["device_id"], case["channel"],
                               ip, country, "SUCCESS", None, case["case_id"]))
    return events


def generate_logins(txns, labels, accounts, devices, cfg) -> pd.DataFrame:
    seed = cfg["project"]["seed"] + 6
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)
    fake = Faker(cfg["project"]["locale"])
    fake.seed_instance(seed)

    start = datetime.fromisoformat(cfg["simulation"]["start_date"])
    n_days = cfg["simulation"]["n_days"]
    end = start + timedelta(days=n_days)
    l = cfg["logins"]

    t = txns.merge(labels, on="txn_id", how="left")
    t["ts"] = pd.to_datetime(t["txn_timestamp"])
    t["customer_id"] = t["account_id"].map(accounts.set_index("account_id")["customer_id"])
    device_type = devices.set_index("device_id")["device_type"].to_dict()
    home_ip = {d: fake.ipv4_public() for d in devices["device_id"]}          # each device's usual IP
    channel_of = lambda d: "WEB" if device_type[d] == "WEB_BROWSER" else "MOBILE"

    is_attack = (t["fraud_type"] == "ACCOUNT_TAKEOVER") & (t["fraud_role"] == "VICTIM")
    events = []

    # 1. Customers log in before their own online activity.
    #    A new session starts when a device has been inactive longer than the timeout.
    own = t[t["channel"].isin(DIGITAL) & ~is_attack & t["device_id"].isin(home_ip)]
    own = own.sort_values(["device_id", "ts"])
    gap = own.groupby("device_id")["ts"].diff()
    starts = own[gap.isna() | (gap > timedelta(minutes=l["session_timeout_minutes"]))]
    for row in starts.itertuples(index=False):
        login_ts = row.ts - timedelta(minutes=rng.uniform(*l["minutes_before_activity"]))   # before day 1: dropped below
        events += customer_session(rng, fake, login_ts, row.customer_id, row.device_id, channel_of(row.device_id),
                                   home_ip[row.device_id], cfg)

    # 2. Extra logins just to check the balance (customers who are active this period)
    active_customers = set(t["customer_id"])
    dev = devices[devices["customer_id"].isin(active_customers)].reset_index(drop=True)
    counts = np_rng.poisson(l["extra_logins_per_device_per_day"], size=(len(dev), n_days))
    hours = cfg["transactions"]["hour_weights"]
    for i, day in zip(*np.nonzero(counts)):
        d = dev.iloc[i]
        for _ in range(counts[i, day]):
            hour = rng.choices(range(24), weights=hours)[0]
            ts = start + timedelta(days=int(day), hours=hour, minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))
            events += customer_session(rng, fake, ts, d["customer_id"], d["device_id"],
                                       channel_of(d["device_id"]), home_ip[d["device_id"]], cfg)

    # 3. Criminal logins before each account takeover
    attacks = t[is_attack].sort_values("ts").groupby("case_id").agg(
        first_ts=("ts", "min"), customer_id=("customer_id", "first"),
        device_id=("device_id", "first"), channel=("channel", "first")).reset_index()
    for _, case in attacks.iterrows():
        events += attacker_session(rng, fake, case, cfg)

    df = pd.DataFrame(events)
    df["ts"] = pd.to_datetime(df["ts"]).dt.floor("s")
    df = df[(df["ts"] >= start) & (df["ts"] < end)].sort_values("ts", kind="stable").reset_index(drop=True)
    df["event_id"] = [f"LGN{i:09d}" for i in range(1, len(df) + 1)]
    df["event_timestamp"] = df["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    return df


def write_daily_jsonl(df: pd.DataFrame, out_dir: Path) -> int:
    """One JSON Lines file per day: each line is one login event (a common format for app logs)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for day, part in df.groupby(df["ts"].dt.date):
        part[COLUMNS].to_json(out_dir / f"logins_{day:%Y%m%d}.jsonl", orient="records", lines=True)
        n += 1
    return n


def main():
    cfg = load_config()
    ref_dir = Path(cfg["paths"]["reference"])
    gt_dir = Path(cfg["paths"]["ground_truth"])
    snap = cfg["project"]["snapshot_date"].replace("-", "")

    txns = read_daily_files(cfg["paths"]["transactions"])
    labels = pd.read_csv(gt_dir / "transaction_labels.csv")
    accounts = pd.read_csv(ref_dir / f"accounts_{snap}.csv")
    devices = pd.read_csv(ref_dir / f"devices_{snap}.csv")

    df = generate_logins(txns, labels, accounts, devices, cfg)
    n_files = write_daily_jsonl(df, Path(cfg["paths"]["logins"]))
    df.loc[df["case_id"].notna(), ["event_id", "case_id"]].to_csv(gt_dir / "login_labels.csv", index=False)

    attack = df[df["case_id"].notna()]
    logger.info("Created %s login events in %s daily files", f"{len(df):,}", n_files)
    logger.info("Failed logins: %.2f%%", (df["result"] == "FAILED").mean() * 100)
    logger.info("Attacker login events: %s (%s cases)", len(attack), attack["case_id"].nunique())
    logger.info("Attacker logins from abroad: %.0f%%",
                (attack["ip_country"] != cfg["project"]["home_country"]).mean() * 100)


if __name__ == "__main__":
    main()
