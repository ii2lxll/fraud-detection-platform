-- =====================================================================
-- Schema drift tracking and a load summary view.
-- Safe to run many times: the ingestion pipeline re-applies every file in sql/init/
-- on start, so new tables also reach databases created before this file existed.
-- =====================================================================

-- One row each time a raw table is created or gains a column it has never seen before
CREATE TABLE IF NOT EXISTS audit.schema_change_log (
    change_id        BIGSERIAL    PRIMARY KEY,
    table_name       TEXT         NOT NULL,
    change_type      TEXT         NOT NULL CHECK (change_type IN ('TABLE_CREATED', 'COLUMN_ADDED')),
    column_name      TEXT,                                   -- empty for TABLE_CREATED
    first_seen_file  TEXT         NOT NULL,
    load_id          BIGINT       NOT NULL REFERENCES audit.file_load_log (load_id),
    detected_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE audit.schema_change_log IS 'Raw tables created and columns added because a delivered file changed shape';

-- Quick overview: how many files per feed ended in each status, and how many rows were loaded
CREATE OR REPLACE VIEW audit.v_load_summary AS
SELECT feed,
       status,
       count(*)                  AS files,
       coalesce(sum(loaded_rows), 0) AS rows_loaded,
       min(delivery_date)        AS first_delivery,
       max(delivery_date)        AS last_delivery
FROM audit.file_load_log
GROUP BY feed, status
ORDER BY feed, status;
