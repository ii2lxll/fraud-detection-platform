"""
Generate daily transaction files (payments hub) with realistic customer behaviour.

The engine works in two phases:
  1. PLAN:    decide what every active customer does each day (purchases, transfers, salary...)
  2. PROCESS: replay all events in time order, update balances, decline when funds are short

Fraud cases (see fraud_patterns.py) are mixed into the same event stream, so stolen money
really leaves victims' balances and really arrives in mule accounts.

Inputs:  reference tables in data/landing/reference/
Outputs: data/landing/transactions/transactions_<YYYYMMDD>.csv  (one file per day, NO fraud labels)
         data/_ground_truth/  transaction_labels.csv, fraud_cases.csv, mule_accounts.csv
"""
import heapq
import logging
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from fraud_patterns import plan_fraud, plan_mule_cashout, select_mules
from generate_customers import load_config, weighted_choice

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

COLUMNS = [
    "txn_id", "txn_ref", "txn_timestamp", "account_id", "card_id", "txn_type", "direction",
    "amount", "currency", "channel", "merchant_id", "counterparty_account", "counterparty_bank",
    "device_id", "balance_before", "balance_after", "status", "decline_reason",
]
LABEL_COLUMNS = ["case_id", "fraud_type", "fraud_role"]     # ground truth only
BILLER_CATEGORIES = ["UTILITIES", "TELECOM"]


# ---------------------------------------------------------------- helpers
def draw_amount(rng: random.Random, spec: dict, multiplier: float = 1.0) -> float:
    """Random amount from a log-normal distribution: many small values, a few large ones."""
    return round(rng.lognormvariate(math.log(spec["median"] * multiplier), spec["sigma"]), 2)


def random_time(rng: random.Random, day: date, hour_weights: list) -> datetime:
    """Random time on a given day; busy hours are more likely than the middle of the night."""
    hour = rng.choices(range(24), weights=hour_weights)[0]
    # seconds stop at 58 so a linked credit 1 second later never spills into the next day
    return datetime(day.year, day.month, day.day, hour, rng.randint(0, 59), rng.randint(0, 58))


def new_payee(rng: random.Random, own_account: str, ctx: dict) -> tuple:
    """A person this customer sends money to: either another bank customer or an external account."""
    if rng.random() < ctx["t"]["internal_transfer_share"]:
        account = rng.choice(ctx["internal_accounts"])
        while account == own_account:
            account = rng.choice(ctx["internal_accounts"])
        return account, ctx["bank_code"]
    return f"EXT{rng.randint(10**9, 10**10 - 1)}", rng.choice(ctx["external_banks"])


def digital_channel(rng: random.Random, profile: dict, ctx: dict) -> tuple:
    """Pick a device for an online action. Customers without a device go to the branch."""
    if not profile["devices"]:
        return "BRANCH", None
    device_id = rng.choice(profile["devices"])
    channel = "WEB" if ctx["device_type"][device_id] == "WEB_BROWSER" else "MOBILE"
    return channel, device_id


