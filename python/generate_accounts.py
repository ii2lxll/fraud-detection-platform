"""
Generate accounts and debit cards (account and card management systems).
Input:  data/landing/reference/customers_<snapshot>.csv
Output: data/landing/reference/accounts_<snapshot>.csv
        data/landing/reference/cards_<snapshot>.csv
"""
import logging
import math
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from faker import Faker

from generate_customers import load_config, weighted_choice

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

CARD_STATUS = {"ACTIVE": "ACTIVE", "DORMANT": "INACTIVE", "CLOSED": "CANCELLED"}


def random_date_between(rng: random.Random, start: date, end: date) -> date:
    """Pick a random date from start to end (inclusive)."""
    return start + timedelta(days=rng.randint(0, (end - start).days))


def generate_accounts(customers: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the accounts and cards tables for the given customers."""
    seed = cfg["project"]["seed"] + 1        # separate random stream from customers
    snapshot = date.fromisoformat(cfg["project"]["snapshot_date"])
    a, k = cfg["accounts"], cfg["cards"]
    validity_days = k["validity_years"] * 365

    rng = random.Random(seed)
    fake = Faker(cfg["project"]["locale"])
    fake.seed_instance(seed)

    accounts, cards = [], []
    for cust in customers.itertuples(index=False):
        joined = date.fromisoformat(str(cust.customer_since))
        main_type = "BUSINESS_CHECKING" if cust.segment == "SMALL_BUSINESS" else "CHECKING"

        products = [(main_type, joined)]                       # everyone gets a main account
        if rng.random() < a["savings_probability"][cust.segment]:
            products.append(("SAVINGS", random_date_between(rng, joined, snapshot)))

        for acc_type, open_date in products:
            account_id = f"ACC{len(accounts) + 1:07d}"
            status = weighted_choice(rng, a["status_weights"])
            median = a["median_balance"][cust.segment] * (2 if acc_type == "SAVINGS" else 1)

            accounts.append({
                "account_id": account_id,
                "customer_id": cust.customer_id,
                "account_type": acc_type,
                "open_date": open_date,
                "status": status,
                "close_date": random_date_between(rng, open_date, snapshot) if status == "CLOSED" else None,
                "currency": a["currency"],
                "current_balance": 0.0 if status == "CLOSED"
                else round(rng.lognormvariate(math.log(median), a["balance_sigma"]), 2),
            })

            if acc_type == "SAVINGS":
                continue                                      # debit cards only on checking accounts

            # Cards are reissued every N years: find the most recent issue date
            reissues = (snapshot - open_date).days // validity_days
            issue_date = open_date + timedelta(days=reissues * validity_days)

            cards.append({
                "card_id": f"CARD{len(cards) + 1:07d}",
                "account_id": account_id,
                "card_token": fake.uuid4(),
                "masked_pan": f"**** **** **** {rng.randint(0, 9999):04d}",
                "network": weighted_choice(rng, k["network_weights"]),
                "issue_date": issue_date,
                "expiry_date": issue_date + timedelta(days=validity_days),
                "card_status": CARD_STATUS[status],
            })

    return pd.DataFrame(accounts), pd.DataFrame(cards)


def main():
    cfg = load_config()
    ref_dir = Path(cfg["paths"]["reference"])
    snapshot = cfg["project"]["snapshot_date"].replace("-", "")

    customers = pd.read_csv(ref_dir / f"customers_{snapshot}.csv")
    logger.info("Loaded %s customers", f"{len(customers):,}")

    accounts, cards = generate_accounts(customers, cfg)

    accounts.to_csv(ref_dir / f"accounts_{snapshot}.csv", index=False)
    cards.to_csv(ref_dir / f"cards_{snapshot}.csv", index=False)

    logger.info("Saved %s accounts and %s cards", f"{len(accounts):,}", f"{len(cards):,}")
    logger.info("Account types: %s", accounts["account_type"].value_counts().to_dict())
    logger.info("Account status: %s", accounts["status"].value_counts().to_dict())


if __name__ == "__main__":
    main()
