"""
Fraud scenarios hidden inside normal banking activity.

Each planner returns event records like the normal ones, plus three hidden label fields:
    case_id     - groups all transactions of one fraud incident
    fraud_type  - ACCOUNT_TAKEOVER, SCAM or CARD_NOT_PRESENT
    fraud_role  - VICTIM (money leaving the victim), MULE_INBOUND (stolen money arriving at a mule),
                  MULE_CASHOUT (the mule moving the money out)
The labels are ground truth: they are NEVER written to the landing files the pipeline reads.

Amounts that depend on the balance at that moment (e.g. "take 60% of whatever is there")
are given as an `amount_rule` and resolved by the engine when the event is processed.
"""
import math
import random
import uuid
from datetime import date, datetime, time, timedelta


# ---------------------------------------------------------------- helpers
def between(rng: random.Random, pair: list) -> float:
    return rng.uniform(pair[0], pair[1])


def int_between(rng: random.Random, pair: list) -> int:
    return rng.randint(pair[0], pair[1])


def safe_ts(ts: datetime) -> datetime:
    """Keep seconds at most 58, so a linked credit 1 second later never crosses midnight."""
    return ts.replace(second=min(ts.second, 58), microsecond=0)


def age_on(profile: dict, day: date) -> int:
    return (day - profile["date_of_birth"]).days // 365


def external_payee(rng: random.Random, ctx: dict) -> tuple:
    return f"EXT{rng.randint(10**9, 10**10 - 1)}", rng.choice(ctx["external_banks"])


def fraud_payee(rng: random.Random, mules: list, to_mule_share: float, ctx: dict) -> tuple:
    """Stolen money goes to a mule at our bank, or straight out to another bank."""
    if mules and rng.random() < to_mule_share:
        return rng.choice(mules)["account_id"], ctx["bank_code"]
    return external_payee(rng, ctx)


def own_device(rng: random.Random, profile: dict, ctx: dict) -> tuple:
    """The customer's own registered device (used in scams: the victim pays themselves)."""
    if not profile["devices"]:
        return "BRANCH", None
    device_id = rng.choice(profile["devices"])
    return ("WEB" if ctx["device_type"][device_id] == "WEB_BROWSER" else "MOBILE"), device_id


def fraud_event(ts, profile, txn_type, case_id, fraud_type, role="VICTIM", **fields) -> dict:
    event = {
        "ts": safe_ts(ts), "account_id": profile["account_id"], "txn_type": txn_type, "direction": "DR",
        "amount": None, "channel": None, "card_id": None, "merchant_id": None,
        "counterparty_account": None, "counterparty_bank": None, "device_id": None,
        "always_attempt": True,          # criminals (and pressured victims) try even when funds are short
        "case_id": case_id, "fraud_type": fraud_type, "fraud_role": role,
    }
    event.update(fields)
    return event


# ---------------------------------------------------------------- mule recruitment
def select_mules(profiles: list, cfg: dict, rng: random.Random) -> list:
    """Money mules: young customers with recently opened accounts."""
    f = cfg["fraud"]
    snapshot = date.fromisoformat(cfg["project"]["snapshot_date"])
    candidates = [
        p for p in profiles
        if p["segment"] == f["mule_segment"]
        and age_on(p, snapshot) <= f["mule_max_age"]
        and (snapshot - p["customer_since"]).days <= f["mule_max_tenure_days"]
    ]
    n_mules = round(cfg["customers"]["n_customers"] * f["mule_share"])
    return rng.sample(candidates, min(n_mules, len(candidates)))


# ---------------------------------------------------------------- scenario 1: account takeover
def plan_account_takeover(rng, victim, case_id, day, mules, ctx, cfg) -> list:
    """A criminal logs in from THEIR OWN device (often at night) and drains the account in minutes."""
    a = cfg["fraud"]["account_takeover"]
    if rng.random() < a["night_share"]:
        hour = rng.choice(a["night_hours"])
    else:
        hour = rng.choices(range(24), weights=ctx["t"]["hour_weights"])[0]
    ts = datetime.combine(day, time(hour, rng.randint(0, 59), rng.randint(0, 58)))
    attacker_device = str(uuid.UUID(int=rng.getrandbits(128)))     # never seen before
    channel = rng.choice(["MOBILE", "WEB"])

    events = []
    for _ in range(int_between(rng, a["transfers"])):
        account, bank = fraud_payee(rng, mules, a["to_mule_share"], ctx)
        events.append(fraud_event(
            ts, victim, "TRANSFER_OUT", case_id, "ACCOUNT_TAKEOVER",
            channel=channel, device_id=attacker_device, counterparty_account=account, counterparty_bank=bank,
            amount_rule=("fraction", between(rng, a["drain_fraction"])),
        ))
        ts += timedelta(minutes=between(rng, a["minutes_between"]))
    return events


# ---------------------------------------------------------------- scenario 2: scam (authorised push payment)
def plan_scam(rng, victim, case_id, day, mules, ctx, cfg) -> list:
    """The victim is tricked into sending money themselves: own device, normal hours, ONE new payee."""
    s = cfg["fraud"]["scam"]
    account, bank = fraud_payee(rng, mules, s["to_mule_share"], ctx)
    span = int_between(rng, s["days_span"])
    days = sorted(day + timedelta(days=rng.randint(0, span - 1)) for _ in range(int_between(rng, s["payments"])))
    max_payment = between(rng, s["max_payment"]) * victim["multiplier"] ** 0.5

    events = []
    for d in days:
        ts = datetime.combine(d, time(rng.randint(*s["hours"]), rng.randint(0, 59), rng.randint(0, 58)))
        channel, device = own_device(rng, victim, ctx)
        events.append(fraud_event(
            ts, victim, "TRANSFER_OUT", case_id, "SCAM",
            channel=channel, device_id=device, counterparty_account=account, counterparty_bank=bank,
            amount_rule=("fraction_capped", between(rng, s["balance_fraction"]), round(max_payment, 2)),
        ))
    return events