# ---------------------------------------------------------------- setup
def build_context(customers, accounts, cards, merchants, devices, cfg) -> dict:
    """Lookup tables the engine needs, built once."""
    t = cfg["transactions"]
    active_main = accounts[(accounts["account_type"] != "SAVINGS") & (accounts["status"] == "ACTIVE")]
    shops = merchants[~merchants["category"].isin(BILLER_CATEGORIES)]
    category_weight = {c: info["weight"] for c, info in cfg["merchants"]["categories"].items()}
    risky_online = merchants[(merchants["risk_tier"] == "HIGH") & (merchants["channel"] == "ONLINE")]
    is_home = risky_online["country"] == cfg["project"]["home_country"]
    start = date.fromisoformat(cfg["simulation"]["start_date"])

    return {
        "t": t,
        "cfg": cfg,
        "sim_end": datetime.combine(start + timedelta(days=cfg["simulation"]["n_days"]), datetime.min.time()),
        "currency": cfg["accounts"]["currency"],
        "bank_code": cfg["simulation"]["bank_code"],
        "external_banks": cfg["simulation"]["external_banks"],
        "active_main": active_main,
        "internal_accounts": active_main["account_id"].tolist(),
        "card_by_account": cards[cards["card_status"] == "ACTIVE"].set_index("account_id")["card_id"].to_dict(),
        "devices_by_customer": devices.groupby("customer_id")["device_id"].apply(list).to_dict(),
        "device_type": devices.set_index("device_id")["device_type"].to_dict(),
        "customer": customers.set_index("customer_id")[
            ["segment", "income_band", "date_of_birth", "customer_since"]].to_dict("index"),
        "shop_ids": shops["merchant_id"].tolist(),
        "shop_weights": shops["category"].map(category_weight).tolist(),
        "merchant_channel": merchants.set_index("merchant_id")["channel"].to_dict(),
        "biller_ids": merchants.loc[merchants["category"].isin(BILLER_CATEGORIES), "merchant_id"].tolist(),
        "risky_home": risky_online.loc[is_home, "merchant_id"].tolist(),        # targets for card fraud
        "risky_foreign": risky_online.loc[~is_home, "merchant_id"].tolist(),
    }


def build_profiles(ctx: dict, rng: random.Random) -> list:
    """One behaviour profile per active customer: account, card, devices, favourite shops, payees."""
    t = ctx["t"]
    profiles = []
    for acc in ctx["active_main"].itertuples(index=False):
        info = ctx["customer"][acc.customer_id]
        monthly_income = t["income_midpoint"][info["income_band"]] / 12
        profiles.append({
            "customer_id": acc.customer_id,
            "account_id": acc.account_id,
            "segment": info["segment"],
            "income_band": info["income_band"],
            "date_of_birth": date.fromisoformat(str(info["date_of_birth"])),
            "customer_since": date.fromisoformat(str(info["customer_since"])),
            "card_id": ctx["card_by_account"].get(acc.account_id),
            "devices": ctx["devices_by_customer"].get(acc.customer_id, []),
            "favourite_shops": rng.sample(ctx["shop_ids"], t["favourite_merchants"]),
            "payees": [new_payee(rng, acc.account_id, ctx) for _ in range(t["regular_payees"])],
            "employer": f"EMP{rng.randint(10**5, 10**6 - 1)}",
            "monthly_income": monthly_income,
            # higher earners spend proportionally more, so balances stay stable over time
            "multiplier": monthly_income / t["income_reference"],
        })
    return profiles


# ---------------------------------------------------------------- phase 1: plan
def plan_event(rng: random.Random, profile: dict, txn_type: str, day: date, ctx: dict):
    """Turn 'customer does X today' into a full event record. Returns None if not possible."""
    t = ctx["t"]
    mult = profile["multiplier"]
    event = {
        "ts": random_time(rng, day, t["hour_weights"]), "account_id": profile["account_id"],
        "txn_type": txn_type, "direction": "DR", "card_id": None, "merchant_id": None,
        "counterparty_account": None, "counterparty_bank": None, "device_id": None,
    }

    if txn_type == "CARD_PURCHASE":
        if profile["card_id"] is None:
            return None
        if rng.random() < t["favourite_merchant_share"]:
            merchant = rng.choice(profile["favourite_shops"])
        else:
            merchant = rng.choices(ctx["shop_ids"], weights=ctx["shop_weights"])[0]
        online = ctx["merchant_channel"][merchant] == "ONLINE"
        event.update(card_id=profile["card_id"], merchant_id=merchant,
                     channel="ECOM" if online else "POS", amount=draw_amount(rng, t["amounts"][txn_type], mult))

    elif txn_type == "TRANSFER_OUT":
        if rng.random() < t["regular_payee_share"]:
            account, bank = rng.choice(profile["payees"])
        else:
            account, bank = new_payee(rng, profile["account_id"], ctx)      # first-time payee
        channel, device = digital_channel(rng, profile, ctx)
        event.update(counterparty_account=account, counterparty_bank=bank, channel=channel,
                     device_id=device, amount=draw_amount(rng, t["amounts"][txn_type], mult))

    elif txn_type == "TRANSFER_IN":                                          # money from another bank
        event.update(direction="CR", channel="NETWORK", counterparty_account=f"EXT{rng.randint(10**9, 10**10 - 1)}",
                     counterparty_bank=rng.choice(ctx["external_banks"]),
                     amount=draw_amount(rng, t["amounts"][txn_type], mult))

    elif txn_type == "BILL_PAYMENT":
        channel, device = digital_channel(rng, profile, ctx)
        event.update(merchant_id=rng.choice(ctx["biller_ids"]), channel=channel, device_id=device,
                     amount=draw_amount(rng, t["amounts"][txn_type], mult))

    elif txn_type == "ATM_WITHDRAWAL":
        if profile["card_id"] is None:
            return None
        raw = draw_amount(rng, t["amounts"][txn_type], mult)
        event.update(card_id=profile["card_id"], channel="ATM", amount=float(max(20, round(raw / 20) * 20)))

    return event


