-- =====================================================================
-- STAGING LAYER: setup
-- Staging is rebuilt from raw on every run, inside ONE transaction.
-- Raw is the source of truth; staging can always be thrown away and rebuilt.
--
-- Every problem found is recorded in staging.dq_findings, with what was done about it:
--   REMOVED   the row was an exact duplicate and was dropped
--   REPAIRED  the value was fixed (reformatted, or recovered from another trusted field)
--   FLAGGED   the row was kept, but the bad value was set to NULL or marked
--   REJECTED  the row could not be trusted and was left out of staging
-- =====================================================================

DROP TABLE IF EXISTS staging.dq_findings CASCADE;

CREATE TABLE staging.dq_findings (
    finding_id    BIGSERIAL PRIMARY KEY,
    source_table  TEXT    NOT NULL,
    record_key    TEXT    NOT NULL,          -- txn_id, event_id, report_id, customer_id, merchant_id
    finding       TEXT    NOT NULL,
    action        TEXT    NOT NULL CHECK (action IN ('REMOVED', 'REPAIRED', 'FLAGGED', 'REJECTED')),
    _load_id      BIGINT,                    -- lineage: which load and which row of that file
    _row_number   INTEGER
);

COMMENT ON TABLE staging.dq_findings IS 'Every data quality problem found while building staging, and the action taken';
