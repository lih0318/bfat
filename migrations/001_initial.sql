-- BFAT initial schema (SQLite / PostgreSQL compatible)
-- Run against SQLite; for PostgreSQL replace INTEGER PRIMARY KEY AUTOINCREMENT with SERIAL

CREATE TABLE IF NOT EXISTS trade_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    size REAL NOT NULL,
    exit_time TEXT NOT NULL,
    exit_price REAL NOT NULL,
    pnl REAL NOT NULL,
    pnl_r REAL,
    stop_phase TEXT,
    signal_candle_ts TEXT,
    correlation_id TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS equity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    equity REAL NOT NULL,
    available_balance REAL,
    unrealized_pnl REAL,
    daily_start_equity REAL
);

CREATE TABLE IF NOT EXISTS system_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    message TEXT,
    payload TEXT,
    correlation_id TEXT
);
