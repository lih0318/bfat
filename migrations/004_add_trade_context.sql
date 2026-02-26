-- Add gross_pnl, net_pnl, initial_risk, trade_hash for full trade context.
-- Do not derive later; persist at close.

ALTER TABLE trade_log ADD COLUMN gross_pnl REAL;
ALTER TABLE trade_log ADD COLUMN net_pnl REAL;
ALTER TABLE trade_log ADD COLUMN initial_risk REAL;
ALTER TABLE trade_log ADD COLUMN trade_hash TEXT;
