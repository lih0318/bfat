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
        migration_file = migrations_dir / "001_initial.sql"
        if not migration_file.exists():
            raise FileNotFoundError(f"Migration not found: {migration_file}")

        sql = _load_migration(migration_file)
        conn = self.get_connection()
        conn.executescript(sql)
        conn.commit()
        logger.info("database_tables_initialized", extra={"migration": "001_initial"})

    def close(self) -> None:
        """Close the connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("database_connection_closed")
