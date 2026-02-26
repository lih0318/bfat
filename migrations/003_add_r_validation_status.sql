-- Add r_validation_status for R-Multiple consistency detection.
-- Values: OK, WARNING, CRITICAL, ANOMALY

ALTER TABLE trade_log ADD COLUMN r_validation_status TEXT;
