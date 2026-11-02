"""
Generate registered customer devices (digital banking system).
Input:  data/landing/reference/customers_<snapshot>.csv
Output: data/landing/reference/devices_<snapshot>.csv
"""
import logging
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from faker import Faker

from generate_accounts import random_date_between
from generate_customers import load_config, weighted_choice

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

MOBILE_MODELS = {
    "IOS": ["iPhone 13", "iPhone 14", "iPhone 15", "iPhone 16"],
    "ANDROID": ["Samsung Galaxy S23", "Samsung Galaxy A54", "Google Pixel 8", "Xiaomi Redmi Note 13"],
}


def generate_devices(customers: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Build the devices table: which phones and browsers each customer uses."""
    seed = cfg["project"]["seed"] + 3
    snapshot = date.fromisoformat(cfg["project"]["snapshot_date"])
    d = cfg["devices"]

    rng = random.Random(seed)
    fake = Faker(cfg["project"]["locale"])
    fake.seed_instance(seed)

    rows = []
    for cust in customers.itertuples(index=False):
        # 1. Decide which device types this customer uses
        if cust.preferred_channel == "MOBILE":
            types = [weighted_choice(rng, d["mobile_os_weights"])]
        elif cust.preferred_channel == "WEB":
            types = ["WEB_BROWSER"]
        else:                                          # BRANCH customers may have no device
            no_device = rng.random() < d["branch_no_device_probability"]
            types = [] if no_device else [weighted_choice(rng, d["extra_device_weights"])]

        if types and rng.random() < d["extra_device_probability"]:
            types.append(weighted_choice(rng, d["extra_device_weights"]))

        # 2. Create each device, first seen after the customer joined (within 5 years)
        joined = date.fromisoformat(str(cust.customer_since))
        earliest = max(joined, snapshot - timedelta(days=5 * 365))

        for device_type in types:
            is_web = device_type == "WEB_BROWSER"
            rows.append({
                "device_id": fake.uuid4(),
                "customer_id": cust.customer_id,
                "device_type": device_type,
                "device_model": None if is_web else rng.choice(MOBILE_MODELS[device_type]),
                "user_agent": fake.user_agent() if is_web else None,
                "first_seen": random_date_between(rng, earliest, snapshot),
            })

    return pd.DataFrame(rows)


def main():
    cfg = load_config()
    ref_dir = Path(cfg["paths"]["reference"])
    snapshot = cfg["project"]["snapshot_date"].replace("-", "")

    customers = pd.read_csv(ref_dir / f"customers_{snapshot}.csv")
    devices = generate_devices(customers, cfg)
    devices.to_csv(ref_dir / f"devices_{snapshot}.csv", index=False)

    no_device = len(set(customers["customer_id"]) - set(devices["customer_id"]))
    logger.info("Saved %s devices", f"{len(devices):,}")
    logger.info("Device types: %s", devices["device_type"].value_counts().to_dict())
    logger.info("Customers with no device: %s", no_device)


if __name__ == "__main__":
    main()