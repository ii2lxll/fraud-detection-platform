# Architecture Decision Records

Short records of the important design decisions in this project: the context, the decision, and its consequences, including the downsides accepted. New decisions get the next number; old ones are never deleted, only marked as superseded.

| # | Decision | Status |
|---|---|---|
| [0001](0001-raw-layer-stores-text-as-delivered.md) | The raw layer stores every value as text, exactly as delivered | Accepted |
| [0002](0002-staging-rebuilt-in-one-transaction.md) | Staging is rebuilt from raw in one transaction, with every problem recorded | Accepted |
| [0003](0003-fraud-labels-from-bank-reports.md) | Fraud labels in the mart come from the bank's own claims, not the answer key | Accepted |
| [0004](0004-export-csv-to-sas.md) | The mart reaches SAS OnDemand as CSV files with a manifest | Accepted |