def plan_income(rng: random.Random, np_rng, profile: dict, day: date, ctx: dict) -> list:
    """Salaries (monthly) for individuals, business income (daily) for small businesses."""
    t = ctx["t"]
    events = []
    base = {"account_id": profile["account_id"], "direction": "CR", "channel": "NETWORK", "card_id": None,
            "merchant_id": None, "device_id": None, "counterparty_bank": rng.choice(ctx["external_banks"])}

    if profile["segment"] == "SMALL_BUSINESS":
        for _ in range(np_rng.poisson(t["business_income_rate"])):
            events.append({**base, "ts": random_time(rng, day, t["hour_weights"]), "txn_type": "BUSINESS_INCOME",
                           "counterparty_account": f"EXT{rng.randint(10**9, 10**10 - 1)}",
                           "amount": draw_amount(rng, t["amounts"]["BUSINESS_INCOME"], profile["multiplier"])})
    elif day.day == t["salary_day"]:
        events.append({**base, "ts": datetime(day.year, day.month, day.day, 6, rng.randint(0, 59), 0),
                       "txn_type": "SALARY", "counterparty_account": profile["employer"],
                       "amount": round(profile["monthly_income"] * rng.uniform(0.95, 1.05), 2)})
    return events


def plan_all_events(profiles: list, ctx: dict, cfg: dict, rng: random.Random, np_rng) -> list:
    """Phase 1: every customer, every day."""
    t = ctx["t"]
    start = date.fromisoformat(cfg["simulation"]["start_date"])
    events = []
    for offset in range(cfg["simulation"]["n_days"]):
        day = start + timedelta(days=offset)
        for p in profiles:
            events.extend(plan_income(rng, np_rng, p, day, ctx))
            for _ in range(np_rng.poisson(t["daily_rate"][p["segment"]])):   # how many actions today
                event = plan_event(rng, p, weighted_choice(rng, t["type_weights"]), day, ctx)
                if event is not None:
                    events.append(event)
    return events


# ---------------------------------------------------------------- phase 2: process
def resolve_amount(e: dict, balance: float, rng: random.Random) -> float:
    """Fraud amounts often depend on the balance at that moment ("take 60% of what's there")."""
    rule = e.get("amount_rule")
    if rule is None:
        return e["amount"]
    kind = rule[0]
    if kind == "fraction":
        amount = balance * rule[1]
    else:                                                   # "fraction_capped"
        amount = min(balance * rule[1], rule[2])
    if amount < 20:                                         # account already empty: the attempt will fail
        return float(rng.randint(10, 100) * 20)
    return round(amount, 2)


