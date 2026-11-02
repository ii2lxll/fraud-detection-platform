-- =====================================================================
-- MART: fact_logins (one row per login attempt)
-- =====================================================================

DROP TABLE IF EXISTS mart.fact_logins CASCADE;

CREATE TABLE mart.fact_logins AS
SELECT l.event_id,
       to_char(l.event_timestamp, 'YYYYMMDD')::integer AS date_key,
       l.event_timestamp,
       extract(hour FROM l.event_timestamp)::smallint  AS hour_of_day,
       c.customer_key,
       l.session_id,
       l.device_id,
       CASE WHEN l.device_id IS NOT NULL THEN (d.device_id IS NOT NULL)::int::smallint END AS device_known,
       l.channel,
       l.ip_address,
       l.ip_country,
       (l.ip_country <> current_setting('mart.home_country'))::int::smallint AS is_foreign_ip,
       l.result,
       (l.result = 'FAILED')::int::smallint             AS is_failed,
       l.failure_reason
FROM staging.logins l
JOIN mart.dim_customer c ON c.customer_id = l.customer_id
LEFT JOIN staging.devices d ON d.device_id = l.device_id AND d.customer_id = l.customer_id;

ALTER TABLE mart.fact_logins ADD PRIMARY KEY (event_id);
ALTER TABLE mart.fact_logins ADD FOREIGN KEY (date_key) REFERENCES mart.dim_date (date_key);
ALTER TABLE mart.fact_logins ADD FOREIGN KEY (customer_key) REFERENCES mart.dim_customer (customer_key);
CREATE INDEX ix_fact_logins_customer_time ON mart.fact_logins (customer_key, event_timestamp);

COMMENT ON TABLE  mart.fact_logins IS 'One row per digital banking login attempt';
COMMENT ON COLUMN mart.fact_logins.event_id IS 'Login event ID';
COMMENT ON COLUMN mart.fact_logins.date_key IS 'Login date (dim_date)';
COMMENT ON COLUMN mart.fact_logins.event_timestamp IS 'Date and time of the attempt';
COMMENT ON COLUMN mart.fact_logins.hour_of_day IS 'Hour of the attempt, 0-23';
COMMENT ON COLUMN mart.fact_logins.customer_key IS 'Customer whose account was accessed (dim_customer)';
COMMENT ON COLUMN mart.fact_logins.session_id IS 'Groups the attempts of one login session';
COMMENT ON COLUMN mart.fact_logins.device_id IS 'Device fingerprint; NULL if missing in the source';
COMMENT ON COLUMN mart.fact_logins.device_known IS '1 if the device is registered to this customer, 0 if not; NULL if the device is missing';
COMMENT ON COLUMN mart.fact_logins.channel IS 'MOBILE or WEB';
COMMENT ON COLUMN mart.fact_logins.ip_address IS 'IP address. Personal data in a real bank: restrict access';
COMMENT ON COLUMN mart.fact_logins.ip_country IS 'Country of the IP address (ISO 3166 alpha-2)';
COMMENT ON COLUMN mart.fact_logins.is_foreign_ip IS '1 if the IP is outside the bank''s home country';
COMMENT ON COLUMN mart.fact_logins.result IS 'SUCCESS or FAILED';
COMMENT ON COLUMN mart.fact_logins.is_failed IS '1 if the attempt failed';
COMMENT ON COLUMN mart.fact_logins.failure_reason IS 'WRONG_PASSWORD or OTP_FAILED for failed attempts';
