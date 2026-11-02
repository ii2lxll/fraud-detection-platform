# Project Roadmap: Fraud Detection & Risk Analytics Platform

End-to-end platform following the real data lifecycle: source systems → ingestion → data quality → warehouse → analytics → AI → operations → delivery.

Status key: ☐ not started · ◐ in progress · ☑ done

---

## Phase 1: Foundations
- ☑ Repository, folder structure, virtual environment, `.gitignore`
- ☑ Central configuration file (`config/config.yaml`)
- ☑ Structured logging (no `print` in pipeline code)
- ☑ Automated tests with `pytest`
- ☐ Code formatting and linting with Ruff + pre-commit hooks
- ☐ CI with GitHub Actions (tests run on every push)
- ☐ GitHub Projects board with issues per task

## Phase 2: Synthetic Source Systems (Faker)
- ☑ Customers (core banking snapshot)
- ☑ Accounts and cards
- ☑ Merchants
- ☑ Devices and login events (JSON Lines)
- ☑ Daily transactions with realistic behaviour
- ☑ Fraud patterns: account takeover, scams (APP), card-not-present, money mules
- ☑ Fraud reports with realistic delay, unreported fraud and false claims
- ☑ Deliberately injected data quality issues, schema drift, late and incomplete files
- ☑ Daily delivery manifests (row counts + checksums)
- ☑ Ground-truth answer key (kept separate, never used for training)

## Phase 3: Ingestion & Data Quality
- ☑ PostgreSQL in Docker
- ☑ Ingestion pipeline with manifest validation and load log
- ◐ Data contracts: validation rules built in SQL (types, formats, keys, ranges); formal Pandera contracts later
- ☑ Data quality findings (removed / repaired / flagged / rejected) and automated report: 100% recall, 0 false positives, all repairs verified
- ☐ **AI data quality monitoring:** anomaly detection on daily delivery metrics

## Phase 4: Data Warehouse
- ☑ Layers: `raw` → `staging` → `mart`, each rebuilt in one transaction
- ☑ Star schema: 3 facts (transactions, logins, fraud claims) + 4 dimensions; fraud labels from the bank's own claims; no PII
- ☑ Data dictionary generated from the database (test fails if any column is undocumented)
- ☑ Architecture Decision Records (4 so far)
- ☑ SAS-ready CSV export with manifest

## Phase 5: Analytics & Modelling (SAS core)
- ☐ Exploratory data analysis in SAS
- ☐ Feature engineering (velocity, behaviour deviation, device novelty, balance drain)
- ☐ Rules-based baseline
- ☐ SAS models: logistic regression scorecard, gradient boosting / random forest
- ☐ Python benchmark: XGBoost
- ☐ Time-based validation (no leakage), cost-based threshold selection

## Phase 6: Advanced AI
- ☐ **Explainable AI (SHAP):** per-transaction reason codes
- ☐ **Graph analytics (NetworkX):** mule network detection
- ☐ **Unsupervised anomaly detection (Isolation Forest):** unknown fraud patterns
- ☐ **LLM alert summaries (local model via Ollama):** plain-English case notes for investigators; the LLM explains, it never decides

## Phase 7: Operations
- ◐ Orchestration: one-command simulation runner done; Prefect for the daily pipeline later
- ☐ Batch scoring and daily prioritised alert queue
- ☐ Monitoring: performance tracking and PSI drift
- ☐ Automated weekly monitoring report
- ☐ Champion / challenger model comparison

## Phase 8: Delivery
- ☐ Streamlit investigator app (alert queue + explanations)
- ☐ Tableau Public dashboard (executive + analyst views)
- ☐ Business impact report ($ fraud prevented, analyst hours saved)
- ☐ Model card
- ☐ README with architecture diagram, results and screenshots
