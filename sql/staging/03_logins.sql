-- =====================================================================
-- STAGING: login events
-- Timestamps arrive as ISO text ("2026-06-01T08:14:02") or as epoch milliseconds ("1780282686000").
-- A missing device can sometimes be recovered from another event in the same login session.
-- =====================================================================

DROP TABLE IF EXISTS staging.logins CASCADE;

CREATE TEMP TABLE logins_checked ON COMMIT DROP AS
SELECT r.*,
       row_number() OVER (PARTITION BY r.event_id ORDER BY r._load_id, r._row_number) AS copy_number,
       CASE
           WHEN r.event_timestamp ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$'
               THEN replace(r.event_timestamp, 'T', ' ')::timestamp
           WHEN r.event_timestamp ~ '^\d{12,14}$'
               THEN to_timestamp(r.event_timestamp::bigint / 1000.0) AT TIME ZONE 'UTC'
       END AS timestamp_clean,
       coalesce(r.device_id, max(r.device_id) OVER (PARTITION BY r.session_id)) AS device_clean
FROM raw.logins r;

INSERT INTO staging.dq_findings (source_table, record_key, finding, action, _load_id, _row_number)
SELECT 'logins', c.event_id, f.finding, f.action, c._load_id, c._row_number
FROM logins_checked c
CROSS JOIN LATERAL (VALUES
    ('DUPLICATE',             'REMOVED',  c.copy_number > 1),
    ('NONSTANDARD_TIMESTAMP', 'REPAIRED', c.copy_number = 1 AND c.event_timestamp ~ '^\d{12,14}$'),
    ('INVALID_TIMESTAMP',     'REJECTED', c.copy_number = 1 AND c.timestamp_clean IS NULL),
    ('NONSTANDARD_COUNTRY',   'REPAIRED', c.copy_number = 1 AND c.ip_country <> upper(c.ip_country)),
    ('MISSING_DEVICE',        CASE WHEN c.device_clean IS NULL THEN 'FLAGGED' ELSE 'REPAIRED' END,
                              c.copy_number = 1 AND c.device_id IS NULL)
) AS f(finding, action, applies)
WHERE f.applies;

CREATE TABLE staging.logins AS
SELECT event_id,
       timestamp_clean     AS event_timestamp,
       customer_id, session_id,
       device_clean        AS device_id,
       channel, ip_address,
       upper(ip_country)   AS ip_country,
       result, failure_reason,
       _load_id    AS source_load_id,
       _row_number AS source_row_number
FROM logins_checked
WHERE copy_number = 1 AND timestamp_clean IS NOT NULL;

ALTER TABLE staging.logins ADD PRIMARY KEY (event_id);
CREATE INDEX ix_stg_logins_customer_time ON staging.logins (customer_id, event_timestamp);
