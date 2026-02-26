"""Database connection factory (SQLite). PostgreSQL-compatible schema."""

import sqlite3
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger
from app.config.settings import Settings

logger = get_logger(__name__)


def _sqlite_path_from_url(url: str) -> Path:
    """Extract file path from sqlite:/// URL."""
    if url.startswith("sqlite:///"):
        return Path(url.replace("sqlite:///", ""))
    return Path(url)


def _load_migration(filepath: Path) -> str:
    """Load SQL migration file."""
    return filepath.read_text(encoding="utf-8")


class DatabaseFactory:
    """Creates and manages SQLite DB connections."""

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or Settings()
        self._db_path = _sqlite_path_from_url(self._settings.database_url)
        self._conn: Optional[sqlite3.Connection] = None

    def get_connection(self) -> sqlite3.Connection:
        """Return a DB connection. Creates one if not exists."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            logger.info("database_connected", extra={"db_path": str(self._db_path)})
        return self._conn

    def init_tables(self) -> None:
        """Create tables from migrations. Call on startup."""
        migrations_dir = Path(__file__).resolve().parent.parent.parent.parent / "migrations"
        conn = self.get_connection()
        for name in (
            "001_initial",
            "002_add_initial_stop_r",
            "003_add_r_validation_status",
            "004_add_trade_context",
        ):
            migration_file = migrations_dir / f"{name}.sql"
            if not migration_file.exists():
                continue
            try:
                sql = _load_migration(migration_file)
                conn.executescript(sql)
                conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    pass
                else:
                    raise
            logger.info("database_migration_applied", extra={"migration": name})

    def close(self) -> None:
        """Close the connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("database_connection_closed")
