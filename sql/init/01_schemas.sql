-- =====================================================================
-- Runs automatically the FIRST time the database container starts.
-- Creates the warehouse layers and the audit tables the pipeline writes to.
-- =====================================================================

-- Warehouse layers
CREATE SCHEMA IF NOT EXISTS raw;       -- files loaded exactly as delivered (messy, all text)
CREATE SCHEMA IF NOT EXISTS staging;   -- cleaned, typed, de-duplicated
CREATE SCHEMA IF NOT EXISTS mart;      -- business-ready star schema for analytics and SAS
CREATE SCHEMA IF NOT EXISTS audit;     -- pipeline bookkeeping: what was loaded, when, and how it went

COMMENT ON SCHEMA raw     IS 'Landing files loaded as-is, every column as text, plus load metadata';
COMMENT ON SCHEMA staging IS 'Cleaned and validated data with correct types';
COMMENT ON SCHEMA mart    IS 'Star schema: fact and dimension tables for reporting and modelling';
COMMENT ON SCHEMA audit   IS 'Load log, manifest checks and data quality results';

-- One row per file the pipeline tries to load
CREATE TABLE IF NOT EXISTS audit.file_load_log (
    load_id          BIGSERIAL    PRIMARY KEY,
    feed             TEXT         NOT NULL,               -- transactions, logins, fraud_reports, reference
    file_name        TEXT         NOT NULL,
    delivery_date    DATE         NOT NULL,               -- from the manifest
    expected_rows    INTEGER,                             -- what the manifest says
    loaded_rows      INTEGER,                             -- what we actually found
    expected_sha256  CHAR(64),
    actual_sha256    CHAR(64),
    status           TEXT         NOT NULL
                     CHECK (status IN ('LOADED', 'REJECTED_MANIFEST', 'FAILED', 'SKIPPED_DUPLICATE')),
    message          TEXT,
    started_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ
);

-- The same file can only be loaded successfully once (protects against double-loading)
CREATE UNIQUE INDEX IF NOT EXISTS ux_file_loaded_once
    ON audit.file_load_log (feed, file_name, actual_sha256)
    WHERE status = 'LOADED';

COMMENT ON TABLE audit.file_load_log IS 'One row per file load attempt, with manifest comparison and outcome';