def process_events(events: list, balances: dict, ctx: dict, rng: random.Random,
                   fraud_rng: random.Random, mules: dict) -> pd.DataFrame:
    """Phase 2: replay events in time order, keep running balances, decline if funds are short."""
    heap = [(e["ts"], i, e) for i, e in enumerate(events)]
    heapq.heapify(heap)                                   # always gives us the earliest event next
    counter = len(events)
    rows = []

    while heap:
        ts, _, e = heapq.heappop(heap)
        before = balances[e["account_id"]]
        amount = resolve_amount(e, before, fraud_rng)

        if e["direction"] == "DR" and amount > before:
            # Most people check their balance first and simply don't make the payment.
            # Criminals (and pressured scam victims) try anyway.
            if not e.get("always_attempt") and rng.random() >= ctx["t"]["attempt_when_short"]:
                continue
            status, reason, after = "DECLINED", "INSUFFICIENT_FUNDS", before
        else:
            status, reason = "SUCCESS", None
            after = round(before - amount, 2) if e["direction"] == "DR" else round(before + amount, 2)
            balances[e["account_id"]] = after

        txn_id = f"TXN{len(rows) + 1:09d}"
        txn_ref = e.get("txn_ref", txn_id)
        rows.append({
            "txn_id": txn_id, "txn_ref": txn_ref, "txn_timestamp": ts, "account_id": e["account_id"],
            "card_id": e["card_id"], "txn_type": e["txn_type"], "direction": e["direction"],
            "amount": amount, "currency": ctx["currency"], "channel": e["channel"],
            "merchant_id": e["merchant_id"], "counterparty_account": e["counterparty_account"],
            "counterparty_bank": e["counterparty_bank"], "device_id": e["device_id"],
            "balance_before": before, "balance_after": after, "status": status, "decline_reason": reason,
            "case_id": e.get("case_id"), "fraud_type": e.get("fraud_type"), "fraud_role": e.get("fraud_role"),
        })

        # A successful transfer to another customer of our bank creates their incoming credit
        if status == "SUCCESS" and e["txn_type"] == "TRANSFER_OUT" and e["counterparty_bank"] == ctx["bank_code"]:
            credit = {
                "ts": ts + timedelta(seconds=1), "account_id": e["counterparty_account"], "txn_type": "TRANSFER_IN",
                "direction": "CR", "amount": amount, "channel": "INTERNAL", "card_id": None,
                "merchant_id": None, "counterparty_account": e["account_id"],
                "counterparty_bank": ctx["bank_code"], "device_id": None, "txn_ref": txn_ref,
            }
            if e.get("case_id"):                    # stolen money arriving at a mule
                credit.update(case_id=e["case_id"], fraud_type=e["fraud_type"], fraud_role="MULE_INBOUND")
            counter += 1
            heapq.heappush(heap, (credit["ts"], counter, credit))

        # A mule that receives stolen money moves it out a few hours later
        if status == "SUCCESS" and e.get("fraud_role") == "MULE_INBOUND" and e["account_id"] in mules:
            for cashout in plan_mule_cashout(fraud_rng, mules[e["account_id"]], ts, amount, e["case_id"],
                                             e["fraud_type"], ctx, ctx["cfg"]):
                counter += 1
                heapq.heappush(heap, (cashout["ts"], counter, cashout))

    df = pd.DataFrame(rows, columns=COLUMNS + LABEL_COLUMNS)
    df["txn_timestamp"] = pd.to_datetime(df["txn_timestamp"])
    return df


# ---------------------------------------------------------------- main entry points
def simulate(customers, accounts, cards, merchants, devices, cfg) -> dict:
    """Run the full simulation: normal behaviour + fraud. Returns transactions (with labels) and mules."""
    seed = cfg["project"]["seed"] + 4
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)          # numpy has a Poisson sampler; the random module doesn't
    fraud_rng = random.Random(seed + 1)           # separate stream: adding fraud never changes normal behaviour plans

    ctx = build_context(customers, accounts, cards, merchants, devices, cfg)
    profiles = build_profiles(ctx, rng)
    events = plan_all_events(profiles, ctx, cfg, rng, np_rng)

    mules = select_mules(profiles, cfg, fraud_rng)
    events += plan_fraud(profiles, mules, ctx, cfg, fraud_rng)

    balances = accounts.set_index("account_id")["current_balance"].to_dict()
    mule_lookup = {m["account_id"]: m for m in mules}
    txns = process_events(events, balances, ctx, rng, fraud_rng, mule_lookup)
    return {"transactions": txns, "mules": mules}


