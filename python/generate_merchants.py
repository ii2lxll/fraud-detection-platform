"""
Generate the merchants reference table (card network / acquirer system).
Output: data/landing/reference/merchants_<snapshot>.csv
"""
import logging
import random
from pathlib import Path

import pandas as pd
from faker import Faker

from generate_customers import load_config, weighted_choice

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def generate_merchants(cfg: dict) -> pd.DataFrame:
    """Build the merchants table from the category settings in the config."""
    seed = cfg["project"]["seed"] + 2
    home = cfg["project"]["home_country"]
    m = cfg["merchants"]

    rng = random.Random(seed)
    fake = Faker(cfg["project"]["locale"])
    fake.seed_instance(seed)

    category_weights = {cat: info["weight"] for cat, info in m["categories"].items()}

    rows = []
    for i in range(1, m["n_merchants"] + 1):
        category = weighted_choice(rng, category_weights)
        info = m["categories"][category]

        # Categories marked BOTH can have physical or online merchants
        channel = info["channel"] if info["channel"] != "BOTH" else rng.choice(["IN_STORE", "ONLINE"])

        if channel == "ONLINE":
            is_foreign = rng.random() < m["foreign_online_share"]
            country = rng.choice(m["foreign_countries"]) if is_foreign else home
            city = state = None                       # online merchants have no store location
        else:
            country, city, state = home, fake.city(), fake.state_abbr()

        rows.append({
            "merchant_id": f"MER{i:05d}",
            "merchant_name": fake.company(),
            "category": category,
            "mcc": info["mcc"],
            "channel": channel,
            "country": country,
            "city": city,
            "state": state,
            "risk_tier": info["risk"],
        })

    return pd.DataFrame(rows)


def main():
    cfg = load_config()
    merchants = generate_merchants(cfg)

    out_dir = Path(cfg["paths"]["reference"])
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot = cfg["project"]["snapshot_date"].replace("-", "")
    out_file = out_dir / f"merchants_{snapshot}.csv"
    merchants.to_csv(out_file, index=False)

    logger.info("Saved %s merchants to %s", f"{len(merchants):,}", out_file)
    logger.info("Risk tiers: %s", merchants["risk_tier"].value_counts().to_dict())
    logger.info("Foreign merchants: %s", (merchants["country"] != cfg["project"]["home_country"]).sum())


if __name__ == "__main__":
    main()
    