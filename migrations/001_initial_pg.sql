-- BFAT schema for PostgreSQL (future migration reference)
-- Use when migrating from SQLite to PostgreSQL

CREATE TABLE IF NOT EXISTS trade_log (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_time TIMESTAMPTZ NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    size DOUBLE PRECISION NOT NULL,
    exit_time TIMESTAMPTZ NOT NULL,
    exit_price DOUBLE PRECISION NOT NULL,
    pnl DOUBLE PRECISION NOT NULL,
    pnl_r DOUBLE PRECISION,
    stop_phase TEXT,
    signal_candle_ts TIMESTAMPTZ,
    correlation_id TEXT,
    metadata JSONB
);

CREATE TABLE IF NOT EXISTS equity_log (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    equity DOUBLE PRECISION NOT NULL,
    available_balance DOUBLE PRECISION,
    unrealized_pnl DOUBLE PRECISION,
    daily_start_equity DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS system_log (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    message TEXT,
    payload JSONB,
    correlation_id TEXT
);
