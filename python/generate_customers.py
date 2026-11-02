"""
Generate the customers reference table (core banking system snapshot).
Output: data/landing/reference/customers_<snapshot_date>.csv
"""
import logging
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml
from faker import Faker

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def load_config(path: str = "config/config.yaml") -> dict:
    """Read the project settings from the YAML config file."""
    with open(path) as f:
        return yaml.safe_load(f)


def weighted_choice(rng: random.Random, weights: dict) -> str:
    """Pick one key from a {option: weight} dictionary."""
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def generate_customers(cfg: dict) -> pd.DataFrame:
    """Build the customers table from the config settings."""
    seed = cfg["project"]["seed"]
    snapshot = date.fromisoformat(cfg["project"]["snapshot_date"])
    c = cfg["customers"]

    fake = Faker(cfg["project"]["locale"])
    fake.seed_instance(seed)        # seed this Faker object only
    rng = random.Random(seed)       # our own random generator (no global state)

    rows = []
    for i in range(1, c["n_customers"] + 1):
        segment = weighted_choice(rng, c["segment_weights"])
        first, last = fake.first_name(), fake.last_name()

        # Age 18-85 on the snapshot date
        dob = snapshot - timedelta(days=rng.randint(18 * 365, 85 * 365))

        # Joined after turning 18, within the last 15 years, not after the snapshot
        adult_date = dob + timedelta(days=18 * 365)
        earliest_join = max(adult_date, snapshot - timedelta(days=15 * 365))
        customer_since = earliest_join + timedelta(days=rng.randint(0, (snapshot - earliest_join).days))

        rows.append({
            "customer_id": f"CUST{i:06d}",
            "first_name": first,
            "last_name": last,
            "date_of_birth": dob,
            "email": f"{first.lower()}.{last.lower()}{rng.randint(1, 99)}@{fake.free_email_domain()}",
            "phone": fake.phone_number(),
            "street_address": fake.street_address(),
            "city": fake.city(),
            "state": fake.state_abbr(),
            "postcode": fake.postcode(),
            "occupation": fake.job(),
            "segment": segment,
            "income_band": rng.choice(c["income_bands"][segment]),
            "kyc_risk_rating": weighted_choice(rng, c["kyc_weights"]),
            "customer_since": customer_since,
            "preferred_channel": weighted_choice(rng, c["channel_weights"]),
        })

    return pd.DataFrame(rows)


def main():
    cfg = load_config()
    logger.info("Generating %s customers...", cfg["customers"]["n_customers"])

    df = generate_customers(cfg)

    out_dir = Path(cfg["paths"]["reference"])
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot = cfg["project"]["snapshot_date"].replace("-", "")
    out_file = out_dir / f"customers_{snapshot}.csv"
    df.to_csv(out_file, index=False)

    logger.info("Saved %s rows to %s", f"{len(df):,}", out_file)
    logger.info("Segments: %s", df["segment"].value_counts().to_dict())


if __name__ == "__main__":
    main()