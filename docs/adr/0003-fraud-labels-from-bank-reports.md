# 0003: Fraud labels in the mart come from the bank's own claims, not the answer key

**Status:** Accepted · **Date:** 2026-11-03

## Context
The simulator knows exactly which transactions were fraud. A real bank does not: it only learns about fraud when a customer reports it, days or weeks later, and some fraud is never reported. Training and evaluating models on perfect labels would give unrealistically good results that would not hold up in a real bank.

## Decision
- `mart.fact_transactions.fraud_label` is 1 only when the bank's own investigation **confirmed** the claim (from `fact_fraud_claims`).
- `claim_status` distinguishes confirmed, rejected and still-open claims; `confirmed_date` records when the label became known.
- The answer key in `data/_ground_truth/` is used only to measure models afterwards, never to build features or labels.
- Mule findings are kept at account level (`dim_account.is_confirmed_mule`), with the date they became known.

## Consequences
- Labels are incomplete: with the current data, every labelled transaction is truly fraud, but only about 58% of successful fraud payments are labelled. Models must be evaluated with that in mind.
- Time matters: a model may only use a label from its `confirmed_date`, and `is_confirmed_mule` must not be used as a feature for transactions before `mule_confirmed_date`. Ignoring this would leak future knowledge into the model.
