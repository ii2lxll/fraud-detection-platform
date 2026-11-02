# Business Case: Transaction Fraud Detection Platform

> **Note:** Northbridge Bank is a fictional retail bank. The project is country-neutral and all data is synthetic. No real customer data is used.

**Author:** Abdullah Khaled Hasan Barabaa
**Status:** Draft v1.1 (country-neutral)
**Last updated:** 2026-11-02

---

## 1. Background

Northbridge Bank serves around 3,000 retail customers (scaled down for this project) through its mobile app, online banking, debit cards and ATMs. Like banks worldwide, it faces a rising volume of digital fraud:

- **Account takeover (ATO):** criminals obtain a customer's login credentials (phishing, malware, SIM swap), log in from a new device and drain the account through rapid transfers.
- **Scams / authorised push payment (APP) fraud:** customers are manipulated (for example, impersonation, romance or investment scams) into transferring money themselves. The customer's own device and credentials are used, which makes these hard to detect.
- **Card-not-present (CNP) fraud:** stolen card details are used for online purchases, often starting with a small "test" transaction.
- **Money mule accounts:** accounts that receive stolen funds from many victims and cash them out quickly.

Today the bank relies on a small set of static rules (for example, "block transfers above $50,000"). Fraud losses are rising, and the investigation team is overwhelmed by alerts that are mostly false positives.

## 2. Problem statement

> The bank cannot reliably identify fraudulent transactions early enough to prevent losses, and its current rules generate too many false alerts for the investigation team to handle.

## 3. Objectives

1. Build a **reliable data pipeline** that ingests daily transaction, login and fraud-report feeds from separate source systems, validates them, and loads them into an analytics warehouse.
2. Develop a **fraud risk score** for every transaction, combining business rules with machine learning models (SAS as the primary modelling platform).
3. Produce a **daily prioritised alert queue** with reason codes, sized to the investigation team's capacity.
4. **Monitor** model performance and data drift over time, and report results to management through a dashboard.

## 4. Stakeholders

| Stakeholder | Interest |
|---|---|
| Head of Fraud Risk | Lower fraud losses, regulatory compliance |
| Fraud investigation team | Fewer, higher-quality alerts with clear reasons |
| Customer experience | Fewer legitimate customers blocked or inconvenienced |
| Data engineering / IT | Reliable, documented, maintainable pipeline |
| Model risk / audit | Explainable models, documented validation |

## 5. Success metrics

Targets are illustrative and will be refined after the baseline is measured.

| Metric | Definition | Target |
|---|---|---|
| Fraud detection rate (recall) | Share of confirmed fraud transactions flagged | ≥ 70% |
| Value detection rate | Share of fraud **dollar value** flagged | ≥ 80% |
| Alert precision | Share of alerts that are confirmed fraud | ≥ 10% (vs. ~1–2% for current rules) |
| Alert volume | Alerts per day | ≤ investigation capacity (assumed 50/day) |
| False positive ratio | Legitimate transactions flagged per fraud caught | ≤ 10 : 1 |
| Pipeline reliability | Daily loads completed with data quality checks passed | ≥ 99% |

**Why not accuracy?** Fraud is rare (roughly 0.5–1% of transactions). A model that predicts "not fraud" for everything is 99% accurate and completely useless. Evaluation focuses on recall, precision, PR-AUC and dollar value saved.

## 6. Data sources

| Source system | Feed | Format | Frequency |
|---|---|---|---|
| Core banking | Customers, accounts | CSV | Monthly snapshot |
| Card management | Merchants | CSV | Static reference |
| Payments hub | Transactions | CSV | Daily |
| Digital banking | Login / session events | JSON Lines | Daily |
| Fraud operations | Fraud reports and investigation outcomes | CSV | Daily |

Each daily delivery comes with a manifest file (row counts and checksums) so the pipeline can confirm the file arrived complete.

**Known real-world data challenges** (deliberately simulated):

- **Label delay:** fraud is only known after a customer complains or a chargeback arrives, days or weeks later. The most recent transactions are therefore under-labelled.
- **Unreported and disputed fraud:** not all fraud gets reported, and some customer claims turn out to be legitimate after investigation.
- **Data quality issues:** duplicates, missing values, inconsistent formats, orphan records, late-arriving data, schema changes and incomplete file deliveries.

## 7. Scope

**In scope:** data ingestion, data quality, warehouse design, exploratory analysis, feature engineering, rules and ML models, batch scoring, alert generation, monitoring, dashboard, documentation.

**Out of scope:** real-time (sub-second) scoring, case management software, customer notification systems, real customer data.

## 8. Constraints and assumptions

- **Budget:** free and student tools only (SAS OnDemand for Academics, Python, PostgreSQL, Tableau Public, GitHub).
- **Platform boundary:** SAS OnDemand runs in the cloud and cannot reach the local Postgres database, so curated tables are exported and uploaded to SAS. This is documented as an architecture decision.
- **Privacy:** synthetic data only, designed in line with general data protection principles (such as GDPR). No real names, ID numbers or card numbers.
- **Capacity:** the investigation team can review about 50 alerts per day.

## 9. Deliverables

1. Synthetic multi-source data generator
2. Ingestion pipeline with load logging
3. Data quality framework and report
4. PostgreSQL warehouse (raw → staging → mart) with data dictionary
5. SAS EDA and feature engineering programs
6. Rules baseline, SAS models, and Python benchmark
7. Batch scoring and daily alert queue
8. Monitoring (performance and PSI drift)
9. Tableau dashboard
10. Model card and README

## 10. Risks

| Risk | Mitigation |
|---|---|
| Label leakage (using information not available at transaction time) | Strict time-based splits; features built only from past data |
| Class imbalance distorts model training | Class weighting / resampling; evaluate with PR-AUC |
| Synthetic data too "easy" | Include hard cases (scams on the customer's own device), label noise and overlap with normal behaviour |
| Scope creep | Phase-by-phase delivery with a working version at each stage |