def generate_transactions(customers, accounts, cards, merchants, devices, cfg) -> pd.DataFrame:
    """Transactions including the hidden label columns (used by tests and the ground-truth writer)."""
    return simulate(customers, accounts, cards, merchants, devices, cfg)["transactions"]


def write_daily_files(df: pd.DataFrame, out_dir: Path) -> int:
    """Split transactions into one file per day, like a real daily feed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n_files = 0
    for day, part in df.groupby(df["txn_timestamp"].dt.date):
        part[COLUMNS].to_csv(out_dir / f"transactions_{day:%Y%m%d}.csv", index=False)   # labels stay out
        n_files += 1
    return n_files


def write_ground_truth(df: pd.DataFrame, mules: list, out_dir: Path) -> pd.DataFrame:
    """The answer key: which transactions were fraud. Used ONLY to evaluate models, never to train on directly."""
    out_dir.mkdir(parents=True, exist_ok=True)
    labelled = df[df["case_id"].notna()]
    labelled[["txn_id"] + LABEL_COLUMNS].to_csv(out_dir / "transaction_labels.csv", index=False)

    victim = labelled[labelled["fraud_role"] == "VICTIM"]
    cases = victim.groupby("case_id").agg(
        fraud_type=("fraud_type", "first"),
        victim_account=("account_id", "first"),
        first_txn=("txn_timestamp", "min"),
        last_txn=("txn_timestamp", "max"),
        n_victim_txns=("txn_id", "count"),
        attacker_device=("device_id", "first"),
    )
    lost = victim[victim["status"] == "SUCCESS"].groupby("case_id")["amount"].sum()
    cases["amount_lost"] = lost.reindex(cases.index).fillna(0).round(2)
    cases.loc[cases["fraud_type"] != "ACCOUNT_TAKEOVER", "attacker_device"] = None
    cases.reset_index().to_csv(out_dir / "fraud_cases.csv", index=False)

    pd.DataFrame([{"account_id": m["account_id"], "customer_id": m["customer_id"]} for m in mules]) \
        .to_csv(out_dir / "mule_accounts.csv", index=False)
    return cases


def main():
    cfg = load_config()
    ref_dir = Path(cfg["paths"]["reference"])
    snap = cfg["project"]["snapshot_date"].replace("-", "")
    load = lambda name: pd.read_csv(ref_dir / f"{name}_{snap}.csv")

    logger.info("Simulating %s days of transactions...", cfg["simulation"]["n_days"])
    result = simulate(load("customers"), load("accounts"), load("cards"), load("merchants"), load("devices"), cfg)
    df = result["transactions"]

    n_files = write_daily_files(df, Path(cfg["paths"]["transactions"]))
    cases = write_ground_truth(df, result["mules"], Path(cfg["paths"]["ground_truth"]))

    logger.info("Created %s transactions in %s daily files", f"{len(df):,}", n_files)
    logger.info("Decline rate: %.2f%%", (df["status"] == "DECLINED").mean() * 100)
    logger.info("Fraud cases: %s", cases["fraud_type"].value_counts().to_dict())
    logger.info("Fraud transactions by role: %s", df["fraud_role"].value_counts().to_dict())
    logger.info("Fraud share of transactions: %.3f%%", df["case_id"].notna().mean() * 100)
    logger.info("Money lost by type: %s", cases.groupby("fraud_type")["amount_lost"].sum().round(0).to_dict())
    logger.info("Mule accounts: %s", len(result["mules"]))


if __name__ == "__main__":
    main()