# ---------------------------------------------------------------- scenario 3: card-not-present
def plan_card_not_present(rng, victim, case_id, day, ctx, cfg) -> list:
    """Stolen card details: a tiny test purchase, then a burst of online buys at high-risk merchants."""
    c = cfg["fraud"]["card_not_present"]
    if victim["card_id"] is None or not (ctx["risky_home"] or ctx["risky_foreign"]):
        return []

    def pick_merchant():
        if ctx["risky_foreign"] and (rng.random() < c["foreign_share"] or not ctx["risky_home"]):
            return rng.choice(ctx["risky_foreign"])
        return rng.choice(ctx["risky_home"])

    start = datetime.combine(day, time(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 58)))
    common = {"card_id": victim["card_id"], "channel": "ECOM"}
    events = [fraud_event(start, victim, "CARD_PURCHASE", case_id, "CARD_NOT_PRESENT",
                          merchant_id=pick_merchant(), amount=round(between(rng, c["test_amount"]), 2), **common)]

    window_minutes = between(rng, c["hours_span"]) * 60
    offsets = sorted(rng.uniform(5, window_minutes) for _ in range(int_between(rng, c["purchases"])))
    for minutes in offsets:
        events.append(fraud_event(start + timedelta(minutes=minutes), victim, "CARD_PURCHASE", case_id,
                                  "CARD_NOT_PRESENT", merchant_id=pick_merchant(),
                                  amount=round(between(rng, c["amount"]), 2), **common))
    return events


# ---------------------------------------------------------------- mule cash-out (triggered by the engine)
def plan_mule_cashout(rng, mule, received_at, received_amount, case_id, fraud_type, ctx, cfg) -> list:
    """Hours after stolen money lands, the mule forwards it to another bank or withdraws it at ATMs."""
    m = cfg["fraud"]["mule_cashout"]
    ts = received_at + timedelta(minutes=int_between(rng, m["delay_minutes"]))
    to_move = received_amount * between(rng, m["received_fraction"])       # the mule keeps a small cut
    events = []

    if mule["card_id"] is None or rng.random() < m["onward_transfer_share"]:
        account, bank = external_payee(rng, ctx)
        channel, device = own_device(rng, mule, ctx)
        events.append(fraud_event(ts, mule, "TRANSFER_OUT", case_id, fraud_type, "MULE_CASHOUT",
                                  channel=channel, device_id=device, counterparty_account=account,
                                  counterparty_bank=bank, amount=round(to_move, 2)))
    else:
        remaining = math.floor(to_move / 20) * 20                         # ATMs pay out $20 notes
        while remaining >= 20:
            cash = min(remaining, m["atm_max_withdrawal"])
            events.append(fraud_event(ts, mule, "ATM_WITHDRAWAL", case_id, fraud_type, "MULE_CASHOUT",
                                      card_id=mule["card_id"], channel="ATM", amount=float(cash)))
            remaining -= cash
            ts += timedelta(minutes=rng.randint(10, 90))

    return [e for e in events if e["ts"] < ctx["sim_end"]]


# ---------------------------------------------------------------- schedule all cases
def plan_fraud(profiles: list, mules: list, ctx: dict, cfg: dict, rng: random.Random) -> list:
    """Create every fraud case in the config, at random days within the simulation."""
    f = cfg["fraud"]
    start = date.fromisoformat(cfg["simulation"]["start_date"])
    n_days = cfg["simulation"]["n_days"]
    mule_ids = {m["account_id"] for m in mules}

    pool = [p for p in profiles if p["account_id"] not in mule_ids]      # mules aren't victims
    digital = [p for p in pool if p["devices"]]                          # ATO needs online banking
    with_card = [p for p in pool if p["card_id"]]
    scam_weights = [f["scam"]["older_victim_weight"] if age_on(p, start) >= f["scam"]["older_victim_age"] else 1
                    for p in pool]

    events, case_no = [], 0
    for fraud_type, n_cases in f["cases"].items():
        for _ in range(n_cases):
            case_no += 1
            case_id = f"CASE{case_no:05d}"
            if fraud_type == "ACCOUNT_TAKEOVER" and digital:
                day = start + timedelta(days=rng.randint(0, n_days - 1))
                events += plan_account_takeover(rng, rng.choice(digital), case_id, day, mules, ctx, cfg)
            elif fraud_type == "SCAM" and pool:
                last_start = max(0, n_days - f["scam"]["days_span"][1])
                day = start + timedelta(days=rng.randint(0, last_start))
                victim = rng.choices(pool, weights=scam_weights)[0]
                events += plan_scam(rng, victim, case_id, day, mules, ctx, cfg)
            elif fraud_type == "CARD_NOT_PRESENT" and with_card:
                day = start + timedelta(days=rng.randint(0, n_days - 1))
                events += plan_card_not_present(rng, rng.choice(with_card), case_id, day, ctx, cfg)

    return [e for e in events if e["ts"] < ctx["sim_end"]]
