-- Add initial_stop_price to trade_log for deterministic R calculation.
-- R is computed at close using initial stop (risk baseline), not trailing stop.

ALTER TABLE trade_log ADD COLUMN initial_stop_price REAL;
