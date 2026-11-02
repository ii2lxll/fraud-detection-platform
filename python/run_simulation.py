"""
Run the whole simulation in the right order, with one command:

    python3 python/run_simulation.py

reference tables -> transactions (with fraud) -> logins -> fraud reports -> delivery to landing
"""
import logging
import time

import deliver_files
import generate_accounts
import generate_customers
import generate_devices
import generate_fraud_reports
import generate_logins
import generate_merchants
import generate_transactions

logger = logging.getLogger("run_simulation")

STEPS = [
    ("Customers", generate_customers.main),
    ("Accounts and cards", generate_accounts.main),
    ("Merchants", generate_merchants.main),
    ("Devices", generate_devices.main),
    ("Transactions and fraud", generate_transactions.main),
    ("Login events", generate_logins.main),
    ("Fraud reports", generate_fraud_reports.main),
    ("Delivery to landing zone", deliver_files.main),
]


def main():
    started = time.perf_counter()
    for number, (name, step) in enumerate(STEPS, start=1):
        t0 = time.perf_counter()
        logger.info("Step %s/%s: %s", number, len(STEPS), name)
        step()
        logger.info("Step %s/%s done in %.1fs", number, len(STEPS), time.perf_counter() - t0)
    logger.info("Simulation complete in %.1fs", time.perf_counter() - started)


if __name__ == "__main__":
    main()
